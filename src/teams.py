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


def payment_deadline(job, earlier=''):
    if earlier:
        day(earlier)
    wedding_day = job.get('boda_date')
    if not wedding_day:
        return earlier
    limit = (date.fromisoformat(wedding_day[:10]) + timedelta(days=30)).isoformat()
    return min(earlier, limit) if earlier else limit


TRAVEL_RESPONSES = {'available': 'Disponible todo el viaje', 'wedding_only': 'Solo el día de la boda',
                    'unavailable': 'No disponible', 'pending': 'Por responder'}


def assignment_trip(assignment, plans):
    plan = next((p for p in plans if p['job_id'] == assignment['job_id']), {})
    if not plan.get('enabled'):
        return None
    override = assignment.get('travel_override') or {}
    return dict(departure=override.get('departure') or plan['departure'],
                return_date=override.get('return_date') or plan['return_date'],
                note=plan.get('note', ''), personal=bool(override),
                response=assignment.get('travel_response', 'pending'),
                response_label=TRAVEL_RESPONSES.get(assignment.get('travel_response'), 'Por responder'),
                response_note=assignment.get('travel_response_note', ''))


def availability_window(assignment, trip=None):
    if trip and trip['response'] != 'wedding_only':
        return (datetime.fromisoformat(trip['departure']).replace(tzinfo=LOCAL_ZONE),
                datetime.fromisoformat(trip['return_date']).replace(tzinfo=LOCAL_ZONE) + timedelta(days=1))
    if assignment.get('schedule_pending'):
        return (datetime.fromisoformat(assignment['start']).replace(hour=0, minute=0, second=0, microsecond=0),
                datetime.fromisoformat(assignment['end']).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
    return datetime.fromisoformat(assignment['start']), datetime.fromisoformat(assignment['end'])


def coverage_times(data, job, *, pending=False):
    pending = data.get('schedule_pending', pending)
    if type(pending) is not bool:
        raise TeamsError('Revisa si el horario está pendiente.')
    try:
        if pending:
            start = datetime.fromisoformat(day(job.get('boda_date'))).replace(tzinfo=LOCAL_ZONE)
            end = datetime.fromisoformat(day(job.get('end_date') or job.get('boda_date'))).replace(
                hour=23, minute=59, second=59, tzinfo=LOCAL_ZONE)
        else:
            start = datetime.fromisoformat(data['start']).replace(tzinfo=LOCAL_ZONE)
            end = datetime.fromisoformat(data['end']).replace(tzinfo=LOCAL_ZONE)
        buffer = int(data.get('buffer', 0))
        if start >= end or not 0 <= buffer <= 1440:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise TeamsError('Revisa inicio, fin y margen de traslado. El fin debe ser posterior al inicio.')
    return start, end, buffer, pending


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
        parent_ids = {c.get('parent_job_id') for c in self.records(db, tenant, 'cost') if c['job_id'] == job_id}
        operations = [r for r in self.records(db, tenant, 'operation') if r['job_id'] in parent_ids | {job_id}]
        for operation in operations:
            if operation.get('reviewed'):
                operation['reviewed'] = False
                self.save(db, tenant, 'operation', operation)

    def conflicts(self, db, tenant, member_id, start, end, buffer, exclude=None, candidate=None):
        plans = self.records(db, tenant, 'travel')
        current = candidate or next((a for a in self.records(db, tenant, 'assignment') if a['id'] == exclude), None)
        trip = assignment_trip(current, plans) if current else None
        start, end = availability_window(dict(start=start, end=end, schedule_pending=(current or {}).get('schedule_pending')), trip)
        start = start - timedelta(minutes=buffer)
        end = end + timedelta(minutes=buffer)
        conflicts = []
        for a in self.records(db, tenant, 'assignment'):
            if a['id'] == exclude or a['member_id'] != member_id or a['status'] in ('cancelada', 'rechazada'):
                continue
            other_start, other_end = availability_window(a, assignment_trip(a, plans))
            other_start -= timedelta(minutes=a['buffer'])
            other_end += timedelta(minutes=a['buffer'])
            if start < other_end and other_start < end:
                conflicts.append(a)
        if getattr(self, 'crm_store', None):
            from src.teams_shared import cross_conflicts
            conflicts.extend(cross_conflicts(self, db, tenant, member_id, start, end))
        return conflicts

    def command(self, tenant, actor, data, job_reader, *, member_id=None):
        if not isinstance(data, dict):
            raise TeamsError('Se necesita un objeto JSON.')
        key = text(data, 'key', maximum=100)
        member_actions = ('response', 'document_read', 'task_complete', 'expense_request', 'availability', 'availability_remove', 'travel_response')
        if member_id:
            if data.get('action') not in member_actions:
                raise TeamsError('Esta acción no pertenece al portal del miembro.', 403)
            key = f'member:{member_id}:{key}'
        elif data.get('action') in ('response', 'document_read', 'expense_request', 'availability', 'availability_remove', 'travel_response'):
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
                protected = ('assignment', 'assignment_status', 'assignment_publish', 'assignment_edit', 'assignment_schedule_pending', 'assignment_acknowledge', 'cost',
                             'cost_status', 'cost_edit', 'operation', 'advance', 'settlement', 'expense_review', 'cost_shared', 'schedule', 'travel', 'assignment_travel')
                if action in protected and any(o['job_id'] in (identifier, job.get('parent_job_id')) and o.get('closed') for o in self.records(db, tenant, 'operation')):
                    raise TeamsError('La operación está cerrada. Reábrela con un motivo antes de cambiar sus costos o coberturas.', 409)
                return job
            job_reader = guarded_job_reader
            before = None
            warnings = []
            if action == 'job_classification':
                job = job_reader(text(data, 'job_id', maximum=200))
                state = text(data, 'state')
                if state not in ('included', 'archived', 'not_applicable'):
                    raise TeamsError('Clasificación de Teams inválida.')
                rows = [r for r in self.records(db, tenant, 'job_classification') if r['job_id'] == job['id']]
                record = rows[0] if rows else dict(id=str(uuid4()), job_id=job['id'], state='included', version=0)
                self.check_version(record, data)
                before = dict(record)
                commitments = any(a['job_id'] == job['id'] for a in self.records(db, tenant, 'assignment'))
                commitments = commitments or any(c['job_id'] == job['id'] for c in self.records(db, tenant, 'cost'))
                commitments = commitments or any(p.get('job_id') == job['id'] or any(a['job_id'] == job['id'] for a in p['allocations'])
                                                  for p in self.records(db, tenant, 'payment'))
                if state == 'not_applicable' and commitments and data.get('confirmed') is not True:
                    raise TeamsError('Esta boda tiene asignaciones o movimientos. Confirma que sus registros y obligaciones se conservarán.', 409)
                record.update(state=state, updated_at=now(), updated_by=actor)
                self.save(db, tenant, 'job_classification', record)
            elif action == 'member':
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
                    record['shared_portal_blocked'] = True
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
                start, end, buffer, schedule_pending = coverage_times(data, job)
                slot = text(data, 'slot', required=False, maximum=100) or (text(data, 'role', maximum=100) + ' · ' + member['name'])[:100]
                if any(a['job_id'] == job['id'] and a['slot'].casefold() == slot.casefold()
                       and a['status'] not in ('cancelada', 'rechazada') for a in self.records(db, tenant, 'assignment')):
                    raise TeamsError('Esta plaza ya tiene una asignación. Usa otra plaza o cancela la anterior.', 409)
                candidate = dict(job_id=job['id'], start=start.isoformat(), end=end.isoformat(), schedule_pending=schedule_pending)
                trip = assignment_trip(candidate, self.records(db, tenant, 'travel'))
                if trip and not trip['departure'] <= start.date().isoformat() <= end.date().isoformat() <= trip['return_date']:
                    raise TeamsError('La cobertura debe estar dentro de las fechas de viaje de esta boda.')
                conflicts = self.conflicts(db, tenant, member['id'], start.isoformat(), end.isoformat(), buffer, candidate=candidate)
                if conflicts:
                    warnings.append('Borrador guardado con conflicto de horario. Revisa el calendario antes de confirmar con el equipo.')
                    brands = sorted({a['brand_name'] for a in conflicts if a.get('brand_name')})
                    if brands:
                        warnings.append('Esta persona ya tiene una cobertura en ' + ', '.join(brands) + ' en ese horario.')
                fee = cents(data.get('amount'))
                record = self.create(db, tenant, 'assignment', job_id=job['id'], member_id=member['id'],
                                     role=text(data, 'role', maximum=100), slot=slot, start=start.isoformat(),
                                     end=end.isoformat(), buffer=buffer, schedule_pending=schedule_pending, status='borrador', terms_version=1,
                                     job_day=job.get('boda_date'), instructions=text(data, 'instructions', required=False, maximum=3000))
                cost = self.create(db, tenant, 'cost', job_id=job['id'], parent_job_id=job.get('parent_job_id'), assignment_id=record['id'],
                                   category='Honorarios', description=f"{record['role']} · {member['name']}",
                                   beneficiary=member['id'], beneficiary_name=member['name'], budget=fee,
                                   estimate=fee, final=None, status='estimado', due_date=payment_deadline(job, text(data, 'due_date', required=False)),
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
                    name = text(data, 'beneficiary_name', required=False, maximum=150) or 'Gastos generales de la boda'
                    beneficiary = 'supplier:' + name.casefold()
                due = text(data, 'due_date', required=False)
                if due:
                    day(due)
                record = self.create(db, tenant, 'cost', job_id=job['id'], parent_job_id=job.get('parent_job_id'), category=category,
                                     description=text(data, 'description', maximum=300), beneficiary=beneficiary,
                                     beneficiary_name=name, budget=amount, estimate=amount, final=None,
                                     status='estimado', due_date=due, currency='GTQ')
                self.invalidate(db, tenant, job['id'])
            elif action == 'cost_edit':
                record = self.get(db, tenant, 'cost', text(data, 'id'))
                job_reader(record['job_id'])
                self.check_version(record, data)
                before = dict(record)
                if record.get('assignment_id') or record['status'] not in ('estimado', 'aprobado'):
                    raise TeamsError('Solo se editan gastos pendientes. Los honorarios se editan desde el trabajador.')
                amount = cents(data.get('amount'))
                if amount < self.paid(db, tenant, record['id']):
                    raise TeamsError('El gasto no puede quedar debajo de lo ya pagado.')
                if record.get('schedule_id'):
                    plan = self.get(db, tenant, 'schedule', record['schedule_id'])
                    if sum(p['amount'] for p in plan['plan']) != amount:
                        raise TeamsError('El gasto debe coincidir con sus cuotas.')
                due = text(data, 'due_date', required=False)
                if due:
                    day(due)
                record.update(description=text(data, 'description', maximum=300), estimate=amount, due_date=due)
                self.save(db, tenant, 'cost', record)
                self.invalidate(db, tenant, record['job_id'])
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
    weekday = ('lunes','martes','miércoles','jueves','viernes','sábado','domingo')[date.weekday()]
    result = f'{weekday}, {date.day} de {MONTHS_ES[date.month - 1]} de {date.year}'
    if len(raw) >= 16 and raw[10] in ('T', ' '):
        result += ' · ' + raw[11:16]
    return result


def teams_zone(crm_store, tenant):
    company = (crm_store.get_tenant_dict('settings', tenant_id=tenant) or {}).get('company') or {}
    try:
        return ZoneInfo(company.get('timezone') or 'America/Guatemala')
    except (ValueError, KeyError):
        return LOCAL_ZONE


def job_phase(job, assignments, today):
    """Presentation only: an ongoing event keeps its place without changing CRM."""
    try:
        start = date.fromisoformat(str(job.get('boda_date'))[:10])
    except ValueError:
        return 'undated'
    end = start
    for value in [job.get('end_date')] + [a['end'] for a in assignments if a['status'] not in ('cancelada', 'rechazada')]:
        try:
            end = max(end, date.fromisoformat(str(value)[:10]))
        except ValueError:
            pass
    return 'upcoming' if end >= today else 'past'


def register_teams(app, crm_store, canonical_jobs, financial_summary, job_is_active):
    """Owner-only extension; production data stays on the CRM persistent disk."""
    data_dir = Path(crm_store.data_dir)
    database = TeamsStore((data_dir.parent if app.config.get('FLOW_TEAMS_LOCAL') else data_dir) / 'teams.sqlite3')
    database.crm_store = crm_store
    app.extensions['teams'] = database
    blueprint = Blueprint('teams', __name__)
    app.jinja_env.filters['teams_money'] = money
    app.jinja_env.filters['teams_date'] = teams_date

    @app.template_filter('teams_calendar_date')
    def calendar_date(value):
        if not value:return teams_date(value)
        return teams_date(datetime.fromisoformat(value).astimezone(teams_zone(crm_store, session['tenant_id'])).isoformat())

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
            limit = MAX_FILE_BYTES + 1024 * 1024 if request.endpoint in ('teams.upload', 'teams.payment_upload') else 65536
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

    primary_jobs = canonical_jobs
    def operational_jobs():
        from src.linked_coverages import linked_coverages
        jobs = primary_jobs()
        return jobs + linked_coverages(jobs, crm_store.list('calendar'))

    def read_job(identifier):
        job = next((j for j in operational_jobs() if j['id'] == identifier), None)
        if not job:
            raise TeamsError('Boda no disponible en esta marca.', 404)
        tenant = crm_store.get('tenants', session['tenant_id'])
        if (job.get('currency') or tenant.get('currency', 'GTQ')) != 'GTQ':
            raise TeamsError('La propuesta solo admite GTQ. No se convierte la moneda.')
        commercial = dict(total=0) if job.get('secondary') else financial_summary(job, [p for p in crm_store.list('payments') if p.get('job_id') == identifier and p.get('tipo') != 'team_payment'])
        return dict(job, reference_income_cents=cents(commercial['total'], imported=True))

    def job_totals(report_jobs):
        report_jobs = [j for j in report_jobs if not j.get('secondary')]
        totals = {key: sum(j[key] for j in report_jobs) for key in ('income', 'cost_total', 'margin', 'pending', 'paid')}
        eligible = [j for j in report_jobs if j['configured'] and j['income'] and j.get('status') not in ('Cancelado', 'Archivado')]
        totals['margin'] = sum(j['margin'] for j in eligible)
        totals['margin_income'] = sum(j['income'] for j in eligible)
        totals['margin_events'] = len(eligible)
        totals['percent'] = round(totals['margin'] * 100 / totals['margin_income'], 1) if totals['margin_income'] else None
        totals['incomplete'] = sum(j['incomplete'] for j in report_jobs)
        return totals

    def snapshot(year=None, *, active_only=False):
        from src.teams_calendar import valid_invitation_email
        tenant = session['tenant_id']
        jobs = operational_jobs()
        with database.transaction() as db:
            members = database.records(db, tenant, 'member')
            assignments = database.records(db, tenant, 'assignment')
            costs = database.records(db, tenant, 'cost')
            payments = database.records(db, tenant, 'payment')
            operations = database.records(db, tenant, 'operation')
            audit = database.records(db, tenant, 'audit')
            extra = {kind: database.records(db, tenant, kind) for kind in (
                'document', 'receipt', 'notice', 'task', 'expense_request', 'availability', 'advance', 'settlement', 'report', 'config', 'schedule', 'job_classification', 'calendar_sync', 'travel')}
            from src.teams_shared import cross_conflicts
            for a in assignments:
                start, end = availability_window(a, assignment_trip(a, extra['travel']))
                margin = timedelta(minutes=a['buffer'])
                a['other_brand_conflicts'] = (cross_conflicts(database, db, tenant, a['member_id'], start-margin, end+margin)
                    if a['status'] not in ('cancelada', 'rechazada') else [])
            from src.teams_features import advance_balance, clean
            for advance in extra['advance']:
                advance['remaining'] = advance_balance(database, db, tenant, advance)
            extra['document'] = [clean(d) for d in extra['document']]
        for a in assignments:
            a['trip'] = assignment_trip(a, extra['travel'])
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
            if c.get('assignment_id'):
                c['due_date'] = payment_deadline(job_map.get(c['job_id'], {}), c.get('due_date', ''))
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
                c['late'] = c['late'] or any(e['pending'] and e['due_date'] < datetime.now(LOCAL_ZONE).date().isoformat() for e in c['schedule'])
        for a in assignments:
            a['calendar_day'] = a['start'][:10]
            a['member_email'] = member_map.get(a['member_id'], {}).get('email', '')
            a['invite_problem'] = ('Agrega un correo válido para enviar la invitación.' if not valid_invitation_email(a['member_email'])
                else 'Este miembro está inactivo. Revisa su ficha antes de invitarlo.' if not member_map.get(a['member_id'], {}).get('active', False)
                else 'La fecha de esta cobertura cambió. Revisa su horario antes de invitar.' if a['job_day'] != job_map.get(a['job_id'], {}).get('boda_date') else '')
            a['can_invite'] = not a['invite_problem']
            a['cost'] = next((c for c in costs if c.get('assignment_id') == a['id']), None)
            a['member_name'] = member_map.get(a['member_id'], {}).get('name', 'Miembro inactivo')
            a['job_name'] = job_map.get(a['job_id'], {}).get('nombre', 'Evento no disponible')
            a['changed'] = a['job_day'] != job_map.get(a['job_id'], {}).get('boda_date')
            current = not a['changed'] and job_map.get(a['job_id'], {}).get('status') not in ('Cancelado', 'Archivado')
            a['can_confirm_manually'] = current and member_map.get(a['member_id'], {}).get('active', False) and a['status'] in ('pendiente', 'aceptada', 'reconfirmar')
            confirmation = a.get('manual_confirmation') or {}
            a['manual_confirmed'] = bool(current and a['status'] in ('pendiente', 'aceptada', 'reconfirmar', 'realizada')
                and confirmation.get('member_id') == a['member_id'] and confirmation.get('terms_version') == a['terms_version'])
            a['conflicts'] = [other['id'] for other in assignments if other['id'] != a['id']
                and other['member_id'] == a['member_id'] and other['status'] not in ('cancelada','rechazada') and a['status'] not in ('cancelada','rechazada')
                and availability_window(a, a['trip'])[0] - timedelta(minutes=a['buffer']) < availability_window(other, other['trip'])[1] + timedelta(minutes=other['buffer'])
                and availability_window(other, other['trip'])[0] - timedelta(minutes=other['buffer']) < availability_window(a, a['trip'])[1] + timedelta(minutes=a['buffer'])]
        billable = [p for p in crm_store.list('payments') if p.get('tipo') != 'team_payment']
        today = datetime.now(teams_zone(crm_store, tenant)).date()
        for j in jobs:
            job_payments = [p for p in billable if p.get('job_id') == j['id']]
            j['es_activo'] = job_is_active(j, job_payments)
            commercial = dict(total=0, pagado=0, descuadre_cotizado_vs_cuotas=False) if j.get('secondary') else financial_summary(j, job_payments)
            j['income'] = cents(commercial['total'], imported=True)
            j['collected'] = cents(commercial['pagado'], imported=True)
            j['secondary_jobs'] = [child for child in jobs if child.get('parent_job_id') == j['id']]
            family = {j['id']} | {child['id'] for child in jobs if child.get('parent_job_id') == j['id']}
            jcosts = [c for c in costs if c['job_id'] in family or c.get('parent_job_id') == j['id']]
            op = next((o for o in operations if o['job_id'] == j['id']), {})
            j['cost_total'] = sum(c['total'] for c in jcosts)
            j['pending'] = sum(c['pending'] for c in jcosts)
            j['paid'] = sum(c['paid'] for c in jcosts)
            j['cash_out'] = sum(a['amount'] * p['sign'] for p in payments for a in p['allocations'] if a['job_id'] in family)
            j['cash_out'] += sum(p['amount'] * p['sign'] for p in payments if p.get('advance_id') and p.get('job_id') in family)
            j['margin'] = j['income'] - j['cost_total']
            j['percent'] = round(j['margin'] * 100 / j['income'], 1) if j['income'] else None
            j['cash'] = j['collected'] - j['cash_out']
            j['reviewed'] = bool(op.get('reviewed'))
            j['configured'] = bool(jcosts or j['reviewed'])
            j['incomplete'] = not j['income'] or not j['reviewed'] or bool(commercial['descuadre_cotizado_vs_cuotas'])
            j['travel'] = next((p for p in extra['travel'] if p['job_id'] == j['id']), {})
            j['assignments'] = [a for a in assignments if a['job_id'] == j['id'] and a['status'] not in ('cancelada', 'rechazada')]
            classification = next((r for r in extra['job_classification'] if r['job_id'] == j['id']), {})
            j['teams_state'] = classification.get('state', 'included')
            j['classification_version'] = classification.get('version', 0)
            j['teams_phase'] = job_phase(j, j['assignments'], today)
            j['teams_label'] = {'archived':'Archivada', 'not_applicable':'No aplica'}.get(j['teams_state']) or {
                'upcoming':'Próxima', 'past':'Pasada', 'undated':'Sin fecha'}[j['teams_phase']]
            j['team_pending'] = sum(c['pending'] for c in jcosts if c['beneficiary'] in member_map)
            j['has_commitments'] = bool(jcosts or any(a['job_id'] == j['id'] for a in assignments))
            j['closed'] = bool(op.get('closed') or any(o['job_id'] == j.get('parent_job_id') and o.get('closed') for o in operations))
            j['report'] = next((r for r in extra['report'] if r['id'] == op.get('report_id')), None)
            j['income_delta'] = j['income'] - j['report']['income'] if j['report'] else 0
            if j['closed'] and j['report']:
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
        visible_jobs = [j for j in jobs if (not active_only or j['es_activo'])
                        and (not year or str(j.get('boda_date', '')).startswith(year + '-'))]
        visible_payments = [p for p in payments if not year or p['effective_date'].startswith(year + '-')]
        totals = job_totals(visible_jobs)
        totals['cash_out'] = sum(p['amount'] * p['sign'] for p in visible_payments)
        return dict(jobs=visible_jobs, members=members, assignments=assignments, costs=costs,
                    payments=[clean(p) for p in visible_payments], operations=operations, audit=audit[-50:][::-1], totals=totals,
                    documents=extra['document'], receipts=extra['receipt'], notices=extra['notice'], tasks=extra['task'],
                    expense_requests=[clean(r) for r in extra['expense_request']], availability=extra['availability'], advances=extra['advance'],
                    reports=extra['report'], calendar_sync=extra['calendar_sync'], team_config=next(iter(extra['config']), {}),
                    role_options=list(dict.fromkeys(r.strip() for r in
                        next(iter(extra['config']), {}).get('roles', DEFAULT_ROLES).splitlines() if r.strip())))

    from src.teams_calendar_routes import register_calendar
    calendar_email = register_calendar(app, blueprint, crm_store, database, read_job, operational_jobs)

    @app.context_processor
    def team_mail_history():
        # Only owner pages receive these copies; never expose them in the worker portal.
        if request.endpoint not in ('job_detail', 'teams.page', 'teams.member_private'):
            return {}
        tenant = session.get('tenant_id')
        owner = crm_store.get('tenants', tenant) if tenant else None
        if not owner or not session.get('logged_in') or session.get('user_email') != owner.get('login_email'):
            return {}
        args = request.view_args or {}
        member_id = args.get('member_id')
        job_id = args.get('job_id') or (request.args.get('job_id') if member_id else None)
        if not job_id and not member_id:
            return {}
        with database.transaction() as db:
            rows = [r for r in database.records(db, tenant, 'team_mail')
                    if (r.get('job_id') == job_id if job_id else r.get('member_id') == member_id)]
        return dict(team_mail_history=sorted(rows, key=lambda r: r['created_at'], reverse=True))

    @blueprint.route('/api/teams/command', methods=['POST'])
    def command():
        return jsonify(database.command(session['tenant_id'], session['user_email'], request.get_json(silent=True), read_job))

    @blueprint.route('/api/teams/summary')
    def summary():
        return jsonify(snapshot())

    @blueprint.route('/teams/members/<member_id>')
    def member_private(member_id):
        from src.teams_directory import DIRECTORY, PRIVATE_FIELDS
        with database.transaction() as db:
            member = database.get(db,session['tenant_id'],'member',member_id)
            profile = database.get(db,DIRECTORY,'person_private',member['directory_id']) if member.get('directory_id') else {}
            job_id = request.args.get('job_id')
            if job_id:
                read_job(job_id)
                if not any(a['job_id']==job_id and a['member_id']==member_id for a in database.records(db,session['tenant_id'],'assignment')):
                    abort(404)
        session.setdefault('teams_csrf',secrets.token_urlsafe(32))
        return render_template('teams_member_private.html',member=member,profile=profile,private_fields=PRIVATE_FIELDS,access_job_id=job_id,
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
        if year and not (year == 'all' and section in ('jobs', 'dashboard')) and (len(year) != 4 or not year.isdigit()):
            abort(400)
        if section == 'dashboard':
            section = 'jobs'
        # Job pages always resolve against the complete authorized set, independently of report filters.
        data = snapshot(None if job_id or section == 'jobs' else year)
        view = request.args.get('view', 'upcoming')
        query = request.args.get('q', '').strip()
        if view not in ('upcoming', 'past', 'archived', 'not_applicable', 'all') or len(query) > 200:
            abort(400)
        if section == 'jobs' and not job_id:
            list_year = request.args.get('list_year', 'all')
            if list_year != 'all' and (len(list_year) != 4 or not list_year.isascii() or not list_year.isdigit()):
                abort(400)
            current_year = str(datetime.now(teams_zone(crm_store, session['tenant_id'])).year)
            report_year = year or current_year
            report_jobs = [j for j in data['jobs'] if report_year == 'all' or str(j.get('boda_date') or '').startswith(report_year + '-')]
            event_years = {str(j['boda_date'])[:4] for j in data['jobs'] if j.get('boda_date')}
            data.update(list_year=list_year, current_year=current_year,
                        list_years=sorted(event_years | {current_year} | ({list_year} if list_year != 'all' else set()), reverse=True),
                        report_year=report_year, report_years=sorted(event_years | {current_year} | ({report_year} if report_year != 'all' else set()), reverse=True),
                        report_totals=job_totals(report_jobs), report_jobs_count=sum(not j.get('secondary') for j in report_jobs))
            def in_view(job):
                if view == 'all':
                    return True
                if view in ('archived', 'not_applicable'):
                    return job['teams_state'] == view
                return job['teams_state'] == 'included' and job['teams_phase'] == view
            data['all_jobs_count'] = len(data['jobs'])
            data['jobs'] = sorted((j for j in data['jobs'] if in_view(j)
                                  and (list_year == 'all' or str(j.get('boda_date') or '').startswith(list_year + '-'))
                                  and query.casefold() in
                                  (str(j.get('nombre', ''))+' '+str(j.get('location', ''))).casefold()),
                                  key=lambda j: (not bool(j.get('boda_date')), j.get('boda_date') or '', j.get('nombre') or ''))
        selected = None
        if job_id:
            read_job(job_id)
            selected = next(j for j in data['jobs'] if j['id'] == job_id)
            section = 'detail'
        session.setdefault('teams_csrf', secrets.token_urlsafe(32))
        from src.tenant_brand_map import all_resolved_brands
        switches = [b for b in all_resolved_brands() if b.brand_key in ('astral', 'norkevin')
                    and crm_store.get('tenants', b.internal_tenant_id)] if current_app.config.get('FLOW_TEAMS_LOCAL') else []
        return render_template('teams.html', section=section, selected=selected, year=year, view=view, query=query, switches=switches,
                               action_labels={'member':'Miembro actualizado','assignment':'Cobertura creada','assignment_edit':'Condiciones revisadas',
                                   'assignment_publish':'Cobertura compartida','assignment_status':'Servicio actualizado','assignment_schedule_pending':'Horario pendiente','assignment_acknowledge':'Confirmación manual','response':'Respuesta del miembro','calendar_response':'Respuesta de Google Calendar',
                                   'cost':'Gasto registrado','cost_status':'Importe aprobado o validado','operation':'Revisión interna',
                                   'payment':'Pago registrado','reverse':'Pago revertido','document':'Documento revisado','document_publish':'Documento publicado',
                                   'document_read':'Lectura confirmada','document_withdraw':'Documento retirado','advance':'Fondo entregado',
                                   'settlement':'Fondo liquidado','advance_return':'Fondo devuelto','expense_review':'Reembolso revisado',
                                   'cost_shared':'Gasto distribuido','schedule':'Cuotas planificadas','job_close':'Operación cerrada','job_reopen':'Operación reabierta'},
                               calendar_email=calendar_email(session['tenant_id']), calendar_message=session.pop('teams_calendar_message', ''),
                               csrf=session['teams_csrf'], today=datetime.now(LOCAL_ZONE).date().isoformat(), **data)

    from src.teams_portal import register_portal
    from src.teams_shared import register_shared
    register_shared(blueprint, database, crm_store)
    register_portal(app, blueprint, database, crm_store, read_job)
    from src.teams_enrollment import register_enrollment
    register_enrollment(app, blueprint, database, crm_store)
    app.register_blueprint(blueprint)
