from datetime import datetime, timedelta
import uuid

import pytest


@pytest.fixture
def booking(auth_client, monkeypatch):
    import app as crm
    from conftest import login_as_tenant
    tenant = 'tenant-norkevin-photography'
    login_as_tenant(auth_client, tenant)
    saved = crm.store.get_tenant_dict('workflow_templates', tenant)
    monkeypatch.setattr(crm.workflow_engine, 'instances', {})
    token = crm._workflow_tenant.set(tenant)
    suffix = uuid.uuid4().hex[:8]
    normal = 'tpl-available-' + suffix
    unavailable = 'tpl-unavailable-' + suffix
    for tid, name, text in [(normal, 'Paquetes', 'Fecha disponible %job_date%'),
                            (unavailable, 'Paquetes de boda (FECHA NO DISPONIBLE)', 'Fecha NO DISPONIBLE %job_date%')]:
        crm.store.upsert('email_templates', {'id': tid, 'name': name, 'asunto': text, 'cuerpo': text,
                                           'activo': True, 'tenant_id': tenant})
    workflow = crm.LEAD_WORKFLOW(tenant).to_dict()
    workflow['steps'] = [{'id': 'envio_paquetes', 'name': 'Envio de paquetes', 'action_type': 'send_email',
                          'email_template_id': normal, 'due_date': {'mode': 'after_creation', 'amount': 3,
                          'unit': 'hours', 'relative_to': 'lead_created'}}]
    crm.store.save_tenant_dict('workflow_templates', {workflow['id']: workflow}, tenant)
    lead = {'id': 'lead-availability-' + suffix, 'nombre': 'Ana', 'email': 'ana@example.invalid',
            'tenant_id': tenant, 'fecha_tentativa': '2034-05-15', 'status': 'Nuevo',
            'created': '2026-10-01T00:00:00'}
    crm.store.upsert('leads', lead)
    instance = crm.trigger_workflow_for_lead(lead['id'], 'Ana', tenant)
    instance.trigger_at = datetime(2026, 10, 1)
    job = {'id': 'job-booked-' + suffix, 'lead_id': 'lead-other-' + suffix, 'tenant_id': tenant,
           'nombre': 'Boda reservada', 'boda_date': '2034-05-15', 'status': 'Confirmado'}
    yield crm, auth_client, lead, job, normal, unavailable, instance
    crm.store.save_tenant_dict('workflow_templates', saved, tenant)
    for table, ids in [('jobs', [job['id']]), ('leads', [lead['id']]), ('email_templates', [normal, unavailable])]:
        for rid in ids:
            crm.store.delete(table, rid)
    for mail in crm.store.list('pending_emails'):
        if mail.get('lead_id') == lead['id']:
            crm.store.delete('pending_emails', mail['id'])
    crm._workflow_tenant.reset(token)


@pytest.mark.parametrize('occupied', [False, True])
def test_scheduled_first_mail_selects_template_and_remains_pending(booking, occupied):
    crm, client, lead, job, normal, unavailable, instance = booking
    if occupied:
        crm.store.upsert('jobs', job)
    prepared = crm._prepare_due_workflow_emails(now=instance.trigger_at + timedelta(hours=4))
    assert len(prepared) == 1
    mail = crm.store.get('pending_emails', prepared[0]['mail_id'])
    assert mail['status'] == 'pending'
    assert mail['template_id'] == (unavailable if occupied else normal)
    assert '%job_date%' not in mail['body'] and '2034' in mail['body']
    assert not crm._prepare_due_workflow_emails(now=instance.trigger_at + timedelta(days=1))


def test_modal_workflow_and_availability_check_agree(booking):
    crm, client, lead, job, normal, unavailable, instance = booking
    crm.store.upsert('jobs', dict(job, boda_date='2034-05-14', end_date='2034-05-16'))
    step = crm.compute_workflow_steps_for_lead(lead)[0][0]
    assert step['email_template_id'] == unavailable
    assert 'FECHA NO DISPONIBLE' in step['email_template_name']
    assert not client.post('/api/leads/' + lead['id'] + '/check-date').get_json()['disponible']
    html = client.get('/leads/' + lead['id']).get_data(as_text=True)
    assert '"envio_paquetes": "' + unavailable + '"' in html
    overview = client.get('/leads').get_data(as_text=True)
    assert 'nextEmailTemplateId: "' + unavailable + '"' in overview


@pytest.mark.parametrize('change', ['other_tenant', 'cancelled', 'archived', 'quoting', 'own_job'])
def test_only_another_active_job_in_this_account_reserves_the_date(booking, change):
    crm, client, lead, job, normal, unavailable, instance = booking
    if change == 'other_tenant': job['tenant_id'] = 'tenant-other'
    elif change == 'own_job': job['lead_id'] = lead['id']
    else: job['status'] = {'cancelled': 'Cancelado', 'archived': 'Archivado', 'quoting': 'Cotizando'}[change]
    assert not crm._lead_date_conflicts(lead, jobs=[job])
    assert not crm._lead_date_conflicts(dict(lead, fecha_tentativa=''), jobs=[job])


@pytest.mark.parametrize('overview', [False, True])
def test_stale_available_composer_cannot_queue_the_wrong_template(booking, overview):
    crm, client, lead, job, normal, unavailable, instance = booking
    crm.store.upsert('jobs', job)
    payload = {'step_id': 'envio_paquetes', 'template_id': normal, 'subject': 'Fecha disponible',
               'body': 'Esta fecha está disponible', 'complete_step': True}
    route = 'send-email' if overview else 'trigger-step'
    response = client.post('/api/leads/' + lead['id'] + '/' + route, json=payload)
    assert response.status_code == 200
    mail = next(m for m in crm.store.list('pending_emails') if m.get('lead_id') == lead['id'])
    assert mail['template_id'] == unavailable and 'NO DISPONIBLE' in mail['body']
    assert mail['status'] == 'pending'


def test_missing_unavailable_template_blocks_preparation(booking):
    crm, client, lead, job, normal, unavailable, instance = booking
    crm.store.upsert('jobs', job)
    crm.store.upsert('email_templates', {'id': unavailable, 'activo': False})
    response = client.post('/api/leads/' + lead['id'] + '/trigger-step', json={'step_id': 'envio_paquetes'})
    assert response.status_code == 400 and 'ocupada' in response.get_json()['error']
    assert not crm._prepare_due_workflow_emails(now=instance.trigger_at + timedelta(hours=4))
    assert not any(m.get('lead_id') == lead['id'] for m in crm.store.list('pending_emails'))


def test_astral_keeps_its_normal_template_even_with_another_booking(booking):
    crm, client, lead, job, normal, unavailable, instance = booking
    astral_lead = dict(lead, tenant_id='tenant-norkevin')
    astral_job = dict(job, tenant_id='tenant-norkevin')
    assert crm._lead_date_conflicts(astral_lead, jobs=[astral_job])
    assert not crm._lead_packages_unavailable(astral_lead, 'envio_paquetes', jobs=[astral_job])
