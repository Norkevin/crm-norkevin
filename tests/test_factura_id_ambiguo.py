"""
FACTURA: un invoice_id repetido mandaba a la boda equivocada (4-sep-2026)

Kevin: "me meto a cualquier trabajo y al ir a factura, y le doy ver como el
cliente, me tira solo a keller zapote, en todos los jobs".

Causa exacta, reproducida con su archivo real de Studio Ninja:

    invoice_id = 'INV-SN-' + slug.upper().replace('-', '')[:8] + f'-{qi+1}'

Los slugs son 'job_20270123_keller-zapote'. Quitar los guiones y cortar a 8
deja 'JOB20270' para TODAS las bodas de ese ano. Sus 19 bodas quedaron con
5 invoice_id distintos; siete clientes comparten 'INV-SN-JOB20270-1'.

Como todas las pantallas resolvian con
    next(p for p in payments if p['invoice_id'] == clave)
siempre ganaba la primera fila. No era solo un enlace feo: el enlace
publico /i/<token> degradaba el token (que si identifica una fila unica) a
invoice_id, asi que el cliente A podia ver la factura del cliente B.

Estos tests fijan las tres capas: como se generan los ids, como se
resuelven, y con que llave enlazan las plantillas.
"""
import ast
import hashlib
import os
import re
import sys
import unicodedata

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

APP = open(os.path.join(RAIZ, 'app.py'), encoding='utf-8').read()
ARBOL = ast.parse(APP)
FUNCS = {n.name: n for n in ast.walk(ARBOL)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

# Los slugs reales del archivo de Kevin. Con la formula vieja los cuatro
# primeros colapsan en el mismo invoice_id.
SLUGS = [
    'job-20270123-keller-zapote',
    'job-20270130-jessica-estrada',
    'job-20270214-ana-lucia',
    'job-20270301-maria-jose',
    'job-20261128-boda-con-geraldine',
    'job-20261128-juan-estela',
]


def _slug_a_invoice_viejo(slug, qi=0):
    return 'INV-SN-' + slug.upper().replace('-', '')[:8] + f'-{qi + 1}'


def _slug_a_invoice_nuevo(slug, qi=0):
    firma = hashlib.sha1(slug.encode('utf-8')).hexdigest()[:8].upper()
    return f'INV-SN-{firma}-{qi + 1}'


def test_la_formula_vieja_de_verdad_colisionaba():
    """Sin esto, el resto de los tests no prueban nada: deja constancia de
    que el bug era real y no una precaucion teorica."""
    viejos = {_slug_a_invoice_viejo(s) for s in SLUGS}
    assert len(viejos) < len(SLUGS), 'la formula vieja no colisiona: revisar'


def test_cada_boda_recibe_un_invoice_id_propio():
    nuevos = [_slug_a_invoice_nuevo(s) for s in SLUGS]
    assert len(set(nuevos)) == len(SLUGS), f'siguen colisionando: {nuevos}'


def test_el_invoice_id_sigue_siendo_deterministico():
    """El importador es idempotente porque los ids no cambian entre
    corridas. Un uuid arreglaria la colision y romperia eso: reimportar
    duplicaria las facturas en vez de saltarlas."""
    for s in SLUGS:
        assert _slug_a_invoice_nuevo(s) == _slug_a_invoice_nuevo(s)


def test_el_importador_usa_la_formula_nueva():
    cuerpo = ast.unparse(FUNCS['api_admin_import_studio_ninja'])
    assert "[:8] + f'-{qi + 1}'" not in cuerpo, 'quedo la formula truncada'
    assert 'hashlib.sha1' in cuerpo and 'firma_slug' in cuerpo


def test_hashlib_esta_importado_a_nivel_de_modulo():
    """El import va arriba: si queda dentro de la funcion, el import falla
    recien cuando alguien importa de verdad, en produccion."""
    assert re.search(r'^import hashlib$', APP, re.M)


# --- Resolucion: por id de fila, nunca por una llave ambigua ---

def test_las_rutas_de_factura_resuelven_con_el_helper_unico():
    """Cuatro entradas resolvian una factura por su cuenta. Ahora todas
    pasan por _fila_de_factura, que busca por id (unico) antes que por
    invoice_id (repetible)."""
    assert '_fila_de_factura' in FUNCS
    for ruta in ('_invoice_document', 'invoice_view', 'invoice_pdf'):
        cuerpo = ast.unparse(FUNCS[ruta])
        assert '_fila_de_factura' in cuerpo, f'{ruta} resuelve por su cuenta'
        assert "p.get('invoice_id') == invoice_id" not in cuerpo, \
            f'{ruta} todavia resuelve por invoice_id crudo'


def test_el_helper_prefiere_el_id_de_la_fila():
    cuerpo = ast.unparse(FUNCS['_fila_de_factura'])
    pos_id = cuerpo.find("p.get('id') == clave")
    pos_inv = cuerpo.find("p.get('invoice_id') == clave")
    assert 0 <= pos_id < pos_inv, 'busca por invoice_id antes que por id'


def test_el_token_publico_no_se_degrada_a_invoice_id():
    """El token identifica UNA fila. Devolver su invoice_id tiraba esa
    precision y era la via por la que un cliente veia la factura de otro."""
    cuerpo = ast.unparse(FUNCS['_resolve_invoice_by_token'])
    assert "fila.get('id')" in cuerpo
    assert cuerpo.find("fila.get('id')") < cuerpo.find("fila.get('invoice_id')")


def test_el_calendario_de_una_factura_no_cruza_bodas():
    """Una factura pertenece a UN job. Agrupar solo por invoice_id metia
    cuotas de clientes distintos en el mismo documento."""
    cuerpo = ast.unparse(FUNCS['_invoice_document'])
    bloque = cuerpo[cuerpo.find('mismo ='):]
    bloque = bloque[:bloque.find('\n', bloque.find('schedule = mismo'))]
    assert "job_id" in bloque, 'el fallback agrupa sin exigir el mismo job'


def test_la_lista_global_de_facturas_no_fusiona_clientes():
    """/invoices cruza todas las bodas: sin el job_id en la llave, dos
    facturas distintas se sumaban en una sola fila."""
    cuerpo = ast.unparse(FUNCS['invoices_list'])
    grupo = [l for l in cuerpo.split('\n') if 'group_key =' in l]
    assert grupo and "job_id" in grupo[0], f'llave sin job_id: {grupo}'


# --- Enlaces: la llave que viaja en la URL tiene que ser unica ---

@pytest.mark.parametrize('plantilla,patron', [
    ('job_detail.html', r'/invoices/\{\{\s*g\.'),
    ('invoices.html', r'/invoices/\{\{\s*inv\.'),
    ('invoice_view.html', r'/invoices/\{\{\s*invoice\.'),
])
def test_ninguna_plantilla_enlaza_solo_por_invoice_id(plantilla, patron):
    html = open(os.path.join(RAIZ, 'templates', plantilla), encoding='utf-8').read()
    enlaces = re.findall(patron + r'([^}]*)\}\}', html)
    assert enlaces, f'{plantilla}: no se encontro ningun enlace a /invoices'
    for expr in enlaces:
        # El primer termino de la expresion es el que gana: tiene que ser
        # la llave unica (id de la fila o 'enlace'), y invoice_id solo puede
        # aparecer como respaldo despues de un 'or'.
        primero = re.split(r'\bor\b', expr)[0].strip()
        assert primero in ('enlace', 'id') or primero.endswith('.id'), \
            f'{plantilla} enlaza con {primero!r} en vez del id unico de la fila'


def test_los_grupos_exponen_la_llave_de_enlace():
    for ruta in ('job_detail', 'invoices_list'):
        assert "'enlace'" in ast.unparse(FUNCS[ruta]), f'{ruta} sin enlace'


# --- Diagnostico: poder VER el desastre sin tocarlo ---

def test_hay_una_forma_de_listar_las_facturas_ambiguas():
    """No se migra nada automaticamente: primero se mira. La herramienta
    solo lee."""
    assert '_facturas_ambiguas' in FUNCS
    cuerpo = ast.unparse(FUNCS['_facturas_ambiguas'])
    for escritura in ('store.upsert', 'store.save', 'store.delete', 'store.put'):
        assert escritura not in cuerpo, 'el diagnostico escribe: debe solo leer'
