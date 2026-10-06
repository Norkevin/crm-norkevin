"""Imported steps use approval drafts; historical checks never prove delivery."""
from uuid import uuid4
import pytest


@pytest.fixture
def imported(auth_client):
    import app as crm
    key=uuid4().hex[:8];tenant='tenant-norkevin'
    person=dict(id='client-imported-'+key,first_name='Prueba',email='test@example.com',tenant_id=tenant)
    job=dict(id='job-imported-'+key,client_id=person['id'],nombre='Boda de prueba',tenant_id=tenant,
        boda_date='2034-05-14',status='Listo',studio_ninja_workflow=[
            dict(id='imported-google-'+key,name='Google Comments',status='done',source_stage='PRODUCTION',source_details='Auto send email\n15 Oct 2026'),
            dict(id='imported-close-'+key,name='Job complete',status='done',source_stage='PRODUCTION',source_details='',is_job_complete=True)])
    template=dict(id='template-imported-'+key,name='Google Comments',asunto='%client_name%, tu reseña',
        cuerpo='Gracias %client_name%. https://example.com/review',activo=True,tenant_id=tenant)
    with crm.app.test_request_context('/'):
        from flask import session
        session['tenant_id']=tenant
        for table,row in [('clients',person),('jobs',job),('email_templates',template)]:crm.store.upsert(table,row)
    return crm,auth_client,job,template


def complete(imported):
    crm,client,job,_=imported
    return client.post(f"/api/jobs/{job['id']}/imported-workflow/{job['studio_ninja_workflow'][0]['id']}/complete",json={})


def test_imported_email_queues_once_and_completes_only_after_approval(imported,monkeypatch):
    crm,client,job,template=imported
    from src.email_delivery import DeliveryResult
    sends=[]
    monkeypatch.setattr('src.mail_tracker.send_email',lambda *a,**k:sends.append(a) or DeliveryResult(ok=True,provider='test',message_id='fake',mode='test'))
    response=complete(imported)
    assert response.status_code==200,response.json
    mail_id=response.json['mail_id']
    assert response.json['delivery_status']=='pending' and sends==[]
    assert complete(imported).json['mail_id']==mail_id
    with crm.app.test_request_context('/'):
        from flask import session
        session['tenant_id']=job['tenant_id']
        mail=crm.store.get('pending_emails',mail_id)
        assert mail['template_id']==template['id'] and 'Prueba' in mail['subject']
        current=crm.get_job(job['id']);step=current['studio_ninja_workflow'][0]
        assert step['status']=='queued' and 'executed_at' not in step
        assert current['status']=='Listo' and current['studio_ninja_workflow'][1]['status']=='done'
    html=client.get('/jobs/'+job['id']).get_data(as_text=True)
    assert 'Pendiente de aprobación en Correos' in html
    assert client.post(f'/api/pending-emails/{mail_id}/send',json={}).status_code==200
    assert len(sends)==1
    with crm.app.test_request_context('/'):
        from flask import session
        session['tenant_id']=job['tenant_id']
        step=crm.get_job(job['id'])['studio_ninja_workflow'][0]
        assert step['status']=='done' and step['delivery_status']=='sent'
    assert complete(imported).json['mail_id']==mail_id and len(sends)==1


def test_missing_template_is_an_error_and_does_not_falsely_complete(imported):
    crm,client,job,template=imported
    with crm.app.test_request_context('/'):
        from flask import session
        session['tenant_id']=job['tenant_id']
        crm.store.delete('email_templates',template['id'])
        job['studio_ninja_workflow'][0]['status']='pending';crm.upsert_job(job)
    assert complete(imported).status_code==400
    with crm.app.test_request_context('/'):
        from flask import session
        session['tenant_id']=job['tenant_id']
        assert crm.get_job(job['id'])['studio_ninja_workflow'][0]['status']=='pending'
        assert not [m for m in crm.store.list('pending_emails') if m.get('job_id')==job['id']]


def test_imported_email_failure_and_retry_update_the_exact_step(imported,monkeypatch):
    crm,client,job,_=imported
    from src.email_delivery import DeliveryResult
    mail_id=complete(imported).json['mail_id']
    monkeypatch.setattr('src.mail_tracker.send_email',lambda *a,**k:DeliveryResult(ok=False,provider='test',error='test failure',mode='test'))
    assert client.post(f'/api/pending-emails/{mail_id}/send',json={}).status_code==400
    with crm.app.test_request_context('/'):
        from flask import session
        session['tenant_id']=job['tenant_id']
        assert crm.get_job(job['id'])['studio_ninja_workflow'][0]['status']=='failed'
    monkeypatch.setattr('src.mail_tracker.send_email',lambda *a,**k:DeliveryResult(ok=True,provider='test',message_id='fake',mode='test'))
    from src.mail_tracker import MailTracker
    with crm.app.test_request_context('/'):
        from flask import session
        session['tenant_id']=job['tenant_id']
        assert MailTracker().retry_failed(mail_id)['ok']
        assert crm.get_job(job['id'])['studio_ninja_workflow'][0]['status']=='done'


def test_imported_skip_discards_pending_and_foreign_tenant_cannot_trigger(imported):
    crm,client,job,_=imported
    mail_id=complete(imported).json['mail_id'];step_id=job['studio_ninja_workflow'][0]['id']
    assert client.post(f"/api/jobs/{job['id']}/steps/{step_id}/skip",json={}).status_code==200
    with crm.app.test_request_context('/'):
        from flask import session
        session['tenant_id']=job['tenant_id']
        assert crm.store.get('pending_emails',mail_id)['status']=='discarded'
        assert crm.get_job(job['id'])['studio_ninja_workflow'][0]['status']=='skipped'
    assert complete(imported).status_code==400
    with client.session_transaction() as session:session['tenant_id']='tenant-norkevin-photography';session['user_email']='norkevinfoto@gmail.com'
    assert complete(imported).status_code==404


def test_imported_questionnaire_queues_real_document_link_and_updates_both_on_approval(imported):
    crm,client,job,template=imported
    with crm.app.test_request_context('/'):
        from flask import session
        session['tenant_id']=job['tenant_id']
        job['studio_ninja_workflow'][0].update(name='Cuestionario de prueba',status='pending',source_details='Auto send questionnaire')
        template.update(name='Cuestionario de prueba',cuerpo='Completa [LINK AL CUESTIONARIO]')
        crm.upsert_job(job);crm.store.upsert('email_templates',template)
    response=complete(imported)
    assert response.status_code==200,response.json
    mail_id=response.json['mail_id'];document=response.json['questionnaire']
    with crm.app.test_request_context('/'):
        from flask import session
        session['tenant_id']=job['tenant_id']
        mail=crm.store.get('pending_emails',mail_id)
        assert '/questionnaires/'+document['id'] in mail['body'] and '[LINK AL CUESTIONARIO]' not in mail['body']
        assert crm.get_job(job['id'])['studio_ninja_workflow'][0]['status']=='queued'
    assert client.post(f'/api/pending-emails/{mail_id}/send',json={}).status_code==200
    with crm.app.test_request_context('/'):
        from flask import session
        session['tenant_id']=job['tenant_id']
        assert crm.store.get('questionnaires',document['id'])['delivery_status']=='sent'
        assert crm.get_job(job['id'])['studio_ninja_workflow'][0]['status']=='done'


def test_imported_manual_email_links_to_same_step_and_survives_repeated_submissions(imported):
    crm,client,job,template=imported;step_id=job['studio_ninja_workflow'][0]['id']
    payload=dict(step_id=step_id,template_id=template['id'],subject='Reseña de prueba',body='https://example.com/review')
    first=client.post(f"/api/jobs/{job['id']}/send-email",json=payload)
    assert first.status_code==200 and first.json['workflow']['queued']
    assert client.post(f"/api/jobs/{job['id']}/send-email",json=payload).json['mail_id']==first.json['mail_id']
    payload['step_id']='foreign-step'
    assert client.post(f"/api/jobs/{job['id']}/send-email",json=payload).status_code==400
