"""Original service documents never recreate acceptance, payments or mail."""
from copy import deepcopy
from uuid import uuid4
import pytest


@pytest.fixture
def original_services(auth_client):
    import app as crm
    sid='123456';suffix=uuid4().hex[:8];tenant='tenant-norkevin'
    job=dict(id='job-source-'+suffix,tenant_id=tenant,nombre='Boda de prueba',boda_date='2026-11-14',
        status='En produccion',studio_ninja_active_source_id=sid,price_total=100,
        studio_ninja_workflow=[dict(id='event',name='Boda',status='pending',source_stage='PRODUCTION')],
        notes='Notas anteriores')
    payment=dict(id='pay-source-'+suffix,tenant_id=tenant,job_id=job['id'],invoice_id='20251008',
        status='Pendiente',amount=60,original_amount=100,paid_amount=40,due_date='2026-11-14',
        studio_ninja_url='https://app.studioninja.co/invoices/789')
    for table,row in [('jobs',job),('payments',payment)]:crm.store.upsert(table,row)
    description='2 Fotógrafos\n12 horas de cobertura\n2 Camarógrafos\nSave the date: 2 horas'
    doc=dict(kind='quote',url=f'https://app.studioninja.co/jobs/{sid}/quotes/pick-and-choose/456',number='20251008',
        total=100,items=[dict(name='Paquete original',description=description)],source_text='Texto original de prueba')
    invoice=dict(doc,kind='invoice',url=payment['studio_ninja_url'])
    entry=dict(existing_job_id=job['id'],source_id=sid,documents=[doc,invoice])
    return crm,auth_client,job,payment,dict(mode='recover_service_documents',jobs=[entry])


def recover(fixture,data=None):
    _,client,_,_,payload=fixture
    return client.post('/api/admin/import-studio-ninja',json={'confirm':'IMPORTAR','payload':data or payload})


def test_recovery_is_idempotent_and_preserves_every_business_record(original_services):
    crm,client,job,payment,payload=original_services
    tables=('payments','quotes','contracts','questionnaires','pending_emails','mail_log','payment_schedules')
    before={t:deepcopy(crm.store.list(t)) for t in tables}
    money=crm._job_payment_summary(job,[payment])
    assert recover(original_services).status_code==200
    saved=crm.get_job(job['id']);assert len(saved['studio_ninja_service_documents'])==2
    assert {k:v for k,v in saved.items() if not k.startswith('studio_ninja_services_') and k!='studio_ninja_service_documents'}==job
    assert {t:crm.store.list(t) for t in tables}==before
    assert crm._job_payment_summary(saved,[payment])==money
    repeated=recover(original_services).json
    assert not repeated['updated'] and len(repeated['skipped'])==1
    html=client.get('/jobs/'+job['id']).get_data(as_text=True)
    assert 'Cotizaciones <span class="sn-tab-count">1</span>' in html
    assert 'Ver servicios contratados' in html
    page=client.get(f"/jobs/{job['id']}/source-document/quote-456")
    assert page.status_code==200
    assert '2 Fotógrafos' in page.get_data(as_text=True) and '2 Camarógrafos' in page.get_data(as_text=True)
    assert 'Aceptar cotización' not in page.get_data(as_text=True)
    invoice_html=client.get('/invoices/'+payment['id']).get_data(as_text=True)
    assert '2 Fotógrafos' in invoice_html
    doc=crm._invoice_document(payment['id'])
    assert doc['fuente_conceptos']=='studio_ninja_invoice'
    assert doc['total']==100 and doc['pendiente']==60 and doc['pagado']==40
    assert any('2 Camarógrafos' in s['texto'] for g in doc['grupos'] for s in g['servicios'])


@pytest.mark.parametrize('change', ['other_year','wrong_source','wrong_quote_job','foreign_invoice','completed','invalid_amount','invalid_items'])
def test_recovery_validates_entire_batch_before_any_write(original_services,change):
    crm,_,job,_,payload=original_services
    bad=deepcopy(payload['jobs'][0]);badjob=dict(job,id=job['id']+'-bad',studio_ninja_payment_invoices=[{'url':payload['jobs'][0]['documents'][1]['url']}])
    bad['existing_job_id']=badjob['id']
    if change=='other_year':badjob['boda_date']='2027-01-01'
    elif change=='wrong_source':bad['source_id']='654321'
    elif change=='wrong_quote_job':bad['documents'][0]['url']='https://app.studioninja.co/jobs/654321/quotes/fixed/456'
    elif change=='foreign_invoice':bad['documents'][1]['url']='https://app.studioninja.co/invoices/999'
    elif change=='completed':badjob.update(status='Listo',boda_date='2026-01-01')
    elif change=='invalid_amount':bad['documents'][0]['total']='not a number'
    elif change=='invalid_items':bad['documents'][0]['items']=None
    crm.store.upsert('jobs',badjob)
    before=deepcopy(crm.store.list('jobs'));batch=deepcopy(payload);batch['jobs'].append(bad)
    assert recover(original_services,batch).status_code==400
    assert crm.store.list('jobs')==before


def test_source_view_is_private_tenant_scoped_and_escapes_original_content(original_services):
    crm,client,job,_,payload=original_services
    payload['jobs'][0]['documents'][0]['items'][0]['description']='<script>bad()</script>\n2 Fotógrafos'
    assert recover(original_services).status_code==200
    path=f"/jobs/{job['id']}/source-document/quote-456"
    html=client.get(path).get_data(as_text=True)
    assert '&lt;script&gt;bad()&lt;/script&gt;' in html and '<script>bad()' not in html
    with client.session_transaction() as session:session['tenant_id']='tenant-norkevin-photography';session['user_email']='norkevinfoto@gmail.com'
    assert client.get(path).status_code==404 and recover(original_services).status_code==400
    with crm.app.test_client() as anonymous:assert anonymous.get(path).status_code in (302,401,403)


def test_recovered_internal_invoice_displays_whole_invoice_and_never_other_jobs(original_services):
    crm,client,job,payment,_=original_services
    next_row=dict(payment,id=payment['id']+'-next',amount=100,original_amount=100,paid_amount=0,due_date='2026-12-14')
    foreign=dict(next_row,id='foreign-'+next_row['id'],job_id='other-job',amount=900,original_amount=900)
    crm.store.upsert('payments',next_row);crm.store.upsert('payments',foreign)
    assert recover(original_services).status_code==200
    html=client.get('/invoices/'+payment['id']).get_data(as_text=True)
    assert 'Q200.00' in html and 'Q160.00' in html and 'Q40.00' in html
    assert 'Q900.00' not in html and '14 de diciembre de 2026' in html
    assert crm.store.get('payments',payment['id'])==payment
    assert crm.store.get('payments',next_row['id'])==next_row
