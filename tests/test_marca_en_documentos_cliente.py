"""Marca fija ('ASTRAL WEDDINGS') en pantallas que ve el cliente, o que
Kevin usa para escribirle.

Hallazgo del 26-ago, durante el cierre del punto "clientes multiples /
portal" del backlog: contract_view.html, quote_view.html,
questionnaire_view.html, client_portal.html y quote_edit.html tenian la
marca escrita a mano en el HTML. El PDF de cada uno de esos documentos
(generate_contract_pdf, generate_quote_pdf, etc.) ya estaba corregido
desde la fase de estabilizacion via resolve_pdf_brand(tenant_id) -- pero
la vista WEB que abre el mismo link (el que el cliente realmente visita)
nunca paso por el mismo arreglo. Un cliente de Norkevin Photography veia
"ASTRAL WEDDINGS" en la pagina de su propio contrato firmado, cotizacion y
portal -- aunque el PDF que descargara del mismo link ya decia lo
correcto.

Mismo patron en los defaults de JS al componer un correo desde
job_detail/lead_detail/leads (esta vez texto que Kevin ve y podria no
notar antes de enviar), y en el fallback de nombre de empresa de
Settings cuando esa cuenta todavia no guardo su propia info -- ya
documentado como deuda conocida en POST_CUTOVER_BACKLOG.md.

Segundo hallazgo, 26-ago (corrida final de Windows, daily_usage FAIL):
los 3 tests de "compone correo con la marca de la sesion" de mas abajo
encontraron una fuga distinta -- no en el HTML/JS de la plantilla (eso ya
estaba arreglado arriba) sino en los DATOS: data/email_templates.json
tenia, para Norkevin Photography Y para Ramiro Cruz Photo, 4 plantillas
de correo (tpl-paquetes, tpl-reserva, tpl-reserva-prod,
tpl-fecha-no-disponible) que eran copia literal, texto por texto, de las
de Astral -- con "ASTRAL WEDDINGS" (o "Astral Films") escrito adentro del
asunto/cuerpo. El aislamiento por tenant_id funcionaba bien (cada cuenta
solo ve sus propias 12 plantillas); el problema era que esas 12 nunca se
habian personalizado de verdad al copiarlas por tenant. Se corrigieron
las 8 filas afectadas (Norkevin x4, Ramiro x4) via store.upsert -- ver
test_plantillas_de_correo_no_tienen_la_marca_de_otro_tenant mas abajo.
data/seeds/email_templates.default.json (la semilla generica, sin
tenant_id, que se copia para cuentas nuevas) tambien tenia el nombre de
Astral escrito en 2 de sus 8 plantillas -- pero esa semilla es,
deliberadamente, un espejo del contenido ACTIVO de Astral
(test_astral_packages_catalog.py::test_seed_send_packages_template_
matches_active_template ya lo exige para tpl-paquetes), asi que no se
toco: el problema real nunca fue la semilla en si, sino que las copias
de Norkevin/Ramiro se quedaron sin personalizar despues de copiarse.

Tercer hallazgo, 27-ago (misma corrida repetida despues del fix de
arriba): los 3 tests de "compone correo" seguian fallando IGUAL, pero con
las 12 plantillas de Norkevin ya confirmadas limpias (verificado dos
veces, por archivo y por store.list). La causa esta vez era el propio
test: _sin_astral(html) escanea la PAGINA ENTERA, y /jobs/<id>,
/leads/<id> y sobre todo /leads no muestran solo lo que este test crea --
tambien reflejan datos acumulados de OTROS tests de la misma corrida de
pytest (el store es un singleton de sesion; ver _restore_tenants_table en
conftest.py, que solo restaura la tabla `tenants`, ninguna otra). Un
ejemplo concreto: _default_config_items('fuentes') arma la lista de
"fuentes de lead" leyendo TODOS los leads del tenant activo y agregando
cualquier valor de `fuente` no reconocido -- si algun otro test de la
corrida crea, para Norkevin, un lead con un `fuente` de prueba que
mencione a Astral (para probar otra cosa), esa palabra aparece en
/leads aunque el bug de marca ya este cerrado. Se corrigio arrimando
_sin_astral a la seccion que el nombre del test promete probar (las
declaraciones JS de COMPANY_NAME/EMAIL_TEMPLATES), en vez de la pagina
completa -- exactamente el mismo criterio que ya se uso para el test de
Settings (que se topaba con la tarjeta, legitima, de "Migracion a 3
cuentas"). El chequeo de "nada de marca fija en el CODIGO/las plantillas"
sigue cubierto, sin depender de datos de sesion, por
tools/verificacion_final.py.
"""
import json
import uuid

from conftest import login_as_tenant

NORKEVIN = 'tenant-norkevin-photography'
RAMIRO = 'tenant-ramiro-cruz'
ASTRAL = 'tenant-norkevin'


def _cliente_y_job(app_module, sufijo, tenant_id=NORKEVIN):
    client_id = f'client-marca-{sufijo}'
    job_id = f'job-marca-{sufijo}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Marca', 'last_name': 'Prueba',
        'email': f'{sufijo}@example.invalid', 'tenant_id': tenant_id,
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Marca',
        'status': 'Confirmado', 'price_total': 10000, 'tenant_id': tenant_id,
    })
    return client_id, job_id


def _sin_astral(html):
    return 'ASTRAL' not in html.upper()


def _seccion_correo(html, *nombres_variables):
    """Extrae SOLO las declaraciones JS var/const <nombre> = ...; que
    arman el correo (COMPANY_NAME, EMAIL_TEMPLATES/LEAD_EMAIL_TEMPLATES),
    sin el resto de la pagina. El resto puede traer datos acumulados de
    OTROS tests de la misma corrida (leads con fuente de prueba, etc.) que
    no tienen nada que ver con si el compose-email usa la marca de la
    sesion o una fija -- ver docstring del modulo, "Tercer hallazgo"."""
    partes = []
    for nombre in nombres_variables:
        for marcador in (f'var {nombre} = ', f'const {nombre} = '):
            idx = html.find(marcador)
            if idx == -1:
                continue
            fin = html.find(';\n', idx)
            partes.append(html[idx:fin if fin != -1 else idx + 4000])
            break
    return '\n'.join(partes)


# ============================================================
# Documentos publicos (el cliente los abre SIN sesion)
# ============================================================

def test_contrato_muestra_la_marca_del_tenant_del_job(client):
    import app as app_module
    client_id, job_id = _cliente_y_job(app_module, 'contrato')
    contract_id = 'contract-marca-' + uuid.uuid4().hex[:8]
    app_module.store.upsert('contracts', {
        'id': contract_id, 'job_id': job_id, 'client_id': client_id,
        'tenant_id': NORKEVIN, 'status': 'Borrador', 'signed': False,
        'created': '2026-08-26',
    })
    resp = client.get(f'/contracts/{contract_id}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Norkevin' in html, 'el contrato no muestra la marca real del tenant'
    assert _sin_astral(html), 'un contrato de Norkevin no puede decir ASTRAL WEDDINGS'


def test_cotizacion_muestra_la_marca_del_tenant_del_lead(client):
    import app as app_module
    lead_id = 'lead-marca-' + uuid.uuid4().hex[:8]
    quote_id = 'quote-marca-' + uuid.uuid4().hex[:8]
    app_module.upsert_lead({
        'id': lead_id, 'nombre': 'Lead Marca', 'email': 'leadmarca@example.invalid',
        'status': 'Nuevo', 'tenant_id': NORKEVIN,
    })
    app_module.store.upsert('quotes', {
        'id': quote_id, 'lead_id': lead_id, 'status': 'Pendiente',
        'quote_kind': 'fixed', 'paquete_nombre': 'Paquete prueba',
        'options': [{'name': 'Paquete prueba', 'price': 1000.0, 'description': 'x'}],
        'tenant_id': NORKEVIN,
    })
    resp = client.get(f'/quotes/{quote_id}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Norkevin' in html
    assert _sin_astral(html), 'una cotizacion de Norkevin no puede decir ASTRAL WEDDINGS'


def test_cuestionario_muestra_la_marca_del_tenant_del_job(client):
    import app as app_module
    client_id, job_id = _cliente_y_job(app_module, 'cuestionario')
    q_id = 'quest-marca-' + uuid.uuid4().hex[:8]
    app_module.store.upsert('questionnaires', {
        'id': q_id, 'job_id': job_id, 'client_id': client_id,
        'tenant_id': NORKEVIN, 'status': 'Pendiente', 'questions': [], 'answers': {},
    })
    resp = client.get(f'/questionnaires/{q_id}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Norkevin' in html
    assert _sin_astral(html), 'un cuestionario de Norkevin no puede decir ASTRAL WEDDINGS'


def test_portal_muestra_la_marca_del_tenant_del_cliente(client):
    import app as app_module
    client_id, _job_id = _cliente_y_job(app_module, 'portal')
    resp = client.get(f'/portal/{client_id}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Norkevin' in html
    assert _sin_astral(html), 'el portal de un cliente de Norkevin no puede decir ASTRAL WEDDINGS'


def test_cotizacion_aceptada_muestra_la_marca_del_tenant(client):
    import app as app_module
    lead_id = 'lead-acepta-' + uuid.uuid4().hex[:8]
    quote_id = 'quote-acepta-' + uuid.uuid4().hex[:8]
    client_id = 'client-acepta-' + uuid.uuid4().hex[:8]
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Acepta', 'last_name': 'Marca',
        'email': 'acepta@example.invalid', 'tenant_id': NORKEVIN,
    })
    app_module.upsert_lead({
        'id': lead_id, 'nombre': 'Lead Acepta', 'client_id': client_id,
        'status': 'Nuevo', 'tenant_id': NORKEVIN,
    })
    app_module.store.upsert('quotes', {
        'id': quote_id, 'lead_id': lead_id, 'client_id': client_id,
        'status': 'Aceptada', 'quote_kind': 'fixed', 'paquete_nombre': 'Paquete prueba',
        'precio_total': 5000, 'plan_pago': 1,
        'options': [{'id': 'op1', 'name': 'Paquete prueba', 'precio_total': 5000, 'incluye': []}],
        'selected_option_id': 'op1', 'tenant_id': NORKEVIN,
    })
    resp = client.post(f'/quotes/{quote_id}/accept', json={})
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Norkevin' in html
    assert _sin_astral(html), 'la pantalla de cotizacion aceptada no puede decir ASTRAL WEDDINGS'


# ============================================================
# Paginas internas (Kevin, con sesion) que arman texto para el cliente
# ============================================================

def test_editor_de_cotizacion_muestra_la_marca_de_la_sesion(client):
    import app as app_module
    login_as_tenant(client, NORKEVIN, email='norkph-quoteedit@example.invalid',
                     name='Norkevin Photography')
    app_module.store.upsert('tenants', {
        'id': NORKEVIN, 'name': 'Norkevin Photography',
        'login_email': 'norkph-quoteedit@example.invalid', 'active': True,
    })
    quote_id = 'quote-edit-marca-' + uuid.uuid4().hex[:8]
    app_module.store.upsert('quotes', {
        'id': quote_id, 'status': 'Borrador', 'options': [], 'tenant_id': NORKEVIN,
    })
    resp = client.get(f'/quotes/{quote_id}/edit')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Norkevin' in html
    assert _sin_astral(html), 'el editor de cotizacion no puede mostrar ASTRAL WEDDINGS a Norkevin'


def test_job_detail_compone_correo_con_la_marca_de_la_sesion(client):
    import app as app_module
    login_as_tenant(client, NORKEVIN, email='norkph-jobdetail@example.invalid',
                     name='Norkevin Photography')
    app_module.store.upsert('tenants', {
        'id': NORKEVIN, 'name': 'Norkevin Photography',
        'login_email': 'norkph-jobdetail@example.invalid', 'active': True,
    })
    client_id, job_id = _cliente_y_job(app_module, 'jobdetail')
    resp = client.get(f'/jobs/{job_id}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'var COMPANY_NAME' in html, 'falta la constante que alimenta los defaults de correo'
    assert 'Norkevin Photography' in html
    seccion = _seccion_correo(html, 'COMPANY_NAME', 'EMAIL_TEMPLATES')
    assert seccion, 'no se encontraron las declaraciones de COMPANY_NAME/EMAIL_TEMPLATES'
    assert _sin_astral(seccion), 'el compose-email de job_detail para Norkevin no puede usar la marca de Astral'


def test_lead_detail_compone_correo_con_la_marca_de_la_sesion(client):
    import app as app_module
    login_as_tenant(client, NORKEVIN, email='norkph-leaddetail@example.invalid',
                     name='Norkevin Photography')
    app_module.store.upsert('tenants', {
        'id': NORKEVIN, 'name': 'Norkevin Photography',
        'login_email': 'norkph-leaddetail@example.invalid', 'active': True,
    })
    lead_id = 'lead-detail-marca-' + uuid.uuid4().hex[:8]
    app_module.upsert_lead({
        'id': lead_id, 'nombre': 'Lead Detail Marca', 'email': 'ldm@example.invalid',
        'status': 'Nuevo', 'tenant_id': NORKEVIN,
    })
    resp = client.get(f'/leads/{lead_id}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Norkevin Photography' in html
    seccion = _seccion_correo(html, 'COMPANY_NAME', 'EMAIL_TEMPLATES')
    assert seccion, 'no se encontraron las declaraciones de COMPANY_NAME/EMAIL_TEMPLATES'
    assert _sin_astral(seccion), 'el compose-email de lead_detail para Norkevin no puede usar la marca de Astral'


def test_lista_de_leads_compone_correo_con_la_marca_de_la_sesion(client):
    import app as app_module
    login_as_tenant(client, NORKEVIN, email='norkph-leads@example.invalid',
                     name='Norkevin Photography')
    app_module.store.upsert('tenants', {
        'id': NORKEVIN, 'name': 'Norkevin Photography',
        'login_email': 'norkph-leads@example.invalid', 'active': True,
    })
    resp = client.get('/leads')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Norkevin Photography' in html
    seccion = _seccion_correo(html, 'LEAD_EMAIL_TEMPLATES', 'COMPANY_NAME')
    assert seccion, 'no se encontraron las declaraciones de LEAD_EMAIL_TEMPLATES/COMPANY_NAME'
    assert _sin_astral(seccion), 'el compose-email de /leads para Norkevin no puede usar la marca de Astral'


def test_settings_sin_datos_guardados_usa_la_marca_de_la_cuenta_no_astral(client):
    """Cuenta sintetica que nunca guardo su settings.company (el hueco que
    ya documentaba POST_CUTOVER_BACKLOG.md): el <h2> debia caer en
    'ASTRAL WEDDINGS' fijo en vez del nombre real de la cuenta.

    Nota: la pagina de Settings SI menciona 'Astral Weddings' en un lugar
    legitimo -- la tarjeta roja de 'Migracion a 3 cuentas independientes'
    explica un hecho historico real (todo lo viejo quedo asignado a Astral
    Weddings) y se muestra igual para cualquier cuenta, no es una fuga de
    marca. Por eso este test mira puntualmente el <h2> del nombre de
    cuenta -- que es el unico lugar donde 'ASTRAL WEDDINGS' seria un bug
    de verdad -- en vez de escanear la pagina entera como hacen los demas
    tests de este archivo."""
    import app as app_module
    tenant_id = 'tenant-settings-sin-company-demo'
    login_as_tenant(client, tenant_id, email='sincompany@example.invalid',
                     name='Cuenta Sin Company')
    resp = client.get('/settings')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    idx = html.index('font-size:17px;font-weight:600;margin:0;')
    encabezado_cuenta = html[idx:idx + 200]
    assert tenant_id in encabezado_cuenta, \
        'sin company.name guardado, el h2 debe caer en el nombre de la cuenta (current_tenant.name)'
    assert _sin_astral(encabezado_cuenta), \
        'una cuenta sin company.name guardado no puede mostrar ASTRAL WEDDINGS por default'


def test_plantillas_de_correo_no_tienen_la_marca_de_otro_tenant():
    """Guarda de datos (no de codigo): ninguna plantilla de correo guardada
    para Norkevin o Ramiro puede tener el nombre de Astral escrito adentro
    del asunto/cuerpo/nombre -- y viceversa. Astral SI puede (y debe)
    decir 'Astral Weddings' en las suyas, es su propia marca real."""
    import app as app_module

    OTRAS_MARCAS = {
        NORKEVIN: ('astral weddings', 'astral films'),
        RAMIRO: ('astral weddings', 'astral films'),
        ASTRAL: ('norkevin photography', 'ramiro cruz photo'),
    }
    filtradas = []
    for tpl in app_module.store.list('email_templates'):
        tenant_id = tpl.get('tenant_id')
        prohibidas = OTRAS_MARCAS.get(tenant_id)
        if not prohibidas:
            continue
        blob = json.dumps(tpl, ensure_ascii=False).lower()
        for marca_ajena in prohibidas:
            if marca_ajena in blob:
                filtradas.append((tenant_id, tpl.get('id'), marca_ajena))
    assert not filtradas, f'plantillas con la marca de otra cuenta: {filtradas}'
