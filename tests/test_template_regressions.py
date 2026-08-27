"""Guardas contra bugs concretos que ya pasaron una vez en este proyecto,
para que no vuelvan a colarse sin que nadie se de cuenta.
"""
import os
import re

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'templates')

# {{ x|tojson }} genera comillas dobles (es JSON). Si se usa dentro de un
# atributo onclick="..." tambien delimitado por comillas dobles, el HTML se
# corta a la mitad y el boton queda sin funcion, en silencio (sin error de
# JS visible). Paso una vez con "Generar link de pago" en invoice_view.html.
#
# 21-ago-2026: volvio a pasar, y esta vez en un onchange= (el selector de rol
# de cliente en job_detail.html). El detector solo miraba onclick, asi que no
# lo vio. Ahora cubre CUALQUIER manejador inline on*=, que es donde puede
# aparecer el mismo error. Con comillas simples es seguro: el filtro tojson de
# Flask escapa la comilla simple como \u0027.
_BROKEN_ONCLICK = re.compile(r'\son[a-z]+="[^"]*\|\s*tojson')


def _all_template_files():
    for root, _dirs, files in os.walk(TEMPLATES_DIR):
        for name in files:
            if name.endswith('.html'):
                yield os.path.join(root, name)


def test_no_double_quoted_onclick_with_tojson():
    """onclick debe usar comillas simples cuando mete un valor |tojson
    adentro -- si no, la primera comilla de tojson corta el atributo."""
    offenders = []
    for path in _all_template_files():
        with open(path, encoding='utf-8') as f:
            content = f.read()
        if _BROKEN_ONCLICK.search(content):
            rel = os.path.relpath(path, TEMPLATES_DIR)
            offenders.append(rel)
    assert not offenders, (
        f'Estos templates tienen un manejador inline on*="..." con |tojson adentro (se rompe el HTML): {offenders}. '
        f'Usa onclick=\'...\' (comillas simples) en su lugar.'
    )
