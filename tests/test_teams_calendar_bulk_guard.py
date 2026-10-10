from copy import deepcopy
from datetime import datetime, timezone
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

import pytest

from src.google_calendar import CalendarClient
from src.teams import TeamsStore
from src.teams_calendar import CalendarSync, events

TENANT = 'tenant-norkevin-photography'
JOB = dict(id='wedding', nombre='Boda', boda_date='2027-11-14', status='En curso')
EMAIL = 'worker@flow-qa-84982.com'


class Provider(CalendarClient):
    """Simulate successful notifications separately from failed HTTP writes."""
    def __init__(self, previous=None):
        super().__init__(TENANT)
        self.previous = deepcopy(previous)
        self.calls = []
        self.notifications = []
        self.get_failure = False
        self.patch_race = False
        self.insert_conflict = None

    def request(self, method, event_id='', body=None, notify=False, if_match=None):
        self.calls.append((method, event_id, deepcopy(body), notify, if_match))
        if method == 'GET':
            if self.get_failure:
                raise OSError('private provider failure')
            if self.previous is None:
                raise HTTPError('https://calendar.invalid', 404, 'not found', {}, None)
            return deepcopy(self.previous)
        if method == 'POST' and self.insert_conflict:
            self.previous = deepcopy(self.insert_conflict)
            raise HTTPError('https://calendar.invalid', 409, 'conflict', {}, None)
        if method == 'PATCH' and self.patch_race:
            self.previous['attendees'][0]['responseStatus'] = 'accepted'
            self.previous['etag'] = 'new-version'
            self.patch_race = False
        if method == 'PATCH' and if_match and if_match != self.previous.get('etag'):
            raise HTTPError('https://calendar.invalid', 412, 'precondition failed', {}, None)
        if method == 'DELETE':
            self.previous['status'] = 'cancelled'
        self.previous = dict(self.previous or {}, **(body or {}), etag='written-version',
                             htmlLink='https://calendar.google.com/event?test=1')
        for attendee in self.previous.get('attendees', []):
            attendee.setdefault('responseStatus', 'needsAction')
        if notify:
            self.notifications.append(method)
        return deepcopy(self.previous)


@pytest.fixture
def prepared(tmp_path):
    store = TeamsStore(tmp_path / 'teams.sqlite3')
    with store.transaction() as db:
        member = store.create(db, TENANT, 'member', name='Persona', email=EMAIL, active=True, access_version=1)
        assignment = store.create(db, TENANT, 'assignment', job_id=JOB['id'], member_id=member['id'],
                                  status='pendiente', job_day=JOB['boda_date'], role='Fotografía',
                                  start='2027-11-14T13:00:00-06:00', end='2027-11-14T22:00:00-06:00',
                                  instructions='', terms_version=1)
    identity = 'assignment:' + assignment['id']
    event = events(store, TENANT, JOB, 'https://flowingcrm.com', ZoneInfo('America/Guatemala'), 'secret')[identity]
    return store, assignment, identity, event


def live_event(identity, event, status='needsAction', digest='old'):
    return dict(deepcopy(event), id='existing', status='confirmed', etag='first-version',
                attendees=[dict(email=EMAIL, responseStatus=status)],
                htmlLink='https://calendar.google.com/event?test=1',
                extendedProperties={'private': {'flow_identity': identity, 'flow_digest': digest}})


def queued(prepared, provider, *, key='bulk', guarded=True):
    store, assignment, identity, event = prepared
    sync = CalendarSync(store, lambda tenant: provider)
    sync.invitation_eligible = lambda tenant, record: True
    sent = []
    sync.send_invitation_email = lambda tenant, record: sent.append(record['identity'])
    sync.enqueue_events(TENANT, JOB['id'], {identity: event}, background=False, include_new=True,
                        delivery_key=key, invite_unanswered_only=guarded)
    return sync, sent


def row(store):
    with store.transaction() as db:
        return store.records(db, TENANT, 'calendar_sync')[0]


@pytest.mark.parametrize('status', ['accepted', 'declined', 'tentative', 'cancelled', 'unknown'])
def test_live_terminal_response_skips_calendar_email_and_archive(prepared, status):
    store, _, identity, event = prepared
    previous = live_event(identity, event, status)
    if status == 'cancelled':
        previous['status'] = 'cancelled'
    provider = Provider(previous)
    sync, sent = queued(prepared, provider)
    with store.transaction() as db:
        cached = store.records(db, TENANT, 'calendar_sync')[0]
        cached['response_status'] = 'needsAction'
        store.save(db, TENANT, 'calendar_sync', cached)
    sync.drain(TENANT)
    current = row(store)
    assert [call[0] for call in provider.calls] == ['GET']
    assert provider.notifications == [] and sent == []
    assert current['status'] == 'synced' and current['response_status'] == status
    assert current['bulk_skip_reason'] == status and current['email_status'] == 'skipped'
    assert current['invite_unanswered_only'] is False
    with store.transaction() as db:
        assert store.records(db, TENANT, 'team_mail') == []


def test_existing_unanswered_guest_patches_conditionally_and_new_guest_creates(prepared):
    store, _, identity, event = prepared
    provider = Provider(live_event(identity, event))
    sync, sent = queued(prepared, provider)
    sync.drain(TENANT)
    assert provider.notifications == ['PATCH'] and sent == [identity]
    patch = provider.calls[-1]
    assert patch[4] == 'first-version' and patch[3] is True
    assert 'attendees' not in patch[2]
    assert row(store)['invite_unanswered_only'] is False
    provider = Provider()
    result = provider.sync('new', event, 'new-digest', identity, invite_unanswered_only=True)
    assert provider.notifications == ['POST'] and result['attendees'][0]['responseStatus'] == 'needsAction'


@pytest.mark.parametrize('change,reason', [('guest', 'guest_changed'), ('etag', 'missing_etag')])
def test_bulk_cannot_replace_guest_or_patch_without_etag(prepared, change, reason):
    _, _, identity, event = prepared
    previous = live_event(identity, event)
    if change == 'guest':
        previous['attendees'][0]['email'] = 'replacement@flow-qa-84982.com'
    else:
        previous.pop('etag')
    provider = Provider(previous)
    result = provider.sync('existing', event, 'new-digest', identity, invite_unanswered_only=True)
    assert result['_flow_invite_skipped'] == reason and provider.notifications == []
    assert [call[0] for call in provider.calls] == ['GET']


@pytest.mark.parametrize('response', [None, '', 'unrecognized'])
def test_missing_or_unrecognized_live_rsvp_is_never_notified(prepared, response):
    _, _, identity, event = prepared
    provider = Provider(live_event(identity, event, response))
    result = provider.sync('existing', event, 'new-digest', identity, invite_unanswered_only=True)
    assert result['_flow_invite_skipped'] == 'unknown' and provider.notifications == []


def test_bulk_does_not_recreate_gone_cancelled_event(prepared):
    _, _, identity, event = prepared
    client = CalendarClient(TENANT)
    calls = []
    def request(method, *args, **options):
        calls.append(method)
        raise HTTPError('https://calendar.invalid', 410, 'gone', {}, None)
    client.request = request
    result = client.sync('existing', event, 'new-digest', identity, invite_unanswered_only=True)
    assert calls == ['GET'] and result['_flow_invite_skipped'] == 'cancelled'


def test_uncertain_previous_success_does_not_notify_or_email_again(prepared):
    store, _, identity, event = prepared
    provider = Provider()
    sync, sent = queued(prepared, provider)
    provider.previous = live_event(identity, event, digest=row(store)['digest'])
    sync.drain(TENANT)
    assert provider.notifications == [] and sent == []
    assert row(store)['bulk_skip_reason'] == 'already_delivered'


@pytest.mark.parametrize('status', ['accepted', 'declined'])
def test_post_conflict_rechecks_live_rsvp_before_patch(prepared, status):
    _, _, identity, event = prepared
    provider = Provider()
    provider.insert_conflict = live_event(identity, event, status)
    result = provider.sync('existing', event, 'new-digest', identity, invite_unanswered_only=True)
    assert [call[0] for call in provider.calls] == ['GET', 'POST', 'GET']
    assert provider.notifications == [] and result['_flow_invite_skipped'] == status


def test_get_failure_and_concurrent_acceptance_fail_closed_then_recheck(prepared):
    store, _, identity, event = prepared
    provider = Provider(live_event(identity, event))
    provider.get_failure = True
    sync, sent = queued(prepared, provider)
    sync.drain(TENANT)
    assert row(store)['status'] == 'failed' and row(store)['invite_unanswered_only'] is True
    assert provider.notifications == [] and sent == []
    provider.get_failure = False
    provider.patch_race = True
    with store.transaction() as db:
        current = store.records(db, TENANT, 'calendar_sync')[0]
        current['retry_after'] = None
        store.save(db, TENANT, 'calendar_sync', current)
    sync.drain(TENANT)
    assert row(store)['status'] == 'failed' and row(store)['invite_unanswered_only'] is True
    assert provider.notifications == [] and sent == []
    with store.transaction() as db:
        current = store.records(db, TENANT, 'calendar_sync')[0]
        current['retry_after'] = None
        store.save(db, TENANT, 'calendar_sync', current)
    sync.drain(TENANT)
    assert row(store)['response_status'] == 'accepted' and row(store)['email_status'] == 'skipped'
    assert provider.notifications == [] and sent == []


def test_fresh_local_eligibility_blocks_changed_assignment_before_any_google_call(prepared):
    store, assignment, identity, event = prepared
    provider = Provider(live_event(identity, event))
    sync, sent = queued(prepared, provider)
    def eligible(tenant, record):
        with store.transaction() as db:
            return store.get(db, tenant, 'assignment', assignment['id'])['status'] in ('pendiente', 'reconfirmar') or 'La cobertura ya recibió respuesta.'
    sync.invitation_eligible = eligible
    with store.transaction() as db:
        assignment['status'] = 'aceptada'
        store.save(db, TENANT, 'assignment', assignment)
    sync.drain(TENANT)
    assert provider.calls == [] and sent == []
    assert row(store)['status'] == 'paused' and row(store)['email_status'] == 'skipped'


def test_mail_rechecks_local_state_after_successful_calendar_update(prepared):
    store, _, identity, event = prepared
    provider = Provider(live_event(identity, event))
    sync, sent = queued(prepared, provider)
    sync.invitation_eligible = lambda tenant, record: not provider.notifications or 'La cobertura cambió.'
    sync.drain(TENANT)
    assert provider.notifications == ['PATCH'] and sent == []
    assert row(store)['email_status'] == 'skipped' and row(store)['bulk_skip_reason'] == 'La cobertura cambió.'


def test_background_preserves_pending_bulk_policy_and_individual_clears_it(prepared):
    store, assignment, identity, event = prepared
    sync = CalendarSync(store)
    options = dict(background=False, include_new=True, invite_ids=[assignment['id']])
    sync.enqueue(TENANT, JOB, 'https://flowingcrm.com', ZoneInfo('America/Guatemala'), 'secret',
                 delivery_key='bulk', invite_unanswered_only=True, **options)
    sync.enqueue(TENANT, dict(JOB, location='Lugar actualizado'), 'https://flowingcrm.com',
                 ZoneInfo('America/Guatemala'), 'secret', **options)
    with store.transaction() as db:
        records = store.records(db, TENANT, 'calendar_sync')
        current = next(record for record in records if record['identity'] == identity)
        assert current['invite_unanswered_only'] is True and current['delivery_key'] == 'bulk'
        assert not next(record for record in records if record['identity'].startswith('job:')).get('invite_unanswered_only')
        current['bulk_skip_reason'] = 'accepted'
        store.save(db, TENANT, 'calendar_sync', current)
    sync.enqueue(TENANT, JOB, 'https://flowingcrm.com', ZoneInfo('America/Guatemala'), 'secret',
                 delivery_key='individual', **options)
    with store.transaction() as db:
        current = next(record for record in store.records(db, TENANT, 'calendar_sync') if record['identity'] == identity)
        assert current['invite_unanswered_only'] is False and 'bulk_skip_reason' not in current


def test_background_cancellation_replaces_bulk_guard_and_deletes_existing_invite(prepared):
    store, _, identity, event = prepared
    provider = Provider(live_event(identity, event))
    sync, sent = queued(prepared, provider, key='individual', guarded=False)
    sync.drain(TENANT)
    assert row(store)['status'] == 'synced'
    provider.calls.clear()
    provider.notifications.clear()
    sent.clear()
    sync.enqueue_events(TENANT, JOB['id'], {identity: event}, background=False,
                        delivery_key='bulk', invite_unanswered_only=True)
    assert row(store)['invite_unanswered_only'] is True
    sync.enqueue_events(TENANT, JOB['id'], {identity: None}, background=False)
    sync.invitation_eligible = lambda tenant, record: 'La cobertura fue cancelada.'
    assert row(store)['invite_unanswered_only'] is False
    sync.drain(TENANT)
    assert [call[0] for call in provider.calls] == ['GET', 'DELETE']
    assert provider.notifications == ['DELETE'] and sent == []
    assert row(store)['status'] == 'synced' and row(store)['email_status'] == 'cancelled'
    assert provider.previous['status'] == 'cancelled'


@pytest.mark.parametrize('has_etag', [True, False])
def test_individual_still_notifies_accepted_guest_without_conditional_header(prepared, has_etag):
    _, _, identity, event = prepared
    provider = Provider(live_event(identity, event, 'accepted'))
    if not has_etag:
        provider.previous.pop('etag')
    sync, sent = queued(prepared, provider, guarded=False)
    sync.drain(TENANT)
    assert provider.notifications == ['PATCH'] and sent == [identity]
    assert provider.calls[-1][4] is None and 'attendees' not in provider.calls[-1][2]


def test_calendar_request_sends_conditional_header_only_when_supplied(monkeypatch):
    from src import google_calendar
    requests = []
    class Response:
        def __enter__(self):return self
        def __exit__(self, *args):pass
        def read(self):return b'{}'
    monkeypatch.setattr(google_calendar, 'load_token', lambda tenant: dict(
        refresh_token='fake', access_token='fake', expires_at=datetime.now(timezone.utc).timestamp() + 300))
    monkeypatch.setattr(google_calendar, 'urlopen', lambda request, **options: requests.append(request) or Response())
    client = CalendarClient(TENANT)
    client.request('PATCH', 'event', {}, notify=True, if_match='read-version')
    client.request('PATCH', 'event', {}, notify=True)
    assert requests[0].get_header('If-match') == 'read-version'
    assert requests[1].get_header('If-match') is None
