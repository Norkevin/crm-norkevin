"""Punto 17 de Kevin: cross-tenant como excepcion deliberada, y tokens
publicos guardados como hash.

Los dos son "preparado, no activado" en el sentido de que no rotan ni migran
nada; lo que se fija aca es la arquitectura y sus garantias.
"""
import uuid

import pytest

from src import public_tokens as pt
from src.storage import TenantMismatchError

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'


def _seed(app_module, tabla, tenant_id, **campos):
    record = {'id': f'{tabla[:4]}-{uuid.uuid4().hex[:8]}', 'tenant_id': tenant_id}
    record.update(campos)
    app_module.store.upsert(tabla, record)
    return record


# ------------------------------------------ scope all_tenants (punto 17a)

def test_omitir_la_empresa_no_da_acceso_a_todas(client):
    """Kevin: tenant_id=None NO puede significar 'todos los tenants'."""
    import app as app_module

    with pytest.raises(ValueError) as e:
        app_module.store.list_privileged('clients', reason='prueba')
    assert 'all_tenants' in str(e.value)


def test_all_tenants_se_rechaza_desde_una_ruta_normal(client):
    """Aunque el codigo lo pida, una peticion que no es administrativa no
    puede mirar las dos empresas."""
    import app as app_module

    ctx = app_module.app.test_request_context('/')
    ctx.push()
    try:
        from flask import session
        session['tenant_id'] = ASTRAL  # usuario normal, con sesion
        with pytest.raises(TenantMismatchError) as e:
            app_module.store.list_privileged(
                'clients', scope='all_tenants', reason='intento desde ruta normal')
    finally:
        ctx.pop()
    assert 'administrativa' in str(e.value)


def test_all_tenants_funciona_desde_una_ruta_administrativa(client):
    """Caso POSITIVO: los reportes de admin si necesitan cruzar empresas."""
    import app as app_module

    _seed(app_module, 'clients', ASTRAL, first_name='A')
    _seed(app_module, 'clients', NORKEVIN, first_name='N')

    resp = client.get('/api/admin/tenant-inventory?token='
                      + app_module._ADMIN_ONE_TIME_TOKEN)
    assert resp.status_code == 200
    totales = resp.get_json()['totales']['clients']
    assert ASTRAL in totales and NORKEVIN in totales, \
        'un reporte administrativo si debe poder contar las dos empresas'


def test_fuera_de_una_peticion_all_tenants_sigue_disponible(client):
    """Scripts de migracion y tests corren fuera de request y necesitan el
    archivo completo; ahi no hay una ruta web que proteger."""
    import app as app_module

    registros = app_module.store.list_privileged(
        'clients', scope='all_tenants', reason='script de migracion')
    assert isinstance(registros, list)


# ----------------------------------------- tokens con hash (punto 17b)

def test_el_token_generado_es_largo_y_aleatorio():
    a, b = pt.generar_token(), pt.generar_token()
    assert a != b
    assert len(a) >= 40, 'un token corto se puede fuerza-brutear'


def test_lo_que_se_guarda_no_sirve_para_abrir_el_enlace():
    """El punto central: leer la base no debe entregar enlaces usables."""
    token, record = pt.emitir_para({'id': 'contract-x'})

    assert 'public_token_hash' in record
    assert record['public_token_hash'] != token
    assert token not in str(record), \
        'el token en claro no puede quedar guardado en ningun campo'
    # Y desde el hash no se puede reconstruir el token.
    assert pt.hash_token(record['public_token_hash']) != record['public_token_hash']


def test_el_token_correcto_abre_y_uno_parecido_no():
    token, record = pt.emitir_para({'id': 'contract-x'})
    h = record['public_token_hash']

    assert pt.token_coincide(token, h) is True
    assert pt.token_coincide(token[:-1] + 'x', h) is False
    assert pt.token_coincide('', h) is False
    assert pt.token_coincide(token, None) is False


def test_buscar_por_token_encuentra_solo_el_correcto():
    t1, r1 = pt.emitir_para({'id': 'contract-1'})
    t2, r2 = pt.emitir_para({'id': 'contract-2'})
    registros = [r1, r2]

    assert pt.buscar_por_token(registros, t1)['id'] == 'contract-1'
    assert pt.buscar_por_token(registros, t2)['id'] == 'contract-2'
    assert pt.buscar_por_token(registros, 'token-inventado') is None
    assert pt.buscar_por_token(registros, '') is None


def test_un_registro_sin_token_no_se_abre_con_nada():
    """Un registro viejo sin public_token_hash no debe resolver por accidente."""
    assert pt.buscar_por_token([{'id': 'viejo'}], 'lo-que-sea') is None
    assert pt.buscar_por_token([{'id': 'viejo', 'public_token_hash': None}], 'x') is None


def test_la_huella_nunca_muestra_el_token_completo():
    """Kevin: los tokens no pueden aparecer completos en logs ni pantallas."""
    token = pt.generar_token()
    h = pt.huella(token)

    assert token not in h
    assert len(h) < len(token)
    assert '•' in h
    # Deja ver lo justo para identificarlo sin poder reutilizarlo.
    assert h.startswith(token[:4])


def test_emitir_dos_veces_da_tokens_distintos():
    """Rotar debe invalidar el anterior."""
    t1, r1 = pt.emitir_para({'id': 'contract-x'})
    t2, r2 = pt.emitir_para(r1)

    assert t1 != t2
    assert r2['public_token_hash'] != r1['public_token_hash']
    assert pt.token_coincide(t1, r2['public_token_hash']) is False, \
        'el token viejo debe dejar de funcionar tras rotar'


# ------------------------------ regresion: fail-closed en rutas admin

def test_el_inventario_de_admin_no_sale_vacio(client):
    """Kevin: "no devuelvas [] silenciosamente".

    Con el aislamiento cerrado, un `store.list()` dentro de una ruta admin
    (que no tiene cuenta activa) devuelve [] y el reporte sale vacio sin
    avisar de nada. Eso convierte una proteccion en un reporte que miente.
    """
    import app as app_module

    _seed(app_module, 'jobs', ASTRAL, nombre='Boda de Astral')
    _seed(app_module, 'jobs', NORKEVIN, nombre='Sesion de Norkevin')

    datos = client.get('/api/admin/tenant-inventory?token='
                       + app_module._ADMIN_ONE_TIME_TOKEN).get_json()

    assert datos['totales']['jobs'].get(ASTRAL), 'faltan los jobs de una empresa'
    assert datos['totales']['jobs'].get(NORKEVIN), 'faltan los jobs de la otra'


def test_el_import_de_leads_no_pisa_un_lead_existente(client):
    """El mismo fallo por otro lado: si el chequeo de duplicados lee [], el
    import "idempotente" sobreescribe leads que ya existen."""
    import app as app_module

    lead = {'id': 'lead-repetido', 'tenant_id': ASTRAL,
            'first_name': 'Editado', 'email': 'repetido@ejemplo.com'}
    app_module.store.upsert('leads', lead)

    resp = client.post('/api/admin/import-astral-leads?token='
                       + app_module._ADMIN_ONE_TIME_TOKEN,
                       json={'confirm': 'IMPORTAR', 'tenant_id': ASTRAL,
                             'leads': [{'id': 'lead-repetido',
                                        'first_name': 'Del import',
                                        'email': 'repetido@ejemplo.com'}]})
    assert resp.status_code == 200
    assert resp.get_json()['creados'] == []

    guardado = next(l for l in app_module.store.list_privileged(
        'leads', tenant_id=ASTRAL, reason='verificacion de test')
        if l['id'] == 'lead-repetido')
    assert guardado['first_name'] == 'Editado', \
        'el import no debe pisar lo que Kevin ya tenia'


# --------------------------- las rutas admin no cuelgan de "estar logueado"

def test_estar_logueado_no_abre_las_rutas_de_admin(auth_client):
    """Estas rutas cruzan las dos empresas. Una sesion normal valida --
    cualquiera de los dos negocios -- no debe poder abrirlas."""
    import app as app_module

    for ruta in app_module._ADMIN_PATHS:
        resp = auth_client.get(ruta)
        assert resp.status_code == 404, \
            f'{ruta} respondio {resp.status_code} a un usuario normal logueado'
        # 404 y no 403: no confirmamos siquiera que la ruta exista.
        assert 'Not found' in resp.get_data(as_text=True)


def test_la_migracion_no_corre_con_solo_estar_logueado(auth_client):
    """La mas peligrosa de todas: reescribe tenant_id en las dos empresas."""
    resp = auth_client.post('/api/admin/migrate-to-multi-tenant',
                            json={'confirm': 'MIGRAR', 'dry_run': False})
    assert resp.status_code == 404
