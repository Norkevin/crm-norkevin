from copy import deepcopy
from uuid import uuid4

import pytest

from conftest import login_as_tenant
from test_flow_teams import assignment, run
from test_teams_calendar import prepared, FakeCalendar, JOB, ORIGIN, TENANT, ZONE, rows
from src.teams_calendar import CalendarSync


@pytest.fixture
def notifications(client, prepared, monkeypatch):
    import app as crm
    database, people, assignments, _ = prepared
    monkeypatch.setitem(crm.app.extensions, 'teams', database)
    login_as_tenant(client, TENANT)
    crm.store.upsert('jobs', dict(JOB, tenant_id=TENANT))
    fake = FakeCalendar()
    sync = CalendarSync(database, lambda tenant: fake)
    sync.enqueue(TENANT, JOB, ORIGIN, ZONE, 'secret', background=False, include_new=True)
    sync.drain(TENANT)
    return client, database, people, assignments, fake, sync


def notices(client):
    return [n for n in client.get('/api/notifications/recent').get_json()['notifications'] if n['type'] == 'teams']


def refresh(database, people, assignments, fake, sync, status):
    fake.response = lambda *args: dict(attendees=[dict(email=people[0]['email'], responseStatus=status)])
    with database.transaction() as db:
        record = next(r for r in database.records(db, TENANT, 'calendar_sync') if r['identity'] == 'assignment:' + assignments[0]['id'])
        record['response_retry_at'] = None
        database.save(db, TENANT, 'calendar_sync', record)
    sync.refresh_responses(TENANT)


@pytest.mark.parametrize('status,verb', [('accepted', 'aceptó'), ('declined', 'rechazó')])
def test_calendar_response_notifies_once_and_preserves_read_state_and_assignments(notifications, status, verb):
    client, database, people, assignments, fake, sync = notifications
    before = deepcopy(rows(database, 'assignment'))
    calls = len(fake.calls)
    refresh(database, people, assignments, fake, sync, status)
    notice, = notices(client)
    assert notice['title'] == f"Teams · {people[0]['name']} {verb} la boda {JOB['nombre']}"
    assert notice['url'] == '/teams/jobs/wedding' and notice['age'] == 'Google Calendar'
    assert not notice['read'] and notice['time']
    assert client.post('/api/notifications/read', json={'ids': [notice['id']]}).status_code == 200
    for response in (status, 'unknown', status):
        refresh(database, people, assignments, fake, sync, response)
    assert len(notices(client)) == 1 and notices(client)[0]['read']
    refresh(database, people, assignments, fake, sync, 'declined' if status == 'accepted' else 'accepted')
    changed = notices(client)
    assert len(changed) == 2 and not changed[0]['read'] and changed[0]['id'] != notice['id']
    assert rows(database, 'assignment') == before and len(fake.calls) == calls
    audits = [a for a in rows(database, 'audit') if a['action'] == 'calendar_response']
    assert len(audits) == 2 and '/p/' not in str(audits)


def test_sync_response_also_notifies_and_is_isolated_from_other_brands(notifications):
    client, database, people, assignments, fake, sync = notifications
    original = fake.sync
    def accepted(*args):
        return dict(original(*args), attendees=[dict(email=people[0]['email'], responseStatus='accepted')])
    fake.sync = accepted
    sync.enqueue(TENANT, JOB, ORIGIN, ZONE, 'secret', background=False,
                 invite_ids=[assignments[0]['id']], delivery_key='explicit-invite')
    sync.drain(TENANT)
    notice, = notices(client)
    login_as_tenant(client, 'tenant-norkevin')
    assert notices(client) == []
    assert client.post('/api/notifications/read', json={'ids': [notice['id']]}).status_code == 404


def test_existing_google_response_is_not_reannounced_and_failures_keep_last_response(notifications):
    client, database, people, assignments, fake, sync = notifications
    with database.transaction() as db:
        record = next(r for r in database.records(db, TENANT, 'calendar_sync')
                      if r['identity'] == 'assignment:' + assignments[0]['id'])
        record['response_status'] = 'accepted'
        database.save(db, TENANT, 'calendar_sync', record)
    refresh(database, people, assignments, fake, sync, 'accepted')
    assert notices(client) == []
    refresh(database, people, assignments, fake, sync, 'declined')
    original = notices(client)
    def unavailable(*args):
        raise OSError('private provider data')
    fake.response = unavailable
    with database.transaction() as db:
        record = database.get(db, TENANT, 'calendar_sync', record['id'])
        record['response_retry_at'] = None
        database.save(db, TENANT, 'calendar_sync', record)
    sync.refresh_responses(TENANT)
    assert notices(client) == original
    assert next(r for r in rows(database) if r['id'] == record['id'])['response_status'] == 'declined'


def test_secondary_coverage_links_to_teams_and_removed_jobs_are_not_listed(notifications):
    import app as crm
    client, database, people, _, _, _ = notifications
    crm.store.upsert('calendar', dict(id='civil', tenant_id=TENANT, job_id=JOB['id'],
        type='event', title='Boda civil', date='2026-11-13'))
    with database.transaction() as db:
        database.create(db, TENANT, 'audit', action='response', created_at='2099-01-01T10:00:00-06:00',
            after=dict(id='coverage', member_id=people[0]['id'], job_id='secondary:civil', status='aceptada'))
        database.create(db, TENANT, 'audit', action='response', created_at='2099-01-01T11:00:00-06:00',
            after=dict(id='deleted', member_id=people[0]['id'], job_id='deleted', status='rechazada'))
    notice, = notices(client)
    assert notice['url'] == '/teams/jobs/secondary:civil' and 'Boda civil' in notice['title']
    with client.session_transaction() as session:
        session['user_email'] = 'collaborator@example.invalid'
    with client.application.test_request_context('/'):
        crm.session.update(logged_in=True, tenant_id=TENANT, user_email='collaborator@example.invalid')
        assert crm._teams_response_notifications(TENANT) == []


@pytest.mark.parametrize('status,verb', [('aceptada', 'aceptó'), ('rechazada', 'rechazó')])
def test_portal_response_uses_existing_audit_and_command_retries_do_not_duplicate(notifications, status, verb):
    import app as crm
    client, database, _, _, _, _ = notifications
    job = dict(JOB, id='portal-wedding', tenant_id=TENANT)
    crm.store.upsert('jobs', job)
    person = run(database, 'member', tenant=TENANT, name='Fernando', email='fernando@example.invalid', role='Foto')
    coverage = assignment(database, person, job=job['id'], tenant=TENANT)
    coverage = run(database, 'assignment_publish', tenant=TENANT, id=coverage['id'], version=coverage['version'])
    command = dict(action='response', key=str(uuid4()), id=coverage['id'], version=coverage['version'],
                   terms_version=coverage['terms_version'], status=status)
    for _ in range(2):
        database.command(TENANT, 'member:' + person['id'], command, lambda i: job, member_id=person['id'])
    notice, = notices(client)
    assert notice['title'] == f"Teams · Fernando {verb} la boda {JOB['nombre']}"
    assert notice['age'] == 'Portal de Teams' and notice['url'] == '/teams/jobs/portal-wedding'
