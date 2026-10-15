"""One-off client meetings, kept in the existing tenant-scoped calendar."""
from datetime import datetime
from hashlib import sha256
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

from flask import abort, jsonify, request


def meetings_for_lead(store, lead_id):
    if not lead_id:
        return []
    notices = {m.get('idempotency_key'): m for m in store.list('mail_log')}
    notices.update({m.get('idempotency_key'): m for m in store.list('pending_emails')})
    meetings = []
    for row in store.list('calendar'):
        if row.get('lead_id') != lead_id or not row.get('meeting_kind'):
            continue
        meeting = dict(row)
        notice = notices.get(f"leadmeeting:{row['id']}:invite", {})
        meeting['delivery_status'] = notice.get('status') or 'unprepared'
        meetings.append(meeting)
    return sorted(meetings, key=lambda m: (m['date'], m['start_time']), reverse=True)


def _text(data, key, maximum):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError('Revisa el campo ' + key + '.')
    return value.strip()


def _meeting_data(data):
    kind = data.get('meeting_kind')
    if kind not in ('virtual', 'presencial'):
        raise ValueError('Selecciona reunión virtual o presencial.')
    day = _text(data, 'date', 10)
    start, end = _text(data, 'start_time', 5), _text(data, 'end_time', 5)
    try:
        begins = datetime.strptime(day + ' ' + start, '%Y-%m-%d %H:%M')
        finishes = datetime.strptime(day + ' ' + end, '%Y-%m-%d %H:%M')
        if begins.year > 9998 or begins.strftime('%Y-%m-%d %H:%M') != day + ' ' + start:
            raise ValueError()
        if finishes.strftime('%H:%M') != end or finishes <= begins:
            raise ValueError()
    except ValueError:
        raise ValueError('Indica una fecha válida y una hora de fin posterior al inicio.') from None
    if begins.replace(tzinfo=ZoneInfo('America/Guatemala')) <= datetime.now(ZoneInfo('America/Guatemala')):
        raise ValueError('La reunión debe empezar en una fecha y hora futuras de Guatemala.')
    link = _text(data, 'meeting_url', 2000) if kind == 'virtual' else ''
    location = _text(data, 'location', 500) if kind == 'presencial' else ''
    if kind == 'virtual':
        try:
            parsed = urlsplit(link)
            valid = (parsed.scheme == 'https' and parsed.hostname and (parsed.port is None or parsed.port > 0) and not parsed.username
                     and not parsed.password and not any(c.isspace() or ord(c) < 32 for c in link))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('Pega el enlace completo de la reunión, empezando con https://.')
    elif not location:
        raise ValueError('Escribe el lugar de la reunión presencial.')
    return dict(meeting_kind=kind, date=day, end_date=day, start_time=start, end_time=end,
                meeting_url=link, location=location, notes=_text(data, 'notes', 3000))


def register_lead_meetings(app, store, get_lead, brand_name):
    @app.post('/api/leads/<lead_id>/meetings')
    @app.post('/api/leads/<lead_id>/meetings/<meeting_id>/invite')
    def create_lead_meeting(lead_id, meeting_id=None):
        from src.mail_tracker import get_tracker
        tenant = store.current_tenant_id()
        lead = get_lead(lead_id)
        if not tenant or not lead or lead.get('tenant_id') != tenant:
            abort(404)
        if meeting_id:
            meeting = store.get('calendar', meeting_id)
            if not meeting or meeting.get('lead_id') != lead_id or not meeting.get('meeting_kind'):
                abort(404)
        else:
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify(ok=False, error='Envía los datos de la reunión.'), 400
            try:
                request_id = str(UUID(data.get('request_id', '')))
            except (ValueError, TypeError, AttributeError):
                return jsonify(ok=False, error='Vuelve a abrir el formulario e intenta de nuevo.'), 400
            try:
                fields = _meeting_data(data)
                recipient = (lead.get('email') or '').strip()
                if not recipient:
                    raise ValueError('Agrega primero el correo del cliente en Editar datos.')
            except (ValueError, TypeError, AttributeError) as error:
                return jsonify(ok=False, error=str(error) if isinstance(error, ValueError) and str(error)
                               else 'Vuelve a abrir el formulario e intenta de nuevo.'), 400
            meeting_id = 'meeting-' + sha256(f'{tenant}:{lead_id}:{request_id}'.encode()).hexdigest()[:32]
            with store.locked_tables('calendar'):
                meeting = store.get('calendar', meeting_id)
                if meeting:
                    if any(meeting.get(key) != value for key, value in fields.items()) or meeting['recipient'] != recipient:
                        return jsonify(ok=False, error='Esta reunión ya se guardó con otros datos. Recarga la página.'), 409
                else:
                    meeting = dict(fields, id=meeting_id, tenant_id=tenant, lead_id=lead_id,
                                   type='event', title=f"Reunión {fields['meeting_kind']} · {lead.get('nombre') or 'Cliente'}",
                                   recipient=recipient, created=datetime.now(ZoneInfo('America/Guatemala')).isoformat(),
                                   url=f'/leads/{lead_id}#lead-meetings')
                    store.upsert('calendar', meeting)
        # Persist before contacting the provider. A failed invitation never loses the meeting.
        day_label = datetime.strptime(meeting['date'], '%Y-%m-%d').strftime('%d/%m/%Y')
        body = (f"Hola {lead.get('nombre') or ''},\n\nTe compartimos los datos de nuestra reunión {meeting['meeting_kind']}.\n\n"
                f"Fecha: {day_label}\nHorario: {meeting['start_time']} a {meeting['end_time']} (hora de Guatemala)\n\n")
        body += ('Entrar a la reunión\n' + meeting['meeting_url'] if meeting['meeting_kind'] == 'virtual'
                 else 'Lugar: ' + meeting['location'])
        if meeting['notes']:
            body += '\n\n' + meeting['notes']
        body += '\n\n' + brand_name(tenant)
        try:
            mail = get_tracker().queue_email(
                to_email=meeting['recipient'], subject=meeting['title'], body=body,
                lead_id=lead_id, client_id=lead.get('client_id') or None, tenant_id=tenant,
                source='manual:lead-meeting', idempotency_key=f'leadmeeting:{meeting_id}:invite')
            status = mail.get('status') or 'pending'
        except Exception:
            app.logger.exception('La reunión se guardó, pero no se pudo preparar su invitación')
            status = 'unprepared'
        return jsonify(ok=True, meeting_id=meeting_id, delivery_status=status)
