from datetime import datetime,timedelta
from uuid import uuid4
import pytest


@pytest.fixture
def automatic(auth_client,monkeypatch):
    import app as a
    from src.email_delivery import DeliveryResult
    monkeypatch.setenv('WORKFLOW_AUTO_SEND_BRANDS','astral,norkevin')
    monkeypatch.setattr(a.workflow_engine,'instances',{})
    sent=[]
    monkeypatch.setattr('src.mail_tracker.send_email',lambda *args,**kwargs: sent.append((args,kwargs)) or DeliveryResult(ok=True,provider='test',message_id='synthetic',mode='test'))
    saved={}
    def setup(tenant,kind='lead',created=datetime(2026,10,14,10,0)):
        token=a._workflow_tenant.set(tenant)
        try:
            if tenant not in saved:saved[tenant]=a.store.get_tenant_dict('workflow_templates',tenant)
            key=uuid4().hex[:8];tpl='auto-tpl-'+key;sid=kind+'-auto-'+key;cid='client-'+key
            a.store.upsert('clients',dict(id=cid,tenant_id=tenant,first_name='Ana',email=key+'@example.com'))
            a.store.upsert('email_templates',dict(id=tpl,tenant_id=tenant,name='Automático de prueba',asunto='Hola',cuerpo='Mensaje de prueba',activo=True))
            factory=a.LEAD_WORKFLOW if kind=='lead' else a.PRODUCTION_WORKFLOW
            workflow=factory(tenant).to_dict()
            workflow['steps']=[dict(id='auto_step',name='Correo automático',action_type='send_email',email_template_id=tpl,due_date=dict(mode='after_creation',amount=3,unit='hours',relative_to='lead_created' if kind=='lead' else 'job_created'))]
            a.store.save_tenant_dict('workflow_templates',{workflow['id']:workflow},tenant)
            record=dict(id=sid,tenant_id=tenant,client_id=cid,nombre='Prueba',email=key+'@example.com',status='Nuevo' if kind=='lead' else 'Confirmado',created=created.isoformat())
            (a.upsert_lead if kind=='lead' else a.upsert_job)(record)
            instance=a.workflow_engine.start_workflow(factory(tenant),kind,sid,tenant_id=tenant,auto_prepare=True)
            instance.trigger_at=created
            return record,instance
        finally:a._workflow_tenant.reset(token)
    yield a,setup,sent
    for tenant,value in saved.items():a.store.save_tenant_dict('workflow_templates',value,tenant)


@pytest.mark.parametrize('tenant',['tenant-norkevin','tenant-norkevin-photography'])
@pytest.mark.parametrize('kind',['lead','job'])
def test_due_mail_delivers_once_in_each_brand_through_the_scheduled_queue(automatic,tenant,kind):
    a,setup,sent=automatic;record,instance=setup(tenant,kind);due=instance.trigger_at+timedelta(hours=3)
    assert not a._prepare_due_workflow_emails(tenant_id=tenant,now=due-timedelta(minutes=1))
    queued=a._prepare_due_workflow_emails(tenant_id=tenant,now=due)
    assert len(queued)==1
    assert not a._send_due_workflow_emails(now=due-timedelta(minutes=1))
    result=a._send_due_workflow_emails(now=due)
    assert len(result)==1 and result[0]['ok'] is True and len(sent)==1
    assert sent[0][1]['metadata']['tenant_id']==tenant
    assert instance.step_states['auto_step']==a.StepStatus.DONE
    assert not a._send_due_workflow_emails(now=due+timedelta(days=1)) and len(sent)==1


def test_production_default_enables_only_the_two_authorized_brands(automatic,monkeypatch):
    a,_,_=automatic;monkeypatch.delenv('WORKFLOW_AUTO_SEND_BRANDS')
    assert a._workflow_mail_auto_enabled('tenant-norkevin')
    assert a._workflow_mail_auto_enabled('tenant-norkevin-photography')
    assert not a._workflow_mail_auto_enabled('tenant-other')


@pytest.mark.parametrize('case',['disabled','skipped','cancelled','failed','wrong_instance','foreign_account'])
def test_ineligible_or_failed_mail_never_auto_delivers(automatic,monkeypatch,case):
    a,setup,sent=automatic;tenant='tenant-norkevin';record,instance=setup(tenant);due=instance.trigger_at+timedelta(hours=3)
    row=a._prepare_due_workflow_emails(tenant_id=tenant,now=due)[0]
    token=a._workflow_tenant.set(tenant)
    try:
        mail=a.store.get('pending_emails',row['mail_id'])
        if case=='disabled':monkeypatch.setenv('WORKFLOW_AUTO_SEND_BRANDS','')
        if case=='skipped':instance.step_states['auto_step']=a.StepStatus.SKIPPED
        if case=='cancelled':instance.status=a.WorkflowStatus.CANCELLED
        if case=='failed':mail['status']='failed'
        if case=='wrong_instance':mail['workflow_instance_id']='missing-instance'
        if case=='foreign_account':instance.tenant_id='tenant-norkevin-photography'
        a.store.upsert('pending_emails',mail)
    finally:a._workflow_tenant.reset(token)
    assert not a._send_due_workflow_emails(now=due) and sent==[]


def test_failed_delivery_is_recorded_without_an_automatic_duplicate_retry(automatic,monkeypatch):
    a,setup,sent=automatic;tenant='tenant-norkevin';_,instance=setup(tenant);due=instance.trigger_at+timedelta(hours=3)
    row=a._prepare_due_workflow_emails(tenant_id=tenant,now=due)[0]
    from src.email_delivery import DeliveryResult
    monkeypatch.setattr('src.mail_tracker.send_email',lambda *args,**kwargs: sent.append(args) or DeliveryResult(ok=False,provider='test',error='synthetic failure',mode='test'))
    result=a._send_due_workflow_emails(now=due)
    assert len(result)==1 and result[0]['ok'] is False
    assert instance.step_states['auto_step']==a.StepStatus.FAILED
    assert not a._send_due_workflow_emails(now=due+timedelta(minutes=1)) and len(sent)==1


def test_older_queued_mail_is_sent_first_with_a_bounded_batch(automatic):
    a,setup,sent=automatic;tenant='tenant-norkevin';ids=[]
    for i in range(3):
        _,instance=setup(tenant,created=datetime(2026,10,14,10,i))
        row=a._prepare_due_workflow_emails(tenant_id=tenant,now=datetime(2026,10,14,15))[0]
        token=a._workflow_tenant.set(tenant)
        try:
            mail=a.store.get('pending_emails',row['mail_id']);mail['created_at']=datetime(2026,10,14,10,i).isoformat();a.store.upsert('pending_emails',mail);ids.append(mail['id'])
        finally:a._workflow_tenant.reset(token)
    first=a._send_due_workflow_emails(now=datetime(2026,10,14,15),limit=2)
    assert [m['mail_id'] for m in first]==ids[:2] and len(sent)==2
    assert [m['mail_id'] for m in a._send_due_workflow_emails(now=datetime(2026,10,14,15))]==ids[2:]


def test_disabled_legacy_workflow_only_enrolls_upcoming_due_steps(automatic):
    a,setup,sent=automatic;tenant='tenant-norkevin'
    _,old=setup(tenant,created=datetime(2025,1,1));old.auto_prepare=False
    assert not a._prepare_due_workflow_emails(tenant_id=tenant,now=datetime(2026,10,14,15))
    _,upcoming=setup(tenant);upcoming.auto_prepare=False
    assert len(a._prepare_due_workflow_emails(tenant_id=tenant,now=datetime(2026,10,14,15)))==1
    assert len(a._send_due_workflow_emails(now=datetime(2026,10,14,15)))==1

@pytest.mark.parametrize('tenant',['tenant-norkevin','tenant-norkevin-photography'])
def test_imported_upcoming_steps_deliver_and_old_unqueued_campaigns_stay_untouched(automatic,tenant):
    a,setup,sent=automatic;job,instance=setup(tenant,'job')
    token=a._workflow_tenant.set(tenant)
    try:
        template=a.PRODUCTION_WORKFLOW(tenant).steps[0].email_template_id
        job['studio_ninja_workflow']=[
            dict(id='old',name='Viejo',status='pending',source_stage='PRODUCTION',action_type='send_email',email_template_id=template,scheduled='2025-01-01T10:00:00'),
            dict(id='upcoming',name='Próximo',status='pending',source_stage='PRODUCTION',action_type='send_email',email_template_id=template,scheduled='2026-10-14T13:00:00')]
        a.upsert_job(job)
    finally:a._workflow_tenant.reset(token)
    assert not a._prepare_due_workflow_emails(tenant_id=tenant,now=datetime(2026,10,14,12))
    rows=a._prepare_due_workflow_emails(tenant_id=tenant,now=datetime(2026,10,14,13))
    assert len(rows)==1 and rows[0]['step_id']=='upcoming'
    assert len(a._send_due_workflow_emails(now=datetime(2026,10,14,13)))==1 and len(sent)==1
    assert not a._prepare_due_workflow_emails(tenant_id=tenant,now=datetime(2026,10,15))
    assert job['studio_ninja_workflow'][0]['status']=='pending'


def test_calendar_dates_use_guatemala_while_creation_delays_keep_their_clock(automatic,monkeypatch):
    a,_,_=automatic
    from datetime import timezone
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            moment=datetime(2026,10,14,5,0,tzinfo=timezone.utc)
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)
    monkeypatch.setattr(a,'datetime',Clock)
    assert not a._workflow_step_is_due({'scheduled':'2026-10-14T00:00:00','schedule_basis':'calendar'})
    assert a._workflow_step_is_due({'scheduled':'2026-10-13T23:00:00','schedule_basis':'calendar'})
    assert a._workflow_step_is_due({'scheduled':'2026-10-14T05:00:00','schedule_basis':'server'})
    assert not a._workflow_step_is_due({'scheduled':'2026-10-14T05:01:00','schedule_basis':'server'})
    assert a._workflow_step_is_due({'scheduled':'2026-10-14T05:00:00+00:00','schedule_basis':'calendar'})
