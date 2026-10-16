from datetime import date, datetime, timedelta

import pytest

from conftest import login_as_tenant
from src.mail_tracker import get_tracker
from test_workflow_automatic_delivery import automatic


BRANDS = ['tenant-norkevin', 'tenant-norkevin-photography']


@pytest.mark.parametrize('tenant', BRANDS)
@pytest.mark.parametrize('kind', ['invoice', 'reminder', 'quote', 'contract', 'lead-questionnaire', 'job-questionnaire'])
def test_document_buttons_deliver_without_approval(automatic, auth_client, tenant, kind):
    app, setup, sent = automatic
    login_as_tenant(auth_client, tenant)
    lead, _ = setup(tenant)
    job, _ = setup(tenant, 'job')
    job.update(lead_id=lead['id'], client_id=lead['client_id'], email=lead['email'])
    app.upsert_job(job)
    common = dict(tenant_id=tenant, job_id=job['id'], lead_id=lead['id'], client_id=lead['client_id'])
    if kind in ('invoice', 'reminder'):
        payment = dict(common, id='pay-no-approval', invoice_id='INV-TEST', amount=100, status='Pendiente', due_date=date.today().isoformat())
        app.store.upsert('payments', payment)
        url = f"/api/payments/{payment['id']}/" + ('send' if kind == 'invoice' else 'send-reminder')
    elif kind == 'quote':
        app.store.upsert('quotes', dict(common, id='quote-no-approval', status='Preparada', precio_total=100, options=[]))
        url = '/api/quotes/quote-no-approval/send'
    elif kind == 'contract':
        prepared = auth_client.post('/api/contracts/new', json={'job_id': job['id']})
        assert prepared.status_code == 200, prepared.json
        url = f"/api/contracts/{prepared.json['contract_id']}/send"
    else:
        url = f"/api/{'leads/' + lead['id'] if kind == 'lead-questionnaire' else 'jobs/' + job['id']}/questionnaires"

    response = auth_client.post(url, json={})
    assert response.status_code == 200, response.json
    mail = app.store.get('pending_emails', response.json['mail_id'])
    assert mail['status'] == 'sent', response.json
    assert mail['delivery_policy'] == 'automatic'
    assert not response.json.get('mail_warning')
    assert 'aprobaci' not in response.json.get('message', '')
    assert len(sent) == 1 and sent[0][1]['metadata']['tenant_id'] == tenant


@pytest.mark.parametrize('tenant', BRANDS)
def test_background_reminders_and_future_sources_use_common_delivery_policy(automatic, auth_client, tenant):
    app, setup, sent = automatic
    login_as_tenant(auth_client, tenant)
    job, _ = setup(tenant, 'job')
    app.store.upsert('payments', dict(id='automatic-reminder', tenant_id=tenant, job_id=job['id'],
        client_id=job['client_id'], invoice_id='INV-AUTO', amount=100, status='Pendiente', due_date=date.today().isoformat()))
    assert app.check_and_send_payment_reminders() == ['automatic-reminder']
    assert len(sent) == 1
    app.check_and_send_payment_reminders()
    assert len(sent) == 1
    future = get_tracker().queue_email(job['email'], 'Otro tipo de correo', 'Mensaje sintético',
        tenant_id=tenant, job_id=job['id'], client_id=job['client_id'], source='new:future-feature', idempotency_key='future-feature')
    assert future['status'] == 'sent' and len(sent) == 2


@pytest.mark.parametrize('tenant', BRANDS)
@pytest.mark.parametrize('action', ['send_email', 'send_contract', 'send_questionnaire', 'send_gallery'])
def test_job_workflow_manual_delivers_and_scheduled_waits(automatic, auth_client, tenant, action):
    app, setup, sent = automatic
    login_as_tenant(auth_client, tenant)
    job, instance = setup(tenant, 'job', created=datetime.now())
    job['galeria_url'] = 'https://gallery.example.invalid/client'
    app.upsert_job(job)
    workflow = app.PRODUCTION_WORKFLOW(tenant).to_dict()
    workflow['steps'][0]['action_type'] = action
    app.store.save_tenant_dict('workflow_templates', {workflow['id']: workflow}, tenant)
    step = app.PRODUCTION_WORKFLOW(tenant).steps[0].to_dict()
    queued = app._prepare_job_workflow_email(job, step)
    app._complete_job_workflow_step(job, step['id'], mail_id=queued['mail_id'])
    mail = app.store.get('pending_emails', queued['mail_id'])
    assert mail['status'] == 'pending' and mail['delivery_policy'] == 'scheduled'
    assert sent == []
    assert not app._send_due_workflow_emails(tenant_id=tenant, now=instance.trigger_at + timedelta(hours=2))
    response = auth_client.post(f"/api/jobs/{job['id']}/production-step", json={'step_id': step['id']})
    assert response.status_code == 200, response.json
    mail = app.store.get('pending_emails', response.json['mail_id'])
    assert mail['status'] == 'sent' and len(sent) == 1
    assert response.json['mail_id'] == queued['mail_id']
    assert not app._send_due_workflow_emails(tenant_id=tenant, now=instance.trigger_at + timedelta(hours=4))


@pytest.mark.parametrize('tenant', BRANDS)
def test_imported_workflow_send_now_delivers_once(automatic, auth_client, tenant):
    app, setup, sent = automatic
    login_as_tenant(auth_client, tenant)
    job, _ = setup(tenant, 'job', created=datetime.now())
    template = app.PRODUCTION_WORKFLOW(tenant).steps[0].email_template_id
    job['studio_ninja_workflow'] = [dict(id='imported-now', name='Correo importado', status='pending',
        source_stage='PRODUCTION', action_type='send_email', email_template_id=template,
        scheduled=(datetime.now() + timedelta(days=1)).isoformat())]
    app.upsert_job(job)
    url = f"/api/jobs/{job['id']}/imported-workflow/imported-now/complete"
    response = auth_client.post(url, json={})
    assert response.status_code == 200, response.json
    assert response.json['delivery_status'] == 'sent' and len(sent) == 1
    assert auth_client.post(url, json={}).json['delivery_status'] == 'sent' and len(sent) == 1


@pytest.mark.parametrize('tenant', BRANDS)
def test_changed_scheduled_content_is_blocked_instead_of_awaiting_approval(automatic, auth_client, tenant):
    app, setup, sent = automatic
    login_as_tenant(auth_client, tenant)
    job, _ = setup(tenant, 'job')
    tracker = get_tracker()
    args = dict(tenant_id=tenant, job_id=job['id'], client_id=job['client_id'], idempotency_key='changed-content')
    first = tracker.queue_email(job['email'], 'Mensaje', 'Original', defer_delivery=True, **args)
    second = tracker.queue_email(job['email'], 'Mensaje', 'Modificado', **args)
    assert first['id'] == second['id'] and second['status'] == 'blocked'
    assert second['body'] == 'Original' and sent == []
    assert 'contenido cambió' in second['blocked_reason']


@pytest.mark.parametrize('tenant', BRANDS)
def test_mail_page_explains_all_mail_policy(automatic, auth_client, tenant):
    login_as_tenant(auth_client, tenant)
    response = auth_client.get('/emails')
    assert response.status_code == 200
    assert 'Todos los correos de esta marca se envían sin aprobación.' in response.text
