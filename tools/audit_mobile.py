"""Auditor estatico de problemas de movil en las plantillas del CRM.

No reemplaza mirar el telefono, pero encuentra la clase de errores que se
detectan leyendo: anchos fijos mas grandes que la pantalla, tablas sin
tratamiento movil, texto que no puede cortarse, y areas de toque
demasiado chicas.

Uso:  python tools/audit_mobile.py
"""
import os
import re
import sys
from collections import defaultdict

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(RAIZ, 'templates')

# iPhone SE / iPhone 13 mini: el piso realista.
ANCHO_MIN = 375
# Recomendacion de area tactil (WCAG 2.5.5 / Apple HIG): 44px.
TOQUE_MIN = 44


def plantillas():
    for n in sorted(os.listdir(TPL)):
        if n.endswith('.html'):
            yield n, os.path.join(TPL, n)


def _sin_media_queries(css):
    """Devuelve el CSS que aplica SIEMPRE, quitando bloques @media.

    Un ancho fijo dentro de `@media (min-width: 992px)` no es un problema
    de movil; el mismo ancho fuera de cualquier media query si lo es.
    """
    salida = []
    i = 0
    while True:
        m = re.search(r'@media[^{]*\{', css[i:])
        if not m:
            salida.append(css[i:])
            break
        salida.append(css[i:i + m.start()])
        j = i + m.end()
        prof = 1
        while j < len(css) and prof:
            if css[j] == '{':
                prof += 1
            elif css[j] == '}':
                prof -= 1
            j += 1
        i = j
    return ''.join(salida)


def _css_movil_global():
    """El bloque movil de base.html, que heredan todas las plantillas hijas."""
    with open(os.path.join(TPL, 'base.html'), encoding='utf-8') as f:
        base = f.read()
    bloques = re.findall(r'@media \(max-width: 760px\)', base)
    return base if bloques else ''


_MOVIL_GLOBAL = None


def revisar(nombre, ruta):
    global _MOVIL_GLOBAL
    if _MOVIL_GLOBAL is None:
        _MOVIL_GLOBAL = _css_movil_global()
    with open(ruta, encoding='utf-8') as f:
        html = f.read()
    hereda_base = '{% extends' in html and 'base.html' in html
    hallazgos = []

    # --- 1. Anchos fijos que no caben en un telefono ---------------------
    css_bloques = re.findall(r'<style[^>]*>(.*?)</style>', html, re.S)
    css_siempre = _sin_media_queries('\n'.join(css_bloques))
    for prop in ('width', 'min-width'):
        for m in re.finditer(rf'(?<!-){prop}\s*:\s*(\d+)px', css_siempre):
            px = int(m.group(1))
            if px > ANCHO_MIN:
                sel = css_siempre[max(0, m.start() - 120):m.start()]
                sel = (sel.rsplit('}', 1)[-1].rsplit('{', 1)[0] or '?').strip()[-60:]
                hallazgos.append(('ancho_fijo',
                                  f'{prop}:{px}px en "{sel}" (pantalla minima {ANCHO_MIN}px)'))

    # Anchos fijos en atributos style= del propio HTML
    for m in re.finditer(r'style="[^"]*?(?<!-)(min-)?width:\s*(\d+)px', html):
        px = int(m.group(2))
        if px > ANCHO_MIN:
            linea = html[:m.start()].count('\n') + 1
            hallazgos.append(('ancho_fijo_inline', f'width:{px}px inline, linea {linea}'))

    # --- 2. Tablas sin tratamiento movil --------------------------------
    n_tablas = len(re.findall(r'<table', html))
    if n_tablas:
        tiene_stack = ('stack-mobile' in html or 'stack-full' in html
                       or 'table-responsive' in html)
        tiene_labels = 'data-label' in html
        # Las tablas de documento (cotizacion/contrato) usan otro sistema.
        es_doc = 'doc-table' in html
        if not (tiene_stack and tiene_labels) and not es_doc:
            hallazgos.append(('tabla_sin_movil',
                              f'{n_tablas} tabla(s) sin data-label/stack-mobile: '
                              'en el telefono queda scroll horizontal'))
        elif es_doc and 'doc-table' in html:
            hallazgos.append(('tabla_doc',
                              'usa .doc-table (pagina que ve el cliente): '
                              'verificar que apile en movil'))

    # --- 3. Texto que no puede cortarse ---------------------------------
    # Correos y telefonos largos revientan una tarjeta si no hay
    # overflow-wrap en algun lado.
    if (not hereda_base) and re.search(r'\.email|email\b', html) \
            and 'overflow-wrap' not in html and 'word-break' not in html \
            and 'sn-ellipsis' not in html:
        hallazgos.append(('sin_corte_texto',
                          'muestra correos sin overflow-wrap/word-break/sn-ellipsis'))

    # --- 4. Areas de toque chicas ---------------------------------------
    for m in re.finditer(r'(?:min-)?height\s*:\s*(\d+)px', css_siempre):
        px = int(m.group(1))
        if 0 < px < TOQUE_MIN:
            ctx = css_siempre[max(0, m.start() - 150):m.start()]
            sel = (ctx.rsplit('}', 1)[-1].rsplit('{', 1)[0] or '?').strip()[-50:]
            interactivo = re.search(r'btn|button|\.mail-tab|\.sn-tab\b|input|select|pagination|move-btn', sel, re.I)
            # El icono de adentro no es el area de toque; el contenedor si.
            es_icono = re.search(r'svg|\bicon\b|::before|count', sel, re.I)
            base_lo_cubre = hereda_base and re.search(
                r'min-height:\s*(4[0-9]|[5-9][0-9])px', _MOVIL_GLOBAL)
            propio = re.search(rf'{re.escape(sel.strip().split()[-1])}[^{{]*\{{[^}}]*min-height:\s*(3[6-9]|4[0-9])px', html)
            if interactivo and not es_icono and not propio:
                hallazgos.append(('toque_chico',
                                  f'height:{px}px en "{sel}" (minimo recomendado {TOQUE_MIN}px)'))

    # --- 5. Fuentes ilegibles -------------------------------------------
    for m in re.finditer(r'font-size\s*:\s*(\d+(?:\.\d+)?)px', css_siempre):
        px = float(m.group(1))
        if px < 10:
            ctx = css_siempre[max(0, m.start() - 130):m.start()]
            sel = (ctx.rsplit('}', 1)[-1].rsplit('{', 1)[0] or '?').strip()[-50:]
            hallazgos.append(('fuente_chica', f'font-size:{px}px en "{sel}"'))

    # --- 6. Modales con ancho fijo --------------------------------------
    for m in re.finditer(r'\.modal[^{]*\{([^}]*)\}', css_siempre):
        cuerpo = m.group(1)
        if re.search(r'(?<!max-)width\s*:\s*\d{3,}px', cuerpo) and 'max-width' not in cuerpo:
            hallazgos.append(('modal_fijo', 'modal con width fijo y sin max-width'))

    return hallazgos


def main():
    total = defaultdict(int)
    por_plantilla = {}
    for nombre, ruta in plantillas():
        h = revisar(nombre, ruta)
        if h:
            por_plantilla[nombre] = h
            for tipo, _ in h:
                total[tipo] += 1

    print('=' * 66)
    print('AUDITORIA DE MOVIL')
    print('=' * 66)
    for nombre in sorted(por_plantilla):
        print(f'\n{nombre}')
        vistos = set()
        for tipo, detalle in por_plantilla[nombre]:
            clave = (tipo, detalle[:45])
            if clave in vistos:
                continue
            vistos.add(clave)
            print(f'   [{tipo}] {detalle}')

    print('\n' + '=' * 66)
    print('RESUMEN')
    for tipo, n in sorted(total.items(), key=lambda x: -x[1]):
        print(f'   {tipo:<22} {n}')
    print(f'   {"plantillas afectadas":<22} {len(por_plantilla)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
