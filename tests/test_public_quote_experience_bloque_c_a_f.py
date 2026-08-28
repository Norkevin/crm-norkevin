"""BLOQUE C-F (Public Quote Experience, 28-ago-2026): renderer publico
premium, builder dentro de FLOW, extras conectados al motor de aceptacion,
y Settings > Cotizaciones (tema/portafolio/condiciones/plantillas).

Ver PUBLIC_QUOTES_AUDIT.md y tests/test_public_quote_experience_bloque_b.py
(BLOQUE A/B: numeracion, token publico, opciones con grupos, aislamiento
portfolio/condiciones, snapshot al enviar -- no se repite aca).

Regla de esta suite, igual que la de BLOQUE B: nada de esto toca
accept_quote(), _convert_lead_to_job(), conversion_registry,
_ensure_payments_for_quote ni tenant_brand_map -- se prueba que BLOQUE E
(extras) se CONECTA al motor existente via los mismos campos
(precio_total/plan_pago) que ese motor ya leia, sin duplicarlo.

No se corre en este sandbox (falta pytest/Flask). Verificado con
tools/verificacion_final.py (compilacion + AST) en su lugar, mas
simulacion aislada en Python puro de la logica de resolucion de extras
(ver la conversacion que produjo este archivo) y render manual de las
plantillas tocadas con Jinja standalone.
"""
import uuid

import pytest

from conftest import login_as_tenant

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'


def _crear_lead(app_module, tenant_id, *, nombre='Cliente Test', email=None):
    lead_id = 'lead-pqe2-' + uuid.uuid4().hex[:8]
    email = email or f'{lead_id}@example.com'
    app_module.store.upsert('leads', {
        'id': lead_id, 'nombre': nombre, 'email': email,
        'status': 'Nuevo', 'tenant_id': tenant_id,
    })
    return lead_id


def _crear_borrador_con_opcion(auth_client, app_module, tenant_id, *, option_id='opt-1',
                                precio_total=10000, **extra_option_fields):
    lead_id = _crear_lead(app_module, tenant_id)
    r = auth_client.post('/api/quotes/draft', json={'lead_id': lead_id})
    quote_id = r.get_json()['quote_id']
    payload = {'id': option_id, 'name': 'Paquete Test', 'precio_total': precio_total}
    payload.update(extra_option_fields)
    r = auth_client.post(f'/api/quotes/{quote_id}/options', json=payload)
    assert r.status_code == 200, r.get_json()
    return quote_id


def _enviar(auth_client, quote_id):
    r = auth_client.post(f'/api/quotes/{quote_id}/send', json={})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['quote_url'].rsplit('/q/', 1)[1]


# ---------------------------------------------------------------------
# BLOQUE C: renderer publico -- contenido real, no solo status code
# ---------------------------------------------------------------------

def test_public_view_muestra_numero_grupos_y_extras(auth_client):
    import app as app_module
    quote_id = _crear_borrador_con_opcion(
        auth_client, app_module, ASTRAL, precio_total=15000,
        subtitle='Cobertura del dia', label='Mas elegido',
        groups=[{'title': 'Boda principal', 'items': ['8 horas', 'Editor principal']}],
    )
    auth_client.post(f'/api/quotes/{quote_id}/extras', json={
        'extras': [{'name': 'Dron', 'price': 1500}],
    })
    token = _enviar(auth_client, quote_id)

    r = auth_client.get(f'/q/{token}')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    quote = app_module.store.get('quotes', quote_id)
    assert quote['number'] in html
    assert 'Boda principal' in html
    assert 'Editor principal' in html
    assert 'Mas elegido' in html
    assert 'Dron' in html
    assert f'/q/{token}/accept' in html
    assert f'/q/{token}/decline' in html


def test_send_reemplaza_el_marcador_de_link_por_la_url_real(auth_client):
    """quote_edit.html no puede mostrar el link real en la vista previa del
    mensaje (el token recien se emite al enviar) -- manda un marcador
    [[QUOTE_LINK]] en su lugar. Antes de esto, un envio sin tocar el
    mensaje salia con el link interno viejo (/quotes/<id>) en vez de
    /q/<token> en el correo real.

    STAGE 2 (agosto 2026): api_quote_send ya no entrega de inmediato --
    encola con MailTracker.queue_email(), asi que el correo compuesto vive
    en pending_emails (esperando aprobacion en /emails) y no en mail_log
    hasta que alguien lo apruebe. La correccion del marcador debe seguir
    viendose en el pendiente, que es la copia congelada de lo que se
    mandaria si se aprueba."""
    import app as app_module
    quote_id = _crear_borrador_con_opcion(auth_client, app_module, ASTRAL, precio_total=5000)
    quote_antes = app_module.store.get('quotes', quote_id)
    lead_id = quote_antes['lead_id']

    r = auth_client.post(f'/api/quotes/{quote_id}/send', json={
        'subject': 'x',
        'body': 'Hola,\n\nTu link: [[QUOTE_LINK]]\n\nSaludos',
    })
    assert r.status_code == 200
    quote_url = r.get_json()['quote_url']
    token = quote_url.rsplit('/q/', 1)[1]

    pendientes = [p for p in app_module.store.list('pending_emails') if p.get('lead_id') == lead_id]
    assert pendientes, 'no se encontro en pending_emails el correo encolado para este lead'
    correo = pendientes[-1]
    assert correo['status'] == 'pending', 'debe quedar esperando aprobacion, no enviarse solo'
    assert '[[QUOTE_LINK]]' not in correo['body'], 'el marcador no debe llegar tal cual al correo encolado'
    assert f'/q/{token}' in correo['body'], 'el correo encolado debe tener la URL del token, no el marcador'


def test_public_view_legacy_quote_sin_options_sigue_renderizando(auth_client):
    """Cotizacion vieja: un solo paquete plano, sin 'options', sin
    extras_catalog, sin portfolio/condiciones asignadas. No debe romperse
    (backward compat de punta a punta, no solo a nivel de datos)."""
    import app as app_module
    lead_id = _crear_lead(app_module, ASTRAL)
    quote_id = 'quote-legacy-' + uuid.uuid4().hex[:6]
    app_module.store.upsert('quotes', {
        'id': quote_id, 'lead_id': lead_id, 'tenant_id': ASTRAL,
        'status': 'Borrador', 'paquete_nombre': 'Paquete Unico',
        'precio_total': 8000, 'incluye': ['Cobertura basica'],
    })
    r = auth_client.get(f'/quotes/{quote_id}')
    assert r.status_code == 200
    assert 'Paquete Unico' in r.get_data(as_text=True)


# ---------------------------------------------------------------------
# BLOQUE D: builder -- campos extendidos, presentacion, plantillas
# ---------------------------------------------------------------------

def test_option_save_persiste_todos_los_campos_nuevos(auth_client):
    import app as app_module
    quote_id = _crear_borrador_con_opcion(
        auth_client, app_module, ASTRAL, option_id='opt-full', precio_total=20000,
        subtitle='Sub', label='Popular', horas=10, precio_anterior=25000,
        descuento=5000, description='Descripcion breve',
    )
    quote = app_module.store.get('quotes', quote_id)
    opt = quote['options'][0]
    assert opt['subtitle'] == 'Sub'
    assert opt['label'] == 'Popular'
    assert opt['horas'] == 10
    assert opt['precio_anterior'] == 25000
    assert opt['descuento'] == 5000
    assert opt['description'] == 'Descripcion breve'


def test_presentation_save_ignora_portfolio_id_de_otra_cuenta(client):
    """El picker de Settings/builder manda ids -- si alguien manipula el
    POST para incluir un id de portfolio de OTRA cuenta, se descarta en
    vez de guardarse. Mismo principio que ya protege extras/accept."""
    import app as app_module
    login_as_tenant(client, NORKEVIN, email='norkevin-pres@example.com')
    r = client.post('/api/portfolio', json={'title': 'Boda secreta de Norkevin'})
    id_ajeno = r.get_json()['item']['id']

    login_as_tenant(client, ASTRAL, email='astral-pres@example.com')
    lead_id = _crear_lead(app_module, ASTRAL)
    r = client.post('/api/quotes/draft', json={'lead_id': lead_id})
    quote_id = r.get_json()['quote_id']

    r = client.post(f'/api/quotes/{quote_id}/presentation', json={
        'portfolio_ids': [id_ajeno], 'terms_template_id': '',
    })
    assert r.status_code == 200
    assert r.get_json()['portfolio_ids'] == [], \
        'un portfolio_id de otra cuenta no debe guardarse en el quote'

    quote = app_module.store.get('quotes', quote_id)
    assert quote.get('portfolio_ids') == []


def test_presentation_save_acepta_ids_propios(auth_client):
    import app as app_module
    r = auth_client.post('/api/portfolio', json={'title': 'Boda propia'})
    portfolio_id = r.get_json()['item']['id']
    r = auth_client.post('/api/quote-terms-templates', json={
        'title': 'Condiciones propias', 'blocks': [{'title': 'X', 'body': 'Y'}],
    })
    terms_id = r.get_json()['item']['id']

    lead_id = _crear_lead(app_module, ASTRAL)
    r = auth_client.post('/api/quotes/draft', json={'lead_id': lead_id})
    quote_id = r.get_json()['quote_id']

    r = auth_client.post(f'/api/quotes/{quote_id}/presentation', json={
        'portfolio_ids': [portfolio_id], 'terms_template_id': terms_id,
    })
    assert r.status_code == 200
    assert r.get_json()['portfolio_ids'] == [portfolio_id]
    assert r.get_json()['terms_template_id'] == terms_id


def test_quote_template_creado_desde_builder_es_independiente(auth_client):
    """'Guardar como plantilla' (BLOQUE F, boton en quote_edit.html) manda
    las options de una cotizacion real a /api/quote-templates. Al usar esa
    plantilla para crear una cotizacion NUEVA, las opciones deben ser una
    copia independiente: editar la plantilla despues no debe alterar
    cotizaciones ya creadas con ella, y viceversa."""
    import app as app_module
    quote_id = _crear_borrador_con_opcion(auth_client, app_module, ASTRAL, precio_total=9500)
    quote = app_module.store.get('quotes', quote_id)

    r = auth_client.post('/api/quote-templates', json={
        'name': 'Plantilla Boda Civil',
        'options': quote['options'],
        'plan_pago_opciones': [1, 2],
    })
    assert r.status_code == 200
    template_id = r.get_json()['item']['id']

    lead_id2 = _crear_lead(app_module, ASTRAL)
    r = auth_client.post('/api/quotes/draft-from-template', json={
        'lead_id': lead_id2, 'template_id': template_id,
    })
    assert r.status_code == 200
    quote_id2 = r.get_json()['quote_id']
    quote2 = app_module.store.get('quotes', quote_id2)
    assert quote2['options'][0]['precio_total'] == 9500

    # Editar las opciones del quote nuevo NO debe tocar la plantilla.
    auth_client.post(f'/api/quotes/{quote_id2}/options', json={
        'id': quote2['options'][0]['id'], 'name': 'Renombrado', 'precio_total': 1,
    })
    template_after = app_module.store.get('quote_templates', template_id)
    assert template_after['options'][0]['precio_total'] == 9500, \
        'editar un quote creado desde una plantilla no debe alterar la plantilla'


# ---------------------------------------------------------------------
# BLOQUE E: extras conectados al motor de aceptacion (idempotencia +
# precio server-side, el nucleo de este bloque)
# ---------------------------------------------------------------------

def test_accept_suma_extras_al_total_y_a_la_cuota(auth_client):
    import app as app_module
    quote_id = _crear_borrador_con_opcion(
        auth_client, app_module, ASTRAL, option_id='opt-e1', precio_total=10000)
    r = auth_client.post(f'/api/quotes/{quote_id}/extras', json={'extras': [
        {'id': 'ex-dron', 'name': 'Dron', 'price': 1500},
        {'id': 'ex-album', 'name': 'Album', 'price': 2500},
    ]})
    assert r.status_code == 200
    token = _enviar(auth_client, quote_id)

    r = auth_client.post(f'/q/{token}/accept', json={
        'option_id': 'opt-e1', 'plan_pago': 2, 'extra_ids': 'ex-dron,ex-album',
    })
    assert r.status_code == 200

    quote = app_module.store.get('quotes', quote_id)
    assert quote['paquete_precio_base'] == 10000
    assert quote['extras_total'] == 4000
    assert quote['precio_total'] == 14000, 'precio_total debe incluir los extras'
    assert quote['cuota_monto'] == 7000, 'cuota_monto debe salir del total CON extras'
    nombres = sorted(e['name'] for e in quote['selected_extras'])
    assert nombres == ['Album', 'Dron']


def test_accept_ignora_extra_id_inventado_y_precio_del_cliente(auth_client):
    """El precio SIEMPRE sale del catalogo server-side. Un extra_id que no
    existe en esta cotizacion se descarta -- no suma nada, no revienta."""
    import app as app_module
    quote_id = _crear_borrador_con_opcion(
        auth_client, app_module, ASTRAL, option_id='opt-e2', precio_total=5000)
    auth_client.post(f'/api/quotes/{quote_id}/extras', json={'extras': [
        {'id': 'ex-real', 'name': 'Real', 'price': 1000},
    ]})
    token = _enviar(auth_client, quote_id)

    r = auth_client.post(f'/q/{token}/accept', json={
        'option_id': 'opt-e2', 'plan_pago': 1,
        'extra_ids': 'ex-real,ex-que-no-existe',
    })
    assert r.status_code == 200
    quote = app_module.store.get('quotes', quote_id)
    assert quote['extras_total'] == 1000, 'solo el extra real debe sumar'
    assert quote['precio_total'] == 6000
    assert len(quote['selected_extras']) == 1


def test_accept_sin_extras_seleccionados_no_cambia_el_total(auth_client):
    """Cliente que no marca ningun extra: comportamiento identico a antes
    de BLOQUE E (regresion contra lo que ya funcionaba)."""
    import app as app_module
    quote_id = _crear_borrador_con_opcion(
        auth_client, app_module, ASTRAL, option_id='opt-e3', precio_total=7000)
    auth_client.post(f'/api/quotes/{quote_id}/extras', json={'extras': [
        {'id': 'ex-x', 'name': 'X', 'price': 999},
    ]})
    token = _enviar(auth_client, quote_id)

    r = auth_client.post(f'/q/{token}/accept', json={'option_id': 'opt-e3', 'plan_pago': 1})
    assert r.status_code == 200
    quote = app_module.store.get('quotes', quote_id)
    assert quote['extras_total'] == 0
    assert quote['precio_total'] == 7000
    assert quote['selected_extras'] == []


def test_accept_legacy_quote_sin_extras_catalog_no_revienta(auth_client):
    """Cotizacion creada antes de BLOQUE E: no tiene 'extras_catalog' en
    absoluto (ni siquiera lista vacia). Aceptar no debe fallar."""
    import app as app_module
    lead_id = _crear_lead(app_module, ASTRAL)
    quote_id = 'quote-legacy-accept-' + uuid.uuid4().hex[:6]
    app_module.store.upsert('quotes', {
        'id': quote_id, 'lead_id': lead_id, 'tenant_id': ASTRAL,
        'status': 'Borrador',
        'options': [{'id': 'opt-legacy', 'name': 'Paquete', 'precio_total': 3000, 'incluye': []}],
    })
    r = auth_client.post(f'/quotes/{quote_id}/accept', json={
        'option_id': 'opt-legacy', 'plan_pago': 1,
    })
    assert r.status_code == 200
    quote = app_module.store.get('quotes', quote_id)
    assert quote['precio_total'] == 3000
    assert quote['extras_total'] == 0


def test_doble_accept_no_recalcula_extras(auth_client):
    """Idempotencia: una segunda visita a /accept (doble click, retry de
    red, link reabierto) no debe volver a sumar ni cambiar los extras ya
    cobrados en el primer accept, incluso si el segundo POST manda
    extra_ids distintos (un cliente que cambia de opinion DESPUES de
    aceptar no puede alterar lo ya cobrado por esta via)."""
    import app as app_module
    quote_id = _crear_borrador_con_opcion(
        auth_client, app_module, ASTRAL, option_id='opt-e4', precio_total=10000)
    auth_client.post(f'/api/quotes/{quote_id}/extras', json={'extras': [
        {'id': 'ex-a', 'name': 'A', 'price': 1000},
        {'id': 'ex-b', 'name': 'B', 'price': 2000},
    ]})
    token = _enviar(auth_client, quote_id)

    r1 = auth_client.post(f'/q/{token}/accept', json={
        'option_id': 'opt-e4', 'plan_pago': 1, 'extra_ids': 'ex-a',
    })
    assert r1.status_code == 200
    quote_after_1 = app_module.store.get('quotes', quote_id)
    assert quote_after_1['precio_total'] == 11000
    assert quote_after_1['status'] == 'Aceptada'

    # Segundo POST -- intenta agregar ex-b, que NO se habia elegido antes.
    r2 = auth_client.post(f'/q/{token}/accept', json={
        'option_id': 'opt-e4', 'plan_pago': 1, 'extra_ids': 'ex-a,ex-b',
    })
    assert r2.status_code == 200
    quote_after_2 = app_module.store.get('quotes', quote_id)
    assert quote_after_2['precio_total'] == 11000, \
        'un segundo accept no debe poder agregar mas extras a lo ya cobrado'
    assert quote_after_2['extras_total'] == 1000

    # Y el motor de pagos existente no debe haber duplicado el schedule.
    job = app_module.get_job(quote_after_2['job_id'])
    activos = [s for s in app_module.store.list('payment_schedules')
               if s.get('job_id') == job['id'] and s.get('status') == 'active']
    assert len(activos) == 1


def test_accept_valida_plan_pago_contra_las_opciones_ofrecidas(auth_client):
    """Hardening post-revision: plan_pago viene de un form publico sin
    login. Si el admin ofrecio [1, 2, 4] cuotas y el POST manda 3 (nunca
    ofrecido -- form manipulado o bug del cliente), no se usa 3 tal cual:
    se cae al default de la cotizacion en vez de aceptar cualquier numero."""
    import app as app_module
    quote_id = _crear_borrador_con_opcion(
        auth_client, app_module, ASTRAL, option_id='opt-plan1', precio_total=8000)
    r = auth_client.post(f'/api/quotes/{quote_id}/payment-options',
                          json={'plan_pago_opciones': [1, 2, 4]})
    assert r.status_code == 200
    token = _enviar(auth_client, quote_id)

    r = auth_client.post(f'/q/{token}/accept', json={'option_id': 'opt-plan1', 'plan_pago': 3})
    assert r.status_code == 200
    quote = app_module.store.get('quotes', quote_id)
    assert quote['plan_pago'] != 3, 'un plan_pago no ofrecido no debe aceptarse tal cual'
    assert quote['plan_pago'] in (1, 2, 4)


def test_accept_rechaza_plan_pago_negativo_sin_opciones_definidas(auth_client):
    """Cotizacion vieja sin plan_pago_opciones: un plan_pago negativo (form
    manipulado) no debe llegar a dividir el precio ni crear un calendario
    de pagos negativo/absurdo."""
    import app as app_module
    quote_id = _crear_borrador_con_opcion(
        auth_client, app_module, ASTRAL, option_id='opt-plan2', precio_total=6000)
    quote_antes = app_module.store.get('quotes', quote_id)
    quote_antes.pop('plan_pago_opciones', None)
    app_module.store.upsert('quotes', quote_antes)
    token = _enviar(auth_client, quote_id)

    r = auth_client.post(f'/q/{token}/accept', json={'option_id': 'opt-plan2', 'plan_pago': -7})
    assert r.status_code == 200
    quote = app_module.store.get('quotes', quote_id)
    assert quote['plan_pago'] >= 1, 'un plan_pago negativo nunca debe persistir tal cual'
    assert quote['cuota_monto'] > 0


def test_accept_extra_id_duplicado_en_la_seleccion_no_duplica_el_total(auth_client):
    """Si el mismo id llega repetido en extra_ids (checkbox marcado dos
    veces, form manipulado, doble evento de click) no debe sumarse dos
    veces -- ni por duplicados del lado del pedido ni del catalogo."""
    import app as app_module
    quote_id = _crear_borrador_con_opcion(
        auth_client, app_module, ASTRAL, option_id='opt-dup', precio_total=5000)
    auth_client.post(f'/api/quotes/{quote_id}/extras', json={'extras': [
        {'id': 'ex-unico', 'name': 'Unico', 'price': 1000},
    ]})
    token = _enviar(auth_client, quote_id)

    r = auth_client.post(f'/q/{token}/accept', json={
        'option_id': 'opt-dup', 'plan_pago': 1, 'extra_ids': 'ex-unico,ex-unico,ex-unico',
    })
    assert r.status_code == 200
    quote = app_module.store.get('quotes', quote_id)
    assert quote['extras_total'] == 1000, 'un id repetido en extra_ids no debe multiplicar el precio'
    assert len(quote['selected_extras']) == 1


def test_extras_save_rechaza_precio_infinito_o_nan(auth_client):
    """json.loads acepta Infinity/NaN como extension no estandar -- si un
    POST manda eso como precio, no debe persistir tal cual (envenenaria
    precio_total/cuota_monto en cualquier accept futuro)."""
    import app as app_module
    quote_id = _crear_borrador_con_opcion(auth_client, app_module, ASTRAL, precio_total=1000)
    raw = '{"extras": [{"name": "Malicioso", "price": Infinity}, {"name": "NaN", "price": NaN}]}'
    r = auth_client.post(f'/api/quotes/{quote_id}/extras', data=raw, content_type='application/json')
    assert r.status_code == 200
    catalog = r.get_json()['extras_catalog']
    assert all(e['price'] == 0 for e in catalog), \
        'Infinity/NaN como precio debe normalizarse a 0, no persistir tal cual'


def test_extras_save_descarta_id_duplicado_en_el_mismo_guardado(auth_client):
    import app as app_module
    quote_id = _crear_borrador_con_opcion(auth_client, app_module, ASTRAL, precio_total=1000)
    r = auth_client.post(f'/api/quotes/{quote_id}/extras', json={'extras': [
        {'id': 'ex-rep', 'name': 'Primero', 'price': 100},
        {'id': 'ex-rep', 'name': 'Segundo (mismo id)', 'price': 9999},
    ]})
    assert r.status_code == 200
    catalog = r.get_json()['extras_catalog']
    assert len(catalog) == 1, 'un id repetido en el mismo guardado no debe duplicarse en el catalogo'
    assert catalog[0]['name'] == 'Primero'


def test_accept_con_extras_acepta_tambien_lista_json_de_ids(auth_client):
    """El form publico manda un string coma-separada, pero un cliente JSON
    (o un test) puede mandar una lista -- ambas formas deben funcionar."""
    import app as app_module
    quote_id = _crear_borrador_con_opcion(
        auth_client, app_module, ASTRAL, option_id='opt-e5', precio_total=4000)
    auth_client.post(f'/api/quotes/{quote_id}/extras', json={'extras': [
        {'id': 'ex-list', 'name': 'Extra lista', 'price': 500},
    ]})
    token = _enviar(auth_client, quote_id)

    r = auth_client.post(f'/q/{token}/accept', json={
        'option_id': 'opt-e5', 'plan_pago': 1, 'extra_ids': ['ex-list'],
    })
    assert r.status_code == 200
    quote = app_module.store.get('quotes', quote_id)
    assert quote['extras_total'] == 500


# ---------------------------------------------------------------------
# BLOQUE F: Settings > Cotizaciones -- tema y aislamiento entre cuentas
# ---------------------------------------------------------------------

def test_quote_theme_save_y_lectura_por_cuenta(client):
    import app as app_module
    login_as_tenant(client, ASTRAL, email='astral-theme@example.com')
    r = client.post('/api/settings/quote-theme', json={'accent': '#ff0000', 'cta_text': 'AGENDAR'})
    assert r.status_code == 200
    theme = app_module._quote_theme_for_tenant(ASTRAL)
    assert theme['accent'] == '#ff0000'
    assert theme['cta_text'] == 'AGENDAR'


def test_quote_theme_aislado_entre_cuentas(client):
    import app as app_module
    login_as_tenant(client, ASTRAL, email='astral-theme2@example.com')
    client.post('/api/settings/quote-theme', json={'accent': '#111111'})

    login_as_tenant(client, NORKEVIN, email='norkevin-theme2@example.com')
    client.post('/api/settings/quote-theme', json={'accent': '#222222'})

    assert app_module._quote_theme_for_tenant(ASTRAL)['accent'] == '#111111'
    assert app_module._quote_theme_for_tenant(NORKEVIN)['accent'] == '#222222'


def test_quote_theme_campo_vacio_vuelve_al_default(auth_client):
    import app as app_module
    auth_client.post('/api/settings/quote-theme', json={'accent': '#abcdef'})
    assert app_module._quote_theme_for_tenant(ASTRAL)['accent'] == '#abcdef'

    auth_client.post('/api/settings/quote-theme', json={'accent': ''})
    assert app_module._quote_theme_for_tenant(ASTRAL)['accent'] == '#c9a961', \
        'un campo vaciado debe volver al default, no quedar en blanco'


def test_settings_quotes_page_solo_muestra_datos_de_la_cuenta_activa(client):
    import app as app_module
    login_as_tenant(client, ASTRAL, email='astral-sq@example.com')
    client.post('/api/portfolio', json={'title': 'Boda Astral Only'})

    login_as_tenant(client, NORKEVIN, email='norkevin-sq@example.com')
    r = client.get('/settings/quotes')
    assert r.status_code == 200
    assert 'Boda Astral Only' not in r.get_data(as_text=True)
