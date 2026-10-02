import copy
import uuid
import pytest
from conftest import login_as_tenant


@pytest.fixture
def job(auth_client):
    import app as module
    tables = ('jobs', 'clients', 'calendar')
    snapshots = {t: copy.deepcopy(module.store._read_raw(t)) for t in tables}
    suffix = uuid.uuid4().hex[:8]
    client_id = 'activity-client-' + suffix
    job_id = 'activity-job-' + suffix
    module.store.upsert('clients', {'id': client_id, 'first_name': 'Prueba', 'last_name': 'Actividad',
                                   'email': 'test@example.invalid', 'tenant_id': 'tenant-norkevin'})
    module.store.upsert('jobs', {'id': job_id, 'nombre': 'Boda de prueba', 'client_id': client_id,
                                'boda_date': '2027-06-12', 'tenant_id': 'tenant-norkevin'})
    yield auth_client, module, job_id
    for table, records in snapshots.items():
        module.store._save(table, records)


def test_visible_controls_and_wedding_follow_job_date(job):
    client, module, job_id = job
    html = client.get('/jobs/' + job_id).get_data(as_text=True)
    assert 'class="sn-btn job-portal-button"' in html
    assert html.index('Portal del cliente') < html.index('<details class="mobile-event-details"')
    assert '>+ Agregar</button>' in html
    assert '>Editar plantilla ↗</a>' not in html
    assert html.index('Día de la boda') < html.index('Entrega y cierre')
    assert 'datetime="2027-06-12"' in html
    for name in ('Reunión de Meet', 'Save the date', 'Boda civil', 'Welcome party', 'Evento extra', 'Correo electrónico'):
        assert f'<strong>{name}</strong>' in html
    for old in ('To-do', 'Automation', 'Extra Event', 'Appointment'):
        assert f'<strong>{old}</strong>' not in html
    row = module.get_job(job_id)
    row['boda_date'] = '2027-08-21'
    module.upsert_job(row)
    html = client.get('/jobs/' + job_id).get_data(as_text=True)
    assert 'datetime="2027-08-21"' in html and 'href="/calendar?month=2027-08"' in html
    row['boda_date'] = ''
    module.upsert_job(row)
    assert 'Definir fecha' in client.get('/jobs/' + job_id).get_data(as_text=True)


@pytest.mark.parametrize('kind,name', [
    ('appointment', 'Reunión de Meet'), ('extra-event', 'Save the date'),
    ('extra-event', 'Boda civil'), ('extra-event', 'Welcome party'),
    ('extra-event', 'Evento extra'), ('email', 'Correo electrónico'),
])
def test_activity_calendar_lifecycle_without_charges_or_email(job, kind, name):
    client, module, job_id = job
    tables = ('quotes', 'payments', 'payment_schedules', 'pending_emails', 'mail_log')
    before = {t: copy.deepcopy(module.store._read_raw(t)) for t in tables}
    url = f'/api/jobs/{job_id}/workflow-task'
    response = client.post(url, json={'type': kind, 'name': name, 'start_date': '2027-05-01',
                                      'start_time': '10:00', 'end_time': '11:00'})
    assert response.status_code == 200
    task = response.get_json()['task']
    event_id = task['calendar_event_id']
    assert module.store.get('calendar', event_id)['date'] == '2027-05-01'
    assert name in client.get('/calendar?month=2027-05').get_data(as_text=True)
    assert name in client.get('/jobs/' + job_id).get_data(as_text=True)
    assert client.post(url + '/' + task['id'] + '/update', json={
        'name': name, 'start_date': '2027-05-02', 'start_time': '12:00', 'end_time': '13:00',
        'location': 'Antigua',
    }).status_code == 200
    assert module.store.get('calendar', event_id)['date'] == '2027-05-02'
    assert client.post(url + '/' + task['id'] + '/delete', json={}).status_code == 200
    assert module.store.get('calendar', event_id) is None
    assert not module.get_job(job_id)['manual_workflow_tasks']
    assert {t: module.store._read_raw(t) for t in tables} == before


@pytest.mark.parametrize('invalid', [
    {'start_date': ''}, {'start_date': '2027-02-30'}, {'end_date': '2027-04-30'},
    {'start_time': '25:00'}, {'start_time': '12:00', 'end_time': '11:00'},
])
def test_invalid_activity_schedule_never_changes_job_or_calendar(job, invalid):
    client, module, job_id = job
    url = f'/api/jobs/{job_id}/workflow-task'
    valid = {'type': 'email', 'name': 'Correo extra', 'start_date': '2027-05-01'}
    task = client.post(url, json=valid).get_json()['task']
    before = {t: copy.deepcopy(module.store._read_raw(t)) for t in ('jobs', 'calendar')}
    for endpoint in (url, url + '/' + task['id'] + '/update'):
        assert client.post(endpoint, json={**valid, **invalid}).status_code == 400
        assert {t: module.store._read_raw(t) for t in before} == before


def test_activity_requires_same_tenant(job):
    client, module, job_id = job
    login_as_tenant(client, 'tenant-norkevin-photography')
    assert client.post(f'/api/jobs/{job_id}/workflow-task', json={
        'type': 'email', 'start_date': '2027-05-01',
    }).status_code == 404
