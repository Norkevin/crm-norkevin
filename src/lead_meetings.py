"""One-off client meetings, kept in the existing tenant-scoped calendar."""
from datetime import datetime
from hashlib import sha256
import json
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

from flask import abort, jsonify, request


def meeting_notice_key(meeting):
    if meeting.get('version', 1) == 1:
        return f"leadmeeting:{meeting['id']}:invite"
    return f"leadmeeting:{meeting['id']}:{meeting['version']}:{meeting['notice_kind']}"


def meeting_notice_error(store, mail):
    if mail.get('source') != 'manual:lead-meeting':
        return None
    parts = (mail.get('idempotency_key') or '').split(':')
    meeting = store.get('calendar', parts[1]) if len(parts) >= 3 and parts[0] == 'leadmeeting' else None
    if (not meeting or not meeting.get('meeting_kind') or meeting.get('tenant_id') != mail.get('tenant_id')
            or meeting.get('lead_id') != mail.get('lead_id')
            or meeting_notice_key(meeting) != mail.get('idempotency_key')):
        return 'La reunión cambió o fue cancelada. Usa el aviso actual desde el lead.'
    return None


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
        meeting.setdefault('version', 1)
        meeting.setdefault('notice_kind', 'invite')
        notice = notices.get(meeting_notice_key(meeting), {})
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
    def context(lead_id, meeting_id=None):
        tenant = store.current_tenant_id()
        lead = get_lead(lead_id)
        if not tenant or not lead or lead.get('tenant_id') != tenant:
            abort(404)
        meeting = store.get('calendar', meeting_id) if meeting_id else None
        if meeting_id and (not meeting or meeting.get('lead_id') != lead_id or not meeting.get('meeting_kind')):
            abort(404)
        return tenant, lead, meeting

    def notice(meeting, lead):
        from src.mail_tracker import get_tracker
        kind = meeting.get('notice_kind', 'invite')
        day_label = datetime.strptime(meeting['date'], '%Y-%m-%d').strftime('%d/%m/%Y')
        when = f"{day_label}, de {meeting['start_time']} a {meeting['end_time']} (hora de Guatemala)"
        body = f"Hola {lead.get('nombre') or ''},\n\n"
        if kind == 'cancel':
            subject = 'Reunión cancelada · ' + (lead.get('nombre') or 'Cliente')
            body += f"La reunión {meeting['meeting_kind']} prevista para el {when} ha sido cancelada.\n"
        else:
            subject = ('Nueva fecha de reunión · ' + (lead.get('nombre') or 'Cliente')) if kind == 'update' else meeting['title']
            if kind == 'update':
                old = meeting['history'][-1]
                old_day = datetime.strptime(old['date'], '%Y-%m-%d').strftime('%d/%m/%Y')
                body += f"Actualizamos los datos de nuestra reunión.\nAntes: {old_day}, {old['start_time']}–{old['end_time']} (Guatemala).\n\n"
            else:
                body += f"Te compartimos los datos de nuestra reunión {meeting['meeting_kind']}.\n\n"
            body += f"Fecha: {day_label}\nHorario: {meeting['start_time']} a {meeting['end_time']} (hora de Guatemala)\n\n"
            body += ('Entrar a la reunión\n' + meeting['meeting_url'] if meeting['meeting_kind'] == 'virtual'
                     else 'Lugar: ' + meeting['location'])
            if meeting['notes']:
                body += '\n\n' + meeting['notes']
        body += '\n\n' + brand_name(meeting['tenant_id'])
        try:
            mail = get_tracker().queue_email(
                to_email=meeting['recipient'], subject=subject, body=body,
                lead_id=lead['id'], client_id=lead.get('client_id') or None, tenant_id=meeting['tenant_id'],
                source='manual:lead-meeting', idempotency_key=meeting_notice_key(meeting))
            status = mail.get('status') or 'pending'
        except Exception:
            app.logger.exception('La reunión se guardó, pero no se pudo preparar su aviso')
            status = 'unprepared'
        return jsonify(ok=True, meeting_id=meeting['id'], version=meeting.get('version', 1), delivery_status=status)

    @app.post('/api/leads/<lead_id>/meetings')
    def create_lead_meeting(lead_id):
        tenant, lead, _ = context(lead_id)
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
            return jsonify(ok=False, error=str(error)), 400
        meeting_id = 'meeting-' + sha256(f'{tenant}:{lead_id}:{request_id}'.encode()).hexdigest()[:32]
        with store.locked_tables('calendar'):
            meeting = store.get('calendar', meeting_id)
            if meeting:
                if (meeting.get('version', 1) != 1 or meeting.get('status') == 'cancelled'
                        or any(meeting.get(key) != value for key, value in fields.items()) or meeting['recipient'] != recipient):
                    return jsonify(ok=False, error='Esta reunión ya se guardó con otros datos. Recarga la página.'), 409
            else:
                meeting = dict(fields, id=meeting_id, tenant_id=tenant, lead_id=lead_id, version=1,
                               notice_kind='invite', type='event', title=f"Reunión {fields['meeting_kind']} · {lead.get('nombre') or 'Cliente'}",
                               recipient=recipient, created=datetime.now(ZoneInfo('America/Guatemala')).isoformat(),
                               url=f'/leads/{lead_id}#lead-meetings')
                store.upsert('calendar', meeting)
        return notice(meeting, lead)

    @app.post('/api/leads/<lead_id>/meetings/<meeting_id>/invite')
    def prepare_lead_meeting_notice(lead_id, meeting_id):
        _, lead, meeting = context(lead_id, meeting_id)
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict) or (data.get('version') is not None and data['version'] != meeting.get('version', 1)):
            return jsonify(ok=False, error='La reunión cambió. Recarga la página antes de preparar el aviso.'), 409
        return notice(meeting, lead)

    @app.patch('/api/leads/<lead_id>/meetings/<meeting_id>')
    @app.post('/api/leads/<lead_id>/meetings/<meeting_id>/cancel')
    def change_lead_meeting(lead_id, meeting_id):
        from src.mail_tracker import get_tracker
        _, lead, _ = context(lead_id, meeting_id)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(ok=False, error='Envía los datos de la reunión.'), 400
        kind = 'cancel' if request.method == 'POST' else 'update'
        try:
            request_id = str(UUID(data.get('request_id', '')))
        except (ValueError, TypeError, AttributeError):
            return jsonify(ok=False, error='Vuelve a abrir el formulario e intenta de nuevo.'), 400
        try:
            if type(data.get('version')) is not int or data['version'] < 1:
                raise ValueError('La reunión cambió. Recarga la página e intenta de nuevo.')
            fields = _meeting_data(data) if kind == 'update' else {}
        except (ValueError, TypeError, AttributeError) as error:
            return jsonify(ok=False, error=str(error) if isinstance(error, ValueError) and str(error)
                           else 'Vuelve a abrir el formulario e intenta de nuevo.'), 400
        signature = sha256(json.dumps(dict(action=kind, fields=fields), sort_keys=True).encode()).hexdigest()
        # Match MailTracker's lock order. Release all file locks before provider I/O.
        with store.locked_tables('pending_emails', 'mail_log'):
            with store.locked_tables('calendar'):
                _, _, meeting = context(lead_id, meeting_id)
                if meeting.get('last_request_id') == request_id:
                    if meeting.get('last_request_signature') != signature:
                        return jsonify(ok=False, error='La solicitud ya se guardó con otros datos. Recarga la página.'), 409
                else:
                    if meeting.get('status') == 'cancelled' or meeting.get('version', 1) != data['version']:
                        return jsonify(ok=False, error='La reunión cambió o está cancelada. Recarga la página.'), 409
                    mails = [m for m in store.list('pending_emails')
                             if (m.get('idempotency_key') or '').startswith(f'leadmeeting:{meeting_id}:')]
                    if any(m.get('status') == 'sending' for m in mails):
                        return jsonify(ok=False, error='Se está enviando un aviso de esta reunión. Espera unos segundos e intenta de nuevo.'), 409
                    if kind == 'update' and all(meeting.get(key) == value for key, value in fields.items()):
                        return jsonify(ok=False, error='Cambia la fecha, el horario u otro dato antes de guardar.'), 400
                    prior = {key: meeting.get(key) for key in ('date', 'start_time', 'end_time', 'meeting_kind',
                             'meeting_url', 'location', 'notes', 'recipient')}
                    prior.update(version=meeting.get('version', 1), notice_kind=meeting.get('notice_kind', 'invite'))
                    now = datetime.now(ZoneInfo('America/Guatemala')).isoformat()
                    meeting.setdefault('history', []).append(dict(prior, changed_at=now))
                    meeting.update(fields, version=meeting.get('version', 1) + 1, notice_kind=kind,
                                   updated_at=now, last_request_id=request_id, last_request_signature=signature)
                    if kind == 'cancel':
                        meeting.update(status='cancelled', released_at=now, cancelled_at=now)
                    else:
                        meeting['title'] = f"Reunión {fields['meeting_kind']} · {lead.get('nombre') or 'Cliente'}"
                    store.upsert('calendar', meeting)
                    for mail in mails:
                        if mail.get('status') in ('pending', 'blocked', 'failed'):
                            get_tracker().discard_pending(mail['id'], actor='sistema:reunion-actualizada')
        return notice(meeting, lead)
