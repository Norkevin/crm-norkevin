"""Versioned meeting changes keep calendar, client notices and mail retries aligned."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from conftest import login_as_tenant
from test_lead_meetings import payload
from test_workflow_automatic_delivery import automatic


def setup_meeting(automatic, client, tenant='tenant-norkevin', kind='virtual'):
    a, setup, sent = automatic
    login_as_tenant(client, tenant)
    lead, instance = setup(tenant)
    data = payload()
    if kind == 'presencial':
        data.update(meeting_kind=kind, location='Café de prueba, Antigua')
    base = f"/api/leads/{lead['id']}/meetings"
    created = client.post(base, json=data).get_json()
    return a, lead, instance, sent, data, f"{base}/{created['meeting_id']}"


@pytest.mark.parametrize('tenant', ['tenant-norkevin', 'tenant-norkevin-photography'])
@pytest.mark.parametrize('kind', ['virtual', 'presencial'])
def test_reschedule_and_cancel_notify_once_and_keep_history(automatic, auth_client, tenant, kind):
    a, lead, instance, sent, data, endpoint = setup_meeting(automatic, auth_client, tenant, kind)
    original_workflow = instance.to_dict()
    change = dict(data, request_id=str(uuid4()), version=1, date='2050-11-12', start_time='16:00', end_time='16:45')
    response = auth_client.patch(endpoint, json=change)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['delivery_status'] == 'sent' and response.get_json()['version'] == 2
    assert auth_client.patch(endpoint, json=change).get_json() == response.get_json() and len(sent) == 2
    assert 'Antes: 18/10/2050, 14:00–15:00' in sent[-1][0][2]
    assert 'Fecha: 12/11/2050' in sent[-1][0][2] and '16:00 a 16:45' in sent[-1][0][2]
    assert 'Nueva fecha de reunión' in sent[-1][0][1] and sent[-1][0][0] == lead['email']
    assert 'DTSTART:20501112T220000Z' in auth_client.get('/api/calendar/export.ics').get_data(as_text=True)
    html = auth_client.get(f"/leads/{lead['id']}").get_data(as_text=True)
    assert 'Reprogramada' in html and 'Cambio avisado' in html
    cancel = dict(version=2, request_id=str(uuid4()))
    response = auth_client.post(endpoint + '/cancel', json=cancel)
    assert response.status_code == 200 and response.get_json()['delivery_status'] == 'sent'
    assert auth_client.post(endpoint + '/cancel', json=cancel).get_json() == response.get_json() and len(sent) == 3
    assert 'Reunión cancelada' in sent[-1][0][1] and '12/11/2050' in sent[-1][0][2]
    assert 'https://' not in sent[-1][0][2]
    ics = auth_client.get('/api/calendar/export.ics').get_data(as_text=True)
    assert endpoint.rsplit('/', 1)[-1] not in ics
    token = a._workflow_tenant.set(tenant)
    try:
        stored = a.store.get('calendar', endpoint.rsplit('/', 1)[-1])
        assert stored['version'] == 3 and stored['status'] == 'cancelled' and stored['released_at']
        assert [row['date'] for row in stored['history']] == ['2050-10-18', '2050-11-12']
        assert not any(e['id'] == stored['id'] for e in a._calendar_non_job_events())
    finally:
        a._workflow_tenant.reset(token)
    html = auth_client.get(f"/leads/{lead['id']}").get_data(as_text=True)
    assert 'Cancelada' in html and 'Cancelación avisada' in html
    assert 'data-edit-meeting=' not in html and 'data-cancel-meeting=' not in html
    assert instance.to_dict() == original_workflow


def test_stale_tabs_invalid_changes_and_duplicate_creation_cannot_overwrite(automatic, auth_client):
    a, lead, _, sent, data, endpoint = setup_meeting(automatic, auth_client)
    assert auth_client.patch(endpoint, json=dict(data, version=1)).status_code == 400  # No change.
    assert auth_client.patch(endpoint, json=dict(data, version=True, date='2050-11-12')).status_code == 400
    assert auth_client.patch(endpoint, json=dict(data, version=1, date='2020-01-01')).status_code == 400
    change = dict(data, request_id=str(uuid4()), version=1, date='2050-11-12')
    assert auth_client.patch(endpoint, json=change).status_code == 200
    assert auth_client.patch(endpoint, json=dict(change, date='2050-11-13')).status_code == 409
    assert auth_client.patch(endpoint, json=dict(change, request_id=str(uuid4()))).status_code == 409
    assert auth_client.post(endpoint + '/cancel', json=dict(version=1, request_id=str(uuid4()))).status_code == 409
    assert auth_client.post(endpoint + '/invite', json=dict(version=1)).status_code == 409
    assert auth_client.post(endpoint.rsplit('/', 1)[0], json=data).status_code == 409
    assert len(sent) == 2
    login_as_tenant(auth_client, 'tenant-norkevin-photography')
    assert auth_client.patch(endpoint, json=change).status_code == 404
    assert auth_client.post(endpoint + '/cancel', json=dict(version=2, request_id=str(uuid4()))).status_code == 404
    assert len(sent) == 2


@pytest.mark.parametrize('action', ['update', 'cancel'])
def test_outdated_pending_mail_is_cancelled_and_cannot_be_sent_later(automatic, auth_client, monkeypatch, action):
    from src.mail_tracker import get_tracker
    monkeypatch.setenv('WORKFLOW_AUTO_SEND_BRANDS', '')
    a, lead, _, sent, data, endpoint = setup_meeting(automatic, auth_client)
    token = a._workflow_tenant.set(lead['tenant_id'])
    try:
        old = next(m for m in a.store.list('pending_emails') if m.get('lead_id') == lead['id'])
    finally:
        a._workflow_tenant.reset(token)
    monkeypatch.setenv('WORKFLOW_AUTO_SEND_BRANDS', 'astral,norkevin')
    request_data = dict(data, version=1, request_id=str(uuid4()), date='2050-11-12')
    response = auth_client.patch(endpoint, json=request_data) if action == 'update' else auth_client.post(endpoint + '/cancel', json=request_data)
    assert response.status_code == 200 and response.get_json()['delivery_status'] == 'sent' and len(sent) == 1
    token = a._workflow_tenant.set(lead['tenant_id'])
    try:
        assert a.store.get('pending_emails', old['id'])['status'] == 'discarded'
        # Even a restored stale snapshot must fail the send-time version guard.
        old['status'] = 'pending'
        a.store.upsert('pending_emails', old)
        result = get_tracker().approve_and_send(old['id'], sender_tenant_id=lead['tenant_id'])
        assert not result['ok'] and result['pendiente']['status'] == 'blocked'
        direct = get_tracker().log_email(old['to'], old['subject'], old['body'], lead_id=lead['id'],
            tenant_id=lead['tenant_id'], source=old['source'], idempotency_key=old['idempotency_key'])
        assert direct['status'] == 'blocked' and len(sent) == 1
    finally:
        a._workflow_tenant.reset(token)


def test_change_waits_for_an_in_flight_notice_and_calendar_delete_cannot_bypass_notice(automatic, auth_client, monkeypatch):
    monkeypatch.setenv('WORKFLOW_AUTO_SEND_BRANDS', '')
    a, lead, _, sent, data, endpoint = setup_meeting(automatic, auth_client)
    token = a._workflow_tenant.set(lead['tenant_id'])
    try:
        mail = next(m for m in a.store.list('pending_emails') if m.get('lead_id') == lead['id'])
        mail['status'] = 'sending'
        a.store.upsert('pending_emails', mail)
    finally:
        a._workflow_tenant.reset(token)
    change = dict(data, request_id=str(uuid4()), version=1, date='2050-11-12')
    assert auth_client.patch(endpoint, json=change).status_code == 409
    assert auth_client.post(endpoint + '/cancel', json=change).status_code == 409
    assert auth_client.delete('/api/calendar/events/' + endpoint.rsplit('/', 1)[-1]).status_code == 409
    assert not sent


def test_simultaneous_changes_only_apply_one_version(automatic, auth_client):
    a, lead, _, sent, data, endpoint = setup_meeting(automatic, auth_client)
    barrier = Barrier(2)
    def change(day):
        with a.app.test_client() as client:
            login_as_tenant(client, lead['tenant_id'])
            body = dict(data, request_id=str(uuid4()), version=1, date=day)
            barrier.wait(timeout=5)
            return client.patch(endpoint, json=body).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, ['2050-11-12', '2050-11-13']))
    assert sorted(results) == [200, 409] and len(sent) == 2


@pytest.mark.parametrize('failure', ['preparation', 'delivery'])
def test_cancel_survives_notice_failure_and_recovers_current_notice_only(automatic, auth_client, monkeypatch, failure):
    from src.email_delivery import DeliveryResult
    from src.mail_tracker import get_tracker
    a, lead, _, sent, _, endpoint = setup_meeting(automatic, auth_client)
    tracker = get_tracker()
    original = tracker.queue_email
    if failure == 'preparation':
        def unavailable(*args, **kwargs):
            raise RuntimeError('Fallo sintético de preparación')
        monkeypatch.setattr(tracker, 'queue_email', unavailable)
    else:
        monkeypatch.setattr('src.mail_tracker.send_email', lambda *args, **kwargs:
                            sent.append((args, kwargs)) or DeliveryResult(ok=False, provider='test', error='Fallo sintético'))
    cancel = dict(version=1, request_id=str(uuid4()))
    response = auth_client.post(endpoint + '/cancel', json=cancel)
    result = response.get_json()
    assert result['ok'] and result['delivery_status'] == ('unprepared' if failure == 'preparation' else 'failed')
    auth_client.post(endpoint + '/cancel', json=cancel)
    assert len(sent) == (1 if failure == 'preparation' else 2)
    assert endpoint.rsplit('/', 1)[-1] not in auth_client.get('/api/calendar/export.ics').get_data(as_text=True)
    assert auth_client.patch(endpoint, json=dict(payload(), version=2)).status_code == 409
    if failure == 'preparation':
        monkeypatch.setattr(tracker, 'queue_email', original)
        response = auth_client.post(endpoint + '/invite', json=dict(version=2))
        assert response.get_json()['delivery_status'] == 'sent' and len(sent) == 2
        assert 'Reunión cancelada' in sent[-1][0][1]
