"""A real guest's current Calendar RSVP confirms the same portal coverage."""
from copy import deepcopy

import pytest

from test_flow_teams import web, member, member_client, records
from src.teams_calendar import attendee_response
from src.teams import TeamsError


class Calendar:
    def __init__(self):
        self.events = {}
        self.writes = []

    def sync(self, event_id, event, digest, identity, **options):
        self.writes.append(identity)
        result = dict(deepcopy(event), id=event_id, status='confirmed', etag='provider-version',
                      extendedProperties={'private': {'flow_identity': identity, 'flow_digest': digest}})
        for guest in result.get('attendees', []):
            guest['responseStatus'] = 'needsAction'
        self.events[identity] = result
        return deepcopy(result)

    def response(self, event_id, identity):
        assert self.events[identity]['id'] == event_id
        return deepcopy(self.events[identity])


@pytest.fixture
def invitation(web):
    app, owner, storage = web
    from flask import session
    with app.test_request_context('/'):
        session['tenant_id'] = 'brand-a'
        job = dict(storage.get('jobs', 'job-1'), boda_date='2099-11-14')
        storage.upsert('jobs', job)
    store = app.extensions['teams']
    person = member(store)
    with store.transaction() as db:
        person['email'] = 'worker@flow-qa-84982.com'
        store.save(db, 'brand-a', 'member', person)

    def command(action, **fields):
        from uuid import uuid4
        response = owner.post('/api/teams/command', headers={'X-Teams-CSRF': 'csrf'},
                              json=dict(action=action, key=str(uuid4()), **fields))
        assert response.status_code == 200, response.get_data(as_text=True)
        return response.get_json()['record']

    coverage = command('assignment', job_id=job['id'], member_id=person['id'], role='Foto', slot='Principal',
                       amount='1500', start='2099-11-14T13:00', end='2099-11-14T22:00', buffer=30)
    coverage = command('assignment_publish', id=coverage['id'], version=coverage['version'])
    sync = app.extensions['teams_calendar']
    provider = Calendar()
    sync.client_factory = lambda tenant: provider
    from zoneinfo import ZoneInfo
    sync.enqueue('brand-a', job, 'https://flow.invalid', ZoneInfo('America/Guatemala'), 'secret',
                 background=False, include_new=True, delivery_key='current-invitation')
    sync.drain('brand-a')
    identity = 'assignment:' + coverage['id']
    provider.events[identity]['attendees'][0]['responseStatus'] = 'accepted'
    return app, owner, storage, store, person, coverage, sync, provider, identity, command


def poll(store, sync):
    with store.transaction() as db:
        for item in store.records(db, 'brand-a', 'calendar_sync'):
            item['response_retry_at'] = None
            store.save(db, 'brand-a', 'calendar_sync', item)
    sync.refresh_responses('brand-a')


def test_calendar_acceptance_updates_existing_portal_once_without_sends_or_financial_changes(invitation):
    app, owner, _, store, person, coverage, sync, provider, _, _ = invitation
    portal = member_client(app, owner, person)
    before = {kind: deepcopy(records(store, kind)) for kind in ('cost', 'payment', 'document', 'task')}
    writes = list(provider.writes)
    poll(store, sync)
    accepted, = records(store, 'assignment')
    assert accepted['status'] == 'aceptada' and accepted['accepted_terms'] == coverage['terms_version']
    assert accepted['accepted_by'] == 'Google Calendar' and accepted['accepted_at']
    assert accepted['version'] == coverage['version'] + 1
    summary = portal.get('/teams-portal/summary').get_json()
    assert summary['assignments'][0]['status'] == 'aceptada'
    assert summary['assignments'][0]['accepted_terms'] == coverage['terms_version']
    poll(store, sync)
    assert records(store, 'assignment') == [accepted]
    assert len([a for a in records(store, 'audit') if a['action'] == 'calendar_acceptance']) == 1
    assert provider.writes == writes
    assert {kind: records(store, kind) for kind in before} == before
    assert records(store, 'assignment', tenant='brand-b') == []


def test_legacy_pending_first_terms_are_confirmed_without_resending(invitation):
    _, _, _, store, _, _, sync, _, _, _ = invitation
    with store.transaction() as db:
        for item in store.records(db, 'brand-a', 'calendar_sync'):
            item.pop('response_snapshot', None)
            item['response_status'] = item['response_notified_status'] = 'accepted'
            store.save(db, 'brand-a', 'calendar_sync', item)
    poll(store, sync)
    assert records(store, 'assignment')[0]['status'] == 'aceptada'


def test_new_invitation_tracks_terms_edited_before_first_publication(invitation):
    _, _, _, store, _, _, sync, provider, _, command = invitation
    person = command('member', name='Otra persona', email='second@flow-qa-84982.com', role='Video', rate='1000')
    draft = command('assignment', job_id='job-1', member_id=person['id'], role='Video', slot='Video',
                    amount='1000', start='2099-11-14T13:00', end='2099-11-14T22:00', buffer=30)
    edited = command('assignment_edit', id=draft['id'], version=draft['version'], amount='1200',
                     start='2099-11-14T13:00', end='2099-11-14T22:00', buffer=30, reason='Honorario revisado')
    published = command('assignment_publish', id=edited['id'], version=edited['version'])
    identity = 'assignment:' + published['id']
    from zoneinfo import ZoneInfo
    sync.enqueue('brand-a', dict(id='job-1', nombre='Ejemplo', boda_date='2099-11-14', status='En curso'),
                 'https://flow.invalid', ZoneInfo('America/Guatemala'), 'secret', background=False,
                 include_new=True, invite_ids=[published['id']], delivery_key='new-current-terms')
    sync.drain('brand-a')
    provider.events[identity]['attendees'][0]['responseStatus'] = 'accepted'
    poll(store, sync)
    accepted = next(a for a in records(store, 'assignment') if a['id'] == published['id'])
    assert accepted['status'] == 'aceptada' and accepted['accepted_terms'] == 2


def test_sync_write_response_can_also_confirm_the_portal(invitation):
    _, _, _, store, _, coverage, sync, provider, identity, _ = invitation
    original = provider.sync
    def accepted(event_id, event, digest, current_identity, **options):
        result = original(event_id, event, digest, current_identity, **options)
        if current_identity == identity: result['attendees'][0]['responseStatus'] = 'accepted'
        return result
    provider.sync = accepted
    from zoneinfo import ZoneInfo
    sync.enqueue('brand-a', dict(id='job-1', nombre='Ejemplo', boda_date='2099-11-14', status='En curso'),
                 'https://flow.invalid', ZoneInfo('America/Guatemala'), 'secret', background=False,
                 invite_ids=[coverage['id']], delivery_key='deliberate-current-invite')
    sync.drain('brand-a')
    assert records(store, 'assignment')[0]['status'] == 'aceptada'


def test_concurrent_terms_change_during_google_read_remains_protected(invitation):
    _, _, _, store, _, coverage, sync, provider, identity, _ = invitation
    original = provider.response
    def response(event_id, current_identity):
        result = original(event_id, current_identity)
        if current_identity == identity:
            with store.transaction() as db:
                assignment = store.get(db, 'brand-a', 'assignment', coverage['id'])
                assignment.update(status='reconfirmar', terms_version=assignment['terms_version'] + 1)
                store.save(db, 'brand-a', 'assignment', assignment)
        return result
    provider.response = response
    poll(store, sync)
    assignment, = records(store, 'assignment')
    assert assignment['status'] == 'reconfirmar' and not assignment.get('accepted_terms')


@pytest.mark.parametrize('changed_content', [False, True])
def test_accepted_bulk_skip_uses_old_digest_only_for_the_identical_current_invitation(invitation, changed_content):
    _, _, _, store, _, _, sync, provider, identity, _ = invitation
    event = provider.events[identity]
    event['extendedProperties']['private']['flow_digest'] = 'previous-delivery-digest'
    if changed_content: event['description'] += '\nOld instructions'
    with store.transaction() as db:
        item = next(r for r in store.records(db, 'brand-a', 'calendar_sync') if r['identity'] == identity)
        item['bulk_skip_reason'] = 'accepted'
        store.save(db, 'brand-a', 'calendar_sync', item)
    poll(store, sync)
    assert records(store, 'assignment')[0]['status'] == ('pendiente' if changed_content else 'aceptada')


def test_bulk_already_accepted_result_confirms_without_patching_or_resending(invitation):
    _, _, _, store, _, _, sync, provider, identity, _ = invitation
    original = deepcopy(provider.events[identity])
    provider.sync = lambda *args, **kwargs: dict(deepcopy(original), _flow_invite_skipped='accepted')
    sync.invitation_eligible = lambda *args: True
    item = next(r for r in records(store, 'calendar_sync') if r['identity'] == identity)
    writes = list(provider.writes)
    sync.enqueue_events('brand-a', 'job-1', {identity: item['event']}, background=False,
                        invite_ids=[identity.split(':',1)[1]], delivery_key='bulk-key', invite_unanswered_only=True)
    sync.drain('brand-a')
    assert records(store, 'assignment')[0]['status'] == 'aceptada'
    assert provider.writes == writes


@pytest.mark.parametrize('change', ['digest', 'identity', 'organizer', 'missing_guest', 'other_guest',
                                  'inactive', 'changed_email', 'snapshot', 'cancelled', 'past',
                                  'reconfirmar', 'rejected', 'completed', 'job_date', 'job_archived',
                                  'job_removed', 'closed', 'apartada', 'unavailable', 'conflict'])
def test_stale_or_invalid_google_acceptance_does_not_confirm_portal(invitation, change):
    app, _, storage, store, person, coverage, sync, provider, identity, _ = invitation
    event = provider.events[identity]
    if change in ('digest', 'identity'):
        event['extendedProperties']['private']['flow_' + change] = 'stale'
    elif change == 'organizer': event['attendees'][0]['organizer'] = True
    elif change == 'missing_guest': event['attendees'] = []
    elif change == 'other_guest': event['attendees'][0]['email'] = 'another@example.invalid'
    elif change == 'cancelled': event['status'] = 'cancelled'
    with store.transaction() as db:
        current = store.get(db, 'brand-a', 'assignment', coverage['id'])
        member = store.get(db, 'brand-a', 'member', person['id'])
        if change == 'inactive': member['active'] = False
        if change == 'changed_email': member['email'] = 'replacement@example.invalid'
        store.save(db, 'brand-a', 'member', member)
        if change == 'past': current['end'] = '2000-11-14T22:00:00-06:00'
        if change in ('reconfirmar', 'rejected', 'completed'):
            current['status'] = {'reconfirmar':'reconfirmar', 'rejected':'rechazada', 'completed':'realizada'}[change]
        if change == 'snapshot': current['terms_version'] += 1
        store.save(db, 'brand-a', 'assignment', current)
        if change == 'closed': store.create(db, 'brand-a', 'operation', job_id='job-1', closed=True)
        if change == 'apartada': store.create(db, 'brand-a', 'job_classification', job_id='job-1', state='archived')
        if change == 'unavailable':
            store.create(db, 'brand-a', 'availability', member_id=person['id'], start='2099-11-14', end='2099-11-14')
        if change == 'conflict':
            conflict = dict(current, id='other-confirmed', status='aceptada')
            store.save(db, 'brand-a', 'assignment', conflict)
    if change.startswith('job_'):
        from flask import session
        with app.test_request_context('/'):
            session['tenant_id'] = 'brand-a'
            job = storage.get('jobs', 'job-1')
            if change == 'job_date': job['boda_date'] = '2099-11-15'
            elif change == 'job_archived': job['status'] = 'Archivado'
            elif change == 'job_removed':
                def missing(*args): raise TeamsError('Missing',404)
                sync.response_job = missing
            storage.upsert('jobs', job)
    before = deepcopy(records(store, 'assignment'))
    poll(store, sync)
    assert records(store, 'assignment') == before
    assert not [a for a in records(store, 'audit') if a['action'] == 'calendar_acceptance']


@pytest.mark.parametrize('status', ['declined', 'tentative', 'needsAction'])
def test_other_google_responses_keep_existing_financial_and_portal_semantics(invitation, status):
    _, _, _, store, _, _, sync, provider, identity, _ = invitation
    provider.events[identity]['attendees'][0]['responseStatus'] = status
    before = deepcopy(records(store, 'assignment'))
    costs = deepcopy(records(store, 'cost'))
    poll(store, sync)
    assert records(store, 'assignment') == before and records(store, 'cost') == costs


def test_organizer_acceptance_is_not_the_members_response():
    record = {'event': {'attendees': [{'email': 'worker@example.invalid'}]}}
    assert attendee_response(record, {'attendees': [{'email': 'worker@example.invalid',
        'organizer': True, 'responseStatus': 'accepted'}]}) == 'unknown'
