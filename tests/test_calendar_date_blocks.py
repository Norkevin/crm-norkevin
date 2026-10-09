"""Availability and authorized automatic replies, using isolated data/fake mail."""
from test_lead_package_availability import booking
import pytest


def test_block_range_release_and_tenant_isolation(booking):
    crm, client, lead, job, *_ = booking
    response = client.post('/api/calendar/events', json={
        'type': 'block', 'date': '2027-05-14', 'end_date': '2027-05-16'})
    assert response.status_code == 200
    block = response.json['event']
    assert block['title'] == 'Fecha bloqueada'
    assert block['tenant_id'] == lead['tenant_id']
    exported = client.get('/api/calendar/export.ics').get_data(as_text=True)
    assert 'DTEND;VALUE=DATE:20270517' in exported and 'TRANSP:OPAQUE' in exported
    assert block['id'] in exported
    availability = client.post(f"/api/leads/{lead['id']}/check-date").json
    assert availability['disponible'] is False
    assert availability['conflicts'][0]['type'] == 'block'
    assert not [m for m in crm.store.list('pending_emails') if m.get('lead_id') == lead['id']]
    page = client.get('/calendar?year=2027&month=5').get_data(as_text=True)
    assert 'Bloquear fecha' in page and f'data-block-id="{block["id"]}"' in page
    foreign = dict(block, id='foreign-' + block['id'], tenant_id='tenant-astral-weddings')
    token = crm._workflow_tenant.set(foreign['tenant_id'])
    try:
        crm.store.upsert('calendar', foreign)
    finally:
        crm._workflow_tenant.reset(token)
    assert client.post(f"/api/calendar/blocks/{foreign['id']}/release").status_code == 404
    assert client.post(f"/api/calendar/blocks/{block['id']}/release").status_code == 200
    assert crm.store.get('calendar', block['id'])['released_at']
    assert block['id'] not in client.get('/api/calendar/export.ics').get_data(as_text=True)
    assert client.post(f"/api/leads/{lead['id']}/check-date").json['disponible'] is True
    crm.store.upsert('jobs', job)
    assert client.post(f"/api/leads/{lead['id']}/check-date").json['disponible'] is False
    crm.store.delete('calendar', block['id'])
    token = crm._workflow_tenant.set(foreign['tenant_id'])
    try:
        crm.store.delete('calendar', foreign['id'])
    finally:
        crm._workflow_tenant.reset(token)


@pytest.mark.parametrize('date,end', [('invalid',''), ('2027-05-15','2027-05-14'),
                                     ('2027-02-30',''), ('20270515','')])
def test_invalid_block_does_not_write(booking, date, end):
    crm, client, *_ = booking
    before = crm.store.list('calendar')
    assert client.post('/api/calendar/events', json={'type': 'block', 'date': date, 'end_date': end}).status_code == 400
    assert crm.store.list('calendar') == before


@pytest.mark.parametrize('delivery_ok', [True, False])
@pytest.mark.parametrize('channel', ['manual', 'public'])
def test_new_blocked_lead_sends_once_and_tracks_delivery(booking, monkeypatch, delivery_ok, channel):
    crm, client, lead, job, normal, unavailable, _ = booking
    from src.email_delivery import DeliveryResult
    StepStatus = crm.StepStatus
    calls = []
    def send(to, subject, body='', **kwargs):
        calls.append((to, subject, body, kwargs))
        return DeliveryResult(ok=delivery_ok, provider='test', message_id='test-block', mode='test')
    monkeypatch.setattr('src.mail_tracker.send_email', send)
    template = crm.store.get('email_templates', unavailable)
    template['cuerpo'] = 'Fecha no disponible %job_date%. Te recomendamos Astral.'
    crm.store.upsert('email_templates', template)
    block = client.post('/api/calendar/events', json={'type': 'block', 'date': '2027-05-15',
                                                     'title': 'Mi descanso privado'}).json['event']
    if channel == 'public':
        monkeypatch.setattr(crm, '_notify_new_lead', lambda *args: None)
        tenant = crm.store.get('tenants', lead['tenant_id'])
        crm.store.upsert('tenants', dict(tenant, slug='norkevin-photography'))
        token = crm._workflow_tenant.set(None)
        try:
            with crm.app.test_client() as anonymous:
                response = anonymous.post('/api/leads/nuevo', json={'nombre': 'Nueva pareja',
                    'email': 'pareja@example.invalid', 'apellido': 'Prueba', 'pais': 'Guatemala', 'fecha_boda': '2027-05-15',
                    'tenant_slug': 'norkevin-photography'})
        finally:
            crm._workflow_tenant.reset(token)
        assert response.status_code == 200, response.json
        new_id = response.json['lead_id']
    else:
        response = client.post('/api/leads/new', json={'nombre': 'Nueva pareja', 'email': 'pareja@example.invalid',
                                                     'fecha_tentativa': '2027-05-15'})
        new_id = response.json['lead']['id']
    assert response.status_code == 200
    new_lead = crm.get_lead(new_id)
    assert len(calls) == 1
    assert 'Astral' in calls[0][2] and 'Mi descanso privado' not in calls[0][2]
    assert calls[0][3]['metadata']['tenant_id'] == lead['tenant_id']
    pending = next(m for m in crm.store.list('pending_emails') if m.get('lead_id') == new_lead['id'])
    assert pending['template_id'] == unavailable
    assert pending['status'] == ('sent' if delivery_ok else 'failed')
    instance = crm.workflow_engine.get_instance(pending['workflow_instance_id'])
    assert instance.step_states['envio_paquetes'] == (StepStatus.DONE if delivery_ok else StepStatus.FAILED)
    crm._notify_blocked_date_lead(crm.get_lead(new_lead['id']))
    assert len(calls) == 1
    client.post(f"/api/calendar/blocks/{block['id']}/release")
    crm.store.delete('calendar', block['id'])
    crm.store.delete('leads', new_lead['id'])
    crm.store.delete('pending_emails', pending['id'])


def test_normal_events_and_jobs_do_not_auto_send(booking, monkeypatch):
    crm, client, lead, job, *_ = booking
    def unexpected_send(*args, **kwargs):
        pytest.fail('Only blocked dates authorize automatic client replies')
    monkeypatch.setattr('src.mail_tracker.send_email', unexpected_send)
    event = client.post('/api/calendar/events', json={'title': 'Recordatorio', 'date': lead['fecha_tentativa']}).json['event']
    crm._notify_blocked_date_lead(lead)
    assert not crm._lead_date_conflicts(lead)
    crm.store.upsert('jobs', job)
    crm._notify_blocked_date_lead(lead)
    assert not crm.get_lead(lead['id']).get('blocked_date_notice_attempted_at')
    crm.store.delete('calendar', event['id'])


def test_imported_lead_never_gets_automatic_notice(booking, monkeypatch):
    crm, client, lead, *_ = booking
    block = client.post('/api/calendar/events', json={'type': 'block', 'date': lead['fecha_tentativa']}).json['event']
    def unexpected_send(*args, **kwargs):
        pytest.fail('Imported leads must never be mailed automatically')
    monkeypatch.setattr('src.mail_tracker.send_email', unexpected_send)
    crm._notify_blocked_date_lead(dict(lead, source_id='studio-ninja-old-lead'))
    crm._notify_blocked_date_lead(dict(lead, studio_ninja_workflow={'steps': []}))
    assert not crm.get_lead(lead['id']).get('blocked_date_notice_attempted_at')
    crm.store.delete('calendar', block['id'])


def test_whole_day_cells_and_mobile_date_headers_open_date_actions(booking):
    from html.parser import HTMLParser
    _, client, *_=booking
    class Buttons(HTMLParser):
        def __init__(self):super().__init__();self.depth=0;self.cells=[];self.mobile=[]
        def handle_starttag(self,tag,attrs):
            if tag!='button':return
            assert self.depth==0,'Calendar buttons must not nest'
            self.depth+=1;attrs=dict(attrs)
            if 'calendar-cell-trigger' in attrs.get('class',''):self.cells.append(attrs)
            if 'agenda-day-head calendar-day-action' in attrs.get('class',''):self.mobile.append(attrs)
        def handle_endtag(self,tag):
            if tag=='button':self.depth-=1
    document=Buttons();document.feed(client.get('/calendar?year=2027&month=5').get_data(as_text=True))
    assert len(document.cells)==31 and len(document.mobile)==31
    assert all('openDateMenu(' in b['onclick'] and b.get('aria-label') for b in document.cells+document.mobile)
