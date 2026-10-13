"""APNs adapter and bounded, leased delivery queue. Network sending is opt-in."""
import base64
import json
import os
import time
import uuid

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature


def cipher():
    key = os.environ.get('FLOW_MOBILE_ENCRYPTION_KEY', '')
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise ValueError('Mobile encryption key is not configured') from None


def encrypt_device(value):
    return cipher().encrypt(value.encode()).decode()


def decrypt_device(value):
    try:
        return cipher().decrypt(value.encode()).decode()
    except InvalidToken:
        raise ValueError('Mobile device credential is unavailable') from None


def apns_jwt():
    def encoded(value):
        return base64.urlsafe_b64encode(value).rstrip(b'=').decode()

    key = serialization.load_pem_private_key(
        os.environ['FLOW_APNS_PRIVATE_KEY'].replace('\\n', '\n').encode(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError('APNs requires a P-256 signing key')
    header = encoded(json.dumps(dict(alg='ES256', kid=os.environ['FLOW_APNS_KEY_ID'])).encode())
    claims = encoded(json.dumps(dict(iss=os.environ['FLOW_APNS_TEAM_ID'], iat=int(time.time()))).encode())
    message = header + '.' + claims
    r, s = decode_dss_signature(key.sign(message.encode(), ec.ECDSA(hashes.SHA256())))
    return message + '.' + encoded(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))


def send_apns(device, environment, notice, hide_details, badge):
    import httpx  # Optional mobile requirements; the CRM needs no new dependency.
    host = 'api.sandbox.push.apple.com' if environment == 'sandbox' else 'api.push.apple.com'
    payload = dict(aps=dict(alert=dict(title='Flowing', body='Tienes una nueva notificación.' if hide_details else notice['title']),
                            sound='default', badge=badge), notification_id=notice['id'])
    headers = {'authorization': 'bearer ' + apns_jwt(),
               'apns-topic': os.environ['FLOW_APNS_BUNDLE_ID'], 'apns-push-type': 'alert',
               'apns-priority': '10', 'apns-expiration': str(int(time.time()) + 3600),
               'apns-id': str(uuid.uuid5(uuid.NAMESPACE_URL, notice['id'])),
               'apns-collapse-id': notice['id'][:64]}
    with httpx.Client(http2=True, timeout=15) as client:
        response = client.post(f'https://{host}/3/device/{device}', json=payload, headers=headers)
    return response.status_code


def drain(journal, store, current_keys, sender=send_apns, limit=30):
    """Provider acceptance is not proof of display. Ambiguous timeouts may retry."""
    if os.environ.get('FLOW_MOBILE_PUSH_ENABLED') != '1':
        return 0
    from src.mobile import valid_owner
    if sender is send_apns and not all(os.environ.get(k) for k in
            ('FLOW_APNS_PRIVATE_KEY', 'FLOW_APNS_KEY_ID', 'FLOW_APNS_TEAM_ID', 'FLOW_APNS_BUNDLE_ID', 'FLOW_MOBILE_ENCRYPTION_KEY')):
        raise ValueError('APNs server configuration is incomplete')
    if sender is send_apns:
        import httpx
        cipher()
        apns_jwt()  # Fail before claiming deliveries when signing is misconfigured.
    delivered = 0
    for _ in range(limit):
        with journal.transaction() as db:
            row = db.execute('''SELECT d.*, n.tenant_id,n.recipient,n.event_key,n.kind,n.title,n.created,
                n.id,s.device,s.push_enabled,s.hide_details,s.apns_environment,s.expires,s.revoked
                FROM deliveries d JOIN notifications n ON n.id=d.notification_id
                JOIN sessions s ON s.token_hash=d.session_hash
                WHERE d.status IN ('queued','retry','sending') AND d.next_attempt<=?
                ORDER BY n.created LIMIT 1''', (time.time(),)).fetchone()
            if not row:
                break
            notice = dict(row)
            pref = db.execute('SELECT enabled FROM preferences WHERE tenant_id=? AND recipient=? AND kind=?',
                              (row['tenant_id'], row['recipient'], row['kind'])).fetchone()
            eligible = (not row['revoked'] and row['expires'] > time.time() and row['push_enabled'] and row['device']
                        and valid_owner(store, row['tenant_id'], row['recipient']) and (not pref or pref['enabled'])
                        and time.time() - row['created'] < 7 * 86400
                        and (row['kind'] == 'test' or row['event_key'] in current_keys.get(row['tenant_id'], set())))
            if not eligible or row['attempts'] >= 5:
                db.execute('UPDATE deliveries SET status=? WHERE notification_id=? AND session_hash=?',
                           ('cancelled' if not eligible else 'failed', row['notification_id'], row['session_hash']))
                continue
            db.execute('UPDATE deliveries SET status=\'sending\',attempts=attempts+1,next_attempt=? WHERE notification_id=? AND session_hash=?',
                       (time.time() + 120, row['notification_id'], row['session_hash']))
            badge = db.execute('SELECT COUNT(*) FROM notifications WHERE tenant_id=? AND recipient=? AND hidden=0 AND read_at IS NULL',
                               (row['tenant_id'], row['recipient'])).fetchone()[0]
        try:
            status = sender(decrypt_device(notice['device']), notice['apns_environment'], notice, bool(notice['hide_details']), badge)
            result = 'accepted' if status == 200 else ('retry' if status == 429 or status >= 500 else 'failed')
            error = None if status == 200 else str(status)
        except Exception:
            # No provider response, token, key or client data enters logs.
            status, result, error = 0, 'retry', 'transport_or_configuration'
        with journal.transaction() as db:
            db.execute('UPDATE deliveries SET status=?,error=?,next_attempt=? WHERE notification_id=? AND session_hash=?',
                       (result, error, time.time() + min(3600, 30 * 2 ** notice['attempts']), notice['notification_id'], notice['session_hash']))
            if status == 410:
                db.execute('UPDATE sessions SET device=NULL,push_enabled=0 WHERE token_hash=?', (notice['session_hash'],))
        delivered += result == 'accepted'
    return delivered
