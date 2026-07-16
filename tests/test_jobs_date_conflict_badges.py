"""Kevin: 'falta implementar esto, telo dije antes' -- los indicadores
naranja/rojo de fecha ya estaban en /leads (ver test_leads_date_conflict_badges.py)
pero el pedido original era para 'leads y jobs'. Aca el sentido es al reves
del de Leads: rojo = otra boda real ya agendada el mismo dia (doble booking
de verdad), naranja = un lead todavia abierto pregunta por la misma fecha
que un job ya confirmado (utile para decidir si ofrecerle otro dia).

El store persiste entre tests (ver conftest.py), asi que /jobs devuelve
TODOS los jobs acumulados de toda la sesion de tests -- las aserciones
tienen que mirar solo la fila del job de este test, no la pagina entera.

Las fechas de boda_date/fecha_tentativa NO pueden ser literales fijos como
'2027-05-08' -- otro archivo de test (test_leads_date_conflict_badges.py)
usa fechas hardcodeadas tambien, y como ambos comparten el mismo store de
toda la sesion, dos tests de archivos distintos usando la misma fecha se
contaminan entre si (un job de este archivo aparecia como 'boda ya
agendada' en el test de leads del otro archivo). Cada fecha se deriva del
suffix unico de cada test para que nunca choque con nada mas."""
import uuid
from datetime import date, timedelta


def _unique_date(suffix):
    return (date(2030, 1, 1) + timedelta(days=int(suffix, 16) % 3000)).isoformat()


def _make_job(app_module, suffix, fecha, status='Confirmado'):
    client_id = f'client-jobconf-{suffix}'
    job_id = f'job-jobconf-{suffix}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Job', 'last_name': 'Test',
        'email': f'{suffix}@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': f'Boda Job Test {suffix}',
        'boda_date': fecha, 'status': status, 'tenant_id': 'tenant-norkevin',
    })
    return job_id


def _make_lead(app_module, suffix, fecha, status='Nuevo'):
    lead_id = f'lead-jobconf-{suffix}'
    app_module.store.upsert('leads', {
        'id': lead_id, 'nombre': f'Lead Job Test {suffix}', 'email': f'{suffix}@example.com',
        'fecha_tentativa': fecha, 'status': status, 'tenant_id': 'tenant-norkevin',
        'created': '2026-07-01',
    })
    return lead_id


def _row_html(html, job_id):
    """Aisla la fila <tr> de un job especifico dentro del HTML completo de
    /jobs, para no confundir el badge de este job con el de otro job
    acumulado en el store de tests de otra prueba."""
    marker = f"goJob(event, '{job_id}')"
    start = html.index(marker)
    end = html.index('</tr>', start)
    return html[start:end]


def test_two_jobs_same_date_get_red_conflict(auth_client):
    import app as app_module
    suffix = uuid.uuid4().hex[:6]
    fecha = _unique_date(suffix)
    job_a = _make_job(app_module, f'a-{suffix}', fecha)
    _make_job(app_module, f'b-{suffix}', fecha)

    resp = auth_client.get('/jobs')
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'date-status-badge red' in _row_html(html, job_a)


def test_job_alone_on_its_date_has_no_conflict_badge(auth_client):
    import app as app_module
    suffix = uuid.uuid4().hex[:6]
    job_id = _make_job(app_module, suffix, _unique_date(suffix))

    resp = auth_client.get('/jobs')
    html = resp.get_data(as_text=True)
    assert 'date-status-badge' not in _row_html(html, job_id)


def test_open_lead_on_same_date_as_job_gets_orange(auth_client):
    import app as app_module
    suffix = uuid.uuid4().hex[:6]
    fecha = _unique_date(suffix)
    job_id = _make_job(app_module, suffix, fecha)
    _make_lead(app_module, suffix, fecha)

    resp = auth_client.get('/jobs')
    html = resp.get_data(as_text=True)
    assert 'date-status-badge orange' in _row_html(html, job_id)


def test_red_wins_over_orange_when_both_apply(auth_client):
    import app as app_module
    suffix = uuid.uuid4().hex[:6]
    fecha = _unique_date(suffix)
    job_x = _make_job(app_module, f'x-{suffix}', fecha)
    _make_job(app_module, f'y-{suffix}', fecha)
    _make_lead(app_module, suffix, fecha)

    resp = auth_client.get('/jobs')
    row = _row_html(resp.get_data(as_text=True), job_x)
    assert 'date-status-badge red' in row
    assert 'date-status-badge orange' not in row


def test_archived_job_does_not_count_as_a_conflict(auth_client):
    import app as app_module
    suffix = uuid.uuid4().hex[:6]
    fecha = _unique_date(suffix)
    job_live = _make_job(app_module, f'live-{suffix}', fecha)
    _make_job(app_module, f'dead-{suffix}', fecha, status='Archivado')

    resp = auth_client.get('/jobs')
    row = _row_html(resp.get_data(as_text=True), job_live)
    assert 'date-status-badge red' not in row


def test_converted_or_lost_leads_do_not_trigger_orange(auth_client):
    import app as app_module
    suffix = uuid.uuid4().hex[:6]
    fecha = _unique_date(suffix)
    job_id = _make_job(app_module, suffix, fecha)
    _make_lead(app_module, f'conv-{suffix}', fecha, status='Convertido')
    _make_lead(app_module, f'lost-{suffix}', fecha, status='Perdido')

    resp = auth_client.get('/jobs')
    row = _row_html(resp.get_data(as_text=True), job_id)
    assert 'date-status-badge orange' not in row
