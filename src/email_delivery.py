"""
email_delivery.py - Entrega de emails para el CRM.

Por defecto usa una bandeja local de prueba en data/mail_outbox.json.
Para envio real, configurar EMAIL_DELIVERY_MODE=real y EMAIL_PROVIDER=smtp
o EMAIL_PROVIDER=resend con sus credenciales.
"""
import json
import os
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError


@dataclass
class DeliveryResult:
    ok: bool
    provider: str
    message_id: str = ''
    status: str = 'sent'
    error: str = ''
    mode: str = 'test'


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _data_dir() -> Path:
    return _project_root() / 'data'


def _setting(path, default=''):
    current = os.environ
    for key in path.split('.'):
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            current = None
            break
    return current if current not in (None, '') else default


def _company_settings():
    settings_path = _data_dir() / 'settings.json'
    if not settings_path.exists():
        return {}
    try:
        return json.loads(settings_path.read_text(encoding='utf-8')).get('company', {})
    except Exception:
        return {}


def _from_address():
    company = _company_settings()
    return (
        os.environ.get('MAIL_FROM')
        or os.environ.get('SMTP_FROM')
        or os.environ.get('RESEND_FROM')
        or company.get('email')
        or 'no-reply@astralweddings.local'
    )


def _append_local_outbox(message):
    outbox_path = _data_dir() / 'mail_outbox.json'
    outbox_path.parent.mkdir(parents=True, exist_ok=True)
    if outbox_path.exists():
        try:
            outbox = json.loads(outbox_path.read_text(encoding='utf-8'))
        except Exception:
            outbox = []
    else:
        outbox = []
    outbox.append(message)
    outbox_path.write_text(json.dumps(outbox, indent=2, ensure_ascii=False), encoding='utf-8')


def _send_local(to_email, subject, body, *, attachments=None, metadata=None):
    message_id = 'outbox-' + uuid.uuid4().hex[:10]
    _append_local_outbox({
        'id': message_id,
        'provider': 'local_outbox',
        'from': _from_address(),
        'to': to_email,
        'subject': subject,
        'body': body or '',
        'attachments': attachments or [],
        'metadata': metadata or {},
        'created_at': datetime.now().isoformat(),
    })
    return DeliveryResult(ok=True, provider='local_outbox', message_id=message_id, mode='test')


def _send_smtp(to_email, subject, body, *, attachments=None, metadata=None):
    host = os.environ.get('SMTP_HOST')
    user = os.environ.get('SMTP_USER')
    password = os.environ.get('SMTP_PASSWORD')
    if not host or not user or not password:
        return DeliveryResult(
            ok=False,
            provider='smtp',
            status='failed',
            error='Faltan SMTP_HOST, SMTP_USER o SMTP_PASSWORD',
            mode='real',
        )

    port = int(os.environ.get('SMTP_PORT', '587'))
    use_tls = os.environ.get('SMTP_TLS', 'true').lower() not in ('0', 'false', 'no')
    message = EmailMessage()
    message['From'] = _from_address()
    message['To'] = to_email
    message['Subject'] = subject
    message.set_content(body or '')

    try:
        if use_tls:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(user, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
                smtp.login(user, password)
                smtp.send_message(message)
        return DeliveryResult(ok=True, provider='smtp', message_id='smtp-' + uuid.uuid4().hex[:10], mode='real')
    except Exception as exc:
        return DeliveryResult(ok=False, provider='smtp', status='failed', error=str(exc), mode='real')


def _send_resend(to_email, subject, body, *, attachments=None, metadata=None):
    api_key = os.environ.get('RESEND_API_KEY')
    if not api_key:
        return DeliveryResult(
            ok=False,
            provider='resend',
            status='failed',
            error='Falta RESEND_API_KEY',
            mode='real',
        )

    payload = json.dumps({
        'from': _from_address(),
        'to': [to_email],
        'subject': subject,
        'text': body or '',
    }).encode('utf-8')
    req = urlrequest.Request(
        'https://api.resend.com/emails',
        data=payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urlrequest.urlopen(req, timeout=20) as response:
            data = json.loads(response.read().decode('utf-8') or '{}')
        return DeliveryResult(ok=True, provider='resend', message_id=data.get('id', ''), mode='real')
    except HTTPError as exc:
        return DeliveryResult(ok=False, provider='resend', status='failed', error=exc.read().decode('utf-8'), mode='real')
    except URLError as exc:
        return DeliveryResult(ok=False, provider='resend', status='failed', error=str(exc), mode='real')


def _send_gmail(to_email, subject, body, *, attachments=None, metadata=None):
    from . import gmail_delivery
    tenant_id = (metadata or {}).get('tenant_id')
    ok, result = gmail_delivery.send_gmail(to_email, subject, body, tenant_id=tenant_id)
    if ok:
        return DeliveryResult(ok=True, provider='gmail', message_id=result, mode='real')
    return DeliveryResult(ok=False, provider='gmail', status='failed', error=result, mode='real')


def send_email(to_email, subject, body='', *, attachments=None, metadata=None):
    if not to_email:
        return DeliveryResult(ok=False, provider='none', status='failed', error='Destinatario vacio')

    # Si la cuenta activa conecto su Gmail, se usa automaticamente sin
    # necesidad de tocar EMAIL_DELIVERY_MODE/EMAIL_PROVIDER. tenant_id
    # explicito en metadata (lo agrega mail_tracker.log_email) porque esto
    # puede correr sin sesion (hilo de recordatorios en segundo plano).
    from . import gmail_delivery
    tenant_id = (metadata or {}).get('tenant_id')
    if gmail_delivery.is_connected(tenant_id=tenant_id):
        return _send_gmail(to_email, subject, body, attachments=attachments, metadata=metadata)

    mode = os.environ.get('EMAIL_DELIVERY_MODE', 'test').lower()
    provider = os.environ.get('EMAIL_PROVIDER', 'local_outbox').lower()
    if mode != 'real':
        return _send_local(to_email, subject, body, attachments=attachments, metadata=metadata)
    if provider == 'smtp':
        return _send_smtp(to_email, subject, body, attachments=attachments, metadata=metadata)
    if provider == 'resend':
        return _send_resend(to_email, subject, body, attachments=attachments, metadata=metadata)
    return DeliveryResult(ok=False, provider=provider, status='failed', error=f'Proveedor no soportado: {provider}', mode='real')
