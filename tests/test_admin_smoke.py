"""Barrida rapida de las paginas principales del panel de administracion
(logueado). No prueba cada detalle, pero detecta si algo quedo roto
(errores 500, excepciones de template) despues de un cambio grande."""
import pytest
import uuid

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
    if not leads:
        app_module.upsert_lead({
            'id': 'lead-smoke-' + uuid.uuid4().hex[:8],
            'nombre': 'Lead Smoke',
            'email': 'lead-smoke@example.com',
            'status': 'Nuevo',
            'tenant_id': 'tenant-norkevin',
        })
        leads = app_module.store.list('leads')
    # Un lead ya convertido redirige a su job (comportamiento correcto), asi
    # que seguimos el redirect si aplica en vez de exigir 200 directo.
    resp = auth_client.get(f'/leads/{leads[0]["id"]}', follow_redirects=True)
    assert resp.status_code == 200


def test_job_detail_loads_for_real_job(auth_client):
    import app as app_module
    jobs = app_module.store.list('jobs')
    if not jobs:
        client_id = 'client-smoke-' + uuid.uuid4().hex[:8]
        app_module.store.upsert('clients', {
            'id': client_id,
            'first_name': 'Client',
            'last_name': 'Smoke',
            'email': 'client-smoke@example.com',
            'tenant_id': 'tenant-norkevin',
        })
        app_module.upsert_job({
            'id': 'job-smoke-' + uuid.uuid4().hex[:8],
            'nombre': 'Job Smoke',
            'client_id': client_id,
            'status': 'Confirmado',
            'tenant_id': 'tenant-norkevin',
        })
        jobs = app_module.store.list('jobs')
    resp = auth_client.get(f'/jobs/{jobs[0]["id"]}')
    assert resp.status_code == 200


def test_client_detail_loads_for_real_client(auth_client):
    import app as app_module
    clients = app_module.store.list('clients')
    if not clients:
        app_module.store.upsert('clients', {
            'id': 'client-smoke-' + uuid.uuid4().hex[:8],
            'first_name': 'Client',
            'last_name': 'Smoke',
            'email': 'client-smoke@example.com',
            'tenant_id': 'tenant-norkevin',
        })
        clients = app_module.store.list('clients')
    resp = auth_client.get(f'/clients/{clients[0]["id"]}')
    assert resp.status_code == 200
