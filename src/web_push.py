"""Owner-only Web Push. Browser credentials and deliveries stay on the CRM disk."""
import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask import Blueprint, abort, g, jsonify, request, session

PROVIDERS = {'web.push.apple.com', 'fcm.googleapis.com', 'updates.push.services.mozilla.com'}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def enabled(app):
    return app.config.get('FLOW_WEB_PUSH_ENABLED', os.environ.get('FLOW_WEB_PUSH_ENABLED') == '1')


def valid_owner(store, tenant, recipient):
    owner = store.get('tenants', tenant)
    return bool(owner and owner.get('active') is not False and recipient
                and recipient == (owner.get('login_email') or '').strip().lower())


def endpoint(value, *, resolve=True):
    if not isinstance(value, str) or not 1 <= len(value) <= 4096 or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise ValueError('La suscripción del navegador no es válida.')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.hostname not in PROVIDERS or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.fragment or not parsed.path or '\\' in value):
        raise ValueError('Este proveedor de notificaciones no está admitido.')
    if resolve:
        try:
            addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global
                                    or ipaddress.ip_address(row[4][0]).is_multicast for row in addresses):
                raise ValueError('El proveedor de notificaciones no tiene una dirección pública válida.')
        except OSError:
            raise ValueError('No se pudo comprobar el proveedor de notificaciones. Reintenta.') from None
    return value


def subscription(value):
    if not isinstance(value, dict) or not isinstance(value.get('keys'), dict):
        raise ValueError('La suscripción del navegador no es válida.')
    keys = {}
    for name, size in (('p256dh', 65), ('auth', 16)):
        text = value['keys'].get(name)
        if not isinstance(text, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', text):
            raise ValueError('Las claves del navegador no son válidas.')
        raw = base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))
        if len(raw) != size:
            raise ValueError('Las claves del navegador no son válidas.')
        if name == 'p256dh':
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
        keys[name] = text
    return dict(endpoint=endpoint(value.get('endpoint')), keys=keys)


def private_material(directory):
    """The lock prevents two web/worker processes from replacing each other's keys."""
    import fcntl
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    descriptor = os.open(directory / '.keys.lock', os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, 'rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for name in ('subscription.key', 'vapid.pem'):
            path = directory / name
            if not path.exists():
                data = Fernet.generate_key() if name == 'subscription.key' else ec.generate_private_key(ec.SECP256R1()).private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, 'wb') as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
            path.chmod(0o600)
        cipher = Fernet((directory / 'subscription.key').read_bytes())
        key = serialization.load_pem_private_key((directory / 'vapid.pem').read_bytes(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError('La clave de notificaciones no es válida.')
        public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return cipher, base64.urlsafe_b64encode(public).rstrip(b'=').decode()


class WebPushJournal:
    def __init__(self, data_dir):
        self.directory = Path(data_dir) / 'webpush'
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.path = self.directory / 'webpush.sqlite3'
        with self.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, recipient TEXT NOT NULL,
                    info TEXT NOT NULL, hide_details INTEGER NOT NULL, created REAL NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1, last_test REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS observed (
                    tenant TEXT NOT NULL, event_key TEXT NOT NULL, PRIMARY KEY(tenant,event_key));
                CREATE TABLE IF NOT EXISTS baselines (tenant TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS mode (id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS worker_health (id INTEGER PRIMARY KEY CHECK(id=1), last_scan REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL, tenant TEXT NOT NULL,
                    recipient TEXT NOT NULL, event_key TEXT NOT NULL, payload TEXT NOT NULL,
                    created REAL NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS rate_limits (key TEXT PRIMARY KEY, count INTEGER NOT NULL, resets REAL NOT NULL);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def material(self):
        return private_material(self.directory)

    def record_scan(self):
        with self.transaction() as db:
            db.execute('INSERT OR REPLACE INTO worker_health VALUES (1,?)', (time.time(),))

    def worker_ready(self):
        with self.transaction() as db:
            row = db.execute('SELECT last_scan FROM worker_health WHERE id=1').fetchone()
        return bool(row and 0 <= time.time() - row['last_scan'] <= 120)

    def pause(self):
        with self.transaction() as db:
            db.execute('INSERT OR REPLACE INTO mode VALUES (1,0)')
            db.execute("UPDATE deliveries SET status='cancelled' WHERE status IN ('queued','retry','sending')")

    def observe(self, sources, *, sending):
        now = time.time()
        with self.transaction() as db:
            previous = db.execute('SELECT enabled FROM mode WHERE id=1').fetchone()
            resumed = sending and previous and bool(previous['enabled'])
            if not sending:
                db.execute("UPDATE deliveries SET status='cancelled' WHERE status IN ('queued','retry','sending')")
            for tenant, recipient, events in sources:
                baseline = db.execute('SELECT 1 FROM baselines WHERE tenant=?', (tenant,)).fetchone()
                browsers = db.execute('SELECT * FROM subscriptions WHERE tenant=? AND recipient=? AND active=1', (tenant, recipient)).fetchall()
                for notice in events:
                    key = event_key(notice)
                    added = db.execute('INSERT OR IGNORE INTO observed VALUES (?,?)', (tenant, key)).rowcount
                    if not baseline or not resumed or not added or notice['timestamp'] <= 0:
                        continue
                    for browser in browsers:
                        if notice['timestamp'] < browser['created']:
                            continue
                        payload = notification_payload(tenant, notice, bool(browser['hide_details']))
                        identifier = digest(browser['id'] + ':' + key)
                        db.execute('''INSERT OR IGNORE INTO deliveries
                            (id,subscription_id,tenant,recipient,event_key,payload,created) VALUES (?,?,?,?,?,?,?)''',
                            (identifier, browser['id'], tenant, recipient, key, json.dumps(payload), now))
                db.execute('INSERT OR IGNORE INTO baselines VALUES (?)', (tenant,))
            db.execute('INSERT OR REPLACE INTO mode VALUES (1,?)', (int(sending),))
            if sending:
                db.execute('INSERT OR REPLACE INTO worker_health VALUES (1,?)', (now,))


def journal(app, store):
    if 'web_push' not in app.extensions:
        app.extensions['web_push'] = WebPushJournal(store.data_dir)
    return app.extensions['web_push']


def event_key(notice):
    return digest(str(notice['id']) + ':' + str(notice['timestamp']))


def safe_target(value):
    if value == '/notifications':
        return value
    if isinstance(value, str) and re.fullmatch(r'/(leads|jobs|teams/jobs)/[A-Za-z0-9_:-]+', value):
        return value
    return '/notifications'


def notification_payload(tenant, notice, hidden=True):
    identifier = digest(tenant + ':' + event_key(notice))
    return dict(title='Flowing', body='Tienes una nueva actividad en Flowing.' if hidden else str(notice['title'])[:240],
                hide_details=hidden, url='/notifications' if hidden else safe_target(notice.get('url')),
                notification_id=identifier, tag='flowing-' + identifier[:32])


def send_subscription(info, payload, directory):
    from pywebpush import webpush
    from requests import Session

    class SafeSession(Session):
        def request(self, method, url, **kwargs):
            endpoint(url)
            if method.upper() != 'POST':
                raise ValueError('Unexpected push method')
            kwargs['allow_redirects'] = False
            kwargs['timeout'] = 15
            return super().request(method, url, **kwargs)

    endpoint(info['endpoint'])
    with SafeSession() as transport:
        transport.trust_env = False
        response = webpush(info, json.dumps(payload, ensure_ascii=False),
            vapid_private_key=str(directory / 'vapid.pem'), vapid_claims={'sub': 'https://flowingcrm.com'},
            ttl=3600, timeout=15, requests_session=transport)
        return response.status_code


def drain(app, store, sources, *, sender=send_subscription, limit=30):
    if not enabled(app):
        return 0
    storage = journal(app, store)
    cipher, _ = storage.material()
    current = {tenant: {event_key(n) for n in events} for tenant, _, events in sources}
    accepted = 0
    for _ in range(limit):
        with storage.transaction() as db:
            row = db.execute('''SELECT d.*,s.info,s.hide_details,s.active,s.created AS subscribed,
                s.tenant AS owner_tenant,s.recipient AS owner_recipient FROM deliveries d
                JOIN subscriptions s ON s.id=d.subscription_id
                WHERE d.status IN ('queued','retry','sending') AND d.next_attempt<=?
                ORDER BY d.created LIMIT 1''', (time.time(),)).fetchone()
            if not row:
                break
            notice = dict(row)
            eligible = (row['active'] and row['tenant'] == row['owner_tenant'] and row['recipient'] == row['owner_recipient']
                and valid_owner(store, row['tenant'], row['recipient']) and row['created'] >= row['subscribed']
                and time.time() - row['created'] < 86400
                and (row['event_key'].startswith('test:') or row['event_key'] in current.get(row['tenant'], set())))
            if not eligible or row['attempts'] >= 5:
                db.execute('UPDATE deliveries SET status=? WHERE id=?', ('cancelled' if not eligible else 'failed', row['id']))
                continue
            db.execute("UPDATE deliveries SET status='sending',attempts=attempts+1,next_attempt=? WHERE id=?",
                       (time.time() + 120, row['id']))
        try:
            info = json.loads(cipher.decrypt(notice['info'].encode()))
            payload = json.loads(notice['payload'])
            if notice['hide_details']:
                payload.update(body='Tienes una nueva actividad en Flowing.', hide_details=True, url='/notifications')
            status = sender(info, payload, storage.directory)
        except Exception as exc:
            # Never log provider bodies, endpoints, encryption secrets or subscription keys.
            response = getattr(exc, 'response', None)
            status = getattr(response, 'status_code', 0) if response is not None else getattr(exc, 'status_code', 0) or 0
        result = 'accepted' if 200 <= status < 300 else 'retry' if status in (0, 429) or status >= 500 else 'failed'
        with storage.transaction() as db:
            db.execute('UPDATE deliveries SET status=?,next_attempt=? WHERE id=?',
                (result, time.time() + min(3600, 30 * 2 ** row['attempts']), notice['id']))
            if status in (404, 410):
                db.execute('UPDATE subscriptions SET active=0 WHERE id=?', (notice['subscription_id'],))
        accepted += result == 'accepted'
    return accepted


def revoke_browser(app, store):
    identifier = session.get('web_push_subscription_id')
    path = Path(store.data_dir) / 'webpush' / 'webpush.sqlite3'
    if identifier and ('web_push' in app.extensions or path.exists()):
        with journal(app, store).transaction() as db:
            tenant, recipient = session.get('tenant_id'), (session.get('user_email') or '').strip().lower()
            db.execute('UPDATE subscriptions SET active=0 WHERE id=? AND tenant=? AND recipient=?', (identifier, tenant, recipient))
            db.execute("UPDATE deliveries SET status='cancelled' WHERE subscription_id=? AND tenant=? AND recipient=? AND status IN ('queued','retry','sending')",
                       (identifier, tenant, recipient))


def register_web_push(app, store, recent_notifications):
    app.extensions['web_push_source'] = recent_notifications
    bp = Blueprint('web_push', __name__)

    def error(message, code):
        return jsonify(ok=False, error=message), code

    @bp.before_request
    def owner_only():
        tenant, recipient = session.get('tenant_id'), (session.get('user_email') or '').strip().lower()
        if not session.get('logged_in') or not valid_owner(store, tenant, recipient):
            return error('Inicia sesión con la cuenta propietaria de esta empresa.', 403)
        g.web_push_owner = (tenant, recipient)
        if request.method != 'GET':
            scheme = 'https' if os.environ.get('RENDER') in ('1', 'true') else request.scheme
            if request.headers.get('Origin') != scheme + '://' + request.host:
                return error('Abre Flowing y vuelve a intentarlo desde esta página.', 403)
            token = request.headers.get('X-CSRF-Token', '')
            if not token or not secrets.compare_digest(token, session.get('web_push_csrf', '')):
                return error('Actualiza la página para autorizar esta solicitud.', 403)
            if request.content_length is None or request.content_length > 8192:
                return error('La solicitud de notificaciones no es válida.', 413)

    @bp.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    def data():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            abort(400)
        return value

    def limited(db, suffix):
        key = digest(':'.join(g.web_push_owner) + ':' + suffix)
        now = time.time()
        db.execute('DELETE FROM rate_limits WHERE resets<?', (now,))
        db.execute('INSERT OR IGNORE INTO rate_limits VALUES (?,0,?)', (key, now + 60))
        db.execute('UPDATE rate_limits SET count=count+1 WHERE key=?', (key,))
        return db.execute('SELECT count FROM rate_limits WHERE key=?', (key,)).fetchone()[0] > 20

    @bp.get('/api/web-push/status')
    def status():
        session.setdefault('web_push_csrf', secrets.token_urlsafe(32))
        configured = bool(enabled(app))
        public, ids = None, []
        ready = False
        if configured:
            try:
                _, public = journal(app, store).material()
            except Exception:
                return error('No se pudo preparar la configuración de avisos. Reintenta más tarde.', 503)
        if configured or 'web_push' in app.extensions or (Path(store.data_dir) / 'webpush' / 'webpush.sqlite3').exists():
            ready = journal(app, store).worker_ready()
            with journal(app, store).transaction() as db:
                ids = [row['id'] for row in db.execute('SELECT id FROM subscriptions WHERE tenant=? AND recipient=? AND active=1', g.web_push_owner)]
        return jsonify(available=configured and ready, enabled=configured, worker_ready=configured and ready,
                       vapid_public_key=public, csrf=session['web_push_csrf'],
                       has_subscription=bool(ids), subscription_ids=ids)

    @bp.post('/api/web-push/subscribe')
    def subscribe():
        if not enabled(app):
            return error('Los avisos del navegador aún no están habilitados.', 503)
        value = data()
        hidden = value.get('hide_details', True)
        if type(hidden) is not bool:
            return error('La opción de privacidad no es válida.', 400)
        try:
            info = subscription(value.get('subscription'))
        except (ValueError, TypeError):
            return error('La suscripción del navegador no es válida o su proveedor no está disponible.', 400)
        storage = journal(app, store)
        cipher, _ = storage.material()
        identifier = digest(info['endpoint'])
        tenant, recipient = g.web_push_owner
        with storage.transaction() as db:
            if limited(db, 'subscribe'):
                return error('Espera un minuto antes de volver a registrar avisos.', 429)
            old = db.execute('SELECT * FROM subscriptions WHERE id=?', (identifier,)).fetchone()
            if old and old['active'] and (old['tenant'], old['recipient']) != (tenant, recipient):
                return error('Este navegador está vinculado a otra cuenta. Desactiva sus avisos antes de cambiar de cuenta.', 409)
            if not old or not old['active']:
                count = db.execute('SELECT COUNT(*) FROM subscriptions WHERE tenant=? AND recipient=? AND active=1', (tenant, recipient)).fetchone()[0]
                if count >= 5:
                    return error('Ya tienes cinco navegadores con avisos. Desactiva uno antes de añadir otro.', 409)
            created = old['created'] if old and old['active'] else time.time()
            encrypted = cipher.encrypt(json.dumps(info).encode()).decode()
            db.execute('''INSERT INTO subscriptions(id,tenant,recipient,info,hide_details,created,active) VALUES (?,?,?,?,?,?,1)
                ON CONFLICT(id) DO UPDATE SET tenant=excluded.tenant,recipient=excluded.recipient,
                info=excluded.info,hide_details=excluded.hide_details,created=excluded.created,active=1''',
                (identifier, tenant, recipient, encrypted, int(hidden), created))
        session['web_push_subscription_id'] = identifier
        return jsonify(ok=True, id=identifier, hide_details=hidden, worker_ready=storage.worker_ready())

    @bp.post('/api/web-push/bind')
    def bind():
        try:
            identifier = digest(endpoint(data().get('endpoint'), resolve=False))
        except (ValueError, TypeError):
            return error('La suscripción del navegador no es válida.', 400)
        if 'web_push' not in app.extensions and not (Path(store.data_dir) / 'webpush' / 'webpush.sqlite3').exists():
            return error('Los avisos de este navegador ya no están activos.', 404)
        with journal(app, store).transaction() as db:
            if limited(db, 'bind'):
                return error('Espera un minuto antes de volver a comprobar avisos.', 429)
            row = db.execute('SELECT id FROM subscriptions WHERE id=? AND tenant=? AND recipient=? AND active=1',
                             (identifier, *g.web_push_owner)).fetchone()
            if not row:
                return error('Los avisos de este navegador ya no están activos en esta cuenta.', 404)
        session['web_push_subscription_id'] = identifier
        return jsonify(ok=True, id=identifier)

    @bp.post('/api/web-push/unsubscribe')
    def unsubscribe():
        try:
            identifier = digest(endpoint(data().get('endpoint'), resolve=False))
        except (ValueError, TypeError):
            return error('La suscripción del navegador no es válida.', 400)
        with journal(app, store).transaction() as db:
            tenant, recipient = g.web_push_owner
            db.execute('UPDATE subscriptions SET active=0 WHERE id=? AND tenant=? AND recipient=?', (identifier, tenant, recipient))
            db.execute("UPDATE deliveries SET status='cancelled' WHERE subscription_id=? AND tenant=? AND recipient=? AND status IN ('queued','retry','sending')", (identifier, tenant, recipient))
        if session.get('web_push_subscription_id') == identifier:
            session.pop('web_push_subscription_id', None)
        return jsonify(ok=True)

    @bp.post('/api/web-push/test')
    def test():
        if not enabled(app):
            return error('Los avisos del navegador están desactivados.', 503)
        if not journal(app, store).worker_ready():
            return error('El servicio de avisos está temporalmente sin conexión. Reintenta más tarde.', 503)
        try:
            identifier = digest(endpoint(data().get('endpoint'), resolve=False))
        except (ValueError, TypeError):
            return error('La suscripción del navegador no es válida.', 400)
        with journal(app, store).transaction() as db:
            tenant, recipient = g.web_push_owner
            browser = db.execute('SELECT * FROM subscriptions WHERE id=? AND tenant=? AND recipient=? AND active=1', (identifier, tenant, recipient)).fetchone()
            if not browser:
                return error('Activa los avisos de este navegador antes de enviar una prueba.', 404)
            now = time.time()
            if now - browser['last_test'] < 60 or limited(db, 'test'):
                return error('Espera un minuto antes de otra prueba.', 429)
            db.execute('UPDATE subscriptions SET last_test=? WHERE id=?', (now, identifier))
            key = 'test:' + secrets.token_hex(16)
            payload = notification_payload(tenant, dict(id=key, timestamp=now, title='Tu prueba de Flowing', url='/notifications'))
            db.execute('''INSERT INTO deliveries(id,subscription_id,tenant,recipient,event_key,payload,created)
                VALUES (?,?,?,?,?,?,?)''', (digest(identifier + key), identifier, tenant, recipient, key, json.dumps(payload), now))
        return jsonify(ok=True, status='queued')

    app.register_blueprint(bp)
