"""Kevin: 'sigue sin funcionar' -- el Dashboard mostraba 'Sin sesiones
proximas' aunque tenia bodas reales agendadas, porque el widget solo
miraba los proximos 60 dias. Sus bodas reales (importadas de Studio Ninja
o cargadas a mano) suelen estar meses o mas de un año adelante -- el
widget debe mostrar cualquier boda futura, no solo las de corto plazo."""
from datetime import date, timedelta
import uuid


def _make_job(app_module, suffix, boda_date, status='Confirmado'):
    client_id = f'client-updash-{suffix}'
    job_id = f'job-updash-{suffix}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Dash', 'last_name': 'Test',
        'email': 'dashtest@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': f'Boda Dash Test {suffix}',
        'boda_date': boda_date, 'status': status, 'tenant_id': 'tenant-norkevin',
    })
    return job_id


def test_job_far_in_the_future_shows_up_in_upcoming_sessions(auth_client):
    import app as app_module
    far_future = (date.today() + timedelta(days=400)).isoformat()
    job_id = _make_job(app_module, uuid.uuid4().hex[:6], far_future)

    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert f'/jobs/{job_id}' in html, 'una boda real a mas de 60 dias debe seguir apareciendo en Upcoming Sessions'


def test_job_in_the_past_does_not_show_up(auth_client):
    import app as app_module
    past = (date.today() - timedelta(days=10)).isoformat()
    job_id = _make_job(app_module, uuid.uuid4().hex[:6], past)

    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert f'/jobs/{job_id}' not in html


def test_archived_job_does_not_show_up_even_if_future(auth_client):
    import app as app_module
    future = (date.today() + timedelta(days=30)).isoformat()
    job_id = _make_job(app_module, uuid.uuid4().hex[:6], future, status='Archivado')

    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert f'/jobs/{job_id}' not in html


def test_nearest_job_sorts_first(auth_client):
    import app as app_module
    suffix = uuid.uuid4().hex[:6]
    near = (date.today() + timedelta(days=5)).isoformat()
    far = (date.today() + timedelta(days=300)).isoformat()
    near_id = _make_job(app_module, f'near-{suffix}', near)
    far_id = _make_job(app_module, f'far-{suffix}', far)

    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert html.index(f'/jobs/{near_id}') < html.index(f'/jobs/{far_id}')
