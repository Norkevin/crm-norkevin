"""Custom messages use the real queue with fake delivery and isolated CRM data."""
from datetime import datetime, timedelta

import pytest

from test_workflow_automatic_delivery import automatic
from conftest import login_as_tenant


def custom_mail(automatic, client, tenant='tenant-norkevin', imported=False):
    a, setup, sent = automatic
    login_as_tenant(client, tenant)
    job, _ = setup(tenant, 'job')
    if imported:
        job['studio_ninja_workflow'] = []
        a.upsert_job(job)
    response = client.post(f"/api/jobs/{job['id']}/workflow-task", json=dict(
        type='email', name='Mensaje especial', start_date='2026-10-18', start_time='14:05'))
    assert response.status_code == 200, response.get_json()
    task = response.get_json()['task']
    payload = dict(task_id=task['id'], subject='Mensaje personalizado', body='Hola Ana, este mensaje es solo para ti.')
    response = client.post(f"/api/jobs/{job['id']}/send-email", json=payload)
    assert response.status_code == 200, response.get_json()
    return a, job, task, response.get_json(), payload, sent


@pytest.mark.parametrize('tenant', ['tenant-norkevin', 'tenant-norkevin-photography'])
@pytest.mark.parametrize('imported', [False, True])
def test_custom_workflow_mail_sends_at_local_due_time_once_and_completes_its_task(automatic, auth_client, tenant, imported):
    a, job, task, result, _, sent = custom_mail(automatic, auth_client, tenant, imported)
    due = datetime(2026, 10, 18, 14, 5)
    assert result['delivery_status'] == 'pending' and sent == []
    assert 'automáticamente' in result['mail_warning']
    assert not a._send_due_workflow_emails(now=due-timedelta(minutes=1))
    assert len(a._send_due_workflow_emails(now=due)) == 1 and len(sent) == 1
    token = a._workflow_tenant.set(tenant)
    try:
        saved = a.store.get('jobs', job['id'])['manual_workflow_tasks'][0]
        assert saved['id'] == task['id'] and saved['status'] == 'done'
        assert saved['delivery_status'] == 'sent' and saved['completed']
        mail = a.store.get('pending_emails', result['mail_id'])
        assert mail['workflow_task_id'] == task['id'] and mail['status'] == 'sent'
        assert mail['body'] == 'Hola Ana, este mensaje es solo para ti.'
    finally:
        a._workflow_tenant.reset(token)
    assert sent[0][1]['metadata']['tenant_id'] == tenant
    assert not a._send_due_workflow_emails(now=due+timedelta(days=1)) and len(sent) == 1


@pytest.mark.parametrize('tenant', ['tenant-norkevin', 'tenant-norkevin-photography'])
@pytest.mark.parametrize('kind', ['job', 'lead'])
def test_send_button_delivers_free_custom_message_without_second_approval(automatic, auth_client, tenant, kind):
    a, setup, sent = automatic
    login_as_tenant(auth_client, tenant)
    record, _ = setup(tenant, kind)
    endpoint = f"/api/{'jobs' if kind == 'job' else 'leads'}/{record['id']}/send-email"
    payload = dict(subject='Correo personal', body='Texto escrito a mano.')
    first = auth_client.post(endpoint, json=payload)
    assert first.status_code == 200 and first.get_json()['delivery_status'] == 'sent'
    second = auth_client.post(endpoint, json=payload)
    assert second.get_json()['delivery_status'] == 'sent' and len(sent) == 1


def test_custom_task_date_edits_are_respected_and_deletion_discards_queued_mail(automatic, auth_client):
    a, job, task, result, _, sent = custom_mail(automatic, auth_client)
    root = f"/api/jobs/{job['id']}/workflow-task/{task['id']}"
    edit = auth_client.post(root+'/update', json=dict(name=task['name'], start_date='2026-10-19', start_time='15:00'))
    assert edit.status_code == 200
    assert not a._send_due_workflow_emails(now=datetime(2026, 10, 18, 18)) and not sent
    assert auth_client.post(root+'/delete', json={}).status_code == 200
    assert not a._send_due_workflow_emails(now=datetime(2026, 10, 20)) and not sent
    assert a.store.get('pending_emails', result['mail_id'])['status'] == 'discarded'


def test_custom_task_is_never_completed_without_confirmed_delivery(automatic, auth_client, monkeypatch):
    from src.email_delivery import DeliveryResult
    a, job, task, result, _, sent = custom_mail(automatic, auth_client)
    root = f"/api/jobs/{job['id']}/workflow-task/{task['id']}"
    assert auth_client.post(root+'/complete', json={}).status_code == 400
    monkeypatch.setattr('src.mail_tracker.send_email', lambda *args, **kwargs:
                        sent.append(args) or DeliveryResult(ok=False, provider='test', error='Fallo sintético'))
    a._send_due_workflow_emails(now=datetime(2026, 10, 20))
    assert a.store.get('jobs', job['id'])['manual_workflow_tasks'][0]['status'] == 'failed'
    assert not a._send_due_workflow_emails(now=datetime(2026, 10, 21)) and len(sent) == 1
    assert auth_client.post(root+'/complete', json={}).status_code == 400


@pytest.mark.parametrize('case', ['missing_task', 'foreign_job', 'cancelled_job', 'restart'])
def test_custom_task_requires_live_own_job_and_binding_and_survives_restart(automatic, auth_client, monkeypatch, case):
    a, job, task, result, payload, sent = custom_mail(automatic, auth_client)
    if case == 'missing_task':
        payload['task_id'] = 'nonexistent-task'
        assert auth_client.post(f"/api/jobs/{job['id']}/send-email", json=payload).status_code == 400
        job = a.store.get('jobs', job['id']); job['manual_workflow_tasks'] = []
        a.upsert_job(job)
    if case == 'foreign_job':
        login_as_tenant(auth_client, 'tenant-norkevin-photography')
        assert auth_client.post(f"/api/jobs/{job['id']}/send-email", json=payload).status_code == 404
    if case == 'cancelled_job':
        job = a.store.get('jobs', job['id']); job['status'] = 'Cancelado'; a.upsert_job(job)
    if case == 'restart':
        monkeypatch.setattr(a.workflow_engine, 'instances', {})
    rows = a._send_due_workflow_emails(now=datetime(2026, 10, 20))
    assert len(rows) == (1 if case in ('restart', 'foreign_job') else 0)
    assert len(sent) == len(rows)


def test_changed_custom_snapshot_is_blocked_and_does_not_send_original_text(automatic, auth_client):
    a, job, task, result, payload, sent = custom_mail(automatic, auth_client, imported=True)
    payload['body'] = 'Una versión nueva que no debe reemplazar el original en silencio.'
    duplicate = auth_client.post(f"/api/jobs/{job['id']}/send-email", json=payload).get_json()
    assert duplicate['mail_id'] == result['mail_id'] and 'BLOQUEADO' in duplicate['mail_warning']
    assert duplicate['delivery_status'] == 'blocked'
    assert not a._send_due_workflow_emails(now=datetime(2026, 10, 20)) and sent == []
    html = auth_client.get('/jobs/'+job['id']).get_data(as_text=True)
    assert 'El envío necesita revisión en Correos.' in html
    partial = a.app.jinja_env.get_template('_job_manual_workflow_tasks.html').render(
        job=a.store.get('jobs', job['id']), workflow_auto_mail=True)
    assert 'Envío automático programado' not in partial and 'data-workflow-watch="1"' not in partial


def test_manual_composer_for_a_workflow_step_sends_now_with_custom_body(automatic, auth_client):
    a, setup, sent = automatic
    job, instance = setup('tenant-norkevin', 'job')
    login_as_tenant(auth_client, 'tenant-norkevin')
    response = auth_client.post(f"/api/jobs/{job['id']}/send-email", json=dict(
        step_id='auto_step', subject='Especial', body='Texto personalizado del paso.'))
    assert response.status_code == 200 and response.get_json()['delivery_status'] == 'sent' and len(sent) == 1
    due = instance.trigger_at + timedelta(hours=3)
    assert not a._send_due_workflow_emails(now=due-timedelta(minutes=1))
    assert not a._send_due_workflow_emails(now=due) and len(sent) == 1
    assert sent[0][0][2] == 'Texto personalizado del paso.'


def test_invalid_imported_date_does_not_stop_another_due_step(automatic):
    a, setup, sent = automatic
    job, _ = setup('tenant-norkevin', 'job')
    job['studio_ninja_workflow'] = [
        dict(id='broken-date', name='Fecha inválida', status='pending', source_stage='PRODUCTION',
             action_type='send_email', email_template_id=a.PRODUCTION_WORKFLOW('tenant-norkevin').steps[0].email_template_id,
             scheduled='not-a-date'),
        dict(id='valid-date', name='Fecha válida', status='pending', source_stage='PRODUCTION',
             action_type='send_email', email_template_id=a.PRODUCTION_WORKFLOW('tenant-norkevin').steps[0].email_template_id,
             scheduled='2026-10-18T12:00:00')]
    a.upsert_job(job)
    rows = a._prepare_due_workflow_emails(now=datetime(2026, 10, 18, 12))
    assert [r['step_id'] for r in rows] == ['valid-date']
    assert len(a._send_due_workflow_emails(now=datetime(2026, 10, 18, 12))) == 1 and len(sent) == 1


def test_imported_and_native_jobs_render_the_same_custom_task_composer(automatic, auth_client):
    a, job, task, _, _, _ = custom_mail(automatic, auth_client, imported=True)
    html = auth_client.get('/jobs/'+job['id']).get_data(as_text=True)
    assert 'Envío automático programado.' in html and 'email-task-id' in html
    assert 'programa su envío automático' in html and 'No se envía automáticamente' not in html
    assert 'task_id: document.getElementById' in html


@pytest.mark.parametrize('outcome', ['failed', 'uncertain'])
def test_requested_custom_send_never_retries_a_failed_or_uncertain_double_click(automatic, auth_client, monkeypatch, outcome):
    from src.email_delivery import DeliveryResult
    a, setup, sent = automatic
    login_as_tenant(auth_client, 'tenant-norkevin')
    job, _ = setup('tenant-norkevin', 'job')
    def deliver(*args, **kwargs):
        sent.append(args)
        if outcome == 'uncertain':raise RuntimeError('Resultado sintético desconocido')
        return DeliveryResult(ok=False, provider='test', error='Fallo sintético')
    monkeypatch.setattr('src.mail_tracker.send_email', deliver)
    endpoint = f"/api/jobs/{job['id']}/send-email"
    payload = dict(subject='Personal', body='Texto')
    first = auth_client.post(endpoint, json=payload).get_json()
    second = auth_client.post(endpoint, json=payload).get_json()
    assert first['delivery_status'] == second['delivery_status'] == 'failed'
    assert first['mail_id'] == second['mail_id'] and len(sent) == 1


def test_automatic_lead_delivery_updates_its_card_and_next_task(automatic):
    a, setup, sent = automatic
    lead, instance = setup('tenant-norkevin')
    due = instance.trigger_at + timedelta(hours=3)
    a._prepare_due_workflow_emails(now=due)
    a._send_due_workflow_emails(now=due)
    token = a._workflow_tenant.set('tenant-norkevin')
    try:
        saved = a.get_lead(lead['id'])
        assert saved['mail_status'] == 'ENVIADO' and saved['next_task'] != 'Correo automático'
    finally:
        a._workflow_tenant.reset(token)
    assert len(sent) == 1


def test_job_list_and_header_do_not_claim_completion_while_custom_mail_is_queued(automatic, auth_client):
    a, job, task, _, _, _ = custom_mail(automatic, auth_client, imported=True)
    listing = auth_client.get('/jobs').get_data(as_text=True)
    assert task['name'] in listing
    html = auth_client.get('/jobs/'+job['id']).get_data(as_text=True)
    assert 'Envío automático programado' in html and 'Tarea manual pendiente' not in html


def test_legacy_task_without_date_does_not_create_an_unprocessable_pending_mail(automatic, auth_client):
    a, setup, sent = automatic
    job, _ = setup('tenant-norkevin', 'job')
    login_as_tenant(auth_client, 'tenant-norkevin')
    job['manual_workflow_tasks'] = [dict(id='legacy-without-date', type='email', status='pending', name='Sin fecha')]
    a.upsert_job(job)
    response = auth_client.post(f"/api/jobs/{job['id']}/send-email", json=dict(
        task_id='legacy-without-date', subject='Personalizado', body='Mensaje'))
    assert response.status_code == 400 and 'Edita la fecha' in response.get_json()['error']
    assert not sent and not a.store.list('pending_emails')
