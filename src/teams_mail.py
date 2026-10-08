"""Explicit Teams invitation emails; access codes are stored only as hashes."""
import hashlib
import secrets
from datetime import datetime, timedelta

from src import gmail_delivery
from src.teams import LOCAL_ZONE, TeamsError, now


def issue_code(store, tenant, member, actor):
    token = secrets.token_urlsafe(32)
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


def send_portal_email(store, tenant, member, origin, actor, *, event=None, calendar_url=''):
    from src.teams_calendar import valid_invitation_email
    if not member['active'] or not valid_invitation_email(member.get('email', '')):
        raise TeamsError('Agrega un correo válido en la ficha del trabajador.')
    if not gmail_delivery.is_connected(tenant_id=tenant):
        raise TeamsError('Conecta Gmail para esta marca en Configuración del CRM.')
    token = issue_code(store, tenant, member, actor)
    link = origin.rstrip('/') + '/teams-portal/login#access=' + token
    subject = 'Tu acceso personal a Teams'
    lines = [f"Hola, {member['name']}."]
    if event:
        subject = 'Invitación · ' + event['summary']
        lines += [event['summary'], event.get('location', ''), event.get('description', '')]
        if calendar_url:
            lines += ['Revisar invitación en Google Calendar:', calendar_url]
    lines += ['Abre tu portal personal para revisar tus bodas, condiciones, gastos y documentos:', link,
              'Este enlace es privado, se usa una sola vez y caduca en 24 horas. No lo reenvíes.',
              'Si caduca, pide un nuevo acceso al responsable. Acepta tu asignación dentro del portal.']
    try:
        ok, result = gmail_delivery.send_gmail(member['email'], subject, '\n\n'.join(filter(None, lines)),
                                              from_name='Flow Teams', tenant_id=tenant)
    except Exception:
        ok, result = False, ''
    if not ok:
        # A provider/network failure can be ambiguous. Do not expose its response or retry blindly.
        with store.transaction() as db:
            access = store.get(db, tenant, 'access', hashlib.sha256(token.encode()).hexdigest())
            access['used'] = True
            store.save(db, tenant, 'access', access)
        raise TeamsError('No se confirmó el correo. Revisa Gmail y vuelve a enviarlo desde la ficha.', 502)
    return result
