"""Guardas de movil: que lo arreglado no se vuelva a romper.

Kevin usa el CRM desde el telefono durante los eventos, y los clientes
abren cotizaciones, contratos y cuestionarios desde WhatsApp. Estas
comprobaciones son estaticas (leen el CSS y el HTML), asi que corren en
cualquier lado y no dependen de un navegador.

No reemplazan mirar el telefono. Cubren la clase de errores que se puede
detectar leyendo, que es justo la que se cuela sin que nadie la note.
"""
import os
import re

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(RAIZ, 'templates')

# Paginas que abre el CLIENTE, no Kevin. Son las que mas se ven en movil.
PAGINAS_CLIENTE = [
    'quote_view.html', 'contract_view.html', 'questionnaire_view.html',
    'captacion.html', 'client_portal.html',
]


def _leer(nombre):
    with open(os.path.join(TPL, nombre), encoding='utf-8') as f:
        return f.read()


def _css(nombre):
    return '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>', _leer(nombre), re.S))


def _bloques_movil(css):
    """Contenido de todos los @media (max-width: <=760px) del CSS."""
    salida = []
    for m in re.finditer(r'@media\s*\(max-width:\s*(\d+)px\)\s*\{', css):
        if int(m.group(1)) > 760:
            continue
        j = m.end()
        prof = 1
        while j < len(css) and prof:
            if css[j] == '{':
                prof += 1
            elif css[j] == '}':
                prof -= 1
            j += 1
        salida.append(css[m.end():j])
    return '\n'.join(salida)


# ============================================================
# 1. Auto-zoom de iOS
# ============================================================

def test_los_inputs_del_crm_no_provocan_zoom_en_iphone():
    """Safari hace zoom automatico al enfocar un input de menos de 16px.
    Pasaba en TODOS los formularios y en el buscador: se enfocaba el
    campo, la pagina saltaba, y habia que alejar con dos dedos."""
    movil = _bloques_movil(_css('base.html'))
    assert re.search(r'\.form-input[^{]*\{[^}]*font-size:\s*16px', movil, re.S) \
        or re.search(r'font-size:\s*16px', movil), \
        'base.html no fija font-size:16px para los inputs en movil'


@pytest.mark.parametrize('pagina', PAGINAS_CLIENTE)
def test_las_paginas_del_cliente_tampoco_hacen_zoom(pagina):
    """Estas paginas no heredan base.html: necesitan su propia regla."""
    css = _css(pagina)
    if 'input' not in _leer(pagina) and 'select' not in _leer(pagina):
        pytest.skip(f'{pagina} no tiene campos de formulario')
    movil = _bloques_movil(css)
    if not movil and '_document_style' in _leer(pagina):
        movil = _bloques_movil(_css('_document_style.html'))
    assert 'font-size: 16px' in movil or 'font-size:16px' in movil, \
        f'{pagina} deja los inputs abajo de 16px en movil (zoom automatico)'


def test_el_editor_de_cotizaciones_tampoco_hace_zoom():
    """quote_edit.html no es una pagina de cliente (no esta en
    PAGINAS_CLIENTE), pero Kevin arma/edita cotizaciones desde el telefono
    durante los eventos igual que el resto del CRM -- sus campos de opcion
    (nombre, subtitulo, precio, horas, grupos...) tenian font-size: 14px
    fijo, sin condicionar a movil, hasta la revision de BLOQUE H."""
    css = _css('quote_edit.html')
    assert re.search(r'\.opt-field input,\s*\.opt-field textarea\s*\{[^}]*font-size:\s*16px', css, re.S), \
        'los inputs del editor de opciones siguen abajo de 16px (zoom automatico en iOS)'


# ============================================================
# 2. Areas tactiles
# ============================================================

def test_los_botones_chicos_se_pueden_tocar_con_el_dedo():
    """.sn-btn-sm media 25px. Es lo que usa la ficha del job para el rol
    del cliente, Quitar, Editar y + Agregar: los botones que Kevin toca
    con una mano mientras sostiene una camara con la otra."""
    movil = _bloques_movil(_css('base.html'))
    m = re.search(r'\.sn-btn-sm[^{]*\{([^}]*)\}', movil, re.S)
    assert m, 'el bloque movil no toca .sn-btn-sm'
    alto = re.search(r'min-height:\s*(\d+)px', m.group(1))
    assert alto and int(alto.group(1)) >= 36, \
        f'.sn-btn-sm sigue siendo chico en movil: {m.group(1).strip()}'


def test_los_botones_normales_llegan_a_44px():
    movil = _bloques_movil(_css('base.html'))
    m = re.search(r'\.sn-btn,\s*\n\s*\.btn\s*\{([^}]*)\}', movil, re.S)
    assert m, 'el bloque movil no toca .sn-btn'
    alto = re.search(r'min-height:\s*(\d+)px', m.group(1))
    assert alto and int(alto.group(1)) >= 44, '.sn-btn no llega a 44px en movil'


def test_los_radio_del_cuestionario_son_tocables():
    """El cuestionario lo contesta el cliente desde el telefono. Un radio
    nativo mide 18px: si no se agranda, se falla el toque y la gente
    abandona el formulario."""
    movil = _bloques_movil(_css('_document_style.html'))
    m = re.search(r'input\[type="radio"\][^{]*\{([^}]*)\}', movil, re.S)
    assert m, '_document_style.html no agranda los radio en movil'
    ancho = re.search(r'width:\s*(\d+)px', m.group(1))
    assert ancho and int(ancho.group(1)) >= 22, 'los radio siguen chicos en movil'


# ============================================================
# 3. Tablas
# ============================================================

def _tablas_sin_apilado():
    ofensores = []
    for nombre in sorted(os.listdir(TPL)):
        if not nombre.endswith('.html'):
            continue
        html = _leer(nombre)
        if '<table' not in html:
            continue
        apila = any(c in html for c in ('stack-mobile', 'table-responsive',
                                        'doc-table', 'dashboard-jobs-table'))
        if not (apila and 'data-label' in html):
            ofensores.append(nombre)
    return ofensores


def test_ninguna_tabla_queda_con_scroll_horizontal_en_movil():
    """Una tabla de 5 o 7 columnas en 375px obliga a arrastrar de lado.
    El patron del repo es apilar cada fila como ficha etiqueta/valor,
    con las etiquetas en data-label."""
    ofensores = _tablas_sin_apilado()
    assert not ofensores, (
        f'Estas plantillas tienen tablas sin apilado movil: {ofensores}. '
        'Agrega data-label a cada <td> y una clase que apile '
        '(.table-responsive, .stack-mobile o una regla propia).'
    )


def test_la_cotizacion_del_cliente_apila_su_plan_de_pago():
    """La cotizacion llega por WhatsApp y se abre en el telefono. El plan de
    pago no puede depender de una tabla ancha que se corte.

    BLOQUE C (Public Quote Experience) rediseño esta pagina: el plan de
    pago ya no es un <table class="doc-table"> de 4 columnas con celdas
    data-label, sino filas flex (.extra-row, una por cuota) que apilan
    solas en cualquier ancho porque nunca fueron una grilla de columnas.
    .doc-table sigue existiendo y sigue necesitando apilarse (lo usan
    contract_view.html y questionnaire_view.html), asi que esa guarda se
    mantiene; lo que ya no aplica es exigirle data-label a quote_view.html
    en particular."""
    movil = _bloques_movil(_css('_document_style.html'))
    assert '.doc-table thead' in movil and 'display: none' in movil, \
        '.doc-table no apila en movil'
    assert re.search(r'\.doc-table td\[data-label\]::before', movil), \
        '.doc-table no pinta las etiquetas al apilar'
    quote_html = _leer('quote_view.html')
    assert '<table' not in quote_html, \
        'quote_view.html volvio a depender de una tabla ancha para el plan de pago'


def test_el_dashboard_no_obliga_a_arrastrar_de_lado():
    movil = _bloques_movil(_css('dashboard.html'))
    assert '.dashboard-jobs-table thead' in movil, \
        'la tabla del dashboard no apila en movil'
    assert 'overflow-x: visible' in movil, \
        'el wrap del dashboard sigue forzando scroll horizontal'


# ============================================================
# 4. Legibilidad
# ============================================================

def test_no_queda_texto_abajo_de_10px_en_movil():
    """9px no se lee en un telefono. Las etiquetas del calendario, que es
    lo que Kevin mira entre evento y evento, estaban en 9 y 9.5px."""
    ofensores = []
    for nombre in sorted(os.listdir(TPL)):
        if not nombre.endswith('.html'):
            continue
        movil = _bloques_movil(_css(nombre))
        for m in re.finditer(r'font-size:\s*(\d+(?:\.\d+)?)px', movil):
            if float(m.group(1)) < 10:
                ctx = movil[max(0, m.start() - 90):m.start()]
                sel = (ctx.rsplit('}', 1)[-1].rsplit('{', 1)[0] or '?').strip()[-40:]
                ofensores.append(f'{nombre}: {m.group(1)}px en "{sel}"')
    assert not ofensores, f'Texto ilegible en movil: {ofensores}'


def test_el_texto_largo_no_empuja_la_pantalla():
    """Un correo largo o un telefono con formato sacaban la fila del
    viewport en vez de cortarse."""
    movil = _bloques_movil(_css('base.html'))
    assert 'overflow-wrap: anywhere' in movil, \
        'base.html no permite cortar texto largo en movil'
