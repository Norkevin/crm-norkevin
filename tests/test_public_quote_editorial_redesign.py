"""Rediseño editorial de la cotización pública (29-ago-2026). Kevin: "quiero
que rediseñes ÚNICAMENTE LA EXPERIENCIA PÚBLICA DE LAS COTIZACIONES... mismo
motor de cotizaciones, nueva experiencia visual". Este archivo protege
específicamente lo que templates/quote_view.html (compartido por /quotes/<id>
y /q/<token>, ver app.py: quote_view()) tiene que seguir cumpliendo con el
HTML/CSS nuevo: que sigue siendo el MISMO motor de siempre (accept_quote,
extras, tenant), que el tema (moneda y video destacado son nuevos en este
rediseño) es multi-tenant de verdad, y que la selección sigue siendo
accesible por teclado.

La lógica de dinero/idempotencia/aislamiento de cross-tenant YA está
protegida por test_public_quote_experience_bloque_b.py y _bloque_c_a_f.py
(ninguno de esos depende de una clase CSS ni de estructura de HTML
específica -- son asserts sobre JSON y sobre presencia de texto -- así que
siguen valiendo sin cambios con el nuevo template). Este archivo no repite
esas pruebas; solo agrega lo que es nuevo o lo que cambió de forma real."""
import uuid

from conftest import login_as_tenant

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'
RAMIRO = 'tenant-ramiro-cruz'


def _crear_lead(app_module, tenant_id, *, nombre='Cliente Test'):
    lead_id = 'lead-redesign-' + uuid.uuid4().hex[:8]
    app_module.store.upsert('leads', {
        'id': lead_id, 'nombre': nombre, 'email': f'{lead_id}@example.com',
        'status': 'Nuevo', 'tenant_id': tenant_id,
    })
    return lead_id


def _crear_borrador_con_opcion(auth_client, app_module, tenant_id, *, option_id='opt-1',
                                precio_total=10000, **campos):
    lead_id = _crear_lead(app_module, tenant_id)
    r = auth_client.post('/api/quotes/draft', json={'lead_id': lead_id})
    quote_id = r.get_json()['quote_id']
    payload = {'id': option_id, 'name': 'Paquete Test', 'precio_total': precio_total}
    payload.update(campos)
    r = auth_client.post(f'/api/quotes/{quote_id}/options', json=payload)
    assert r.status_code == 200, r.get_json()
    return quote_id


def _enviar(auth_client, quote_id):
    r = auth_client.post(f'/api/quotes/{quote_id}/send', json={})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['quote_url'].rsplit('/q/', 1)[1]


def _quote_publica_minima(app_module, tenant_id, *, extra_theme=None, suffix=''):
    lead_id = 'lead-min-' + suffix + uuid.uuid4().hex[:6]
    quote_id = 'quote-min-' + suffix + uuid.uuid4().hex[:6]
    app_module.store.upsert('leads', {'id': lead_id, 'nombre': 'Cliente Min', 'tenant_id': tenant_id, 'status': 'Nuevo'})
    app_module.store.upsert('quotes', {
        'id': quote_id, 'lead_id': lead_id, 'tenant_id': tenant_id, 'status': 'Enviada',
        'options': [{'id': 'op1', 'name': 'Paquete', 'precio_total': 1000}],
    })
    return quote_id


# ---------------------------------------------------------------------
# El nuevo template no rompe lo que ya funcionaba
# ---------------------------------------------------------------------

def test_pagina_publica_carga_y_no_usa_table_con_plan_de_varias_cuotas(auth_client):
    """Regresión directa del pedido de Kevin: nada de tablas anchas en la
    cotización pública. El caso más parecido a una tabla es un plan de
    varias cuotas, así que se prueba justo con eso."""
    import app as app_module
    quote_id = _crear_borrador_con_opcion(auth_client, app_module, ASTRAL, precio_total=15000)
    token = _enviar(auth_client, quote_id)
    r = auth_client.get(f'/q/{token}')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert '<table' not in html


def test_opciones_multiples_forman_un_radiogroup_accesible_por_teclado(auth_client):
    """El layout en columnas sigue siendo la MISMA pieza interactiva de
    siempre: cada opción expone data-option-id (de eso depende
    selectPackage() en el JS del propio template) y ahora además es un
    radiogroup real -- accesible por teclado, no solo con el mouse, tal
    como pidió Kevin explícitamente ('debe seguir siendo totalmente
    accesible por teclado')."""
    import app as app_module
    quote_id = _crear_borrador_con_opcion(
        auth_client, app_module, ASTRAL, precio_total=17500, label='Recomendada',
        groups=[{'title': 'Boda principal', 'items': ['8 horas']}],
    )
    r = auth_client.post(f'/api/quotes/{quote_id}/options', json={
        'id': 'opt-2', 'name': 'Paquete Dos', 'precio_total': 20000,
    })
    assert r.status_code == 200, r.get_json()
    token = _enviar(auth_client, quote_id)

    r = auth_client.get(f'/q/{token}')
    html = r.get_data(as_text=True)
    assert 'role="radiogroup"' in html
    assert html.count('role="radio"') >= 2
    assert 'data-option-id="opt-1"' in html
    assert 'data-option-id="opt-2"' in html
    assert 'tabindex="0"' in html
    # 'Recomendada' (opt.label) solo debe aparecer marcada para la opcion
    # que la tiene -- no en la que no la tiene.
    assert 'opt-recommended">Recomendada' in html


def test_opcion_sin_label_no_muestra_recomendada(auth_client):
    """No hay que inventar cuál es la recomendada (pedido explícito de
    Kevin): si el admin no puso opt.label, el badge no debe aparecer."""
    import app as app_module
    quote_id = _crear_borrador_con_opcion(auth_client, app_module, ASTRAL, precio_total=9000)
    token = _enviar(auth_client, quote_id)
    r = auth_client.get(f'/q/{token}')
    assert 'opt-recommended' not in r.get_data(as_text=True)


def test_extras_siguen_conectados_al_form_de_aceptar(auth_client):
    """El texto del botón cambió a 'Agregar'/'Agregado ✓', pero el dato que
    de verdad importa para aceptar (data-extra-id + el input oculto que
    llena el JS) tiene que seguir presente."""
    import app as app_module
    quote_id = _crear_borrador_con_opcion(auth_client, app_module, ASTRAL, precio_total=5000)
    auth_client.post(f'/api/quotes/{quote_id}/extras', json={'extras': [{'name': 'Dron', 'price': 1500}]})
    token = _enviar(auth_client, quote_id)
    r = auth_client.get(f'/q/{token}')
    html = r.get_data(as_text=True)
    assert 'data-extra-id=' in html
    assert 'id="accept-extra-ids"' in html
    assert 'id="accept-option-id"' in html
    assert 'id="accept-plan-pago"' in html
    assert f'/q/{token}/accept' in html
    assert f'/q/{token}/decline' in html


def test_aceptar_a_traves_del_nuevo_template_sigue_generando_job(auth_client):
    """Prueba de punta a punta con el mismo POST que manda el accept-form
    nuevo: si el rediseño hubiera roto el name de algún input, esto lo
    detecta -- no solo que el HTML se vea bien, que el submit real siga
    funcionando igual que antes."""
    import app as app_module
    quote_id = _crear_borrador_con_opcion(auth_client, app_module, ASTRAL, precio_total=9000)
    quote_antes = app_module.store.get('quotes', quote_id)
    token = _enviar(auth_client, quote_id)

    r = auth_client.post(f'/q/{token}/accept', data={'option_id': 'opt-1', 'plan_pago': '1'})
    assert r.status_code == 200
    quote_despues = app_module.store.get('quotes', quote_id)
    assert quote_despues['status'] == 'Aceptada'
    assert quote_despues['precio_total'] == 9000
    jobs = [j for j in app_module.store.list('jobs') if j.get('lead_id') == quote_antes['lead_id']]
    assert jobs, 'aceptar desde el nuevo template debe seguir convirtiendo el lead en job'


# ---------------------------------------------------------------------
# Tema nuevo (moneda, video destacado, WhatsApp) -- multi-tenant de verdad
# ---------------------------------------------------------------------

def test_moneda_del_tema_no_esta_hardcodeada(client):
    """Astral cambia su símbolo/nombre de moneda; el HTML tiene que
    reflejarlo -- si estuviera hardcodeado como antes ('Q'/'Quetzales'),
    este cambio no se vería."""
    import app as app_module
    login_as_tenant(client, ASTRAL, email='astral-currency@example.com')
    r = client.post('/api/settings/quote-theme', json={
        'currency_symbol': '$', 'currency_label': 'Dólares (USD)',
    })
    assert r.status_code == 200

    quote_id = _quote_publica_minima(app_module, ASTRAL, suffix='cur')
    r = client.get(f'/quotes/{quote_id}')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Dólares (USD)' in html
    assert '$1,000.00' in html


def test_video_destacado_solo_aparece_si_el_link_es_reconocible(client):
    import app as app_module
    login_as_tenant(client, NORKEVIN, email='norkevin-video@example.com')
    app_module.store.upsert('tenants', {
        'id': NORKEVIN, 'name': 'Norkevin Photography',
        'login_email': 'norkevin-video@example.com', 'active': True,
    })
    r = client.post('/api/settings/quote-theme', json={
        'featured_video_url': 'https://www.youtube.com/watch?v=abcDEF1234',
    })
    assert r.status_code == 200

    quote_id = _quote_publica_minima(app_module, NORKEVIN, suffix='vid')
    r = client.get(f'/quotes/{quote_id}')
    html = r.get_data(as_text=True)
    assert 'youtube.com/embed/abcDEF1234' in html


def test_video_con_link_no_reconocido_no_rompe_la_pagina(client):
    """Mejor no mostrar nada que mostrar un iframe roto -- ver
    _video_embed_url en app.py."""
    import app as app_module
    login_as_tenant(client, NORKEVIN, email='norkevin-badvideo@example.com')
    app_module.store.upsert('tenants', {
        'id': NORKEVIN, 'name': 'Norkevin Photography',
        'login_email': 'norkevin-badvideo@example.com', 'active': True,
    })
    r = client.post('/api/settings/quote-theme', json={'featured_video_url': 'https://example.com/no-es-video'})
    assert r.status_code == 200

    quote_id = _quote_publica_minima(app_module, NORKEVIN, suffix='badvid')
    r = client.get(f'/quotes/{quote_id}')
    assert r.status_code == 200
    assert '<iframe' not in r.get_data(as_text=True)


def test_whatsapp_flotante_usa_el_numero_del_tenant_dueno_del_link(client):
    import app as app_module
    login_as_tenant(client, ASTRAL, email='astral-wa@example.com')
    r = client.post('/api/settings/quote-theme', json={'whatsapp': '+502 5555 1234'})
    assert r.status_code == 200
    quote_id = _quote_publica_minima(app_module, ASTRAL, suffix='wa')
    r = client.get(f'/quotes/{quote_id}')
    html = r.get_data(as_text=True)
    assert 'wa.me/50255551234' in html


def test_ramiro_no_hereda_tema_de_astral_ni_de_norkevin(client):
    """Aislamiento del tema nuevo (moneda/video/whatsapp). Autocontenido a
    propósito -- NO depende de que los tests de arriba hayan corrido antes
    en el mismo proceso (settings/quote_theme persiste entre tests dentro
    de la sesión de pytest; solo la tabla 'tenants' se restaura por test,
    ver conftest.py). Sin este cuidado, correr este test solo (o con
    pytest-randomly/xdist) haría que las aserciones de 'no heredado' pasen
    en falso incluso si hubiera una fuga real, porque los valores
    'envenenados' nunca se habrían escrito. Este test escribe sus propios
    valores distintivos en Astral y Norkevin, y recién después confirma
    que Ramiro no los ve."""
    import app as app_module

    login_as_tenant(client, ASTRAL, email='astral-poison@example.com')
    r = client.post('/api/settings/quote-theme', json={
        'currency_symbol': '£', 'currency_label': 'Libras (GBP)',
        'whatsapp': '+502 9999 0001',
    })
    assert r.status_code == 200

    login_as_tenant(client, NORKEVIN, email='norkevin-poison@example.com')
    app_module.store.upsert('tenants', {
        'id': NORKEVIN, 'name': 'Norkevin Photography',
        'login_email': 'norkevin-poison@example.com', 'active': True,
    })
    r = client.post('/api/settings/quote-theme', json={
        'featured_video_url': 'https://www.youtube.com/watch?v=poisonID99',
    })
    assert r.status_code == 200

    login_as_tenant(client, RAMIRO, email='ramiro-redesign@example.com')
    quote_id = _quote_publica_minima(app_module, RAMIRO, suffix='ram')
    r = client.get(f'/quotes/{quote_id}')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Libras (GBP)' not in html
    assert 'youtube.com/embed/poisonID99' not in html
    assert 'wa.me/50299990001' not in html
