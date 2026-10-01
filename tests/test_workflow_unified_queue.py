"""Template edits, due queue, documents and tenant isolation use the same workflow."""
from datetime import datetime, timedelta
import uuid
import pytest


@pytest.fixture
def flow(auth_client, monkeypatch):
    import app as a
    tenant = 'tenant-norkevin'
    saved = a.store.get_tenant_dict('workflow_templates', tenant)
    monkeypatch.setattr(a.workflow_engine, 'instances', {})
    token = a._workflow_tenant.set(tenant)
    a.store.save_tenant_dict('workflow_templates', {}, tenant)
    template_id = 'tpl-unified-' + uuid.uuid4().hex[:8]
    a.store.upsert('email_templates', {'id': template_id, 'name': 'Prueba integrada',
        'asunto': 'Hola %client_name%', 'cuerpo': 'Mensaje de prueba %client_name%', 'tenant_id': tenant})
    yield a, auth_client, tenant, template_id
    a.store.save_tenant_dict('workflow_templates', saved, tenant)
    a._workflow_tenant.reset(token)


def subject(a, tenant, kind='lead'):
    sid = kind + '-unified-' + uuid.uuid4().hex[:8]
    client_id = 'client-' + sid
    a.store.upsert('clients', {'id': client_id, 'first_name': 'Ana', 'email': sid+'@example.com', 'tenant_id': tenant})
    record = {'id': sid, 'nombre': 'Ana', 'email': sid+'@example.com', 'tenant_id': tenant,
              'client_id': client_id, 'created': '2026-10-01T00:00:00', 'status': 'Nuevo'}
    a.store.upsert('leads' if kind == 'lead' else 'jobs', record)
    if kind == 'lead':
        inst = a.trigger_workflow_for_lead(sid, 'Ana', tenant)
    else:
        inst = a.trigger_workflow_for_quote_accepted('', 'Ana', sid, tenant)
    inst.trigger_at = datetime(2026, 10, 1, 10, 13)
    return record, inst


def configure(a, tenant, tpl, kind='lead', action='send_email', mode='after_creation', amount=3):
    workflow = (a.LEAD_WORKFLOW if kind == 'lead' else a.PRODUCTION_WORKFLOW)(tenant).to_dict()
    workflow['steps'] = [{'id': 'test_step', 'name': 'Paso de prueba', 'action_type': action,
        'email_template_id': tpl, 'due_date': {'mode': mode, 'amount': amount, 'unit': 'hours',
        'relative_to': 'lead_created' if kind == 'lead' else 'job_created'}}]
    a.store.save_tenant_dict('workflow_templates', {workflow['id']: workflow}, tenant)
    return workflow


def test_due_queue_uses_exact_creation_time_and_is_idempotent(flow):
    a, client, tenant, tpl = flow
    configure(a, tenant, tpl)
    lead, inst = subject(a, tenant)
    assert a.compute_workflow_steps_for_lead(lead)[0][0]['scheduled'] == '2026-10-01T13:13:00'
    assert not a._prepare_due_workflow_emails(now=inst.trigger_at + timedelta(hours=2))
    result = a._prepare_due_workflow_emails(now=inst.trigger_at + timedelta(hours=3))
    assert len(result) == 1
    mail = a.store.get('pending_emails', result[0]['mail_id'])
    assert mail['status'] == 'pending' and mail['template_id'] == tpl
    assert 'Ana' in mail['body'] and '%client_name%' not in mail['body']
    assert inst.step_states['test_step'] == a.StepStatus.QUEUED
    assert not a._prepare_due_workflow_emails(now=inst.trigger_at + timedelta(days=2))
    a._apply_workflow_delivery(inst, 'test_step', {**mail, 'status': 'sent'})
    assert a.compute_workflow_steps_for_lead(lead)[0][0]['status'] == 'done'


@pytest.mark.parametrize('case', ['legacy', 'manual', 'skipped', 'converted', 'missing_template', 'foreign_template'])
def test_queue_does_not_revive_or_cross_accounts(flow, case):
    a, client, tenant, tpl = flow
    if case == 'foreign_template':
        token = a._workflow_tenant.set('tenant-other')
        a.store.upsert('email_templates', {'id': 'foreign-unified', 'tenant_id': 'tenant-other', 'cuerpo': 'Secret'})
        a._workflow_tenant.reset(token)
        tpl = 'foreign-unified'
    if case == 'missing_template':
        tpl = 'missing-unified'
    configure(a, tenant, tpl, mode='manual' if case == 'manual' else 'after_creation')
    lead, inst = subject(a, tenant)
    if case == 'legacy': inst.auto_prepare = False
    if case == 'skipped': inst.step_states['test_step'] = a.StepStatus.SKIPPED
    if case == 'converted': inst.status = a.WorkflowStatus.COMPLETED
    assert not a._prepare_due_workflow_emails(now=inst.trigger_at + timedelta(days=3))


@pytest.mark.parametrize('action,collection,path', [('send_contract','contracts','/contracts/'),
    ('send_questionnaire','questionnaires','/questionnaires/')])
def test_production_queue_uses_selected_template_and_real_document_link(flow, action, collection, path):
    a, client, tenant, tpl = flow
    configure(a, tenant, tpl, kind='job', action=action)
    job, inst = subject(a, tenant, 'job')
    result = a._prepare_due_workflow_emails(now=inst.trigger_at + timedelta(hours=3))
    assert len(result) == 1
    mail = a.store.get('pending_emails', result[0]['mail_id'])
    assert mail['template_id'] == tpl and 'Mensaje de prueba Ana' in mail['body']
    assert 'https://flowingcrm.com'+path in mail['body']
    docs = [d for d in a.store.list(collection) if d.get('job_id') == job['id']]
    assert len(docs) == 1 and docs[0]['delivery_status'] == 'pending'
    assert not a._prepare_due_workflow_emails(now=inst.trigger_at + timedelta(days=5))
    assert len([d for d in a.store.list(collection) if d.get('job_id') == job['id']]) == 1


def test_editor_and_existing_lead_share_template_without_cross_tenant_edit(flow):
    a, client, tenant, tpl = flow
    workflow = configure(a, tenant, tpl)
    lead, inst = subject(a, tenant)
    workflow['steps'][0]['due_date']['amount'] = 7
    response = client.put('/api/workflow/template/lead_workflow_v1', json=workflow)
    assert response.status_code == 200
    step = a.compute_workflow_steps_for_lead(lead)[0][0]
    assert step['scheduled'] == '2026-10-01T17:13:00'
    assert step['email_template_name'] == 'Prueba integrada'
    assert a.workflow_engine.get_template('lead_workflow_v1', tenant).steps[0].due_date.amount == 7
    assert a.LEAD_WORKFLOW('tenant-other').steps[0].id != 'test_step'
    assert b'Prueba integrada' in client.get('/workflow-editor').data


def test_queue_enrollment_survives_restart_but_legacy_defaults_off(flow):
    a, client, tenant, tpl = flow
    configure(a, tenant, tpl)
    lead, inst = subject(a, tenant)
    a.workflow_engine._save_to_storage()
    restored = a.WorkflowEngine(persistence_store=a.store)
    assert restored.get_instance(inst.id).auto_prepare is True
    data = a.store.get_dict('workflow_instances')
    del data[inst.id]['auto_prepare']
    a.store.save_dict('workflow_instances', data)
    assert a.WorkflowEngine(persistence_store=a.store).get_instance(inst.id).auto_prepare is False


def test_event_relative_and_zero_delay_persist(flow):
    a, client, tenant, tpl = flow
    workflow = configure(a, tenant, tpl, kind='job', mode='after_event', amount=24)
    workflow['steps'][0]['due_date']['relative_to'] = 'before_boda'
    assert client.put('/api/workflow/template/production_workflow_v1', json=workflow).status_code == 200
    job, inst = subject(a, tenant, 'job')
    job['boda_date'] = '2026-12-01'
    assert a.compute_workflow_steps_for_job(job)[0][0]['scheduled'] == '2026-11-30T00:00:00'
    workflow['steps'][0]['due_date'] = {'mode':'after_creation', 'amount':0, 'unit':'minutes','relative_to':'job_created'}
    assert client.put('/api/workflow/template/production_workflow_v1', json=workflow).status_code == 200
    assert a.compute_workflow_steps_for_job(job)[0][0]['scheduled'] == inst.trigger_at.isoformat()


def test_background_runner_resolves_each_tenant_and_restores_context(flow):
    a, client, tenant, tpl = flow
    configure(a, tenant, tpl)
    own, first = subject(a, tenant)
    other_tenant = 'tenant-unified-second'
    token = a._workflow_tenant.set(other_tenant)
    other_tpl = 'tpl-second-unified'
    a.store.upsert('email_templates', {'id':other_tpl, 'tenant_id':other_tenant,
        'name':'Segunda cuenta', 'asunto':'Otro', 'cuerpo':'Solo segunda cuenta'})
    configure(a, other_tenant, other_tpl)
    other, second = subject(a, other_tenant)
    a._workflow_tenant.reset(token)
    token = a._workflow_tenant.set(None)
    try:
        with a.app.app_context():
            result = a._prepare_due_workflow_emails(now=first.trigger_at + timedelta(hours=3))
            assert a._workflow_tenant.get() is None
            assert len(result) == 2
        for tid, instance, template_id in [(tenant, first, tpl),(other_tenant, second, other_tpl)]:
            scoped = a._workflow_tenant.set(tid)
            mail = next(m for m in a.store.list('pending_emails') if m.get('workflow_instance_id') == instance.id)
            assert mail['tenant_id'] == tid and mail['template_id'] == template_id
            a._workflow_tenant.reset(scoped)
    finally:
        a._workflow_tenant.reset(token)
        a.store.save_tenant_dict('workflow_templates', {}, other_tenant)


def test_concurrent_preparation_keeps_one_pending_message(flow):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from src.mail_tracker import get_tracker
    a, client, tenant, tpl = flow
    lead, inst = subject(a, tenant)
    barrier = Barrier(2)
    def prepare():
        token = a._workflow_tenant.set(tenant)
        try:
            barrier.wait(timeout=3)
            return get_tracker().queue_email(to_email=lead['email'], subject='Concurrent', body='Test',
                tenant_id=tenant, lead_id=lead['id'], client_id=lead['client_id'], template_id=tpl,
                idempotency_key='concurrent:'+lead['id'])['id']
        finally:
            a._workflow_tenant.reset(token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: prepare(), range(2)))
    assert ids[0] == ids[1]


def test_manual_production_entry_points_share_dispatcher(flow):
    a, client, tenant, tpl = flow
    configure(a, tenant, tpl, kind='job', action='send_contract')
    job, inst = subject(a, tenant, 'job')
    first = client.post('/api/jobs/'+job['id']+'/trigger-step', json={'step_id':'test_step'})
    second = client.post('/api/jobs/'+job['id']+'/production-step', json={'step_id':'test_step'})
    assert first.status_code == second.status_code == 200
    assert first.json['mail_id'] == second.json['mail_id']
    assert inst.step_states['test_step'] == a.StepStatus.QUEUED
    mail = a.store.get('pending_emails', first.json['mail_id'])
    contract = next(d for d in a.store.list('contracts') if d.get('job_id') == job['id'])
    a._sync_mail_delivery({**mail, 'status':'sent'})
    assert a.store.get('contracts', contract['id'])['delivery_status'] == 'sent'
    assert inst.step_states['test_step'] == a.StepStatus.DONE
