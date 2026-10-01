"""Proposal -> acceptance -> documents -> deposit, with no real delivery."""
import uuid
from datetime import datetime, timedelta

from flask import session
from src.email_delivery import DeliveryResult

TENANT = 'tenant-norkevin'


def seed(a):
    suffix = uuid.uuid4().hex[:8]
    client = {'id': 'client-journey-' + suffix, 'first_name': 'Prueba', 'email': suffix + '@example.invalid', 'tenant_id': TENANT}
    lead = {'id': 'lead-journey-' + suffix, 'nombre': 'Prueba integrada', 'email': client['email'], 'client_id': client['id'],
            'tenant_id': TENANT, 'fecha_tentativa': (datetime.now() + timedelta(days=200)).strftime('%Y-%m-%d')}
    quote = {'id': 'quote-journey-' + suffix, 'lead_id': lead['id'], 'client_id': client['id'], 'tenant_id': TENANT,
             'status': 'Borrador', 'precio_total': 10000, 'paquete_nombre': 'Mix Gold', 'plan_pago': 3,
             'plan_pago_opciones': [3, 4, 5]}
    for table, row in [('clients', client), ('leads', lead), ('quotes', quote)]:
        a.store.upsert(table, row)
    return client, lead, quote


def test_preview_acceptance_and_reservation(auth_client):
    import app as a
    client, lead, quote = seed(a)
    expected = a._payment_due_dates(3, lead['fecha_tentativa'])
    html = auth_client.get('/quotes/' + quote['id']).get_data(as_text=True)
    assert 'PAYMENT_PREVIEW' in html
    assert all(due in html for due in expected)
    for _ in range(2):
        response = auth_client.post('/quotes/' + quote['id'] + '/accept', json={'plan_pago': 3})
        assert response.status_code == 200
    quote = a.store.get('quotes', quote['id'])
    job = a.get_job(quote['job_id'])
    payments = sorted([p for p in a.store.list('payments') if p.get('quote_id') == quote['id']], key=lambda p: p['due_date'])
    assert len(payments) == 3
    assert [p['due_date'] for p in payments] == expected
    assert round(sum(p['amount'] for p in payments), 2) == 10000
    contracts = [c for c in a.store.list('contracts') if c.get('job_id') == job['id']]
    assert len(contracts) == 1
    assert len([q for q in a.store.list('questionnaires') if q.get('job_id') == job['id']]) == 1
    with a.app.test_request_context('/'):
        session['tenant_id'] = TENANT
        assert not a._booking_progress(job)['complete']
        contract = contracts[0]
        contract.update(signed=True, photographer_signed=True)
        a.store.upsert('contracts', contract)
        assert not a._booking_progress(job)['complete']
        payments[0].update(status='Pagado', amount=0)
        a.store.upsert('payments', payments[0])
        assert a._booking_progress(job)['complete']
    portal = auth_client.get('/portal/' + client['id']).get_data(as_text=True)
    assert 'Reserva confirmada' in portal
    assert 'Tu próximo paso' in portal
    assert 'Revisá tu próximo pago' in portal


def test_preparation_opt_out_and_locked_after_acceptance(auth_client):
    import app as a
    _, _, quote = seed(a)
    path = '/api/quotes/' + quote['id'] + '/preparation'
    assert auth_client.post(path, json={'prepare_contract': 'false', 'prepare_questionnaire': False}).status_code == 400
    assert auth_client.post(path, json={'prepare_contract': False, 'prepare_questionnaire': False}).json['ok']
    auth_client.post('/quotes/' + quote['id'] + '/accept', json={'plan_pago': 4})
    job_id = a.store.get('quotes', quote['id'])['job_id']
    assert not [c for c in a.store.list('contracts') if c.get('job_id') == job_id]
    assert not [q for q in a.store.list('questionnaires') if q.get('job_id') == job_id]
    assert auth_client.post(path, json={'prepare_contract': True, 'prepare_questionnaire': True}).status_code == 400


def test_queue_failure_retry_keeps_truthful_quote_state(auth_client, monkeypatch):
    import app as a
    from src.mail_tracker import MailTracker
    _, _, quote = seed(a)
    quote['options'] = [{'id': 'gold', 'paquete_nombre': 'Mix Gold', 'precio_total': 10000}]
    a.store.upsert('quotes', quote)
    response = auth_client.post('/api/quotes/' + quote['id'] + '/send', json={})
    assert response.json['ok'], response.json
    pending_id = response.json['mail_id']
    saved = a.store.get('quotes', quote['id'])
    assert saved['status'] == 'Preparada'
    assert saved['delivery_status'] == 'pending'
    assert not saved.get('sent_at')
    monkeypatch.setattr('src.mail_tracker.send_email', lambda *args, **kwargs: DeliveryResult(ok=False, provider='test', error='test failure'))
    with a.app.test_request_context('/'):
        session['tenant_id'] = TENANT
        result = MailTracker().approve_and_send(pending_id, sender_tenant_id=TENANT)
        assert not result['ok']
        assert not a.store.get('quotes', quote['id']).get('sent_at')
        assert not result['pendiente'].get('sent_at')
        assert a.store.get('quotes', quote['id'])['delivery_status'] == 'failed'
        monkeypatch.setattr('src.mail_tracker.send_email', lambda *args, **kwargs: DeliveryResult(ok=True, provider='test', message_id='test-msg'))
        result = MailTracker().retry_failed(pending_id, sender_tenant_id=TENANT)
        assert result['ok'], result
        assert a.store.get('quotes', quote['id'])['status'] == 'Enviada'
        assert a.store.get('quotes', quote['id'])['sent_at']


def test_converted_lead_skips_unfinished_steps(auth_client):
    import app as a
    from src.workflow.models import StepStatus
    _, lead, _ = seed(a)
    with a.app.test_request_context('/'):
        session['tenant_id'] = TENANT
        instance = a.workflow_engine.start_workflow(a.LEAD_WORKFLOW(), 'lead', lead['id'], tenant_id=TENANT)
        first = next(iter(instance.step_states))
        instance.step_states[first] = StepStatus.DONE
        instance.step_results[first] = 'Actual completed action'
        a._complete_original_lead_workflow(lead, {'id': 'test-job'})
        assert instance.step_states[first] == StepStatus.DONE
        assert instance.step_results[first] == 'Actual completed action'
        assert all(value == StepStatus.SKIPPED for sid, value in instance.step_states.items() if sid != first)


def test_workflow_waits_for_delivery_and_discard_is_visible(auth_client):
    import app as a
    from src.mail_tracker import MailTracker
    from src.workflow.models import StepStatus
    _, lead, _ = seed(a)
    with a.app.test_request_context('/'):
        session['tenant_id'] = TENANT
        instance = a.workflow_engine.start_workflow(a.LEAD_WORKFLOW(), 'lead', lead['id'], tenant_id=TENANT)
        step = next(s for s in a.LEAD_WORKFLOW().steps if s.email_template_id)
        result = a._complete_lead_workflow_step(lead, step.id)
        assert result['queued'] and not result['sent']
        assert instance.step_states[step.id] == StepStatus.QUEUED
        result = MailTracker().approve_and_send(result['mail_id'], sender_tenant_id=TENANT)
        assert result['ok'], result
        assert instance.step_states[step.id] == StepStatus.DONE


def test_date_change_preview_and_history_do_not_mutate_payments(auth_client):
    import app as a
    _, _, quote = seed(a)
    auth_client.post('/quotes/' + quote['id'] + '/accept', json={'plan_pago': 5})
    job_id = a.store.get('quotes', quote['id'])['job_id']
    before = [p for p in a.store.list('payments') if p.get('job_id') == job_id]
    response = auth_client.get('/api/jobs/' + job_id + '/date-preview?date=2029-01-01')
    assert response.json['ok'] and response.json['changes']
    assert response.json['payments_unchanged']
    assert before == [p for p in a.store.list('payments') if p.get('job_id') == job_id]
    missing = auth_client.get('/api/jobs/' + job_id + '/date-preview?date=').json
    assert any(change['after'] is None for change in missing['changes'])
    history = auth_client.get('/api/jobs/' + job_id + '/history').json['history']
    assert any(h['message'] == 'Cotización aceptada' for h in history)
    assert any(h['message'] == 'Contrato preparado' for h in history)
    assert auth_client.get('/api/jobs/' + job_id + '/date-preview?date=bad').status_code == 400
    assert auth_client.get('/jobs/' + job_id).status_code == 200


def test_discard_updates_step_without_claiming_delivery(auth_client):
    import app as a
    from src.mail_tracker import MailTracker
    from src.workflow.models import StepStatus
    _, lead, _ = seed(a)
    with a.app.test_request_context('/'):
        session['tenant_id'] = TENANT
        instance = a.workflow_engine.start_workflow(a.LEAD_WORKFLOW(), 'lead', lead['id'], tenant_id=TENANT)
        step = next(s for s in a.LEAD_WORKFLOW().steps if s.email_template_id)
        result = a._complete_lead_workflow_step(lead, step.id)
        MailTracker().discard_pending(result['mail_id'])
        assert instance.step_states[step.id] == StepStatus.FAILED
        assert instance.step_results[step.id] == 'Correo descartado'


def test_preparation_and_date_preview_are_tenant_scoped(auth_client):
    import app as a
    a.store.upsert('quotes', {'id': 'foreign-journey', 'tenant_id': 'tenant-norkevin-photography', 'status': 'Borrador'})
    a.store.upsert('jobs', {'id': 'foreign-journey', 'tenant_id': 'tenant-norkevin-photography'})
    assert auth_client.post('/api/quotes/foreign-journey/preparation', json={'prepare_contract': False, 'prepare_questionnaire': False}).status_code == 404
    assert auth_client.get('/api/jobs/foreign-journey/date-preview?date=2029-01-01').status_code == 404


def test_public_acceptance_closes_lead_and_starts_job_without_admin_session(client):
    import app as a
    from src.workflow.models import StepStatus
    _, lead, quote = seed(a)
    instance = a.workflow_engine.start_workflow(a.LEAD_WORKFLOW(), 'lead', lead['id'], tenant_id=TENANT)
    response = client.post('/quotes/' + quote['id'] + '/accept', json={'plan_pago': 3})
    assert response.status_code == 200
    assert all(state == StepStatus.SKIPPED for state in instance.step_states.values())
    job_id = a.store.get('quotes', quote['id'])['job_id']
    jobs = a.workflow_engine.list_instances(subject_type='job', subject_id=job_id)
    assert len(jobs) == 1
    assert jobs[0].step_states['job_accepted'] == StepStatus.DONE


def test_reminders_wait_for_resolution_and_ignore_replaced_schedule(auth_client):
    import app as a
    _, _, quote = seed(a)
    auth_client.post('/quotes/' + quote['id'] + '/accept', json={'plan_pago': 3})
    job_id = a.store.get('quotes', quote['id'])['job_id']
    with a.app.test_request_context('/'):
        session['tenant_id'] = TENANT
        pay = next(p for p in a.store.list('payments') if p.get('job_id') == job_id)
        a.check_and_send_payment_reminders()
        pay = a.store.get('payments', pay['id'])
        assert pay['reminder_delivery_status'] == 'pending'
        assert not pay.get('reminder_sent_at')
        pay['reminder_queued_at'] = '2000-01-01'
        a.store.upsert('payments', pay)
        count = len(a.store.list('pending_emails'))
        a.check_and_send_payment_reminders()
        assert len(a.store.list('pending_emails')) == count
        schedule = a._active_schedule_for(TENANT, job_id, quote['id'])
        a.supersede_schedule(schedule['id'])
        pay.pop('reminder_mail_id', None)
        a.store.upsert('payments', pay)
        a.check_and_send_payment_reminders()
        assert len(a.store.list('pending_emails')) == count
