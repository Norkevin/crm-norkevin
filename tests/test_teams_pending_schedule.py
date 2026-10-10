from copy import deepcopy
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest

from test_flow_teams import web, member, assignment, publish, respond, run, records, member_client
from src.teams import TeamsError, availability_window
from src.teams_calendar import events
from src.google_calendar import CalendarClient


def mark_pending(owner, coverage, key='pending-schedule'):
    return owner.post('/api/teams/command', headers={'X-Teams-CSRF': 'csrf'}, json=dict(
        action='assignment_schedule_pending', key=key, id=coverage['id'], version=coverage['version']))


def test_pending_button_hides_provisional_hours_in_owner_portal_and_calendars(web):
    app, owner, storage = web
    store = app.extensions['teams']
    person = member(store)
    with store.transaction() as db:
        person['email'] = 'worker@flow-qa-84982.com'
        store.save(db, 'brand-a', 'member', person)
    coverage = respond(store, person, publish(store, assignment(store, person)))
    before_costs = deepcopy(records(store, 'cost'))
    response = mark_pending(owner, coverage)
    assert response.status_code == 200
    pending = response.get_json()['record']
    assert pending['schedule_pending'] and pending['status'] == 'reconfirmar'
    assert pending['accepted_terms'] is None and pending['terms_version'] == coverage['terms_version'] + 1
    assert mark_pending(owner, coverage).get_json() == response.get_json()
    assert mark_pending(owner, coverage, key='stale').status_code == 409
    after = records(store, 'cost')
    assert [(c['budget'], c['estimate'], c['status']) for c in after] == [(c['budget'], c['estimate'], c['status']) for c in before_costs]
    assert len(records(store, 'terms_history')) == 1
    for path in ('/teams/jobs/job-1', '/teams/calendar'):
        html = owner.get(path).get_data(as_text=True)
        assert 'Horario pendiente' in html and '13:00–22:00' not in html
        assert '00:00' not in html and '23:59' not in html
    worker = member_client(app, owner, person)
    summary = worker.get('/teams-portal/summary?job_id=job-1').get_json()
    assert summary['assignments'][0]['schedule_pending'] is True
    html = worker.get('/teams-portal/?job_id=job-1').get_data(as_text=True)
    coverage_html = html.split('class="ft-coverage-time"', 1)[1].split('</div>', 1)[0]
    assert 'Horario pendiente' in coverage_html and '00:00' not in coverage_html and '23:59' not in coverage_html
    ics = worker.get('/teams-portal/calendar.ics?job_id=job-1').get_data(as_text=True).replace('\r\n ', '')
    assert 'DTSTART;VALUE=DATE:20261114' in ics and 'DTEND;VALUE=DATE:20261115' in ics
    assert 'DTSTART:20261114T' not in ics and 'Horario pendiente' in ics
    with app.test_request_context('/'):
        from flask import session
        session['tenant_id'] = 'brand-a'
        job = storage.get('jobs', 'job-1')
    event = events(store, 'brand-a', job, 'https://flowingcrm.com', ZoneInfo('America/Guatemala'), 'secret')['assignment:' + pending['id']]
    assert event['start'] == {'date': '2026-11-14'} and event['end'] == {'date': '2026-11-15'}
    assert 'Horario pendiente' in event['summary'] and '13:00' not in event['description'] and '23:59' not in event['description']


def test_pending_coverage_can_be_created_without_hours_then_defined_and_keeps_day_reserved(web):
    app, owner, _ = web
    store = app.extensions['teams']
    person = member(store)
    pending = assignment(store, person, schedule_pending=True, start='', end='')
    start, end = availability_window(pending)
    assert end - start == timedelta(days=1)
    with store.transaction() as db:
        assert store.conflicts(db, 'brand-a', person['id'], '2026-11-14T23:59:59-06:00',
                               '2026-11-15T00:00:00-06:00', 0)[0]['id'] == pending['id']
    pending = publish(store, pending)
    defined = run(store, 'assignment_edit', id=pending['id'], version=pending['version'],
                  schedule_pending=False, start='2026-11-14T15:00', end='2026-11-14T19:00',
                  buffer=30, amount='1500', reason='Horario confirmado')
    assert not defined['schedule_pending'] and defined['status'] == 'reconfirmar'
    html = owner.get('/teams/jobs/job-1').get_data(as_text=True)
    assert '15:00–19:00' in html and 'Marcar horario pendiente' in html
    assert '13:00' not in html and '22:00' not in html


def test_pending_schedule_obeys_owner_brand_closed_operation_and_version_guards(web):
    app, owner, _ = web
    store = app.extensions['teams']
    person = member(store)
    coverage = publish(store, assignment(store, person))
    with pytest.raises(TeamsError) as denied:
        store.command('brand-a', 'member:' + person['id'], dict(action='assignment_schedule_pending',
            key='not-owner', id=coverage['id'], version=coverage['version']), lambda i: {}, member_id=person['id'])
    assert denied.value.status == 403
    with store.transaction() as db:
        store.create(db, 'brand-a', 'operation', job_id='job-1', closed=True)
    assert mark_pending(owner, coverage).status_code == 409
    with owner.session_transaction() as session:
        session.update(tenant_id='brand-b', user_email='other@example.invalid')
    assert mark_pending(owner, coverage).status_code == 404


@pytest.mark.parametrize('old_mode,new_mode', [('date', 'dateTime'), ('dateTime', 'date')])
def test_google_patch_clears_previous_date_type_and_preserves_guest_response(old_mode, new_mode):
    client = CalendarClient('brand-a')
    value = lambda mode: '2026-11-14' if mode == 'date' else '2026-11-14T15:00:00-06:00'
    previous = dict(start={old_mode:value(old_mode)}, end={old_mode:value(old_mode)},
        attendees=[dict(email='a@example.com', responseStatus='accepted')],
        extendedProperties={'private': {'flow_identity':'assignment:a', 'flow_digest':'old'}})
    calls = []
    def request(method, event_id='', body=None, notify=False):
        calls.append((method, body))
        return deepcopy(previous) if method == 'GET' else body
    client.request = request
    event = dict(start={new_mode:value(new_mode)}, end={new_mode:value(new_mode)}, attendees=[dict(email='a@example.com')])
    client.sync('event', event, 'new', 'assignment:a')
    method, patch = calls[-1]
    assert method == 'PATCH' and 'attendees' not in patch
    assert patch['start'][old_mode] is None and patch['end'][old_mode] is None
