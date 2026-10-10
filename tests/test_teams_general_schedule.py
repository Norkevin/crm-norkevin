from copy import deepcopy
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from flask import session

from test_flow_teams import web, member, assignment, publish, respond, run, records, member_client
from src.teams import TeamsError
from src.teams_calendar import events


def general(owner, version=0, key=None, **fields):
    data = dict(action='job_schedule', key=key or uuid4().hex, job_id='job-1', version=version,
                schedule_pending=False, start='2026-11-14T14:00', end='2026-11-14T21:00')
    data.update(fields)
    return owner.post('/api/teams/command', headers={'X-Teams-CSRF':'csrf'}, json=data)


def other_member(store):
    return run(store, 'member', name='Ramiro', email='ramiro@flow-qa-84982.com', role='Video', rate='1500')


def job_for(app, storage):
    with app.test_request_context('/'):
        session['tenant_id'] = 'brand-a'
        return storage.get('jobs', 'job-1')


def test_general_schedule_updates_inherited_team_and_preserves_personal_hours_everywhere(web):
    app, owner, storage = web
    store = app.extensions['teams']
    fernando = member(store)
    with store.transaction() as db:
        fernando['email'] = 'fernando@flow-qa-84982.com'
        store.save(db, 'brand-a', 'member', fernando)
    ramiro = other_member(store)
    shared = respond(store, fernando, publish(store, assignment(store, fernando, schedule_source='general')))
    personal = respond(store, ramiro, publish(store, assignment(store, ramiro, slot='Video',
        start='2026-11-14T17:00', end='2026-11-14T20:00', schedule_source='individual')))
    old_costs = deepcopy(records(store, 'cost'))
    reply = general(owner, key='general-first')
    assert reply.status_code == 200
    assert general(owner, key='general-first').get_json() == reply.get_json()
    updated = records(store, 'assignment')
    shared_now = next(a for a in updated if a['id'] == shared['id'])
    personal_now = next(a for a in updated if a['id'] == personal['id'])
    assert shared_now['start'][11:16] == '14:00' and shared_now['end'][11:16] == '21:00'
    assert shared_now['status'] == 'reconfirmar' and shared_now['accepted_terms'] is None
    assert shared_now['terms_version'] == shared['terms_version'] + 1
    assert personal_now == personal
    assert len(records(store, 'terms_history')) == 1
    assert [(c['budget'],c['estimate'],c['status']) for c in records(store,'cost')] == [
        (c['budget'],c['estimate'],c['status']) for c in old_costs]
    html = owner.get('/teams/jobs/job-1').get_data(as_text=True)
    assert 'Horario general de la boda' in html and 'Editar horario general' in html
    assert 'Horario general · 14:00–21:00' in html and 'Horario personalizado · 17:00–20:00' in html
    for person, arrival, departure in ((fernando,'14:00','21:00'),(ramiro,'17:00','20:00')):
        client = member_client(app, owner, person)
        coverage = client.get('/teams-portal/summary?job_id=job-1').get_json()['assignments']
        assert len(coverage) == 1 and coverage[0]['start'][11:16] == arrival and coverage[0]['end'][11:16] == departure
        portal = client.get('/teams-portal/?job_id=job-1').get_data(as_text=True)
        assert arrival in portal and departure in portal
        ics = client.get('/teams-portal/calendar.ics?job_id=job-1').get_data(as_text=True)
        assert 'DTSTART:' in ics and 'DTSTART;VALUE=DATE:' not in ics
    calendar = events(store,'brand-a',job_for(app,storage),'https://flowingcrm.com',ZoneInfo('America/Guatemala'),'secret')
    assert calendar['assignment:'+shared['id']]['start']['dateTime'][11:16] == '14:00'
    assert calendar['assignment:'+personal['id']]['start']['dateTime'][11:16] == '17:00'


def test_new_people_use_general_by_default_and_can_leave_and_return_to_it(web):
    app, owner, _ = web
    store = app.extensions['teams']
    person = member(store)
    coverage = run(store,'assignment',job_id='job-1',member_id=person['id'],role='Foto',amount='1500',slot='Foto')
    assert coverage['schedule_source'] == 'general' and coverage['schedule_pending']
    assert general(owner).status_code == 200
    coverage = records(store,'assignment')[0]
    coverage = run(store,'assignment_edit',id=coverage['id'],version=coverage['version'],schedule_source='individual',
        schedule_pending=False,start='2026-11-14T18:00',end='2026-11-14T20:00',buffer=0,amount='1500',reason='Llega después')
    assert general(owner,version=1,start='2026-11-14T15:00').status_code == 200
    assert records(store,'assignment')[0] == coverage
    coverage = run(store,'assignment_edit',id=coverage['id'],version=coverage['version'],schedule_source='general',
        buffer=0,amount='1500',reason='Vuelve con el equipo')
    assert coverage['start'][11:16] == '15:00' and coverage['schedule_source'] == 'general'
    assert general(owner,version=2,schedule_pending=True,start='',end='').status_code == 200
    assert records(store,'assignment')[0]['schedule_pending']
    other = run(store,'assignment',job_id='job-1',member_id=other_member(store)['id'],role='Video',amount='1500',slot='Video')
    assert other['schedule_source'] == 'general' and other['schedule_pending']


def test_personal_pending_schedule_stays_pending_when_general_is_defined(web):
    app, owner, _ = web
    store = app.extensions['teams']
    person = member(store)
    shared = assignment(store,person,schedule_source='general')
    assert general(owner).status_code == 200
    shared = records(store,'assignment')[0]
    personal = run(store,'assignment_schedule_pending',id=shared['id'],version=shared['version'])
    assert personal['schedule_source'] == 'individual'
    assert general(owner,version=1,end='2026-11-14T22:00').status_code == 200
    assert records(store,'assignment')[0] == personal


def test_legacy_matching_hours_follow_general_and_existing_exceptions_are_preserved(web):
    app, owner, storage = web
    store = app.extensions['teams']
    job = job_for(app,storage)
    with app.test_request_context('/'):
        session['tenant_id'] = 'brand-a'
        storage.upsert('jobs',dict(job,start_time='13:00',end_time='22:00'))
    shared = assignment(store,member(store))
    custom = assignment(store,other_member(store),slot='Video',start='2026-11-14T18:00',end='2026-11-14T20:00')
    with store.transaction() as db:
        for coverage in (shared,custom):
            coverage.pop('schedule_source')
            store.save(db,'brand-a','assignment',coverage)
    assert general(owner).status_code == 200
    rows = records(store,'assignment')
    assert rows[0]['schedule_source'] == 'general' and rows[0]['start'][11:16] == '14:00'
    assert rows[1]['schedule_source'] == 'individual' and rows[1]['start'] == custom['start']
    assert general(owner,version=1,start='2026-11-14T18:00',end='2026-11-14T20:00').status_code == 200
    assert general(owner,version=2).status_code == 200
    assert records(store,'assignment')[1]['start'] == custom['start']


def test_general_schedule_conflict_rolls_back_the_whole_team_change(web):
    app, owner, _ = web
    store = app.extensions['teams']
    assert general(owner,start='2026-11-14T13:00',end='2026-11-14T22:00').status_code == 200
    first, second = member(store), other_member(store)
    assignment(store,first,schedule_source='general')
    outside = respond(store,second,publish(store,assignment(store,second,job='job-2',
        start='2026-11-14T08:00',end='2026-11-14T12:00',buffer=0)))
    assignment(store,second,slot='Video',schedule_source='general')
    before = {kind: deepcopy(records(store,kind)) for kind in ('coverage_schedule','assignment','cost','terms_history','audit')}
    assert general(owner,version=1,start='2026-11-14T10:00').status_code == 400
    assert {kind: records(store,kind) for kind in before} == before
    assert general(owner,version=1,start='2026-11-14T10:00',conflict_reason='Traslado acordado con administración').status_code == 200
    assert next(a for a in records(store,'assignment') if a['id']==outside['id']) == outside


def test_general_schedule_keeps_finished_coverages_and_rejects_unauthorized_or_stale_changes(web):
    app, owner, storage = web
    store = app.extensions['teams']
    person = member(store)
    assert general(owner).status_code == 200
    coverage = assignment(store,person,schedule_source='general')
    finished = run(store,'assignment_status',id=coverage['id'],version=coverage['version'],status='realizada')
    assert general(owner,version=1,start='2026-11-14T15:00').status_code == 200
    assert records(store,'assignment')[0] == finished
    assert general(owner,version=1).status_code == 409
    assert general(owner,version=2,end='2026-11-14T10:00').status_code == 400
    assert general(owner,version=2,schedule_pending='false').status_code == 400
    with pytest.raises(TeamsError) as denied:
        store.command('brand-a','member',dict(action='job_schedule',key='denied',job_id='job-1',version=2),
                      lambda i: job_for(app,storage),member_id=person['id'])
    assert denied.value.status == 403
    with store.transaction() as db:
        store.create(db,'brand-a','operation',job_id='job-1',closed=True)
    assert general(owner,version=2).status_code == 409
    with owner.session_transaction() as state:
        state.update(tenant_id='brand-b',user_email='other@example.invalid')
    assert general(owner).status_code == 404


def test_changed_wedding_date_requires_general_schedule_review_before_new_inherited_coverage(web):
    app, owner, storage = web
    store = app.extensions['teams']
    assert general(owner).status_code == 200
    job = job_for(app,storage)
    with app.test_request_context('/'):
        session['tenant_id'] = 'brand-a'
        storage.upsert('jobs',dict(job,boda_date='2026-12-01'))
    person = member(store)
    data = dict(action='assignment',key='new-date',job_id='job-1',member_id=person['id'],role='Foto',
                amount='1500',schedule_source='general')
    reply = owner.post('/api/teams/command',headers={'X-Teams-CSRF':'csrf'},json=data)
    assert reply.status_code == 409
    assert general(owner,version=1,start='2026-12-01T14:00',end='2026-12-01T21:00').status_code == 200
    assert owner.post('/api/teams/command',headers={'X-Teams-CSRF':'csrf'},json=data).status_code == 200


def test_general_schedule_queues_updates_for_linked_invitations_without_inviting_new_people(web, monkeypatch):
    app, owner, storage = web
    store = app.extensions['teams']
    person = member(store)
    with store.transaction() as db:
        person['email'] = 'fernando@flow-qa-84982.com'
        store.save(db,'brand-a','member',person)
    linked = publish(store,assignment(store,person,schedule_source='general'))
    sync = app.extensions['teams_calendar']
    sync.enqueue('brand-a',job_for(app,storage),'https://flowingcrm.com',ZoneInfo('America/Guatemala'),
                 app.secret_key,include_new=True,background=False)
    with store.transaction() as db:
        for row in store.records(db,'brand-a','calendar_sync'):
            row.update(status='synced',response_status='accepted',email_status='sent')
            store.save(db,'brand-a','calendar_sync',row)
    original = next(r for r in records(store,'calendar_sync') if r['identity']=='assignment:'+linked['id'])
    new = publish(store,assignment(store,other_member(store),slot='Video',schedule_source='general'))
    monkeypatch.setattr('src.google_calendar.connected_email',lambda tenant:'owner@flow-qa-84982.com')
    app.config.update(FLOW_TEAMS_LOCAL=False,FLOW_TEAMS_ENABLED=True)
    assert general(owner).status_code == 200
    rows = records(store,'calendar_sync')
    changed = next(r for r in rows if r['identity']=='assignment:'+linked['id'])
    assert changed['status']=='pending' and changed['event']['start']['dateTime'][11:16]=='14:00'
    assert changed['event_id']==original['event_id'] and changed['response_status']=='accepted'
    assert changed['email_status']=='sent'
    assert not any(r['identity']=='assignment:'+new['id'] for r in rows)
