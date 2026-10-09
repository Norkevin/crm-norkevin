from copy import deepcopy

import pytest

from test_flow_teams import web, member, assignment, publish, records, run, member_client, respond


@pytest.fixture
def confirmation(web):
    app, owner, storage = web
    store = app.extensions['teams']
    person = member(store)
    coverage = publish(store, assignment(store, person))
    with store.transaction() as db:
        store.create(db, 'brand-a', 'calendar_sync', identity='assignment:' + coverage['id'], job_id='job-1',
                     event={'summary': 'Boda'}, status='synced', response_status='needsAction', email_status='sent')
    return app, owner, storage, store, person, coverage


def acknowledge(owner, coverage, status='confirmed', key='manual-confirmation'):
    return owner.post('/api/teams/command', json=dict(action='assignment_acknowledge', id=coverage['id'],
        version=coverage['version'], status=status, key=key), headers={'X-Teams-CSRF': 'csrf'})


@pytest.mark.parametrize('accepted', [False, True])
def test_manual_confirmation_is_audited_reversible_and_does_not_change_portal_calendar_or_money(confirmation, monkeypatch, accepted):
    from src import google_calendar
    app, owner, storage, store, person, coverage = confirmation
    if accepted:
        coverage = respond(store, person, coverage)
    before = owner.get('/api/teams/summary').get_json()
    calendar = deepcopy(records(store, 'calendar_sync'))
    app.config.update(FLOW_TEAMS_LOCAL=False, FLOW_TEAMS_ENABLED=True)
    monkeypatch.setattr(google_calendar, 'connected_email', lambda tenant: 'owner@example.invalid')
    sends = []
    def unexpected_send(*args, **kwargs):
        sends.append(True)
        raise AssertionError('Manual confirmation must not send invitations')
    monkeypatch.setattr(app.extensions['teams_calendar'], 'enqueue', unexpected_send)
    response = acknowledge(owner, coverage)
    assert response.status_code == 200
    confirmed = response.get_json()['record']
    assert confirmed['status'] == coverage['status'] and confirmed.get('accepted_terms') == coverage.get('accepted_terms')
    assert not sends
    assert confirmed['manual_confirmation']['by'] == 'owner@example.invalid'
    assert confirmed['manual_confirmation']['terms_version'] == coverage['terms_version']
    assert acknowledge(owner, coverage).get_json() == response.get_json()
    after = owner.get('/api/teams/summary').get_json()
    assert after['totals'] == before['totals'] and after['costs'] == before['costs'] and after['payments'] == before['payments']
    assert records(store, 'calendar_sync') == calendar
    for path in ('/teams/jobs/job-1', '/teams/calendar'):
        html = owner.get(path).get_data(as_text=True)
        assert 'Confirmación manual · Enterado' in html and 'Google Calendar · Sin responder' in html
    assert len([a for a in records(store, 'audit') if a['action'] == 'assignment_acknowledge']) == 1
    assert acknowledge(owner, coverage, key='stale').status_code == 409
    assert acknowledge(owner, confirmed, status='withdrawn', key='undo').status_code == 200
    assert 'Confirmación manual · Enterado' not in owner.get('/teams/jobs/job-1').get_data(as_text=True)
    audit = [a for a in records(store, 'audit') if a['action'] == 'assignment_acknowledge']
    assert audit[-1]['before']['manual_confirmation'] == confirmed['manual_confirmation']
    assert audit[-1]['after']['manual_confirmation'] is None


def test_manual_confirmation_is_owner_only_and_scoped_to_brand(confirmation):
    app, owner, storage, store, person, coverage = confirmation
    assert owner.post('/api/teams/command', json={'action': 'assignment_acknowledge'}).status_code == 403
    worker = member_client(app, owner, person)
    with worker.session_transaction() as session:
        csrf = session['teams_portal_csrf']
    response = worker.post('/teams-portal/command', json=dict(action='assignment_acknowledge', id=coverage['id'],
        version=coverage['version'], status='confirmed', key='unauthorized'), headers={'X-Teams-CSRF': csrf})
    assert response.status_code == 403
    with owner.session_transaction() as session:
        session.update(tenant_id='brand-b', user_email='other@example.invalid')
    assert acknowledge(owner, coverage).status_code == 404
    assert 'manual_confirmation' not in records(store, 'assignment')[0]


@pytest.mark.parametrize('replacement', [False, True])
def test_changed_terms_or_member_require_a_new_manual_confirmation(confirmation, replacement):
    app, owner, storage, store, person, coverage = confirmation
    confirmed = acknowledge(owner, coverage).get_json()['record']
    new_person = run(store, 'member', name='Otra persona', email='otra@example.invalid', role='Video') if replacement else person
    edited = run(store, 'assignment_edit', id=coverage['id'], version=confirmed['version'],
                 member_id=new_person['id'], role='Video', slot=coverage['slot'], start=coverage['start'],
                 end=coverage['end'], buffer=coverage['buffer'], amount='1500', reason='Cambió la cobertura')
    html = owner.get('/teams/jobs/job-1').get_data(as_text=True)
    assert 'Confirmación manual · Enterado' not in html
    assert 'La confirmación manual anterior requiere revisar' in html
    assert 'Registrar confirmación manual' in html
    assert acknowledge(owner, edited, key='new-terms').status_code == 200
    assert 'Confirmación manual · Enterado' in owner.get('/teams/jobs/job-1').get_data(as_text=True)


@pytest.mark.parametrize('status', ['borrador', 'cancelada', 'rechazada', 'realizada'])
def test_unpublished_or_closed_coverage_cannot_be_confirmed_manually(confirmation, status):
    app, owner, storage, store, person, coverage = confirmation
    with store.transaction() as db:
        coverage['status'] = status
        store.save(db, 'brand-a', 'assignment', coverage)
    assert acknowledge(owner, coverage).status_code == 409
    assert 'manual_confirmation' not in records(store, 'assignment')[0]


def test_closed_operation_cannot_be_confirmed_manually(confirmation):
    app, owner, storage, store, person, coverage = confirmation
    with store.transaction() as db:
        store.create(db, 'brand-a', 'operation', job_id='job-1', closed=True)
    assert acknowledge(owner, coverage).status_code == 409
    assert 'manual_confirmation' not in records(store, 'assignment')[0]
