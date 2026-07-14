"""Barrida rapida de las paginas principales del panel de administracion
(logueado). No prueba cada detalle, pero detecta si algo quedo roto
(errores 500, excepciones de template) despues de un cambio grande."""
import pytest

ADMIN_PAGES = [
    '/dashboard',
    '/clients',
    '/leads',
    '/jobs',
    '/payments',
    '/calendar',
    '/settings',
    '/settings/email-templates',
    '/settings/packages',
    '/workflow-editor',
]


@pytest.mark.parametrize('path', ADMIN_PAGES)
def test_admin_page_loads_without_error(auth_client, path):
    resp = auth_client.get(path)
    assert resp.status_code == 200, f'{path} deberia responder 200, dio {resp.status_code}'


def test_lead_detail_loads_for_real_lead(auth_client):
    import app as app_module
    leads = app_module.store.list('leads')
    assert leads, 'necesita al menos un lead en los datos de prueba'
    # Un lead ya convertido redirige a su job (comportamiento correcto), asi
    # que seguimos el redirect si aplica en vez de exigir 200 directo.
    resp = auth_client.get(f'/leads/{leads[0]["id"]}', follow_redirects=True)
    assert resp.status_code == 200


def test_job_detail_loads_for_real_job(auth_client):
    import app as app_module
    jobs = app_module.store.list('jobs')
    assert jobs, 'necesita al menos un job en los datos de prueba'
    resp = auth_client.get(f'/jobs/{jobs[0]["id"]}')
    assert resp.status_code == 200


def test_client_detail_loads_for_real_client(auth_client):
    import app as app_module
    clients = app_module.store.list('clients')
    assert clients, 'necesita al menos un client en los datos de prueba'
    resp = auth_client.get(f'/clients/{clients[0]["id"]}')
    assert resp.status_code == 200
