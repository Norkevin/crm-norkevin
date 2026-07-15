"""Kevin: el boton 'Pagar' en el Portal del cliente solo aparecia si el pago
ya tenia un payment_link_url guardado -- pero las cuotas que genera
_ensure_payments_for_quote() al aceptar una cotizacion nunca pasan por el
flujo de recordatorio ni por 'Generar link de pago' del admin, asi que el
cliente entraba a pagar y no habia ningun boton. El portal ahora genera el
link on-demand la primera vez que alguien visita la pagina."""
from unittest.mock import patch


def _make_client_with_pending_payment(app_module, suffix):
    import uuid
    client_id = f'client-portal-pay-{suffix}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Portal', 'last_name': 'Pay',
        'email': 'portalpay@example.com', 'tenant_id': 'tenant-norkevin',
    })
    job_id = f'job-portal-pay-{suffix}'
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Portal Pay',
        'tenant_id': 'tenant-norkevin',
    })
    pay_id = 'pay-' + uuid.uuid4().hex[:8]
    app_module.store.upsert('payments', {
        'id': pay_id, 'client_id': client_id, 'job_id': job_id,
        'invoice_id': f'INV-{suffix}', 'amount': 5000, 'status': 'Pendiente',
        'due_date': '2027-01-01', 'concepto': 'Cuota 1', 'tenant_id': 'tenant-norkevin',
    })
    return client_id, pay_id


def test_portal_generates_payment_link_when_missing(auth_client):
    import app as app_module
    client_id, pay_id = _make_client_with_pending_payment(app_module, 'a')

    with patch('src.recurrente.is_configured', return_value=True), \
         patch('src.recurrente.create_checkout', return_value={'ok': True, 'checkout_url': 'https://pay.example/xyz', 'id': 'chk_1'}):
        resp = auth_client.get(f'/portal/{client_id}')

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'https://pay.example/xyz' in html
    assert 'Pagar ahora' in html

    stored = app_module.store.get('payments', pay_id)
    assert stored['payment_link_url'] == 'https://pay.example/xyz'


def test_portal_skips_link_generation_when_recurrente_not_configured(auth_client):
    import app as app_module
    client_id, pay_id = _make_client_with_pending_payment(app_module, 'b')

    with patch('src.recurrente.is_configured', return_value=False):
        resp = auth_client.get(f'/portal/{client_id}')

    assert resp.status_code == 200
    stored = app_module.store.get('payments', pay_id)
    assert not stored.get('payment_link_url')


def test_portal_does_not_regenerate_existing_link(auth_client):
    import app as app_module
    client_id, pay_id = _make_client_with_pending_payment(app_module, 'c')
    pay = app_module.store.get('payments', pay_id)
    pay['payment_link_url'] = 'https://pay.example/already-there'
    app_module.store.upsert('payments', pay)

    with patch('src.recurrente.is_configured', return_value=True), \
         patch('src.recurrente.create_checkout') as mock_checkout:
        resp = auth_client.get(f'/portal/{client_id}')

    assert resp.status_code == 200
    mock_checkout.assert_not_called()
    assert 'https://pay.example/already-there' in resp.get_data(as_text=True)
