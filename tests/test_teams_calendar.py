from copy import deepcopy
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

import pytest

from src.teams import TeamsStore
from src.teams_calendar import CalendarSync, events
from src.google_calendar import CalendarClient, token_path, save_token, load_token

TENANT='tenant-norkevin-photography'
ZONE=ZoneInfo('America/Guatemala')
JOB=dict(id='wedding',nombre='Boda de prueba',boda_date='2026-11-14',location='Lugar real',status='En curso')
ORIGIN='https://flowingcrm.com'


@pytest.fixture
def prepared(tmp_path):
    store=TeamsStore(tmp_path/'teams.sqlite3')
    with store.transaction() as db:
        people=[store.create(db,TENANT,'member',name='Persona '+str(i),email='worker'+str(i)+'@flow-qa-84982.com',active=True,access_version=1) for i in range(2)]
        assignments=[store.create(db,TENANT,'assignment',job_id='wedding',member_id=m['id'],status='pendiente',job_day=JOB['boda_date'],role='Fotografía' if i==0 else 'Dron',
            start='2026-11-14T13:00:00-06:00',end='2026-11-14T22:00:00-06:00',instructions='Instrucción real',terms_version=1) for i,m in enumerate(people)]
        document=store.save(db,TENANT,'document',dict(id='document-private',job_id='wedding',status='publicado',audience_ids=[people[0]['id']],title='Call sheet privado',content='Información compartida',kind='Call sheet'))
    return store,people,assignments,document


def rows(store,kind='calendar_sync',tenant=TENANT):
    with store.transaction() as db:return store.records(db,tenant,kind)


class FakeCalendar:
    def __init__(self):self.calls=[];self.fail=False
    def sync(self,*args):
        self.calls.append(args)
        if self.fail:raise OSError('sensitive provider reply must never leak')
        return {'htmlLink':'https://calendar.google.com/event?test=1'}


def test_disabled_api_exposes_only_provider_project_number():
    import io,json
    from src.google_calendar import delivery_error
    payload={'error':{'details':[{'reason':'SERVICE_DISABLED','metadata':{'consumer':'projects/123456789',
              'sensitive':'must-not-leak'}}]}}
    error=HTTPError('https://google.invalid',403,'disabled',{},io.BytesIO(json.dumps(payload).encode()))
    code,message=delivery_error(error)
    assert code=='api_disabled' and '123456789' in message and 'must-not-leak' not in message


def test_automatic_reconciliation_initial_changes_deletion_and_retry(prepared):
    store,people,assignments,doc=prepared
    fake=FakeCalendar();sync=CalendarSync(store,lambda tenant:fake)
    sync.reconcile(TENANT,[JOB],ORIGIN,ZONE,'secret')
    sync.drain(TENANT)
    assert len(fake.calls)==1 and fake.calls[0][3]=='job:wedding'
    event_id=fake.calls[0][0]
    sync.reconcile(TENANT,[JOB],ORIGIN,ZONE,'secret')
    sync.drain(TENANT)
    assert len(fake.calls)==1
    changed=dict(JOB,boda_date='2027-02-01',location='Nuevo lugar')
    fake.fail=True
    sync.reconcile(TENANT,[changed],ORIGIN,ZONE,'secret');sync.drain(TENANT)
    retry=rows(store)[0]['retry_after']
    sync.reconcile(TENANT,[changed],ORIGIN,ZONE,'secret')
    assert rows(store)[0]['retry_after']==retry and rows(store)[0]['status']=='failed'
    fake.fail=False
    sync.reconcile(TENANT,[],ORIGIN,ZONE,'secret');sync.drain(TENANT)
    assert fake.calls[-1][0]==event_id and fake.calls[-1][1] is None
    sync.reconcile(TENANT,[JOB],ORIGIN,ZONE,'secret');sync.drain(TENANT)
    assert fake.calls[-1][0]!=event_id and fake.calls[-1][1]['start']=={'date':JOB['boda_date']}
    assert rows(store,tenant='tenant-norkevin')==[]


def test_background_reconciliation_uses_explicit_brand_context(flask_app,tmp_path,monkeypatch):
    from flask import Flask,Blueprint,g
    import app as crm
    from src.teams_calendar_routes import register_calendar
    application=Flask('calendar-background');application.secret_key='test-secret'
    application.config['TESTING']=True
    database=TeamsStore(tmp_path/'background.sqlite3')
    for tenant,identifier in [(TENANT,'own-wedding'),('tenant-norkevin','other-wedding')]:
        crm.store.upsert('jobs',dict(JOB,id=identifier,tenant_id=tenant))
        crm.store.upsert('calendar',dict(id=identifier,type='block',date='2026-12-24',tenant_id=tenant))
        crm.store.upsert('calendar',dict(id='zoom-'+identifier,type='event',date='2026-12-20',title='Zoom',tenant_id=tenant))
        crm.store.upsert('calendar',dict(id='old-'+identifier,type='job',date='2026-12-20',tenant_id=tenant))
        crm.store.upsert('leads',dict(id=identifier,nombre='Consulta',status='Nuevo',fecha_tentativa='2026-12-21',tenant_id=tenant))
    # Production's Teams portal resolver consults flask.g before the CRM resolver.
    original=crm.store.tenant_resolver
    monkeypatch.setattr(crm.store,'tenant_resolver',lambda: getattr(g,'teams_portal_tenant',None) or original())
    register_calendar(application,Blueprint('calendar-test',__name__),crm.store,database,None,crm._canonical_jobs)
    application.extensions['teams_calendar'].reconcile_tenant(TENANT)
    assert {r['identity'] for r in rows(database)}=={'job:own-wedding','block:own-wedding','event:zoom-own-wedding','lead:lead-own-wedding'}
    assert crm._workflow_tenant.get() is None
    monkeypatch.setattr(crm.store,'tenant_resolver',lambda: None)
    from src.teams import TeamsError
    with pytest.raises(TeamsError,match='marca'):
        application.extensions['teams_calendar'].reconcile_tenant(TENANT)
    assert rows(database)[0]['event'] is not None


def test_blocked_dates_sync_update_release_delete_restore_without_touching_weddings(prepared):
    store,*_=prepared
    fake=FakeCalendar();sync=CalendarSync(store,lambda tenant:fake)
    block=dict(id=JOB['id'],type='block',date='2026-12-24',end_date='2026-12-26',title='Descanso')
    def reconcile(blocks):
        sync.reconcile(TENANT,[JOB],ORIGIN,ZONE,'secret',calendar_entries=blocks);sync.drain(TENANT)
    reconcile([block])
    original={r['identity']:r for r in rows(store)}
    event=original['block:wedding']['event']
    assert event['start']=={'date':'2026-12-24'} and event['end']=={'date':'2026-12-27'}
    assert event['transparency']=='opaque' and event['visibility']=='private' and 'attendees' not in event
    assert original['job:wedding']['event_id']!=original['block:wedding']['event_id']
    reconcile([block]);assert len(fake.calls)==2
    reconcile([dict(block,date='2026-12-25',title='Viaje')])
    assert fake.calls[-1][1]['summary']=='Fecha bloqueada · Viaje'
    assert fake.calls[-1][0]==original['block:wedding']['event_id']
    fake.fail=True
    reconcile([dict(block,released_at='2026-10-07')])
    failed=next(r for r in rows(store) if r['identity']=='block:wedding')
    reconcile([dict(block,released_at='2026-10-07')])
    assert next(r for r in rows(store) if r['identity']=='block:wedding')['retry_after']==failed['retry_after']
    fake.fail=False
    with store.transaction() as db:
        failed['retry_after']=None;store.save(db,TENANT,'calendar_sync',failed)
    sync.drain(TENANT);assert fake.calls[-1][1] is None
    reconcile([block]);assert fake.calls[-1][0]!=original['block:wedding']['event_id']
    reconcile([]);assert fake.calls[-1][1] is None
    assert next(r for r in rows(store) if r['identity']=='job:wedding')==original['job:wedding']


def test_manual_events_and_leads_sync_changes_and_removals(prepared):
    store,*_=prepared;fake=FakeCalendar();sync=CalendarSync(store,lambda tenant:fake)
    entries=[dict(id=str(i),type='event',date='2026-12-01',title=title,notes='Enlace de reunión')
             for i,title in enumerate(['Boda civil','CVDate','Zoom'])]
    entries += [dict(id='lead',type='lead',date='2026-12-02',title='Consulta',url='/leads/lead'),
                dict(id='bad',type='event',date='invalid'),dict(id='old-job',type='job',date='2026-12-01')]
    sync.reconcile(TENANT,[],ORIGIN,ZONE,'secret',calendar_entries=entries);sync.drain(TENANT)
    assert len(fake.calls)==4
    assert {r['event']['summary'] for r in rows(store)}=={'Boda civil','CVDate','Zoom','Lead · Consulta'}
    lead=next(r for r in rows(store) if r['identity']=='lead:lead')
    assert lead['event']['transparency']=='transparent' and '/leads/lead' in lead['event']['description']
    entries[2].update(date='2026-12-05',title='Zoom movido')
    sync.reconcile(TENANT,[],ORIGIN,ZONE,'secret',calendar_entries=entries[:3]);sync.drain(TENANT)
    changed=next(r for r in rows(store) if r['identity']=='event:2')
    assert changed['event']['start']=={'date':'2026-12-05'} and 'Enlace de reunión' in changed['event']['description']
    assert next(r for r in rows(store) if r['identity']=='lead:lead')['event'] is None
    sync.reconcile(TENANT,[],ORIGIN,ZONE,'secret',calendar_entries=[]);sync.drain(TENANT)
    assert all(r['event'] is None and r['status']=='synced' for r in rows(store))


def test_invites_exact_individual_role_hours_and_documents_without_finance(prepared):
    store,people,assignments,doc=prepared
    result=events(store,TENANT,JOB,ORIGIN,ZONE,'secret')
    first=result['assignment:'+assignments[0]['id']];second=result['assignment:'+assignments[1]['id']]
    assert first['attendees']==[{'email':people[0]['email']}]
    assert second['attendees']==[{'email':people[1]['email']}]
    assert first['start']['dateTime']=='2026-11-14T13:00:00-06:00'
    assert first['end']['dateTime']=='2026-11-14T22:00:00-06:00'
    assert 'Call sheet privado' in first['description'] and 'Call sheet privado' not in second['description']
    assert '/teams-portal/calendar-document/' in first['description']
    assert 'amount' not in first and 'cost' not in first and 'bank' not in first
    assert result['job:wedding']['start']=={'date':'2026-11-14'}
    assert result['job:wedding']['end']=={'date':'2026-11-15'}
    assert 'attendees' not in result['job:wedding']


def test_no_guest_invites_until_explicit_send_individual_bulk_and_safe_retry(prepared):
    store,people,assignments,doc=prepared;fake=FakeCalendar();sync=CalendarSync(store,lambda tenant:fake)
    assert sync.enqueue(TENANT,JOB,ORIGIN,ZONE,'secret',background=False)==1
    sync.drain(TENANT);assert len(fake.calls)==1
    assert sync.enqueue(TENANT,JOB,ORIGIN,ZONE,'secret',background=False,include_new=True,invite_ids=[assignments[0]['id']],delivery_key='same-request')==2
    sync.drain(TENANT)
    assert len([c for c in fake.calls if c[3].startswith('assignment:')])==1
    assert sync.enqueue(TENANT,JOB,ORIGIN,ZONE,'secret',background=False,include_new=True,invite_ids=[assignments[0]['id']],delivery_key='same-request')==0
    assert len(rows(store))==2
    sync.enqueue(TENANT,JOB,ORIGIN,ZONE,'secret',background=False,include_new=True)
    sync.drain(TENANT);assert len(rows(store))==3
    event_ids={r['identity']:r['event_id'] for r in rows(store)}
    sync.enqueue(TENANT,dict(JOB,location='Nuevo lugar'),ORIGIN,ZONE,'secret',background=False)
    sync.drain(TENANT)
    assert event_ids=={r['identity']:r['event_id'] for r in rows(store)}


def test_scheduled_survives_restart_awaits_time_and_can_pause(prepared):
    store,people,assignments,doc=prepared;fake=FakeCalendar();sync=CalendarSync(store,lambda tenant:fake)
    future=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    sync.enqueue(TENANT,JOB,ORIGIN,ZONE,'secret',background=False,include_new=True,send_at=future)
    restarted=CalendarSync(TeamsStore(store.path),lambda tenant:fake)
    restarted.drain(TENANT);assert fake.calls==[]
    restarted.enqueue(TENANT,dict(JOB,location='Nuevo lugar'),ORIGIN,ZONE,'secret',background=False)
    assert all(r['not_before']==future for r in rows(store))
    with store.transaction() as db:
        for record in store.records(db,TENANT,'calendar_sync'):
            record['not_before']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
            if record['identity']=='assignment:'+assignments[1]['id']:record['status']='paused'
            store.save(db,TENANT,'calendar_sync',record)
    restarted.drain(TENANT);assert len(fake.calls)==2
    restarted.enqueue(TENANT,dict(JOB,location='Otro lugar'),ORIGIN,ZONE,'secret',background=False)
    assert next(r for r in rows(store) if r['identity']=='assignment:'+assignments[1]['id'])['status']=='paused'


def test_failed_send_visible_and_retry_reuses_event_id(prepared):
    store,_,assignments,_=prepared;fake=FakeCalendar();fake.fail=True;sync=CalendarSync(store,lambda tenant:fake)
    sync.enqueue(TENANT,JOB,ORIGIN,ZONE,'secret',background=False,include_new=True,invite_ids=[assignments[0]['id']])
    sync.drain(TENANT);before=rows(store)
    assert all(r['status']=='failed' and 'sensitive' not in r['error'] for r in before)
    fake.fail=False
    sync.enqueue(TENANT,JOB,ORIGIN,ZONE,'secret',background=False,include_new=True,invite_ids=[assignments[0]['id']])
    sync.drain(TENANT)
    assert {r['event_id'] for r in before}=={r['event_id'] for r in rows(store)}
    assert all(r['status']=='synced' for r in rows(store))


def test_unpublished_cancelled_wrong_date_and_inactive_never_get_invited(prepared):
    store,people,assignments,_=prepared
    for status in ('borrador','cancelada','rechazada'):
        with store.transaction() as db:
            a=store.get(db,TENANT,'assignment',assignments[0]['id']);a['status']=status;store.save(db,TENANT,'assignment',a)
        assert events(store,TENANT,JOB,ORIGIN,ZONE,'secret')['assignment:'+a['id']] is None
    assert all(v is None for v in events(store,TENANT,dict(JOB,status='Cancelado'),ORIGIN,ZONE,'secret').values())
    shifted=events(store,TENANT,dict(JOB,boda_date='2027-01-01'),ORIGIN,ZONE,'secret')
    assert all(shifted['assignment:'+a['id']] is None for a in assignments)
    with store.transaction() as db:
        member=store.get(db,TENANT,'member',people[1]['id']);member['active']=False;store.save(db,TENANT,'member',member)
    assert events(store,TENANT,JOB,ORIGIN,ZONE,'secret')['assignment:'+assignments[1]['id']] is None


def test_individual_send_not_blocked_by_an_unselected_bad_email(prepared):
    store,people,assignments,_=prepared
    with store.transaction() as db:
        member=store.get(db,TENANT,'member',people[1]['id']);member['email']='';store.save(db,TENANT,'member',member)
    sync=CalendarSync(store)
    assert sync.enqueue(TENANT,JOB,ORIGIN,ZONE,'secret',background=False,include_new=True,invite_ids=[assignments[0]['id']])==2


def test_token_storage_keeps_gmail_and_other_brand_separate(tmp_path,monkeypatch):
    monkeypatch.setenv('CRM_DATA_DIR',str(tmp_path))
    save_token(TENANT,{'refresh_token':'calendar-secret','email':'test'})
    assert token_path(TENANT).stat().st_mode & 0o777==0o600
    assert load_token('tenant-norkevin') is None
    assert not (tmp_path/('google_token_'+TENANT+'.json')).exists()
    assert load_token(TENANT)['refresh_token']=='calendar-secret'


def test_provider_idempotency_preserves_google_rsvp_and_notifies():
    client=CalendarClient(TENANT);calls=[]
    old=dict(id='stable',attendees=[{'email':'worker@example.invalid','responseStatus':'accepted'}],extendedProperties={'private':{'flow_identity':'assignment:a','flow_digest':'old'}})
    def request(method,event_id='',body=None,notify=False):
        calls.append((method,event_id,body,notify));return deepcopy(old) if method=='GET' else {'id':'stable'}
    client.request=request
    new=dict(summary='Rol nuevo',attendees=[{'email':'worker@example.invalid'}])
    client.sync('stable',new,'new','assignment:a')
    assert calls[-1][0]=='PATCH' and calls[-1][3] is True and 'attendees' not in calls[-1][2]
    calls.clear();client.sync('stable',new,'old','assignment:a');assert len(calls)==1
    old['extendedProperties']['private']['flow_identity']='foreign'
    with pytest.raises(ValueError):client.sync('stable',new,'new','assignment:a')


def test_send_now_replaces_schedule_and_brands_keep_distinct_event_ids(prepared):
    store,_,assignments,_=prepared;sync=CalendarSync(store)
    future=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    sync.enqueue(TENANT,JOB,ORIGIN,ZONE,'secret',background=False,include_new=True,invite_ids=[assignments[0]['id']],send_at=future,delivery_key='schedule')
    sync.enqueue(TENANT,JOB,ORIGIN,ZONE,'secret',background=False,include_new=True,invite_ids=[assignments[0]['id']],delivery_key='send-now')
    assert all(r['not_before'] is None for r in rows(store))
    sync.enqueue('tenant-norkevin',JOB,ORIGIN,ZONE,'secret',background=False,invite_ids=[])
    assert not ({r['event_id'] for r in rows(store)} & {r['event_id'] for r in rows(store,tenant='tenant-norkevin')})


def test_placeholder_email_cannot_receive_real_calendar_invitation(prepared):
    from src.teams import TeamsError
    store,people,assignments,_=prepared
    with store.transaction() as db:
        person=store.get(db,TENANT,'member',people[0]['id']);person['email']='placeholder@example.invalid';store.save(db,TENANT,'member',person)
    with pytest.raises(TeamsError,match='correo válido'):
        CalendarSync(store).enqueue(TENANT,JOB,ORIGIN,ZONE,'secret',background=False,include_new=True,invite_ids=[assignments[0]['id']])
    assert rows(store)==[]


def test_oauth_requests_calendar_separately_and_explicit_brand_account_selection():
    from urllib.parse import urlsplit,parse_qs
    from src.google_calendar import authorization_url,SCOPE
    params=parse_qs(urlsplit(authorization_url('https://flowingcrm.com/auth/google/callback','calendar.state',TENANT)).query)
    assert params['scope']==[SCOPE+' openid email']
    assert params['login_hint']==['norkevinfoto@gmail.com']
    assert params['prompt']==['select_account consent']
    assert 'gmail.send' not in params['scope'][0]


@pytest.mark.parametrize('status,reason,code,fragment',[
    (403,'accessNotConfigured','api_disabled','API no está activada'),
    (403,'SERVICE_DISABLED','api_disabled','API no está activada'),
    (401,'authError','authorization','Vuelve a conectar'),
    (400,'invalid_grant','authorization','Vuelve a conectar'),
    (403,'rateLimitExceeded','rate_limit','15 minutos'),
    (429,'rateLimitExceeded','rate_limit','15 minutos'),
    (403,'insufficientPermissions','permission','permiso de Calendar'),
    (400,'badRequest','invalid_event','correo, la fecha'),
    (503,'backendError','unconfirmed','sin duplicarla'),
])
def test_provider_diagnostics_are_actionable_and_never_leak_response(status,reason,code,fragment):
    import io,json
    from src.google_calendar import delivery_error
    body=json.dumps({'error':reason if reason=='invalid_grant' else {'errors':[{'reason':reason,'message':'secret-doc-link'}]}}).encode()
    exception=HTTPError('https://provider.invalid/sensitive',status,'private detail',{},io.BytesIO(body))
    actual,message=delivery_error(exception)
    assert actual==code and fragment in message
    assert 'secret-doc-link' not in message and 'private detail' not in message and 'sensitive' not in message


def test_missing_email_names_member_and_never_queues_even_master(prepared):
    from src.teams import TeamsError
    store,people,assignments,_=prepared
    with store.transaction() as db:
        person=store.get(db,TENANT,'member',people[0]['id']);person['email']='';store.save(db,TENANT,'member',person)
    sync=CalendarSync(store)
    with pytest.raises(TeamsError,match=person['name']):
        sync.enqueue(TENANT,JOB,ORIGIN,ZONE,'secret',background=False,include_new=True,invite_ids=[assignments[0]['id']])
    assert rows(store)==[]


def test_explicit_calendar_mail_sent_once_and_failure_does_not_retry_blindly(prepared):
    store, people, assignments, _ = prepared; fake = FakeCalendar()
    sync = CalendarSync(store, lambda tenant: fake); sent = []
    sync.send_invitation_email = lambda tenant, record: sent.append((tenant, record['identity']))
    def enqueue(key):
        sync.enqueue(TENANT, JOB, ORIGIN, ZONE, 'secret', background=False,
                     include_new=True, invite_ids=[assignments[0]['id']], delivery_key=key)
    enqueue('first'); sync.drain(TENANT); sync.drain(TENANT)
    assert sent == [(TENANT, 'assignment:'+assignments[0]['id'])]
    assert next(r for r in rows(store) if r['identity'].startswith('assignment:'))['email_status'] == 'sent'
    enqueue('first'); sync.drain(TENANT); assert len(sent) == 1
    sync.reconcile(TENANT, [dict(JOB, location='Changed')], ORIGIN, ZONE, 'secret')
    sync.drain(TENANT); assert len(sent) == 1
    enqueue('resend'); sync.drain(TENANT); assert len(sent) == 2
    def fail(*args): raise OSError('private provider error')
    sync.send_invitation_email = fail
    enqueue('failure'); sync.drain(TENANT)
    row = next(r for r in rows(store) if r['identity'].startswith('assignment:'))
    assert row['status'] == 'synced' and row['email_status'] == 'failed'
    assert 'private provider error' not in str(row)
    sync.send_invitation_email = lambda *args: sent.append(args)
    sync.drain(TENANT); assert len(sent) == 2
    enqueue('explicit-retry'); sync.drain(TENANT); assert len(sent) == 3


def test_cancelled_invitation_does_not_send_pending_email_and_replacement_clears_old_status(prepared):
    store, people, assignments, _ = prepared; fake=FakeCalendar();sync=CalendarSync(store,lambda tenant:fake)
    sent=[];sync.send_invitation_email=lambda *args:sent.append(args)
    identity='assignment:'+assignments[0]['id']
    sync.enqueue(TENANT,JOB,ORIGIN,ZONE,'secret',background=False,include_new=True,delivery_key='invite')
    sync.enqueue_events(TENANT,JOB['id'],{identity:None},background=False)
    sync.drain(TENANT)
    assert all(args[1]['identity'] != identity for args in sent)
    row=next(r for r in rows(store) if r['identity']==identity)
    assert row['email_status']=='cancelled'
    count=len(fake.calls);sync.drain(TENANT);assert len(fake.calls)==count
    sync.enqueue(TENANT,JOB,ORIGIN,ZONE,'secret',background=False,include_new=True,delivery_key='again')
    sync.drain(TENANT)
    event=deepcopy(next(r for r in rows(store) if r['identity']==identity)['event'])
    event['attendees']=[{'email':'replacement@flow-qa-84982.com'}]
    sync.enqueue_events(TENANT,JOB['id'],{identity:event},background=False)
    count=len(sent);sync.drain(TENANT);assert len(sent)==count
    assert next(r for r in rows(store) if r['identity']==identity)['email_status']=='not_sent'


def test_rsvp_refresh_reads_exact_guest_without_resending_and_preserves_portal(prepared):
    store,people,assignments,_=prepared;fake=FakeCalendar();sync=CalendarSync(store,lambda tenant:fake)
    sync.enqueue(TENANT,JOB,ORIGIN,ZONE,'secret',background=False,include_new=True)
    sync.drain(TENANT);before=deepcopy(rows(store,'assignment'));calls=len(fake.calls)
    responses=[]
    def response(event_id,identity):
        responses.append(identity)
        return dict(attendees=[dict(email='unrelated@example.com',responseStatus='declined'),
                              dict(email=people[0]['email'].upper(),responseStatus='accepted')])
    fake.response=response
    sync.refresh_responses(TENANT)
    invitations={r['identity']:r for r in rows(store) if r['identity'].startswith('assignment:')}
    first=invitations['assignment:'+assignments[0]['id']]
    assert first['response_status']=='accepted' and first['response_checked_at']
    assert invitations['assignment:'+assignments[1]['id']]['response_status']=='unknown'
    assert len(fake.calls)==calls and rows(store,'assignment')==before
    sync.refresh_responses(TENANT);assert len(responses)==2
    def fail(*args):raise OSError('private token data')
    fake.response=fail
    with store.transaction() as db:
        first['response_retry_at']=None;store.save(db,TENANT,'calendar_sync',first)
    sync.refresh_responses(TENANT)
    first=next(r for r in rows(store) if r['id']==first['id'])
    assert first['response_status']=='accepted' and first['response_error']
    assert 'private token data' not in str(first)
    assert rows(store,tenant='other-brand')==[]


@pytest.mark.parametrize('status',['accepted','declined','tentative','needsAction'])
def test_rsvp_all_google_responses_and_ownership_guard(status,monkeypatch):
    from src.teams_calendar import attendee_response
    client=CalendarClient(TENANT);calls=[]
    def request(*args):
        calls.append(args)
        return dict(extendedProperties={'private':{'flow_identity':'assignment:1'}},attendees=[dict(email='a@example.com',responseStatus=status)])
    monkeypatch.setattr(client,'request',request)
    record=dict(event=dict(attendees=[dict(email='a@example.com')]))
    assert attendee_response(record,client.response('event-1','assignment:1'))==status
    assert calls==[('GET','event-1')]
    with pytest.raises(ValueError):client.response('event-1','assignment:other')
    assert attendee_response(record,dict(status='cancelled'))=='cancelled'
