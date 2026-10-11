"""Real validation/encryption and fake providers; no CRM data or outbound calls."""
import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from conftest import login_as_tenant
from src import web_push
from tools.run_web_push_worker import sources, tick


def browser(name='browser'):
    key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b'=').decode()
    return dict(endpoint='https://web.push.apple.com/testing/' + name,
                keys=dict(p256dh=encode(key), auth=encode(b'0123456789abcdef')))


@pytest.fixture
def push(client, monkeypatch, tmp_path):
    import app as crm
    monkeypatch.setitem(crm.app.config, 'FLOW_WEB_PUSH_ENABLED', True)
    storage = web_push.WebPushJournal(tmp_path)
    monkeypatch.setitem(crm.app.extensions, 'web_push', storage)
    monkeypatch.setattr(web_push.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('8.8.8.8', 443))])
    now = [1000.0]
    monkeypatch.setattr(web_push, 'time', SimpleNamespace(time=lambda: now[0]))
    storage.record_scan()  # Synthetic local worker heartbeat; no HTTP override.
    tenant, owner = 'web-push-owner', 'owner@example.invalid'
    login_as_tenant(client, tenant, email=owner)
    status = client.get('/api/web-push/status')
    assert status.status_code == 200
    headers = {'Origin': 'http://localhost', 'X-CSRF-Token': status.json['csrf']}
    return crm, client, storage, headers, tenant, owner, now


def subscribe(push, info=None, **extra):
    _, client, _, headers, *_ = push
    info = info or browser()
    response = client.post('/api/web-push/subscribe', json=dict(subscription=info, **extra), headers=headers)
    assert response.status_code == 200
    return info, response.json['id']


def notice(identifier='lead-one', timestamp=1001, **fields):
    return dict(id=identifier, timestamp=timestamp, title='Nuevo lead: Persona QA', url='/leads/one', **fields)


def test_subscription_is_encrypted_private_material_stable_and_public_status_has_no_keys(push):
    _, client, storage, headers, tenant, owner, _ = push
    info, identifier = subscribe(push)
    status = client.get('/api/web-push/status')
    assert status.json['available'] and status.json['subscription_ids'] == [identifier]
    assert len(base64.urlsafe_b64decode(status.json['vapid_public_key'] + '=')) == 65
    assert status.headers['Cache-Control'] == 'no-store'
    before = {name: (storage.directory / name).read_bytes() for name in ('vapid.pem', 'subscription.key')}
    storage.material()
    assert before == {name: (storage.directory / name).read_bytes() for name in before}
    for name in ('vapid.pem', 'subscription.key', 'webpush.sqlite3'):
        assert (storage.directory / name).stat().st_mode & 0o777 == 0o600
    with storage.transaction() as db:
        row = db.execute('SELECT * FROM subscriptions WHERE id=?', (identifier,)).fetchone()
        assert info['endpoint'] not in row['info'] and info['keys']['auth'] not in row['info']
        cipher, _ = storage.material()
        assert json.loads(cipher.decrypt(row['info'].encode())) == info
    assert info['endpoint'] not in status.get_data(as_text=True)


def test_status_off_does_not_create_or_rotate_keys_and_mutations_require_owner_csrf_origin(push, monkeypatch):
    crm, client, storage, headers, tenant, owner, _ = push
    keys = (storage.directory / 'vapid.pem').read_bytes()
    monkeypatch.setitem(crm.app.config, 'FLOW_WEB_PUSH_ENABLED', False)
    assert client.get('/api/web-push/status').json['available'] is False
    assert keys == (storage.directory / 'vapid.pem').read_bytes()
    fresh = web_push.WebPushJournal(storage.directory.parent / 'uninitialized')
    monkeypatch.setitem(crm.app.extensions, 'web_push', fresh)
    assert client.get('/api/web-push/status').json['vapid_public_key'] is None
    assert not (fresh.directory / 'vapid.pem').exists() and not (fresh.directory / 'subscription.key').exists()
    monkeypatch.setitem(crm.app.config, 'FLOW_WEB_PUSH_ENABLED', True)
    for changes in ({'Origin': ''}, {'Origin': 'null'}, {'Origin': 'https://other.invalid'}, {'X-CSRF-Token': 'wrong'}):
        assert client.post('/api/web-push/subscribe', json={'subscription': browser()},
                           headers=dict(headers, **changes)).status_code == 403
    login_as_tenant(client, tenant, email='collaborator@example.invalid')
    # login_as_tenant updates the test owner; put back the actual business owner.
    crm.store.upsert('tenants', dict(id=tenant, login_email=owner, active=True))
    assert client.get('/api/web-push/status').status_code in (401, 403)


def test_render_origin_requires_https_even_with_internal_http_request(push, monkeypatch):
    _, client, _, headers, *_ = push
    monkeypatch.setenv('RENDER', '1')
    cookie = client.get_cookie('session').value
    client.set_cookie('session', cookie, domain='flowingcrm.com')
    value = {'subscription': browser('render')}
    assert client.post('/api/web-push/subscribe', base_url='http://flowingcrm.com', json=value,
        headers=dict(headers, Origin='http://flowingcrm.com')).status_code == 403
    assert client.post('/api/web-push/subscribe', base_url='http://flowingcrm.com', json=value,
        headers=dict(headers, Origin='https://flowingcrm.com')).status_code == 200


@pytest.mark.parametrize('url', ['http://web.push.apple.com/x', 'https://localhost/x', 'https://127.0.0.1/x',
    'https://web.push.apple.com.evil.invalid/x', 'https://user@web.push.apple.com/x',
    'https://web.push.apple.com:444/x', 'https://web.push.apple.com/x#secret', 'https://web.push.apple.com/\nx'])
def test_unsafe_endpoint_and_strict_privacy_are_rejected(push, url):
    _, client, _, headers, *_ = push
    info = dict(browser(), endpoint=url)
    assert client.post('/api/web-push/subscribe', json={'subscription': info}, headers=headers).status_code == 400
    assert client.post('/api/web-push/subscribe', json={'subscription': browser(), 'hide_details': 'false'}, headers=headers).status_code == 400


def test_private_dns_payload_size_and_device_limit_are_rejected(push, monkeypatch):
    _, client, _, headers, *_ = push
    monkeypatch.setattr(web_push.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('10.0.0.1', 443))])
    assert client.post('/api/web-push/subscribe', json={'subscription': browser()}, headers=headers).status_code == 400
    monkeypatch.setattr(web_push.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('8.8.8.8', 443))])
    for index in range(5):
        subscribe(push, browser(str(index)))
    assert client.post('/api/web-push/subscribe', json={'subscription': browser('six')}, headers=headers).status_code == 409
    assert client.post('/api/web-push/subscribe', data='x' * 9000, content_type='application/json', headers=headers).status_code == 413


def test_owner_isolation_existing_endpoint_cannot_be_taken_and_logout_revokes_one_browser(push):
    crm, client, storage, headers, tenant, owner, _ = push
    info, first = subscribe(push, browser('first'))
    _, second = subscribe(push, browser('second'))
    login_as_tenant(client, 'other-company', email='other@example.invalid')
    other_csrf = client.get('/api/web-push/status').json['csrf']
    other_headers = dict(headers, **{'X-CSRF-Token': other_csrf})
    assert client.get('/api/web-push/status').json['subscription_ids'] == []
    assert client.post('/api/web-push/subscribe', json={'subscription': info}, headers=other_headers).status_code == 409
    assert client.post('/api/web-push/test', json={'endpoint': info['endpoint']}, headers=other_headers).status_code == 404
    assert client.post('/api/web-push/unsubscribe', json={'endpoint': info['endpoint']}, headers=other_headers).status_code == 200
    login_as_tenant(client, tenant, email=owner)
    assert client.get('/api/web-push/status').json['has_subscription']
    # Last subscribe binds only the second browser to this signed session.
    assert client.get('/logout').status_code in (200, 302)
    with storage.transaction() as db:
        assert db.execute('SELECT active FROM subscriptions WHERE id=?', (first,)).fetchone()[0] == 1
        assert db.execute('SELECT active FROM subscriptions WHERE id=?', (second,)).fetchone()[0] == 0


def test_test_push_is_current_browser_only_rate_limited_and_private(push):
    crm, client, storage, headers, tenant, owner, now = push
    info, identifier = subscribe(push)
    assert client.post('/api/web-push/test', json={'endpoint': info['endpoint']}, headers=headers).status_code == 200
    assert client.post('/api/web-push/test', json={'endpoint': info['endpoint']}, headers=headers).status_code == 429
    seen = []
    assert web_push.drain(crm.app, crm.store, [], sender=lambda *args: seen.append(args) or 201) == 1
    assert len(seen) == 1 and seen[0][0] == info
    payload = seen[0][1]
    assert payload['hide_details'] and payload['url'] == '/notifications' and 'Persona QA' not in payload['body']
    assert payload['title'] == 'Flowing' and len(json.dumps(payload).encode()) < 2048


def test_baseline_optin_pause_restart_retry_and_owner_revocation(push, monkeypatch):
    crm, client, storage, headers, tenant, owner, now = push
    info, identifier = subscribe(push)
    report = [(tenant, owner, [notice(timestamp=999)])]
    storage.observe(report, sending=True)
    with storage.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0] == 0
    now[0] = 1002
    report = [(tenant, owner, [notice(timestamp=999), notice('lead-new', 1001)])]
    storage.observe(report, sending=True)
    assert web_push.drain(crm.app, crm.store, report, sender=lambda *args: 503) == 0
    # Reopening the journal is a process restart, not an explicit push pause.
    reopened = web_push.WebPushJournal(storage.directory.parent)
    monkeypatch.setitem(crm.app.extensions, 'web_push', reopened)
    now[0] += 100
    reopened.observe(report, sending=True)
    assert web_push.drain(crm.app, crm.store, report, sender=lambda *args: 201) == 1
    reopened.pause()
    now[0] += 1
    paused_history = report[0][2] + [notice('lead-during-pause', now[0])]
    reopened.observe([(tenant, owner, paused_history)], sending=True)
    with reopened.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM deliveries WHERE status='queued'").fetchone()[0] == 0
    now[0] += 1
    newest = paused_history + [notice('lead-after-resume', now[0])]
    reopened.observe([(tenant, owner, newest)], sending=True)
    crm.store.upsert('tenants', dict(id=tenant, login_email='replacement@example.invalid', active=True))
    assert web_push.drain(crm.app, crm.store, [(tenant, owner, newest)], sender=lambda *args: pytest.fail('Changed owner must not receive data')) == 0


def test_later_subscription_cannot_receive_history_and_push_off_cancels_without_sender(push, monkeypatch):
    crm, client, storage, headers, tenant, owner, now = push
    subscribe(push, browser('first'))
    storage.observe([(tenant, owner, [])], sending=True)
    now[0] = 1100
    _, newest = subscribe(push, browser('later'))
    storage.observe([(tenant, owner, [notice(timestamp=1050)])], sending=True)
    with storage.transaction() as db:
        assert {row[0] for row in db.execute('SELECT subscription_id FROM deliveries')} != {newest}
        assert db.execute('SELECT COUNT(*) FROM deliveries WHERE subscription_id=?', (newest,)).fetchone()[0] == 0
    storage.observe([], sending=False)
    monkeypatch.setitem(crm.app.config, 'FLOW_WEB_PUSH_ENABLED', False)
    assert web_push.drain(crm.app, crm.store, [], sender=lambda *args: pytest.fail('Disabled push must not send')) == 0
    with storage.transaction() as db:
        assert all(row[0] == 'cancelled' for row in db.execute('SELECT status FROM deliveries'))


def test_provider_real_encryption_uses_no_redirects_or_proxy_and_does_not_expose_secrets(push, monkeypatch):
    from requests import Response
    from requests.sessions import Session
    _, _, storage, *_ = push
    calls = []
    def fake_send(self, prepared, **kwargs):
        calls.append((self, prepared, kwargs))
        response = Response()
        response.status_code = 201
        response._content = b''
        response.headers = {}
        return response
    monkeypatch.setattr(Session, 'send', fake_send)
    info = browser('encrypted')
    payload = web_push.notification_payload('tenant', notice(), True)
    assert web_push.send_subscription(info, payload, storage.directory) == 201
    transport, prepared, options = calls[0]
    assert transport.trust_env is False and options['allow_redirects'] is False and options['timeout'] == 15
    assert prepared.url == info['endpoint'] and prepared.headers['Content-Encoding'] == 'aes128gcm'
    assert b'Tienes una nueva actividad' not in prepared.body and b'Persona QA' not in prepared.body
    assert prepared.headers['Authorization'].startswith('vapid ')


def test_worker_uses_web_sources_scoped_without_writing_crm_json(push, monkeypatch):
    crm, client, storage, headers, tenant, owner, now = push
    client._context_stack.close()
    crm.store.upsert('leads', dict(id='one', tenant_id=tenant, nombre='Propio QA', status='Nuevo', created='2099-01-01T12:00:00Z'))
    crm.store.upsert('leads', dict(id='foreign', tenant_id='other-company', nombre='Ajeno QA', status='Nuevo', created='2099-01-01T12:00:00Z'))
    before = {p.name: p.read_bytes() for p in Path(crm.store.data_dir).glob('*.json')}
    reports = sources(crm.app, crm.store)
    own = next(events for company, recipient, events in reports if company == tenant)
    assert [event['id'] for event in own] == ['lead-one']
    assert crm._workflow_tenant.get() is None
    assert before == {p.name: p.read_bytes() for p in Path(crm.store.data_dir).glob('*.json')}
    assert tick(crm.app, crm.store, sender=lambda *args: pytest.fail('Baseline must not send history')) == 0


def test_provider_gone_disables_subscription_and_privacy_change_hides_already_queued_details(push):
    crm, client, storage, headers, tenant, owner, now = push
    info, identifier = subscribe(push, hide_details=False)
    storage.observe([(tenant, owner, [])], sending=True)
    now[0] += 2
    reports = [(tenant, owner, [notice(timestamp=1001)])]
    storage.observe(reports, sending=True)
    subscribe(push, info, hide_details=True)
    seen = []
    assert web_push.drain(crm.app, crm.store, reports, sender=lambda info, payload, path: seen.append(payload) or 410) == 0
    assert seen[0]['hide_details'] and seen[0]['url'] == '/notifications'
    assert 'Persona QA' not in seen[0]['body']
    with storage.transaction() as db:
        assert db.execute('SELECT active FROM subscriptions WHERE id=?', (identifier,)).fetchone()[0] == 0


def test_revoke_browser_cancels_only_bound_browser_pending(push):
    crm, client, storage, headers, tenant, owner, _ = push
    first, first_id = subscribe(push, browser('first'))
    second, second_id = subscribe(push, browser('second'))
    for info in (first, second):
        assert client.post('/api/web-push/test', json={'endpoint': info['endpoint']}, headers=headers).status_code == 200
    assert client.get('/logout').status_code in (200, 302)
    with storage.transaction() as db:
        rows = {row['subscription_id']: row['status'] for row in db.execute('SELECT * FROM deliveries')}
    assert rows == {first_id: 'queued', second_id: 'cancelled'}


def test_web_source_includes_scoped_secondary_teams_without_calendar_requests(push, monkeypatch, tmp_path):
    from src.teams import TeamsStore
    crm, client, storage, headers, tenant, owner, _ = push
    client._context_stack.close()
    crm.store.upsert('jobs', dict(id='parent', tenant_id=tenant, nombre='Boda QA', status='Confirmado', boda_date='2099-01-02'))
    crm.store.upsert('calendar', dict(id='civil', tenant_id=tenant, job_id='parent', type='event', title='Boda civil', date='2099-01-01'))
    database = TeamsStore(tmp_path / 'teams.sqlite3')
    monkeypatch.setitem(crm.app.extensions, 'teams', database)
    with database.transaction() as db:
        person = database.create(db, tenant, 'member', name='Persona QA', email='person@example.invalid')
        audit = database.create(db, tenant, 'audit', action='calendar_response', created_at='2099-01-01T12:00:00Z',
            after=dict(member_id=person['id'], job_id='secondary:civil', status='aceptada'))
        database.create(db, 'other-company', 'audit', action='response', created_at='2099-01-01T13:00:00Z',
            after=dict(member_id=person['id'], job_id='secondary:civil', status='rechazada'))
    reports = sources(crm.app, crm.store)
    own = next(events for company, recipient, events in reports if company == tenant)
    assert len(own) == 1 and own[0]['id'] == 'teams-response-' + audit['id']
    assert own[0]['url'] == '/teams/jobs/secondary:civil'


def test_worker_bootstrap_does_not_import_wsgi_or_register_calendar(push, monkeypatch):
    import builtins
    from tools.run_web_push_worker import load_app
    crm, *_ = push
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert name not in ('wsgi', 'src.teams_calendar_routes', 'src.teams_calendar')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    loaded, store = load_app()
    assert loaded is crm.app and store is crm.store


def test_worker_health_is_honest_but_browser_management_remains_available(push):
    crm, client, storage, headers, tenant, owner, now = push
    info, identifier = subscribe(push)
    now[0] += 121
    status = client.get('/api/web-push/status').json
    assert status['enabled'] and not status['available'] and not status['worker_ready']
    response = client.post('/api/web-push/subscribe', json={'subscription': info}, headers=headers)
    assert response.status_code == 200 and response.json['worker_ready'] is False
    assert client.post('/api/web-push/test', json={'endpoint': info['endpoint']}, headers=headers).status_code == 503
    assert client.post('/api/web-push/unsubscribe', json={'endpoint': info['endpoint']}, headers=headers).status_code == 200


def test_fresh_cookie_binds_existing_browser_without_reactivation_or_key_changes(push, monkeypatch):
    crm, client, storage, headers, tenant, owner, now = push
    info, identifier = subscribe(push, browser('bound'))
    _, other = subscribe(push, browser('other'))
    with storage.transaction() as db:
        before = tuple(db.execute('SELECT * FROM subscriptions WHERE id=?', (identifier,)).fetchone())
    material = {name: (storage.directory / name).read_bytes() for name in ('vapid.pem', 'subscription.key')}
    with client.session_transaction() as state:
        state.clear()
    login_as_tenant(client, tenant, email=owner)
    monkeypatch.setitem(crm.app.config, 'FLOW_WEB_PUSH_ENABLED', False)
    status = client.get('/api/web-push/status').json
    headers = dict(headers, **{'X-CSRF-Token': status['csrf']})
    assert identifier in status['subscription_ids']
    assert client.post('/api/web-push/bind', json={'endpoint': info['endpoint']}, headers=headers).json == {'ok': True, 'id': identifier}
    with storage.transaction() as db:
        assert before == tuple(db.execute('SELECT * FROM subscriptions WHERE id=?', (identifier,)).fetchone())
    assert material == {name: (storage.directory / name).read_bytes() for name in material}
    assert client.get('/logout').status_code in (200, 302)
    with storage.transaction() as db:
        assert db.execute('SELECT active FROM subscriptions WHERE id=?', (identifier,)).fetchone()[0] == 0
        assert db.execute('SELECT active FROM subscriptions WHERE id=?', (other,)).fetchone()[0] == 1
    login_as_tenant(client, tenant, email=owner)
    headers['X-CSRF-Token'] = client.get('/api/web-push/status').json['csrf']
    assert client.post('/api/web-push/bind', json={'endpoint': info['endpoint']}, headers=headers).status_code == 404
    login_as_tenant(client, 'other-company', email='another@example.invalid')
    headers['X-CSRF-Token'] = client.get('/api/web-push/status').json['csrf']
    assert client.post('/api/web-push/bind', json={'endpoint': browser('other')['endpoint']}, headers=headers).status_code == 404


@pytest.mark.parametrize('failure', ['leads', 'teams'])
def test_incomplete_source_snapshot_keeps_retry_observation_and_heartbeat(push, monkeypatch, failure):
    crm, client, storage, headers, tenant, owner, now = push
    subscribe(push)
    client._context_stack.close()
    assert tick(crm.app, crm.store, sender=lambda *args: 201) == 0
    now[0] += 2
    crm.store.upsert('leads', dict(id='one', tenant_id=tenant, nombre='Nuevo QA', status='Nuevo', created='2099-01-01T12:00:00Z'))
    assert tick(crm.app, crm.store, sender=lambda *args: 503) == 0
    def snapshot():
        with storage.transaction() as db:
            return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table)]
                    for table in ('deliveries', 'observed', 'worker_health')}
    before = snapshot()
    assert before['deliveries'][0][7] == 'retry'
    now[0] += 200
    if failure == 'leads':
        original = crm.store.list
        def broken(table, **kwargs):
            if table == 'leads':
                raise OSError('fixture source failure')
            return original(table, **kwargs)
        monkeypatch.setattr(crm.store, 'list', broken)
    else:
        class BrokenTeams:
            def transaction(self):
                raise OSError('fixture Teams failure')
        monkeypatch.setitem(crm.app.extensions, 'teams', BrokenTeams())
    with pytest.raises(OSError):
        tick(crm.app, crm.store, sender=lambda *args: pytest.fail('Incomplete snapshot must not drain'))
    assert snapshot() == before
    assert storage.worker_ready() is False
