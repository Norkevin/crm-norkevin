"""
gmail_delivery.py - Envio de correos reales usando la cuenta de Gmail de Kevin
via OAuth2 (Gmail API), sin depender de google-api-python-client.

Flujo:
  1. Kevin crea un OAuth Client ID (tipo "Web application") en
     https://console.cloud.google.com/apis/credentials, habilita la Gmail API,
     y agrega como Authorized redirect URI la que le mostramos en Settings
     (depende del dominio actual -- ver nota en app.py sobre el tunnel).
  2. Kevin pone GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET en su .env.
  3. Desde Settings > Email Settings, boton "Connect Gmail" -> /auth/google/start
     -> pantalla de consentimiento de Google -> /auth/google/callback
     -> guardamos access_token + refresh_token en data/google_token.json.
  4. A partir de ahi, send_gmail() se usa automaticamente si hay un token
     valido (ver email_delivery.send_email()).
"""
import json
import os
import time
import base64
import uuid
from email.mime.text import MIMEText
from pathlib import Path
from urllib import request as urlrequest, parse as urlparse
from urllib.error import HTTPError, URLError

SCOPE = 'https://www.googleapis.com/auth/gmail.send'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
SEND_URL = 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send'


# Multi-tenant: cada cuenta conecta su PROPIO Gmail (Kevin: 'la conexion de
# Gmail tambien debe mantenerse separada por cuenta'). tenant_resolver es
# la misma pieza que src/storage.py -- una funcion intercambiable que
# app.py configura una sola vez apuntando a session['tenant_id'], para no
# importar Flask directamente aca. Un tenant_id explicito (para el hilo de
# recordatorios en segundo plano, que ya sabe de que job/payment se trata)
# siempre le gana al resolver ambiente.
tenant_resolver = None


def _current_tenant_id():
    if tenant_resolver is None:
        return None
    try:
        return tenant_resolver()
    except Exception:
        return None


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _token_path(tenant_id=None) -> Path:
    resolved = tenant_id or _current_tenant_id()
    filename = f'google_token_{resolved}.json' if resolved else 'google_token.json'
    data_dir = os.environ.get('CRM_DATA_DIR')
    if data_dir:
        return Path(data_dir) / filename
    return _project_root() / 'data' / filename


def is_configured():
    """True si Kevin ya puso las credenciales de la app OAuth en .env
    (esto SI es global -- es el OAuth Client ID de la aplicacion Flow CRM
    misma, no de una cuenta de Gmail en particular)."""
    return bool(os.environ.get('GOOGLE_CLIENT_ID') and os.environ.get('GOOGLE_CLIENT_SECRET'))


def load_token(tenant_id=None):
    path = _token_path(tenant_id=tenant_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def save_token(data, tenant_id=None):
    path = _token_path(tenant_id=tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def disconnect(tenant_id=None):
    path = _token_path(tenant_id=tenant_id)
    if path.exists():
        path.unlink()


def is_connected(tenant_id=None):
    tok = load_token(tenant_id=tenant_id)
    return bool(tok and tok.get('refresh_token'))


def connected_email(tenant_id=None):
    tok = load_token(tenant_id=tenant_id)
    return (tok or {}).get('email', '')


def build_authorization_url(redirect_uri, state):
    params = {
        'client_id': os.environ.get('GOOGLE_CLIENT_ID', ''),
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': SCOPE + ' openid email',
        'access_type': 'offline',
        'prompt': 'consent',
        'state': state,
    }
    return AUTH_URL + '?' + urlparse.urlencode(params)


def _post_form(url, fields):
    data = urlparse.urlencode(fields).encode('utf-8')
    req = urlrequest.Request(url, data=data, headers={
        'Content-Type': 'application/x-www-form-urlencoded',
    }, method='POST')
    with urlrequest.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode('utf-8') or '{}')


def exchange_code_for_token(code, redirect_uri):
    """Intercambia el 'code' del callback por access_token + refresh_token.
    Se guarda para el tenant de la sesion activa (resolver ambiente) --
    esto siempre corre dentro de un request autenticado (el connect flow
    exige estar logueado)."""
    payload = _post_form(TOKEN_URL, {
        'code': code,
        'client_id': os.environ.get('GOOGLE_CLIENT_ID', ''),
        'client_secret': os.environ.get('GOOGLE_CLIENT_SECRET', ''),
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    })
    email = _fetch_email(payload.get('access_token', ''))
    token = {
        'access_token': payload.get('access_token'),
        'refresh_token': payload.get('refresh_token'),
        'expires_at': time.time() + int(payload.get('expires_in', 3600)) - 60,
        'email': email,
    }
    save_token(token)
    return token


def _fetch_email(access_token):
    if not access_token:
        return ''
    req = urlrequest.Request(
        'https://www.googleapis.com/oauth2/v2/userinfo',
        headers={'Authorization': f'Bearer {access_token}'},
    )
    try:
        with urlrequest.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode('utf-8') or '{}')
        return data.get('email', '')
    except Exception:
        return ''


def _refresh_if_needed(token, tenant_id=None):
    if token.get('access_token') and token.get('expires_at', 0) > time.time():
        return token
    if not token.get('refresh_token'):
        return None
    payload = _post_form(TOKEN_URL, {
        'refresh_token': token['refresh_token'],
        'client_id': os.environ.get('GOOGLE_CLIENT_ID', ''),
        'client_secret': os.environ.get('GOOGLE_CLIENT_SECRET', ''),
        'grant_type': 'refresh_token',
    })
    token['access_token'] = payload.get('access_token')
    token['expires_at'] = time.time() + int(payload.get('expires_in', 3600)) - 60
    save_token(token, tenant_id=tenant_id)
    return token


def send_gmail(to_email, subject, body, *, from_name='Flow CRM', tenant_id=None):
    """Manda un correo real via Gmail API usando el token guardado PARA
    ESA CUENTA -- tenant_id explicito porque esto se llama tanto desde
    rutas autenticadas (usa el resolver ambiente si no se pasa nada) como
    desde el hilo de recordatorios en segundo plano (sin sesion, tiene que
    pasarlo a mano desde el job/payment que esta procesando).

    Devuelve (ok, message_id_or_error).
    """
    token = load_token(tenant_id=tenant_id)
    if not token or not token.get('refresh_token'):
        return False, 'Gmail no esta conectado para esta cuenta. Ve a Settings > Email Settings.'

    try:
        token = _refresh_if_needed(token, tenant_id=tenant_id)
    except (HTTPError, URLError) as exc:
        return False, f'No se pudo refrescar el token de Gmail: {exc}'
    if not token or not token.get('access_token'):
        return False, 'Token de Gmail invalido, reconecta la cuenta.'

    msg = MIMEText(body or '', 'plain', 'utf-8')
    msg['To'] = to_email
    msg['Subject'] = subject
    msg['From'] = f'{from_name} <{token.get("email", "")}>'
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('utf-8')

    req = urlrequest.Request(
        SEND_URL,
        data=json.dumps({'raw': raw}).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {token["access_token"]}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urlrequest.urlopen(req, timeout=20) as response:
            data = json.loads(response.read().decode('utf-8') or '{}')
        return True, data.get('id', 'gmail-' + uuid.uuid4().hex[:10])
    except HTTPError as exc:
        return False, f'Gmail rechazo el envio: {exc.read().decode("utf-8")}'
    except URLError as exc:
        return False, f'Error de red enviando por Gmail: {exc}'
