"""The native calendar uses the web's committed, tenant-scoped sources."""
import pytest
from flask import template_rendered

from conftest import login_as_tenant
from test_mobile_native import mobile


def test_calendar_matches_web_with_ranges_blocks_leads_and_completed_jobs(mobile):
    crm, client, _, headers, tenant, owner = mobile
    crm.store.upsert('jobs', dict(id='completed-coverage', tenant_id=tenant,
        nombre='Cobertura terminada QA', status='Listo', workflow_progress=100,
        boda_date='2030-01-31', end_date='2030-02-03', location='Lugar QA', lead_id='canonical-source'))
    crm.store.upsert('jobs', dict(id='draft-duplicate', tenant_id=tenant,
        nombre='Trabajo duplicado antiguo', status='Cotizando',
        boda_date='2030-02-04', lead_id='canonical-source'))
    crm.store.upsert('leads', dict(id='lead-date', tenant_id=tenant,
        nombre='Consulta QA', status='Nuevo', fecha_tentativa='2030-02-05'))
    for row in [
        dict(id='block-span', type='block', title='Descanso QA', date='2030-01-31', end_date='2030-02-02'),
        dict(id='event-date', type='event', title='Cita QA', date='2030-02-14'),
        dict(id='legacy-mirror', type='job', title='Espejo antiguo', date='2030-02-01', job_id='completed-coverage'),
        dict(id='released', type='block', title='Liberada', date='2030-02-01', released_at='2030-01-01'),
        dict(id='previous', type='block', title='Anterior', date='2030-01-30', end_date='2030-01-31'),
        dict(id='next', type='event', title='Siguiente', date='2030-03-01'),
    ]:
        crm.store.upsert('calendar', dict(row, tenant_id=tenant))
    response = client.get('/api/mobile/calendar?month=2030-02', headers=headers)
    assert response.status_code == 200
    page = response.json
    assert page['month'] == '2030-02' and page['next_offset'] is None
    records = {(r['kind'], r['id']): r for r in page['records']}
    assert set(records) == {('jobs', 'completed-coverage'), ('leads', 'lead-date'),
                            ('calendar', 'block-span'), ('calendar', 'event-date')}
    assert records['jobs', 'completed-coverage']['end_date'] == '2030-02-03'
    assert records['jobs', 'completed-coverage']['status'] == 'Listo'
    assert records['jobs', 'completed-coverage']['location'] == 'Lugar QA'
    assert records['calendar', 'block-span']['event_type'] == 'block'
    assert records['calendar', 'event-date']['end_date'] == '2030-02-14'
    assert records['leads', 'lead-date']['event_type'] == 'lead'
    assert all({'id', 'kind', 'name', 'status', 'date', 'email', 'phone', 'location',
                'end_date', 'event_type'} <= r.keys() for r in records.values())

    contexts = []
    def capture(sender, template, context, **extra):
        if template.name == 'calendar.html':
            contexts.append(context)
    login_as_tenant(client, tenant, email=owner)
    with template_rendered.connected_to(capture, crm.app):
        assert client.get('/calendar?year=2030&month=2').status_code == 200
    web_events = {e['id']: e for week in contexts[-1]['calendar_grid']
                  for cell in week for e in cell['events']}
    kinds = {'job': 'jobs', 'lead': 'leads', 'event': 'calendar', 'block': 'calendar'}
    web_targets = {(kinds[e['type']], e.get('job_id') if e['type'] == 'job'
                   else e.get('lead_id') if e['type'] == 'lead' else e['id'])
                  for e in web_events.values()}
    assert set(records) == web_targets


def test_calendar_bearer_scoping_and_month_is_not_truncated(mobile):
    crm, client, _, headers, tenant, _ = mobile
    for number in range(61):
        crm.store.upsert('calendar', dict(id=f'owned-{number:02}', tenant_id=tenant,
            type='event', title=f'Evento QA {number}', date='2030-02-14'))
    crm.store.upsert('calendar', dict(id='foreign', tenant_id='other-company',
        type='block', title='Privado de otra empresa', date='2030-02-14'))
    login_as_tenant(client, 'other-company', email='other@example.invalid')
    page = client.get('/api/mobile/calendar?month=2030-02', headers=headers).json
    assert len(page['records']) == 61
    assert page['next_offset'] is None
    assert {r['id'] for r in page['records']} == {f'owned-{n:02}' for n in range(61)}
    assert client.get('/api/mobile/records/calendar/foreign', headers=headers).status_code == 404
    assert client.get('/api/mobile/records/calendar/owned-00', headers=headers).status_code == 200


@pytest.mark.parametrize('month', ['', '2030-2', '2030-00', '2030-13', '0000-02',
                                  '10000-02', '2030-02-01', 'not-a-month'])
def test_calendar_rejects_invalid_month(mobile, month):
    _, client, _, headers, *_ = mobile
    assert client.get('/api/mobile/calendar', query_string={'month': month}, headers=headers).status_code == 400


def test_calendar_leap_month_and_invalid_source_dates(mobile):
    crm, client, _, headers, tenant, _ = mobile
    for row in [
        dict(id='leap', date='2028-02-29', end_date='2028-03-01'),
        dict(id='invalid-day', date='2028-02-30'),
        dict(id='invalid-end', date='2028-02-10', end_date='invalid'),
        dict(id='reversed-end', date='2028-02-11', end_date='2028-02-09'),
    ]:
        crm.store.upsert('calendar', dict(row, tenant_id=tenant, type='event', title='Evento QA'))
    records = {r['id']: r for r in client.get('/api/mobile/calendar?month=2028-02', headers=headers).json['records']}
    assert set(records) == {'leap', 'invalid-end', 'reversed-end'}
    assert records['leap']['end_date'] == '2028-03-01'
    assert records['invalid-end']['end_date'] == '2028-02-10'
    assert records['reversed-end']['end_date'] == '2028-02-11'
