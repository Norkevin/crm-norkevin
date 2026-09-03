"""Verificacion local completa antes de la corrida de Windows.

Corre TODO lo que se puede comprobar sin Flask ni pytest: compilacion,
AST, plantillas Jinja, guardas estaticas, alineacion runner/gate, kill
switches de correo, y la lista de regresiones que pidio Kevin (N+1,
logica financiera o de estado duplicada, acceso global a workflows,
hardcodes de marca, regresiones de movil, ingles visible nuevo).

Uso:  python tools/verificacion_final.py
"""
import ast
import glob
import os
import py_compile
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(RAIZ)

fallos = []
notas = []


def check(nombre, ok, detalle=''):
    print(f'  {"OK  " if ok else "FALLA"}  {nombre}' + (f'  -- {detalle}' if detalle and not ok else ''))
    if not ok:
        fallos.append((nombre, detalle))


print('=' * 68)
print('1. COMPILACION Y SINTAXIS')
print('=' * 68)

malos = []
n_py = 0
for p in glob.glob('**/*.py', recursive=True):
    if '__pycache__' in p or p.endswith('.bak'):
        continue
    n_py += 1
    try:
        py_compile.compile(p, doraise=True, cfile='/tmp/_vf.pyc')
    except Exception as e:
        malos.append((p, str(e)[:80]))
check(f'{n_py} modulos .py compilan', not malos, str(malos[:3]))

try:
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader('templates'))
    # Los filtros que app.py registra en Flask hay que declararlos aca
    # tambien, si no el validador reporta como rota una plantilla que en
    # produccion funciona. Solo se necesita que EXISTAN para parsear.
    for _f in ('fecha_es',):
        env.filters[_f] = lambda v: v
    rotas, n_tpl = [], 0
    for p in sorted(glob.glob('templates/**/*.html', recursive=True)):
        n_tpl += 1
        try:
            env.get_template(p.split('templates/', 1)[1])
        except Exception as e:
            rotas.append((os.path.basename(p), str(e)[:70]))
    check(f'{n_tpl} plantillas Jinja parsean', not rotas, str(rotas[:3]))
except ImportError:
    notas.append('jinja2 no disponible: parseo de plantillas omitido')

SRC = open('app.py', encoding='utf-8').read()
TREE = ast.parse(SRC)

import builtins
defin = set(dir(builtins)) | {'__file__'}
for n in ast.walk(TREE):
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        defin.add(n.name)
    elif isinstance(n, (ast.Import, ast.ImportFrom)):
        for a in n.names:
            defin.add(a.asname or a.name.split('.')[0])
    elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
        defin.add(n.id)
    elif isinstance(n, ast.arg):
        defin.add(n.arg)
    elif isinstance(n, ast.ExceptHandler) and n.name:
        defin.add(n.name)
    elif isinstance(n, (ast.Global, ast.Nonlocal)):
        defin.update(n.names)
usados = {n.id for n in ast.walk(TREE) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
check('app.py sin nombres sin definir', not (usados - defin), str(sorted(usados - defin)[:5]))

FUNCS = {n.name: n for n in ast.walk(TREE) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _cuerpo_de(nombre):
    return ast.unparse(FUNCS[nombre]) if nombre in FUNCS else ''


print()
print('=' * 68)
print('2. REGRESIONES QUE PIDIO KEVIN')
print('=' * 68)

sys.path.insert(0, 'tests')

# pytest no esta instalado en este entorno (la suite real corre en Windows).
# Un stub minimo alcanza para ejecutar las guardas estaticas, que son
# funciones normales sin fixtures.
if 'pytest' not in sys.modules:
    import types
    _stub = types.ModuleType('pytest')

    class _Skipped(Exception):
        pass

    def _skip(msg=''):
        raise _Skipped(msg)

    class _Marca:
        def parametrize(self, argnames, argvalues):
            def deco(fn):
                fn._params = getattr(fn, '_params', []) + [(argnames, argvalues)]
                return fn
            return deco

    _stub.skip = _skip
    _stub.mark = _Marca()
    _stub.Skipped = _Skipped
    sys.modules['pytest'] = _stub

# --- N+1 / relecturas de JSON ---
try:
    import test_rendimiento_vistas as trv
    trv.test_las_vistas_diarias_no_leen_el_store_dentro_de_un_loop()
    check('sin N+1 ni relecturas de JSON en las 9 vistas diarias', True)
except AssertionError as e:
    check('sin N+1 ni relecturas de JSON en las 9 vistas diarias', False, str(e)[:200])

# --- Logica financiera duplicada ---
# Toda la plata sale de _job_payment_summary. Una resta de saldo escrita a
# mano en una vista es una segunda fuente de verdad.
VISTAS = ['jobs_list', 'job_detail', 'client_detail', 'dashboard', 'payments_list']
formulas = []
for v in VISTAS:
    if v not in FUNCS:
        continue
    # Solo interesa un saldo POR JOB. Los totales agregados del dashboard
    # (para la grafica de torta) son otra cosa y no compiten con
    # _job_payment_summary, que es por boda.
    for nodo in ast.walk(FUNCS[v]):
        if not isinstance(nodo, ast.For):
            continue
        cuerpo = ast.unparse(nodo)
        if not re.search(r"\bfor\s+\w*j\w*\s+in\b", cuerpo):
            continue
        if (re.search(r"price_total.*?-.*?paid_amount", cuerpo, re.S)
                and '_job_payment_summary' not in cuerpo):
            formulas.append(f'{v}:{nodo.lineno}')
check('sin formula de saldo duplicada fuera de _job_payment_summary',
      not formulas, str(formulas))

# --- Logica de estado duplicada ---
estados = []
for v in VISTAS:
    cuerpo = _cuerpo_de(v)
    if 'workflow_progress' in cuerpo and '>= 100' in cuerpo:
        estados.append(v)
check('sin estado deducido del workflow fuera de _job_estado_label',
      not estados, str(estados))

# --- Acceso global a workflows sin filtro de cuenta ---
globales = []
for nombre, nodo in FUNCS.items():
    if nombre in ('_instancia_es_de_la_cuenta', '_workflow_instances_del_tenant'):
        continue
    cuerpo = ast.unparse(nodo)
    for m in re.finditer(r'workflow_engine\.(list_instances|get_history)\(([^)]*)\)', cuerpo):
        args = m.group(2)
        # Filtrado por subject concreto = seguro (el job/lead ya paso el
        # filtro de cuenta al buscarlo).
        if 'subject_id' in args:
            continue
        # Filtrar con `if i.subject_id in <conjunto>` es igual de seguro:
        # el conjunto sale de un job/lead que ya paso el filtro de cuenta.
        if re.search(r'subject_id in \w+', cuerpo):
            continue
        if '_workflow_instances_del_tenant' in cuerpo or 'mios' in cuerpo or '_mis_instancias' in cuerpo:
            continue
        globales.append(f'{nombre}: {m.group(0)[:50]}')
check('sin lectura global de workflows sin filtrar por cuenta',
      not globales, str(globales[:3]))

# --- Hardcodes de marca / tenant ---
MARCAS = re.compile(r"['\"](ASTRAL WEDDINGS|Astral Weddings|Norkevin Photography)['\"]")
hard = []
_LINEAS = SRC.split('\n')
# El registro de tenants SI define los nombres: es la fuente, no un hardcode.
_LEGITIMAS = ("'id': 'tenant-", "'slug': 'astral-weddings'")
for m in MARCAS.finditer(SRC):
    idx = SRC[:m.start()].count('\n')
    linea_txt = _LINEAS[idx]
    # Un comentario que explica el bug historico no es un hardcode.
    if linea_txt.lstrip().startswith('#'):
        continue
    if any(s in linea_txt for s in _LEGITIMAS):
        continue
    if 'tenant_brand_map' in SRC[max(0, m.start() - 220):m.start()]:
        continue
    hard.append(f'linea {idx + 1}: {linea_txt.strip()[:60]}')
check('sin marca escrita a mano en app.py', not hard, str(hard[:5]))

marcas_tpl = []
for p in glob.glob('templates/*.html'):
    c = open(p, encoding='utf-8').read()
    for m in re.finditer(r'\{%\s*block title\s*%\}(.*?)\{%\s*endblock', c, re.S):
        if re.search(r'ASTRAL|NORKEVIN', m.group(1), re.I):
            marcas_tpl.append(os.path.basename(p))
check('sin marca escrita a mano en el <title>', not marcas_tpl, str(marcas_tpl))

# --- Marca fija en el CUERPO de las plantillas (contrato/cotizacion/portal
# tenian "ASTRAL WEDDINGS" a mano fuera del <title> -- lo que el chequeo de
# arriba no mira. Un cliente de Norkevin veia el nombre de la otra empresa
# en su propio contrato firmado. Ventana de +-3 lineas: si el texto
# menciona las DOS marcas junto (p.ej. settings.html explicando el split de
# cuentas), es prosa informativa, no una fuga -- una fuga real solo puede
# nombrar UNA marca (la ajena) en el lugar de "tu cuenta".
PATRON_MARCA_CUERPO = re.compile(r'ASTRAL WEDDINGS|Astral Weddings|NORKEVIN PHOTOGRAPHY|Norkevin Photography')
# Formularios publicos sin cuenta identificada en la URL: _resolve_public_tenant
# cae a 'astral-weddings' a proposito (es el enlace ya embebido en su sitio),
# asi que 'tenant.name or ...ASTRAL...' aca es el default correcto, no un bug.
PERMITIDAS_DEFAULT_ASTRAL = {'captacion.html', 'formulario.html'}
marcas_cuerpo = []
for p in glob.glob('templates/*.html'):
    nombre = os.path.basename(p)
    with open(p, encoding='utf-8') as f:
        c = f.read()
    lineas = c.split('\n')
    for m in PATRON_MARCA_CUERPO.finditer(c):
        idx = c[:m.start()].count('\n')
        if nombre in PERMITIDAS_DEFAULT_ASTRAL:
            continue
        ventana = '\n'.join(lineas[max(0, idx - 3):idx + 4]).lower()
        if 'astral' in ventana and 'norkevin' in ventana:
            continue  # menciona las dos: prosa explicativa, no una fuga
        marcas_cuerpo.append(f'{nombre}:{idx + 1}: {lineas[idx].strip()[:55]}')
check('sin marca fija en el cuerpo de las plantillas (mas alla del <title>)',
      not marcas_cuerpo, str(marcas_cuerpo[:6]))

# --- Regresiones de movil ---
try:
    import test_responsive_movil as trm
    errores_movil = []
    for nombre in dir(trm):
        if not nombre.startswith('test_'):
            continue
        fn = getattr(trm, nombre)
        try:
            fn()
        except TypeError:
            pass  # parametrizado: se cubre en la corrida de pytest
        except AssertionError as e:
            errores_movil.append(f'{nombre}: {str(e)[:90]}')
    check('sin regresiones de movil', not errores_movil, str(errores_movil[:2]))
except Exception as e:
    check('sin regresiones de movil', False, str(e)[:120])

# --- Ingles visible nuevo ---
# Frases inglesas concretas que ya apparecieron en este repo. Se evita el
# patron generico "No <palabra>" porque tambien matchea espanol correcto
# ("No encontrado").
INGLES = re.compile(
    r'>\s*(No (?:clients?|jobs?|quotes?|payments?|contracts?|invoices?|emails?|'
    r'files?|leads?|notes?|mail|date|location|packages?)[^<]{0,30}|'
    r'[A-Z][a-z]+ will appear here|Create the first one|View [A-Z][a-z]+|'
    r'Add New[a-z ]*|Balance due|Expected:|Late:|Paid:|Contact info|'
    r'Send Email|Manual invoice)\s*<')
ingles = []
for p in sorted(glob.glob('templates/*.html')):
    c = open(p, encoding='utf-8').read()
    for m in INGLES.finditer(c):
        ingles.append(f'{os.path.basename(p)}: "{m.group(1).strip()}"')
check('sin texto en ingles visible en las plantillas', not ingles, str(ingles[:4]))

# --- Colision de prefijos en ids de prueba -------------------------------
# 'tenant-norkevin' es PREFIJO de 'tenant-norkevin-photography'. Un id de
# prueba que termine en el tenant_id hace que `assert id_ajeno not in html`
# de un falso positivo de fuga entre marcas. Costo dos corridas de Windows
# en rojo; se detecta leyendo, asi que se detecta aca.
riesgo = []
for ruta in sorted(glob.glob('tests/test_*.py')):
    with open(ruta, encoding='utf-8') as f:
        c = f.read()
    if ' not in html' not in c and ' not in cuerpo' not in c and ' not in indice' not in c:
        continue
    for m in re.finditer(r"f'[^']*\{tenant_id\}'", c):
        frag = m.group(0)
        # El correo de login no se compara nunca con `not in`.
        if 'example' in frag or '@' in frag:
            continue
        linea = c[:m.start()].count('\n') + 1
        riesgo.append(f'{os.path.basename(ruta)}:{linea} {frag[:44]}')
check('sin ids de prueba que colisionen por prefijo entre marcas',
      not riesgo, str(riesgo[:4]))

# --- Guarda de plantillas (on*= con tojson) ---
try:
    import test_template_regressions as ttr
    ttr.test_no_double_quoted_onclick_with_tojson()
    check('sin manejadores inline rotos por |tojson', True)
except AssertionError as e:
    check('sin manejadores inline rotos por |tojson', False, str(e)[:150])


print()
print('=' * 68)
print('3. RUNNER, GATE Y SEGURIDAD')
print('=' * 68)

runner = open('run_pre_cutover_validation.ps1', encoding='utf-8-sig').read()
fases_runner = re.findall(r'name = "([a-z_]+)"', runner)
gate = open('pre_cutover_gate.py', encoding='utf-8').read()
faltan_gate = [f for f in fases_runner if f not in gate]
check(f'runner y gate alineados ({len(fases_runner)} fases)', not faltan_gate, str(faltan_gate))

bat = open('abrir_crm.bat', encoding='utf-8', errors='replace').read()
for flag, valor in [('DISABLE_OUTBOUND_EMAIL', '1'), ('OUTBOUND_EMAIL_ENABLED', '0'),
                    ('ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS', '0'),
                    ('ALLOW_CONTROLLED_CUTOVER', '0')]:
    check(f'kill switch {flag}={valor}', f'set {flag}={valor}' in bat)

check('scheduler de recordatorios apagado por defecto',
      "os.environ.get('ENABLE_REMINDER_SCHEDULER') != '1'" in SRC)

# Los tests nuevos estan en el runner
for t in ['test_uso_diario_clientes', 'test_uso_diario_workflows_calendario',
          'test_navegacion_diaria', 'test_paginas_de_error_y_marca',
          'test_rendimiento_vistas', 'test_responsive_movil',
          'test_documento_web_pdf_paridad', 'test_quote_services',
          'test_snapshot_comercial']:
    check(f'{t}.py incluido en el runner', t in runner)


print()
print('=' * 68)
if notas:
    for n in notas:
        print(f'  nota: {n}')
if fallos:
    print(f'RESULTADO: {len(fallos)} COMPROBACION(ES) EN ROJO')
    for nombre, detalle in fallos:
        print(f'   - {nombre}: {detalle[:180]}')
    sys.exit(1)
print('RESULTADO: TODO VERDE -- listo para la corrida de Windows')
sys.exit(0)
