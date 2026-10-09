"""Kevin: 'poder agregar un shoot extra, desde jobs, porque muchas veces se
anotan bodas civiles, save the dates, trash the dress, welcome party y quiero
que se visualicen en el calendario y en jobs' (con captura de Studio Ninja
como referencia). El flujo de 'Extra Event'/'Appointment' antes creaba la
tarea al instante sin pedir fecha (heredaba boda_date) y el evento de
calendario se guardaba con type='job' -- pero /calendar solo lista type='event'
(las de tipo job se regeneran fresco desde cada job real), asi que el shoot
extra nunca aparecia en el calendario."""
import uuid


def _make_job_with_client(app_module, suffix):
    client_id = f'client-extrashoot-{suffix}'
    job_id = f'job-extrashoot-{suffix}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Extra', 'last_name': 'Shoot',
        'email': 'extrashoot@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Extra Shoot Test',
        'boda_date': '2027-04-17', 'tenant_id': 'tenant-norkevin',
    })
    return job_id


def test_extra_event_requires_a_start_date(auth_client):
    import app as app_module
    job_id = _make_job_with_client(app_module, uuid.uuid4().hex[:6])

    resp = auth_client.post(f'/api/jobs/{job_id}/workflow-task', json={
        'type': 'extra-event', 'name': 'Boda civil',
    })
    assert resp.status_code == 400
    assert resp.get_json()['ok'] is False


def test_to_do_task_does_not_require_a_date(auth_client):
    """Los tipos sin agenda (to-do, automation) siguen funcionando sin fecha,
    como antes."""
    import app as app_module
    job_id = _make_job_with_client(app_module, uuid.uuid4().hex[:6])

    resp = auth_client.post(f'/api/jobs/{job_id}/workflow-task', json={
        'type': 'to-do', 'name': 'Llamar al cliente',
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['calendar_event'] is None


def test_extra_event_creates_a_calendar_event_with_type_event(auth_client):
    """Bug real: guardar type='job' hacia que /calendar lo descartara (esa
    ruta solo toma type='event' -- las de tipo job se regeneran solas desde
    boda_date de cada job real, sin fecha custom)."""
    import app as app_module
    job_id = _make_job_with_client(app_module, uuid.uuid4().hex[:6])

    resp = auth_client.post(f'/api/jobs/{job_id}/workflow-task', json={
        'type': 'extra-event', 'name': 'Boda civil',
        'start_date': '2026-07-18', 'start_time': '12:00', 'end_time': '13:00',
        'location': 'Hotel Atitlan, Panajachel, Guatemala',
        'show_in_portal': True,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['calendar_event']['type'] == 'event'
    assert data['calendar_event']['date'] == '2026-07-18'
    assert data['calendar_event']['location'] == 'Hotel Atitlan, Panajachel, Guatemala'

    event_id = data['calendar_event']['id']
    stored = app_module.store.get('calendar', event_id)
    assert stored['type'] == 'event'

    task = data['task']
    assert task['start_date'] == '2026-07-18'
    assert task['start_time'] == '12:00'
    assert task['end_time'] == '13:00'
    assert task['location'] == 'Hotel Atitlan, Panajachel, Guatemala'
    assert task['show_in_portal'] is True


def test_extra_event_appears_on_the_calendar_page(auth_client):
    import app as app_module
    job_id = _make_job_with_client(app_module, uuid.uuid4().hex[:6])

    auth_client.post(f'/api/jobs/{job_id}/workflow-task', json={
        'type': 'extra-event', 'name': 'Save the Date',
        'start_date': '2026-08-15', 'location': 'Antigua Guatemala',
    })

    resp = auth_client.get('/calendar?month=2026-08')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Save the Date' in html


def test_extra_event_shows_location_and_schedule_on_the_job_page(auth_client):
    import app as app_module
    job_id = _make_job_with_client(app_module, uuid.uuid4().hex[:6])

    auth_client.post(f'/api/jobs/{job_id}/workflow-task', json={
        'type': 'extra-event', 'name': 'Trash the Dress',
        'start_date': '2027-05-01', 'start_time': '15:00', 'end_time': '17:00',
        'location': 'Lago de Atitlan',
    })

    resp = auth_client.get(f'/jobs/{job_id}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Trash the Dress' in html
    assert 'Lago de Atitlan' in html
    assert 'Sesión extra' in html
    assert '2027-05-01' in html


def test_appointment_creates_calendar_event_too(auth_client):
    import app as app_module
    job_id = _make_job_with_client(app_module, uuid.uuid4().hex[:6])

    resp = auth_client.post(f'/api/jobs/{job_id}/workflow-task', json={
        'type': 'appointment', 'name': 'Reunion con el cliente',
        'start_date': '2026-09-01',
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['calendar_event']['type'] == 'event'


def test_civil_is_a_secondary_job_row_and_opens_teams(auth_client):
    import app as crm
    job_id = _make_job_with_client(crm, uuid.uuid4().hex[:6])
    created = auth_client.post(f'/api/jobs/{job_id}/workflow-task',json=dict(type='extra-event',name='Boda civil operativa',
                              start_date='2026-11-07',start_time='10:00',end_time='12:00',location='Civil de prueba')).get_json()
    identifier = 'secondary:' + created['task']['calendar_event_id']
    html = auth_client.get('/jobs').get_data(as_text=True)
    assert 'Boda civil operativa' in html and 'Trabajo secundario' in html
    assert f'/jobs/{job_id}' in html and f'/teams/jobs/{identifier}' in html
    opened = auth_client.get('/jobs/'+identifier)
    assert opened.status_code == 302 and opened.headers['Location'].endswith('/teams/jobs/'+identifier)
    assert 'Trabajos secundarios' in auth_client.get('/jobs/'+job_id).get_data(as_text=True)


def test_secondary_source_cannot_be_deleted_with_team_history(auth_client, monkeypatch, tmp_path):
    import app as crm
    from src.teams import TeamsStore
    database = TeamsStore(tmp_path/'secondary.sqlite3')
    monkeypatch.setitem(crm.app.extensions,'teams',database)
    job_id = _make_job_with_client(crm,uuid.uuid4().hex[:6])
    created = auth_client.post(f'/api/jobs/{job_id}/workflow-task',json=dict(type='extra-event',name='Boda civil',start_date='2026-11-07')).get_json()
    task = created['task']
    with database.transaction() as db:
        database.create(db,'tenant-norkevin','assignment',job_id='secondary:'+task['calendar_event_id'],member_id='synthetic')
    assert auth_client.post(f'/api/jobs/{job_id}/workflow-task/{task["id"]}/delete').status_code == 409
    assert auth_client.delete(f'/api/calendar/events/{task["calendar_event_id"]}').status_code == 409
    assert crm.store.get('jobs',job_id)['manual_workflow_tasks']


def test_completed_secondary_coverage_is_not_active(auth_client):
    import app as m
    job_id = _make_job_with_client(m, uuid.uuid4().hex[:6])
    job = m.store.get('jobs', job_id)
    job['manual_workflow_tasks'] = [dict(id='past-civil', type='extra-event', name='Civil completada QA', start_date='2020-03-07', status='done')]
    m.store.upsert('jobs', job)
    html = auth_client.get('/jobs').get_data(as_text=True)
    import re
    row = re.search(r'<tr[^>]*data-id="secondary:past-civil".*?</tr>', html, re.S)
    if not row:
        row = re.search(r'<tr[^>]*data-name="civil completada qa".*?</tr>', html, re.S)
    assert row is not None
    assert 'data-activo="0"' in row.group(0)
    assert 'data-completado="1"' in row.group(0)
    assert 'Completada' in row.group(0)
