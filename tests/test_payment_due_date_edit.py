import pytest


def test_move_installment_preserves_amount_and_other_dates(auth_client, sample_business):
    import app as crm
    payment=sample_business['payment']
    crm.store.upsert('payments',dict(payment,id='other-installment',due_date='2034-01-01'))
    for due, status in [('2000-02-29','Late'),('2099-12-01','Pendiente')]:
        response=auth_client.post('/api/payments/'+payment['id']+'/update',json={'due_date':due})
        assert response.status_code==200
        updated=crm.store.get('payments',payment['id'])
        assert updated['due_date']==due and updated['status']==status
        assert updated['amount']==payment['amount']
        assert updated['original_amount']==payment['original_amount']
        assert crm.store.get('payments','other-installment')['due_date']=='2034-01-01'


@pytest.mark.parametrize('due',['',None,'2033-02-30','tomorrow','20330101',123])
def test_invalid_date_does_not_change_payment(auth_client,sample_business,due):
    import app as crm
    payment=sample_business['payment']
    response=auth_client.post('/api/payments/'+payment['id']+'/update',json={'due_date':due})
    assert response.status_code==400
    assert crm.store.get('payments',payment['id'])['due_date']==payment['due_date']


def test_due_date_rejects_other_brand_invoice_alias_and_paid(auth_client,sample_business):
    import app as crm
    payment=sample_business['payment']
    crm.store.upsert('payments',dict(payment,id='foreign-payment',tenant_id='tenant-norkevin-photography'))
    for identifier in ('foreign-payment',payment['invoice_id'],'missing'):
        assert auth_client.post('/api/payments/'+identifier+'/update',json={'due_date':'2034-01-01'}).status_code==404
    crm.store.upsert('payments',dict(payment,status='Pagado'))
    assert auth_client.post('/api/payments/'+payment['id']+'/update',json={'due_date':'2034-01-01'}).status_code==409


def test_date_editor_available_on_payment_invoice_and_job(auth_client,sample_business):
    for url in ['/payments?year=2033','/invoices/payment-fixture','/jobs/job-fixture']:
        response=auth_client.get(url)
        assert response.status_code==200
        html=response.get_data(as_text=True)
        assert 'Editar fecha' in html
        assert html.count('id="due-date-modal"')==1
        assert 'showModal()' in html


def test_date_change_discards_obsolete_queued_reminder(auth_client,sample_business):
    import app as crm
    from src.mail_tracker import get_tracker
    payment=sample_business['payment']
    mail=get_tracker().queue_email(to_email='fixture@example.invalid',subject='Vence hoy',body='Fecha anterior',
        tenant_id=payment['tenant_id'],job_id=payment['job_id'],source='auto:payment-reminder',
        idempotency_key='pago:payment-fixture:reminder:2033-01-01')
    crm.store.upsert('payments',dict(payment,reminder_mail_id=mail['id'],reminder_queued_at='2033-01-01'))
    response=auth_client.post('/api/payments/'+payment['id']+'/update',json={'due_date':'2034-01-01'})
    assert response.status_code==200
    assert crm.store.get('pending_emails',mail['id'])['status']=='discarded'
    assert not crm.store.get('payments',payment['id']).get('reminder_queued_at')
