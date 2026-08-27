"""Uso diario: workflows y calendario no cruzan marcas.

El WorkflowEngine guarda TODAS las instancias en un solo diccionario en
memoria y en un `workflow_instances.json` global, sin sufijo de cuenta.
La nota de `_persist_workflow_template` dice que el avance de cada job SI
esta aislado "ligado al job que ya paso por el filtro de tenant al
buscarlo" -- y eso es cierto cuando se llega por el job.

Pero habia tres puertas que NO pasaban por ningun job y listaban el
diccionario entero:

  - `/api/workflow/instances` (y su historial)
  - la actividad reciente del dashboard
  - el contador de instancias activas en Settings

Por ahi Astral veia los nombres de las bodas de Norkevin. Este archivo
cierra esas tres puertas y las deja probadas en las DOS marcas.
"""
import pytest

from conftest import login_as_tenant

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'
AMBAS = [ASTRAL, NORKEVIN]

# 'tenant-norkevin' es prefijo de 'tenant-norkevin-photography': un id de
# prueba terminado en el tenant_id hace que `assert ajeno not in <html>` de
# un falso positivo de fuga entre marcas. Aca todavia no mordia porque las
# comparaciones son por nombre, pero es una bomba de tiempo. Token corto,
# sin colision (misma solucion que en test_uso_diario_clientes.py).
TOKEN = {ASTRAL: 'astral', NORKEVIN: 'norkph'}


def _tk(tenant_id):
    return TOKEN.get(tenant_id, tenant_id.replace('tenant-', ''))


def _job_con_workflow(app_module, tenant_id, sufijo, nombre):
    job = {'id': f'job-wf-{sufijo}-{_tk(tenant_id)}', 'tenant_id': tenant_id,
           'nombre': nombre, 'status': 'Confirmado',
           'boda_date': '2026-12-05'}
    app_module.store.upsert('jobs', job)
    instancia = app_module.workflow_engine.start_workflow(
        workflow=app_module.PRODUCTION_WORKFLOW(),
        subject_type='job',
        subject_id=job['id'],
        subject_name=nombre,
        trigger_event='test.setup',
    )
    return job, instancia


# ============================================================
# Aislamiento de instancias entre marcas
# ============================================================

def test_cada_marca_solo_ve_sus_propias_instancias(auth_client):
    import app as app_module

    creados = {}
    for tenant_id, nombre in ((ASTRAL, 'Boda Secreta de Astral'),
                              (NORKEVIN, 'Boda Secreta de Norkevin')):
        login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
        job, inst = _job_con_workflow(app_module, tenant_id, 'aislada', nombre)
        creados[tenant_id] = (job, inst, nombre)

    for tenant_id in AMBAS:
        otro = NORKEVIN if tenant_id == ASTRAL else ASTRAL
        login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

        resp = auth_client.get('/api/workflow/instances')
        assert resp.status_code == 200
        data = resp.get_json()

        ids = {i['id'] for i in data['instances']}
        assert creados[tenant_id][1].id in ids, 'no ve su propia instancia'
        assert creados[otro][1].id not in ids, 've la instancia de la otra marca'

        cuerpo = resp.get_data(as_text=True)
        assert creados[otro][2] not in cuerpo, \
            'el nombre de una boda de la otra marca viajo en la respuesta'

        # Las stats tienen que contar lo mismo que se devolvio.
        assert data['stats']['total_instances'] == len(data['instances'])


def test_el_detalle_de_una_instancia_ajena_da_404(auth_client):
    """Y da el mismo 404 que si no existiera: responder distinto le
    confirmaria a una marca que la otra tiene esa instancia."""
    import app as app_module

    login_as_tenant(auth_client, NORKEVIN, email=f'{NORKEVIN}@example.invalid')
    _job, inst_ajena = _job_con_workflow(app_module, NORKEVIN, 'ajena', 'Boda Ajena')

    login_as_tenant(auth_client, ASTRAL, email=f'{ASTRAL}@example.invalid')
    ajena = auth_client.get(f'/api/workflow/instances/{inst_ajena.id}')
    inexistente = auth_client.get('/api/workflow/instances/no-existe-jamas')

    assert ajena.status_code == 404
    assert inexistente.status_code == 404
    assert ajena.get_json() == inexistente.get_json()


def test_el_historial_no_muestra_movimientos_de_la_otra_marca(auth_client):
    import app as app_module

    login_as_tenant(auth_client, NORKEVIN, email=f'{NORKEVIN}@example.invalid')
    _job, inst_ajena = _job_con_workflow(app_module, NORKEVIN, 'hist', 'Boda Historial')

    login_as_tenant(auth_client, ASTRAL, email=f'{ASTRAL}@example.invalid')
    resp = auth_client.get('/api/workflow/history')
    assert resp.status_code == 200
    ids = {h.get('instance_id') for h in resp.get_json()['history']}
    assert inst_ajena.id not in ids


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_las_instancias_huerfanas_no_aparecen(auth_client, tenant_id):
    """Los datos demo dejaron 143 instancias apuntando a jobs que ya no
    existen. Una instancia sin job no es accionable por nadie."""
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    huerfana = app_module.workflow_engine.start_workflow(
        workflow=app_module.PRODUCTION_WORKFLOW(),
        subject_type='job',
        subject_id=f'job-que-no-existe-{_tk(tenant_id)}',
        subject_name='Job Borrado',
        trigger_event='test.setup',
    )

    resp = auth_client.get('/api/workflow/instances')
    ids = {i['id'] for i in resp.get_json()['instances']}
    assert huerfana.id not in ids


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_settings_cuenta_solo_las_instancias_propias(auth_client, tenant_id):
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
    _job_con_workflow(app_module, tenant_id, 'settings', 'Boda Settings')

    resp = auth_client.get('/settings')
    assert resp.status_code == 200

    propias = app_module._workflow_instances_del_tenant()
    todas = app_module.workflow_engine.list_instances()
    assert len(propias) <= len(todas)
    for inst in propias:
        assert app_module._instancia_es_de_la_cuenta(inst)


# ============================================================
# Un solo workflow canonico por job
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_no_se_duplica_el_workflow_al_reprocesar_el_job(auth_client, tenant_id):
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    lead = {'id': f'lead-wf-{_tk(tenant_id)}', 'tenant_id': tenant_id, 'nombre': 'Lead WF'}
    app_module.store.upsert('leads', lead)
    job = {'id': f'job-unico-{_tk(tenant_id)}', 'tenant_id': tenant_id,
           'nombre': 'Boda Unica', 'lead_id': lead['id']}
    app_module.store.upsert('jobs', job)

    primero, creado1 = app_module._ensure_production_workflow_for_job(lead, job)
    segundo, creado2 = app_module._ensure_production_workflow_for_job(lead, job)

    assert primero == segundo, 'se creo un segundo workflow para el mismo job'
    assert creado1 is True and creado2 is False

    instancias = app_module.workflow_engine.list_instances(
        subject_id=job['id'], subject_type='job')
    assert len(instancias) == 1


# ============================================================
# Calendario
# ============================================================

def test_el_calendario_no_muestra_bodas_de_la_otra_marca(auth_client):
    import app as app_module

    for tenant_id, nombre in ((ASTRAL, 'Boda Calendario Astral'),
                              (NORKEVIN, 'Boda Calendario Norkevin')):
        login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
        app_module.store.upsert('jobs', {
            'id': f'job-cal-{_tk(tenant_id)}', 'tenant_id': tenant_id,
            'nombre': nombre, 'status': 'Confirmado', 'boda_date': '2026-12-05',
        })

    for tenant_id, propio, ajeno in (
        (ASTRAL, 'Boda Calendario Astral', 'Boda Calendario Norkevin'),
        (NORKEVIN, 'Boda Calendario Norkevin', 'Boda Calendario Astral'),
    ):
        login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
        resp = auth_client.get('/calendar?month=2026-12')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert propio in html, 'no aparece la boda propia'
        assert ajeno not in html, 'aparece una boda de la otra marca'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_un_job_sin_fecha_no_rompe_el_calendario(auth_client, tenant_id):
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
    app_module.store.upsert('jobs', {
        'id': f'job-sinfecha-{_tk(tenant_id)}', 'tenant_id': tenant_id,
        'nombre': 'Boda Sin Fecha', 'status': 'Confirmado', 'boda_date': None,
    })
    assert auth_client.get('/calendar').status_code == 200
