"""
SISTEMA DE DISENO DOCUMENTAL -- CAPA PDF  (3-sep-2026)

Este modulo es el hermano en PDF de templates/_document_base.html. Los dos
describen el MISMO sistema: mismos tokens, misma escala tipografica, mismas
proporciones, mismo tratamiento de totales y estados. La cotizacion web, la
factura web y la factura PDF tienen que leerse como tres vistas del mismo
producto, no como tres plantillas distintas.

Por que existe
--------------
El PDF no se genera desde el HTML: lo dibuja reportlab con un canvas. Eso
significa que no hereda NADA del CSS, y es exactamente por eso que el PDF se
quedo atras cada vez que la web mejoro. La unica forma de que no vuelva a
pasar es que ambos lean los mismos valores desde un solo lugar. Este modulo
es ese lugar del lado PDF, y cada token trae anotado su equivalente CSS.

Fuente
------
reportlab solo trae las 14 fuentes base del formato PDF (Helvetica, Times,
Courier). Ninguna es Inter. `registrar_fuente_documental()` busca los .ttf
de Inter en static/fonts/ y los registra si estan; si no, cae a Helvetica.
Helvetica NO es serif -- es la misma familia grotesca que Inter -- asi que
el documento nunca se ve como una plantilla vieja, pero para que coincida
de verdad con la web hay que dejar los archivos en su sitio (ver el README
que escribe `instrucciones_fuente()`).
"""
import os
from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ============================================================
# TOKENS -- espejo de templates/_document_tokens.html, que a su vez
# espeja los --sn-* de templates/base.html. Si cambia uno, cambian todos.
# ============================================================
COLOR = {
    'primary':        HexColor('#7357F6'),  # --sn-green   / --primary
    'primary_dark':   HexColor('#6447EE'),  # --sn-green-dark
    'primary_soft':   HexColor('#F0EDFF'),  # --sn-mint    / --primary-soft
    'surface':        HexColor('#FFFFFF'),  # --surface
    'surface_2':      HexColor('#F4F5F9'),  # --sn-surface-2
    'background':     HexColor('#F7F8FC'),  # --sn-canvas
    'text':           HexColor('#111827'),  # --sn-ink     / --text-primary
    'text_secondary': HexColor('#667085'),  # --sn-muted   / --text-secondary
    'muted':          HexColor('#98A2B3'),  # --sn-soft    / --muted
    'border':         HexColor('#E7EAF0'),  # --sn-line    / --border
    'border_strong':  HexColor('#D7DCE5'),  # --sn-line-dark
    # Estados: el color de superficie y el de texto son distintos a
    # proposito. #2FB66D sobre papel blanco da 2.6:1 y un PDF se imprime.
    'success':        HexColor('#2FB66D'),
    'success_soft':   HexColor('#EAF8F0'),
    'success_text':   HexColor('#158048'),
    'warning':        HexColor('#F59E0B'),
    'warning_soft':   HexColor('#FFF5DF'),
    'warning_text':   HexColor('#97620C'),
    'danger':         HexColor('#EF5B5B'),
    'danger_soft':    HexColor('#FEEEEE'),
    'danger_text':    HexColor('#C93636'),
    'white':          HexColor('#FFFFFF'),
}

# Escala tipografica en puntos. La web usa px sobre una base de 14; el PDF
# usa pt sobre una base de 9.5, que es la proporcion que mantiene el mismo
# color de pagina al imprimir en A4.
#   doc-h1 (26-33px)  -> titulo    18pt
#   opt-name (20-25)  -> subtitulo 14pt
#   doc-h2 (17-19)    -> seccion   11pt
#   monto (22-30)     -> cifra     17pt
#   contenido (14.5)  -> cuerpo     9.5pt
#   secundario (13.5) -> apoyo      8.5pt
#   label (11)        -> label      7.5pt
TIPO = {
    'titulo':    18,
    'subtitulo': 14,
    'cifra':     17,
    'seccion':   11,
    'cuerpo':    9.5,
    'apoyo':     8.5,
    'label':     7.5,
    'micro':     7,
}

# Espaciado en multiplos de 4, igual que la web.
ESP = {n: n * mm / 4 for n in (4, 8, 12, 16, 24, 32, 40, 48)}

RADIO = 3.5 * mm          # ~14px, el --radius-lg del sistema
MARGEN = 16 * mm          # margen de pagina
LINEA_FINA = 0.5          # grosor de las reglas


# ============================================================
# FUENTES
# ============================================================
_DIR_FUENTES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'static', 'fonts')

# (nombre a registrar, archivo esperado, fallback de las 14 base del PDF)
_FAMILIA = [
    ('Doc',          'Inter-Regular.ttf',  'Helvetica'),
    ('Doc-Medium',   'Inter-Medium.ttf',   'Helvetica'),
    ('Doc-Bold',     'Inter-SemiBold.ttf', 'Helvetica-Bold'),
]

_resuelta = None


def registrar_fuente_documental():
    """Registra Inter si los .ttf estan disponibles; si no, usa Helvetica.

    Devuelve un dict {rol: nombre_de_fuente} listo para `canvas.setFont()`.
    El resultado se cachea: registrar dos veces la misma fuente en reportlab
    no rompe, pero leer los TTF en cada PDF si costaria.

    NUNCA cae a una serif. El fallback es Helvetica, que es grotesca como
    Inter: si faltan los archivos el documento se ve un poco menos afinado,
    pero jamas como una plantilla de procesador de texto.
    """
    global _resuelta
    if _resuelta is not None:
        return _resuelta

    fuentes = {}
    for nombre, archivo, fallback in _FAMILIA:
        ruta = os.path.join(_DIR_FUENTES, archivo)
        usada = fallback
        if os.path.isfile(ruta):
            try:
                pdfmetrics.registerFont(TTFont(nombre, ruta))
                usada = nombre
            except Exception:
                # Un TTF corrupto no puede tumbar la descarga de una factura.
                usada = fallback
        fuentes[nombre] = usada

    _resuelta = {
        'regular': fuentes['Doc'],
        'medium':  fuentes['Doc-Medium'],
        'bold':    fuentes['Doc-Bold'],
        'es_inter': fuentes['Doc'] == 'Doc',
    }
    return _resuelta


def instrucciones_fuente():
    """Texto para el README de static/fonts/. Se usa en el reporte de entrega."""
    return (
        'Para que el PDF use exactamente la misma tipografia que la web,\n'
        'dejar estos tres archivos en static/fonts/:\n'
        '  Inter-Regular.ttf\n'
        '  Inter-Medium.ttf\n'
        '  Inter-SemiBold.ttf\n'
        'Se descargan de https://fonts.google.com/specimen/Inter (Get font ->\n'
        'Download all) o de https://github.com/rsms/inter/releases.\n'
        'No hay que tocar codigo: si los archivos estan, se registran solos;\n'
        'si no, el PDF usa Helvetica, que tambien es sans y se ve limpia.'
    )


# ============================================================
# PRIMITIVAS DE DIBUJO -- equivalen a los componentes del CSS
# ============================================================
def texto(c, x, y, cadena, *, tam='cuerpo', peso='regular', color='text', ancla='left'):
    """Escribe una linea. `tam` es una clave de TIPO, `color` una de COLOR."""
    f = registrar_fuente_documental()
    c.setFont(f[peso], TIPO[tam] if isinstance(tam, str) else tam)
    c.setFillColor(COLOR[color] if isinstance(color, str) else color)
    if ancla == 'right':
        c.drawRightString(x, y, cadena or '')
    elif ancla == 'center':
        c.drawCentredString(x, y, cadena or '')
    else:
        c.drawString(x, y, cadena or '')


def label(c, x, y, cadena, ancla='left'):
    """document-label: 7.5pt, medium, color secundario, mayusculas suaves."""
    texto(c, x, y, (cadena or '').upper(), tam='label', peso='medium',
          color='text_secondary', ancla=ancla)


def regla(c, x1, y, x2, *, color='border', grosor=LINEA_FINA):
    c.setStrokeColor(COLOR[color] if isinstance(color, str) else color)
    c.setLineWidth(grosor)
    c.line(x1, y, x2, y)


def card(c, x, y, ancho, alto, *, fondo='surface', borde='border', radio=RADIO):
    """document-section: rectangulo redondeado con borde de 1px muy suave.

    No lleva sombra: en papel una sombra se imprime como una mancha gris.
    El sistema web usa sombra minima justamente para que su ausencia aca no
    cambie la lectura del documento.
    """
    if fondo:
        c.setFillColor(COLOR[fondo] if isinstance(fondo, str) else fondo)
    if borde:
        c.setStrokeColor(COLOR[borde] if isinstance(borde, str) else borde)
        c.setLineWidth(LINEA_FINA)
    c.roundRect(x, y, ancho, alto, radio, fill=bool(fondo), stroke=bool(borde))


def badge(c, x, y, etiqueta, tono='neutral'):
    """document-status-badge: pildora con fondo tenue y texto del mismo tono.

    Devuelve el ancho ocupado, para poder encadenar. Sin mayusculas
    forzadas ni tracking, igual que .badge en el CSS.
    """
    mapa = {
        'neutral': ('surface_2', 'text_secondary'),
        'info':    ('primary_soft', 'primary_dark'),
        'success': ('success_soft', 'success_text'),
        'warning': ('warning_soft', 'warning_text'),
        'danger':  ('danger_soft', 'danger_text'),
    }
    fondo, tinta = mapa.get(tono, mapa['neutral'])
    f = registrar_fuente_documental()
    tam = TIPO['apoyo']
    ancho_txt = pdfmetrics.stringWidth(etiqueta, f['medium'], tam)
    pad = 2.4 * mm
    ancho = ancho_txt + pad * 2
    alto = 5.4 * mm
    c.setFillColor(COLOR[fondo])
    c.roundRect(x, y - 1.4 * mm, ancho, alto, alto / 2, fill=True, stroke=False)
    texto(c, x + pad, y, etiqueta, tam='apoyo', peso='medium', color=tinta)
    return ancho


def marcador(c, x, y, estado):
    """Marcador del calendario de pagos: circulo de 3.4mm con glifo blanco.

    Mismos estados y colores que .timeline__dot. Usa las variantes de TEXTO
    porque el glifo va en blanco encima.
    """
    tonos = {
        'paid': ('success_text', '✓'),
        'due': ('danger_text', '!'),
        'next': ('primary', '●'),
        'cancelled': ('muted', '×'),
    }
    color, glifo = tonos.get(estado, ('muted', '·'))
    r = 1.7 * mm
    c.setFillColor(COLOR[color])
    c.circle(x + r, y + r - 0.6 * mm, r, fill=True, stroke=False)
    f = registrar_fuente_documental()
    c.setFont(f['bold'], 6)
    c.setFillColor(COLOR['white'])
    c.drawCentredString(x + r, y + r - 2.2 * mm, glifo)
    return r * 2


def recortar(cadena, ancho_max, *, tam='cuerpo', peso='regular'):
    """Recorta con elipsis para que un nombre largo no invada la columna
    vecina. El PDF no tiene overflow: lo que no cabe, pisa."""
    f = registrar_fuente_documental()
    pt = TIPO[tam] if isinstance(tam, str) else tam
    cadena = cadena or ''
    if pdfmetrics.stringWidth(cadena, f[peso], pt) <= ancho_max:
        return cadena
    while cadena and pdfmetrics.stringWidth(cadena + '…', f[peso], pt) > ancho_max:
        cadena = cadena[:-1]
    return (cadena.rstrip() + '…') if cadena else ''


def envolver(cadena, ancho_max, *, tam='cuerpo', peso='regular', max_lineas=None):
    """Parte en lineas midiendo el ancho REAL de la fuente.

    textwrap corta por numero de caracteres, que con una proporcional deja
    unas lineas cortas y otras desbordadas. Esto mide de verdad.
    """
    f = registrar_fuente_documental()
    pt = TIPO[tam] if isinstance(tam, str) else tam
    palabras = (cadena or '').split()
    lineas, actual = [], ''
    for p in palabras:
        prueba = f'{actual} {p}'.strip()
        if pdfmetrics.stringWidth(prueba, f[peso], pt) <= ancho_max:
            actual = prueba
        else:
            if actual:
                lineas.append(actual)
            actual = p
    if actual:
        lineas.append(actual)
    if max_lineas and len(lineas) > max_lineas:
        lineas = lineas[:max_lineas]
        lineas[-1] = recortar(lineas[-1] + ' …', ancho_max, tam=tam, peso=peso)
    return lineas or ['']


def moneda(valor, simbolo='Q'):
    """Formato unico para todo el sistema: Q25,125.00. Igual que la web."""
    try:
        return f'{simbolo}{float(valor or 0):,.2f}'
    except (TypeError, ValueError):
        return f'{simbolo}0.00'
