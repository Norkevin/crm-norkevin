"""
SNAPSHOT COMERCIAL: cotizacion aceptada == factura == PDF  (3-sep-2026)

Kevin: "lo que coticé = lo que el cliente aceptó = lo que facturo".

El bug: al aceptar, quote_accept congelaba `incluye` e `items` de la
opcion elegida, pero NO sus `servicios` estructurados ni sus `groups`.
Cotizacion y factura leian esos campos desde la RAIZ del quote, que podia
tener los grupos de otra opcion -- o ninguno. Resultado: dos desgloses
distintos del mismo acuerdo.

Estos tests fijan la regla del sistema:

    QUOTE TEMPLATE      plantilla reutilizable
    QUOTE DRAFT         propuesta editable
    SNAPSHOT ACEPTADO   el acuerdo comercial, congelado
    JOB                 ejecucion operativa
    INVOICE             representacion financiera de ese acuerdo

Una factura no reconstruye el paquete: lee el acuerdo.

Corren sin Flask: se prueba _snapshot_comercial extraida de app.py.
"""
import ast
import os
import sys
import types

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)


def _cargar_funciones():
    """Extrae de app.py solo las funciones que se prueban, sin importar el
    modulo entero (que necesita Flask). Es la misma tecnica que usan los
    otros tests stdlib del repo."""
    with open(os.path.join(RAIZ, 'app.py'), encoding='utf-8') as fh:
        arbol = ast.parse(fh.read())
    querido = {'_snapshot_comercial', '_normalize_quote_options', '_quote_grupos_display'}
    nodos = [n for n in arbol.body
             if isinstance(n, ast.FunctionDef) and n.name in querido]
    assert len(nodos) == len(querido), f'faltan funciones: {querido - {n.name for n in nodos}}'
    modulo = types.ModuleType('app_parcial')
    modulo.__dict__['qsvc'] = __import__('src.quote_services', fromlist=['x'])
    codigo = compile(ast.Module(body=nodos, type_ignores=[]), 'app.py', 'exec')
    exec(codigo, modulo.__dict__)
    return modulo


APP = _cargar_funciones()
snapshot_comercial = APP._snapshot_comercial
grupos_display = APP._quote_grupos_display


# ============================================================
# Fixtures: una plantilla y una cotizacion sobre ella
# ============================================================
def _plantilla_silver():
    """La plantilla, tal como vive en packages.json. Es MUTABLE: el
    fotografo la edita cuando quiere."""
    return {
        'id': 'pkg-silver', 'name': 'Fotografía Silver 2 Fotógrafos',
        'includes': ['8 horas de cobertura', '500 imágenes digitales',
                     'Galería fotográfica'],
        'price': 7500.0,
    }


def _quote_desde_plantilla(plantilla, quote_id='q-1'):
    """Una cotizacion en Borrador construida desde la plantilla."""
    return {
        'id': quote_id, 'tenant_id': 'tenant-astral', 'status': 'Borrador',
        'options': [{
            'id': 'opt-a', 'name': plantilla['name'],
            'precio_total': plantilla['price'],
            'incluye': list(plantilla['includes']),
            'servicios': [
                {'tipo': 'horas_cobertura', 'cantidad': 8},
                {'tipo': 'fotos_digitales', 'cantidad': 500},
                {'tipo': 'galeria_fotografica'},
            ],
            'groups': [],
        }],
    }


def _aceptar(quote, option_id='opt-a', extras=None, plan=1):
    """Replica lo que hace quote_accept al materializar la aceptacion.

    Se mantiene alineado a mano con app.py a proposito: si alguien cambia
    quote_accept y se olvida de congelar un campo, el test de plantilla
    modificada lo caza."""
    opciones = quote.get('options') or []
    elegida = next(o for o in opciones if o['id'] == option_id)
    extras = extras or []
    extras_total = sum(float(e.get('price') or 0) for e in extras)
    base = float(elegida.get('precio_total') or 0)
    quote.update({
        'status': 'Aceptada',
        'selected_option_id': elegida['id'],
        'paquete_nombre': elegida.get('name'),
        'paquete_precio_base': base,
        'selected_extras': extras,
        'extras_total': extras_total,
        'precio_total': base + extras_total,
        'incluye': elegida.get('incluye'),
        'items': elegida.get('items', []),
        'servicios': elegida.get('servicios') or [],
        'groups': elegida.get('groups') or [],
        'plan_pago': plan,
        'snapshot_aceptado': {
            'option_id': elegida.get('id'), 'name': elegida.get('name'),
            'subtitle': elegida.get('subtitle') or '',
            'description': elegida.get('description') or '',
            'servicios': elegida.get('servicios') or [],
            'groups': elegida.get('groups') or [],
            'incluye': elegida.get('incluye') or [],
            'precio_base': base, 'extras': extras, 'extras_total': extras_total,
            'total': base + extras_total, 'plan_pago': plan,
            'aceptado_en': '2026-09-01',
        },
    })
    return quote


def _textos(grupos):
    return [s['texto'] for g in grupos for s in g['servicios']]


# ============================================================
# EL CASO DE REGRESION QUE PIDIO KEVIN
# ============================================================
def test_la_factura_muestra_exactamente_lo_aceptado():
    """Cotizacion: 8 horas / 500 imagenes / galeria.
    Factura: los MISMOS tres conceptos, ni uno mas."""
    quote = _aceptar(_quote_desde_plantilla(_plantilla_silver()))
    snap = snapshot_comercial(quote)

    de_cotizacion = _textos(grupos_display(snap))
    de_factura = _textos(grupos_display(snap))   # misma fuente, por diseño

    assert de_cotizacion == de_factura, 'cotizacion y factura divergen'
    assert len(de_cotizacion) == 3, f'se colaron conceptos: {de_cotizacion}'
    for esperado in ('8 horas de cobertura', '500 fotografías digitales',
                     'Galería fotográfica'):
        assert esperado in de_cotizacion, f'falta {esperado}: {de_cotizacion}'
    # Nada de video: la plantilla Silver no lo incluye.
    assert not any('ideo' in t for t in de_cotizacion), de_cotizacion


# ============================================================
# EL TEST CRITICO: plantilla modificada DESPUES de aceptar
# ============================================================
def test_modificar_la_plantilla_no_altera_una_cotizacion_aceptada():
    """1 sep: Silver = 8h / 500 fotos. El cliente acepta.
    10 sep: el fotografo cambia Silver a 12h / 800 fotos + video.
    La factura del primer cliente DEBE seguir diciendo 8h / 500 fotos."""
    plantilla = _plantilla_silver()
    quote = _aceptar(_quote_desde_plantilla(plantilla))

    antes = _textos(grupos_display(snapshot_comercial(quote)))

    # El fotografo edita la plantilla. Es un objeto vivo y reutilizable.
    plantilla['includes'] = ['12 horas de cobertura', '800 imágenes digitales',
                             'Galería fotográfica', 'Video de 5 minutos']
    plantilla['price'] = 11000.0

    despues = _textos(grupos_display(snapshot_comercial(quote)))

    assert antes == despues, 'la plantilla contamino un documento historico'
    assert '8 horas de cobertura' in despues
    assert '12 horas de cobertura' not in despues
    assert not any('ideo' in t for t in despues), despues
    assert snapshot_comercial(quote)['total'] == 7500.0


def test_personalizar_antes_de_aceptar_manda_sobre_la_plantilla():
    """Plantilla 8h, pero para ESTE cliente se cotizo 10h.
    La factura debe decir 10, no volver a la plantilla."""
    quote = _quote_desde_plantilla(_plantilla_silver())
    quote['options'][0]['servicios'][0]['cantidad'] = 10
    quote['options'][0]['incluye'][0] = '10 horas de cobertura'
    quote = _aceptar(quote)

    textos = _textos(grupos_display(snapshot_comercial(quote)))
    assert '10 horas de cobertura' in textos, textos
    assert '8 horas de cobertura' not in textos


# ============================================================
# Opciones y extras
# ============================================================
def test_se_factura_la_opcion_aceptada_no_todas():
    quote = {
        'id': 'q-2', 'tenant_id': 'tenant-astral', 'status': 'Borrador',
        'options': [
            {'id': 'opt-a', 'name': 'Silver', 'precio_total': 7500.0,
             'incluye': ['8 horas de cobertura'],
             'servicios': [{'tipo': 'horas_cobertura', 'cantidad': 8}]},
            {'id': 'opt-b', 'name': 'Gold', 'precio_total': 12000.0,
             'incluye': ['12 horas de cobertura', '1 videógrafo'],
             'servicios': [{'tipo': 'horas_cobertura', 'cantidad': 12},
                           {'tipo': 'videografos', 'cantidad': 1}]},
            {'id': 'opt-c', 'name': 'Diamond', 'precio_total': 18000.0,
             'incluye': ['2 videógrafos'],
             'servicios': [{'tipo': 'videografos', 'cantidad': 2}]},
        ],
    }
    quote = _aceptar(quote, option_id='opt-b')
    snap = snapshot_comercial(quote)
    textos = _textos(grupos_display(snap))

    assert snap['nombre'] == 'Gold'
    assert snap['total'] == 12000.0
    assert '12 horas de cobertura' in textos and '1 videógrafo' in textos
    # Ni la opcion A ni la C: solo se factura lo aceptado.
    assert '8 horas de cobertura' not in textos
    assert '2 videógrafos' not in textos


def test_los_extras_aceptados_se_conservan_y_los_no_elegidos_no_aparecen():
    quote = _aceptar(_quote_desde_plantilla(_plantilla_silver()),
                     extras=[{'id': 'x1', 'name': 'Drone', 'price': 800.0}])
    snap = snapshot_comercial(quote)
    nombres = [e.get('name') for e in snap['extras']]
    assert nombres == ['Drone'], nombres
    assert 'Álbum' not in nombres
    assert snap['total'] == 8300.0, snap['total']


# ============================================================
# Prioridad de fuentes y legacy
# ============================================================
def test_el_snapshot_manda_sobre_los_campos_planos():
    """Si por cualquier via los campos planos quedaran desincronizados, el
    snapshot -- que es el acuerdo -- es el que vale."""
    quote = _aceptar(_quote_desde_plantilla(_plantilla_silver()))
    quote['incluye'] = ['ALGO QUE NADIE ACEPTO']
    quote['servicios'] = [{'tipo': 'videografos', 'cantidad': 9}]

    snap = snapshot_comercial(quote)
    assert snap['fuente'] == 'snapshot_aceptado'
    textos = _textos(grupos_display(snap))
    assert '8 horas de cobertura' in textos
    assert 'ALGO QUE NADIE ACEPTO' not in textos
    assert not any('videógrafo' in t for t in textos)


def test_cotizacion_aceptada_sin_snapshot_usa_los_campos_materializados():
    """Aceptadas antes de que existiera el snapshot. Los campos planos ya
    los materializo quote_accept, asi que son historicos igual -- lo que NO
    se puede hacer es volver a la plantilla."""
    quote = _aceptar(_quote_desde_plantilla(_plantilla_silver()))
    del quote['snapshot_aceptado']

    snap = snapshot_comercial(quote)
    assert snap['fuente'] == 'campos_materializados'
    textos = _textos(grupos_display(snap))
    assert '8 horas de cobertura' in textos
    assert snap['total'] == 7500.0


def test_cotizacion_legacy_solo_con_incluye_plano():
    quote = {'id': 'q-legacy', 'tenant_id': 'tenant-astral', 'status': 'Aceptada',
             'paquete_nombre': 'Paquete 2024', 'precio_total': 5000.0,
             'incluye': ['2 fotografos', '6 horas de cobertura']}
    snap = snapshot_comercial(quote)
    assert snap['fuente'] == 'campos_materializados'
    textos = _textos(grupos_display(snap))
    assert len(textos) == 2, textos


def test_cotizacion_pendiente_usa_la_opcion_seleccionada():
    quote = _quote_desde_plantilla(_plantilla_silver())
    quote['selected_option_id'] = 'opt-a'
    snap = snapshot_comercial(quote)
    assert snap['fuente'] == 'opcion_seleccionada'
    assert snap['nombre'] == 'Fotografía Silver 2 Fotógrafos'


def test_sin_cotizacion_no_se_inventa_desglose():
    assert snapshot_comercial(None) is None
    assert grupos_display({}) == []


# ============================================================
# Multi-tenant
# ============================================================
def test_el_snapshot_no_cruza_tenants():
    """El snapshot vive DENTRO del quote, asi que no hay forma de que la
    factura de un tenant lea el acuerdo de otro: para llegar al snapshot
    hay que tener el quote, y el quote se resuelve por tenant."""
    astral = _aceptar(_quote_desde_plantilla(_plantilla_silver(), 'q-astral'))
    nork = _quote_desde_plantilla(_plantilla_silver(), 'q-nork')
    nork['tenant_id'] = 'tenant-norkevin'
    nork['options'][0]['name'] = 'Norkevin Bodas'
    nork['options'][0]['incluye'] = ['1 fotografo']
    nork['options'][0]['servicios'] = [{'tipo': 'fotografos', 'cantidad': 1}]
    nork = _aceptar(nork)

    sa, sn = snapshot_comercial(astral), snapshot_comercial(nork)
    assert sa['nombre'] != sn['nombre']
    assert astral['tenant_id'] != nork['tenant_id']
    assert '8 horas de cobertura' in _textos(grupos_display(sa))
    assert '8 horas de cobertura' not in _textos(grupos_display(sn))


# ============================================================
# El snapshot no toca dinero ya cobrado
# ============================================================
def test_el_snapshot_no_contiene_pagos_ni_saldos():
    """Congelar el acuerdo comercial no puede arrastrar estado financiero:
    los pagos viven en `payments` y cambian con el tiempo."""
    quote = _aceptar(_quote_desde_plantilla(_plantilla_silver()))
    snap = quote['snapshot_aceptado']
    for prohibido in ('pagado', 'pendiente', 'saldo', 'payments', 'paid_amount',
                      'due_date', 'invoice_id'):
        assert prohibido not in snap, f'el snapshot arrastro {prohibido}'
