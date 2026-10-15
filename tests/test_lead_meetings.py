"""Meetings use the calendar and real mail policy with a fake delivery provider."""
from copy import deepcopy
from uuid import uuid4

import pytest

from conftest import login_as_tenant
from test_workflow_automatic_delivery import automatic


def payload(**changes):
    return dict(request_id=str(uuid4()), meeting_kind='virtual', date='2050-10-18',
                start_time='14:00', end_time='15:00', meeting_url='https://zoom.us/j/123?pwd=prueba',
                notes='Revisaremos la cobertura de la boda.', **changes)


@pytest.mark.parametrize('tenant', ['tenant-norkevin', 'tenant-norkevin-photography'])
@pytest.mark.parametrize('kind', ['virtual', 'presencial'])
def test_meeting_sends_once_and_appears_in_calendar_without_touching_workflow(automatic, auth_client, tenant, kind):
    a, setup, sent = automatic
    login_as_tenant(auth_client, tenant)
    lead, instance = setup(tenant)
    before = deepcopy(instance.to_dict())
    data = payload()
    if kind == 'presencial':
        data.update(meeting_kind=kind, location='Café de prueba, Antigua; salón 2')
    endpoint = f"/api/leads/{lead['id']}/meetings"
    first = auth_client.post(endpoint, json=data)
    assert first.status_code == 200, first.get_json()
    result = first.get_json()
    assert result['delivery_status'] == 'sent' and len(sent) == 1
    assert auth_client.post(endpoint, json=data).get_json() == result and len(sent) == 1
    assert instance.to_dict() == before
    args, kwargs = sent[0]
    assert args[0] == lead['email']
    assert '18/10/2050' in args[2] and '14:00 a 15:00 (hora de Guatemala)' in args[2]
    assert data['notes'] in args[2]
    assert ('https://zoom.us/j/123?pwd=prueba' if kind == 'virtual' else data['location']) in args[2]
    assert kwargs['metadata']['tenant_id'] == tenant
    html = auth_client.get(f"/leads/{lead['id']}").get_data(as_text=True)
    assert 'Invitación enviada' in html and 'lead-meetings' in html
    calendar = auth_client.get('/calendar?month=2050-10').get_data(as_text=True)
    assert f"/leads/{lead['id']}#lead-meetings" in calendar
    ics = auth_client.get('/api/calendar/export.ics').get_data(as_text=True)
    assert 'DTSTART:20501018T200000Z' in ics and 'DTEND:20501018T210000Z' in ics
    assert ('https://zoom.us/j/123?pwd=prueba' if kind == 'virtual' else r'Café de prueba\, Antigua\; salón 2') in ics
    changed = dict(data, notes='Otros datos')
    assert auth_client.post(endpoint, json=changed).status_code == 409 and len(sent) == 1


@pytest.mark.parametrize('changes', [
    {'date': '2050-02-30'}, {'date': '2020-01-01'}, {'date': '2050-1-01'}, {'date': '9999-12-31'},
    {'end_time': '13:59'}, {'end_time': '14:00'}, {'start_time': '99:00'},
    {'meeting_url': 'javascript:alert(1)'}, {'meeting_url': 'http://zoom.us/j/1'},
    {'meeting_url': 'https://user:pass@zoom.us/j/1'}, {'meeting_url': 'https://zoom.us/\ninjection'},
    {'meeting_url': 'https://zoom.us:invalid/j/1'},
    {'meeting_url': []}, {'meeting_kind': 'presencial', 'location': ''},
    {'meeting_kind': 'desconocido'}, {'request_id': None}, {'notes': 'x' * 3001},
])
def test_invalid_meeting_does_not_save_or_send(automatic, auth_client, changes):
    a, setup, sent = automatic
    lead, _ = setup('tenant-norkevin')
    data = payload()
    data.update(changes)
    response = auth_client.post(f"/api/leads/{lead['id']}/meetings", json=data)
    assert response.status_code == 400 and not sent
    token = a._workflow_tenant.set(lead['tenant_id'])
    try:
        assert not any(e.get('lead_id') == lead['id'] for e in a.store.list('calendar'))
    finally:
        a._workflow_tenant.reset(token)


def test_tenant_isolation_and_missing_email(automatic, auth_client):
    a, setup, sent = automatic
    lead, _ = setup('tenant-norkevin')
    endpoint = f"/api/leads/{lead['id']}/meetings"
    login_as_tenant(auth_client, 'tenant-norkevin-photography')
    assert auth_client.post(endpoint, json=payload()).status_code == 404 and not sent
    assert lead['id'] not in auth_client.get('/calendar?month=2050-10').get_data(as_text=True)
    login_as_tenant(auth_client, 'tenant-norkevin')
    token = a._workflow_tenant.set(lead['tenant_id'])
    try:
        lead['email'] = ''
        a.store.upsert('leads', lead)
    finally:
        a._workflow_tenant.reset(token)
    assert auth_client.post(endpoint, json=payload()).status_code == 400 and not sent


def test_failed_invitation_keeps_meeting_and_never_silently_retries(automatic, auth_client, monkeypatch):
    from src.email_delivery import DeliveryResult
    a, setup, sent = automatic
    lead, _ = setup('tenant-norkevin')
    monkeypatch.setattr('src.mail_tracker.send_email', lambda *args, **kwargs:
                        sent.append(args) or DeliveryResult(ok=False, provider='test', error='Fallo sintético'))
    data = payload()
    endpoint = f"/api/leads/{lead['id']}/meetings"
    first = auth_client.post(endpoint, json=data).get_json()
    assert first['ok'] and first['delivery_status'] == 'failed' and len(sent) == 1
    auth_client.post(endpoint, json=data)
    assert len(sent) == 1
    html = auth_client.get(f"/leads/{lead['id']}").get_data(as_text=True)
    assert 'Revisar invitación' in html and f"/leads/{lead['id']}#lead-meetings" in auth_client.get('/calendar?month=2050-10').get_data(as_text=True)


def test_converted_lead_keeps_meeting_in_job(automatic, auth_client):
    a, setup, _ = automatic
    lead, _ = setup('tenant-norkevin')
    auth_client.post(f"/api/leads/{lead['id']}/meetings", json=payload())
    job, _ = setup('tenant-norkevin', 'job')
    token = a._workflow_tenant.set(lead['tenant_id'])
    try:
        job['lead_id'] = lead['id']
        a.upsert_job(job)
    finally:
        a._workflow_tenant.reset(token)
    response = auth_client.get(f"/jobs/{job['id']}")
    assert response.status_code == 200 and 'Invitación enviada' in response.get_data(as_text=True)


def test_client_meeting_never_becomes_a_secondary_team_coverage():
    from src.linked_coverages import linked_coverages
    jobs = [dict(id='job', lead_id='lead', tenant_id='tenant-norkevin')]
    event = dict(id='meeting', type='event', lead_id='lead', date='2050-10-18',
                 meeting_kind='virtual', title='Reunión virtual · Boda civil', notes='Conversar sobre extra-event')
    assert linked_coverages(jobs, [event]) == []


def test_preparation_failure_can_recover_after_reload_without_duplicate(automatic, auth_client, monkeypatch):
    from src.mail_tracker import get_tracker
    a, setup, sent = automatic
    lead, _ = setup('tenant-norkevin')
    tracker = get_tracker()
    original = tracker.queue_email
    def unavailable(*args, **kwargs):
        raise RuntimeError('Fallo sintético de preparación')
    monkeypatch.setattr(tracker, 'queue_email', unavailable)
    result = auth_client.post(f"/api/leads/{lead['id']}/meetings", json=payload()).get_json()
    assert result['ok'] and result['delivery_status'] == 'unprepared' and not sent
    assert 'Preparar invitación' in auth_client.get(f"/leads/{lead['id']}").get_data(as_text=True)
    monkeypatch.setattr(tracker, 'queue_email', original)
    retry = f"/api/leads/{lead['id']}/meetings/{result['meeting_id']}/invite"
    assert auth_client.post(retry).get_json()['delivery_status'] == 'sent' and len(sent) == 1
    assert auth_client.post(retry).get_json()['delivery_status'] == 'sent' and len(sent) == 1
    login_as_tenant(auth_client, 'tenant-norkevin-photography')
    assert auth_client.post(retry).status_code == 404 and len(sent) == 1


def test_disabled_auto_policy_preserves_manual_approval(automatic, auth_client, monkeypatch):
    _, setup, sent = automatic
    lead, _ = setup('tenant-norkevin')
    monkeypatch.setenv('WORKFLOW_AUTO_SEND_BRANDS', '')
    result = auth_client.post(f"/api/leads/{lead['id']}/meetings", json=payload()).get_json()
    assert result['delivery_status'] == 'pending' and not sent
    html = auth_client.get(f"/leads/{lead['id']}").get_data(as_text=True)
    assert 'Invitación pendiente' in html and 'Hay reuniones guardadas cuya invitación aún no se ha enviado.' in html
