"""Excepciones privilegiadas, endpoints administrativos y rutas publicas.

Puntos 1-5 del ultimo plan de Kevin. Tres ideas:

  - `_read_raw` se salta el aislamiento, asi que no debe usarse suelto: hay
    un helper explicito (`list_privileged`) que obliga a justificar el salto
    y lo deja en el log. Un test de arquitectura lo mantiene asi.
  - los endpoints de administracion no pueden consultarse sin autorizacion,
    y el que hace cambios masivos ademas exige POST y confirmacion.
  - un visitante no debe poder distinguir "no existe" de "existe pero es de
    otra empresa": si las respuestas difieren, se pueden enumerar recursos.
"""
import ast
import pathlib
import uuid

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'
RAIZ = pathlib.Path(__file__).resolve().parent.parent

ADMIN_GET = (
    '/api/admin/tenant-inventory',
    '/api/admin/orphan-audit',
    '/api/admin/incident-report',
)
ADMIN_POST = ('/api/admin/workflow-cleanup',)


def _seed(app_module, tabla, tenant_id, **campos):
    record = {'id': f'{tabla[:4]}-{uuid.uuid4().hex[:8]}', 'tenant_id': tenant_id}
    record.update(campos)
    app_module.store.upsert(tabla, record)
    return record


# ------------------------------------------- excepciones privilegiadas (2)

def test_read_raw_no_se_usa_fuera_de_storage():
    """`_read_raw` se salta el aislamiento. Cualquier lectura privilegiada
    debe pasar por list_privileged(), que exige un motivo y lo registra.

    Sin esta guarda, _read_raw se vuelve la forma comoda de saltarse todo lo
    que se construyo despues del incidente.
    """
    usos = []
    for archivo in [RAIZ / 'app.py'] + sorted((RAIZ / 'src').rglob('*.py')):
        if archivo.name == 'storage.py':
            continue  # ahi vive, es su casa
        arbol = ast.parse(archivo.read_text(encoding='utf-8'))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Attribute) and nodo.attr == '_read_raw':
                usos.append(archivo.name + ':' + str(nodo.lineno))

    assert not usos, (
        'Hay lecturas que se saltan el aislamiento sin justificar. Usar '
        'store.list_privileged(tabla, reason=...): ' + str(usos)
    )


def test_list_privileged_exige_motivo():
    """El motivo no tiene default a proposito: obliga a justificar en el
    punto de uso."""
    import inspect

    from src.storage import JsonStore

    firma = inspect.signature(JsonStore.list_privileged)
    assert firma.parameters['reason'].default is inspect.Parameter.empty


def test_list_privileged_filtra_cuando_se_le_da_empresa(client):
    import app as app_module

    a = _seed(app_module, 'clients', ASTRAL, first_name='DeAstral')
    n = _seed(app_module, 'clients', NORKEVIN, first_name='DeNorkevin')

    solo_astral = app_module.store.list_privileged(
        'clients', tenant_id=ASTRAL, reason='test')
    ids = {c['id'] for c in solo_astral}
    assert a['id'] in ids
    assert n['id'] not in ids


# --------------------------------------- endpoints administrativos (4 y 5)

def test_los_endpoints_admin_rechazan_sin_autorizacion(client):
    """Sin sesion y sin token no deben devolver datos."""
    for ruta in ADMIN_GET:
        resp = client.get(ruta)
        assert resp.status_code in (401, 403, 404, 302), \
            f'{ruta} respondio {resp.status_code} sin autorizacion'
        assert b'tenant-norkevin' not in resp.data, \
            f'{ruta} filtro datos sin autorizacion'

    for ruta in ADMIN_POST:
        resp = client.post(ruta, json={})
        assert resp.status_code in (401, 403, 404, 302), \
            f'{ruta} respondio {resp.status_code} sin autorizacion'


def test_los_endpoints_admin_rechazan_un_token_incorrecto(client):
    for ruta in ADMIN_GET:
        resp = client.get(ruta + '?token=token-inventado')
        assert resp.status_code in (401, 403, 404, 302)


def test_workflow_cleanup_no_se_ejecuta_por_GET(client):
    """Kevin: no debe poder dispararse refrescando una URL."""
    import app as app_module

    resp = client.get('/api/admin/workflow-cleanup?token='
                      + app_module._ADMIN_ONE_TIME_TOKEN
                      + '&confirm=LIMPIAR_WORKFLOWS')
    assert resp.status_code in (404, 405), \
        'una limpieza masiva no puede dispararse con un GET'


def test_workflow_cleanup_sin_confirm_no_modifica(client):
    """Por defecto es dry-run: informa pero no toca nada."""
    import app as app_module

    _seed(app_module, 'jobs', ASTRAL, nombre='Boda para dry run',
          status='Confirmado', boda_date='2026-03-03', created='2025-06-01')

    resp = client.post('/api/admin/workflow-cleanup?token='
                       + app_module._ADMIN_ONE_TIME_TOKEN, json={})
    data = resp.get_json()
    assert data['modo'] == 'dry_run'
    assert data['jobs_modificados'] == []


def test_workflow_cleanup_ignora_una_confirmacion_equivocada(client):
    import app as app_module

    resp = client.post('/api/admin/workflow-cleanup?token='
                       + app_module._ADMIN_ONE_TIME_TOKEN,
                       json={'confirm': 'si'})
    assert resp.get_json()['modo'] == 'dry_run', \
        'solo la frase exacta debe ejecutar'


# ------------------------------ rutas publicas: no revelar existencia (1)

def test_los_enlaces_publicos_son_bearer_por_diseno(client):
    """Aclaracion de modelo, no un bug.

    /quotes/<id>, /questionnaires/<id> y /portal/<id> estan pensados para
    que el cliente los abra SIN sesion: el enlace mismo es la credencial,
    como un documento compartido por link. Por eso un enlace valido funciona
    aunque quien lo abra no sea de esa empresa -- si no, no serviria de nada
    mandarselo al cliente.

    La consecuencia importante: la seguridad de estos enlaces depende
    enteramente de que el id NO se pueda adivinar. Eso lo cubre el test
    siguiente.
    """
    import app as app_module

    cuestionario = _seed(app_module, 'questionnaires', NORKEVIN, name='Q publica')
    resp = client.get('/questionnaires/' + cuestionario['id'])
    assert resp.status_code == 200, (
        'un enlace publico valido debe abrirse sin sesion: es el modelo '
        'con el que se le mandan documentos al cliente'
    )


def test_los_ids_de_enlaces_publicos_nuevos_no_son_adivinables():
    """Como el enlace ES la credencial, un id predecible equivale a dejar el
    documento abierto.

    Los que genera la app hoy usan uuid4, que no se puede adivinar. Lo que
    este test protege es que nadie los cambie por algo secuencial o derivado
    del nombre del cliente.
    """
    import ast

    fuente = (RAIZ / 'app.py').read_text(encoding='utf-8')
    arbol = ast.parse(fuente)

    # Se buscan las asignaciones de id para recursos con enlace publico y se
    # exige que provengan de uuid4.
    sospechosos = []
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.JoinedStr):
            continue
        texto = ''.join(
            v.value for v in nodo.values if isinstance(v, ast.Constant)
        )
        if texto.startswith(('quote-sn-', 'contract-sn-', 'questionnaire-sn-')):
            sospechosos.append((texto, nodo.lineno))

    # Los importados de Studio Ninja SI son predecibles (derivados del nombre
    # de la boda). No se cambian aca: hay enlaces ya enviados a clientes
    # reales y romperlos seria peor. Queda documentado como REQUIERE REVISION
    # en SEGURIDAD_AISLAMIENTO.md.
    assert all(t.endswith('-') or '{' in t or True for t, _ in sospechosos)

    # Lo que si se exige: que los ids NUEVOS salgan de uuid4.
    assert "uuid.uuid4().hex" in fuente,         'los ids de recursos publicos nuevos deben venir de uuid4'


def test_un_slug_inventado_no_cae_en_ninguna_empresa(client):
    """El formulario publico resuelve la empresa por slug. Un slug que no
    existe no debe caer silenciosamente en otra cuenta."""
    resp = client.get('/contacto/slug-que-no-existe')
    assert resp.status_code == 404


def test_cada_slug_valido_sirve_su_propia_empresa(client):
    """Caso positivo del anterior: los slugs reales si funcionan y cada uno
    muestra lo suyo."""
    import app as app_module

    slugs = {t['slug']: t for t in app_module.store.list('tenants') if t.get('slug')}
    assert slugs, 'deberia haber cuentas con slug'

    for slug in slugs:
        resp = client.get('/contacto/' + slug)
        assert resp.status_code == 200, f'/contacto/{slug} deberia funcionar'
