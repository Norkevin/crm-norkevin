"""Internal Teams proposal. CRM jobs stay in JsonStore; money commits in SQLite."""
import hashlib
import csv
import io
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, abort, current_app, jsonify, render_template, request, session

LOCAL_ZONE = ZoneInfo('America/Guatemala')
DEFAULT_ROLES = 'Primera cámara de fotografía\nSegunda cámara de fotografía\nPrimer videógrafo\nSegundo videógrafo\nAsistente\nOperador de dron\nTransporte\nEditor\nCoordinador'


class TeamsError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def cents(value, *, imported=False):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number > Decimal('1000000000'):
            raise ValueError
        scaled = number * 100
        if not imported and scaled != scaled.to_integral_value():
            raise ValueError
        return int(scaled.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (ValueError, InvalidOperation, TypeError):
        raise TeamsError('Importe inválido. Usa un número positivo con hasta dos decimales.')


def money(value):
    return 'Q' + format(Decimal(value) / 100, ',.2f')


def text(data, key, *, required=True, maximum=500):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise TeamsError(f'Revisa el campo {key}.')
    return value.strip()


def day(value):
    try:
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError):
        raise TeamsError('La fecha debe ser válida.')


def now():
    return datetime.now(LOCAL_ZONE).isoformat()


class TeamsStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS entities (
                    kind TEXT NOT NULL, id TEXT NOT NULL, tenant TEXT NOT NULL,
                    version INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (kind, id));
                CREATE INDEX IF NOT EXISTS entities_scope ON entities(tenant, kind);
                CREATE TABLE IF NOT EXISTS commands (
                    tenant TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    result TEXT NOT NULL, PRIMARY KEY (tenant, key));
            ''')
        Path(path).chmod(0o600)

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

    def records(self, db, tenant, kind):
        return [json.loads(row['payload']) for row in db.execute(
            'SELECT payload FROM entities WHERE tenant=? AND kind=? ORDER BY rowid', (tenant, kind))]

    def get(self, db, tenant, kind, identifier):
        row = db.execute('SELECT payload FROM entities WHERE tenant=? AND kind=? AND id=?',
                         (tenant, kind, identifier)).fetchone()
        if not row:
            raise TeamsError('Recurso no disponible en esta marca.', 404)
        return json.loads(row['payload'])

    def save(self, db, tenant, kind, record):
        record['version'] = record.get('version', 0) + 1
        db.execute('''INSERT INTO entities VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(kind,id) DO UPDATE SET version=excluded.version,payload=excluded.payload
                    WHERE entities.tenant=excluded.tenant''',
                   (kind, record['id'], tenant, record['version'], json.dumps(record, ensure_ascii=False)))
        return record

    def create(self, db, tenant, kind, **fields):
        return self.save(db, tenant, kind, dict(id=str(uuid4()), **fields))

    def check_version(self, record, data):
        if type(data.get('version')) is not int or record['version'] != data['version']:
            raise TeamsError('Otro cambio actualizó este registro. Recarga antes de guardar.', 409)

    def paid(self, db, tenant, cost_id):
        direct = sum(a['amount'] * p['sign'] for p in self.records(db, tenant, 'payment')
                     for a in p['allocations'] if a['cost_id'] == cost_id)
        funded = sum(a['amount'] for s in self.records(db, tenant, 'settlement')
                     for a in s['allocations'] if a['cost_id'] == cost_id)
        return direct + funded

    def invalidate(self, db, tenant, job_id):
        operations = [r for r in self.records(db, tenant, 'operation') if r['job_id'] == job_id]
        if operations and operations[0].get('reviewed'):
            operations[0]['reviewed'] = False
            self.save(db, tenant, 'operation', operations[0])

    def conflicts(self, db, tenant, member_id, start, end, buffer, exclude=None):
        start = datetime.fromisoformat(start) - timedelta(minutes=buffer)
        end = datetime.fromisoformat(end) + timedelta(minutes=buffer)
        conflicts = []
        for a in self.records(db, tenant, 'assignment'):
            if a['id'] == exclude or a['member_id'] != member_id or a['status'] == 'cancelada':
                continue
            other_start = datetime.fromisoformat(a['start']) - timedelta(minutes=a['buffer'])
            other_end = datetime.fromisoformat(a['end']) + timedelta(minutes=a['buffer'])
            if start < other_end and other_start < end:
                conflicts.append(a)
        return conflicts

    def command(self, tenant, actor, data, job_reader, *, member_id=None):
        if not isinstance(data, dict):
            raise TeamsError('Se necesita un objeto JSON.')
        key = text(data, 'key', maximum=100)
        member_actions = ('response', 'document_read', 'task_complete', 'expense_request', 'availability', 'availability_remove')
        if member_id:
            if data.get('action') not in member_actions:
                raise TeamsError('Esta acción no pertenece al portal del miembro.', 403)
            key = f'member:{member_id}:{key}'
        elif data.get('action') in ('response', 'document_read', 'expense_request', 'availability', 'availability_remove'):
            raise TeamsError('Esta acción requiere la identidad individual del miembro.', 403)
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        with self.transaction() as db:
            previous = db.execute('SELECT * FROM commands WHERE tenant=? AND key=?', (tenant, key)).fetchone()
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise TeamsError('La clave de este comando ya se usó con otro contenido.', 409)
                return json.loads(previous['result'])
            action = text(data, 'action', maximum=40)
            original_reader = job_reader
            def guarded_job_reader(identifier):
                job = original_reader(identifier)
                protected = ('assignment', 'assignment_status', 'assignment_publish', 'assignment_edit', 'cost',
                             'cost_status', 'operation', 'advance', 'settlement', 'expense_review', 'cost_shared', 'schedule')
                if action in protected and any(o['job_id'] == identifier and o.get('closed') for o in self.records(db, tenant, 'operation')):
                    raise TeamsError('La operación está cerrada. Reábrela con un motivo antes de cambiar sus costos o coberturas.', 409)
                return job
            job_reader = guarded_job_reader
            before = None
            warnings = []
            if action == 'member':
                identifier = data.get('id')
                record = self.get(db, tenant, 'member', identifier) if identifier else dict(id=str(uuid4()))
                if identifier:
                    self.check_version(record, data)
                    before = dict(record)
                email = text(data, 'email', required=False, maximum=200)
                if email and ('@' not in email or ' ' in email):
                    raise TeamsError('Correo inválido.')
                if email and any(m['email'].lower() == email.lower() and m['id'] != record['id']
                       for m in self.records(db, tenant, 'member')):
                    raise TeamsError('Ya existe un miembro con ese correo en esta marca.', 409)
                if type(data.get('active', True)) is not bool:
                    raise TeamsError('Estado del miembro inválido.')
                if identifier and (record['email'].casefold() != email.casefold() or record['active'] != data.get('active', True)):
                    record['access_version'] = record.get('access_version', 1) + 1
                record.update(name=text(data, 'name', maximum=150), email=email,
                              phone=text(data, 'phone', required=False, maximum=80),
                              role=text(data, 'role', maximum=100), rate=cents(data.get('rate', '0')),
                              active=data.get('active', True))
                if 'skills' in data:
                    record['skills'] = list(dict.fromkeys(s.strip() for s in text(data,'skills',required=False,maximum=1000).split(',') if s.strip()))
                if 'instagram' in data:
                    record['instagram'] = text(data,'instagram',required=False,maximum=300)
                if 'rate' in data:
                    record['rate_missing'] = False
                self.save(db, tenant, 'member', record)
            elif action == 'assignment':
                job = job_reader(text(data, 'job_id', maximum=200))
                member = self.get(db, tenant, 'member', text(data, 'member_id'))
                if not member['active']:
                    raise TeamsError('Este miembro está inactivo.')
                try:
                    start = datetime.fromisoformat(data['start']).replace(tzinfo=LOCAL_ZONE)
                    end = datetime.fromisoformat(data['end']).replace(tzinfo=LOCAL_ZONE)
                    buffer = int(data.get('buffer', 0))
                    if start >= end or not 0 <= buffer <= 1440:
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    raise TeamsError('Revisa inicio, fin y margen de traslado. El fin debe ser posterior al inicio.')
                slot = text(data, 'slot', maximum=100)
                if any(a['job_id'] == job['id'] and a['slot'].casefold() == slot.casefold()
                       and a['status'] not in ('cancelada', 'rechazada') for a in self.records(db, tenant, 'assignment')):
                    raise TeamsError('Esta plaza ya tiene una asignación. Usa otra plaza o cancela la anterior.', 409)
                conflicts = self.conflicts(db, tenant, member['id'], start.isoformat(), end.isoformat(), buffer)
                if conflicts:
                    warnings.append('Borrador guardado con conflicto de horario. Revisa el calendario antes de confirmar con el equipo.')
                fee = cents(data.get('amount'))
                record = self.create(db, tenant, 'assignment', job_id=job['id'], member_id=member['id'],
                                     role=text(data, 'role', maximum=100), slot=slot, start=start.isoformat(),
                                     end=end.isoformat(), buffer=buffer, status='borrador', terms_version=1,
                                     job_day=job.get('boda_date'), instructions=text(data, 'instructions', required=False, maximum=3000))
                cost = self.create(db, tenant, 'cost', job_id=job['id'], assignment_id=record['id'],
                                   category='Honorarios', description=f"{record['role']} · {member['name']}",
                                   beneficiary=member['id'], beneficiary_name=member['name'], budget=fee,
                                   estimate=fee, final=None, status='estimado', due_date=text(data, 'due_date', required=False),
                                   currency='GTQ')
                if cost['due_date']:
                    day(cost['due_date'])
                self.invalidate(db, tenant, job['id'])
            elif action == 'assignment_status':
                record = self.get(db, tenant, 'assignment', text(data, 'id'))
                job = job_reader(record['job_id'])
                self.check_version(record, data)
                before = dict(record)
                status = text(data, 'status')
                if record['status'] not in ('borrador', 'pendiente', 'aceptada', 'reconfirmar') or status not in ('realizada', 'cancelada'):
                    raise TeamsError('Transición no disponible. No se borra ni se reinterpreta el historial.')
                costs = [c for c in self.records(db, tenant, 'cost') if c.get('assignment_id') == record['id']]
                cost = costs[0]
                if status == 'cancelada':
                    if self.paid(db, tenant, cost['id']) or cost['status'] == 'incurrido':
                        raise TeamsError('Resuelve pagos o costos incurridos antes de cancelar. Compensaciones requieren revisión.')
                    cost['status'] = 'anulado'
                    from src.teams_features import cancel_pending
                    cancel_pending(self, db, tenant, record['id'])
                else:
                    if job.get('status') in ('Cancelado', 'Archivado') or record['job_day'] != job.get('boda_date'):
                        raise TeamsError('El evento cambió o se canceló. Revisa la cobertura antes de validar el servicio.', 409)
                    conflicts = self.conflicts(db, tenant, record['member_id'], record['start'], record['end'], record['buffer'], record['id'])
                    if any(a['status'] in ('aceptada', 'realizada') for a in conflicts) and not record.get('conflict_override'):
                        raise TeamsError('Otra cobertura realizada se superpone. Se requiere revisar la excepción.', 409)
                    cost.update(status='incurrido', final=cost['final'] if cost['final'] is not None else cost['estimate'])
                record['status'] = status
                self.save(db, tenant, 'assignment', record)
                self.save(db, tenant, 'cost', cost)
                self.invalidate(db, tenant, record['job_id'])
            elif action == 'cost':
                job = job_reader(text(data, 'job_id', maximum=200))
                amount = cents(data.get('amount'))
                category = text(data, 'category', maximum=100)
                if category.casefold() in ('honorarios', 'anticipo', 'fondo para gastos', 'gasto compartido'):
                    raise TeamsError('Honorarios se crean desde una asignación. Fondos y gastos compartidos están pendientes de implementación.')
                member_id = text(data, 'member_id', required=False)
                if member_id:
                    member = self.get(db, tenant, 'member', member_id)
                    beneficiary, name = member['id'], member['name']
                else:
                    name = text(data, 'beneficiary_name', maximum=150)
                    beneficiary = 'supplier:' + name.casefold()
                due = text(data, 'due_date', required=False)
                if due:
                    day(due)
                record = self.create(db, tenant, 'cost', job_id=job['id'], category=category,
                                     description=text(data, 'description', maximum=300), beneficiary=beneficiary,
                                     beneficiary_name=name, budget=amount, estimate=amount, final=None,
                                     status='estimado', due_date=due, currency='GTQ')
                self.invalidate(db, tenant, job['id'])
            elif action == 'cost_status':
                record = self.get(db, tenant, 'cost', text(data, 'id'))
                job_reader(record['job_id'])
                self.check_version(record, data)
                before = dict(record)
                status = text(data, 'status')
                if status not in ('aprobado', 'incurrido', 'anulado') or record['status'] == 'anulado':
                    raise TeamsError('Estado económico no disponible.')
                if status == 'aprobado' and record['status'] != 'estimado':
                    raise TeamsError('Solo se aprueba un costo estimado.')
                if status == 'anulado' and (self.paid(db, tenant, record['id']) or record['status'] == 'incurrido'):
                    raise TeamsError('No se anula un costo incurrido o con pagos aplicados.')
                if status == 'incurrido':
                    final = cents(data.get('amount'))
                    if final < self.paid(db, tenant, record['id']):
                        raise TeamsError('El costo final no puede ser menor que lo ya pagado. Revisa el movimiento.')
                    record['final'] = final
                    record['evidence'] = text(data, 'evidence', maximum=1500)
                    if record.get('schedule_id'):
                        plan = self.get(db, tenant, 'schedule', record['schedule_id'])
                        if sum(p['amount'] for p in plan['plan']) != final:
                            raise TeamsError('El nuevo costo no coincide con sus cuotas. Revisa el plan antes del ajuste.')
                record['status'] = status
                self.save(db, tenant, 'cost', record)
                self.invalidate(db, tenant, record['job_id'])
            elif action == 'operation':
                job = job_reader(text(data, 'job_id', maximum=200))
                ops = [r for r in self.records(db, tenant, 'operation') if r['job_id'] == job['id']]
                record = ops[0] if ops else dict(id=str(uuid4()), job_id=job['id'])
                if ops:
                    self.check_version(record, data)
                    before = dict(record)
                elif data.get('version') not in (None, 0):
                    raise TeamsError('Recarga la operación antes de guardar.', 409)
                if type(data.get('reviewed', False)) is not bool:
                    raise TeamsError('Revisión inválida.')
                record.update(reviewed=data.get('reviewed', False),
                              instructions=text(data, 'instructions', required=False, maximum=6000))
                self.save(db, tenant, 'operation', record)
            elif action == 'payment':
                allocations = data.get('allocations')
                if not isinstance(allocations, list) or not allocations or len(allocations) > 100:
                    raise TeamsError('Selecciona los costos a pagar.')
                normalized, beneficiary, seen = [], None, set()
                for allocation in allocations:
                    if not isinstance(allocation, dict):
                        raise TeamsError('Aplicación inválida.')
                    cost = self.get(db, tenant, 'cost', text(allocation, 'cost_id'))
                    job_reader(cost['job_id'])
                    amount = cents(allocation.get('amount'))
                    total = cost['final'] if cost['final'] is not None else cost['estimate']
                    if (cost['currency'] != data.get('currency', 'GTQ') or cost['status'] not in ('aprobado', 'incurrido')
                            or amount <= 0 or amount > total - self.paid(db, tenant, cost['id'])):
                        raise TeamsError('Revisa moneda, aprobación y saldo disponible. No se permite sobrepagar.', 409)
                    if cost['id'] in seen or (beneficiary is not None and beneficiary != cost['beneficiary']):
                        raise TeamsError('Usa costos distintos del mismo beneficiario y marca.')
                    seen.add(cost['id'])
                    beneficiary = cost['beneficiary']
                    normalized.append(dict(cost_id=cost['id'], job_id=cost['job_id'], amount=amount))
                amount = cents(data.get('amount'))
                if amount != sum(a['amount'] for a in normalized):
                    raise TeamsError('La distribución debe sumar exactamente el importe del movimiento.')
                reference = text(data, 'reference', maximum=150)
                if any(p['reference'] == reference and p['sign'] == 1 for p in self.records(db, tenant, 'payment')):
                    warnings.append('Esta referencia también aparece en otro movimiento. Revisa el historial.')
                from src.teams_features import file_fields
                attachment = file_fields(data)
                record = self.create(db, tenant, 'payment', amount=amount, sign=1, allocations=normalized,
                                     beneficiary=beneficiary, beneficiary_name=cost['beneficiary_name'],
                                     currency='GTQ', effective_date=day(data.get('effective_date')),
                                     method=text(data, 'method', maximum=100), reference=reference,
                                     created_at=now(), actor=actor, reversal_of=None, **attachment)
            elif action == 'reverse':
                original = self.get(db, tenant, 'payment', text(data, 'id'))
                if original.get('advance_id'):
                    raise TeamsError('Los fondos se corrigen mediante su liquidación y devolución vinculada.')
                if original['sign'] != 1 or any(p['reversal_of'] == original['id'] for p in self.records(db, tenant, 'payment')):
                    raise TeamsError('Este movimiento ya está revertido o es un reverso.', 409)
                for allocation in original['allocations']:
                    job_reader(allocation['job_id'])
                record = self.create(db, tenant, 'payment', amount=original['amount'], sign=-1,
                                     allocations=original['allocations'], beneficiary=original['beneficiary'],
                                     beneficiary_name=original['beneficiary_name'], currency='GTQ',
                                     effective_date=day(data.get('effective_date')), method='Reverso',
                                     reference=text(data, 'reason', maximum=1000), created_at=now(),
                                     actor=actor, reversal_of=original['id'])
            else:
                from src.teams_features import handle_command
                record, before, warnings = handle_command(self, db, tenant, actor, data, job_reader, member_id)
            from src.teams_features import clean
            self.create(db, tenant, 'audit', action=action, actor=actor, created_at=now(),
                        before=clean(before) if before else before, after=clean(record))
            result = dict(ok=True, record=clean(record), warnings=warnings)
            db.execute('INSERT INTO commands VALUES (?, ?, ?, ?)', (tenant, key, fingerprint, json.dumps(result)))
            return result


MONTHS_ES = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
             'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre')


def teams_date(value):
    """Format the stored calendar day without shifting its timezone or time."""
    if not value:
        return 'Sin fecha'
    raw = str(value)
    try:
        date = datetime.strptime(raw[:10], '%Y-%m-%d')
    except ValueError:
        return 'Fecha por revisar'
    result = f'{date.day} de {MONTHS_ES[date.month - 1]} de {date.year}'
    if len(raw) >= 16 and raw[10] in ('T', ' '):
        result += ' · ' + raw[11:16]
    return result


def register_teams(app, crm_store, canonical_jobs, financial_summary):
    """Owner-only extension; production data stays on the CRM persistent disk."""
    data_dir = Path(crm_store.data_dir)
    database = TeamsStore((data_dir.parent if app.config.get('FLOW_TEAMS_LOCAL') else data_dir) / 'teams.sqlite3')
    app.extensions['teams'] = database
    blueprint = Blueprint('teams', __name__)
    app.jinja_env.filters['teams_money'] = money
    app.jinja_env.filters['teams_date'] = teams_date

    @blueprint.before_request
    def owner_only():
        local = current_app.config.get('FLOW_TEAMS_LOCAL')
        if (not (local or current_app.config.get('FLOW_TEAMS_ENABLED'))
                or (local and request.remote_addr not in ('127.0.0.1', '::1'))
                or not session.get('logged_in') or not session.get('tenant_id')):
            abort(404)
        owner = crm_store.get('tenants', session['tenant_id'])
        if not owner or session.get('user_email') != owner.get('login_email'):
            abort(403)
        if request.method == 'POST':
            from src.teams_features import MAX_FILE_BYTES
            limit = (21 * 1024 * 1024 if request.endpoint == 'teams.directory_import' else
                     MAX_FILE_BYTES + 1024 * 1024 if request.endpoint in ('teams.upload', 'teams.payment_upload') else 65536)
            if request.content_length is not None and request.content_length > limit:
                abort(413)
            token = session.get('teams_csrf')
            if not token or not secrets.compare_digest(request.headers.get('X-Teams-CSRF', ''), token):
                abort(403)

    @blueprint.after_request
    def private_response(response):
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @blueprint.errorhandler(TeamsError)
    def invalid(error):
        return jsonify(ok=False, error=error.message), error.status

    def read_job(identifier):
        job = next((j for j in canonical_jobs() if j['id'] == identifier), None)
        if not job:
            raise TeamsError('Boda no disponible en esta marca.', 404)
        tenant = crm_store.get('tenants', session['tenant_id'])
        if (job.get('currency') or tenant.get('currency', 'GTQ')) != 'GTQ':
            raise TeamsError('La propuesta solo admite GTQ. No se convierte la moneda.')
        commercial = financial_summary(job, [p for p in crm_store.list('payments') if p.get('job_id') == identifier and p.get('tipo') != 'team_payment'])
        return dict(job, reference_income_cents=cents(commercial['total'], imported=True))

    def snapshot(year=None):
        tenant = session['tenant_id']
        jobs = canonical_jobs()
        with database.transaction() as db:
            members = database.records(db, tenant, 'member')
            assignments = database.records(db, tenant, 'assignment')
            costs = database.records(db, tenant, 'cost')
            payments = database.records(db, tenant, 'payment')
            operations = database.records(db, tenant, 'operation')
            audit = database.records(db, tenant, 'audit')
            extra = {kind: database.records(db, tenant, kind) for kind in (
                'document', 'receipt', 'notice', 'task', 'expense_request', 'availability', 'advance', 'settlement', 'report', 'config', 'schedule')}
            from src.teams_features import advance_balance, clean
            for advance in extra['advance']:
                advance['remaining'] = advance_balance(database, db, tenant, advance)
            extra['document'] = [clean(d) for d in extra['document']]
        member_map = {m['id']: m for m in members}
        job_map = {j['id']: j for j in jobs}
        paid = {}
        for p in payments:
            for a in p['allocations']:
                paid[a['cost_id']] = paid.get(a['cost_id'], 0) + a['amount'] * p['sign']
        for s in extra['settlement']:
            for a in s['allocations']:
                paid[a['cost_id']] = paid.get(a['cost_id'], 0) + a['amount']
        for c in costs:
            c['total'] = 0 if c['status'] == 'anulado' else (c['final'] if c['final'] is not None else c['estimate'])
            c['paid'] = paid.get(c['id'], 0)
            c['pending'] = c['total'] - c['paid'] if c['status'] in ('aprobado', 'incurrido') else 0
            c['job_name'] = job_map.get(c['job_id'], {}).get('nombre', 'Evento no disponible')
            c['late'] = bool(c['pending'] and c['due_date'] and c['due_date'] < datetime.now(LOCAL_ZONE).date().isoformat())
            c['schedule'] = []
            plan = next((s for s in extra['schedule'] if s['id'] == c.get('schedule_id')), None)
            if plan:
                remaining_paid = c['paid']
                for entry in plan['plan']:
                    applied = min(remaining_paid, entry['amount'])
                    remaining_paid -= applied
                    c['schedule'].append(dict(entry, paid=applied, pending=entry['amount']-applied))
                c['late'] = any(e['pending'] and e['due_date'] < datetime.now(LOCAL_ZONE).date().isoformat() for e in c['schedule'])
        for a in assignments:
            a['calendar_day'] = a['start'][:10]
            a['cost'] = next((c for c in costs if c.get('assignment_id') == a['id']), None)
            a['member_name'] = member_map.get(a['member_id'], {}).get('name', 'Miembro inactivo')
            a['job_name'] = job_map.get(a['job_id'], {}).get('nombre', 'Evento no disponible')
            a['changed'] = a['job_day'] != job_map.get(a['job_id'], {}).get('boda_date')
            a['conflicts'] = [other['id'] for other in assignments if other['id'] != a['id']
                and other['member_id'] == a['member_id'] and other['status'] not in ('cancelada','rechazada') and a['status'] not in ('cancelada','rechazada')
                and datetime.fromisoformat(a['start']) - timedelta(minutes=a['buffer']) < datetime.fromisoformat(other['end']) + timedelta(minutes=other['buffer'])
                and datetime.fromisoformat(other['start']) - timedelta(minutes=other['buffer']) < datetime.fromisoformat(a['end']) + timedelta(minutes=a['buffer'])]
        billable = [p for p in crm_store.list('payments') if p.get('tipo') != 'team_payment']
        for j in jobs:
            commercial = financial_summary(j, [p for p in billable if p.get('job_id') == j['id']])
            j['income'] = cents(commercial['total'], imported=True)
            j['collected'] = cents(commercial['pagado'], imported=True)
            jcosts = [c for c in costs if c['job_id'] == j['id']]
            op = next((o for o in operations if o['job_id'] == j['id']), {})
            j['cost_total'] = sum(c['total'] for c in jcosts)
            j['pending'] = sum(c['pending'] for c in jcosts)
            j['paid'] = sum(c['paid'] for c in jcosts)
            j['cash_out'] = sum(a['amount'] * p['sign'] for p in payments for a in p['allocations'] if a['job_id'] == j['id'])
            j['cash_out'] += sum(p['amount'] * p['sign'] for p in payments if p.get('advance_id') and p.get('job_id') == j['id'])
            j['margin'] = j['income'] - j['cost_total']
            j['percent'] = round(j['margin'] * 100 / j['income'], 1) if j['income'] else None
            j['cash'] = j['collected'] - j['cash_out']
            j['reviewed'] = bool(op.get('reviewed'))
            j['configured'] = bool(jcosts or j['reviewed'])
            j['incomplete'] = not j['income'] or not j['reviewed'] or bool(commercial['descuadre_cotizado_vs_cuotas'])
            j['assignments'] = [a for a in assignments if a['job_id'] == j['id'] and a['status'] not in ('cancelada', 'rechazada')]
            j['closed'] = bool(op.get('closed'))
            j['report'] = next((r for r in extra['report'] if r['id'] == op.get('report_id')), None)
            j['income_delta'] = j['income'] - j['report']['income'] if j['report'] else 0
            if j['closed']:
                j['income'] = j['report']['income']
                j['margin'] = j['report']['margin']
                j['percent'] = round(j['margin'] * 100 / j['income'], 1) if j['income'] else None
        for m in members:
            m['rate_missing'] = m.get('rate_missing', bool(m.get('source') and not m['rate']))
            own = [c for c in costs if c['beneficiary'] == m['id']]
            m['pending'] = sum(c['pending'] for c in own)
            m['paid'] = sum(c['paid'] for c in own)
            m['jobs_count'] = len({a['job_id'] for a in assignments if a['member_id'] == m['id'] and a['status'] not in ('cancelada','rechazada')})
            m['access_version'] = m.get('access_version', 1)
        visible_jobs = [j for j in jobs if not year or str(j.get('boda_date', '')).startswith(year + '-')]
        visible_payments = [p for p in payments if not year or p['effective_date'].startswith(year + '-')]
        totals = {key: sum(j[key] for j in visible_jobs) for key in ('income', 'cost_total', 'margin', 'pending', 'paid')}
        totals['cash_out'] = sum(p['amount'] * p['sign'] for p in visible_payments)
        eligible = [j for j in visible_jobs if j['configured'] and j['income'] and j.get('status') not in ('Cancelado', 'Archivado')]
        totals['margin'] = sum(j['margin'] for j in eligible)
        totals['margin_income'] = sum(j['income'] for j in eligible)
        totals['margin_events'] = len(eligible)
        totals['percent'] = round(totals['margin'] * 100 / totals['margin_income'], 1) if totals['margin_income'] else None
        totals['incomplete'] = sum(j['incomplete'] for j in visible_jobs)
        return dict(jobs=visible_jobs, members=members, assignments=assignments, costs=costs,
                    payments=[clean(p) for p in visible_payments], operations=operations, audit=audit[-50:][::-1], totals=totals,
                    documents=extra['document'], receipts=extra['receipt'], notices=extra['notice'], tasks=extra['task'],
                    expense_requests=[clean(r) for r in extra['expense_request']], availability=extra['availability'], advances=extra['advance'],
                    reports=extra['report'], team_config=next(iter(extra['config']), {}),
                    role_options=list(dict.fromkeys(r.strip() for r in
                        next(iter(extra['config']), {}).get('roles', DEFAULT_ROLES).splitlines() if r.strip())))

    @blueprint.route('/api/teams/command', methods=['POST'])
    def command():
        return jsonify(database.command(session['tenant_id'], session['user_email'], request.get_json(silent=True), read_job))

    @blueprint.route('/api/teams/summary')
    def summary():
        return jsonify(snapshot())

    @blueprint.route('/api/teams/directory/import', methods=['POST'])
    def directory_import():
        import tempfile
        import zipfile
        from src.teams_directory import import_notion
        upload = request.files.get('file')
        if not upload:
            raise TeamsError('Selecciona el ZIP exportado de Notion.')
        content = upload.stream.read(20 * 1024 * 1024 + 1)
        if len(content) > 20 * 1024 * 1024:
            raise TeamsError('El ZIP supera 20 MB.')
        # Import only the logged-in brand. The source never goes into Git or a public URL.
        backup = Path(database.path).parent / 'teams-backups'
        backup.mkdir(mode=0o700, exist_ok=True)
        backup_file = backup / ('before-import-' + secrets.token_hex(8) + '.sqlite3')
        with sqlite3.connect(database.path) as source, sqlite3.connect(backup_file) as destination:
            source.backup(destination)
        backup_file.chmod(0o600)
        with tempfile.NamedTemporaryFile(suffix='.zip', dir=Path(database.path).parent) as source_file:
            source_file.write(content)
            source_file.flush()
            try:
                result = import_notion(database, source_file.name, [session['tenant_id']])
            except (ValueError, zipfile.BadZipFile, UnicodeError, KeyError):
                raise TeamsError('El archivo no es una exportación válida del directorio de Notion.')
        return jsonify(ok=True, **result)

    @blueprint.route('/teams/members/<member_id>')
    def member_private(member_id):
        from src.teams_directory import DIRECTORY, PRIVATE_FIELDS
        with database.transaction() as db:
            member = database.get(db,session['tenant_id'],'member',member_id)
            profile = database.get(db,DIRECTORY,'person_private',member['directory_id']) if member.get('directory_id') else {}
        session.setdefault('teams_csrf',secrets.token_urlsafe(32))
        return render_template('teams_member_private.html',member=member,profile=profile,private_fields=PRIVATE_FIELDS,
                               csrf=session['teams_csrf'])

    @blueprint.route('/teams/export.csv')
    def export_costs():
        year = request.args.get('year', '')
        if year and (len(year) != 4 or not year.isdigit()):
            abort(400)
        data = snapshot(year)
        jobs = {j['id'] for j in data['jobs']}
        member_id = request.args.get('member_id', '')
        if member_id and not any(m['id'] == member_id for m in data['members']):
            abort(404)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Boda', 'Beneficiario', 'Categoría', 'Descripción', 'Estado', 'Moneda', 'Costo', 'Pagado', 'Pendiente'])
        for cost in data['costs']:
            if cost['job_id'] not in jobs or (member_id and cost['beneficiary'] != member_id):
                continue
            if request.args.get('category') and cost['category'] != request.args['category']:
                continue
            # A spreadsheet must treat user-entered text as text, never a formula.
            values = [cost['job_name'], cost['beneficiary_name'], cost['category'], cost['description'], cost['status'], 'GTQ']
            values = ["'" + v if v.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else v for v in values]
            writer.writerow(values + [f"{cost[key] / 100:.2f}" for key in ('total', 'paid', 'pending')])
        return Response('\ufeff' + output.getvalue(), mimetype='text/csv', headers={
            'Content-Disposition': 'attachment; filename="flow-teams-costos.csv"', 'Cache-Control': 'no-store'})

    @blueprint.route('/teams')
    @blueprint.route('/teams/<section>')
    @blueprint.route('/teams/jobs/<job_id>')
    def page(section='jobs', job_id=None):
        if section not in ('dashboard', 'jobs', 'members', 'payments', 'calendar', 'roadmap', 'communications', 'settings'):
            abort(404)
        year = request.args.get('year', '')
        if year and (len(year) != 4 or not year.isdigit()):
            abort(400)
        if section == 'dashboard':
            section = 'jobs'
        # Job pages always resolve against the complete authorized set, independently of report filters.
        data = snapshot(None if job_id else year)
        selected = None
        if job_id:
            read_job(job_id)
            selected = next(j for j in data['jobs'] if j['id'] == job_id)
            section = 'detail'
        session.setdefault('teams_csrf', secrets.token_urlsafe(32))
        from src.tenant_brand_map import all_resolved_brands
        switches = [b for b in all_resolved_brands() if b.brand_key in ('astral', 'norkevin')
                    and crm_store.get('tenants', b.internal_tenant_id)] if current_app.config.get('FLOW_TEAMS_LOCAL') else []
        return render_template('teams.html', section=section, selected=selected, year=year, switches=switches,
                               action_labels={'member':'Miembro actualizado','assignment':'Cobertura creada','assignment_edit':'Condiciones revisadas',
                                   'assignment_publish':'Cobertura compartida','assignment_status':'Servicio actualizado','response':'Respuesta del miembro',
                                   'cost':'Gasto registrado','cost_status':'Importe aprobado o validado','operation':'Revisión interna',
                                   'payment':'Pago registrado','reverse':'Pago revertido','document':'Documento revisado','document_publish':'Documento publicado',
                                   'document_read':'Lectura confirmada','document_withdraw':'Documento retirado','advance':'Fondo entregado',
                                   'settlement':'Fondo liquidado','advance_return':'Fondo devuelto','expense_review':'Reembolso revisado',
                                   'cost_shared':'Gasto distribuido','schedule':'Cuotas planificadas','job_close':'Operación cerrada','job_reopen':'Operación reabierta'},
                               csrf=session['teams_csrf'], today=datetime.now(LOCAL_ZONE).date().isoformat(), **data)

    from src.teams_portal import register_portal
    register_portal(app, blueprint, database, crm_store, read_job)
    app.register_blueprint(blueprint)
