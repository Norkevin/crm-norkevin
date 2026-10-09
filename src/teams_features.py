"""Portal, publications and financial extensions for the isolated Teams workspace."""
import base64
from datetime import datetime, timedelta
from uuid import uuid4

from src.teams import LOCAL_ZONE, TeamsError, cents, day, now, text, payment_deadline, assignment_trip, availability_window, TRAVEL_RESPONSES

MAX_FILE_BYTES = 10 * 1024 * 1024

VISIBLE_ASSIGNMENTS = ('pendiente', 'aceptada', 'reconfirmar', 'realizada')


def clean(record):
    if record.get('private_profile'):
        return {k:record[k] for k in ('id','version') if k in record}
    return {k: v for k, v in record.items() if k not in ('file_data', 'token_hash')}


def cost_amount(cost):
    return cost['final'] if cost['final'] is not None else cost['estimate']


def advance_balance(store, db, tenant, advance):
    settled = sum(a['amount'] for s in store.records(db, tenant, 'settlement')
                  if s['advance_id'] == advance['id'] for a in s['allocations'])
    returned = sum(p['amount'] for p in store.records(db, tenant, 'payment')
                   if p.get('advance_id') == advance['id'] and p['sign'] == -1)
    return advance['amount'] - settled - returned


def cancel_pending(store, db, tenant, assignment_id):
    for kind in ('notice', 'task'):
        for record in store.records(db, tenant, kind):
            if record.get('assignment_id') == assignment_id and record['status'] not in ('simulado', 'hecha', 'cancelado'):
                record['status'] = 'cancelado'
                store.save(db, tenant, kind, record)


def notice(store, db, tenant, assignment, purpose, body, document=None):
    revision = document['version'] if document else assignment['terms_version']
    dedup = f"{assignment['id']}:{assignment['terms_version']}:{purpose}:{revision}:{document['id'] if document else ''}"
    previous = next((n for n in store.records(db, tenant, 'notice') if n['dedup'] == dedup), None)
    if previous:
        return previous
    return store.create(db, tenant, 'notice', assignment_id=assignment['id'], member_id=assignment['member_id'],
                        job_id=assignment['job_id'], terms_version=assignment['terms_version'], purpose=purpose,
                        document_id=document['id'] if document else None,
                        document_version=document['version'] if document else None, dedup=dedup,
                        body=body, status='preparado', created_at=now())


def tasks_for(store, db, tenant, assignment):
    settings = next(iter(store.records(db, tenant, 'config')), {})
    start = datetime.fromisoformat(assignment['start'])
    current = datetime.now(LOCAL_ZONE)
    for purpose, due in (
        ('reconfirmar', start - timedelta(days=settings.get('reconfirm_days', 7))),
        ('call_sheet', start - timedelta(days=settings.get('call_sheet_days', 1))),
        ('entrega', datetime.fromisoformat(assignment['end']) + timedelta(days=1))):
        store.create(db, tenant, 'task', assignment_id=assignment['id'], member_id=assignment['member_id'],
                     job_id=assignment['job_id'], terms_version=assignment['terms_version'], purpose=purpose,
                     due=due.isoformat(), status='omitida' if due < current else 'pendiente')


def valid_assignment(store, db, tenant, assignment, job_reader):
    job = job_reader(assignment['job_id'])
    member = store.get(db, tenant, 'member', assignment['member_id'])
    if (not member['active'] or assignment['status'] not in VISIBLE_ASSIGNMENTS
            or job.get('status') in ('Cancelado', 'Archivado') or assignment['job_day'] != job.get('boda_date')):
        raise TeamsError('La cobertura o su destinatario cambió. Revisa antes de continuar.', 409)
    return job, member


def document_visible(store, db, tenant, document, member_id):
    return (document['status'] == 'publicado' and member_id in document['audience_ids']
            and any(a['job_id'] == document['job_id'] and a['member_id'] == member_id
                    and a['status'] in VISIBLE_ASSIGNMENTS for a in store.records(db, tenant, 'assignment')))


def file_fields(data):
    encoded = data.get('file_data', '')
    if not encoded:
        return {}
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise TeamsError('Archivo inválido.')
    name = text(data, 'file_name', maximum=150)
    if '/' in name or '\\' in name or not 0 < len(raw) <= MAX_FILE_BYTES:
        raise TeamsError('Archivo inválido o mayor que 10 MB.')
    extension = name.rsplit('.', 1)[-1].lower()
    types = {'pdf': ('application/pdf', b'%PDF-'), 'png': ('image/png', b'\x89PNG\r\n\x1a\n'),
             'jpg': ('image/jpeg', b'\xff\xd8\xff'), 'jpeg': ('image/jpeg', b'\xff\xd8\xff')}
    if extension == 'txt':
        try:
            raw.decode('utf-8')
        except UnicodeDecodeError:
            raise TeamsError('El archivo TXT debe tener texto UTF-8.')
        mime = 'text/plain'
    elif extension in types and raw.startswith(types[extension][1]):
        mime = types[extension][0]
    else:
        raise TeamsError('Solo PDF, PNG, JPG o TXT válidos. No se permiten archivos ejecutables ni HTML.')
    # ponytail: 10 MB per file in private SQLite on the persistent CRM disk; use object storage when volume exceeds disk capacity.
    return dict(file_data=encoded, file_name=name, file_type=mime)


def travel_dates(data, job, assignment=None):
    departure = day(data.get('departure'))
    return_date = day(data.get('return_date'))
    wedding = (job.get('boda_date') or '')[:10]
    finish = (job.get('end_date') or wedding)[:10]
    if not wedding or not departure <= wedding <= finish <= return_date:
        raise TeamsError('La salida debe ser antes o el día de la boda y el regreso después o el último día del evento.')
    if assignment and not departure <= assignment['start'][:10] <= assignment['end'][:10] <= return_date:
        raise TeamsError('Las fechas de viaje deben incluir toda la cobertura de esta persona.')
    return departure, return_date


def handle_command(store, db, tenant, actor, data, job_reader, member_id=None):
    action = data['action']
    before, warnings = None, []
    if action in ('travel', 'assignment_travel'):
        if action == 'travel':
            job = job_reader(text(data, 'job_id', maximum=200))
            if job.get('status') in ('Cancelado', 'Archivado'):
                raise TeamsError('Esta boda no está disponible para planificar un viaje.')
            record = next((r for r in store.records(db, tenant, 'travel') if r['job_id'] == job['id']),
                          dict(id=uuid4().hex, job_id=job['id'], version=0))
            store.check_version(record, data)
            before = dict(record)
            enabled = data.get('enabled')
            if type(enabled) is not bool:
                raise TeamsError('Indica si esta boda requiere viaje.')
            departure, return_date = '', ''
            if enabled:
                departure, return_date = travel_dates(data, job)
            record.update(enabled=enabled, departure=departure, return_date=return_date,
                          note=text(data, 'note', required=False, maximum=1000), updated_at=now())
            store.save(db, tenant, 'travel', record)
            affected = [a for a in store.records(db, tenant, 'assignment') if a['job_id'] == job['id']
                        and a['status'] not in ('cancelada', 'rechazada', 'realizada')]
        else:
            record = store.get(db, tenant, 'assignment', text(data, 'id'))
            store.check_version(record, data)
            before = dict(record)
            job = job_reader(record['job_id'])
            if record['status'] in ('cancelada', 'rechazada', 'realizada'):
                raise TeamsError('Esta cobertura está cerrada.')
            if not assignment_trip(record, store.records(db, tenant, 'travel')):
                raise TeamsError('Primero activa el viaje de esta boda.')
            if data.get('personal') is True:
                departure, return_date = travel_dates(data, job, record)
                record['travel_override'] = dict(departure=departure, return_date=return_date)
            elif data.get('personal') is False:
                record['travel_override'] = None
            else:
                raise TeamsError('Indica si esta persona viaja en otras fechas.')
            affected = [record]
        for a in affected:
            if action == 'travel':
                old_trip = assignment_trip(a, [before])
                new_trip = assignment_trip(a, [record])
                if new_trip and not new_trip['departure'] <= a['start'][:10] <= a['end'][:10] <= new_trip['return_date']:
                    raise TeamsError('Las fechas de viaje deben incluir todas las coberturas. Revisa salida y regreso.')
                if (old_trip or {}).get('departure') == (new_trip or {}).get('departure') and (old_trip or {}).get('return_date') == (new_trip or {}).get('return_date'):
                    continue
            else:
                new_trip = assignment_trip(a, store.records(db, tenant, 'travel'))
                if not new_trip['departure'] <= a['start'][:10] <= a['end'][:10] <= new_trip['return_date']:
                    raise TeamsError('El viaje debe incluir toda la cobertura.')
            # Any revised itinerary requires a fresh answer, independent of attendance and fees.
            a.update(travel_response='pending', travel_response_note='', travel_answered_at=None)
            if action == 'travel' and not record['enabled']:
                a['travel_override'] = None
            store.save(db, tenant, 'assignment', a)
        warnings.append('Viaje guardado. El equipo debe responder a las fechas vigentes en su portal.')
    elif action == 'travel_response':
        record = store.get(db, tenant, 'assignment', text(data, 'id'))
        if record['member_id'] != member_id:
            raise TeamsError('Solo puedes responder por tu propia cobertura.', 403)
        store.check_version(record, data)
        valid_assignment(store, db, tenant, record, job_reader)
        if record['status'] == 'realizada':
            raise TeamsError('Esta cobertura ya está realizada.', 409)
        trip = assignment_trip(record, store.records(db, tenant, 'travel'))
        if not trip:
            raise TeamsError('Esta boda no tiene un viaje activo.', 409)
        answer = text(data, 'response')
        if answer not in TRAVEL_RESPONSES or answer == 'pending':
            raise TeamsError('Elige tu disponibilidad para este viaje.')
        before = dict(record)
        record.update(travel_response=answer, travel_response_note=text(data, 'note', required=False, maximum=500))
        if answer == 'available':
            start, end = availability_window(record, assignment_trip(record, store.records(db, tenant, 'travel')))
            unavailable = any(r['member_id'] == member_id and r.get('status') != 'retirada'
                              and r['start'] <= (end - timedelta(days=1)).date().isoformat()
                              and r['end'] >= start.date().isoformat() for r in store.records(db, tenant, 'availability'))
            conflicts = store.conflicts(db, tenant, member_id, record['start'], record['end'], record['buffer'], record['id'], candidate=record)
            if unavailable or any(a['status'] in ('aceptada', 'realizada') for a in conflicts):
                raise TeamsError('Estas fechas se cruzan con tu indisponibilidad u otra cobertura confirmada. Revisa con el responsable.', 409)
        record['travel_answered_at'] = now()
        store.save(db, tenant, 'assignment', record)
    elif action in ('assignment_publish', 'assignment_edit'):
        record = store.get(db, tenant, 'assignment', text(data, 'id'))
        store.check_version(record, data)
        before = dict(record)
        job = job_reader(record['job_id'])
        member = store.get(db, tenant, 'member', record['member_id'])
        if not member['active'] or job.get('status') in ('Cancelado', 'Archivado'):
            raise TeamsError('Miembro o evento no disponible.')
        cost = next(c for c in store.records(db, tenant, 'cost') if c.get('assignment_id') == record['id'])
        if action == 'assignment_edit':
            if record['status'] in ('realizada', 'cancelada', 'rechazada') or cost['status'] == 'incurrido':
                raise TeamsError('Una cobertura cerrada conserva sus condiciones. Revisa una compensación por separado.')
            try:
                start = datetime.fromisoformat(data['start']).replace(tzinfo=LOCAL_ZONE)
                end = datetime.fromisoformat(data['end']).replace(tzinfo=LOCAL_ZONE)
                buffer = int(data.get('buffer', 0))
                if start >= end or not 0 <= buffer <= 1440:
                    raise ValueError
            except (ValueError, TypeError, KeyError):
                raise TeamsError('Revisa inicio, fin y margen de traslado.')
            amount = cents(data.get('amount'))
            if amount < store.paid(db, tenant, cost['id']):
                raise TeamsError('El honorario no puede quedar debajo de lo ya pagado.')
            reason = text(data, 'reason', maximum=1000)
            replacement = store.get(db, tenant, 'member', data.get('member_id') or record['member_id'])
            if not replacement['active']:
                raise TeamsError('El trabajador está inactivo.')
            if replacement['id'] != record['member_id'] and (store.paid(db, tenant, cost['id']) or cost.get('schedule_id')):
                raise TeamsError('Esta persona ya tiene pagos o cuotas. Revisa esos movimientos antes de reemplazarla.')
            slot = text(data, 'slot', maximum=100) if 'slot' in data else record['slot']
            if any(a['id'] != record['id'] and a['job_id'] == job['id'] and a['slot'].casefold() == slot.casefold()
                   and a['status'] not in ('cancelada', 'rechazada') for a in store.records(db, tenant, 'assignment')):
                raise TeamsError('Esta cobertura ya está ocupada.', 409)
            if cost.get('schedule_id'):
                plan = store.get(db, tenant, 'schedule', cost['schedule_id'])
                if sum(p['amount'] for p in plan['plan']) != amount:
                    raise TeamsError('El honorario debe coincidir con las cuotas acordadas.')
            store.create(db, tenant, 'terms_history', assignment_id=record['id'], terms=dict(record), reason=reason, actor=actor, created_at=now())
            cancel_pending(store, db, tenant, record['id'])
            if replacement['id'] != record['member_id'] or start.isoformat() != record['start'] or end.isoformat() != record['end']:
                record.update(travel_response='pending', travel_response_note='', travel_answered_at=None)
            record.update(member_id=replacement['id'], slot=slot, start=start.isoformat(), end=end.isoformat(), buffer=buffer, job_day=job.get('boda_date'),
                          role=text(data, 'role', maximum=100) if 'role' in data else record['role'],
                          instructions=text(data, 'instructions', required=False, maximum=3000),
                          status='reconfirmar' if record['status'] in VISIBLE_ASSIGNMENTS else 'borrador',
                          terms_version=record['terms_version'] + 1, accepted_terms=None)
            cost['estimate'] = amount
            cost.update(beneficiary=replacement['id'], beneficiary_name=replacement['name'])
            cost['description'] = f"{record['role']} · {cost['beneficiary_name']}"
            store.save(db, tenant, 'cost', cost)
            store.invalidate(db, tenant, job['id'])
        else:
            if record['status'] != 'borrador' or record['job_day'] != job.get('boda_date'):
                raise TeamsError('Publica un borrador con las condiciones vigentes.', 409)
            if cost['status'] == 'anulado':
                raise TeamsError('No se publica un honorario anulado.')
            record.update(status='pendiente', published_at=now())
            if cost['status'] == 'estimado':
                cost['status'] = 'aprobado'
                store.save(db, tenant, 'cost', cost)
        trip = assignment_trip(record, store.records(db, tenant, 'travel'))
        if trip and not trip['departure'] <= record['start'][:10] <= record['end'][:10] <= trip['return_date']:
            raise TeamsError('El horario de cobertura quedó fuera de las fechas de viaje. Revisa el viaje de esta persona.')
        conflicts = store.conflicts(db, tenant, record['member_id'], record['start'], record['end'], record['buffer'], record['id'], candidate=record)
        if any(a['status'] in ('aceptada', 'realizada') for a in conflicts):
            record['conflict_override'] = text(data, 'conflict_reason', maximum=1000)
            warnings.append('Excepción de solapamiento registrada por administración.')
        else:
            record['conflict_override'] = None
        store.save(db, tenant, 'assignment', record)
        if record['status'] in VISIBLE_ASSIGNMENTS:
            notice(store, db, tenant, record, 'asignacion', 'Revisa tu cobertura y sus condiciones vigentes en el portal.')
            tasks_for(store, db, tenant, record)
    elif action == 'response':
        record = store.get(db, tenant, 'assignment', text(data, 'id'))
        if not member_id or record['member_id'] != member_id:
            raise TeamsError('Solo el miembro puede responder a su cobertura.', 403)
        store.check_version(record, data)
        valid_assignment(store, db, tenant, record, job_reader)
        if record['status'] not in ('pendiente', 'reconfirmar') or data.get('terms_version') != record['terms_version']:
            raise TeamsError('Las condiciones cambiaron. Recarga y revisa la nueva versión.', 409)
        before = dict(record)
        status = text(data, 'status')
        if status not in ('aceptada', 'rechazada'):
            raise TeamsError('Elige aceptar o rechazar.')
        if status == 'aceptada':
            start, end = availability_window(record, assignment_trip(record, store.records(db, tenant, 'travel')))
            unavailable = any(r['member_id'] == member_id and r.get('status') != 'retirada' and r['start'] <= (end - timedelta(microseconds=1)).date().isoformat()
                              and r['end'] >= start.date().isoformat() for r in store.records(db, tenant, 'availability'))
            if unavailable and not record.get('conflict_override'):
                raise TeamsError('Declaraste indisponibilidad en estas fechas. Solicita revisión al responsable.', 409)
            conflicts = store.conflicts(db, tenant, member_id, record['start'], record['end'], record['buffer'], record['id'], candidate=record)
            if any(a['status'] in ('aceptada', 'realizada') for a in conflicts) and not record.get('conflict_override'):
                raise TeamsError('Otra cobertura confirmada se superpone. Solicita revisión al responsable.', 409)
            record.update(accepted_terms=record['terms_version'], accepted_at=now(), accepted_by=actor)
        else:
            cancel_pending(store, db, tenant, record['id'])
            cost = next(c for c in store.records(db, tenant, 'cost') if c.get('assignment_id') == record['id'])
            if not store.paid(db, tenant, cost['id']) and cost['status'] != 'incurrido':
                cost['status'] = 'anulado'
                store.save(db, tenant, 'cost', cost)
            else:
                warnings.append('Hay pagos previos: administración debe revisar el ajuste pendiente.')
            store.invalidate(db, tenant, record['job_id'])
        record['status'] = status
        store.save(db, tenant, 'assignment', record)
    elif action == 'member_revoke':
        record = store.get(db, tenant, 'member', text(data, 'id'))
        store.check_version(record, data)
        before = dict(record)
        record['access_version'] = record.get('access_version', 1) + 1
        store.save(db, tenant, 'member', record)
    elif action == 'document':
        job = job_reader(text(data, 'job_id', maximum=200))
        record = store.get(db, tenant, 'document', text(data, 'id')) if data.get('id') else dict(id=uuid4().hex)
        if data.get('id'):
            store.check_version(record, data)
            if record['job_id'] != job['id']:
                raise TeamsError('No se cambia la boda de un documento.')
            before = clean(record)
            store.create(db, tenant, 'document_history', document_id=record['id'], document=dict(record), actor=actor, created_at=now())
        audience = data.get('audience_ids', [])
        if not isinstance(audience, list) or len(audience) > 100:
            raise TeamsError('Audiencia inválida.')
        assigned = {a['member_id'] for a in store.records(db, tenant, 'assignment') if a['job_id'] == job['id'] and a['status'] != 'cancelada'}
        if any(m not in assigned for m in audience):
            raise TeamsError('La audiencia debe pertenecer al equipo de esta boda.')
        for m in audience:
            store.get(db, tenant, 'member', m)
        record.update(job_id=job['id'], title=text(data, 'title', maximum=150),
                      content=text(data, 'content', required=False, maximum=12000),
                      audience_ids=list(dict.fromkeys(audience)), audience='selected' if audience else 'team',
                      status='borrador', author=actor, kind=text(data, 'kind', maximum=50), updated_at=now())
        record.update(file_fields(data))
        if not record['content'] and not record.get('file_data'):
            raise TeamsError('Escribe contenido o adjunta un documento.')
        store.save(db, tenant, 'document', record)
    elif action in ('document_publish', 'document_withdraw'):
        record = store.get(db, tenant, 'document', text(data, 'id'))
        store.check_version(record, data)
        before = clean(record)
        job_reader(record['job_id'])
        if action == 'document_withdraw':
            record['status'] = 'retirado'
            store.save(db, tenant, 'document', record)
        else:
            if record['status'] != 'borrador':
                raise TeamsError('Solo se publica una versión en borrador.', 409)
            assignments = [a for a in store.records(db, tenant, 'assignment') if a['job_id'] == record['job_id'] and a['status'] in VISIBLE_ASSIGNMENTS]
            audience = record['audience_ids'] if record['audience'] == 'selected' else [a['member_id'] for a in assignments]
            assignments = [a for a in assignments if a['member_id'] in audience]
            if not assignments:
                raise TeamsError('Publica primero una cobertura para el destinatario.')
            for a in assignments:
                valid_assignment(store, db, tenant, a, job_reader)
            record.update(status='publicado', audience_ids=list({a['member_id'] for a in assignments}), published_at=now())
            store.save(db, tenant, 'document', record)
            for a in assignments:
                notice(store, db, tenant, a, 'documento', 'Hay una nueva versión de un documento de tu cobertura.', record)
    elif action == 'document_read':
        doc = store.get(db, tenant, 'document', text(data, 'id'))
        if not member_id or not document_visible(store, db, tenant, doc, member_id):
            raise TeamsError('Documento no disponible para este miembro.', 404)
        store.check_version(doc, data)
        current = [a for a in store.records(db, tenant, 'assignment') if a['job_id'] == doc['job_id']
                   and a['member_id'] == member_id and a['status'] in VISIBLE_ASSIGNMENTS]
        if not current:
            raise TeamsError('Documento no disponible.', 404)
        valid_assignment(store, db, tenant, current[0], job_reader)
        record = next((r for r in store.records(db, tenant, 'receipt') if r['document_id'] == doc['id']
                       and r['document_version'] == doc['version'] and r['member_id'] == member_id), None)
        if not record:
            record = store.create(db, tenant, 'receipt', document_id=doc['id'], document_version=doc['version'],
                                  member_id=member_id, job_id=doc['job_id'], read_at=now(), actor=actor)
    elif action == 'notice_cancel':
        record = store.get(db, tenant, 'notice', text(data, 'id'))
        store.check_version(record, data)
        before = dict(record)
        if record['status'] not in ('preparado','aprobado'):
            raise TeamsError('Solo se cancela un aviso pendiente.')
        record['status'] = 'cancelado'
        store.save(db, tenant, 'notice', record)
    elif action == 'notice_status':
        record = store.get(db, tenant, 'notice', text(data, 'id'))
        store.check_version(record, data)
        before = dict(record)
        a = store.get(db, tenant, 'assignment', record['assignment_id'])
        valid_assignment(store, db, tenant, a, job_reader)
        if a['terms_version'] != record['terms_version']:
            raise TeamsError('Aviso de condiciones antiguas. Se requiere preparar uno vigente.', 409)
        if record['document_id']:
            doc = store.get(db, tenant, 'document', record['document_id'])
            if doc['version'] != record['document_version'] or not document_visible(store, db, tenant, doc, record['member_id']):
                raise TeamsError('El documento o la audiencia cambió.', 409)
        status = text(data, 'status')
        if (record['status'], status) not in (('preparado', 'aprobado'), ('aprobado', 'simulado')):
            raise TeamsError('Primero prepara y aprueba el aviso; solo se simula una vez.', 409)
        record.update(status=status, updated_at=now(), updated_by=actor)
        store.save(db, tenant, 'notice', record)
    elif action == 'workflow_prepare':
        current = datetime.now(LOCAL_ZONE)
        tasks = store.records(db, tenant, 'task')
        count = 0
        for task in tasks:
            if task['status'] not in ('pendiente', 'bloqueada') or datetime.fromisoformat(task['due']) > current:
                continue
            a = store.get(db, tenant, 'assignment', task['assignment_id'])
            try:
                valid_assignment(store, db, tenant, a, job_reader)
            except TeamsError:
                task['status'] = 'cancelado'
            else:
                if a['terms_version'] != task['terms_version']:
                    task['status'] = 'cancelado'
                elif task['purpose'] == 'call_sheet':
                    doc = next((d for d in store.records(db, tenant, 'document') if d['job_id'] == a['job_id']
                                and d['kind'] == 'Call sheet' and document_visible(store, db, tenant, d, a['member_id'])), None)
                    if not doc:
                        task['status'] = 'bloqueada'
                    else:
                        notice(store, db, tenant, a, 'call_sheet', 'Revisa el call sheet vigente antes de tu cobertura.', doc)
                        task['status'] = 'preparada'
                else:
                    notice(store, db, tenant, a, task['purpose'], 'Tienes una tarea pendiente de tu cobertura en el portal.')
                    task['status'] = 'preparada'
            store.save(db, tenant, 'task', task)
            count += 1
        record = dict(id='revision-workflow', prepared=count, reviewed_at=now())
    elif action == 'task_complete':
        record = store.get(db, tenant, 'task', text(data, 'id'))
        store.check_version(record, data)
        before = dict(record)
        a = store.get(db, tenant, 'assignment', record['assignment_id'])
        valid_assignment(store, db, tenant, a, job_reader)
        if member_id and a['member_id'] != member_id:
            raise TeamsError('Tarea no disponible.', 404)
        if record['terms_version'] != a['terms_version'] or record['status'] in ('cancelado', 'omitida'):
            raise TeamsError('Tarea de condiciones anteriores.', 409)
        record.update(status='hecha', completed_at=now(), completed_by=actor)
        store.save(db, tenant, 'task', record)
    elif action == 'expense_request':
        a = store.get(db, tenant, 'assignment', text(data, 'assignment_id'))
        if not member_id or a['member_id'] != member_id:
            raise TeamsError('Cobertura no disponible.', 404)
        valid_assignment(store, db, tenant, a, job_reader)
        amount = cents(data.get('amount'))
        if amount <= 0:
            raise TeamsError('El gasto debe tener un importe positivo.')
        attachment = file_fields(data)
        evidence = text(data,'evidence',required=False,maximum=1000)
        if not attachment and not evidence:
            raise TeamsError('Adjunta un comprobante o escribe una referencia del gasto.')
        record = store.create(db, tenant, 'expense_request', member_id=member_id, job_id=a['job_id'],
                              amount=amount, description=text(data, 'description', maximum=500),
                              evidence=evidence, status='pendiente', created_at=now(), **attachment)
    elif action == 'expense_review':
        record = store.get(db, tenant, 'expense_request', text(data, 'id'))
        store.check_version(record, data)
        before = dict(record)
        if record['status'] != 'pendiente':
            raise TeamsError('Esta solicitud ya fue revisada.', 409)
        record['status'] = text(data, 'status')
        if record['status'] not in ('aprobada', 'rechazada'):
            raise TeamsError('Estado de revisión inválido.')
        job_reader(record['job_id'])
        member = store.get(db, tenant, 'member', record['member_id'])
        if record['status'] == 'aprobada':
            cost = store.create(db, tenant, 'cost', job_id=record['job_id'], beneficiary=member['id'], beneficiary_name=member['name'],
                                category='Reembolso', description=record['description'], budget=record['amount'],
                                estimate=record['amount'], final=record['amount'], status='incurrido', due_date='', currency='GTQ',
                                evidence=record['evidence'], expense_request_id=record['id'])
            record['cost_id'] = cost['id']
            store.invalidate(db, tenant, record['job_id'])
        record['reviewed_at'] = now()
        store.save(db, tenant, 'expense_request', record)
    elif action == 'availability':
        if not member_id:
            raise TeamsError('Acción del miembro.', 403)
        start, end = day(data.get('start')), day(data.get('end'))
        if start > end:
            raise TeamsError('El fin debe ser posterior al inicio.')
        record = store.create(db, tenant, 'availability', member_id=member_id, start=start, end=end,
                              note=text(data, 'note', required=False, maximum=500), created_at=now(), status='vigente')
    elif action == 'availability_remove':
        record = store.get(db, tenant, 'availability', text(data, 'id'))
        if not member_id or record['member_id'] != member_id:
            raise TeamsError('Disponibilidad no disponible.', 404)
        store.check_version(record, data)
        before = dict(record)
        record['status'] = 'retirada'
        store.save(db, tenant, 'availability', record)
    elif action == 'advance':
        job = job_reader(text(data, 'job_id', maximum=200))
        member = store.get(db, tenant, 'member', text(data, 'member_id'))
        amount = cents(data.get('amount'))
        if not member['active'] or amount <= 0 or data.get('currency', 'GTQ') != 'GTQ':
            raise TeamsError('Revisa miembro, moneda e importe.')
        record = store.create(db, tenant, 'advance', job_id=job['id'], member_id=member['id'], amount=amount,
                              effective_date=day(data.get('effective_date')), reference=text(data, 'reference'), created_at=now())
        store.create(db, tenant, 'payment', advance_id=record['id'], job_id=job['id'], amount=amount, sign=1,
                     allocations=[], beneficiary=member['id'], beneficiary_name=member['name'], currency='GTQ',
                     effective_date=record['effective_date'], reference=record['reference'], method='Fondo para gastos',
                     created_at=now(), actor=actor, reversal_of=None)
    elif action == 'settlement':
        advance = store.get(db, tenant, 'advance', text(data, 'advance_id'))
        job_reader(advance['job_id'])
        allocations = data.get('allocations')
        if not isinstance(allocations, list) or not allocations:
            raise TeamsError('Selecciona gastos aprobados para liquidar.')
        normalized, seen = [], set()
        for a in allocations:
            if not isinstance(a, dict):
                raise TeamsError('Distribución inválida.')
            cost = store.get(db, tenant, 'cost', text(a, 'cost_id'))
            amount = cents(a.get('amount'))
            if (cost['job_id'] != advance['job_id'] or cost.get('assignment_id') or cost['currency'] != 'GTQ'
                    or cost['status'] != 'incurrido' or cost['id'] in seen or amount <= 0
                    or amount > cost_amount(cost) - store.paid(db, tenant, cost['id'])):
                raise TeamsError('Solo gastos incurridos de la misma boda, sin honorarios ni sobrepago.')
            seen.add(cost['id'])
            normalized.append(dict(cost_id=cost['id'], job_id=cost['job_id'], amount=amount))
        if sum(a['amount'] for a in normalized) > advance_balance(store, db, tenant, advance):
            raise TeamsError('La liquidación supera el fondo pendiente.', 409)
        record = store.create(db, tenant, 'settlement', advance_id=advance['id'], allocations=normalized,
                              job_id=advance['job_id'], member_id=advance['member_id'], evidence=text(data, 'evidence'), created_at=now())
    elif action == 'advance_return':
        advance = store.get(db, tenant, 'advance', text(data, 'advance_id'))
        job_reader(advance['job_id'])
        amount = cents(data.get('amount'))
        if amount <= 0 or amount > advance_balance(store, db, tenant, advance):
            raise TeamsError('La devolución supera el saldo del fondo.', 409)
        member = store.get(db, tenant, 'member', advance['member_id'])
        record = store.create(db, tenant, 'payment', advance_id=advance['id'], job_id=advance['job_id'], amount=amount, sign=-1,
                              allocations=[], beneficiary=member['id'], beneficiary_name=member['name'], currency='GTQ',
                              effective_date=day(data.get('effective_date')), reference=text(data, 'reference'), method='Devolución de fondo',
                              created_at=now(), actor=actor, reversal_of=None)
    elif action == 'cost_shared':
        amount = cents(data.get('amount'))
        distribution = data.get('distribution')
        if not isinstance(distribution, list) or not distribution or len(distribution) > 100 or data.get('currency', 'GTQ') != 'GTQ':
            raise TeamsError('Distribución inválida; misma marca y GTQ.')
        parts, seen = [], set()
        for part in distribution:
            if not isinstance(part, dict):
                raise TeamsError('Distribución inválida.')
            job = job_reader(text(part, 'job_id', maximum=200))
            portion = cents(part.get('amount'))
            if job['id'] in seen or portion <= 0:
                raise TeamsError('No repitas una boda ni uses importes vacíos.')
            seen.add(job['id'])
            parts.append(dict(job_id=job['id'], amount=portion))
        if sum(p['amount'] for p in parts) != amount:
            raise TeamsError('Las partes deben sumar exactamente el gasto común.')
        name = text(data, 'beneficiary_name', maximum=150)
        record = store.create(db, tenant, 'shared_cost', amount=amount, distribution=parts,
                              description=text(data, 'description'), evidence=text(data, 'evidence'), created_at=now())
        for p in parts:
            store.create(db, tenant, 'cost', job_id=p['job_id'], shared_id=record['id'], category='Gasto compartido',
                         description=record['description'], beneficiary='supplier:' + name.casefold(), beneficiary_name=name,
                         budget=p['amount'], estimate=p['amount'], final=p['amount'], status='incurrido', due_date='', currency='GTQ')
            store.invalidate(db, tenant, p['job_id'])
    elif action == 'schedule':
        cost = store.get(db, tenant, 'cost', text(data, 'cost_id'))
        store.check_version(cost, data)
        job = job_reader(cost['job_id'])
        if cost['status'] not in ('aprobado', 'incurrido'):
            raise TeamsError('Aprueba el costo antes de definir cuotas.')
        plan = []
        for line in text(data, 'plan', maximum=3000).splitlines():
            parts = line.split()
            if len(parts) != 2:
                raise TeamsError('Escribe una cuota por línea: fecha AAAA-MM-DD e importe.')
            amount = cents(parts[1])
            if amount <= 0:
                raise TeamsError('Las cuotas deben ser positivas.')
            plan.append(dict(due_date=day(parts[0]), amount=amount))
        if cost.get('assignment_id') and payment_deadline(job) and any(p['due_date'] > payment_deadline(job) for p in plan):
            raise TeamsError('Los honorarios deben pagarse como máximo 30 días después de la boda.')
        if sum(p['amount'] for p in plan) != cost_amount(cost):
            raise TeamsError('Las cuotas deben sumar exactamente la obligación aprobada.')
        record = store.create(db, tenant, 'schedule', cost_id=cost['id'], job_id=cost['job_id'],
                              plan=sorted(plan, key=lambda p: p['due_date']), created_at=now())
        cost['schedule_id'] = record['id']
        store.save(db, tenant, 'cost', cost)
    elif action in ('job_close', 'job_reopen'):
        job = job_reader(text(data, 'job_id', maximum=200))
        op = next((o for o in store.records(db, tenant, 'operation') if o['job_id'] == job['id']), None)
        if not op:
            raise TeamsError('Revisa primero la operación y sus costos.')
        store.check_version(op, data)
        before = dict(op)
        if action == 'job_reopen':
            if not op.get('closed'):
                raise TeamsError('La operación no está cerrada.')
            op.update(closed=False, reopen_reason=text(data, 'reason', maximum=1000))
            record = store.save(db, tenant, 'operation', op)
        else:
            costs = [c for c in store.records(db, tenant, 'cost') if c['job_id'] == job['id']]
            assignments = [a for a in store.records(db, tenant, 'assignment') if a['job_id'] == job['id']]
            advances = [a for a in store.records(db, tenant, 'advance') if a['job_id'] == job['id']]
            income = job.get('reference_income_cents', cents(job.get('price_total', '0'), imported=True))
            if (op.get('closed') or not op['reviewed'] or not income or job.get('status') in ('Cancelado', 'Archivado')
                    or any(c['status'] not in ('incurrido', 'anulado') for c in costs)
                    or any(a['status'] not in ('realizada', 'cancelada', 'rechazada') for a in assignments)
                    or any(r['job_id'] == job['id'] and r['status'] == 'pendiente' for r in store.records(db, tenant, 'expense_request'))
                    or any(advance_balance(store, db, tenant, a) for a in advances)):
                raise TeamsError('Para cerrar: servicios validados, costos finales revisados, ingreso conocido y fondos liquidados.')
            total = sum(cost_amount(c) for c in costs if c['status'] != 'anulado')
            record = store.create(db, tenant, 'report', job_id=job['id'], income=income, cost_total=total,
                                  margin=income-total, cost_versions={c['id']: c['version'] for c in costs},
                                  created_at=now(), actor=actor)
            op.update(closed=True, report_id=record['id'])
            store.save(db, tenant, 'operation', op)
    elif action == 'member_private':
        from src.teams_directory import DIRECTORY, PRIVATE_FIELDS
        member = store.get(db, tenant, 'member', text(data,'member_id'))
        if member.get('directory_id'):
            record = store.get(db,DIRECTORY,'person_private',member['directory_id'])
            store.check_version(record,data)
            before = dict(record)
        else:
            if data.get('version') != 0:
                raise TeamsError('Recarga la ficha privada.',409)
            record = dict(id=uuid4().hex,private_profile=True)
            member['directory_id'] = record['id']
            store.save(db,tenant,'member',member)
        record.update({k:text(data,k,required=False,maximum=1000 if k == 'notes' else 150) for k in PRIVATE_FIELDS})
        record['updated_at'] = now()
        store.save(db,DIRECTORY,'person_private',record)
    elif action == 'config':
        configs = store.records(db, tenant, 'config')
        record = configs[0] if configs else dict(id=uuid4().hex)
        if configs:
            store.check_version(record, data)
            before = dict(record)
        try:
            reconfirm = int(data.get('reconfirm_days', 7))
            call_sheet = int(data.get('call_sheet_days', 1))
            if not 1 <= reconfirm <= 60 or not 1 <= call_sheet <= 30:
                raise ValueError
        except (ValueError, TypeError):
            raise TeamsError('Los recordatorios requieren días positivos dentro del rango permitido.')
        roles = list(dict.fromkeys(r.strip() for r in text(data, 'roles', maximum=3000).splitlines() if r.strip()))
        if not roles or any(len(r) > 100 for r in roles):
            raise TeamsError('Añade al menos un rol, de máximo 100 caracteres por línea.')
        record.update(reconfirm_days=reconfirm, call_sheet_days=call_sheet,
                      roles='\n'.join(roles), categories=text(data, 'categories', maximum=3000),
                      communication_mode='local', updated_at=now())
        store.save(db, tenant, 'config', record)
    else:
        raise TeamsError('Función no disponible en la propuesta local.')
    return record, before, warnings
