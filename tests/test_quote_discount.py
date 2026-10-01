"""El descuento se aplica una sola vez y llega al importe contratado."""
import uuid

import pytest


@pytest.fixture
def draft(auth_client):
    import app
    lead_id = 'lead-discount-' + uuid.uuid4().hex[:8]
    app.store.upsert('leads', {
        'id': lead_id, 'nombre': 'Ana y Luis', 'email': 'discount@example.com',
        'tenant_id': 'tenant-norkevin', 'status': 'Nuevo',
    })
    response = auth_client.post('/api/quotes/draft', json={'lead_id': lead_id})
    assert response.status_code == 200
    return response.get_json()['quote_id']


@pytest.mark.parametrize('extra_price', [0, 500])
def test_discount_save_edit_remove_and_accept(auth_client, draft, extra_price):
    import app
    endpoint = f'/api/quotes/{draft}/options'
    payload = {'name': 'Mix Gold', 'precio_base': '20500', 'descuento': '1500', 'precio_total': 1}
    result = auth_client.post(endpoint, json=payload)
    assert result.status_code == 200
    option = result.get_json()['options'][0]
    assert (option['precio_base'], option['precio_anterior'], option['precio_total']) == (20500, 20500, 19000)
    payload['id'] = option['id']
    # Guardar la misma opción no vuelve a restar del precio final.
    assert auth_client.post(endpoint, json=payload).get_json()['options'][0]['precio_total'] == 19000
    payload['descuento'] = 0
    cleared = auth_client.post(endpoint, json=payload).get_json()['options'][0]
    assert cleared['precio_total'] == 20500
    assert cleared['precio_anterior'] is None
    payload['descuento'] = 1500
    auth_client.post(endpoint, json=payload)
    editor = auth_client.get(f'/quotes/{draft}/edit').get_data(as_text=True)
    assert 'Descuento aplicado: Q1,500.00' in editor
    extras = auth_client.post(f'/api/quotes/{draft}/extras', json={'extras': [{'name': 'Boda civil', 'price': extra_price}]}).get_json()['extras_catalog']
    sent = auth_client.post(f'/api/quotes/{draft}/send', json={})
    assert sent.status_code == 200, sent.get_json()
    token = sent.get_json()['quote_url'].rsplit('/q/', 1)[1]
    public = auth_client.get(f'/q/{token}').get_data(as_text=True)
    assert 'Descuento aplicado: −Q1,500.00' in public
    assert 'data-price="19000.0"' in public
    assert auth_client.post(endpoint, json=payload).status_code == 400
    accepted = auth_client.post(f'/q/{token}/accept', json={'option_id': option['id'], 'plan_pago': 2, 'extra_ids': [extras[0]['id']]})
    assert accepted.status_code in (200, 302), accepted.get_data(as_text=True)
    quote = app.store.get('quotes', draft)
    assert quote['precio_total'] == 19000 + extra_price
    assert quote['snapshot_aceptado']['total'] == 19000 + extra_price
    assert quote['snapshot_aceptado']['descuento'] == 1500
    assert 'Descuento aplicado: −Q1,500.00' in auth_client.get(f'/q/{token}').get_data(as_text=True)
    payments = [p for p in app.store.list('payments') if p.get('quote_id') == draft]
    assert payments
    assert sum(float(p['amount']) for p in payments) == 19000 + extra_price


@pytest.mark.parametrize('price,discount', [('100', '-1'), ('100', '100'), ('100', '101'), ('100', 'NaN'), ('Infinity', '0'), ('100', 'bad'), ('0', '0'), ('-1', '0')])
def test_invalid_discount_does_not_save(auth_client, draft, price, discount):
    response = auth_client.post(f'/api/quotes/{draft}/options', json={
        'name': 'Foto Gold', 'precio_base': price, 'descuento': discount,
    })
    assert response.status_code == 400
    import app
    assert app.store.get('quotes', draft)['options'] == []


def test_decimal_rounding_and_legacy_final_price(auth_client, draft):
    endpoint = f'/api/quotes/{draft}/options'
    option = auth_client.post(endpoint, json={
        'name': 'Video', 'precio_base': '100.105', 'descuento': '0.105',
    }).get_json()['options'][0]
    assert option['precio_total'] == 100
    legacy = auth_client.post(endpoint, json={
        'name': 'Anterior', 'precio_total': 500, 'descuento': 50,
    }).get_json()['options'][1]
    assert legacy['precio_total'] == 500
    assert legacy['precio_base'] is None


@pytest.mark.parametrize('name,category,expected', [
    ('Mix Gold', '', 'Mix'), ('Mix Platinum', '', 'Mix'),
    ('GOLD MIX', 'Photo Collection', 'Mix'), ('PHOTO GOLD', '', 'Foto'),
    ('Fotografía Platinum', '', 'Foto'), ('VIDEO GOLD', '', 'Video'),
    ('Boda civil', 'Photo Collection', 'Extras'), ('Save the Date', 'Video', 'Extras'),
    ('Trash the Dress', 'Foto', 'Extras'), ('Wedding Content Creator', '', 'Extras'),
    ('Hora adicional', 'Video', 'Extras'),
])
def test_package_sections(flask_app, name, category, expected):
    import app
    assert app._package_section({'name': name, 'category': category}) == expected
