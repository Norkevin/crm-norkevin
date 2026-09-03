"""
PARIDAD WEB / PDF DEL DOCUMENTO DE FACTURA  (3-sep-2026)

Kevin: "no quiero que en seis meses la factura web vuelva a cambiar y el
PDF quede atras otra vez". Estos tests son ese seguro.

Lo que se verifica no es que se vean identicos -- un PDF tiene otras
limitaciones -- sino que NO PUEDAN divergir en lo que importa:

  - los dos leen el MISMO dict (`_invoice_document`), asi que no pueden
    mostrar cifras distintas;
  - los dos usan los MISMOS tokens (pdf_document_system espeja
    _document_tokens.html);
  - el PDF nunca cae a una serif;
  - una factura de una marca no puede mostrar branding de otra.

Corren sin Flask: se le pasa a cada renderer el mismo dict ya armado.
"""
import re
import subprocess
import tempfile
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.pdf_invoice import render_invoice_pdf
from src.pdf_document_system import registrar_fuente_documental, COLOR


MARCAS = {
    'Astral Weddings': {'display_name': 'Astral Weddings', 'tagline': 'Photography',
                        'email': 'astral@example.com', 'phone': '', 'initial': 'A'},
    'Norkevin Photography': {'display_name': 'Norkevin Photography', 'tagline': 'Bodas',
                             'email': 'nork@example.com', 'phone': '', 'initial': 'N'},
    'Ramiro Cruz Photo': {'display_name': 'Ramiro Cruz Photo', 'tagline': 'Retrato',
                          'email': 'ramiro@example.com', 'phone': '', 'initial': 'R'},
}


def _fila(estado, cuando, posicion, etiqueta, monto):
    return {'estado': estado, 'cuando': cuando, 'posicion': posicion,
            'etiqueta': etiqueta, 'monto': monto}


def _doc(**extra):
    base = dict(
        invoice_id='INV-TEST-1', estado_label='Vencida', estado_tono='danger',
        estado_detalle='1 pago vencido por Q281.25.',
        total=25125.0, pagado=18843.75, pendiente=6281.25,
        concepto='Boda · 28 noviembre 2026',
        incluye=['2 fotógrafos', '8 horas de cobertura + 1 hora extra',
                 '800 imágenes digitales'],
        proximo={'cuando': '28 noviembre 2026', 'monto': 6000.0},
        emitida='22 julio 2025', vence='22 julio 2025',
        vence_label='Primer vencimiento',
        cliente_nombre='Juan Manuel', job_nombre='Boda de Juan Manuel',
        boda_fecha='28 noviembre 2026', notas='',
        filas_pago=[
            _fila('paid', '23 julio 2025', 'Pago 1 de 5', 'Pagado', 6281.25),
            _fila('due', '16 mayo 2026', 'Pago 4 de 5', 'Vencido', 281.25),
            _fila('next', '28 noviembre 2026', 'Pago 5 de 5', 'Próximo pago', 6000.0),
        ],
        selected={'payment_link_url': None},
    )
    base.update(extra)
    return base


def _texto_del_pdf(datos):
    """Extrae el texto real del PDF con pdftotext. Mirar los bytes crudos no
    sirve: reportlab comprime los streams."""
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as t:
        t.write(datos)
        ruta = t.name
    try:
        salida = subprocess.run(['pdftotext', '-enc', 'UTF-8', ruta, '-'],
                                capture_output=True, text=True)
        return salida.stdout
    finally:
        os.unlink(ruta)


def _hay_pdftotext():
    try:
        subprocess.run(['pdftotext', '-v'], capture_output=True)
        return True
    except FileNotFoundError:
        return False


# ============================================================
# El PDF se genera y es valido
# ============================================================
def test_el_pdf_se_genera_y_es_un_pdf_valido():
    datos = render_invoice_pdf(_doc(), MARCAS['Astral Weddings'], simbolo='Q')
    assert datos[:4] == b'%PDF', 'la salida no es un PDF'
    assert len(datos) > 1500, 'el PDF salio sospechosamente vacio'


def test_el_pdf_no_usa_ninguna_serif():
    """El punto que mas molestaba a Kevin. reportlab solo trae 14 fuentes
    base y por defecto varias son Times; si alguna se cuela, el documento
    vuelve a parecer una plantilla vieja."""
    datos = render_invoice_pdf(_doc(), MARCAS['Astral Weddings'], simbolo='Q')
    for serif in (b'Times', b'Georgia', b'Garamond', b'Serif', b'Palatino'):
        assert serif not in datos, f'el PDF incrusta una serif: {serif!r}'


def test_la_familia_del_pdf_es_sans():
    f = registrar_fuente_documental()
    for rol in ('regular', 'medium', 'bold'):
        nombre = f[rol]
        assert 'Times' not in nombre and 'Serif' not in nombre, \
            f'el rol {rol} resolvio a una serif: {nombre}'


# ============================================================
# Paridad de contenido
# ============================================================
def test_los_montos_del_pdf_son_los_del_documento():
    """Web y PDF leen el mismo dict; ninguno recalcula. Si un monto no
    aparece igual, alguien empezo a hacer aritmetica por su cuenta."""
    if not _hay_pdftotext():
        return
    doc = _doc()
    txt = _texto_del_pdf(render_invoice_pdf(doc, MARCAS['Astral Weddings'], simbolo='Q'))
    for clave in ('total', 'pagado', 'pendiente'):
        esperado = f'Q{doc[clave]:,.2f}'
        assert esperado in txt, f'{clave} ({esperado}) no aparece en el PDF'


def test_el_pdf_lleva_todos_los_pagos_del_calendario():
    if not _hay_pdftotext():
        return
    doc = _doc()
    txt = _texto_del_pdf(render_invoice_pdf(doc, MARCAS['Astral Weddings'], simbolo='Q'))
    for fila in doc['filas_pago']:
        assert fila['posicion'] in txt, f"falta {fila['posicion']} en el PDF"
        assert f"Q{fila['monto']:,.2f}" in txt, f"falta el monto de {fila['posicion']}"


def test_el_pdf_no_recorta_las_inclusiones():
    """La factura documenta lo que se cobra: el cliente tiene que poder
    verificar que le estan cobrando lo que acepto."""
    if not _hay_pdftotext():
        return
    doc = _doc(incluye=[f'Servicio {i + 1}' for i in range(25)])
    txt = _texto_del_pdf(render_invoice_pdf(doc, MARCAS['Astral Weddings'], simbolo='Q'))
    for item in doc['incluye']:
        assert item in txt, f'se perdio la inclusion "{item}"'


def test_una_inclusion_larga_no_se_parte_en_palabras_sueltas():
    """Kevin vio "extra" y "ya pagadas" como si fueran conceptos aparte.
    No era un problema de datos: era el ancho de columna."""
    if not _hay_pdftotext():
        return
    frase = '8 horas de cobertura + 1 hora extra'
    txt = _texto_del_pdf(render_invoice_pdf(_doc(), MARCAS['Astral Weddings'], simbolo='Q'))
    plano = re.sub(r'\s+', ' ', txt)
    assert frase in plano, 'la inclusion se partio en fragmentos'


def test_el_numero_de_factura_aparece_en_el_pdf():
    if not _hay_pdftotext():
        return
    doc = _doc()
    txt = _texto_del_pdf(render_invoice_pdf(doc, MARCAS['Astral Weddings'], simbolo='Q'))
    assert doc['invoice_id'] in txt


# ============================================================
# Multi-tenant
# ============================================================
def test_cada_marca_ve_solo_lo_suyo_en_el_pdf():
    """El incidente de agosto de 2026 fue exactamente esto en un email.
    En una factura seria peor."""
    if not _hay_pdftotext():
        return
    for nombre, marca in MARCAS.items():
        txt = _texto_del_pdf(render_invoice_pdf(_doc(), marca, simbolo='Q'))
        assert nombre in txt, f'el PDF de {nombre} no lleva su propia marca'
        assert marca['email'] in txt, f'falta el email de {nombre}'
        for otra, datos_otra in MARCAS.items():
            if otra == nombre:
                continue
            assert otra not in txt, f'el PDF de {nombre} muestra la marca {otra}'
            assert datos_otra['email'] not in txt, \
                f'el PDF de {nombre} muestra el email de {otra}'


def test_sin_marca_no_se_asume_ninguna():
    """Si no se resuelve el tenant, el documento dice que no se identifico
    en vez de poner silenciosamente la marca equivocada."""
    if not _hay_pdftotext():
        return
    txt = _texto_del_pdf(render_invoice_pdf(_doc(), None, simbolo='Q'))
    assert 'Estudio no identificado' in txt
    for nombre in MARCAS:
        assert nombre not in txt


# ============================================================
# Casos limite: nada debe reventar ni perder datos
# ============================================================
def test_casos_limite_no_revientan():
    casos = {
        'saldada': _doc(estado_label='Pagada', estado_tono='success', pagado=25125.0,
                        pendiente=0.0, proximo=None, estado_detalle=''),
        'sin_inclusiones': _doc(incluye=[]),
        'inclusiones_None': _doc(incluye=None),
        'sin_pagos': _doc(filas_pago=[]),
        'sin_proximo': _doc(proximo=None),
        'nombres_largos': _doc(
            cliente_nombre='Juan Manuel Jiménez Cruz de la Santísima Trinidad del Valle',
            job_nombre='Boda de Juan Manuel Jiménez Cruz & Lucía Fernanda Charchal Ciudad Real'),
        'montos_grandes': _doc(total=1250000.0, pagado=1000000.0, pendiente=250000.0),
        'muchos_pagos': _doc(filas_pago=[
            _fila('paid', f'fecha {i}', f'Pago {i} de 12', 'Pagado', 1000.0)
            for i in range(1, 13)]),
        'muchos_conceptos': _doc(incluye=[f'Concepto número {i}' for i in range(40)]),
        'cuota_cancelada': _doc(filas_pago=[
            _fila('paid', '1 ene 2026', 'Pago 1 de 3', 'Pagado', 5000.0),
            _fila('cancelled', '1 mar 2026', 'Pago 2 de 3', 'Cancelado', 0.0),
            _fila('next', '1 jun 2026', 'Pago 3 de 3', 'Próximo pago', 5000.0)]),
    }
    for nombre, doc in casos.items():
        datos = render_invoice_pdf(doc, MARCAS['Astral Weddings'], simbolo='Q')
        assert datos[:4] == b'%PDF', f'el caso {nombre} no produjo un PDF'


def test_los_tokens_del_pdf_son_los_del_sistema_web():
    """pdf_document_system tiene que espejar _document_tokens.html. Si
    alguien cambia un color en un lado y no en el otro, esto lo caza."""
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(raiz, 'app.py'), encoding='utf-8') as fh:
        app = fh.read()
    bloque = app[app.index("        'primary': '#"):][:2500]

    equivalencias = [
        ('primary', 'primary'), ('primary_dark', 'primary_dark'),
        ('primary_soft', 'primary_soft'), ('surface_2', 'surface_2'),
        ('background', 'background'), ('text_primary', 'text'),
        ('text_secondary', 'text_secondary'), ('muted', 'muted'),
        ('border', 'border'), ('border_strong', 'border_strong'),
        ('success', 'success'), ('success_text', 'success_text'),
        ('danger', 'danger'), ('danger_text', 'danger_text'),
        ('warning', 'warning'),
    ]
    for clave_py, clave_pdf in equivalencias:
        m = re.search(rf"'{clave_py}': '(#[0-9A-Fa-f]{{6}})'", bloque)
        assert m, f'no se encontro {clave_py} en el theme de app.py'
        esperado = m.group(1).upper()
        real = COLOR[clave_pdf].hexval()[2:].upper()
        assert real == esperado[1:], \
            f'{clave_py}: la web usa {esperado} y el PDF #{real}'
