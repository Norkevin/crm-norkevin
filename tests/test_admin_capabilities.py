"""La frontera que Kevin quiere fijada (puntos 8 y 9).

    usuario autenticado  !=  operacion administrativa global autorizada

Son dos niveles distintos y no deben volver a mezclarse. Antes se mezclaban:
_require_login dejaba pasar cualquier /api/admin/* a cualquier sesion valida.
"""
import pytest

from conftest import login_as_tenant

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'


def _rutas_admin(app_module):
    """Todas las rutas /api/admin/ que existen DE VERDAD en el url_map.

    Se enumeran del mapa de Flask y no de una lista escrita a mano: el punto
    del test es cazar la ruta que alguien agregue manana.
    """
    return {str(r) for r in app_module.app.url_map.iter_rules()
            if str(r).startswith('/api/admin/')}


# ------------------------------------------------- nada se queda sin declarar

def test_toda_ruta_admin_declara_una_capacidad():
    """Kevin: que no dependa solo de que la URL empiece con /api/admin/.

    Si este test falla es porque alguien agrego una ruta administrativa y no
    dijo que hace. Declararla en _ADMIN_CAPABILITIES es la correccion; no
    ampliar la excepcion.
    """
    import app as app_module

    sin_declarar = _rutas_admin(app_module) - set(app_module._ADMIN_CAPABILITIES)
    assert not sin_declarar, (
        f'rutas administrativas sin capacidad declarada: {sorted(sin_declarar)}. '
        'Agregalas a _ADMIN_CAPABILITIES en app.py.')


def test_no_se_declaran_rutas_que_no_existen():
    """El simetrico: una entrada que ya no corresponde a ninguna ruta da la
    falsa impresion de estar protegiendo algo."""
    import app as app_module

    fantasmas = set(app_module._ADMIN_CAPABILITIES) - _rutas_admin(app_module)
    assert not fantasmas, f'capacidades declaradas sin ruta real: {sorted(fantasmas)}'


def test_las_rutas_con_token_salen_del_mapa_de_capacidades():
    """_ADMIN_PATHS se deriva de _ADMIN_CAPABILITIES: no se pueden
    desincronizar. Son exactamente las declaradas NIVEL_GLOBAL."""
    import app as app_module

    esperadas = {ruta for ruta, (_, nivel) in app_module._ADMIN_CAPABILITIES.items()
                 if nivel == app_module.NIVEL_GLOBAL}
    assert set(app_module._ADMIN_PATHS) == esperadas


def test_las_capacidades_son_las_acordadas():
    """Que no aparezcan categorias nuevas sin que Kevin lo sepa."""
    import app as app_module

    capacidades = {cap for cap, _ in app_module._ADMIN_CAPABILITIES.values()}
    niveles = {nivel for _, nivel in app_module._ADMIN_CAPABILITIES.values()}

    assert capacidades <= {
        'tenant_audit', 'incident_report', 'workflow_cleanup', 'migration',
        'data_import', 'data_reset',
    }
    assert niveles <= {'global', 'empresa'}


def test_una_ruta_de_nivel_empresa_no_puede_mirar_las_dos_empresas():
    """La etiqueta no puede mentir.

    Si una ruta se declara NIVEL_EMPRESA -- y por eso se deja detras de una
    sesion normal en vez del token -- entonces no puede usar
    scope='all_tenants'. Se verifica leyendo el arbol de sintaxis, no
    confiando en el comentario que tenga al lado.
    """
    import ast

    import app as app_module

    arbol = ast.parse(open(app_module.__file__, encoding='utf-8').read())
    vista_de_ruta = {}
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.FunctionDef):
            continue
        for dec in nodo.decorator_list:
            if (isinstance(dec, ast.Call) and dec.args
                    and isinstance(dec.args[0], ast.Constant)):
                vista_de_ruta.setdefault(dec.args[0].value, nodo)

    infractoras = []
    for ruta, (cap, nivel) in app_module._ADMIN_CAPABILITIES.items():
        if nivel != app_module.NIVEL_EMPRESA:
            continue
        vista = vista_de_ruta.get(ruta)
        assert vista is not None, 'no se encontro la vista de ' + ruta
        for n in ast.walk(vista):
            if isinstance(n, ast.Constant) and n.value == 'all_tenants':
                infractoras.append((ruta, cap))
                break

    assert not infractoras, (
        'declaradas de nivel empresa pero leen las dos empresas: '
        + str(infractoras) + '. O suben a NIVEL_GLOBAL (token) o dejan de '
        'usar all_tenants.')


# --------------------------------------- autenticado != admin global

@pytest.mark.parametrize('tenant', [ASTRAL, NORKEVIN])
def test_ninguna_sesion_normal_abre_una_ruta_global(client, tenant):
    """Ni desde una empresa ni desde la otra. Estar logueado como duenio de
    un negocio no da acceso a operaciones sobre los dos."""
    import app as app_module

    login_as_tenant(client, tenant, email=f'admin@{tenant}.com')
    for ruta in app_module._ADMIN_PATHS:
        resp = client.get(ruta)
        assert resp.status_code == 404, \
            f'{ruta} respondio {resp.status_code} a una sesion normal de {tenant}'


def test_tampoco_por_POST(client):
    """Las mas peligrosas son POST, no GET."""
    login_as_tenant(client, ASTRAL)
    for ruta in ('/api/admin/migrate-to-multi-tenant',
                 '/api/admin/workflow-cleanup',
                 '/api/admin/import-astral-leads'):
        resp = client.post(ruta, json={'confirm': 'MIGRAR', 'dry_run': True})
        assert resp.status_code == 404, f'{ruta} respondio {resp.status_code}'


def test_un_token_equivocado_tampoco_entra(client):
    login_as_tenant(client, ASTRAL)
    resp = client.get('/api/admin/tenant-inventory?token=token-inventado')
    assert resp.status_code == 404


def test_el_404_no_confirma_que_la_ruta_exista(client):
    """Kevin: externamente, Not found o Access denied. Una ruta admin real
    sin token tiene que responder igual que una que no existe."""
    login_as_tenant(client, ASTRAL)

    real = client.get('/api/admin/incident-report')
    inventada = client.get('/api/admin/no-existe-esta-ruta')

    assert real.status_code == inventada.status_code == 404


def test_con_el_token_si_entra(client):
    """Caso POSITIVO: el token sigue abriendo las rutas de auditoria."""
    import app as app_module

    resp = client.get('/api/admin/tenant-inventory?token='
                      + app_module._ADMIN_ONE_TIME_TOKEN)
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True


def test_las_de_nivel_empresa_si_responden_a_una_sesion_normal(client):
    """Caso POSITIVO: subirlas a token romperia Settings sin ganar
    aislamiento -- el store ya las limita a la empresa de la sesion."""
    login_as_tenant(client, ASTRAL)

    # Sin la palabra de confirmacion no hacen nada, pero la ruta EXISTE para
    # una sesion normal: NO devuelve 404.
    #
    # Actualizado por el hardening de prioridad 6 (agosto 2026): antes esto
    # daba 400 ('Confirmacion requerida'). Ahora la primera guarda que
    # dispara es la flag de entorno ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS, que
    # por diseno esta ausente por defecto -> 403 antes de llegar siquiera a
    # mirar la confirmacion. Lo que este test verifica sigue siendo lo
    # mismo: que la ruta es alcanzable por una sesion normal (no es 404) y
    # que sin autorizacion explicita no hace nada.
    resp = client.post('/api/admin/reset-test-data', json={})
    assert resp.status_code != 404, 'la ruta debe existir para una sesion normal'
    assert resp.status_code in (400, 403)
    assert resp.get_json()['ok'] is False


def test_la_capacidad_queda_registrada_al_usarse(client, caplog):
    """Para que el log diga QUE capacidad se uso, no solo que URL se llamo."""
    import logging

    import app as app_module

    with caplog.at_level(logging.WARNING):
        client.get('/api/admin/incident-report?token='
                   + app_module._ADMIN_ONE_TIME_TOKEN)

    lineas = [r.message for r in caplog.records if 'CAPACIDAD_ADMIN_USADA' in r.message]
    assert lineas, 'usar una ruta admin debe quedar en el log de seguridad'
    assert 'capacidad=incident_report' in lineas[-1]


def test_el_rechazo_tambien_registra_que_se_intentaba(client, caplog):
    """Saber que alguien intento un incident_report sin token es mas util que
    saber que pidio una URL."""
    import logging

    login_as_tenant(client, ASTRAL)
    with caplog.at_level(logging.WARNING):
        client.get('/api/admin/incident-report')

    lineas = [r.message for r in caplog.records if 'RUTA_ADMIN_SIN_TOKEN' in r.message]
    assert lineas and 'capacidad=incident_report' in lineas[-1]
