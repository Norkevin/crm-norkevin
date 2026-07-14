"""
recurrente.py - Genera links de pago con la API de Recurrente
(https://docs.recurrente.com), sin dependencias externas.

Flujo:
  1. Kevin obtiene su llave secreta en Recurrente > Configuracion > Llaves API.
  2. Kevin pone RECURRENTE_SECRET_KEY en su .env.
  3. Desde Payments/Invoice, boton "Generar link de pago" -> POST
     /api/payments/<id>/payment-link -> create_checkout() -> guarda
     payment_link_url en el payment y lo muestra como boton "Pagar ahora".
"""
import json
import os
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

API_URL = 'https://app.recurrente.com/api/checkouts'


def is_test_mode() -> bool:
    """RECURRENTE_MODE=test usa RECURRENTE_SECRET_KEY_TEST en vez de la llave
    live -- para probar el flujo de pago sin tocar la cuenta real."""
    return os.environ.get('RECURRENTE_MODE', 'live').strip().lower() == 'test'


def _secret_key() -> str:
    if is_test_mode():
        return os.environ.get('RECURRENTE_SECRET_KEY_TEST', '')
    return os.environ.get('RECURRENTE_SECRET_KEY', '')


def is_configured() -> bool:
    return bool(_secret_key())


def create_checkout(*, name: str, amount_in_cents: int, currency: str = 'GTQ',
                     success_url: str = None, cancel_url: str = None) -> dict:
    """Crea un checkout en Recurrente y devuelve {'ok': True, 'checkout_url': ..., 'id': ...}
    o {'ok': False, 'error': ...} si algo falla."""
    secret_key = _secret_key()
    if not secret_key:
        env_var = 'RECURRENTE_SECRET_KEY_TEST' if is_test_mode() else 'RECURRENTE_SECRET_KEY'
        return {'ok': False, 'error': f'{env_var} no configurada en .env'}

    min_cents = 500 if currency == 'GTQ' else 100
    if amount_in_cents < min_cents:
        return {'ok': False, 'error': f'El monto minimo para {currency} es {min_cents} centavos'}

    payload = {
        'items': [{
            'name': name[:200],
            'amount_in_cents': amount_in_cents,
            'currency': currency,
            'quantity': 1,
        }],
    }
    if success_url:
        payload['success_url'] = success_url
    if cancel_url:
        payload['cancel_url'] = cancel_url

    body = json.dumps(payload).encode('utf-8')
    req = urlrequest.Request(
        API_URL,
        data=body,
        method='POST',
        headers={
            'X-SECRET-KEY': secret_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return {'ok': True, 'checkout_url': data.get('checkout_url'), 'id': data.get('id')}
    except HTTPError as e:
        try:
            err_body = json.loads(e.read().decode('utf-8'))
            msg = err_body.get('error') or str(err_body)
        except Exception:
            msg = f'HTTP {e.code}'
        return {'ok': False, 'error': f'Recurrente: {msg}'}
    except URLError as e:
        return {'ok': False, 'error': f'No se pudo conectar con Recurrente: {e.reason}'}
