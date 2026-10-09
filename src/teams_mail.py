"""Teams invitation delivery and owner-only copies without usable access credentials."""
import hashlib
import secrets
import re
from datetime import datetime, timedelta

from src import gmail_delivery
from src.teams import LOCAL_ZONE, TeamsError, now


def issue_code(store, tenant, member, actor):
    token = secrets.token_urlsafe(16)
    with store.transaction() as db:
        current = store.get(db, tenant, 'member', member['id'])
        if not current['active'] or current.get('access_version', 1) != member.get('access_version', 1):
            raise TeamsError('El acceso del trabajador cambió. Recarga su ficha.', 409)
        store.save(db, tenant, 'access', dict(id=hashlib.sha256(token.encode()).hexdigest(),
                   member_id=member['id'], access_version=member.get('access_version', 1),
                   expires=(datetime.now(LOCAL_ZONE) + timedelta(hours=24)).isoformat(), used=False))
        store.create(db, tenant, 'audit', action='access_issue', actor=actor,
                     created_at=now(), before=None, after=dict(id=member['id']))
    return token


def safe_mail_body(body):
    # Keep the content for owners, never archive usable bearer credentials.
    body = re.sub(r'https?://[^\s]+/p/[A-Za-z0-9_-]+', '[enlace privado protegido]', body)
    body = re.sub(r'(https?://[^\s]+/teams-portal/login)#(?:access|invite)=[^\s]+',
                  r'\1 [enlace privado protegido]', body)
    return re.sub(r'https?://[^\s]+/teams-portal/calendar-document/[^\s]+',
                  '[enlace privado al documento]', body)


def archive_calendar(store, db, tenant, record):
    identifier = hashlib.sha256((tenant+record['id']+record['digest']).encode()).hexdigest()
    if any(r['id'] == identifier for r in store.records(db, tenant, 'team_mail')):
        return
    event = record['event']
    store.save(db, tenant, 'team_mail', dict(id=identifier, job_id=record['job_id'],
        channel='calendar', status='synced', created_at=now(), sent_at=now(),
        to=', '.join(a['email'] for a in event.get('attendees', [])), subject=event['summary'],
        body=safe_mail_body(event.get('description', ''))))


def send_portal_email(store, tenant, member, origin, actor, *, event=None, calendar_url='', job_id=None):
    from src.teams_calendar import valid_invitation_email
    if not member['active'] or not valid_invitation_email(member.get('email', '')):
        raise TeamsError('Agrega un correo válido en la ficha del trabajador.')
    if not gmail_delivery.is_connected(tenant_id=tenant):
        raise TeamsError('Conecta Gmail para esta marca en Configuración del CRM.')
    invitation = re.search(r'https?://[^\s]+(?:/p/[A-Za-z0-9_-]+|/teams-portal/login#invite=[^\s]+)', (event or {}).get('description', ''))
    token = None if invitation else issue_code(store, tenant, member, actor)
    link = invitation.group(0) if invitation else origin.rstrip('/') + '/p/' + token
    subject = 'Tu acceso personal a Teams'
    lines = [f"Hola, {member['name']}."]
    if event:
        subject = 'Invitación · ' + event['summary']
        lines += [event['summary'], event.get('location', ''), event.get('description', '')]
        if calendar_url:
            lines += ['Revisar invitación en Google Calendar:', calendar_url]
    lines += ['Tu correo: '+member['email'], 'No necesitas contraseña.', 'Abre tu portal personal para revisar tus bodas, condiciones, gastos y documentos:', link,
              'Este enlace es privado. No lo reenvíes.' if invitation else 'Este enlace es privado, se usa una sola vez y caduca en 24 horas. No lo reenvíes.',
              'Si caduca, pide un nuevo acceso al responsable. Acepta tu asignación dentro del portal.']
    body = '\n\n'.join(filter(None, lines))
    with store.transaction() as db:
        mail = store.create(db, tenant, 'team_mail', job_id=job_id, member_id=member['id'],
            channel='gmail', status='sending', created_at=now(), to=member['email'], subject=subject,
            body=safe_mail_body(body))
    try:
        ok, result = gmail_delivery.send_gmail(member['email'], subject, body,
                                              from_name='Flow Teams', tenant_id=tenant)
    except Exception:
        ok, result = False, ''
    with store.transaction() as db:
        mail.update(status='sent' if ok else 'failed', sent_at=now() if ok else None,
                    message_id=result if ok else None)
        store.save(db, tenant, 'team_mail', mail)
    if not ok:
        # A provider/network failure can be ambiguous. Do not expose its response or retry blindly.
        if token is not None:
            with store.transaction() as db:
                access = store.get(db, tenant, 'access', hashlib.sha256(token.encode()).hexdigest())
                access['used'] = True
                store.save(db, tenant, 'access', access)
        raise TeamsError('No se confirmó el correo. Revisa Gmail y vuelve a enviarlo desde la ficha.', 502)
    return result
