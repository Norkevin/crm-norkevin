"""Private collaborator portal with independent, scoped member authentication."""
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
import secrets

from flask import Blueprint, abort, g, jsonify, redirect, render_template, request, send_file, session, url_for

from src.teams import LOCAL_ZONE, TeamsError, now, text, teams_zone
from src.teams_features import VISIBLE_ASSIGNMENTS, advance_balance, cost_amount, document_visible, file_fields, MAX_FILE_BYTES


def calendar_text(assignments, jobs):
    def escape(value):
        return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,').replace('\r', '')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Flow CRM//Teams Local//ES', 'CALSCALE:GREGORIAN']
    for a in assignments:
        job = jobs[a['job_id']]
        lines.extend(['BEGIN:VEVENT', f"UID:{a['id']}@flow-teams.local", f"SEQUENCE:{a['terms_version']}",
                      'DTSTAMP:' + datetime.now(LOCAL_ZONE).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
                      'DTSTART:' + datetime.fromisoformat(a['start']).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
                      'DTEND:' + datetime.fromisoformat(a['end']).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
                      'SUMMARY:' + escape(f"{job['nombre']} · {a['role']}"), 'LOCATION:' + escape(job.get('location', '')),
                      'DESCRIPTION:Consulta las condiciones vigentes en tu portal privado de Flow Teams.',
                      'CLASS:PRIVATE', 'STATUS:' + ('CONFIRMED' if a['status'] in ('aceptada','realizada') else 'TENTATIVE'), 'END:VEVENT'])
    lines.append('END:VCALENDAR')
    # RFC 5545 lines fold at 75 octets without splitting a UTF-8 character.
    folded = []
    for line in lines:
        part, size = '', 0
        for character in line:
            length = len(character.encode())
            if size + length > 75:
                folded.append(part)
                part, size = ' ', 1
            part += character
            size += length
        folded.append(part)
    return '\r\n'.join(folded) + '\r\n'


def register_portal(app, owner_blueprint, store, crm_store, owner_job_reader):
    portal = Blueprint('teams_portal', __name__, url_prefix='/teams-portal')
    original_resolver = crm_store.tenant_resolver
    crm_store.tenant_resolver = lambda: getattr(g, 'teams_portal_tenant', None) or original_resolver()

    # Only the enabled member portal bypasses CRM-owner login; it authenticates independently below.
    hooks = app.before_request_funcs.get(None, [])
    for index, hook in enumerate(hooks):
        if hook.__name__ == '_require_login':
            def local_portal_login_gate(original=hook):
                if (app.config.get('FLOW_TEAMS_LOCAL') or app.config.get('FLOW_TEAMS_ENABLED')) and (request.path == '/teams-portal' or request.path.startswith('/teams-portal/')):
                    return None
                return original()
            hooks[index] = local_portal_login_gate

    @portal.before_request
    def member_only():
        local = app.config.get('FLOW_TEAMS_LOCAL')
        if not (local or app.config.get('FLOW_TEAMS_ENABLED')) or (local and request.remote_addr not in ('127.0.0.1', '::1')):
            abort(404)
        limit = MAX_FILE_BYTES + 1024*1024 if request.endpoint == 'teams_portal.expense_upload' else 65536
        if request.content_length and request.content_length > limit:
            abort(413)
        if request.endpoint in ('teams_portal.login','teams_portal.calendar_document'):
            return
        tenant, member_id = session.get('teams_member_tenant'), session.get('teams_member_id')
        if not tenant or not member_id:
            if request.method == 'POST' or request.path.endswith('/summary'):
                return jsonify(ok=False, error='Inicia sesión en el portal del equipo.'), 401
            return redirect(url_for('teams_portal.login'))
        with store.transaction() as db:
            member = store.get(db, tenant, 'member', member_id)
        if not member['active'] or member.get('access_version', 1) != session.get('teams_member_access_version'):
            abort(403)
        if session.get('teams_member_preview'):
            owner = crm_store.get('tenants', tenant)
            if (not session.get('logged_in') or session.get('tenant_id') != tenant or not owner
                    or session.get('user_email') != owner.get('login_email')):
                abort(403)
        g.teams_portal_tenant, g.teams_member = tenant, member
        if request.method == 'POST':
            if session.get('teams_member_preview') and request.endpoint != 'teams_portal.logout':
                raise TeamsError('La vista previa es de consulta. El miembro responde desde su acceso individual.', 403)
            token = session.get('teams_portal_csrf')
            if not token or not secrets.compare_digest(request.headers.get('X-Teams-CSRF', ''), token):
                abort(403)

    @portal.after_request
    def private(response):
        response.headers.update({'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
                                 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY'})
        return response

    @portal.errorhandler(TeamsError)
    def invalid(error):
        return jsonify(ok=False, error=error.message), error.status

    def bind_member(tenant, member, preview=False):
        session.update(teams_member_tenant=tenant, teams_member_id=member['id'],
                       teams_member_access_version=member.get('access_version', 1), teams_member_preview=preview,
                       teams_portal_csrf=secrets.token_urlsafe(32))

    @owner_blueprint.route('/teams/preview/<member_id>')
    def preview(member_id):
        with store.transaction() as db:
            member = store.get(db, session['tenant_id'], 'member', member_id)
            if not member['active']:
                abort(403)
            store.create(db, session['tenant_id'], 'audit', action='portal_preview', actor=session['user_email'],
                         created_at=now(), before=None, after=dict(id=member_id))
        bind_member(session['tenant_id'], member, True)
        return redirect(url_for('teams_portal.page'))

    @owner_blueprint.route('/api/teams/access', methods=['POST'])
    def issue_access():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise TeamsError('Selecciona un miembro.')
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with store.transaction() as db:
            member = store.get(db, session['tenant_id'], 'member', text(data, 'member_id'))
            if not member['active']:
                raise TeamsError('El miembro está inactivo.')
            store.save(db, session['tenant_id'], 'access', dict(id=token_hash, member_id=member['id'],
                       access_version=member.get('access_version', 1), expires=(datetime.now(LOCAL_ZONE)+timedelta(hours=24)).isoformat(), used=False))
            store.create(db, session['tenant_id'], 'audit', action='access_issue', actor=session['user_email'],
                         created_at=now(), before=None, after=dict(id=member['id']))
        # The raw code is returned once, never written to the audit, URL, outbox or idempotency table.
        return jsonify(ok=True, code=token, login_url=url_for('teams_portal.login', _external=True) + '#access=' + token,
                       message='Enlace privado de un solo uso. Caduca en 24 horas. No se ha enviado.')

    @portal.route('/login', methods=['GET', 'POST'])
    def login():
        session.setdefault('teams_login_csrf', secrets.token_urlsafe(32))
        error = None
        if request.method == 'POST':
            if not secrets.compare_digest(request.form.get('csrf', ''), session['teams_login_csrf']):
                abort(403)
            token = request.form.get('code', '')
            if len(token) > 200:
                abort(400)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            with store.transaction() as db:
                attempt_id = hashlib.sha256((request.remote_addr or 'unknown').encode()).hexdigest()
                attempts = db.execute("SELECT payload FROM entities WHERE kind='login_attempt' AND id=?", (attempt_id,)).fetchone()
                count = json.loads(attempts['payload']) if attempts else dict(id=attempt_id, count=0, until=now())
                if count['until'] > now() and count['count'] >= 5:
                    return render_template('teams_portal.html', login=True, csrf=session['teams_login_csrf'], error='Demasiados intentos. Espera un minuto.'), 429
                row = db.execute("SELECT tenant,payload FROM entities WHERE kind='access' AND id=?", (token_hash,)).fetchone()
                access = json.loads(row['payload']) if row else None
                member = store.get(db, row['tenant'], 'member', access['member_id']) if access else None
                if (access and not access['used'] and access['expires'] > now() and member['active']
                        and access['access_version'] == member.get('access_version', 1)):
                    access['used'] = True
                    store.save(db, row['tenant'], 'access', access)
                    bind_member(row['tenant'], member)
                    count['count'] = 0
                    store.save(db, '_local', 'login_attempt', count)
                    return redirect(url_for('teams_portal.page'))
                count.update(count=count['count']+1 if count['until'] > now() else 1,
                             until=(datetime.now(LOCAL_ZONE)+timedelta(minutes=1)).isoformat())
                store.save(db, '_local', 'login_attempt', count)
                error = 'Código inválido, usado o caducado. Solicita un código nuevo al responsable.'
        return render_template('teams_portal.html', login=True, csrf=session['teams_login_csrf'], error=error)

    def read_job(identifier):
        job = crm_store.get('jobs', identifier)
        if not job:
            raise TeamsError('Cobertura no disponible.', 404)
        return job

    def state():
        tenant, member = g.teams_portal_tenant, g.teams_member
        with store.transaction() as db:
            assignments = [a for a in store.records(db, tenant, 'assignment') if a['member_id'] == member['id'] and a['status'] in VISIBLE_ASSIGNMENTS]
            job_ids = {a['job_id'] for a in assignments}
            jobs = {}
            for identifier in job_ids:
                job = read_job(identifier)
                jobs[identifier] = {k: job.get(k) for k in ('id', 'nombre', 'boda_date', 'end_date', 'location', 'status')}
            published = {a['id'] for a in store.records(db, tenant, 'assignment') if a['member_id'] == member['id']
                         and (a.get('published_at') or a['status'] in VISIBLE_ASSIGNMENTS)}
            cost_rows = [c for c in store.records(db, tenant, 'cost') if c['beneficiary'] == member['id']
                         and c['status'] in ('aprobado','incurrido')
                         and (not c.get('assignment_id') or c['assignment_id'] in published)]
            costs = []
            for c in cost_rows:
                paid = store.paid(db, tenant, c['id'])
                costs.append(dict(id=c['id'], job_id=c['job_id'], category=c['category'], description=c['description'],
                                  amount=cost_amount(c), paid=paid, pending=cost_amount(c)-paid if c['status'] in ('aprobado','incurrido') else 0,
                                  status=c['status'], honorarium=bool(c.get('assignment_id')), assignment_id=c.get('assignment_id'), schedule_id=c.get('schedule_id')))
            coverage = []
            for a in assignments:
                c = next((c for c in cost_rows if c.get('assignment_id') == a['id']), None)
                own = {k: a.get(k) for k in ('id','job_id','role','slot','start','end','buffer','status','instructions','version','terms_version','accepted_terms')}
                own.update(job=jobs[a['job_id']], amount=cost_amount(c) if c else 0,
                           changed=a['job_day'] != jobs[a['job_id']]['boda_date'] or jobs[a['job_id']]['status'] in ('Cancelado','Archivado'))
                coverage.append(own)
            current_job_ids = {a['job_id'] for a in assignments if a['job_day'] == jobs[a['job_id']]['boda_date']
                               and jobs[a['job_id']]['status'] not in ('Cancelado', 'Archivado')}
            receipts = [r for r in store.records(db, tenant, 'receipt') if r['member_id'] == member['id']]
            documents = []
            for d in store.records(db, tenant, 'document'):
                if d['job_id'] in current_job_ids and document_visible(store, db, tenant, d, member['id']):
                    own = {k: d.get(k) for k in ('id','job_id','title','kind','content','file_name','version','published_at')}
                    own['read'] = any(r['document_id'] == d['id'] and r['document_version'] == d['version'] for r in receipts)
                    documents.append(own)
            advances = [dict(id=a['id'], job_id=a['job_id'], amount=a['amount'], remaining=advance_balance(store, db, tenant, a))
                        for a in store.records(db, tenant, 'advance') if a['member_id'] == member['id']]
            tasks = [{k: t.get(k) for k in ('id','job_id','purpose','due','status','version')}
                     for t in store.records(db, tenant, 'task') if t['member_id'] == member['id']
                     and any(a['id'] == t['assignment_id'] and a['terms_version'] == t['terms_version'] for a in assignments)]
            requests = [{k: r.get(k) for k in ('id','job_id','description','amount','status','file_name','created_at')}
                        for r in store.records(db, tenant, 'expense_request') if r['member_id'] == member['id']]
            availability = [{k:r.get(k) for k in ('id','start','end','note','version','status')}
                            for r in store.records(db, tenant, 'availability') if r['member_id'] == member['id'] and r.get('status') != 'retirada']
            owned_cost_ids = {c['id'] for c in costs}
            cost_map = {c['id']: c for c in costs}
            history = []
            payments = [p for p in store.records(db, tenant, 'payment') if p['beneficiary'] == member['id']]
            reversed_ids = {p.get('reversal_of') for p in payments if p['sign'] == -1}
            for payment in payments:
                allocations = [a for a in payment['allocations'] if a['cost_id'] in owned_cost_ids]
                if not allocations and not payment.get('advance_id'):
                    continue
                own = dict(id=payment['id'], date=payment.get('effective_date'),
                    amount=(sum(a['amount'] for a in allocations) if allocations else payment['amount']) * payment['sign'],
                    reference=payment.get('reference', ''), method=payment.get('method', ''), file_name=payment.get('file_name', ''),
                    kind='Fondo para gastos' if payment.get('advance_id') else 'Pago' if payment['sign']==1 else 'Corrección de pago',
                    status='Anulado' if payment['id'] in reversed_ids else 'Corrección' if payment['sign']==-1 else 'Recibido',
                    fee_amount=sum(a['amount'] for a in allocations if cost_map[a['cost_id']]['honorarium']) * payment['sign'],
                    reimbursement_amount=sum(a['amount'] for a in allocations if not cost_map[a['cost_id']]['honorarium']) * payment['sign'],
                    fee_job_ids=list(dict.fromkeys(a['job_id'] for a in allocations if cost_map[a['cost_id']]['honorarium'])),
                    cost_ids=[a['cost_id'] for a in allocations], sign=payment['sign'], valid=payment['sign']==1 and payment['id'] not in reversed_ids,
                    job_ids=list(dict.fromkeys([a['job_id'] for a in allocations] + ([payment['job_id']] if payment.get('advance_id') else []))))
                history.append(own)
            schedules = store.records(db, tenant, 'schedule')
            assignment_map = {a['id']: a for a in store.records(db, tenant, 'assignment') if a['member_id'] == member['id']}
            for c in costs:
                a = assignment_map.get(c['assignment_id'])
                c['confirmed'] = not c['honorarium'] or bool(a and a['status'] in ('aceptada','realizada'))
                c['movements'] = [dict(p, amount=sum(part['amount'] for payment in payments if payment['id']==p['id'] for part in payment['allocations'] if part['cost_id']==c['id']) * p['sign']) for p in history if c['id'] in p['cost_ids']]
                c['payment_count'] = sum(p['valid'] for p in c['movements'])
                c['schedule_count'] = c['schedule_completed'] = 0
                c['next_payment'] = None
                plan = next((r for r in schedules if r['id'] == c['schedule_id']), None)
                if plan:
                    remaining = c['paid']
                    c['schedule_count'] = len(plan['plan'])
                    for entry in plan['plan']:
                        applied = min(remaining, entry['amount'])
                        remaining -= applied
                        c['schedule_completed'] += applied == entry['amount']
                        if applied < entry['amount'] and c['next_payment'] is None:
                            c['next_payment'] = dict(date=entry['due_date'], amount=entry['amount']-applied)
                c['overpaid'] = c['paid'] > c['amount']
            for a in coverage:
                c = next((c for c in costs if c['assignment_id']==a['id']), None)
                a['financial'] = c
                a['amount'] = c['amount'] if c else None
            fee_costs = [c for c in costs if c['honorarium']]
            fee_payments = [p for p in history if p['fee_amount']]
            fee_summary = dict(amount=sum(c['amount'] for c in fee_costs if c['confirmed']),
                paid=sum(c['paid'] for c in fee_costs), pending=sum(c['pending'] for c in fee_costs if c['confirmed']),
                count=sum(p['valid'] for p in fee_payments),
                unconfirmed=sum(not c['confirmed'] for c in fee_costs),
                advance_paid=sum(c['paid'] for c in fee_costs if not c['confirmed']),
                overpaid=any(c['overpaid'] for c in fee_costs))
            fee_summary['progress'] = round(fee_summary['paid'] * 100 / fee_summary['amount'], 1) if fee_summary['amount'] else None
            history_years = sorted({p['date'][:4] for p in history if p.get('date') and len(p['date'])>=10}, reverse=True)
        financial_ids = {c['job_id'] for c in costs} | {a['job_id'] for a in advances}
        financial_jobs = []
        for identifier in financial_ids:
            job = read_job(identifier)
            own = [c for c in costs if c['job_id']==identifier]
            financial_jobs.append(dict(id=identifier,name=job.get('nombre'),day=job.get('boda_date'),
                honorarium=sum(c['amount'] for c in own if c['honorarium']),reimbursements=sum(c['amount'] for c in own if not c['honorarium']),
                paid=sum(c['paid'] for c in own),pending=sum(c['pending'] for c in own),
                fees=[c for c in own if c['honorarium']],
                fee_amount=sum(c['amount'] for c in own if c['honorarium'] and c['confirmed']),
                fee_paid=sum(c['paid'] for c in own if c['honorarium']),
                fee_pending=sum(c['pending'] for c in own if c['honorarium'] and c['confirmed']),
                fee_count=sum(p['valid'] for p in fee_payments if identifier in p['fee_job_ids'])))
        year = request.args.get('year', str(datetime.now(teams_zone(crm_store, tenant)).year))
        if year == 'all':
            year = ''
        if year and (len(year)!=4 or not year.isdigit()):
            abort(400)
        history = sorted((p for p in history if not year or (p.get('date') or '').startswith(year+'-')),key=lambda p:p.get('date') or '',reverse=True)
        totals = dict(amount=sum(c['amount'] for c in costs),paid=sum(c['paid'] for c in costs),pending=sum(c['pending'] for c in costs),
                      honorarium=sum(c['amount'] for c in costs if c['honorarium']),
                      reimbursements=sum(c['amount'] for c in costs if not c['honorarium']),
                      funds_remaining=sum(a['remaining'] for a in advances))
        return dict(member={k: member.get(k) for k in ('id','name','email','phone')}, assignments=coverage, documents=documents,
                    costs=costs, advances=advances, tasks=tasks, expense_requests=requests, availability=availability,
                    financial_jobs=sorted(financial_jobs,key=lambda j:j['day'] or ''),history=history,year=year,totals=totals,
                    fee_summary=fee_summary, history_years=history_years, current_year=str(datetime.now(teams_zone(crm_store, tenant)).year),
                    history_total=sum(p['amount'] for p in history), history_count=sum(p['valid'] for p in history),
                    history_fee_total=sum(p['fee_amount'] for p in history), history_fee_count=sum(p['valid'] for p in history if p['fee_amount']),
                    history_reimbursements=sum(p['reimbursement_amount'] for p in history),
                    upcoming_assignments=sorted((a for a in coverage if a['end'][:10] >= datetime.now(teams_zone(crm_store, tenant)).date().isoformat()), key=lambda a:a['start']),
                    past_assignments=sorted((a for a in coverage if a['end'][:10] < datetime.now(teams_zone(crm_store, tenant)).date().isoformat()), key=lambda a:a['start'], reverse=True),
                    brand_name=crm_store.get('tenants',tenant).get('name','Tu equipo'),
                    preview=bool(session.get('teams_member_preview')))

    @portal.route('')
    @portal.route('/')
    def page():
        return render_template('teams_portal.html', login=False, csrf=session['teams_portal_csrf'], **state())

    @portal.route('/summary')
    def summary():
        return jsonify(state())

    @portal.route('/command', methods=['POST'])
    def command():
        member = g.teams_member
        actor = ('Vista de prueba del propietario · ' if session.get('teams_member_preview') else 'Miembro · ') + member['id']
        result=store.command(g.teams_portal_tenant, actor, request.get_json(silent=True), read_job, member_id=member['id'])
        data=request.get_json(silent=True) or {}
        if data.get('action')=='response' and not app.config.get('FLOW_TEAMS_LOCAL'):
            from src.google_calendar import connected_email
            from src.teams import teams_zone
            if connected_email(g.teams_portal_tenant):
                import os
                try:
                    app.extensions['teams_calendar'].enqueue(g.teams_portal_tenant,read_job(result['record']['job_id']),
                        os.environ.get('APP_BASE_URL','https://flowingcrm.com').rstrip('/'),
                        teams_zone(crm_store,g.teams_portal_tenant),app.secret_key)
                except Exception:app.logger.warning('Calendar update not queued; member response remains saved')
        return jsonify(result)

    @portal.route('/logout', methods=['POST'])
    def logout():
        for key in list(session):
            if key.startswith('teams_member') or key == 'teams_portal_csrf':
                session.pop(key)
        return jsonify(ok=True, record={}, warnings=[])

    def download(document):
        if not document.get('file_data'):
            raise TeamsError('Este documento contiene texto; no tiene archivo adjunto.', 404)
        response = send_file(BytesIO(base64.b64decode(document['file_data'])), mimetype=document['file_type'],
                             as_attachment=True, download_name=document['file_name'], max_age=0)
        response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
        return response

    @portal.route('/expenses/upload', methods=['POST'])
    def expense_upload():
        data = request.form.to_dict()
        data['action'] = 'expense_request'
        attachment = request.files.get('file')
        if attachment and attachment.filename:
            raw = attachment.read(MAX_FILE_BYTES+1)
            data.update(file_data=base64.b64encode(raw).decode(),file_name=attachment.filename)
            file_fields(data)
        actor = ('Vista de prueba del propietario · ' if session.get('teams_member_preview') else 'Miembro · ') + g.teams_member['id']
        return jsonify(store.command(g.teams_portal_tenant,actor,data,read_job,member_id=g.teams_member['id']))

    @portal.route('/expenses/<identifier>/download')
    def member_expense_download(identifier):
        with store.transaction() as db:
            receipt = store.get(db,g.teams_portal_tenant,'expense_request',identifier)
            if receipt['member_id'] != g.teams_member['id']:
                abort(404)
        return download(receipt)

    @owner_blueprint.route('/teams/expenses/<identifier>/download')
    def owner_expense_download(identifier):
        with store.transaction() as db:
            receipt = store.get(db,session['tenant_id'],'expense_request',identifier)
            owner_job_reader(receipt['job_id'])
        return download(receipt)

    @owner_blueprint.route('/api/teams/payments/upload', methods=['POST'])
    def payment_upload():
        data = request.form.to_dict()
        data['action'] = 'payment'
        try:
            data['allocations'] = json.loads(data.get('allocations', '[]'))
        except (ValueError, TypeError):
            raise TeamsError('Selecciona los importes a abonar.')
        attachment = request.files.get('file')
        if attachment and attachment.filename:
            raw = attachment.read(MAX_FILE_BYTES+1)
            data.update(file_data=base64.b64encode(raw).decode(), file_name=attachment.filename)
        return jsonify(store.command(session['tenant_id'], session['user_email'], data, owner_job_reader))

    @owner_blueprint.route('/teams/payments/<identifier>/download')
    def owner_payment_download(identifier):
        with store.transaction() as db:
            payment = store.get(db, session['tenant_id'], 'payment', identifier)
            for allocation in payment['allocations']:
                owner_job_reader(allocation['job_id'])
        return download(payment)

    @portal.route('/payments/<identifier>/download')
    def member_payment_download(identifier):
        visible_ids = {c['id'] for c in state()['costs']}
        with store.transaction() as db:
            payment = store.get(db, g.teams_portal_tenant, 'payment', identifier)
            if payment['beneficiary'] != g.teams_member['id']:
                abort(404)
            # Apply the same publication rules as the personal payment history.
            if not any(a['cost_id'] in visible_ids for a in payment['allocations']):
                abort(404)
        return download(payment)

    @portal.route('/calendar-document/<token>')
    def calendar_document(token):
        from datetime import timezone
        from itsdangerous import URLSafeSerializer, BadSignature
        try:
            payload=URLSafeSerializer(app.secret_key,salt='teams-calendar-document').loads(token)
            if not isinstance(payload,dict) or payload['expires']<datetime.now(timezone.utc).timestamp():abort(404)
            tenant=payload['tenant']
            with store.transaction() as db:
                member=store.get(db,tenant,'member',payload['member'])
                doc=store.get(db,tenant,'document',payload['document'])
                if (not member['active'] or member.get('access_version',1)!=payload['access']
                    or doc['version']!=payload['version'] or not document_visible(store,db,tenant,doc,member['id'])):abort(404)
                g.teams_portal_tenant=tenant
                job=read_job(doc['job_id'])
                if job.get('status') in ('Cancelado','Archivado') or not any(a['member_id']==member['id'] and a['job_id']==job['id']
                    and a['status'] in VISIBLE_ASSIGNMENTS and a['job_day']==job.get('boda_date')
                    for a in store.records(db,tenant,'assignment')):abort(404)
        except (BadSignature,KeyError,TypeError,ValueError):abort(404)
        # This link authorizes only this published document; it never signs into the portal.
        if doc.get('file_data'):return download(doc)
        from flask import Response
        return Response(doc['title']+'\n\n'+doc['content'],mimetype='text/plain',headers={'Content-Security-Policy':"default-src 'none'; sandbox"})

    @portal.route('/documents/<identifier>/download')
    def member_download(identifier):
        with store.transaction() as db:
            doc = store.get(db, g.teams_portal_tenant, 'document', identifier)
            if not document_visible(store, db, g.teams_portal_tenant, doc, g.teams_member['id']):
                abort(404)
            job = read_job(doc['job_id'])
            current = [a for a in store.records(db, g.teams_portal_tenant, 'assignment') if a['job_id'] == doc['job_id']
                       and a['member_id'] == g.teams_member['id'] and a['status'] in VISIBLE_ASSIGNMENTS
                       and a['job_day'] == job.get('boda_date')]
            if job.get('status') in ('Cancelado', 'Archivado') or not current:
                abort(404)
        return download(doc)

    @owner_blueprint.route('/teams/documents/<identifier>/download')
    def owner_download(identifier):
        with store.transaction() as db:
            doc = store.get(db, session['tenant_id'], 'document', identifier)
            owner_job_reader(doc['job_id'])
        return download(doc)

    @owner_blueprint.route('/api/teams/documents/upload', methods=['POST'])
    def upload():
        data = request.form.to_dict()
        data.update(action='document', audience_ids=request.form.getlist('audience_ids'))
        if 'version' in data:
            try:
                data['version'] = int(data['version'])
            except ValueError:
                raise TeamsError('Versión inválida.')
        attachment = request.files.get('file')
        if attachment and attachment.filename:
            raw = attachment.read(MAX_FILE_BYTES+1)
            data.update(file_data=base64.b64encode(raw).decode(), file_name=attachment.filename)
            file_fields(data)
        return jsonify(store.command(session['tenant_id'], session['user_email'], data, owner_job_reader))

    @portal.route('/calendar.ics')
    def member_calendar():
        with store.transaction() as db:
            assignments = [a for a in store.records(db, g.teams_portal_tenant, 'assignment') if a['member_id'] == g.teams_member['id']
                           and a['status'] in VISIBLE_ASSIGNMENTS]
        jobs = {a['job_id']: read_job(a['job_id']) for a in assignments}
        assignments = [a for a in assignments if jobs[a['job_id']].get('status') not in ('Cancelado', 'Archivado')
                       and a['job_day'] == jobs[a['job_id']].get('boda_date')]
        return send_file(BytesIO(calendar_text(assignments, jobs).encode()), mimetype='text/calendar',
                         as_attachment=True, download_name='mis-coberturas.ics', max_age=0)

    app.register_blueprint(portal)
