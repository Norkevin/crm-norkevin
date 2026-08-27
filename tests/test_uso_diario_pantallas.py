"""Uso diario: lo primero que se ve al abrir cada pantalla.

Kevin arranca con el CRM vacio, asi que durante los primeros dias TODAS
las pantallas que abra van a estar en su estado vacio. Si esa pantalla no
explica que va ahi ni ofrece el siguiente paso, el CRM se siente roto
aunque funcione perfecto.

Tambien cubre dos bugs de marca encontrados el 21-ago: los datos por
defecto (paquetes, cuentas bancarias, reglas de pago a equipo) se
etiquetaban con 'ASTRAL WEDDINGS' fijo. Al abrir Configuracion por
primera vez con Norkevin Photography, sus propios datos aparecian con el
nombre de la otra empresa.
"""
import glob
import os
import re

import pytest

from conftest import login_as_tenant

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'
AMBAS = [ASTRAL, NORKEVIN]

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(RAIZ, 'templates')


# ============================================================
# Estados vacios
# ============================================================

@pytest.mark.parametrize('ruta,esperado', [
    ('/jobs', 'Todavia no hay trabajos'),
    ('/clients', 'Todavia no hay clientes'),
    ('/quotes', 'Todavia no hay cotizaciones'),
    ('/invoices', 'Todavia no hay facturas'),
    ('/payments', 'Todavia no hay pagos'),
])
def test_las_listas_vacias_explican_que_va_ahi(auth_client, ruta, esperado):
    """`/jobs` era el peor caso: mostraba una tabla con encabezados y nada
    mas, sin una linea de texto ni un boton."""
    login_as_tenant(auth_client, 'tenant-vacio-demo', email='vacio@example.invalid')
    resp = auth_client.get(ruta)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert esperado in html, f'{ruta} no explica su estado vacio'


@pytest.mark.parametrize('ruta', ['/jobs', '/clients', '/quotes', '/invoices', '/payments'])
def test_las_listas_vacias_ofrecen_el_siguiente_paso(auth_client, ruta):
    login_as_tenant(auth_client, 'tenant-vacio-demo', email='vacio@example.invalid')
    html = auth_client.get(ruta).get_data(as_text=True)
    assert 'sn-empty-state-cta' in html, \
        f'{ruta} no ofrece ninguna accion cuando esta vacia'


def test_todas_las_listas_usan_el_mismo_patron_de_estado_vacio():
    """Si cada pantalla inventa su propio vacio, el CRM se ve como varios
    productos distintos."""
    faltan = []
    for nombre in ('jobs.html', 'clients.html', 'quotes.html',
                   'invoices.html', 'payments.html', 'leads.html'):
        with open(os.path.join(TPL, nombre), encoding='utf-8') as f:
            c = f.read()
        if 'sn-empty-state' not in c:
            faltan.append(nombre)
    assert not faltan, f'no usan el patron compartido de estado vacio: {faltan}'


def test_las_listas_avisan_cuando_el_filtro_no_encuentra_nada():
    """Antes, filtrar algo inexistente dejaba la lista en blanco sin
    explicacion: parecia que se habian perdido los datos."""
    faltan = []
    for nombre in ('jobs.html', 'clients.html', 'leads.html'):
        with open(os.path.join(TPL, nombre), encoding='utf-8') as f:
            c = f.read()
        if 'mostrarSinResultados' not in c:
            faltan.append(nombre)
    assert not faltan, f'no avisan cuando el filtro no devuelve nada: {faltan}'


# ============================================================
# El dashboard muestra a toda la gente de la boda
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_el_dashboard_muestra_a_los_dos_novios(auth_client, tenant_id):
    """El dashboard armaba el nombre a mano desde `client_id`, o sea solo
    el principal. La misma boda mostraba una persona en la pantalla de
    inicio y dos o tres en la lista de trabajos."""
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    novio = {'id': f'cli-dash-a-{tenant_id}', 'tenant_id': tenant_id,
             'first_name': 'Novio', 'last_name': 'Dash'}
    novia = {'id': f'cli-dash-b-{tenant_id}', 'tenant_id': tenant_id,
             'first_name': 'Novia', 'last_name': 'Dash'}
    for c in (novio, novia):
        app_module.store.upsert('clients', c)

    job = {'id': f'job-dash-{tenant_id}', 'tenant_id': tenant_id,
           'nombre': 'Boda Dashboard', 'status': 'Confirmado',
           'boda_date': '2026-12-31', 'client_id': novio['id']}
    app_module.store.upsert('jobs', job)
    app_module._set_job_clients(job, [(novio['id'], app_module.ROL_PRINCIPAL),
                                      (novia['id'], app_module.ROL_PAREJA)],
                                tenant_id=tenant_id)

    # El helper canonico es el mismo que usa /jobs.
    clientes = {c['id']: c for c in app_module.list_clients()}
    display = app_module._job_clients_display(job, clientes)
    assert 'Novio' in display and 'Novia' in display, \
        f'el display canonico perdio a alguien: {display!r}'

    assert auth_client.get('/dashboard').status_code == 200


# ============================================================
# Marca de los datos por defecto
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_los_datos_por_defecto_llevan_la_marca_de_la_cuenta(auth_client, tenant_id):
    """Los items semilla de Configuracion decian 'ASTRAL WEDDINGS' fijo.

    Se piden por la RUTA, no llamando al helper suelto: la marca se
    resuelve con get_current_tenant_id(), que necesita la sesion de una
    peticion. Fuera de una peticion devuelve None y la marca sale como
    "(empresa sin identificar)" -- correcto, pero no es lo que se quiere
    comprobar aca.
    """
    from src.tenant_brand_map import display_name_for_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    esperado = display_name_for_tenant(tenant_id)
    # Ids de los items SEMILLA (_default_config_items). Los demas los pudo
    # editar el usuario y pueden tener cualquier marca legitima; estos, no:
    # los genera el CRM y tienen que nacer con la marca de la cuenta.
    SEMILLA = {'cuenta-transferencia', 'regla-foto-principal', 'regla-asistente'}

    vistos = 0
    for kind in ('cuentas', 'reglas'):
        resp = auth_client.get(f'/api/config/{kind}')
        assert resp.status_code == 200, f'/api/config/{kind} respondio {resp.status_code}'
        for item in resp.get_json()[kind]:
            if item.get('id') not in SEMILLA:
                continue
            vistos += 1
            assert item.get('Marca') == esperado, (
                f"item semilla {item.get('id')!r} quedo con la marca "
                f"{item.get('Marca')!r} en vez de {esperado!r}")
    assert vistos, 'no se encontro ningun item semilla: el test no probo nada' 


def test_norkevin_no_ve_datos_semilla_de_astral(auth_client):
    """La comprobacion que importa: los defaults de una marca nunca pueden
    nombrar a la otra."""
    login_as_tenant(auth_client, NORKEVIN, email=f'{NORKEVIN}@example.invalid')
    for kind in ('cuentas', 'reglas'):
        resp = auth_client.get(f'/api/config/{kind}')
        assert resp.status_code == 200
        for item in resp.get_json()[kind]:
            assert 'ASTRAL' not in str(item.get('Marca', '')).upper(), \
                f'Norkevin vio un item marcado como Astral: {item}'


def test_no_hay_marcas_escritas_a_mano_en_el_codigo():
    """Guarda estatica: la marca se resuelve por tenant_brand_map, nunca
    por un string fijo. Esta regla nacio del incidente de agosto de 2026,
    en el que cientos de correos salieron con la marca equivocada."""
    ruta = os.path.join(RAIZ, 'app.py')
    with open(ruta, encoding='utf-8') as f:
        src = f.read()

    lineas = src.split('\n')
    # El registro de tenants SI define los nombres: es la fuente.
    legitimas = ("'id': 'tenant-", "'slug': 'astral-weddings'")
    ofensores = []
    patron = re.compile(r"""['"](ASTRAL WEDDINGS|Astral Weddings|Norkevin Photography)['"]""")
    for m in patron.finditer(src):
        idx = src[:m.start()].count('\n')
        linea = lineas[idx]
        if linea.lstrip().startswith('#'):
            continue
        if any(s in linea for s in legitimas):
            continue
        ofensores.append(f'linea {idx + 1}: {linea.strip()[:70]}')

    assert not ofensores, (
        'Marca escrita a mano en app.py: '
        f'{ofensores}. Usa _brand_display_name_for_tenant(get_current_tenant_id()).'
    )


def test_ninguna_plantilla_muestra_texto_en_ingles():
    """El CRM es en espanol y Kevin le muestra estas pantallas a sus
    clientes. La pestana decia "Facturas" y la tarjeta de adentro
    "Invoices"."""
    patron = re.compile(
        r'>\s*(No (?:clients?|jobs?|quotes?|payments?|contracts?|invoices?|emails?|'
        r'files?|leads?|notes?|mail|date|location|packages?|new activity|'
        r'questionnaires?)[^<]{0,30}|'
        r'[A-Z][a-z]+ will appear here|Create the first one|View [A-Z][a-z]+|'
        r'Add New[a-z ]*|Balance due|Expected:|Late:|Paid:|Contact info|'
        r'Send Email|Manual invoice)\s*<')
    ofensores = []
    for ruta in sorted(glob.glob(os.path.join(TPL, '*.html'))):
        with open(ruta, encoding='utf-8') as f:
            c = f.read()
        for m in patron.finditer(c):
            ofensores.append(f'{os.path.basename(ruta)}: "{m.group(1).strip()}"')
    assert not ofensores, f'Texto en ingles visible: {ofensores}'
