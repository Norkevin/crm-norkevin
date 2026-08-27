"""Tests del hardening de /api/admin/reset-test-data (bloque de cierre de
brechas, agosto 2026, prioridad 6).

Antes: bastaba con estar logueado (NIVEL_EMPRESA) y mandar
{'confirm': 'BORRAR'} -- un string generico, reproducible por accidente
(un script de pruebas, un curl copiado sin pensar), sin nada que lo
desactive en produccion.

Ahora se exige:
  1. ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS=1 en el entorno (ausente por
     defecto -- pensado para nunca estar presente en Render/produccion
     real salvo mantenimiento explicito).
  2. confirm == 'BORRAR-<tenant_id de la sesion activa>' -- ya no sirve
     el mismo string para cualquier cuenta.
  3. Backup VERIFICADO (store.backup_now()) de cada tabla ANTES de vaciar
     ninguna -- si alguno falla, se aborta sin haber tocado nada.

No se corren en este sandbox (falta pytest/Flask) -- ver
STABILIZATION_EXECUTION_REPORT.md, seccion BLOCKED_BY_MISSING_DEPENDENCY.
"""
import os

import pytest

ASTRAL = 'tenant-norkevin'


def _seed_some_data(app_module, tenant_id):
    app_module.store.upsert('leads', {
        'id': f'lead-hardening-{tenant_id}', 'tenant_id': tenant_id, 'nombre': 'Test',
    })
    app_module.store.upsert('jobs', {
        'id': f'job-hardening-{tenant_id}', 'tenant_id': tenant_id, 'nombre': 'Test Job',
    })


def _contar(app_module, tabla, tenant_id):
    """Cuenta SIEMPRE con el mismo alcance (el de `tenant_id`), sin importar
    si hay o no un request context activo en ese momento.

    Bug encontrado en la validacion real en Windows (agosto 2026): estos
    tests comparaban len(store.list(...)) antes y despues de la peticion.
    Parece simetrico y no lo es. El fixture `client` de conftest.py usa
    `with flask_app.test_client() as c`, y Flask PRESERVA el request
    context despues de la peticion (asi se puede inspeccionar la sesion).
    Resultado: la lectura de ANTES ocurre sin contexto -> sin aislamiento
    -> devuelve los registros de TODAS las cuentas; la de DESPUES ocurre
    con el contexto preservado -> aislada -> solo los de la cuenta activa.
    En la fase aislada de estos tests el store solo tenia datos de una
    cuenta y los dos numeros coincidian, asi que pasaban 6/6; en la suite
    completa, con datos de varias cuentas sembrados por otros tests,
    aparecia una diferencia (44 vs 43) que parecia un borrado y no lo era.
    Contando siempre dentro de un contexto explicito, la comparacion mide
    lo mismo en los dos extremos."""
    with app_module.app.test_request_context('/'):
        from flask import session
        session['tenant_id'] = tenant_id
        return len(app_module.store.list(tabla))


def test_bloqueado_sin_flag_de_entorno(auth_client, monkeypatch):
    """Sin ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS=1, la ruta responde 403 y NO
    borra nada, incluso con la confirmacion correcta."""
    import app as app_module

    monkeypatch.delenv('ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS', raising=False)
    _seed_some_data(app_module, ASTRAL)
    antes = _contar(app_module, 'leads', ASTRAL)

    resp = auth_client.post('/api/admin/reset-test-data',
                             json={'confirm': f'BORRAR-{ASTRAL}'})
    assert resp.status_code == 403
    assert resp.get_json()['ok'] is False
    assert _contar(app_module, 'leads', ASTRAL) == antes, \
        'no debe haber borrado nada con la flag ausente'


def test_bloqueado_con_flag_en_false(auth_client, monkeypatch):
    monkeypatch.setenv('ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS', '0')
    import app as app_module
    _seed_some_data(app_module, ASTRAL)
    antes = _contar(app_module, 'leads', ASTRAL)

    resp = auth_client.post('/api/admin/reset-test-data',
                             json={'confirm': f'BORRAR-{ASTRAL}'})
    assert resp.status_code == 403
    assert _contar(app_module, 'leads', ASTRAL) == antes


def test_confirmacion_generica_ya_no_alcanza(auth_client, monkeypatch):
    """El viejo 'BORRAR' (sin el tenant_id) ya no debe funcionar, aunque la
    flag este activa -- si funcionara, cualquier script viejo con el string
    hardcodeado seguiria pudiendo borrar produccion."""
    monkeypatch.setenv('ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS', '1')
    import app as app_module
    _seed_some_data(app_module, ASTRAL)
    antes = _contar(app_module, 'leads', ASTRAL)

    resp = auth_client.post('/api/admin/reset-test-data', json={'confirm': 'BORRAR'})
    assert resp.status_code == 400
    assert _contar(app_module, 'leads', ASTRAL) == antes


def test_confirmacion_de_otra_cuenta_no_sirve(auth_client, monkeypatch):
    """confirm='BORRAR-<tenant de otra cuenta>' no debe autorizar el borrado
    de la cuenta activa -- la confirmacion debe atarse a LA SESION, no a
    cualquier tenant_id que el llamador decida escribir."""
    monkeypatch.setenv('ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS', '1')
    import app as app_module
    _seed_some_data(app_module, ASTRAL)
    antes = _contar(app_module, 'leads', ASTRAL)

    resp = auth_client.post('/api/admin/reset-test-data',
                             json={'confirm': 'BORRAR-tenant-norkevin-photography'})
    assert resp.status_code == 400
    assert _contar(app_module, 'leads', ASTRAL) == antes


def test_flag_y_confirmacion_correcta_si_borra_y_deja_backup(auth_client, monkeypatch):
    monkeypatch.setenv('ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS', '1')
    import app as app_module
    _seed_some_data(app_module, ASTRAL)

    resp = auth_client.post('/api/admin/reset-test-data',
                             json={'confirm': f'BORRAR-{ASTRAL}'})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True
    assert body['wiped'].get('leads', 0) >= 1

    with app_module.app.test_request_context('/'):
        from flask import session
        session['tenant_id'] = ASTRAL
        assert app_module.store.list('leads') == []


def test_backup_fallido_aborta_sin_borrar_nada(auth_client, monkeypatch):
    """Si store.backup_now() falla para CUALQUIER tabla, no debe haberse
    vaciado NINGUNA -- ni las tablas anteriores en el loop ni las
    posteriores. Se simula el fallo monkeypracheando backup_now()."""
    monkeypatch.setenv('ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS', '1')
    import app as app_module
    _seed_some_data(app_module, ASTRAL)
    antes_leads = _contar(app_module, 'leads', ASTRAL)
    antes_jobs = _contar(app_module, 'jobs', ASTRAL)

    def _backup_que_falla(table):
        if table == 'quotes':  # falla a la mitad de la lista de tablas
            raise RuntimeError('backup simulado fallido')
        return None

    monkeypatch.setattr(app_module.store, 'backup_now', _backup_que_falla)

    resp = auth_client.post('/api/admin/reset-test-data',
                             json={'confirm': f'BORRAR-{ASTRAL}'})
    assert resp.status_code == 500
    body = resp.get_json()
    assert body.get('tables_wiped', 0) == 0
    assert _contar(app_module, 'leads', ASTRAL) == antes_leads, \
        'una tabla ANTERIOR a la que fallo tampoco debe haberse vaciado'
    assert _contar(app_module, 'jobs', ASTRAL) == antes_jobs
