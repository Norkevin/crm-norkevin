"""
google_login.py - "Iniciar sesion con Google" para proteger el CRM (portada
de login), separado de gmail_delivery.py que es para ENVIAR correos.

Este flujo solo pide identidad (email), no permiso para mandar correos --
por eso usa un scope minimo y un callback distinto
(/auth/google/login/callback en vez de /auth/google/callback).

Reutiliza las mismas GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET del .env, pero
Google exige que CADA redirect_uri este autorizado por separado en el
proyecto de Google Cloud.
"""
import json
import os
from urllib import request as urlrequest, parse as urlparse
from urllib.error import HTTPError, URLError

SCOPE = 'openid email profile'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
USERINFO_URL = 'https://www.googleapis.com/oauth2/v2/userinfo'


def is_configured() -> bool:
    return bool(os.environ.get('GOOGLE_CLIENT_ID') and os.environ.get('GOOGLE_CLIENT_SECRET'))


def allowed_emails():
    raw = os.environ.get('ALLOWED_LOGIN_EMAILS', '')
    return {e.strip().lower() for e in raw.split(',') if e.strip()}


def build_login_url(redirect_uri, state):
    params = {
        'client_id': os.environ.get('GOOGLE_CLIENT_ID', ''),
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': SCOPE,
        'prompt': 'select_account',
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


def exchange_code_for_email(code, redirect_uri):
    """Intercambia el codigo del callback por el email de la cuenta de
    Google que inicio sesion. Lanza HTTPError/URLError si algo falla."""
    payload = _post_form(TOKEN_URL, {
        'code': code,
        'client_id': os.environ.get('GOOGLE_CLIENT_ID', ''),
        'client_secret': os.environ.get('GOOGLE_CLIENT_SECRET', ''),
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    })
    access_token = payload.get('access_token', '')
    req = urlrequest.Request(
        USERINFO_URL,
        headers={'Authorization': f'Bearer {access_token}'},
    )
    with urlrequest.urlopen(req, timeout=15) as response:
        data = json.loads(response.read().decode('utf-8') or '{}')
    return data.get('email', ''), data.get('name', ''), data.get('picture', '')
