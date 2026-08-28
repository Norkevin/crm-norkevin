"""Kevin: 'borra todos los datos para seguir haciendo pruebas, prefiero
que este vacio'. /api/admin/reset-test-data vacia leads/clientes/jobs/
cotizaciones/pagos/contratos/cuestionarios/archivos/correos/calendario,
pero NUNCA debe tocar configuracion (plantillas de correo, paquetes,
equipo) -- eso tomo tiempo configurar y no es "dato de prueba".

ACTUALIZADO por el hardening de prioridad 6 (agosto 2026). El contrato de
la ruta cambio y estos tests se ajustaron para reflejarlo, SIN perder lo
que verificaban:
  - hace falta ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS=1 en el entorno (por eso
    el fixture `_permitir_reset` de abajo, que la activa solo dentro de
    estos tests);
  - la confirmacion ya no es el string generico 'BORRAR' sino
    'BORRAR-<tenant_id de la sesion>'.
El rechazo del string viejo tiene su propio test en
tests/test_reset_endpoint_hardening.py::test_confirmacion_generica_ya_no_alcanza.
"""
import uuid

import pytest

ASTRAL = 'tenant-norkevin'
CONFIRM_OK = f'BORRAR-{ASTRAL}'


@pytest.fixture(autouse=True)
def _permitir_reset(monkeypatch):
    """Estos tests existen justamente para ejercitar el borrado real, asi
    que activan la flag destructiva SOLO para si mismos y solo contra el
    CRM_DATA_DIR aislado de conftest.py -- nunca contra data/ real."""
    monkeypatch.setenv('ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS', '1')


def _seed_business_data(app_module):
    suffix = uuid.uuid4().hex[:6]
    app_module.store.upsert('leads', {'id': f'lead-reset-{suffix}', 'nombre': 'Reset Test', 'tenant_id': 'tenant-norkevin'})
    app_module.store.upsert('clients', {'id': f'client-reset-{suffix}', 'first_name': 'Reset', 'tenant_id': 'tenant-norkevin'})
    app_module.store.upsert('jobs', {'id': f'job-reset-{suffix}', 'nombre': 'Reset Job', 'tenant_id': 'tenant-norkevin'})
    app_module.store.upsert('payments', {'id': f'pay-reset-{suffix}', 'amount': 100, 'tenant_id': 'tenant-norkevin'})
    # STAGE 2 (agosto 2026): un correo esperando aprobacion tambien es dato
    # de prueba -- si el reset no lo vacia, sobrevive apuntando a un lead
    # que ya no existe.
    app_module.store.upsert('pending_emails', {
        'id': f'pend-reset-{suffix}', 'tenant_id': 'tenant-norkevin',
        'to': 'reset-test@example.com', 'subject': 'Pendiente de prueba',
        'status': 'pending',
    })
    return suffix


def test_reset_requires_typed_confirmation(auth_client):
    import app as app_module
    _seed_business_data(app_module)
    before = auth_client.get('/api/storage/status').get_json()['counts']

    resp = auth_client.post('/api/admin/reset-test-data', json={})
    assert resp.status_code == 400
    assert resp.get_json()['ok'] is False
    after = auth_client.get('/api/storage/status').get_json()['counts']
    assert after == before, 'sin confirmacion no debe borrar nada'

    resp = auth_client.post('/api/admin/reset-test-data', json={'confirm': 'borrar'})
    assert resp.status_code == 400, 'debe ser exactamente la confirmacion de la cuenta'

    resp = auth_client.post('/api/admin/reset-test-data', json={'confirm': 'BORRAR'})
    assert resp.status_code == 400, 'el string generico viejo ya no alcanza'

    after = auth_client.get('/api/storage/status').get_json()['counts']
    assert after == before, 'ninguna confirmacion invalida debe haber borrado nada'


def test_reset_wipes_business_tables_but_keeps_config(auth_client):
    import app as app_module
    from flask import session as _sess
    _seed_business_data(app_module)

    # Ambas mediciones (antes/despues) deben resolver el mismo tenant activo
    # de forma explicita -- una llamada "pelada" a store.list() ve la sesion
    # de la ULTIMA request real del test client, asi que medir "antes" sin
    # haber hecho ninguna request todavia (sin filtrar) y "despues" de un
    # POST autenticado (filtrado a tenant-norkevin) compara conjuntos
    # distintos por diseño, no por perdida de datos real. Con datos locales
    # de mas de un tenant en email_templates/packages (de una migracion real
    # corrida antes en esta sesion), esa inconsistencia se nota.
    def _snapshot(table):
        with app_module.app.test_request_context():
            _sess['tenant_id'] = 'tenant-norkevin'
            return list(app_module.store.list(table))

    templates_before = _snapshot('email_templates')
    packages_before = _snapshot('packages')
    team_before = _snapshot('team')
    assert templates_before, 'el entorno de pruebas ya deberia tener plantillas sembradas'

    resp = auth_client.post('/api/admin/reset-test-data', json={'confirm': CONFIRM_OK})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True

    for table in ('leads', 'clients', 'jobs', 'quotes', 'payments', 'contracts',
                  'questionnaires', 'files', 'mail_log', 'mail_outbox', 'calendar',
                  'pending_emails'):
        assert app_module.store.list(table) == [], f'{table} deberia quedar vacio'

    assert _snapshot('email_templates') == templates_before
    assert _snapshot('packages') == packages_before
    assert _snapshot('team') == team_before


def test_reset_clears_workflow_engine_instances(auth_client):
    """Ademas de comprobar que la instancia de la cuenta reseteada
    desaparece, esto es el regression test del bug real encontrado el
    27-ago-2026: workflow_engine.instances/history son un diccionario y una
    lista GLOBALES de todo el proceso (no pasan por store.clear() ni por
    tenant_id), asi que un 'workflow_engine.instances = {}' liso y llano en
    el reset de UNA cuenta borraba tambien el progreso de las OTRAS -- la
    aseveracion vieja (`workflow_engine.instances == {}`) solo pasaba
    porque el bug vaciaba todo sin importar la cuenta; con el fix, debe
    seguir habiendo instancias de otras cuentas si las hay."""
    import app as app_module
    import uuid as _uuid

    lead_id = 'lead-wf-reset-' + _uuid.uuid4().hex[:6]
    app_module.upsert_lead({
        'id': lead_id, 'nombre': 'WF Reset', 'email': 'wfreset@example.com',
        'status': 'Nuevo', 'tenant_id': 'tenant-norkevin',
    })
    app_module.trigger_workflow_for_lead(lead_id, 'WF Reset', tenant_id='tenant-norkevin')
    assert app_module.workflow_engine.list_instances(subject_id=lead_id, subject_type='lead')

    # Instancia de OTRA cuenta -- no debe tocarse al resetear tenant-norkevin.
    otro_tenant = 'tenant-norkevin-photography'
    otro_lead_id = 'lead-wf-otra-cuenta-' + _uuid.uuid4().hex[:6]
    app_module.upsert_lead({
        'id': otro_lead_id, 'nombre': 'WF Otra Cuenta', 'email': 'otra@example.com',
        'status': 'Nuevo', 'tenant_id': otro_tenant,
    })
    otra_instancia = app_module.trigger_workflow_for_lead(
        otro_lead_id, 'WF Otra Cuenta', tenant_id=otro_tenant)

    resp = auth_client.post('/api/admin/reset-test-data', json={'confirm': CONFIRM_OK})
    assert resp.status_code == 200
    assert resp.get_json()['workflow_instances_wiped'] >= 1

    assert not app_module.workflow_engine.list_instances(subject_id=lead_id, subject_type='lead'), \
        'la instancia de tenant-norkevin (la cuenta reseteada) debe desaparecer'
    assert app_module.workflow_engine.get_instance(otra_instancia.id) is not None, \
        'la instancia de la OTRA cuenta no debe borrarse por resetear tenant-norkevin'
