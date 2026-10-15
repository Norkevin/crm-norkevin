"""A slow provider must not freeze navigation or release a duplicate send."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from test_workflow_automatic_delivery import automatic


def test_inbox_is_read_only_even_when_workflow_queue_is_busy(auth_client, monkeypatch):
    import app as a
    monkeypatch.setattr(a, '_prepare_due_workflow_emails',
                        lambda **kwargs: pytest.fail('Inbox must not run the workflow queue'))
    assert auth_client.get('/emails').status_code == 200


def test_inbox_batches_names_and_does_not_revalidate_completed_deliveries(auth_client, monkeypatch):
    import app as a
    tenant = 'tenant-norkevin'
    a.store.upsert('clients', dict(id='navigation-client', tenant_id=tenant, first_name='Ana navegación'))
    a.store.upsert('jobs', dict(id='navigation-job', tenant_id=tenant, nombre='Boda navegación'))
    for n in range(30):
        a.store.upsert('pending_emails', dict(id=f'navigation-mail-{n}', tenant_id=tenant,
            client_id='navigation-client', job_id='navigation-job', status='sent', subject='Hola'))
    monkeypatch.setattr(a._mail_tracker_module, '_commercial_check',
                        lambda mail: pytest.fail('Sent mail is historical; do not validate new travel fees'))
    reads = []
    original = a.store.list
    monkeypatch.setattr(a.store, 'list', lambda table, **kwargs: reads.append(table) or original(table, **kwargs))
    response = auth_client.get('/emails')
    assert response.status_code == 200 and 'Ana navegación'.encode() in response.data
    assert reads.count('clients') < 5 and reads.count('jobs') < 5


def test_uncertain_delivery_stays_claimed_and_cannot_auto_resend(automatic, monkeypatch):
    a, setup, _ = automatic
    tenant = 'tenant-norkevin'
    _, instance = setup(tenant)
    due = instance.trigger_at.replace(hour=15)
    mail_id = a._prepare_due_workflow_emails(tenant_id=tenant, now=due)[0]['mail_id']
    attempts = []

    def uncertain(*args, **kwargs):
        attempts.append(args)
        raise RuntimeError('Synthetic connection closed after provider accepted the message')

    monkeypatch.setattr('src.mail_tracker.send_email', uncertain)
    a._send_due_workflow_emails(now=due)
    token = a._workflow_tenant.set(tenant)
    try:
        assert a.store.get('pending_emails', mail_id)['status'] == 'sending'
    finally:
        a._workflow_tenant.reset(token)
    assert not a._send_due_workflow_emails(now=due) and len(attempts) == 1


@pytest.mark.parametrize('mode', ['manual', 'workflow', 'package', 'internal', 'retry'])
def test_navigation_and_claim_remain_available_during_provider_wait(automatic, monkeypatch, mode):
    import app as a
    from src.email_delivery import DeliveryResult
    from src.mail_tracker import MailTracker
    from conftest import login_as_tenant

    _, setup, _ = automatic
    tenant = 'tenant-norkevin'
    lead, instance = setup(tenant)
    due = instance.trigger_at.replace(hour=15)
    tracker = MailTracker()
    token = a._workflow_tenant.set(tenant)
    try:
        mail = tracker.queue_email(lead['email'], 'Hola', 'Mensaje de prueba',
                                   tenant_id=tenant, lead_id=lead['id'], client_id=lead['client_id'])
        if mode == 'workflow':
            mail = a.store.get('pending_emails', a._prepare_due_workflow_emails(tenant_id=tenant, now=due)[0]['mail_id'])
        if mode == 'retry':
            mail['status'] = 'failed'
            a.store.upsert('pending_emails', mail)
        if mode == 'internal':
            a.store.save_tenant_dict('settings', {'company': {'email': 'owner@example.invalid'}}, tenant)
        if mode == 'package':
            monkeypatch.setattr('src.mail_tracker._package_auto_delivery_enabled', lambda entry: True)
    finally:
        a._workflow_tenant.reset(token)

    started, release, read_done = Event(), Event(), Event()
    calls = []

    def provider(*args, **kwargs):
        calls.append(args)
        started.set()
        assert release.wait(5), 'Test must release the synthetic provider'
        return DeliveryResult(ok=True, provider='test', mode='test')

    monkeypatch.setattr('src.mail_tracker.send_email', provider)

    def send():
        with a.app.app_context():
            token = a._workflow_tenant.set(tenant)
            try:
                if mode == 'workflow':
                    return a._send_due_workflow_emails(tenant_id=tenant, now=due)
                if mode == 'package':
                    return tracker.queue_email(lead['email'], 'Hola automático', 'Mensaje', tenant_id=tenant,
                                               lead_id=lead['id'], client_id=lead['client_id'])
                if mode == 'internal':
                    return tracker.send_new_lead_notification(to_email='owner@example.invalid', subject='Nuevo lead',
                                                             body='Aviso', tenant_id=tenant, lead_id=lead['id'])
                if mode == 'retry':
                    return tracker.retry_failed(mail['id'], sender_tenant_id=tenant)
                return tracker.approve_and_send(mail['id'], sender_tenant_id=tenant)
            finally:
                a._workflow_tenant.reset(token)

    def read():
        with a.app.test_client() as client:
            login_as_tenant(client, tenant, email='owner@example.invalid')
            assert client.get('/emails').status_code == 200
            if mode == 'workflow':
                assert client.post(f"/api/leads/{lead['id']}/steps/auto_step/skip", json={}).status_code == 409
        with a.app.app_context():
            token = a._workflow_tenant.set(tenant)
            try:
                sending = next(m for m in a.store.list('pending_emails') if m.get('status') == 'sending')
                assert isinstance(a.store.list('mail_log'), list)
                assert tracker.approve_and_send(sending['id'], sender_tenant_id=tenant)['ok'] is False
                assert tracker.discard_pending(sending['id'])['ok'] is False
            finally:
                a._workflow_tenant.reset(token)
        read_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        sending = pool.submit(send)
        try:
            assert started.wait(2)
            reading = pool.submit(read)
            assert read_done.wait(2), 'Navigation is blocked behind the email provider'
        finally:
            release.set()
        sending.result(timeout=5)
        reading.result(timeout=5)
    assert len(calls) == 1
