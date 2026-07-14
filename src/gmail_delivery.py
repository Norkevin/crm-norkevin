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


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _token_path() -> Path:
    return _project_root() / 'data' / 'google_token.json'


def is_configured():
    """True si Kevin ya puso las credenciales de la app OAuth en .env."""
    return bool(os.environ.get('GOOGLE_CLIENT_ID') and os.environ.get('GOOGLE_CLIENT_SECRET'))


def load_token():
    path = _token_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def save_token(data):
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def disconnect():
    path = _token_path()
    if path.exists():
        path.unlink()


def is_connected():
    tok = load_token()
    return bool(tok and tok.get('refresh_token'))


def connected_email():
    tok = load_token()
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
    """Intercambia el 'code' del callback por access_token + refresh_token."""
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


def _refresh_if_needed(token):
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
    save_token(token)
    return token


def send_gmail(to_email, subject, body, *, from_name='ASTRAL WEDDINGS'):
    """Manda un correo real via Gmail API usando el token guardado.

    Devuelve (ok, message_id_or_error).
    """
    token = load_token()
    if not token or not token.get('refresh_token'):
        return False, 'Gmail no esta conectado. Ve a Settings > Email Settings.'

    try:
        token = _refresh_if_needed(token)
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
