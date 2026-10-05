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
        people=[store.create(db,TENANT,'member',name='Persona '+str(i),email='worker'+str(i)+'@example.invalid',active=True,access_version=1) for i in range(2)]
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
