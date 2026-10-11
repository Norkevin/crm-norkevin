"""Native iPhone API. No CRM writes, public-user access, or automatic sends.

SQLite is a separate journal: observing committed CRM records and inserting the
inbox/outbox happen in one transaction. It does not make JSON writes transactional.
"""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, g, jsonify, render_template, request, session

PUBLIC_API = {'/api/mobile/pair/start', '/api/mobile/pair/poll'}
EVENT_TYPES = ('lead', 'payment', 'teams', 'wedding', 'mail')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class MobileJournal:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS pairings (
                    secret_hash TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL,
                    expires REAL NOT NULL, polled REAL NOT NULL DEFAULT 0,
                    tenant_id TEXT, recipient TEXT, consumed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                    recipient TEXT NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
                    device TEXT, apns_environment TEXT, push_enabled INTEGER NOT NULL DEFAULT 0,
                    hide_details INTEGER NOT NULL DEFAULT 1, last_test REAL NOT NULL DEFAULT 0,
                    device_revision INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS preferences (
                    tenant_id TEXT NOT NULL, recipient TEXT NOT NULL, kind TEXT NOT NULL,
                    enabled INTEGER NOT NULL, PRIMARY KEY(tenant_id, recipient, kind));
                CREATE TABLE IF NOT EXISTS observed (
                    tenant_id TEXT NOT NULL, event_key TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, event_key));
                CREATE TABLE IF NOT EXISTS baselines (tenant_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS rate_limits (
                    key TEXT PRIMARY KEY, count INTEGER NOT NULL, resets REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS notifications (
                    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, recipient TEXT NOT NULL,
                    event_key TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
                    target_table TEXT, target_id TEXT, created REAL NOT NULL, read_at REAL,
                    UNIQUE(tenant_id, recipient, event_key));
                CREATE INDEX IF NOT EXISTS inbox_scope ON notifications(tenant_id, recipient, created);
                CREATE TABLE IF NOT EXISTS deliveries (
                    notification_id TEXT NOT NULL, session_hash TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0, error TEXT,
                    PRIMARY KEY(notification_id, session_hash));
            ''')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(sessions)')}
            if 'device_revision' not in columns:
                db.execute('ALTER TABLE sessions ADD COLUMN device_revision INTEGER NOT NULL DEFAULT 0')
        Path(path).chmod(0o600)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def observe(self, tenant_id, recipient, events):
        """First scan is a baseline. Repeated source events cannot duplicate an alert."""
        with self.transaction() as db:
            initialized = db.execute('SELECT 1 FROM baselines WHERE tenant_id=?', (tenant_id,)).fetchone()
            for event in events:
                added = db.execute('INSERT OR IGNORE INTO observed VALUES (?,?)',
                                   (tenant_id, event['key'])).rowcount
                if not initialized or not added:
                    continue
                pref = db.execute('SELECT enabled FROM preferences WHERE tenant_id=? AND recipient=? AND kind=?',
                                  (tenant_id, recipient, event['kind'])).fetchone()
                # Preferences control pushes; the persistent inbox remains complete.
                nid = digest(tenant_id + ':' + recipient + ':' + event['key'])
                db.execute('INSERT OR IGNORE INTO notifications VALUES (?,?,?,?,?,?,?,?,?,NULL)',
                           (nid, tenant_id, recipient, event['key'], event['kind'], event['title'],
                            event['table'], event['id'], time.time()))
                if not pref or pref['enabled']:
                    db.execute('''INSERT OR IGNORE INTO deliveries(notification_id,session_hash)
                        SELECT ?,token_hash FROM sessions WHERE tenant_id=? AND recipient=?
                        AND revoked=0 AND expires>? AND push_enabled=1 AND device IS NOT NULL''',
                               (nid, tenant_id, recipient, time.time()))
            db.execute('INSERT OR IGNORE INTO baselines VALUES (?)', (tenant_id,))


def journal(app, store):
    if 'mobile' not in app.extensions:
        app.extensions['mobile'] = MobileJournal(Path(store.data_dir) / 'mobile.sqlite3')
    return app.extensions['mobile']


def enabled(app):
    return app.config.get('FLOW_MOBILE_ENABLED', os.environ.get('FLOW_MOBILE_ENABLED') == '1')


def rate_limit(db, key, maximum):
    now = time.time()
    db.execute('DELETE FROM rate_limits WHERE resets<?', (now,))
    db.execute('INSERT OR IGNORE INTO rate_limits VALUES (?,0,?)', (key, now + 60))
    db.execute('UPDATE rate_limits SET count=count+1 WHERE key=?', (key,))
    return db.execute('SELECT count FROM rate_limits WHERE key=?', (key,)).fetchone()[0] > maximum


def valid_owner(store, tenant_id, email):
    tenant = store.get('tenants', tenant_id)
    return bool(tenant and tenant.get('active') is not False and email
                and email == (tenant.get('login_email') or '').strip().lower())


def authorize_request(app, store):
    if not enabled(app):
        abort(404)
    if request.path in PUBLIC_API:
        return None
    header = request.headers.get('Authorization', '')
    token = header[7:] if header.startswith('Bearer ') else ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        return jsonify(ok=False, error='Conecta tu cuenta de Flowing.'), 401
    with journal(app, store).transaction() as db:
        row = db.execute('SELECT * FROM sessions WHERE token_hash=? AND revoked=0 AND expires>?',
                         (digest(token), time.time())).fetchone()
    if not row or not valid_owner(store, row['tenant_id'], row['recipient']):
        return jsonify(ok=False, error='Sesión vencida. Conecta tu cuenta de nuevo.'), 401
    g.mobile_identity = dict(row)
    # Never let a browser cookie change the tenant represented by this bearer.
    return None


def source_events(store, tenant_id, teams=None, today=None):
    """Reconcile persisted records only. No mail opens are called client replies."""
    def rows(table):
        return store.list_privileged(table, tenant_id=tenant_id,
                                     reason='Native inbox reconciliation of committed records')

    today = today or datetime.now(ZoneInfo('America/Guatemala')).date()
    events = []
    leads, jobs = rows('leads'), rows('jobs')
    job_ids = {j['id'] for j in jobs}
    lead_ids = {lead['id'] for lead in leads}

    def add(key, kind, title, table, identifier):
        events.append(dict(key=key, kind=kind, title=title[:240], table=table, id=identifier))

    for lead in leads:
        if lead.get('studio_ninja_source_id'):
            continue  # Historical imports must not look like a new client enquiry.
        add('lead:' + lead['id'], 'lead', 'Nuevo lead: ' + (lead.get('nombre') or 'Sin nombre'), 'leads', lead['id'])
    for payment in rows('payments'):
        if (payment.get('status') == 'Pagado' and payment.get('job_id') in job_ids
                and payment.get('tipo') != 'team_payment' and not payment.get('studio_ninja_url')):
            add('payment:' + payment['id'], 'payment', 'Pago registrado en Flowing', 'jobs', payment['job_id'])
    for job in jobs:
        if job.get('status') != 'Confirmado':
            continue
        try:
            remaining = (date.fromisoformat(str(job.get('boda_date'))[:10]) - today).days
        except ValueError:
            continue
        if remaining in (1, 7):
            add(f"wedding:{job['id']}:{job['boda_date']}:{remaining}", 'wedding',
                ('Mañana: revisa la cobertura de ' if remaining == 1 else 'En 7 días: reconfirma ') + (job.get('nombre') or 'la boda'),
                'jobs', job['id'])
    for mail in rows('mail_log'):
        table, target = ('jobs', mail.get('job_id')) if mail.get('job_id') in job_ids else ('leads', mail.get('lead_id'))
        if table == 'leads' and target not in lead_ids:
            continue
        if mail.get('status') in ('failed', 'blocked') or mail.get('bounced_at'):
            add('mail-alert:' + mail['id'], 'mail', 'Revisa un correo que no se entregó', table, target)
    if teams:
        with teams.transaction() as db:
            for item in teams.records(db, tenant_id, 'audit'):
                after = item.get('after') or {}
                if (item.get('action') in ('response', 'calendar_response')
                        and after.get('status') in ('aceptada', 'rechazada') and after.get('job_id') in job_ids):
                    add('teams:' + item['id'], 'teams', 'Teams: cobertura ' + after['status'], 'jobs', after['job_id'])
    return events


def register_mobile(app, store, open_leads, canonical_jobs, job_is_active, billable_payments, financial_summary, amount, reporting_day):
    bp = Blueprint('mobile', __name__)

    def data():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            abort(400)
        return value

    def identity():
        return g.mobile_identity

    def scoped(db):
        me = identity()
        return me['tenant_id'], me['recipient']

    @bp.after_request
    def private_response(response):
        response.headers['Cache-Control'] = 'no-store'
        # HTML form POSTs need their origin; inherit the web policy there.
        if request.path.startswith('/api/mobile/'):
            response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @bp.post('/api/mobile/pair/start')
    def pair_start():
        # Global SQLite cap bounds unauthenticated attempts across processes/IPs.
        with journal(app, store).transaction() as db:
            origin_key = digest(str(app.secret_key) + ':' + (request.remote_addr or 'unknown'))
            if rate_limit(db, 'pair-start:' + origin_key, 5):
                return jsonify(error='Espera un minuto antes de generar otro código.'), 429
            db.execute('DELETE FROM pairings WHERE expires<?', (time.time(),))
            if db.execute('SELECT COUNT(*) FROM pairings').fetchone()[0] >= 500:
                return jsonify(error='Espera unos minutos antes de conectar.'), 429
            secret = secrets.token_urlsafe(32)
            code = secrets.token_hex(4).upper()
            db.execute('INSERT INTO pairings(secret_hash,code,expires) VALUES (?,?,?)',
                       (digest(secret), code, time.time() + 600))
        return jsonify(device_secret=secret, code=code, verification_path='/mobile/connect', expires_in=600)

    @bp.route('/mobile/connect', methods=['GET', 'POST'])
    def connect():
        if not enabled(app):
            abort(404)
        tenant, recipient = session.get('tenant_id'), (session.get('user_email') or '').strip().lower()
        if not valid_owner(store, tenant, recipient):
            abort(403)
        if request.method == 'GET':
            session['mobile_csrf'] = secrets.token_urlsafe(32)
            return render_template('mobile_connect.html', code='', connected=False)
        csrf = request.form.get('csrf', '')
        if not csrf or not secrets.compare_digest(csrf, session.get('mobile_csrf', '')):
            abort(403)
        code = request.form.get('code', '').strip().upper().replace('-', '')
        with journal(app, store).transaction() as db:
            if rate_limit(db, 'pair-approve:' + digest(tenant + ':' + recipient), 10):
                return render_template('mobile_connect.html', code='', connected=False,
                                       error='Espera un minuto antes de intentar otro código.'), 429
            changed = db.execute('''UPDATE pairings SET tenant_id=?,recipient=? WHERE code=?
                AND expires>? AND tenant_id IS NULL AND consumed=0''', (tenant, recipient, code, time.time())).rowcount
        if not changed:
            return render_template('mobile_connect.html', code=code, connected=False,
                                   error='Código vencido o no disponible. Genera otro desde tu iPhone.'), 400
        session.pop('mobile_csrf', None)
        return render_template('mobile_connect.html', code=code, connected=True)

    @bp.post('/api/mobile/pair/poll')
    def pair_poll():
        secret = data().get('device_secret', '')
        if not isinstance(secret, str) or len(secret) > 100:
            abort(400)
        with journal(app, store).transaction() as db:
            pair = db.execute('SELECT * FROM pairings WHERE secret_hash=? AND consumed=0 AND expires>?',
                              (digest(secret), time.time())).fetchone()
            if not pair:
                return jsonify(error='Código vencido. Vuelve a conectar.'), 401
            if time.time() - pair['polled'] < 4:
                return jsonify(error='Espera antes de consultar de nuevo.'), 429
            db.execute('UPDATE pairings SET polled=? WHERE secret_hash=?', (time.time(), digest(secret)))
            if not pair['tenant_id']:
                return jsonify(status='pending'), 202
            if not valid_owner(store, pair['tenant_id'], pair['recipient']):
                abort(403)
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions(token_hash,tenant_id,recipient,expires) VALUES (?,?,?,?)',
                       (digest(token), pair['tenant_id'], pair['recipient'], time.time() + 30 * 86400))
            db.execute('UPDATE pairings SET consumed=1 WHERE secret_hash=?', (digest(secret),))
        return jsonify(status='connected', token=token, expires_in=30 * 86400)

    @bp.get('/api/mobile/overview')
    def overview():
        me = identity()
        tenant = store.get('tenants', me['tenant_id'])
        leads = open_leads(me['tenant_id'])
        jobs = [j for j in canonical_jobs() if job_is_active(j)]
        today = datetime.now(ZoneInfo('America/Guatemala')).date()
        payments = billable_payments(me['tenant_id'])
        money = financial_summary(payments, today, today.year)
        monthly = [financial_summary([p for p in payments if (day := reporting_day(p))
                    and day.year == today.year and day.month == month], today, today.year)['paid']
                   for month in range(1, 13)]
        upcoming = sorted([job for job in jobs if str(job.get('boda_date') or '')[:10] >= today.isoformat()],
                          key=lambda job: (str(job.get('boda_date') or ''), job['id']))[:4]
        return jsonify(tenant_name=tenant.get('name') or 'Flowing', lead_count=len(leads), job_count=len(jobs),
                       paid=money['paid'], due=money['due'], year=today.year, monthly_paid=monthly,
                       upcoming=[project_record(job, 'jobs') for job in upcoming], user_email=me['recipient'])

    def project_record(record, kind):
        name = record.get('nombre') or record.get('title') or ' '.join(filter(None, (record.get('first_name'), record.get('last_name'))))
        return dict(id=record['id'], kind=kind, name=name or 'Sin nombre',
                    status=record.get('status') or '',
                    date=str(record.get('boda_date') or record.get('fecha_tentativa') or record.get('fecha_evento') or record.get('date') or ''),
                    email=record.get('email') or '', phone=record.get('phone') or record.get('telefono') or '',
                    location=record.get('lugar') or record.get('location') or record.get('locacion') or '')

    @bp.get('/api/mobile/records/<kind>')
    def records(kind):
        if kind not in ('leads', 'jobs', 'clients', 'calendar'):
            abort(404)
        if kind == 'leads':
            rows = open_leads(identity()['tenant_id'])
        elif kind == 'jobs':
            rows = [j for j in canonical_jobs() if job_is_active(j)]
        else:
            rows = store.list(kind)
        rows = [r for r in rows if not r.get('released_at')]
        rows.sort(key=lambda r: (project_record(r, kind)['date'] or '9999', r['id']))
        try:
            offset = max(0, int(request.args.get('offset', 0)))
        except ValueError:
            abort(400)
        return jsonify(records=[project_record(r, kind) for r in rows[offset:offset + 50]],
                       next_offset=offset + 50 if len(rows) > offset + 50 else None)

    @bp.get('/api/mobile/records/<kind>/<identifier>')
    def record(kind, identifier):
        if kind not in ('leads', 'jobs', 'clients', 'calendar'):
            abort(404)
        row = store.get(kind, identifier)
        if not row:
            abort(404)
        return jsonify(project_record(row, kind))

    @bp.get('/api/mobile/notifications')
    def inbox():
        try:
            before = int(request.args.get('before', 9223372036854775807))
        except ValueError:
            abort(400)
        unread = request.args.get('unread') == '1'
        with journal(app, store).transaction() as db:
            tenant, recipient = scoped(db)
            rows = db.execute('''SELECT rowid AS cursor,* FROM notifications WHERE tenant_id=? AND recipient=? AND rowid<?
                AND (?=0 OR read_at IS NULL) ORDER BY rowid DESC LIMIT 51''',
                              (tenant, recipient, before, unread)).fetchall()
            count = db.execute('SELECT COUNT(*) FROM notifications WHERE tenant_id=? AND recipient=? AND read_at IS NULL',
                               (tenant, recipient)).fetchone()[0]
        notices = [dict(id=r['id'], kind=r['kind'], title=r['title'], created=r['created'],
                        read=r['read_at'] is not None, target_kind=r['target_table'], target_id=r['target_id']) for r in rows[:50]]
        return jsonify(notifications=notices, count=count, next_before=rows[49]['cursor'] if len(rows) > 50 else None)

    @bp.get('/api/mobile/notifications/<nid>/target')
    def notification_target(nid):
        with journal(app, store).transaction() as db:
            tenant, recipient = scoped(db)
            row = db.execute('SELECT * FROM notifications WHERE id=? AND tenant_id=? AND recipient=?',
                             (nid, tenant, recipient)).fetchone()
        if not row:
            abort(404)
        if not row['target_table']:
            return jsonify(record=None)
        target = store.get(row['target_table'], row['target_id'])
        if not target:
            abort(404)
        return jsonify(record=project_record(target, row['target_table']))

    @bp.get('/api/mobile/payments')
    def payments():
        today = datetime.now(ZoneInfo('America/Guatemala')).date()
        rows = billable_payments(identity()['tenant_id'])
        customers = {c['id']: c for c in store.list('clients')}
        projected = []
        for row in rows:
            client = customers.get(row.get('client_id'), {})
            projected.append(dict(id=row['id'], name=' '.join(filter(None, (client.get('first_name'), client.get('last_name')))) or row.get('concepto') or 'Pago',
                                  status=row.get('status') or '', amount=amount(row.get('amount')),
                                  date=str(row.get('paid_date') or row.get('fecha_pago') or row.get('due_date') or '')))
        projected.sort(key=lambda r: r['date'], reverse=True)
        try:
            offset = max(0, int(request.args.get('offset', 0)))
        except ValueError:
            abort(400)
        return jsonify(payments=projected[offset:offset + 50], next_offset=offset + 50 if len(projected) > offset + 50 else None,
                       summary=financial_summary(rows, today, today.year), year=today.year)

    @bp.get('/api/mobile/teams')
    def teams():
        local = app.config.get('FLOW_TEAMS_LOCAL')
        available = bool((app.config.get('FLOW_TEAMS_ENABLED') or local) and
                         (not local or request.remote_addr in ('127.0.0.1', '::1')))
        if not available or 'teams_snapshot' not in app.extensions:
            return jsonify(enabled=False, jobs=[], members=[])
        report = app.extensions['teams_snapshot'](tenant=identity()['tenant_id'])
        jobs = []
        for job in report['jobs']:
            if job['teams_state'] != 'included':
                continue
            assignments = []
            for item in job['assignments']:
                cost = item.get('cost') or {}
                assignments.append(dict(id=item['id'], member_id=item['member_id'], name=item['member_name'],
                    role=item['role'], status=item['status'], start=item['start'], end=item['end'],
                    schedule_pending=bool(item.get('schedule_pending')), changed=bool(item['changed']),
                    conflicts=len(item.get('conflicts', [])) + len(item.get('other_brand_conflicts', [])),
                    amount=cost.get('total'), paid=cost.get('paid'), pending=cost.get('pending')))
            jobs.append(dict(id=job['id'], name=job.get('nombre') or 'Evento sin nombre',
                date=job.get('boda_date') or '', location=job.get('location') or '', phase=job['teams_phase'],
                cost=job['cost_total'], pending=job['team_pending'], incomplete=bool(job['incomplete']),
                closed=bool(job['closed']), assignments=assignments))
        jobs.sort(key=lambda j: (j['date'] or '9999', j['name'], j['id']))
        members = [dict(id=m['id'], name=m['name'], role=m['role'], active=bool(m['active']),
                        paid=m['paid'], pending=m['pending'], rate_missing=bool(m['rate_missing']))
                   for m in report['members']]
        return jsonify(enabled=True, jobs=jobs, members=members)

    @bp.post('/api/mobile/notifications/read')
    def read():
        ids = data().get('ids')
        if not isinstance(ids, list) or len(ids) > 100 or not all(isinstance(i, str) for i in ids):
            abort(400)
        with journal(app, store).transaction() as db:
            tenant, recipient = scoped(db)
            for nid in ids:
                if not db.execute('SELECT 1 FROM notifications WHERE id=? AND tenant_id=? AND recipient=?', (nid, tenant, recipient)).fetchone():
                    abort(404)
                db.execute('UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE id=?', (time.time(), nid))
        return jsonify(ok=True)

    @bp.route('/api/mobile/preferences', methods=['GET', 'POST'])
    def preferences():
        with journal(app, store).transaction() as db:
            tenant, recipient = scoped(db)
            if request.method == 'POST':
                changes = data()
                if any(k not in EVENT_TYPES or type(v) is not bool for k, v in changes.items()):
                    abort(400)
                for kind, value in changes.items():
                    db.execute('INSERT OR REPLACE INTO preferences VALUES (?,?,?,?)', (tenant, recipient, kind, int(value)))
            saved = {r['kind']: bool(r['enabled']) for r in db.execute('SELECT * FROM preferences WHERE tenant_id=? AND recipient=?', (tenant, recipient))}
        return jsonify(preferences={kind: saved.get(kind, True) for kind in EVENT_TYPES})

    @bp.post('/api/mobile/device')
    def device():
        from src.mobile_push import encrypt_device
        value = data()
        token, environment = value.get('token'), value.get('environment')
        if (not isinstance(token, str) or not re.fullmatch('[0-9a-fA-F]{64}', token)
                or environment not in ('sandbox', 'production') or type(value.get('hide_details', True)) is not bool
                or type(value.get('revision')) is not int):
            abort(400)
        try:
            encrypted = encrypt_device(token.lower())
        except ValueError:
            return jsonify(error='Las notificaciones aún no están configuradas en el servidor.'), 503
        with journal(app, store).transaction() as db:
            me = db.execute('SELECT * FROM sessions WHERE token_hash=?', (identity()['token_hash'],)).fetchone()
            if me['revoked'] or me['device_revision'] != value['revision']:
                return jsonify(error='La configuración del dispositivo cambió. Actualiza y vuelve a intentarlo.'), 409
            # One APNs token belongs to exactly one active session, including shared phones.
            from src.mobile_push import decrypt_device
            for old in db.execute('SELECT token_hash,device FROM sessions WHERE device IS NOT NULL AND revoked=0').fetchall():
                if decrypt_device(old['device']) == token.lower():
                    if old['token_hash'] != identity()['token_hash']:
                        db.execute('UPDATE sessions SET push_enabled=0,device=NULL,device_revision=device_revision+1 WHERE token_hash=?', (old['token_hash'],))
            db.execute('UPDATE sessions SET device=?,apns_environment=?,push_enabled=1,hide_details=?,device_revision=device_revision+1 WHERE token_hash=?',
                       (encrypted, environment, int(value.get('hide_details', True)), identity()['token_hash']))
        return jsonify(ok=True, revision=me['device_revision'] + 1)

    @bp.delete('/api/mobile/device')
    def disconnect_device():
        with journal(app, store).transaction() as db:
            db.execute('UPDATE sessions SET device=NULL,push_enabled=0,device_revision=device_revision+1 WHERE token_hash=?', (identity()['token_hash'],))
            revision = db.execute('SELECT device_revision FROM sessions WHERE token_hash=?', (identity()['token_hash'],)).fetchone()[0]
        return jsonify(ok=True, revision=revision)

    @bp.get('/api/mobile/device')
    def device_status():
        me = identity()
        return jsonify(push_enabled=bool(me['push_enabled'] and me['device']), hide_details=bool(me['hide_details']), revision=me['device_revision'])

    @bp.post('/api/mobile/device/privacy')
    def device_privacy():
        value = data().get('hide_details')
        if type(value) is not bool:
            abort(400)
        with journal(app, store).transaction() as db:
            db.execute('UPDATE sessions SET hide_details=?,device_revision=device_revision+1 WHERE token_hash=?', (int(value), identity()['token_hash']))
            revision = db.execute('SELECT device_revision FROM sessions WHERE token_hash=?', (identity()['token_hash'],)).fetchone()[0]
        return jsonify(ok=True, revision=revision)

    @bp.post('/api/mobile/push/test')
    def test_push():
        if os.environ.get('FLOW_MOBILE_PUSH_ENABLED') != '1':
            return jsonify(error='Los envíos push están desactivados.'), 503
        with journal(app, store).transaction() as db:
            me = db.execute('SELECT * FROM sessions WHERE token_hash=?', (identity()['token_hash'],)).fetchone()
            if not me['device'] or not me['push_enabled']:
                return jsonify(error='Activa las notificaciones de este iPhone.'), 409
            if time.time() - me['last_test'] < 60:
                return jsonify(error='Espera un minuto antes de otra prueba.'), 429
            nid = secrets.token_hex(16)
            db.execute('UPDATE sessions SET last_test=? WHERE token_hash=?', (time.time(), me['token_hash']))
            db.execute('INSERT INTO notifications VALUES (?,?,?,?,?,?,?,?,?,NULL)',
                       (nid, me['tenant_id'], me['recipient'], 'test:' + nid, 'test', 'Tu prueba de Flowing', None, None, time.time()))
            db.execute('INSERT INTO deliveries(notification_id,session_hash) VALUES (?,?)', (nid, me['token_hash']))
        return jsonify(ok=True, status='queued')

    @bp.post('/api/mobile/logout')
    def logout():
        with journal(app, store).transaction() as db:
            db.execute('UPDATE sessions SET revoked=1,device=NULL,push_enabled=0,device_revision=device_revision+1 WHERE token_hash=?', (identity()['token_hash'],))
        return jsonify(ok=True)

    app.register_blueprint(bp)
