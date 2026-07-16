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
    assert 'Extra Shoot' in html
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
