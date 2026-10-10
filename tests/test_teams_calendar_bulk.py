from copy import deepcopy
from uuid import uuid4

import pytest

from test_flow_teams import web, run, records
from src import google_calendar
from src.teams import TeamsError


@pytest.fixture
def bulk(web,monkeypatch):
    app,owner,crm=web
    app.config.update(FLOW_TEAMS_LOCAL=False,FLOW_TEAMS_ENABLED=True)
    monkeypatch.setattr(google_calendar,'connected_email',lambda tenant:'owner@flow-qa-84982.com')
    store=app.extensions['teams'];sync=app.extensions['teams_calendar']
    # A provider is never reached by these HTTP-selection tests.
    sync.client_factory=lambda tenant: (_ for _ in ()).throw(AssertionError('Unexpected provider call'))
    return app,owner,crm,store,sync


def person(store,name,email=True,tenant='brand-a'):
    return run(store,'member',tenant,name=name,email=(name+'@flow-qa-84982.com') if email else '',role='Foto')


def coverage(store,member,job='job-1',tenant='brand-a',published=False,**overrides):
    date='2026-12-01' if job=='job-2' else '2026-11-14'
    fields=dict(action='assignment',key=str(uuid4()),job_id=job,member_id=member['id'],slot=member['name']+str(uuid4()),
        role='Foto',start=date+'T13:00',end=date+'T22:00',buffer=30,amount='1500')
    fields.update(overrides)
    reader=lambda i:dict(id=i,boda_date=date,status='En curso')
    a=store.command(tenant,'Owner',fields,reader)['record']
    if published:
        a=store.command(tenant,'Owner',dict(action='assignment_publish',key=str(uuid4()),id=a['id'],version=a['version']),reader)['record']
    return a


def cached(bulk,a,status='needsAction',**fields):
    app,owner,crm,store,sync=bulk
    with app.test_request_context('/'):
        from flask import session
        session['tenant_id']='brand-a'
        from src.teams import teams_zone
        job=crm.get('jobs',a['job_id'])
        sync.enqueue('brand-a',job,'https://flowingcrm.com',teams_zone(crm,'brand-a'),app.secret_key,
                     include_new=True,invite_ids=[a['id']],delivery_key='prior-'+a['id'],background=False)
    with store.transaction() as db:
        row=next(r for r in store.records(db,'brand-a','calendar_sync') if r['identity']=='assignment:'+a['id'])
        row.update(status='synced',email_status='sent',response_status=status,**fields)
        store.save(db,'brand-a','calendar_sync',row)
        return deepcopy(row)


def send(owner,key='bulk-first',**fields):
    return owner.post('/api/teams/calendar/sync?list_year=2027&q=nothing',headers={'X-Teams-CSRF':'csrf'},
                      json=dict(scope='pending_invitations',key=key,**fields))


def test_all_upcoming_weddings_publish_drafts_and_queue_once_regardless_of_list_filters(bulk):
    app,owner,crm,store,sync=bulk
    first=coverage(store,person(store,'one'))
    second=coverage(store,person(store,'two'),job='job-2')
    assert send(owner).get_json()['record']==dict(queued=2,answered=0,busy=0,review=0)
    rows=[r for r in records(store,'calendar_sync') if r['identity'].startswith('assignment:')]
    assert {r['identity'] for r in rows}=={'assignment:'+first['id'],'assignment:'+second['id']}
    assert all(r['invite_unanswered_only'] and r['email_status']=='pending' for r in rows)
    assert all(a['status']=='pendiente' for a in records(store,'assignment'))
    assert all(c['status']=='aprobado' for c in records(store,'cost'))
    before=deepcopy(records(store,'calendar_sync'))
    repeated=send(owner).get_json()
    assert repeated['record']['queued']==2 and records(store,'calendar_sync')==before
    assert len(records(store,'notice'))==2
    assert len([r for r in records(store,'audit') if r['action']=='calendar_bulk_invite'])==1
    # A new click conserves deliveries already pending; no queue reset or duplicate notices.
    assert send(owner,key='bulk-again').get_json()['record']['busy']==2
    assert records(store,'calendar_sync')==before
    html=owner.get('/teams/jobs?list_year=2027&q=nothing').get_data(as_text=True)
    assert '<summary>Enviar invitaciones pendientes</summary>' in html
    assert 'name="scope" value="pending_invitations"' in html
    assert 'Los borradores se publican' in html


@pytest.mark.parametrize('status',['accepted','declined','tentative','cancelled'])
def test_button_does_not_reinvite_google_responses_and_does_not_change_individual_send(bulk,status):
    app,owner,crm,store,sync=bulk
    m=person(store,'responded');a=coverage(store,m,published=True)
    row=cached(bulk,a,status)
    assert send(owner).get_json()['record']['queued']==0
    assert next(r for r in records(store,'calendar_sync') if r['id']==row['id'])==row
    result=owner.post('/api/teams/calendar/sync',headers={'X-Teams-CSRF':'csrf'},json=dict(
        job_id=a['job_id'],key='explicit-individual',invite='individual',assignment_id=a['id']))
    assert result.status_code==200
    current=next(r for r in records(store,'calendar_sync') if r['id']==row['id'])
    assert current['status']=='pending' and not current.get('invite_unanswered_only')


def test_portal_responses_invalid_contacts_scheduled_and_foreign_records_are_omitted(bulk):
    app,owner,crm,store,sync=bulk
    answered=coverage(store,person(store,'portal'),published=True)
    rejected=coverage(store,person(store,'rejected'),published=True)
    inactive=coverage(store,person(store,'inactive'))
    missing=coverage(store,person(store,'missing',email=False))
    scheduled=coverage(store,person(store,'scheduled'),published=True)
    scheduled_row=cached(bulk,scheduled,not_before='2027-01-01T00:00:00+00:00')
    past=coverage(store,person(store,'past'),start='2026-01-01T13:00',end='2026-01-01T22:00')
    foreign=coverage(store,person(store,'foreign',tenant='brand-b'),tenant='brand-b')
    with store.transaction() as db:
        for a,status in [(answered,'aceptada'),(rejected,'rechazada')]:
            a['status']=status;store.save(db,'brand-a','assignment',a)
        m=store.get(db,'brand-a','member',inactive['member_id']);m['active']=False;store.save(db,'brand-a','member',m)
    before=deepcopy(records(store,'calendar_sync'));foreign_before=deepcopy(records(store,'assignment','brand-b'))
    result=send(owner).get_json()['record']
    assert result==dict(queued=0,answered=2,busy=1,review=2)
    assert records(store,'calendar_sync')==before and records(store,'assignment','brand-b')==foreign_before
    assert len([c for c in records(store,'cost') if c['status']=='estimado'])==3


def test_conflicting_draft_needs_individual_review_and_other_members_still_queue(bulk):
    app,owner,crm,store,sync=bulk
    m=person(store,'clash');first=coverage(store,m,published=True)
    conflicting=coverage(store,m)
    eligible=coverage(store,person(store,'free'))
    result=send(owner).get_json()['record']
    assert result==dict(queued=1,answered=0,busy=0,review=2)
    current={a['id']:a for a in records(store,'assignment')}
    assert current[conflicting['id']]['status']=='borrador'
    assert current[eligible['id']]['status']=='pendiente'


def test_bulk_requires_owner_csrf_calendar_and_correct_brand(bulk):
    app,owner,crm,store,sync=bulk
    coverage(store,person(store,'safe'))
    assert owner.post('/api/teams/calendar/sync',json={'scope':'pending_invitations','key':'deny'}).status_code==403
    with owner.session_transaction() as s:s['user_email']='member@flow-qa-84982.com'
    assert send(owner).status_code==403
    with owner.session_transaction() as s:s.update(user_email='other@example.invalid',tenant_id='brand-b')
    assert send(owner).get_json()['record']['queued']==0
    assert records(store,'calendar_sync')==[]
    with owner.session_transaction() as s:s.update(user_email='owner@example.invalid',tenant_id='brand-a')
    app.config['FLOW_TEAMS_LOCAL']=True
    assert send(owner).status_code==400 and records(store,'calendar_sync')==[]


def test_actual_bulk_callback_validates_current_terms_before_any_provider_contact(bulk):
    app,owner,crm,store,sync=bulk
    a=coverage(store,person(store,'terms'))
    assert send(owner).get_json()['record']['queued']==1
    row=next(r for r in records(store,'calendar_sync') if r['identity']=='assignment:'+a['id'])
    assert sync.invitation_eligible('brand-a',row) is True
    with store.transaction() as db:
        current=store.get(db,'brand-a','assignment',a['id']);current['instructions']='Changed after queuing';store.save(db,'brand-a','assignment',current)
    assert 'condiciones cambiaron' in sync.invitation_eligible('brand-a',row)
    with store.transaction() as db:
        current['status']='aceptada';store.save(db,'brand-a','assignment',current)
    assert 'ya tiene respuesta' in sync.invitation_eligible('brand-a',row)


def test_uncertain_partial_http_result_keeps_same_queue_and_publication_on_retry(bulk,monkeypatch):
    app,owner,crm,store,sync=bulk
    a=coverage(store,person(store,'uncertain'))
    original=sync.enqueue
    calls=[]
    def uncertain(*args,**kwargs):
        result=original(*args,**kwargs)
        calls.append(True)
        if len(calls)==1:raise OSError('Lost HTTP result after durable enqueue')
        return result
    monkeypatch.setattr(sync,'enqueue',uncertain)
    with pytest.raises(OSError):send(owner,key='same-retry-key')
    before=deepcopy(records(store,'calendar_sync'))
    result=send(owner,key='same-retry-key').get_json()['record']
    assert result['busy']==1 and records(store,'calendar_sync')==before
    assert len(records(store,'notice'))==1
    assert records(store,'assignment')[0]['status']=='pendiente'


def test_bulk_does_not_restore_revoked_personal_access(bulk):
    app,owner,crm,store,sync=bulk
    member=person(store,'revoked');a=coverage(store,member)
    run(store,'member_revoke',id=member['id'],version=member['version'])
    result=send(owner).get_json()['record']
    assert result==dict(queued=0,answered=0,busy=0,review=1)
    assert records(store,'calendar_sync')==[] and records(store,'calendar_access')==[]
    assert records(store,'assignment')[0]['status']=='borrador'
