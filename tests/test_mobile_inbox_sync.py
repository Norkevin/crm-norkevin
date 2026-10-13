"""The native inbox shares web activity without starting delivery or changing CRM data."""
from pathlib import Path
from datetime import datetime, timedelta, timezone

from conftest import login_as_tenant
from src.mobile import digest
from src.teams import TeamsStore
from test_mobile_native import mobile


def notice_id(tenant, owner, key):
    return digest(tenant + ':' + owner + ':' + key)


def inbox(client, headers, suffix=''):
    response = client.get('/api/mobile/notifications' + suffix, headers=headers)
    assert response.status_code == 200
    return response.get_json()


def seed_lead(crm, tenant, identifier='inbox-lead', created='2020-01-01T12:00:00Z'):
    crm.store.upsert('leads', dict(id=identifier, tenant_id=tenant, nombre=identifier,
        status='Nuevo', created=created))


def json_snapshot(crm):
    return {path.name: path.read_bytes() for path in Path(crm.store.data_dir).glob('*.json')}


def assert_no_delivery_state(db):
    with db.transaction() as conn:
        for table in ('deliveries', 'observed', 'baselines'):
            assert conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0


def test_existing_and_new_web_activity_is_visible_without_worker_or_push(mobile, monkeypatch):
    crm, client, db, headers, tenant, owner = mobile
    seed_lead(crm, tenant)
    crm.store.upsert('mail_log', dict(id='inbox-mail', tenant_id=tenant,
        lead_id='inbox-lead', subject='Fallo de prueba', status='failed',
        sent_at='2020-01-02T12:00:00Z'))

    def forbidden(*args, **kwargs):
        raise AssertionError('Reading the inbox must not reconcile or deliver push events')

    monkeypatch.setattr(db, 'observe', forbidden)
    monkeypatch.setattr('src.mobile_push.drain', forbidden)
    monkeypatch.setattr('src.mobile_push.send_apns', forbidden)
    for key in ('FLOW_MOBILE_ENCRYPTION_KEY', 'FLOW_APNS_PRIVATE_KEY', 'FLOW_APNS_KEY_ID',
                'FLOW_APNS_TEAM_ID', 'FLOW_APNS_BUNDLE_ID'):
        monkeypatch.delenv(key, raising=False)
    before = json_snapshot(crm)
    first = inbox(client, headers)
    assert {n['id'] for n in first['notifications']} == {
        notice_id(tenant, owner, 'lead:inbox-lead'),
        notice_id(tenant, owner, 'mail-alert:inbox-mail'),
    }
    assert first['count'] == 2
    assert {n['kind'] for n in first['notifications']} == {'lead', 'mail'}
    assert json_snapshot(crm) == before
    assert_no_delivery_state(db)
    seed_lead(crm, tenant, 'inbox-new', '2099-01-01T12:00:00Z')
    second = inbox(client, headers)
    assert second['count'] == 3
    assert second['notifications'][0]['id'] == notice_id(tenant, owner, 'lead:inbox-new')
    assert inbox(client, headers) == second
    assert_no_delivery_state(db)


def test_inbox_rejects_out_of_range_sqlite_cursors(mobile):
    _, client, _, headers, *_ = mobile
    for value in ('-1', '9223372036854775808', 'invalid'):
        assert client.get('/api/mobile/notifications', query_string={'before': value}, headers=headers).status_code == 400


def test_mobile_read_state_is_shared_without_changing_business_records(mobile):
    crm, client, db, headers, tenant, owner = mobile
    for identifier in ('web-read', 'mobile-read'):
        seed_lead(crm, tenant, identifier)
    crm.store.upsert('notification_reads', dict(id=tenant + ':lead-web-read',
        tenant_id=tenant, notification_id='lead-web-read', read_at='2020-01-02T12:00:00Z'))
    before = json_snapshot(crm)
    page = inbox(client, headers)
    notices = {n['id']: n for n in page['notifications']}
    web_read = notice_id(tenant, owner, 'lead:web-read')
    mobile_read = notice_id(tenant, owner, 'lead:mobile-read')
    assert notices[web_read]['read'] and not notices[mobile_read]['read']
    assert page['count'] == 1
    assert client.post('/api/mobile/notifications/read', json={'ids': [mobile_read]},
        headers=headers).status_code == 200
    for _ in range(3):
        refreshed = inbox(client, headers)
        assert refreshed['count'] == 0 and all(n['read'] for n in refreshed['notifications'])
    after = json_snapshot(crm)
    assert {k: v for k, v in after.items() if k != 'notification_reads.json'} == {k: v for k, v in before.items() if k != 'notification_reads.json'}
    assert any(r['notification_id'] == 'lead-mobile-read' for r in crm.store.list('notification_reads'))
    assert_no_delivery_state(db)


def test_bearer_inbox_and_targets_ignore_another_company_cookie(mobile):
    crm, client, db, headers, tenant, owner = mobile
    seed_lead(crm, tenant, 'inbox-owned')
    seed_lead(crm, 'inbox-foreign-company', 'inbox-foreign')
    crm.store.upsert('mail_log', dict(id='foreign-mail', tenant_id='inbox-foreign-company',
        lead_id='inbox-foreign', subject='Confidencial', status='failed',
        attempted_at='2099-01-01T12:00:00Z'))
    login_as_tenant(client, 'inbox-foreign-company', email='other@example.invalid')
    page = inbox(client, headers)
    owned = notice_id(tenant, owner, 'lead:inbox-owned')
    assert [n['id'] for n in page['notifications']] == [owned]
    response = client.get('/api/mobile/notifications/' + owned + '/target', headers=headers)
    assert response.status_code == 200 and response.json['record']['id'] == 'inbox-owned'
    foreign = notice_id('inbox-foreign-company', 'other@example.invalid', 'lead:inbox-foreign')
    assert client.get('/api/mobile/notifications/' + foreign + '/target', headers=headers).status_code == 404
    assert client.post('/api/mobile/notifications/read', json={'ids': [owned, foreign]},
        headers=headers).status_code == 404
    assert inbox(client, headers)['count'] == 1
    with client.session_transaction() as session:
        assert session['tenant_id'] == 'inbox-foreign-company'


def test_mail_activity_targets_the_existing_job_when_its_lead_was_deleted(mobile):
    crm, client, db, headers, tenant, owner = mobile
    crm.store.upsert('jobs', dict(id='mail-job', tenant_id=tenant, nombre='Trabajo de prueba',
        status='Confirmado', boda_date='2099-01-01'))
    crm.store.upsert('mail_log', dict(id='fallback-mail', tenant_id=tenant,
        lead_id='removed-lead', job_id='mail-job', subject='Correo conservado',
        status='failed', sent_at='2020-01-02T12:00:00Z'))
    page = inbox(client, headers)
    notice, = page['notifications']
    assert notice['id'] == notice_id(tenant, owner, 'mail-alert:fallback-mail')
    assert (notice['target_kind'], notice['target_id']) == ('jobs', 'mail-job')
    response = client.get('/api/mobile/notifications/' + notice['id'] + '/target', headers=headers)
    assert response.status_code == 200 and response.json['record']['name'] == 'Trabajo de prueba'
    crm.store.delete('jobs', 'mail-job')
    assert client.get('/api/mobile/notifications/' + notice['id'] + '/target', headers=headers).status_code == 404


def test_teams_primary_and_secondary_responses_are_visible_to_bearer_owner(mobile, monkeypatch, tmp_path):
    crm, client, db, headers, tenant, owner = mobile
    crm.store.upsert('jobs', dict(id='teams-job', tenant_id=tenant, nombre='Boda QA',
        status='Confirmado', boda_date='2099-01-01'))
    crm.store.upsert('calendar', dict(id='teams-civil', tenant_id=tenant, job_id='teams-job',
        type='event', title='Boda civil', date='2098-12-31'))
    teams = TeamsStore(tmp_path / 'teams-inbox.sqlite3')
    monkeypatch.setitem(crm.app.extensions, 'teams', teams)
    with teams.transaction() as conn:
        member = teams.create(conn, tenant, 'member', name='Persona QA',
            email='person@example.invalid', active=True)
        primary = teams.create(conn, tenant, 'audit', action='response',
            created_at='2020-01-02T12:00:00Z',
            after=dict(member_id=member['id'], job_id='teams-job', status='aceptada'))
        secondary = teams.create(conn, tenant, 'audit', action='calendar_response',
            created_at='2020-01-03T12:00:00Z',
            after=dict(member_id=member['id'], job_id='secondary:teams-civil', status='rechazada'))
        teams.create(conn, 'foreign-company', 'audit', action='response',
            created_at='2099-01-01T12:00:00Z',
            after=dict(member_id=member['id'], job_id='teams-job', status='aceptada'))
    before = json_snapshot(crm)
    notices = inbox(client, headers)['notifications']
    assert {n['id'] for n in notices} == {
        notice_id(tenant, owner, 'teams:' + primary['id']),
        notice_id(tenant, owner, 'teams:' + secondary['id']),
    }
    assert all(n['kind'] == 'teams' and 'Persona QA' in n['title'] for n in notices)
    targets = {}
    for notice in notices:
        response = client.get('/api/mobile/notifications/' + notice['id'] + '/target', headers=headers)
        assert response.status_code == 200
        targets[notice['target_id']] = response.json['record']['name']
    assert targets == {'teams-job': 'Boda QA', 'secondary:teams-civil': 'Boda civil'}
    assert json_snapshot(crm) == before
    assert_no_delivery_state(db)


def test_shared_inbox_deduplicates_an_existing_worker_notice(mobile):
    crm, client, db, headers, tenant, owner = mobile
    seed_lead(crm, tenant, 'existing-worker')
    db.observe(tenant, owner, [])
    db.observe(tenant, owner, [dict(key='lead:existing-worker', kind='lead', title='Aviso anterior',
        table='leads', id='existing-worker')])
    notice, = inbox(client, headers)['notifications']
    assert notice['id'] == notice_id(tenant, owner, 'lead:existing-worker')
    assert notice['title'] == 'Nuevo lead: existing-worker'
    assert client.post('/api/mobile/notifications/read', json={'ids': [notice['id']]},
        headers=headers).status_code == 200
    refreshed = inbox(client, headers)
    assert len(refreshed['notifications']) == 1 and refreshed['notifications'][0]['read']
    with db.transaction() as conn:
        assert conn.execute('SELECT COUNT(*) FROM observed').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM baselines').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0] == 0


def test_shared_inbox_paginates_over_fifty_equal_timestamps(mobile):
    crm, client, db, headers, tenant, owner = mobile
    for index in range(105):
        seed_lead(crm, tenant, 'page-' + str(index), '2020-01-01T12:00:00Z')
    expected = {notice_id(tenant, owner, 'lead:page-' + str(index)) for index in range(105)}
    seen, suffix = [], ''
    while True:
        page = inbox(client, headers, suffix)
        assert page['count'] == 105
        seen.extend(n['id'] for n in page['notifications'])
        if page['next_before'] is None:
            break
        suffix = '?before=' + str(page['next_before'])
    assert len(seen) == len(set(seen)) == 105 and set(seen) == expected
    assert_no_delivery_state(db)


def test_opens_do_not_renew_a_mail_alert_or_reset_its_read_state(mobile):
    crm, client, db, headers, tenant, owner = mobile
    seed_lead(crm, tenant, 'mail-contact', '2020-01-02T12:00:00Z')
    crm.store.upsert('mail_log', dict(id='old-mail', tenant_id=tenant,
        lead_id='mail-contact', subject='Correo anterior', status='failed',
        sent_at='2020-01-01T12:00:00Z'))
    page = inbox(client, headers)
    mail_id = notice_id(tenant, owner, 'mail-alert:old-mail')
    assert page['notifications'][-1]['id'] == mail_id
    assert client.post('/api/mobile/notifications/read', json={'ids': [mail_id]},
        headers=headers).status_code == 200

    crm.store.upsert('mail_log', dict(id='old-mail', tenant_id=tenant,
        lead_id='mail-contact', subject='Correo anterior', status='failed',
        sent_at='2020-01-01T12:00:00Z', opened_at='2099-01-01T12:00:00Z'))
    before = json_snapshot(crm)
    updated = inbox(client, headers)['notifications']
    assert updated[-1]['id'] == mail_id and updated[-1]['read']
    assert updated[-1]['created'] == page['notifications'][-1]['created']
    assert len(updated) == 2
    assert json_snapshot(crm) == before
    assert_no_delivery_state(db)


def test_source_time_cursor_paginates_when_updated_dates_do_not_follow_rowid(mobile):
    crm, client, db, headers, tenant, owner = mobile
    for index in range(105):
        seed_lead(crm, tenant, 'reordered-' + str(index))
    assert inbox(client, headers)['count'] == 105
    origin = datetime(2099, 1, 1, tzinfo=timezone.utc)
    for index in range(105):
        moment = origin + timedelta(hours=(index * 37) % 105)
        seed_lead(crm, tenant, 'reordered-' + str(index), moment.isoformat())

    seen, moments, suffix = [], [], ''
    while True:
        page = inbox(client, headers, suffix)
        seen.extend(n['id'] for n in page['notifications'])
        moments.extend(n['created'] for n in page['notifications'])
        if page['next_before'] is None:
            break
        suffix = '?before=' + str(page['next_before'])
    assert len(seen) == len(set(seen)) == 105
    assert set(seen) == {notice_id(tenant, owner, 'lead:reordered-' + str(index)) for index in range(105)}
    assert moments == sorted(moments, reverse=True)
    assert_no_delivery_state(db)


def test_another_companys_recipients_or_missing_cursor_cannot_anchor_the_inbox(mobile):
    crm, client, db, headers, tenant, owner = mobile
    seed_lead(crm, tenant)
    assert inbox(client, headers)['count'] == 1
    with db.transaction() as conn:
        cursors = []
        for identifier, company, recipient in (
            ('foreign-recipient', tenant, 'another-owner@example.invalid'),
            ('foreign-company', 'another-company', owner),
        ):
            conn.execute('INSERT INTO notifications (id,tenant_id,recipient,event_key,kind,title,target_table,target_id,created,read_at) VALUES (?,?,?,?,?,?,?,?,?,NULL)',
                (identifier, company, recipient, 'foreign:event',
                 'lead', 'Otro destinatario', 'leads', 'inbox-lead', 1))
            cursors.append(conn.execute('SELECT rowid FROM notifications WHERE id=?',
                (identifier,)).fetchone()[0])
    for value in cursors + [cursors[-1] + 1000]:
        page = inbox(client, headers, '?before=' + str(value))
        assert page['notifications'] == [] and page['next_before'] is None
        assert page['count'] == 1
    assert_no_delivery_state(db)


def test_old_redundant_mail_is_hidden_without_erasing_history_and_reads_sync(mobile):
    crm, client, db, headers, tenant, owner = mobile
    seed_lead(crm, tenant, 'current')
    crm.store.upsert('mail_log', dict(id='backup', tenant_id=tenant, lead_id='current', status='sent'))
    db.sync_inbox(tenant, owner, [dict(key='mail-alert:backup', kind='mail', title='Old redundant notice',
        table='leads', id='current', created=1, read=False)])
    page = inbox(client, headers)
    assert len(page['notifications']) == page['count'] == 1
    with db.transaction() as conn:
        assert conn.execute("SELECT hidden FROM notifications WHERE event_key='mail-alert:backup'").fetchone()[0] == 1
    assert crm.store.get('mail_log', 'backup')['status'] == 'sent'
    login_as_tenant(client, tenant, email=owner)
    assert client.post('/api/notifications/read', json={'ids': ['lead-current']}).status_code == 200
    assert inbox(client, headers)['notifications'][0]['read']
    seed_lead(crm, tenant, 'second')
    second = next(n for n in inbox(client, headers)['notifications'] if n['target_id'] == 'second')
    assert client.post('/api/mobile/notifications/read', json={'ids': [second['id']]}, headers=headers).status_code == 200
    assert client.get('/api/notifications/recent').json['count'] == 0


def test_worker_hides_old_mail_before_push_badge_without_deleting_records(mobile):
    crm, client, db, headers, tenant, owner = mobile
    db.sync_inbox(tenant, owner, [dict(key='mail-alert:normal', kind='mail', title='Activity',
        table='leads', id='any', created=1, read=False)])
    db.observe(tenant, owner, [])
    with db.transaction() as conn:
        assert conn.execute('SELECT COUNT(*) FROM notifications').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM notifications WHERE hidden=0').fetchone()[0] == 0
    db.observe(tenant, owner, [dict(key='mail-alert:normal', kind='mail', title='Delivery failed',table='leads',id='any')])
    with db.transaction() as conn:
        assert conn.execute('SELECT COUNT(*) FROM notifications WHERE hidden=0').fetchone()[0] == 1


def test_legacy_journal_schema_upgrade_preserves_read_history(tmp_path):
    import sqlite3
    from src.mobile import MobileJournal
    path = tmp_path / 'legacy.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('''CREATE TABLE notifications (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
            recipient TEXT NOT NULL, event_key TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
            target_table TEXT, target_id TEXT, created REAL NOT NULL, read_at REAL,
            UNIQUE(tenant_id,recipient,event_key))''')
        conn.execute("INSERT INTO notifications VALUES ('old','tenant','owner','mail-alert:old','mail','History','leads','lead',1,2)")
    for _ in range(2):
        journal = MobileJournal(path)
        with journal.transaction() as conn:
            row = conn.execute('SELECT * FROM notifications').fetchone()
            assert row['read_at'] == 2 and row['hidden'] == 0 and row['title'] == 'History'
