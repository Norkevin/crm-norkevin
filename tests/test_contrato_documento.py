"""
CONTRATO: la plantilla se RENDERIZA, no solo se parsea  (3-sep-2026)

Por que existe este archivo:

Al numerar las clausulas y convertir en vinetas las lineas que empiezan con
"*", use `select('match', ...)`. Ese test NO existe en Jinja2 -- es de
Ansible. La plantilla PARSEA perfecto y revienta en tiempo de render con
"No test named 'match'": cualquier contrato con terminos habria devuelto un
500 al cliente en el momento de firmar.

verificacion_final.py dijo TODO VERDE porque solo llamaba a get_template()
(parseo). Un fallo de render no se ve ahi. Estos tests renderizan de verdad,
con la salida real de contract_terms(), que es lo unico que lo caza.

Corre sin Flask: se arma un Environment de Jinja con los mismos filtros que
registra app.py.
"""
import os
import re
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

jinja2 = pytest.importorskip('jinja2')

from src.pdf_generator import contract_terms  # noqa: E402

JOB = {
    'id': 'JOB-1', 'nombre': 'Boda Jimenez Cruz', 'boda_date': '2026-07-26',
    'price_total': 7500.0, 'plan_pago': 3, 'cuota_monto': 2500.0,
    'location': 'Antigua Guatemala',
}
CLIENTE = {'first_name': 'Juan Manuel', 'last_name': 'Jimenez Cruz',
           'email': 'juan@ejemplo.com', 'phone': '+502 5555 5555'}
MARCA = {'display_name': 'Astral Weddings', 'email': 'hola@astral.com',
         'phone': '+502 4444 4444'}


def _tema():
    """El tema por defecto sale de app.py sin importar Flask: se lee el
    literal del AST. Si alguien renombra o mueve el diccionario, el test
    falla en vez de pasar con un tema inventado."""
    import ast
    arbol = ast.parse(open(os.path.join(RAIZ, 'app.py'), encoding='utf-8').read())
    for n in ast.walk(arbol):
        if isinstance(n, ast.FunctionDef) and n.name == '_quote_theme_for_tenant':
            for sub in ast.walk(n):
                if isinstance(sub, ast.Dict):
                    try:
                        d = ast.literal_eval(sub)
                    except Exception:
                        continue
                    if isinstance(d, dict) and 'primary' in d:
                        d.update(MARCA)
                        d.setdefault('tagline', 'Photography')
                        d.setdefault('whatsapp', '')
                        return d
    raise AssertionError('no se encontro el tema por defecto en app.py')


def _render(contrato=None, terms=None):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(os.path.join(RAIZ, 'templates')),
        autoescape=jinja2.select_autoescape(['html']))
    # Los mismos filtros que app.py registra en el entorno de Flask.
    env.filters['fecha_es'] = lambda v: str(v or '')
    contrato = dict({'id': 'CTR-2026-0044', 'signed': False,
                     'photographer_signed': True,
                     'photographer_signed_at': '2026-01-15T10:00:00'},
                    **(contrato or {}))
    html = env.get_template('contract_view.html').render(
        theme=_tema(), terms=contract_terms(JOB, brand=MARCA) if terms is None else terms,
        contract=contrato, job=JOB, client=CLIENTE)
    # El CSS aparte: buscar "*" o numeros dentro del <style> da falsos
    # positivos (el CSS tiene selectores y tamanos).
    return html.split('</style>', 1)[-1]


def test_el_contrato_renderiza_de_verdad():
    """La prueba que faltaba. get_template() no ejecuta el cuerpo."""
    assert 'Astral Weddings' in _render()


def test_las_clausulas_se_numeran_con_indice_propio():
    cuerpo = _render()
    assert re.findall(r'term__num">(\d+)<', cuerpo) == ['1', '2', '3', '4', '5', '6']


def test_las_lineas_con_asterisco_salen_como_vinetas_no_como_texto():
    cuerpo = _render()
    assert '* Proporcionar' not in cuerpo, 'el asterisco crudo llego al cliente'
    items = re.findall(r'<li>([^<]+)</li>', cuerpo)
    # Clausulas 3 y 4: 4 items cada una.
    assert len(items) == 8, items
    assert 'Proporcionar informacion veraz y oportuna sobre el evento' in items
    assert not any(i.startswith('*') for i in items)


def test_una_clausula_de_parrafo_no_se_convierte_en_lista():
    cuerpo = _render()
    # La 5 es prosa: debe seguir siendo prosa.
    assert 'el deposito no sera reembolsable' in cuerpo
    assert cuerpo.count('<ul class="term__lista">') == 2


def test_una_clausula_con_entrada_y_items_conserva_las_dos_partes():
    """El bug que casi introduzco al arreglar el otro: si se filtra SOLO por
    los bullets, la frase de entrada desaparece del contrato firmado."""
    cuerpo = _render(terms=[('7. Entregables', 'El material se entrega asi:\n'
                                               '* Galeria online\n* USB fisico')])
    assert 'El material se entrega asi:' in cuerpo
    assert len(re.findall(r'<li>([^<]+)</li>', cuerpo)) == 2


def test_un_titulo_sin_numero_no_deja_sangria_vacia():
    cuerpo = _render(terms=[('Anexo', 'Texto suelto.')])
    assert 'term--sin-num' in cuerpo
    assert 'term__num' not in cuerpo


def test_el_input_de_firma_no_dispara_zoom_en_ios():
    """Safari hace zoom automatico en cualquier input < 16px. Pasa justo
    cuando el cliente escribe su nombre para firmar."""
    css = open(os.path.join(RAIZ, 'templates', 'contract_view.html'),
               encoding='utf-8').read().split('</style>')[0]
    m = re.search(r'\.sig-input\s*\{[^}]*font-size:\s*([\d.]+)px', css)
    assert m, 'no se encontro el tamano de .sig-input'
    assert float(m.group(1)) >= 16


def test_sobreviven_los_ganchos_de_la_firma():
    """El rediseno no puede romper el JS existente: si un id cambia, el
    boton de firmar deja de funcionar sin ningun error visible."""
    cuerpo = _render()
    for gancho in ('sig-modal-overlay', 'sig-canvas', 'sig-mode-draw',
                   'sig-mode-type', 'sig-draw-panel', 'sig-type-panel',
                   'typed-sig-name', 'typed-sig-preview'):
        assert f'id="{gancho}"' in cuerpo, gancho


def test_un_contrato_ya_firmado_no_muestra_el_formulario():
    cuerpo = _render({'signed': True, 'signed_at': '2026-02-01T09:00:00',
                      'client_signature_name': 'Juan Manuel Jimenez Cruz'})
    assert 'sig-modal-overlay' not in cuerpo


def test_sin_terminos_no_revienta():
    assert 'Firmas' in _render(terms=[])


def test_el_contrato_no_usa_ninguna_serif():
    """Kevin: 'NO QUIERO SERIF EN NINGUNA PARTE DEL SISTEMA DOCUMENTAL'."""
    css = open(os.path.join(RAIZ, 'templates', 'contract_view.html'),
               encoding='utf-8').read()
    for palabra in ('serif;', 'Georgia', 'Times', 'Playfair', 'var(--serif)'):
        assert palabra not in css.replace('sans-serif', ''), palabra


def test_ninguna_puerta_de_firma_pinta_fondo_blanco():
    """Kevin: 'se ve este recuadro feo con la firma'.

    La firma escrita se generaba con un fillRect blanco, asi que el PNG
    llegaba opaco y sobre la card gris parecia un recuadro pegado. Son TRES
    puertas -- contrato publico, portal del cliente y el CRM -- y arreglar
    solo una deja el problema entrando por las otras dos.
    """
    for plantilla in ('contract_view.html', 'client_portal.html', 'job_detail.html'):
        js = open(os.path.join(RAIZ, 'templates', plantilla), encoding='utf-8').read()
        for bloque in re.findall(r'function \w*[Tt]yped\w*\([^)]*\)\s*\{(.*?)\n\}', js, re.S):
            assert 'fillRect' not in bloque, f'{plantilla} pinta fondo en la firma'


def test_las_firmas_viejas_no_muestran_su_recuadro_en_la_web():
    """Las ya guardadas traen el blanco adentro y no se pueden reescribir
    sin tocar datos firmados: 'multiply' lo hace desaparecer."""
    css = open(os.path.join(RAIZ, 'templates', 'contract_view.html'),
               encoding='utf-8').read().split('</style>')[0]
    bloque = re.search(r'\.firma__trazo\s*\{([^}]*)\}', css)
    assert bloque and 'mix-blend-mode: multiply' in bloque.group(1)


def test_el_pdf_deja_la_firma_sin_fondo_en_las_dos_generaciones():
    """reportlab aplana el alfa sobre NEGRO, asi que una firma transparente
    sale como un rectangulo negro -- peor que el blanco. Las dos se aplanan
    sobre blanco antes de dibujar y se recortan por color."""
    Image = pytest.importorskip('PIL.Image')
    import io as _io
    from src.pdf_contract import _trazo_legible, _MASCARA_BLANCO

    for con_alfa in (True, False):
        base = Image.new('RGBA', (40, 20),
                         (0, 0, 0, 0) if con_alfa else (255, 255, 255, 255))
        base.putpixel((20, 10), (10, 14, 26, 255))  # el trazo
        buf = _io.BytesIO()
        base.save(buf, 'PNG')
        lector = _trazo_legible(buf.getvalue())
        assert lector is not None
        plano = lector._image
        assert plano.mode == 'RGB', f'quedo alfa (con_alfa={con_alfa})'
        fondo = plano.getpixel((0, 0))
        assert fondo == (255, 255, 255), f'el fondo no quedo blanco: {fondo}'
        assert fondo[0] >= _MASCARA_BLANCO[0], 'el fondo cae fuera de la mascara'
        assert plano.getpixel((20, 10))[0] < _MASCARA_BLANCO[0], \
            'el trazo cae DENTRO de la mascara y se borraria'


def test_una_firma_corrupta_no_tumba_la_descarga_del_contrato():
    """Devuelve None y el PDF dibuja la linea para firmar a mano."""
    from src.pdf_contract import _trazo_legible
    assert _trazo_legible(b'esto no es un png') is None


def test_la_web_y_el_pdf_parten_las_vinetas_igual():
    """Un item en la web tiene que ser un item en el PDF. Si divergen, el
    documento que el cliente lee y el que se archiva no dicen lo mismo."""
    from src.pdf_contract import _partir_titulo
    for titulo, cuerpo in contract_terms(JOB, brand=MARCA):
        num, limpio = _partir_titulo(titulo)
        partes = titulo.split('. ', 1)
        num_web = partes[0] if len(partes) == 2 and partes[0].isdigit() else ''
        assert num == num_web and limpio == (partes[1] if num_web else titulo)
        pdf_items = [l for l in cuerpo.split('\n') if l.strip().startswith('*')]
        web_items = [l for l in cuerpo.split('\n') if l.strip().startswith('*')]
        assert pdf_items == web_items
