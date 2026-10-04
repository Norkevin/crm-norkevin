from datetime import timedelta
import pytest
from test_lead_package_availability import booking


def test_discard_is_visible_does_not_send_and_cannot_be_sent_later(booking, monkeypatch):
    crm, client, lead, *_ = booking
    from src.mail_tracker import get_tracker
    calls = []
    monkeypatch.setattr('src.mail_tracker.send_email', lambda *a, **k: calls.append(a))
    pending = get_tracker().queue_email('ana@example.invalid', 'Descartar prueba', 'Mensaje', lead_id=lead['id'], tenant_id=lead['tenant_id'])
    assert 'Descartar correo: Descartar prueba' in client.get('/emails').get_data(as_text=True)
    result = client.post('/api/pending-emails/' + pending['id'] + '/discard')
    assert result.status_code == 200 and result.json['email']['estado'] == 'discarded'
    assert crm.store.get('pending_emails', pending['id'])['historial'][-1]['actor']
    assert client.post('/api/pending-emails/' + pending['id'] + '/send').status_code == 400
    assert calls == []
    for status in ['sent', 'sending']:
        crm.store.upsert('pending_emails', dict(pending, status=status))
        assert not get_tracker().discard_pending(pending['id'])['ok']
        assert crm.store.get('pending_emails', pending['id'])['status'] == status
    crm.store.delete('pending_emails', pending['id'])


def test_imported_questionnaire_can_be_omitted_and_included_again(booking):
    crm, client, lead, job, *_ = booking
    job['studio_ninja_workflow'] = [
        {'id':'questionnaire', 'name':'Cuestionario cliente', 'status':'pending', 'source_stage':'PRODUCTION', 'source_details':'Original'},
        {'id':'contract', 'name':'Contrato', 'status':'done', 'source_stage':'PRODUCTION', 'source_details':'Original'}]
    job['manual_workflow_tasks'] = [{'id':'task-test', 'name':'Cuestionario cliente', 'step_id':'questionnaire', 'status':'pending'}]
    crm.store.upsert('jobs', job)
    assert 'Opciones de Cuestionario cliente' in client.get('/jobs/' + job['id']).get_data(as_text=True)
    url = '/api/jobs/' + job['id'] + '/steps/questionnaire/'
    assert client.post(url + 'skip').status_code == 200
    current = crm.get_job(job['id'])
    assert current['studio_ninja_workflow'][0]['status'] == 'skipped'
    assert current['manual_workflow_tasks'][0]['status'] == 'skipped'
    assert 'Omitido' in client.get('/jobs/' + job['id']).get_data(as_text=True)
    assert crm.compute_workflow_steps_for_job(current)[1] == 100
    assert client.post('/api/jobs/' + job['id'] + '/steps/contract/skip').status_code == 400
    assert client.post(url + 'unskip').status_code == 200
    assert crm.get_job(job['id'])['studio_ninja_workflow'][0]['status'] == 'pending'
    assert client.post(url + 'unskip').status_code == 400


def test_astral_referral_removes_three_followups_and_cancels_existing_queue(booking):
    crm, client, lead, job, normal, unavailable, instance = booking
    workflow = crm.LEAD_WORKFLOW(lead['tenant_id']).to_dict()
    for sid in crm.ASTRAL_REFERRAL_FOLLOWUPS:
        workflow['steps'].append({'id':sid, 'name':sid, 'action_type':'send_email', 'email_template_id':normal,
                                 'due_date':{'mode':'after_creation','amount':7,'unit':'days','relative_to':'lead_created'}})
        instance.step_states[sid] = crm.StepStatus.PENDING
    crm.store.save_tenant_dict('workflow_templates', {workflow['id']:workflow}, lead['tenant_id'])
    from src.mail_tracker import get_tracker
    pending = get_tracker().queue_email('ana@example.invalid', 'Seguimiento antiguo', 'Mensaje', lead_id=lead['id'],
                                        tenant_id=lead['tenant_id'], source='workflow:lead-step:seguimiento_cliente')
    crm.store.upsert('jobs', job)
    result = crm._complete_lead_workflow_step(lead, 'envio_paquetes')
    assert result['queued']
    current = crm.get_lead(lead['id'])
    assert current['astral_referral_at']
    assert [s['id'] for s in crm.compute_workflow_steps_for_lead(current)[0]] == ['envio_paquetes']
    assert all(instance.step_states[sid] == crm.StepStatus.SKIPPED for sid in crm.ASTRAL_REFERRAL_FOLLOWUPS)
    assert crm.store.get('pending_emails', pending['id'])['status'] == 'discarded'
    assert not crm._prepare_due_workflow_emails(now=instance.trigger_at + timedelta(days=100))
    assert not crm._complete_lead_workflow_step(current, 'seguimiento_final')['completed']
    assert 'seguimiento_final' not in client.get('/leads/' + lead['id']).get_data(as_text=True)
    for mail in crm.store.list('pending_emails'):
        if mail.get('lead_id') == lead['id']: crm.store.delete('pending_emails', mail['id'])


def test_omitting_queued_native_step_cancels_mail_and_reincluding_requires_new_approval(booking):
    crm, client, lead, job, *_ = booking
    from src.mail_tracker import get_tracker
    crm.store.upsert('jobs', job)
    instance = crm._get_or_create_job_workflow_instance(job)
    sid = crm.PRODUCTION_WORKFLOW().steps[1].id
    pending = get_tracker().queue_email('ana@example.invalid', 'Reserva', 'Mensaje',
                                        job_id=job['id'], tenant_id=lead['tenant_id'],
                                        idempotency_key='skip-native:' + job['id'])
    pending.update(workflow_instance_id=instance.id, workflow_step_id=sid)
    crm.store.upsert('pending_emails', pending)
    instance.step_states[sid] = crm.StepStatus.QUEUED
    url = f"/api/jobs/{job['id']}/steps/{sid}/"
    assert client.post(url + 'skip').status_code == 200
    assert crm.store.get('pending_emails', pending['id'])['status'] == 'discarded'
    assert client.post(url + 'unskip').status_code == 200
    fresh = get_tracker().queue_email('ana@example.invalid', 'Reserva', 'Mensaje',
                                     job_id=job['id'], tenant_id=lead['tenant_id'],
                                     idempotency_key='skip-native:' + job['id'])
    assert fresh['id'] != pending['id'] and fresh['status'] == 'pending'
    assert crm.store.get('pending_emails', pending['id'])['status'] == 'discarded'
    for mail in [pending, fresh]: crm.store.delete('pending_emails', mail['id'])


def test_existing_unavailable_reply_is_recognized_without_resending(booking):
    crm, client, lead, job, normal, unavailable, instance = booking
    from src.mail_tracker import get_tracker
    pending = get_tracker().queue_email('ana@example.invalid', 'Fecha no disponible', 'Astral',
                                        lead_id=lead['id'], tenant_id=lead['tenant_id'], template_id=unavailable)
    pending['status'] = 'sent'
    crm.store.upsert('pending_emails', pending)
    assert crm._is_astral_referral(lead)
    crm._stop_astral_referral_followups(lead)
    assert crm.get_lead(lead['id'])['astral_referral_at']
    assert crm.store.get('pending_emails', pending['id'])['status'] == 'sent'
    crm.store.delete('pending_emails', pending['id'])
