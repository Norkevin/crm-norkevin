"""Google Calendar connection, separate from Gmail and isolated by brand."""
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from src import gmail_delivery
from src.tenant_brand_map import resolve_brand, UnresolvedBrandError

SCOPE = 'https://www.googleapis.com/auth/calendar.events'
BASE = 'https://www.googleapis.com/calendar/v3/calendars/primary/events'


def delivery_error(exception):
    """Only allowlisted diagnostics can reach the owner or logs."""
    if isinstance(exception, HTTPError):
        reasons=set()
        project=''
        try:
            payload=json.loads(exception.read(65536)).get('error',{})
            if isinstance(payload,str):reasons.add(payload)
            else:
                reasons.update(e.get('reason') for e in payload.get('errors',[]))
                reasons.update(e.get('reason') for e in payload.get('details',[]))
                for detail in payload.get('details',[]):
                    consumer=str(detail.get('metadata',{}).get('consumer',''))
                    if consumer.startswith('projects/') and consumer[9:].isascii() and consumer[9:].isdigit():
                        project=' '+consumer[9:]
        except (ValueError, TypeError, AttributeError, OSError):pass
        if reasons & {'accessNotConfigured','SERVICE_DISABLED'}:
            return 'api_disabled','Google Calendar API no está activada para Flow. Actívala en el proyecto de Google'+project+' y vuelve a enviar.'
        if exception.code==401 or 'invalid_grant' in reasons:
            return 'authorization','Google rechazó la autorización. Vuelve a conectar Calendar en Configuración y reintenta.'
        if exception.code==429 or reasons & {'rateLimitExceeded','userRateLimitExceeded','quotaExceeded'}:
            return 'rate_limit','Google alcanzó su límite temporal. Conservamos el envío y lo reintentaremos en 15 minutos.'
        if exception.code==403:
            return 'permission','Google no permite crear esta invitación. Vuelve a conectar Calendar con la cuenta de esta marca y concede el permiso de Calendar.'
        if exception.code==400:
            return 'invalid_event','Google rechazó los datos de la invitación. Revisa el correo, la fecha y el horario antes de reintentar.'
    return 'unconfirmed','No se pudo confirmar el envío con Google. Conservamos la invitación para reintentar sin duplicarla.'


def token_path(tenant):
    resolve_brand(tenant)
    root=Path(os.environ.get('CRM_DATA_DIR') or Path(__file__).resolve().parents[1]/'data')
    return root / ('google_calendar_token_'+tenant+'.json')


def load_token(tenant):
    try:return json.loads(token_path(tenant).read_text())
    except (OSError, ValueError, UnresolvedBrandError):return None


def save_token(tenant,token):
    path=token_path(tenant);path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as output:
        json.dump(token,output);temporary=output.name
    os.chmod(temporary,0o600);os.replace(temporary,path)


def connected_email(tenant):
    token=load_token(tenant) or {}
    return token.get('email','') if token.get('refresh_token') else ''


def authorization_url(redirect_uri,state,tenant):
    return gmail_delivery.AUTH_URL+'?'+urlencode(dict(client_id=os.environ.get('GOOGLE_CLIENT_ID',''),
        redirect_uri=redirect_uri,response_type='code',scope=SCOPE+' openid email',access_type='offline',
        prompt='select_account consent',state=state,login_hint=resolve_brand(tenant).sender_email))


def exchange_code(tenant,code,redirect_uri):
    payload=gmail_delivery._post_form(gmail_delivery.TOKEN_URL,dict(code=code,
        client_id=os.environ.get('GOOGLE_CLIENT_ID',''),client_secret=os.environ.get('GOOGLE_CLIENT_SECRET',''),
        redirect_uri=redirect_uri,grant_type='authorization_code'))
    email=gmail_delivery._fetch_email(payload.get('access_token',''))
    if email.casefold()!=resolve_brand(tenant).sender_email.casefold():
        raise ValueError('Conecta la cuenta de Google de esta marca.')
    if SCOPE not in payload.get('scope','').split() or not payload.get('refresh_token'):
        raise ValueError('Google no concedió acceso continuo a Calendar. Vuelve a conectar.')
    save_token(tenant,dict(access_token=payload['access_token'],refresh_token=payload['refresh_token'],
        expires_at=time.time()+int(payload.get('expires_in',3600))-60,email=email))
    return email


class CalendarClient:
    def __init__(self,tenant):self.tenant=tenant

    def request(self,method,event_id='',body=None,notify=False,if_match=None):
        token=load_token(self.tenant)
        if not token or not token.get('refresh_token'):raise ValueError('Conecta Google Calendar para esta marca.')
        if token.get('expires_at',0)<=time.time():
            payload=gmail_delivery._post_form(gmail_delivery.TOKEN_URL,dict(refresh_token=token['refresh_token'],
                client_id=os.environ.get('GOOGLE_CLIENT_ID',''),client_secret=os.environ.get('GOOGLE_CLIENT_SECRET',''),
                grant_type='refresh_token'))
            token.update(access_token=payload['access_token'],expires_at=time.time()+int(payload.get('expires_in',3600))-60)
            save_token(self.tenant,token)
        url=BASE+('/'+quote(event_id,safe='') if event_id else '')
        if notify:url+='?sendUpdates=all'
        headers={'Authorization':'Bearer '+token['access_token'],'Content-Type':'application/json'}
        if if_match is not None:headers['If-Match']=if_match
        request=Request(url,data=json.dumps(body).encode() if body is not None else None,
            headers=headers,method=method)
        with urlopen(request,timeout=15) as response:return json.loads(response.read() or '{}')

    def response(self, event_id, identity):
        event = self.request('GET', event_id)
        if event.get('extendedProperties', {}).get('private', {}).get('flow_identity') != identity:
            raise ValueError('El evento no pertenece a esta cobertura.')
        return event

    def sync(self,event_id,event,digest,identity,invite_unanswered_only=False):
        try:previous=self.request('GET',event_id)
        except HTTPError as error:
            if error.code not in (404,410):raise
            if invite_unanswered_only and error.code==410:
                return dict(status='cancelled',_flow_invite_skipped='cancelled')
            previous=None
        if event is None and previous and previous.get('status')=='cancelled':
            return dict(previous,_flow_invite_skipped='cancelled') if invite_unanswered_only else previous
        if previous and previous.get('extendedProperties',{}).get('private',{}).get('flow_identity')!=identity:
            raise ValueError('El evento de Calendar no pertenece a esta cobertura de Flow.')
        if event is None:
            if invite_unanswered_only:return dict(previous or {},_flow_invite_skipped='inactive')
            if previous and previous.get('status')!='cancelled':self.request('DELETE',event_id,notify=True)
            return dict(status='cancelled')
        event=dict(event,extendedProperties={'private':{'flow_identity':identity,'flow_digest':digest}})
        if not previous:
            try:return self.request('POST',body=dict(event,id=event_id),notify=True)
            except HTTPError as error:
                if error.code!=409:raise
                # An uncertain insert can already exist: recheck its RSVP before retrying.
                previous=self.request('GET',event_id)
                if previous.get('extendedProperties',{}).get('private',{}).get('flow_identity')!=identity:
                    raise ValueError('Conflicto de identidad del evento de Calendar.')
        if invite_unanswered_only:
            expected={a.get('email','').casefold() for a in event.get('attendees',[])}
            guests=previous.get('attendees',[])
            response=next((a.get('responseStatus','unknown') for a in guests
                           if a.get('email','').casefold() in expected),'unknown')
            if response not in ('accepted','declined','tentative','needsAction'):response='unknown'
            reason=('cancelled' if previous.get('status')=='cancelled' else
                    'guest_changed' if len(expected)!=1 or not all(expected) or expected!={a.get('email','').casefold() for a in guests} else
                    response if response!='needsAction' else '')
            if reason:return dict(previous,_flow_invite_skipped=reason)
        if previous.get('status')!='cancelled' and previous.get('extendedProperties',{}).get('private',{}).get('flow_digest')==digest:
            return dict(previous,_flow_invite_skipped='already_delivered') if invite_unanswered_only else previous
        if invite_unanswered_only and not previous.get('etag'):
            return dict(previous,_flow_invite_skipped='missing_etag')
        # Preserve the guest's Google RSVP when updating role, schedule or documents.
        if previous.get('attendees') and event.get('attendees'):
            old=previous['attendees'];new=event['attendees']
            if {a.get('email') for a in old}=={a.get('email') for a in new}:event.pop('attendees')
        # PATCH retains omitted fields; clear the former date type when changing modes.
        for boundary in ('start', 'end'):
            new = event.get(boundary, {})
            old = previous.get(boundary, {})
            if new.get('date') and old.get('dateTime'):
                event[boundary] = dict(new, dateTime=None, timeZone=None)
            elif new.get('dateTime') and old.get('date'):
                event[boundary] = dict(new, date=None)
        event['status']='confirmed'
        options={'if_match':previous['etag']} if invite_unanswered_only else {}
        return self.request('PATCH',event_id,event,notify=True,**options)
