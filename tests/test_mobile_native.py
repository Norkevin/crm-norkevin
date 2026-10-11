"""Native API security, durable events and APNs with synthetic data only."""
import secrets
import time
from datetime import date

import pytest
from cryptography.fernet import Fernet
from conftest import login_as_tenant
from src.mobile import MobileJournal, digest, source_events
from src.mobile_push import apns_jwt, drain


@pytest.fixture
def mobile(client, monkeypatch, tmp_path):
    import app as crm
    monkeypatch.setitem(crm.app.config, 'FLOW_MOBILE_ENABLED', True)
    db = MobileJournal(tmp_path / 'mobile.sqlite3')
    monkeypatch.setitem(crm.app.extensions, 'mobile', db)
    monkeypatch.setenv('FLOW_MOBILE_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('FLOW_MOBILE_PUSH_ENABLED', '0')
    token = secrets.token_urlsafe(32)
    owner = 'owner@example.invalid'
    tenant = 'native-test-owner'
    crm.store.upsert('tenants', dict(id=tenant, name='Estudio QA', login_email=owner, active=True))
    with db.transaction() as conn:
        conn.execute('INSERT INTO sessions(token_hash,tenant_id,recipient,expires) VALUES (?,?,?,?)',
                     (digest(token), tenant, owner, time.time() + 600))
    return crm, client, db, dict(Authorization='Bearer ' + token), tenant, owner


def event(key='one', tenant='unused'):
    return dict(key='lead:' + key, kind='lead', title='Nuevo lead QA', table='leads', id=key)


def test_mobile_disabled_does_not_create_database(client, monkeypatch):
    import app as crm
    monkeypatch.setitem(crm.app.config, 'FLOW_MOBILE_ENABLED', False)
    assert client.post('/api/mobile/pair/start', json={}).status_code == 404
    assert client.get('/api/mobile/overview').status_code == 404


def test_bearer_requires_active_owner_and_cannot_authenticate_web(mobile):
    crm, client, db, headers, tenant, owner = mobile
    assert client.get('/api/mobile/overview').status_code == 401
    assert client.get('/api/mobile/overview', headers=headers).json['tenant_name'] == 'Estudio QA'
    assert client.get('/api/leads', headers=headers).status_code == 401
    crm.store.upsert('tenants', dict(id=tenant, login_email=owner, active=False))
    assert client.get('/api/mobile/overview', headers=headers).status_code == 401


def test_bearer_overrides_foreign_cookie_and_revalidates_targets(mobile):
    crm, client, db, headers, tenant, owner = mobile
    crm.store.upsert('leads', dict(id='owned', tenant_id=tenant, nombre='Propio', status='Nuevo'))
    crm.store.upsert('leads', dict(id='foreign', tenant_id='other-company', nombre='Ajeno', status='Nuevo'))
    login_as_tenant(client, 'other-company', email='other@example.invalid')
    page = client.get('/api/mobile/records/leads', headers=headers).json
    assert [r['id'] for r in page['records']] == ['owned']
    assert client.get('/api/mobile/records/leads/foreign', headers=headers).status_code == 404
    assert client.get('/api/mobile/records/leads/owned', headers=headers).status_code == 200
    with client.session_transaction() as sess:
        assert sess['tenant_id'] == 'other-company'


def test_pairing_requires_explicit_browser_owner_and_csrf(mobile):
    crm, client, db, headers, tenant, owner = mobile
    pair = client.post('/api/mobile/pair/start', json={}).json
    assert client.post('/api/mobile/pair/poll', json={'device_secret': pair['code']}).status_code == 401
    login_as_tenant(client, tenant, email=owner)
    assert client.get('/mobile/connect').status_code == 200
    assert client.post('/mobile/connect', data={'code': pair['code']}).status_code == 403
    with client.session_transaction() as sess:
        csrf = sess['mobile_csrf']
    assert client.post('/mobile/connect', data={'code': pair['code'], 'csrf': csrf}, headers={'Origin': 'https://evil.invalid'}).status_code == 403
    assert client.post('/mobile/connect', data={'code': pair['code'], 'csrf': csrf}).status_code == 200
    result = client.post('/api/mobile/pair/poll', json={'device_secret': pair['device_secret']})
    assert result.status_code == 200 and len(result.json['token']) == 43
    with db.transaction() as conn:
        assert result.json['token'] not in str([dict(r) for r in conn.execute('SELECT * FROM sessions')])
    assert client.post('/api/mobile/pair/poll', json={'device_secret': pair['device_secret']}).status_code == 401


def test_pairing_rate_limit_and_expiry(mobile):
    crm, client, db, *_ = mobile
    pair = client.post('/api/mobile/pair/start', json={}).json
    assert client.post('/api/mobile/pair/poll', json={'device_secret': pair['device_secret']}).status_code == 202
    assert client.post('/api/mobile/pair/poll', json={'device_secret': pair['device_secret']}).status_code == 429
    with db.transaction() as conn:
        conn.execute('UPDATE pairings SET expires=0')
    assert client.post('/api/mobile/pair/poll', json={'device_secret': pair['device_secret']}).status_code == 401
    for _ in range(4):
        assert client.post('/api/mobile/pair/start', json={}).status_code == 200
    assert client.post('/api/mobile/pair/start', json={}).status_code == 429


def test_journal_baseline_and_100_retries_are_idempotent(mobile):
    crm, client, db, headers, tenant, owner = mobile
    db.observe(tenant, owner, [event('old')])
    assert client.get('/api/mobile/notifications', headers=headers).json['count'] == 0
    for _ in range(100):
        db.observe(tenant, owner, [event('old'), event('new')])
    page = client.get('/api/mobile/notifications', headers=headers)
    assert page.headers['Cache-Control'] == 'no-store'
    assert page.json['count'] == 1 and len(page.json['notifications']) == 1


def test_pagination_keeps_events_with_identical_timestamps(mobile, monkeypatch):
    crm, client, db, headers, tenant, owner = mobile
    db.observe(tenant, owner, [])
    monkeypatch.setattr('src.mobile.time.time', lambda: 1000)
    db.observe(tenant, owner, [event(str(i)) for i in range(105)])
    # Session was created with a real future expiry, so the frozen clock is valid.
    seen, before = [], ''
    while True:
        page = client.get('/api/mobile/notifications' + before, headers=headers).json
        seen.extend(n['id'] for n in page['notifications'])
        if page['next_before'] is None:
            break
        before = '?before=' + str(page['next_before'])
    assert len(seen) == len(set(seen)) == 105


def test_inbox_preferences_read_and_deep_link_isolation(mobile):
    crm, client, db, headers, tenant, owner = mobile
    db.observe(tenant, owner, [])
    db.observe(tenant, owner, [event('owned')])
    page = client.get('/api/mobile/notifications', headers=headers).json
    nid = page['notifications'][0]['id']
    assert client.get(f'/api/mobile/notifications/{nid}/target', headers=headers).status_code == 404
    assert client.post('/api/mobile/notifications/read', json={'ids': [nid, 'foreign']}, headers=headers).status_code == 404
    assert client.get('/api/mobile/notifications', headers=headers).json['count'] == 1
    assert client.post('/api/mobile/notifications/read', json={'ids': [nid]}, headers=headers).status_code == 200
    assert client.get('/api/mobile/notifications', headers=headers).json['count'] == 0
    assert client.post('/api/mobile/preferences', json={'lead': False}, headers=headers).json['preferences']['lead'] is False
    assert client.post('/api/mobile/preferences', json={'lead': 'false'}, headers=headers).status_code == 400
    assert client.post('/api/mobile/preferences', json={'tenant_id': tenant}, headers=headers).status_code == 400


def test_logout_revokes_bearer_and_device(mobile):
    crm, client, db, headers, *_ = mobile
    assert client.post('/api/mobile/device', json={'token': 'a' * 64, 'environment': 'sandbox', 'revision': 0}, headers=headers).status_code == 200
    with db.transaction() as conn:
        assert conn.execute('SELECT device FROM sessions').fetchone()[0] != 'a' * 64
    assert client.post('/api/mobile/logout', json={}, headers=headers).status_code == 200
    assert client.get('/api/mobile/overview', headers=headers).status_code == 401
    with db.transaction() as conn:
        row = conn.execute('SELECT * FROM sessions').fetchone()
        assert row['revoked'] and not row['push_enabled'] and row['device'] is None


def test_sources_only_confirmed_payments_and_current_wedding_date(mobile):
    crm, client, db, headers, tenant, owner = mobile
    crm.store.upsert('jobs', dict(id='job', tenant_id=tenant, nombre='Boda QA', status='Confirmado', boda_date='2030-01-08'))
    for status in ('Pendiente', 'Fallido', 'Pagado'):
        crm.store.upsert('payments', dict(id=status, tenant_id=tenant, status=status, job_id='job'))
    events = source_events(crm.store, tenant, today=date(2030, 1, 1))
    assert [e['key'] for e in events if e['kind'] == 'payment'] == ['payment:Pagado']
    assert [e['key'] for e in events if e['kind'] == 'wedding'] == ['wedding:job:2030-01-08:7']
    crm.store.upsert('jobs', dict(id='job', tenant_id=tenant, status='Confirmado', boda_date='2030-01-20'))
    assert not [e for e in source_events(crm.store, tenant, today=date(2030, 1, 1)) if e['kind'] == 'wedding']


def prepared_delivery(mobile):
    crm, client, db, headers, tenant, owner = mobile
    client.post('/api/mobile/device', json={'token': 'b' * 64, 'environment': 'sandbox', 'revision': 0}, headers=headers)
    db.observe(tenant, owner, [])
    db.observe(tenant, owner, [event()])
    return {tenant: {'lead:one'}}


def test_push_opt_in_provider_acceptance_and_no_duplicate(mobile, monkeypatch):
    crm, client, db, headers, tenant, owner = mobile
    keys = prepared_delivery(mobile)
    calls = []
    def fake(*args):
        calls.append(args)
        return 200
    assert drain(db, crm.store, keys, sender=fake) == 0
    monkeypatch.setenv('FLOW_MOBILE_PUSH_ENABLED', '1')
    assert drain(db, crm.store, keys, sender=fake) == 1
    assert drain(db, crm.store, keys, sender=fake) == 0
    assert len(calls) == 1 and calls[0][0] == 'b' * 64 and calls[0][3] is True
    with db.transaction() as conn:
        assert conn.execute('SELECT status FROM deliveries').fetchone()[0] == 'accepted'


@pytest.mark.parametrize('code,result', [(503, 'retry'), (429, 'retry'), (410, 'failed'), (403, 'failed')])
def test_push_retry_permanent_errors_and_revocation(mobile, monkeypatch, code, result):
    crm, client, db, *_ = mobile
    keys = prepared_delivery(mobile)
    monkeypatch.setenv('FLOW_MOBILE_PUSH_ENABLED', '1')
    assert drain(db, crm.store, keys, sender=lambda *args: code) == 0
    with db.transaction() as conn:
        row = conn.execute('SELECT * FROM deliveries').fetchone()
        assert row['status'] == result and row['attempts'] == 1
        if code == 410:
            assert conn.execute('SELECT device FROM sessions').fetchone()[0] is None


def test_queued_delivery_cancelled_after_logout_or_source_change(mobile, monkeypatch):
    crm, client, db, headers, *_ = mobile
    keys = prepared_delivery(mobile)
    client.post('/api/mobile/logout', json={}, headers=headers)
    monkeypatch.setenv('FLOW_MOBILE_PUSH_ENABLED', '1')
    def forbidden(*args):
        pytest.fail('A revoked phone must not receive pushes')
    assert drain(db, crm.store, keys, sender=forbidden) == 0
    with db.transaction() as conn:
        assert conn.execute('SELECT status FROM deliveries').fetchone()[0] == 'cancelled'


def test_test_push_only_current_phone_and_rate_limit(mobile, monkeypatch):
    crm, client, db, headers, tenant, owner = mobile
    prepared_delivery(mobile)
    assert client.post('/api/mobile/push/test', json={}, headers=headers).status_code == 503
    monkeypatch.setenv('FLOW_MOBILE_PUSH_ENABLED', '1')
    assert client.post('/api/mobile/push/test', json={'recipient': 'other-user'}, headers=headers).status_code == 200
    assert client.post('/api/mobile/push/test', json={}, headers=headers).status_code == 429
    with db.transaction() as conn:
        rows = conn.execute('SELECT * FROM notifications WHERE kind=\'test\'').fetchall()
        assert len(rows) == 1 and rows[0]['recipient'] == owner


def test_privacy_persists_and_never_exposes_device_credential(mobile):
    crm, client, db, headers, *_ = mobile
    client.post('/api/mobile/device', json={'token': 'c' * 64, 'environment': 'sandbox', 'revision': 0}, headers=headers)
    assert client.post('/api/mobile/device/privacy', json={'hide_details': False}, headers=headers).status_code == 200
    response = client.get('/api/mobile/device', headers=headers)
    assert response.json == {'push_enabled': True, 'hide_details': False, 'revision': 2}
    assert 'c' * 64 not in response.get_data(as_text=True)
    assert client.post('/api/mobile/device/privacy', json={'hide_details': 'false'}, headers=headers).status_code == 400


def test_pending_registration_cannot_undo_disable_or_privacy(mobile):
    crm, client, db, headers, *_ = mobile
    value = {'token': 'e' * 64, 'environment': 'sandbox', 'revision': 0}
    assert client.post('/api/mobile/device', json=value, headers=headers).json['revision'] == 1
    disabled = client.delete('/api/mobile/device', headers=headers)
    assert disabled.json['revision'] == 2
    value['revision'] = 1  # A request started before disabling arrives late.
    assert client.post('/api/mobile/device', json=value, headers=headers).status_code == 409
    assert not client.get('/api/mobile/device', headers=headers).json['push_enabled']
    client.post('/api/mobile/device/privacy', json={'hide_details': True}, headers=headers)
    value.update(revision=2, hide_details=False)
    assert client.post('/api/mobile/device', json=value, headers=headers).status_code == 409
    assert client.get('/api/mobile/device', headers=headers).json['hide_details'] is True


def test_shared_phone_detaches_previous_account(mobile):
    crm, client, db, headers, tenant, owner = mobile
    first = client.post('/api/mobile/device', json={'token': 'd' * 64, 'environment': 'sandbox', 'revision': 0}, headers=headers)
    assert first.status_code == 200
    second = secrets.token_urlsafe(32)
    crm.store.upsert('tenants', dict(id='second-owner', login_email='second@example.invalid', active=True))
    with db.transaction() as conn:
        conn.execute('INSERT INTO sessions(token_hash,tenant_id,recipient,expires) VALUES (?,?,?,?)',
                     (digest(second), 'second-owner', 'second@example.invalid', time.time() + 600))
    new_headers = dict(Authorization='Bearer ' + second)
    assert client.post('/api/mobile/device', json={'token': 'd' * 64, 'environment': 'sandbox', 'revision': 0}, headers=new_headers).status_code == 200
    assert client.get('/api/mobile/device', headers=headers).json['push_enabled'] is False
    assert client.get('/api/mobile/device', headers=new_headers).json['push_enabled'] is True


def test_delivery_rechecks_preferences_and_source_state(mobile, monkeypatch):
    crm, client, db, headers, tenant, owner = mobile
    keys = prepared_delivery(mobile)
    client.post('/api/mobile/preferences', json={'lead': False}, headers=headers)
    monkeypatch.setenv('FLOW_MOBILE_PUSH_ENABLED', '1')
    def forbidden(*args):
        pytest.fail('Muted or stale events must not be sent')
    assert drain(db, crm.store, keys, sender=forbidden) == 0
    with db.transaction() as conn:
        assert conn.execute('SELECT status FROM deliveries').fetchone()[0] == 'cancelled'
        conn.execute("UPDATE deliveries SET status='queued'")
    client.post('/api/mobile/preferences', json={'lead': True}, headers=headers)
    assert drain(db, crm.store, {tenant: set()}, sender=forbidden) == 0


def test_mobile_money_uses_existing_rules_and_numeric_wire_values(mobile):
    crm, client, db, headers, tenant, owner = mobile
    year = date.today().year
    crm.store.upsert('payments', dict(id='native-paid', tenant_id=tenant, status='Pagado',
                                    amount='Q1,200.00', paid_date=f'{year}-01-01'))
    response = client.get('/api/mobile/payments', headers=headers).json
    assert response['payments'][0]['amount'] == 1200
    assert response['summary']['paid'] == 1200
    assert client.get('/api/mobile/overview', headers=headers).json['paid'] == 1200


def test_overview_chart_uses_cash_dates_and_filters_upcoming_jobs(mobile):
    crm, client, db, headers, tenant, owner = mobile
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo('America/Guatemala')).date()
    crm.store.upsert('payments', dict(id='chart-paid', tenant_id=tenant, status='Pagado',
                                    amount=1750, paid_date=f'{today.year}-02-03'))
    crm.store.upsert('payments', dict(id='chart-pending', tenant_id=tenant, status='Pendiente',
                                    amount=9000, due_date=f'{today.year}-02-03'))
    for identifier, days in [('next', 7), ('past', -7)]:
        crm.store.upsert('jobs', dict(id=identifier, tenant_id=tenant, nombre=identifier,
                                   status='Confirmado', boda_date=(today + timedelta(days=days)).isoformat()))
    data = client.get('/api/mobile/overview', headers=headers).json
    assert len(data['monthly_paid']) == 12
    assert data['monthly_paid'][1] == 1750 and sum(data['monthly_paid']) == data['paid'] == 1750
    assert [record['id'] for record in data['upcoming']] == ['next']
    assert data['user_email'] == owner


def test_apns_jwt_real_p256_signature_without_network(monkeypatch):
    import base64
    import json
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    monkeypatch.setenv('FLOW_APNS_PRIVATE_KEY', pem)
    monkeypatch.setenv('FLOW_APNS_KEY_ID', 'QA_KEY')
    monkeypatch.setenv('FLOW_APNS_TEAM_ID', 'QA_TEAM')
    header, claims, signature = apns_jwt().split('.')
    decode = lambda value: base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    assert json.loads(decode(header)) == {'alg': 'ES256', 'kid': 'QA_KEY'}
    assert json.loads(decode(claims))['iss'] == 'QA_TEAM'
    raw = decode(signature)
    assert len(raw) == 64
    key.public_key().verify(encode_dss_signature(int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:], 'big')),
                            (header + '.' + claims).encode(), ec.ECDSA(hashes.SHA256()))


def test_native_teams_reuses_report_and_keeps_bearer_scope(mobile, monkeypatch):
    from uuid import uuid4
    crm, client, _, headers, tenant, owner = mobile
    monkeypatch.setitem(crm.app.config, 'FLOW_TEAMS_ENABLED', True)
    from flask import Flask
    from src.teams import register_teams
    team_app = Flask('native_teams_test')
    team_app.secret_key = 'synthetic-native-teams'
    team_app.config.update(TESTING=True, FLOW_TEAMS_LOCAL=False)
    # Registration wraps this resolver; restore it when this isolated test ends.
    monkeypatch.setattr(crm.store, 'tenant_resolver', crm.store.tenant_resolver)
    register_teams(team_app, crm.store, crm._canonical_jobs, crm._job_payment_summary, crm._job_is_active)
    monkeypatch.setitem(crm.app.extensions, 'teams_snapshot', team_app.extensions['teams_snapshot'])
    database = team_app.extensions['teams']
    crm.store.upsert('jobs', dict(id='native-team-job', tenant_id=tenant, nombre='Boda QA',
                                 boda_date='2026-11-14', status='Confirmado', currency='GTQ'))
    def command(action, brand=tenant, **values):
        return database.command(brand, owner, dict(action=action, key=str(uuid4()), **values),
                                lambda identifier: dict(id=identifier, boda_date='2026-11-14', status='Confirmado'))['record']
    member = command('member', name='Fotógrafo QA', email='foto@example.invalid', role='Foto', rate='1500')
    command('member', brand='foreign-native-teams', name='Persona ajena', email='ajena@example.invalid', role='Video', rate='2000')
    command('assignment', job_id='native-team-job', member_id=member['id'], slot='Foto 1', role='Foto',
            amount='1500', start='2026-11-14T13:00', end='2026-11-14T22:00', buffer=30)
    with database.transaction() as conn:
        cost = next(c for c in database.records(conn, tenant, 'cost') if c['job_id'] == 'native-team-job')
    command('cost_status', id=cost['id'], version=cost['version'], status='aprobado')
    command('payment', amount='500', allocations=[dict(cost_id=cost['id'], amount='500')],
            effective_date='2026-10-10', method='Transferencia', reference='QA')
    login_as_tenant(client, 'foreign-native-teams', email='foreign@example.invalid')
    response = client.get('/api/mobile/teams', headers=headers)
    assert response.status_code == 200
    data = response.json
    assert data['enabled']
    assert [m['name'] for m in data['members']] == ['Fotógrafo QA']
    assignment = data['jobs'][0]['assignments'][0]
    assert (assignment['amount'], assignment['paid'], assignment['pending']) == (150000, 50000, 100000)
    assert data['jobs'][0]['pending'] == 100000 and data['jobs'][0]['incomplete']
    assert 'email' not in data['members'][0] and 'audit' not in data
    with client.session_transaction() as session:
        assert session['tenant_id'] == 'foreign-native-teams'
    assert client.get('/api/mobile/teams').status_code == 401
    monkeypatch.setitem(crm.app.config, 'FLOW_TEAMS_LOCAL', True)
    assert not client.get('/api/mobile/teams', headers=headers,
                          environ_overrides={'REMOTE_ADDR': '192.0.2.10'}).json['enabled']
    monkeypatch.setitem(crm.app.config, 'FLOW_TEAMS_ENABLED', False)
    monkeypatch.setitem(crm.app.config, 'FLOW_TEAMS_LOCAL', False)
    assert client.get('/api/mobile/teams', headers=headers).json == dict(enabled=False, jobs=[], members=[])
