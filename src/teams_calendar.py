"""Durable Calendar queue. Only published, real Teams data reaches Google."""
from threading import Event, Thread
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
import re
from uuid import uuid4

from itsdangerous import URLSafeSerializer

from src.teams import TeamsError, now
from src.teams_features import VISIBLE_ASSIGNMENTS, document_visible
from src.google_calendar import CalendarClient, connected_email
from src.tenant_brand_map import all_known_tenant_ids

LOGGER=logging.getLogger(__name__)


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
                domain=email.rsplit('@',1)[-1].casefold()
                if (not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email)
                    or domain.endswith(('.invalid','.test','.example','.localhost'))
                    or domain in ('example.com','example.org','example.net')):
                    raise TeamsError('La persona necesita un correo válido para recibir la invitación.')
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


class CalendarSync:
    def __init__(self,store,client_factory=CalendarClient):
        self.store=store;self.client_factory=client_factory
        self.wakeup=Event()
        self.started=False

    def start(self):
        if self.started:return
        self.started=True
        Thread(target=self.run,daemon=True,name='teams-calendar').start()

    def run(self):
        while True:
            self.wakeup.wait(30);self.wakeup.clear()
            for tenant in all_known_tenant_ids():
                if connected_email(tenant):
                    try:self.drain(tenant)
                    except Exception:LOGGER.warning('Calendar queue unavailable; will retry')

    def enqueue(self,tenant,job,origin,zone,secret,*,background=True, invite_ids=None, send_at=None, include_new=False, delivery_key=None):
        with self.store.transaction() as db:
            tracked=[r for r in self.store.records(db,tenant,'calendar_sync') if r['status']!='paused']
        wanted=invite_ids if invite_ids is not None else (None if include_new else [r['identity'].split(':',1)[1] for r in tracked if r['identity'].startswith('assignment:')])
        desired=events(self.store,tenant,job,origin,zone,secret,wanted)
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
                record=dict(previous) if previous else dict(id=str(uuid4()),identity=identity,
                    event_id=hashlib.sha256((tenant+':'+identity).encode()).hexdigest(),job_id=job['id'])
                if previous and previous['event'] is None and event is not None and previous['status']=='synced':
                    record['event_id']=hashlib.sha256((tenant+':'+identity+':'+str(previous['version'])).encode()).hexdigest()
                record.update(digest=digest,delivery_key=delivery,event=event,status='pending',error='',queued_at=now(),retry_after=None)
                if send_at is not None or not previous or delivery_key is not None:record['not_before']=send_at
                elif previous['status']=='synced':record['not_before']=None
                self.store.save(db,tenant,'calendar_sync',record);queued+=1
        if queued and background:self.wakeup.set()
        return queued

    def drain(self,tenant):
        with self.store.transaction() as db:
            clock=datetime.now(timezone.utc)
            rows=[r for r in self.store.records(db,tenant,'calendar_sync') if r['status'] not in ('synced','paused')
                  and all(not r.get(k) or datetime.fromisoformat(r[k])<=clock for k in ('not_before','retry_after'))]
        client=self.client_factory(tenant)
        for record in rows:
            with self.store.transaction() as db:
                current=self.store.get(db,tenant,'calendar_sync',record['id'])
                if current['digest']!=record['digest'] or current['status']=='paused':continue
            try:
                result=client.sync(record['event_id'],record['event'],record['digest'],record['identity'])
                status,error='synced',''
            except Exception:
                # Never put Google responses, credentials or document access URLs into logs/UI.
                result={};status,error='failed','No se confirmó con Google. Reintenta; si persiste, revisa la conexión o activa Calendar API en el proyecto de Google.'
                LOGGER.warning('Calendar sync failed for a tenant-scoped Teams event')
            with self.store.transaction() as db:
                current=self.store.get(db,tenant,'calendar_sync',record['id'])
                if current['digest']!=record['digest']:continue
                current.update(status=status,error=error,synced_at=now() if status=='synced' else None,
                    retry_after=(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat() if status=='failed' else None,
                    html_url=result.get('htmlLink',''))
                self.store.save(db,tenant,'calendar_sync',current)
