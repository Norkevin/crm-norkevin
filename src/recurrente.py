"""
recurrente.py - Genera links de pago con la API de Recurrente
(https://docs.recurrente.com), sin dependencias externas.

Multi-tenant (3 cuentas independientes): cada tenant conecta su PROPIA
cuenta de Recurrente desde Settings (ya no una unica llave global en el
.env). Las credenciales se cifran en reposo con Fernet (cryptography),
usando una clave derivada de FLASK_SECRET -- nunca se guardan en texto
plano ni se devuelven completas en ninguna respuesta de la API.

Flujo:
  1. Kevin obtiene su llave secreta en Recurrente > Configuracion > Llaves API.
  2. Desde Settings > Recurrente > "Conectar con Recurrente", la pega ahi
     (queda cifrada en data/recurrente_credentials_<tenant_id>.json).
  3. Desde Payments/Invoice, boton "Generar link de pago" -> POST
     /api/payments/<id>/payment-link -> create_checkout() -> guarda
     payment_link_url en el payment y lo muestra como boton "Pagar ahora".
"""
import base64
import hashlib
import json
import os
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from .storage import store

API_URL = 'https://app.recurrente.com/api/checkouts'
CREDENTIALS_TABLE = 'recurrente_credentials'


def _fernet():
    """Fernet key derivada de FLASK_SECRET -- no hace falta pedirle a
    Kevin una env var nueva en Render, y sigue siendo especifica de este
    despliegue (si FLASK_SECRET cambia, las credenciales guardadas dejan
    de poder descifrarse, igual que pasaria con cualquier llave rotada)."""
    from cryptography.fernet import Fernet

    secret = os.environ.get('FLASK_SECRET', 'norkevin-crm-dev-secret-change-me')
    digest = hashlib.sha256(secret.encode('utf-8')).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def _encrypt(value: str) -> str:
    if not value:
        return ''
    return _fernet().encrypt(value.encode('utf-8')).decode('utf-8')


def _decrypt(value: str) -> str:
    if not value:
        return ''
    try:
        return _fernet().decrypt(value.encode('utf-8')).decode('utf-8')
    except Exception:
        return ''


def _mask(value: str) -> str:
    if not value:
        return ''
    return ('*' * max(len(value) - 4, 0)) + value[-4:]


def get_credentials(tenant_id=None) -> dict:
    """Uso interno -- credenciales YA DESCIFRADAS. Nunca exponer el
    resultado de esto directamente en una respuesta JSON."""
    raw = store.get_tenant_dict(CREDENTIALS_TABLE, tenant_id=tenant_id)
    return {
        'secret_key': _decrypt(raw.get('secret_key_encrypted', '')),
        'secret_key_test': _decrypt(raw.get('secret_key_test_encrypted', '')),
        'mode': raw.get('mode', 'live'),
        'connected_at': raw.get('connected_at'),
        'last_test_ok': raw.get('last_test_ok'),
        'last_error': raw.get('last_error'),
    }


def save_credentials(secret_key='', secret_key_test='', mode='live', tenant_id=None):
    from datetime import datetime
    existing = store.get_tenant_dict(CREDENTIALS_TABLE, tenant_id=tenant_id)
    updated = dict(existing)
    if secret_key:
        updated['secret_key_encrypted'] = _encrypt(secret_key)
    if secret_key_test:
        updated['secret_key_test_encrypted'] = _encrypt(secret_key_test)
    updated['mode'] = mode if mode in ('live', 'test') else 'live'
    updated['connected_at'] = datetime.now().isoformat()
    updated.pop('last_error', None)
    store.save_tenant_dict(CREDENTIALS_TABLE, updated, tenant_id=tenant_id)


def disconnect(tenant_id=None):
    store.save_tenant_dict(CREDENTIALS_TABLE, {}, tenant_id=tenant_id)


def connection_status(tenant_id=None) -> dict:
    """'connected' | 'not_connected' | 'error' -- para el bloque de
    Settings. Nunca incluye la llave completa, solo los ultimos 4
    caracteres para que Kevin reconozca cual tiene puesta."""
    raw = store.get_tenant_dict(CREDENTIALS_TABLE, tenant_id=tenant_id)
    creds = get_credentials(tenant_id=tenant_id)
    active_key = creds['secret_key_test'] if creds['mode'] == 'test' else creds['secret_key']
    if not active_key:
        return {'status': 'not_connected', 'mode': creds['mode'], 'masked_key': ''}
    if raw.get('last_error'):
        return {'status': 'error', 'mode': creds['mode'], 'masked_key': _mask(active_key), 'error': raw['last_error']}
    return {'status': 'connected', 'mode': creds['mode'], 'masked_key': _mask(active_key), 'connected_at': raw.get('connected_at')}


def test_connection(tenant_id=None) -> dict:
    """Valida la llave guardada contra la API real (GET a la misma base
    del endpoint de checkouts) sin crear ningun checkout de verdad."""
    from datetime import datetime
    creds = get_credentials(tenant_id=tenant_id)
    active_key = creds['secret_key_test'] if creds['mode'] == 'test' else creds['secret_key']
    if not active_key:
        return {'ok': False, 'error': 'No hay credenciales guardadas todavia'}

    req = urlrequest.Request(
        API_URL,
        method='GET',
        headers={'X-SECRET-KEY': active_key, 'Accept': 'application/json'},
    )
    raw = store.get_tenant_dict(CREDENTIALS_TABLE, tenant_id=tenant_id)
    try:
        with urlrequest.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 201):
                raw.pop('last_error', None)
                raw['last_test_ok'] = datetime.now().isoformat()
                store.save_tenant_dict(CREDENTIALS_TABLE, raw, tenant_id=tenant_id)
                return {'ok': True}
    except HTTPError as e:
        if e.code == 401:
            msg = 'Llave invalida o revocada (401)'
        else:
            msg = f'Recurrente respondio HTTP {e.code}'
        raw['last_error'] = msg
        store.save_tenant_dict(CREDENTIALS_TABLE, raw, tenant_id=tenant_id)
        return {'ok': False, 'error': msg}
    except URLError as e:
        msg = f'No se pudo conectar con Recurrente: {e.reason}'
        raw['last_error'] = msg
        store.save_tenant_dict(CREDENTIALS_TABLE, raw, tenant_id=tenant_id)
        return {'ok': False, 'error': msg}
    raw.pop('last_error', None)
    raw['last_test_ok'] = datetime.now().isoformat()
    store.save_tenant_dict(CREDENTIALS_TABLE, raw, tenant_id=tenant_id)
    return {'ok': True}


def is_test_mode(tenant_id=None) -> bool:
    return get_credentials(tenant_id=tenant_id)['mode'] == 'test'


def _secret_key(tenant_id=None) -> str:
    creds = get_credentials(tenant_id=tenant_id)
    return creds['secret_key_test'] if creds['mode'] == 'test' else creds['secret_key']


def is_configured(tenant_id=None) -> bool:
    return bool(_secret_key(tenant_id=tenant_id))


def create_checkout(*, name: str, amount_in_cents: int, currency: str = 'GTQ',
                     success_url: str = None, cancel_url: str = None, tenant_id=None) -> dict:
    """Crea un checkout en Recurrente usando la llave DE LA CUENTA tenant_id
    (nunca una global) y devuelve {'ok': True, 'checkout_url': ..., 'id': ...}
    o {'ok': False, 'error': ...} si algo falla."""
    secret_key = _secret_key(tenant_id=tenant_id)
    if not secret_key:
        return {'ok': False, 'error': 'Recurrente no esta conectado para esta cuenta. Conectalo en Settings.'}

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
