import copy
import uuid
import pytest
from conftest import login_as_tenant
from src.mail_tracker import MailTracker, MISSING_CLIENT_WARNING, NEW_LEAD_NOTIFICATION, check_recipient_identity


@pytest.fixture(params=['tenant-norkevin', 'tenant-norkevin-photography'])
def notice(client, request, monkeypatch):
    import app as m
    tenant = request.param
    snapshots = {t: copy.deepcopy(m.store._read_raw(t)) for t in ('leads', 'pending_emails', 'mail_log')}
    settings = copy.deepcopy(m.store.get_tenant_dict('settings', tenant_id=tenant))
    login_as_tenant(client, tenant, email='owner@example.invalid')
    m.store.save_tenant_dict('settings', {'company': {'email': 'company@example.invalid'}}, tenant_id=tenant)
    lead = {'id': 'lead-internal-' + uuid.uuid4().hex[:8], 'tenant_id': tenant,
            'nombre': 'Cindy Prueba', 'email': 'cindy@example.invalid'}
    m.store.upsert('leads', lead)
    calls = []
    from src.email_delivery import DeliveryResult
    monkeypatch.setattr('src.mail_tracker.send_email', lambda *a, **kw: calls.append((a, kw)) or DeliveryResult(ok=True, provider='test', mode='test'))
    with client.application.test_request_context():
        from flask import session
        session['tenant_id'] = tenant
        MailTracker().queue_email(
            'company@example.invalid', 'Nuevo lead: Cindy Prueba', 'Aviso de prueba',
            lead_id=lead['id'], tenant_id=tenant, source=NEW_LEAD_NOTIFICATION,
            idempotency_key=f"leadnotify:{lead['id']}")
    pending = next(p for p in m.store._read_raw('pending_emails') if p.get('lead_id') == lead['id'])
    yield client, m, tenant, lead, pending, calls
    for table, rows in snapshots.items():
        m.store._save(table, rows)
    m.store.save_tenant_dict('settings', settings, tenant_id=tenant)


def test_notice_is_verified_without_sending_and_preview_agrees(notice):
    client, m, tenant, lead, pending, calls = notice
    assert pending['status'] == 'pending'
    assert pending['to'] == 'company@example.invalid'
    assert not pending['aviso_identidad']
    assert not calls
    detail = client.get('/api/pending-emails/' + pending['id']).get_json()
    assert detail['ok'] and detail['motivo'] is None and detail['aviso_identidad'] is None
    assert detail['email']['aviso_interno']
    html = client.get('/emails').get_data(as_text=True)
    assert 'Aviso interno para ti' in html
    assert not calls


def test_legacy_warning_clears_in_view_but_other_warning_and_content_survive(notice):
    client, m, tenant, lead, pending, calls = notice
    pending['aviso_identidad'] = MISSING_CLIENT_WARNING + ' | El contenido cambió'
    m.store.upsert('pending_emails', pending)
    before = copy.deepcopy(pending)
    data = client.get('/api/pending-emails/' + pending['id']).get_json()
    assert data['email']['aviso_identidad'] == 'El contenido cambió'
    assert m.store.get('pending_emails', pending['id']) == before
    assert not calls


@pytest.mark.parametrize('change', ['recipient', 'lead-owner', 'missing-lead', 'settings'])
def test_changed_identity_blocks_preview_and_delivery(notice, change):
    client, m, tenant, lead, pending, calls = notice
    if change == 'recipient':
        pending['to'] = 'other@example.invalid'
        m.store.upsert('pending_emails', pending)
    elif change == 'lead-owner':
        lead['tenant_id'] = 'tenant-norkevin' if tenant != 'tenant-norkevin' else 'tenant-norkevin-photography'
        m.store.upsert('leads', lead)
    elif change == 'missing-lead':
        m.store.delete('leads', lead['id'])
    else:
        m.store.save_tenant_dict('settings', {'company': {'email': 'changed@example.invalid'}}, tenant_id=tenant)
    data = client.get('/api/pending-emails/' + pending['id']).get_json()
    assert data['motivo'] and not data['enviable']
    assert not data['email']['aviso_interno']
    response = client.post('/api/pending-emails/' + pending['id'] + '/send')
    assert response.status_code == 400
    assert response.get_json()['email']['estado'] == 'blocked'
    assert not calls


def test_valid_internal_notice_still_requires_explicit_approval(notice):
    client, m, tenant, lead, pending, calls = notice
    assert not calls
    response = client.post('/api/pending-emails/' + pending['id'] + '/send')
    assert response.status_code == 200
    assert response.get_json()['email']['estado'] == 'sent'
    assert len(calls) == 1
    assert client.post('/api/pending-emails/' + pending['id'] + '/send').status_code == 400
    assert len(calls) == 1


def test_unknown_source_keeps_client_warning_and_no_fallback_to_other_brand(notice):
    client, m, tenant, lead, pending, calls = notice
    assert check_recipient_identity(tenant, pending['to'], None, source='manual', lead_id=lead['id']) == (None, MISSING_CLIENT_WARNING)
    m.store.save_tenant_dict('settings', {}, tenant_id=tenant)
    from src.mail_tracker import new_lead_notification_recipient
    assert new_lead_notification_recipient(tenant) == 'owner@example.invalid'
    row = next(t for t in m.store.list('tenants') if t['id'] == tenant)
    row['login_email'] = ''
    m.store.upsert('tenants', row)
    assert new_lead_notification_recipient(tenant) == ''
    reason, _ = check_recipient_identity(tenant, 'norkevinfoto@gmail.com', None, source=NEW_LEAD_NOTIFICATION, lead_id=lead['id'])
    assert reason and not calls


def _notify_fresh(notice, lead=None):
    client, m, tenant, original, pending, calls = notice
    lead = lead or dict(original, id='lead-auto-' + uuid.uuid4().hex[:8])
    m.store.upsert('leads', lead)
    with client.application.test_request_context():
        from flask import g
        g.public_tenant_id = tenant
        m._notify_new_lead(lead, 'Formulario de prueba')
    entry = next(p for p in m.store._read_raw('pending_emails') if p.get('lead_id') == lead['id'])
    return lead, entry


def test_new_internal_notice_is_automatic_exactly_once_and_clients_stay_pending(notice):
    client, m, tenant, original, pending, calls = notice
    lead, entry = _notify_fresh(notice)
    assert entry['status'] == 'sent'
    assert entry['to'] == 'company@example.invalid'
    assert 'Abrir ficha del lead' in entry['html_body']
    assert calls[0][1]['metadata']['html_body'] == entry['html_body']
    logged = next(mail for mail in m.store.list('mail_log') if mail.get('lead_id') == lead['id'])
    assert logged['html_body'] == entry['html_body']
    assert all(h['actor'] == 'sistema:aviso-interno' for h in entry['historial'])
    assert len(calls) == 1
    _notify_fresh(notice, lead)
    assert len(calls) == 1
    with client.application.test_request_context():
        from flask import g
        g.public_tenant_id = tenant
        client_mail = MailTracker().queue_email(
            original['email'], 'Bienvenida', 'Para el cliente', lead_id=original['id'],
            tenant_id=tenant, source='workflow:welcome')
    assert client_mail['status'] == 'pending'
    assert len(calls) == 1
    assert m.store.get('pending_emails', pending['id'])['status'] == 'pending'


@pytest.mark.parametrize('failure', ['provider', 'exception', 'kill-switch'])
def test_auto_notice_keeps_failed_attempt_without_automatic_retry(notice, monkeypatch, failure):
    client, m, tenant, original, pending, calls = notice
    from src.email_delivery import DeliveryResult, send_email
    if failure == 'kill-switch':
        monkeypatch.setenv('DISABLE_OUTBOUND_EMAIL', '1')
        monkeypatch.setattr('src.mail_tracker.send_email', send_email)
    else:
        def fail(*args, **kwargs):
            calls.append(args)
            if failure == 'exception':
                raise TimeoutError('Entrega incierta')
            return DeliveryResult(ok=False, provider='test', error='Fallo de prueba')
        monkeypatch.setattr('src.mail_tracker.send_email', fail)
    lead, entry = _notify_fresh(notice)
    assert entry['status'] == 'failed'
    attempts = len(calls)
    _, second = _notify_fresh(notice, lead)
    assert second['id'] == entry['id'] and len(calls) == attempts
    assert len([p for p in m.store._read_raw('pending_emails') if p.get('lead_id') == lead['id']]) == 1


def test_internal_auto_send_cannot_target_a_client_or_another_company(notice):
    client, m, tenant, original, pending, calls = notice
    with client.application.test_request_context():
        from flask import g
        g.public_tenant_id = tenant
        lead = dict(original, id='lead-misdirected-' + uuid.uuid4().hex[:8])
        m.store.upsert('leads', lead)
        result = MailTracker().send_new_lead_notification(
            to_email=lead['email'], subject='Nuevo lead', body='Aviso', lead_id=lead['id'], tenant_id=tenant)
        assert result['status'] == 'blocked'
    assert not calls


def test_repeated_callback_does_not_release_legacy_pending_notice(notice):
    client, m, tenant, original, pending, calls = notice
    _, entry = _notify_fresh(notice, original)
    assert entry['id'] == pending['id'] and entry['status'] == 'pending'
    assert not calls
