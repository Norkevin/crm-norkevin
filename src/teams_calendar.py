"""Durable Calendar queue. Only published, real Teams data reaches Google."""
from threading import Event, Thread
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
import re
import time
from uuid import uuid4

from itsdangerous import URLSafeSerializer

from src.teams import TeamsError, now
from src.teams_features import VISIBLE_ASSIGNMENTS, document_visible
from src.google_calendar import CalendarClient, connected_email, delivery_error
from src.tenant_brand_map import all_known_tenant_ids

LOGGER=logging.getLogger(__name__)


def valid_invitation_email(email):
    domain=email.rsplit('@',1)[-1].casefold()
    return bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email)
        and not domain.endswith(('.invalid','.test','.example','.localhost'))
        and domain not in ('example.com','example.org','example.net'))


def document_token(secret,tenant,member,document,assignment):
    expires=(datetime.fromisoformat(assignment['end'])+timedelta(days=7)).timestamp()
    return URLSafeSerializer(secret,salt='teams-calendar-document').dumps(dict(tenant=tenant,member=member['id'],
        access=member.get('access_version',1),document=document['id'],version=document['version'],expires=expires))


def events(store,tenant,job,origin,zone,secret,eligible_ids=None):
    with store.transaction() as db:
        members={m['id']:m for m in store.records(db,tenant,'member')}
        assignments=[a for a in store.records(db,tenant,'assignment') if a['job_id']==job['id']]
        documents=[d for d in store.records(db,tenant,'document') if d['job_id']==job['id']]
        eligible=bool(job.get('boda_date')) and job.get('status') not in ('Cancelado','Archivado')
        result={}
        event=None
        if eligible:
            start=date.fromisoformat(job['boda_date'][:10]);end=date.fromisoformat((job.get('end_date') or job['boda_date'])[:10])
            event=dict(summary=job.get('nombre') or 'Boda',location=job.get('location') or '',
                start={'date':start.isoformat()},end={'date':(max(start,end)+timedelta(days=1)).isoformat()},
                description='Boda en Flow CRM\n'+origin+'/teams/jobs/'+job['id'],visibility='private',
                guestsCanInviteOthers=False,guestsCanModify=False)
        result['job:'+job['id']]=event
        for assignment in assignments:
            if eligible_ids is not None and assignment['id'] not in eligible_ids:continue
            person=members[assignment['member_id']];event=None
            if eligible and person['active'] and assignment['status'] in VISIBLE_ASSIGNMENTS and assignment['job_day']==job['boda_date']:
                email=person.get('email','')
                if not valid_invitation_email(email):
                    raise TeamsError(f"{person['name']} necesita un correo válido. Agrégalo en su ficha de Miembros y vuelve a enviar.")
                start=datetime.fromisoformat(assignment['start']).replace(tzinfo=zone)
                end=datetime.fromisoformat(assignment['end']).replace(tzinfo=zone)
                lines=['Rol: '+assignment['role'],'Cobertura: '+start.strftime('%d/%m/%Y %H:%M')+' – '+end.strftime('%d/%m/%Y %H:%M'),
                       assignment.get('instructions') or '']
                for document in documents:
                    if document_visible(store,db,tenant,document,person['id']):
                        token=document_token(secret,tenant,person,document,assignment)
                        lines += [document['title']+': '+origin+'/teams-portal/calendar-document/'+token]
                lines += ['Portal del equipo: '+origin+'/teams-portal/login',
                          'Aceptar en Google Calendar confirma la invitación; revisa las condiciones de tu cobertura en el portal.']
                event=dict(summary=(job.get('nombre') or 'Boda')+' · '+assignment['role'],
                    location=job.get('location') or '',description='\n\n'.join(line for line in lines if line),
                    start={'dateTime':start.isoformat(),'timeZone':str(zone)},end={'dateTime':end.isoformat(),'timeZone':str(zone)},
                    attendees=[{'email':person['email']}],visibility='private',guestsCanInviteOthers=False,
                    guestsCanModify=False,guestsCanSeeOtherGuests=False)
            result['assignment:'+assignment['id']]=event
        return result


def attendee_response(record, event):
    if event.get('status') == 'cancelled':
        return 'cancelled'
    expected = {a.get('email', '').casefold() for a in (record.get('event') or {}).get('attendees', [])}
    guest = next((a for a in event.get('attendees', []) if a.get('email', '').casefold() in expected), {})
    response = guest.get('responseStatus')
    return response if response in ('accepted', 'declined', 'tentative', 'needsAction') else 'unknown'


class CalendarSync:
    def __init__(self,store,client_factory=CalendarClient):
        self.store=store;self.client_factory=client_factory
        self.wakeup=Event()
        self.started=False
        self.reconcile_tenant=None
        self.send_invitation_email=None

    def start(self):
        if self.started:return
        self.started=True
        Thread(target=self.run,daemon=True,name='teams-calendar').start()

    def run(self):
        next_scan=0
        while True:
            self.wakeup.wait(30);self.wakeup.clear()
            scan=time.monotonic()>=next_scan
            for tenant in all_known_tenant_ids():
                if connected_email(tenant):
                    if scan and self.reconcile_tenant:
                        try:self.reconcile_tenant(tenant)
                        except Exception:LOGGER.warning('Calendar reconciliation unavailable; will retry')
                    if scan:
                        try:self.refresh_responses(tenant)
                        except Exception:LOGGER.warning('Calendar responses unavailable; will retry')
                    try:self.drain(tenant)
                    except Exception:LOGGER.warning('Calendar queue unavailable; will retry')
            if scan:next_scan=time.monotonic()+60

    def reconcile(self,tenant,jobs,origin,zone,secret,*,calendar_entries=None):
        """Recover missed writes, initial connection and deleted CRM weddings."""
        jobs={job['id']:job for job in jobs}
        with self.store.transaction() as db:
            tracked={r['job_id'] for r in self.store.records(db,tenant,'calendar_sync') if r['identity'].startswith(('job:','assignment:'))}
        for identifier in tracked-jobs.keys():
            jobs[identifier]=dict(id=identifier,status='Archivado')
        for job in jobs.values():
            try:self.enqueue(tenant,job,origin,zone,secret,background=False,skip_unchanged=True)
            except (ValueError,TypeError,KeyError,TeamsError):
                LOGGER.warning('Calendar wedding data invalid; other weddings continue')
        if calendar_entries is not None:
            entries={entry['type']+':'+entry['id']:entry for entry in calendar_entries
                     if entry.get('type') in ('block','event','lead')}
            with self.store.transaction() as db:
                tracked={r['identity'] for r in self.store.records(db,tenant,'calendar_sync')
                         if r['identity'].startswith(('block:','event:','lead:'))}
            for identity in tracked-entries.keys():
                entries[identity]=dict(id=identity.split(':',1)[1],released_at=True)
            for identity,entry in entries.items():
                try:
                    event=None
                    if not entry.get('released_at'):
                        start=date.fromisoformat(entry['date'])
                        end=date.fromisoformat(entry.get('end_date') or entry['date'])
                        if end<start:raise ValueError('Invalid calendar date range')
                        kind=entry['type']
                        prefix={'block':'Fecha bloqueada · ','lead':'Lead · ','event':''}[kind]
                        description=('Fecha bloqueada' if kind=='block' else 'Lead' if kind=='lead' else 'Evento')+' en Flow CRM'
                        description+='\n'+origin+(entry.get('url') or '/calendar')
                        if kind=='event' and entry.get('notes'):description+='\n\n'+entry['notes']
                        event=dict(summary=prefix+(entry.get('title') or 'Sin título'),
                            start={'date':start.isoformat()},end={'date':(end+timedelta(days=1)).isoformat()},
                            description=description,location=entry.get('location') or '',
                            visibility='private',transparency='transparent' if kind=='lead' else 'opaque',
                            guestsCanInviteOthers=False,guestsCanModify=False)
                    self.enqueue_events(tenant,entry['id'],{identity:event},background=False,skip_unchanged=True)
                except (ValueError,TypeError,KeyError,TeamsError):
                    LOGGER.warning('Calendar entry invalid; other events continue')

    def enqueue(self,tenant,job,origin,zone,secret,*,background=True, invite_ids=None, send_at=None, include_new=False, delivery_key=None,skip_unchanged=False):
        with self.store.transaction() as db:
            tracked=[r for r in self.store.records(db,tenant,'calendar_sync') if r['status']!='paused']
        wanted=invite_ids if invite_ids is not None else (None if include_new else [r['identity'].split(':',1)[1] for r in tracked if r['identity'].startswith('assignment:')])
        desired=events(self.store,tenant,job,origin,zone,secret,wanted)
        return self.enqueue_events(tenant,job['id'],desired,background=background,invite_ids=invite_ids,
            send_at=send_at,include_new=include_new,delivery_key=delivery_key,skip_unchanged=skip_unchanged)

    def enqueue_events(self,tenant,source_id,desired,*,background=True,invite_ids=None,send_at=None,
                       include_new=False,delivery_key=None,skip_unchanged=False):
        queued=0
        with self.store.transaction() as db:
            existing={r['identity']:r for r in self.store.records(db,tenant,'calendar_sync')}
            for identity,event in desired.items():
                previous=existing.get(identity)
                if identity.startswith('assignment:') and (invite_ids is not None and identity.split(':',1)[1] not in invite_ids):continue
                if identity.startswith('assignment:') and not include_new and previous is None:continue
                delivery=delivery_key if delivery_key is not None else (previous or {}).get('delivery_key')
                digest=hashlib.sha256(json.dumps(dict(event=event,delivery=delivery),sort_keys=True).encode()).hexdigest()
                if event is None and previous is None:continue
                if previous and previous['digest']==digest and previous['status']=='synced':continue
                if skip_unchanged and previous and previous['digest']==digest:continue
                record=dict(previous) if previous else dict(id=str(uuid4()),identity=identity,
                    event_id=hashlib.sha256((tenant+':'+identity).encode()).hexdigest(),job_id=source_id)
                if previous and previous['event'] is None and event is not None and previous['status']=='synced':
                    record['event_id']=hashlib.sha256((tenant+':'+identity+':'+str(previous['version'])).encode()).hexdigest()
                record.update(digest=digest,delivery_key=delivery,event=event,status='pending',error='',queued_at=now(),retry_after=None)
                if identity.startswith('assignment:') and (not event or (previous and
                        (previous.get('event') or {}).get('attendees') != event.get('attendees'))):
                    record.update(email_status='not_sent' if event else 'cancelled', email_error='', email_sent_at=None,
                                  response_status='unknown' if event else 'cancelled', response_checked_at=None,
                                  response_retry_at=None, response_error='')
                if (identity.startswith('assignment:') and event and delivery_key is not None
                        and delivery_key != (previous or {}).get('delivery_key')):
                    record.update(email_status='pending', email_error='')
                if send_at is not None or not previous or delivery_key is not None:record['not_before']=send_at
                elif previous['status']=='synced':record['not_before']=None
                self.store.save(db,tenant,'calendar_sync',record);queued+=1
        if queued and background:self.wakeup.set()
        return queued

    def drain(self,tenant):
        with self.store.transaction() as db:
            clock=datetime.now(timezone.utc)
            rows=[r for r in self.store.records(db,tenant,'calendar_sync') if (r['status'] not in ('synced','paused')
                  or (r['status']=='synced' and r.get('email_status')=='pending' and self.send_invitation_email))
                  and all(not r.get(k) or datetime.fromisoformat(r[k])<=clock for k in ('not_before','retry_after'))]
        client=self.client_factory(tenant)
        for record in rows:
            with self.store.transaction() as db:
                current=self.store.get(db,tenant,'calendar_sync',record['id'])
                if current['digest']!=record['digest'] or current['status']=='paused':continue
            try:
                result=client.sync(record['event_id'],record['event'],record['digest'],record['identity'])
                status,error='synced',''
            except Exception as exception:
                # Never put Google responses, credentials or document access URLs into logs/UI.
                code,error=delivery_error(exception)
                result={};status='failed'
                LOGGER.warning('Calendar sync failed: %s',code)
            with self.store.transaction() as db:
                current=self.store.get(db,tenant,'calendar_sync',record['id'])
                if current['digest']!=record['digest']:continue
                current.update(status=status,error=error,synced_at=now() if status=='synced' else None,
                    retry_after=(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat() if status=='failed' else None,
                    html_url=result.get('htmlLink',''))
                if status == 'synced' and record['identity'].startswith('assignment:') and record['event']:
                    current.update(response_status=attendee_response(record,result),response_checked_at=now(),response_error='')
                self.store.save(db,tenant,'calendar_sync',current)
            if status == 'synced' and record['event'] and self.send_invitation_email:
                with self.store.transaction() as db:
                    current=self.store.get(db,tenant,'calendar_sync',record['id'])
                    if current['digest']!=record['digest'] or current.get('email_status')!='pending':continue
                    current.update(email_status='sending', email_error='')
                    self.store.save(db,tenant,'calendar_sync',current)
                try:
                    self.send_invitation_email(tenant,current)
                    email_status,email_error='sent',''
                except TeamsError as error:
                    email_status,email_error='failed',error.message
                except Exception:
                    email_status,email_error='failed','No se confirmó el correo. Revisa Gmail antes de volver a enviarlo.'
                with self.store.transaction() as db:
                    current=self.store.get(db,tenant,'calendar_sync',record['id'])
                    if current['digest']!=record['digest']:continue
                    current.update(email_status=email_status,email_error=email_error,
                                   email_sent_at=now() if email_status=='sent' else None)
                    self.store.save(db,tenant,'calendar_sync',current)

    def refresh_responses(self, tenant):
        """Read Google RSVP without updating events or sending invitations."""
        clock = datetime.now(timezone.utc)
        with self.store.transaction() as db:
            rows = [r for r in self.store.records(db, tenant, 'calendar_sync')
                    if r['identity'].startswith('assignment:') and r['status'] == 'synced' and r.get('event')
                    and (not r.get('response_retry_at') or datetime.fromisoformat(r['response_retry_at']) <= clock)]
        client = self.client_factory(tenant)
        for record in rows:
            try:
                event = client.response(record['event_id'], record['identity'])
                fields = dict(response_status=attendee_response(record, event), response_checked_at=now(), response_error='',
                              response_retry_at=(clock + timedelta(seconds=60)).isoformat())
            except Exception:
                fields = dict(response_error='No se pudo actualizar la respuesta de Google. Se conserva la última consulta.',
                              response_retry_at=(clock + timedelta(minutes=5)).isoformat())
            with self.store.transaction() as db:
                current = self.store.get(db, tenant, 'calendar_sync', record['id'])
                if current['digest'] != record['digest'] or current['status'] != 'synced':
                    continue
                current.update(fields)
                self.store.save(db, tenant, 'calendar_sync', current)
