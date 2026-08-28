"""BLOQUE B (Public Quote Experience, 28-ago-2026): extensiones de modelo
sobre el sistema de Quotes existente -- numeracion por cuenta, token
publico conectado a src/public_tokens.py, opciones con grupos/extras,
portfolio/condiciones/templates por empresa, y snapshot inmutable al
enviar. Ver PUBLIC_QUOTES_AUDIT.md para el mapa completo.

Regla de esta suite: nada de lo de aca debe poder tocar accept_quote(),
_convert_lead_to_job(), conversion_registry, _ensure_payments_for_quote ni
tenant_brand_map -- se prueba que la nueva capa se CONECTA a ese motor sin
duplicarlo ni cambiarlo.

No se corre en este sandbox (falta pytest/Flask) -- ver
STABILIZATION_EXECUTION_REPORT.md, seccion BLOCKED_BY_MISSING_DEPENDENCY.
Verificado con tools/verificacion_final.py (compilacion + AST) en su lugar.
"""
import uuid

import pytest

from conftest import login_as_tenant

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'


def _crear_lead(app_module, tenant_id, *, nombre='Cliente Test', email=None):
    lead_id = 'lead-pqe-' + uuid.uuid4().hex[:8]
    email = email or f'{lead_id}@example.com'
    app_module.store.upsert('leads', {
        'id': lead_id, 'nombre': nombre, 'email': email,
        'status': 'Nuevo', 'tenant_id': tenant_id,
    })
    return lead_id


# ---------------------------------------------------------------------
# Numeracion por cuenta (JsonStore.next_sequence_number)
# ---------------------------------------------------------------------

def test_next_sequence_number_es_secuencial_y_por_cuenta(flask_app):
    import app as app_module
    store = app_module.store

    a1 = store.next_sequence_number('quotes_test', tenant_id=ASTRAL, year=2099)
    a2 = store.next_sequence_number('quotes_test', tenant_id=ASTRAL, year=2099)
    n1 = store.next_sequence_number('quotes_test', tenant_id=NORKEVIN, year=2099)

    assert (a1, a2) == (1, 2), 'la secuencia de Astral debe avanzar 1, 2, ...'
    assert n1 == 1, 'Norkevin tiene su PROPIO contador -- no hereda el de Astral'


def test_next_sequence_number_reinicia_por_anio(flask_app):
    import app as app_module
    store = app_module.store
    tenant = ASTRAL + '-anio-test-' + uuid.uuid4().hex[:6]

    v2099 = store.next_sequence_number('quotes_test', tenant_id=tenant, year=2099)
    v2100 = store.next_sequence_number('quotes_test', tenant_id=tenant, year=2100)
    assert v2099 == 1
    assert v2100 == 1, 'un anio nuevo empieza su propio contador desde 1'


def test_next_sequence_number_sin_tenant_id_revienta(flask_app):
    import app as app_module
    from src.storage import MissingTenantContextError
    with pytest.raises(MissingTenantContextError):
        app_module.store.next_sequence_number('quotes_test', tenant_id=None)


def test_assign_quote_number_prefijo_correcto_por_marca(flask_app):
    import app as app_module

    q_astral = {'id': 'q-1', 'tenant_id': ASTRAL}
    app_module._assign_quote_number(q_astral, ASTRAL)
    assert q_astral['number'].startswith('AST-'), q_astral['number']

    q_norkevin = {'id': 'q-2', 'tenant_id': NORKEVIN}
    app_module._assign_quote_number(q_norkevin, NORKEVIN)
    assert q_norkevin['number'].startswith('NORK-'), q_norkevin['number']


def test_assign_quote_number_es_idempotente(flask_app):
    """Volver a llamar sobre un quote que YA tiene numero no debe quemar
    otro turno de la secuencia (evita saltos de numero en un resave)."""
    import app as app_module
    q = {'id': 'q-3', 'tenant_id': ASTRAL}
    app_module._assign_quote_number(q, ASTRAL)
    primero = q['number']
    app_module._assign_quote_number(q, ASTRAL)
    assert q['number'] == primero


def test_assign_quote_number_sin_marca_resuelta_no_inventa_prefijo(flask_app):
    """tenant_id desconocido/legado (ver tenant_brand_map.py) -> sin numero,
    nunca un prefijo adivinado. Mismo fail-hard que el resto del sistema."""
    import app as app_module
    q = {'id': 'q-4', 'tenant_id': 'tenant-que-no-existe'}
    app_module._assign_quote_number(q, 'tenant-que-no-existe')
    assert 'number' not in q or not q['number']


# ---------------------------------------------------------------------
# Opciones extendidas (grupos, backward-compat, limite de 3)
# ---------------------------------------------------------------------

def test_option_save_con_groups_deriva_incluye_plano(auth_client):
    resp = auth_client.post('/api/quotes/draft', json={'lead_id': _crear_lead(
        __import__('app'), ASTRAL)})
    quote_id = resp.get_json()['quote_id']

    resp = auth_client.post(f'/api/quotes/{quote_id}/options', json={
        'name': 'Fotografia + Video',
        'precio_total': 17500,
        'groups': [
            {'title': 'Boda principal · Fotografia', 'items': ['2 fotografos', '10 horas']},
            {'title': 'Boda principal · Video', 'items': ['1 videografo', 'Pelicula final']},
        ],
    })
    assert resp.status_code == 200, resp.get_json()
    option = resp.get_json()['options'][0]
    assert option['groups'][0]['title'] == 'Boda principal · Fotografia'
    # incluye (plano, lo que ya lee el PDF) se deriva de los grupos:
    assert 'Boda principal · Fotografia: 2 fotografos' in option['incluye']
    assert 'Boda principal · Video: 1 videografo' in option['incluye']


def test_option_save_sin_groups_sigue_funcionando_como_antes(auth_client):
    """Backward-compat: un builder viejo (o un test viejo) que solo manda
    name/precio_total/incluye plano sigue funcionando identico."""
    import app as app_module
    resp = auth_client.post('/api/quotes/draft', json={'lead_id': _crear_lead(app_module, ASTRAL)})
    quote_id = resp.get_json()['quote_id']

    resp = auth_client.post(f'/api/quotes/{quote_id}/options', json={
        'name': 'Basico', 'precio_total': 9000, 'incluye': 'Item 1\nItem 2',
    })
    assert resp.status_code == 200
    option = resp.get_json()['options'][0]
    assert option['incluye'] == ['Item 1', 'Item 2']
    assert option['groups'] == []


def test_option_save_sigue_limitando_a_3_opciones(auth_client):
    import app as app_module
    resp = auth_client.post('/api/quotes/draft', json={'lead_id': _crear_lead(app_module, ASTRAL)})
    quote_id = resp.get_json()['quote_id']
    for i in range(3):
        r = auth_client.post(f'/api/quotes/{quote_id}/options',
                              json={'name': f'Opcion {i}', 'precio_total': 1000 + i})
        assert r.status_code == 200
    r = auth_client.post(f'/api/quotes/{quote_id}/options',
                          json={'name': 'Opcion 4', 'precio_total': 5000})
    assert r.status_code == 400


# ---------------------------------------------------------------------
# Extras
# ---------------------------------------------------------------------

def test_extras_catalog_save_valida_y_normaliza(auth_client):
    import app as app_module
    resp = auth_client.post('/api/quotes/draft', json={'lead_id': _crear_lead(app_module, ASTRAL)})
    quote_id = resp.get_json()['quote_id']

    resp = auth_client.post(f'/api/quotes/{quote_id}/extras', json={'extras': [
        {'name': 'Hora adicional', 'price': 1000},
        {'name': 'Segundo videografo', 'price': -500},  # precio invalido -> se normaliza a 0
        {'name': ''},  # sin nombre -> se descarta
    ]})
    assert resp.status_code == 200
    catalog = resp.get_json()['extras_catalog']
    assert len(catalog) == 2
    assert catalog[0]['price'] == 1000
    assert catalog[1]['price'] == 0


# ---------------------------------------------------------------------
# Portfolio / condiciones / templates -- aislamiento por cuenta
# ---------------------------------------------------------------------

def test_portfolio_aislado_entre_cuentas(client):
    import app as app_module
    login_as_tenant(client, ASTRAL, email='astral-pqe@example.com')
    r = client.post('/api/portfolio', json={'title': 'Boda Astral Secreta'})
    assert r.status_code == 200

    login_as_tenant(client, NORKEVIN, email='norkevin-pqe@example.com')
    r = client.get('/api/portfolio')
    titulos = [i['title'] for i in r.get_json()['items']]
    assert 'Boda Astral Secreta' not in titulos, \
        'un item de portfolio de Astral NUNCA debe verse desde Norkevin'


def test_quote_terms_template_aislado_entre_cuentas(client):
    login_as_tenant(client, ASTRAL, email='astral-pqe2@example.com')
    r = client.post('/api/quote-terms-templates', json={
        'title': 'Condiciones Astral',
        'blocks': [{'title': 'Cobertura', 'body': 'Las horas son continuas.'}],
    })
    assert r.status_code == 200

    login_as_tenant(client, NORKEVIN, email='norkevin-pqe2@example.com')
    r = client.get('/api/quote-terms-templates')
    titulos = [i['title'] for i in r.get_json()['items']]
    assert 'Condiciones Astral' not in titulos


# ---------------------------------------------------------------------
# Snapshot inmutable al enviar
# ---------------------------------------------------------------------

def test_snapshot_no_cambia_si_se_edita_el_portfolio_despues(auth_client):
    import app as app_module
    lead_id = _crear_lead(app_module, ASTRAL)

    r = auth_client.post('/api/portfolio', json={'title': 'Melissa & Joshua', 'order': 1})
    portfolio_id = r.get_json()['item']['id']

    r = auth_client.post('/api/quotes/draft', json={'lead_id': lead_id})
    quote_id = r.get_json()['quote_id']
    auth_client.post(f'/api/quotes/{quote_id}/options',
                      json={'name': 'Paquete', 'precio_total': 10000})

    r = auth_client.post(f'/api/quotes/{quote_id}/send', json={})
    assert r.status_code == 200, r.get_json()

    quote_antes = app_module.store.get('quotes', quote_id)
    assert any(p['title'] == 'Melissa & Joshua' for p in quote_antes['portfolio_snapshot'])

    # Se edita el portfolio DESPUES de enviar la cotizacion.
    auth_client.post('/api/portfolio', json={
        'id': portfolio_id, 'title': 'Nombre Cambiado', 'order': 1,
    })

    quote_despues = app_module.store.get('quotes', quote_id)
    assert any(p['title'] == 'Melissa & Joshua' for p in quote_despues['portfolio_snapshot']), \
        'el snapshot tomado al enviar no debe cambiar aunque se edite el portfolio despues'


# ---------------------------------------------------------------------
# Token publico: conecta a public_tokens.py, sin duplicar accept_quote
# ---------------------------------------------------------------------

def _crear_y_enviar_quote(auth_client, app_module, tenant_id):
    lead_id = _crear_lead(app_module, tenant_id)
    r = auth_client.post('/api/quotes/draft', json={'lead_id': lead_id})
    quote_id = r.get_json()['quote_id']
    auth_client.post(f'/api/quotes/{quote_id}/options',
                      json={'name': 'Paquete', 'precio_total': 12000})
    r = auth_client.post(f'/api/quotes/{quote_id}/send', json={})
    quote_url = r.get_json()['quote_url']
    token = quote_url.rsplit('/q/', 1)[1]
    return quote_id, token


def test_send_emite_token_y_deja_alias_interno_funcionando(auth_client):
    import app as app_module
    quote_id, token = _crear_y_enviar_quote(auth_client, app_module, ASTRAL)

    quote = app_module.store.get('quotes', quote_id)
    assert quote.get('public_token_hash'), 'send debe emitir un token publico'

    # El alias interno /quotes/<id> sigue funcionando (enlaces viejos no se rompen).
    r_legacy = auth_client.get(f'/quotes/{quote_id}')
    assert r_legacy.status_code == 200


def test_public_token_resuelve_anonimo_sin_sesion(client):
    """El caso real: un cliente que nunca inicio sesion abre el link que le
    llego por correo."""
    import app as app_module
    login_as_tenant(client, ASTRAL, email='astral-token@example.com')
    quote_id, token = _crear_y_enviar_quote(client, app_module, ASTRAL)

    with client.session_transaction() as sess:
        sess.clear()  # simula al cliente real: nunca hizo login

    r = client.get(f'/q/{token}')
    assert r.status_code == 200


def test_public_token_de_otra_cuenta_no_resuelve_bajo_otra_sesion(client):
    """Un token de Astral no debe poder verse desde una sesion de Norkevin
    -- mismo aislamiento que ya protege /quotes/<id>."""
    import app as app_module
    login_as_tenant(client, ASTRAL, email='astral-token2@example.com')
    _, token_astral = _crear_y_enviar_quote(client, app_module, ASTRAL)

    login_as_tenant(client, NORKEVIN, email='norkevin-token2@example.com')
    r = client.get(f'/q/{token_astral}')
    assert r.status_code == 404


def test_public_token_invalido_da_404(client):
    r = client.get('/q/token-que-no-existe-' + uuid.uuid4().hex)
    assert r.status_code == 404


def test_accept_via_token_usa_el_mismo_motor_sin_duplicar_ni_duplicar_job(auth_client):
    """El punto central de BLOQUE B: /q/<token>/accept no reimplementa
    accept_quote -- llama a la MISMA funcion. Se verifica por el resultado:
    un job creado, un solo payment_schedule activo, igual que aceptando por
    /quotes/<id>/accept (comportamiento ya cubierto en otras suites)."""
    import app as app_module
    lead_id = _crear_lead(app_module, ASTRAL)
    r = auth_client.post('/api/quotes/draft', json={'lead_id': lead_id})
    quote_id = r.get_json()['quote_id']
    auth_client.post(f'/api/quotes/{quote_id}/options',
                      json={'name': 'Paquete', 'precio_total': 8000, 'id': 'opt-unico'})
    r = auth_client.post(f'/api/quotes/{quote_id}/send', json={})
    token = r.get_json()['quote_url'].rsplit('/q/', 1)[1]

    r = auth_client.post(f'/q/{token}/accept', json={'option_id': 'opt-unico', 'plan_pago': 1})
    assert r.status_code == 200

    quote = app_module.store.get('quotes', quote_id)
    assert quote['status'] == 'Aceptada'
    job = app_module.get_job(quote['job_id'])
    assert job is not None

    activos = [s for s in app_module.store.list('payment_schedules')
               if s.get('job_id') == job['id'] and s.get('status') == 'active']
    assert len(activos) == 1, 'exactamente 1 calendario de pagos activo, sin duplicar'

    # Doble aceptacion (refresh/retry) via el mismo token: no debe duplicar nada.
    r2 = auth_client.post(f'/q/{token}/accept', json={'option_id': 'opt-unico', 'plan_pago': 1})
    assert r2.status_code == 200
    activos_despues = [s for s in app_module.store.list('payment_schedules')
                       if s.get('job_id') == job['id'] and s.get('status') == 'active']
    assert len(activos_despues) == 1, 'reintentar aceptar por el token no debe crear un 2do schedule'
