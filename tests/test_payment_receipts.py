from copy import deepcopy
from uuid import uuid4

import pytest

from conftest import login_as_tenant
from test_invoice_dynamic_payments import _make_job_with_two_installments


def setup_invoice(crm, paid=7500):
    job, first, second, invoice = _make_job_with_two_installments(crm, 'receipt')
    row = crm.store.get('payments', first)
    row.update(original_amount=7500, paid_amount=paid, amount=paid if paid >= 7500 else 7500-paid,
               status='Pagado' if paid >= 7500 else 'Pendiente', paid_date='2026-10-31',
               last_action='Pago registrado el 31 de octubre')
    crm.store.upsert('payments', row)
    crm.store.upsert('quotes', dict(id=row['quote_id'], job_id=job, client_id=row['client_id'],
                                  tenant_id=row['tenant_id'], precio_total=15000, status='Aceptada'))
    return first, second


def test_receipt_uses_existing_template_even_with_an_outstanding_invoice_balance(auth_client):
    import app as crm
    first, _ = setup_invoice(crm)
    template = dict(id='receipt-template', tenant_id='tenant-norkevin', name='PAGO RECIBIDO', activo=True,
                    asunto='Pago Recibido!', cuerpo='¡Hola %client_name%!\n\n¡Muchas gracias por tu pago!\n%invoice_link%')
    crm.store.upsert('email_templates', template)
    before = deepcopy({table: crm.store.list(table) for table in ('payments', 'pending_emails', 'mail_log', 'email_templates')})
    receipt = auth_client.get(f'/api/payments/{first}/send-preview?purpose=receipt')
    assert receipt.status_code == 200
    assert receipt.json['subject'] == 'Pago Recibido!'
    assert '¡Hola Invoice Dyn!' in receipt.json['body']
    assert 'Muchas gracias por tu pago' in receipt.json['body']
    assert 'saldo pendiente' not in receipt.json['body']
    assert '%invoice_link%' not in receipt.json['body']
    assert '[[INVOICE_LINK]]' in receipt.json['body']
    assert receipt.json['preview_url'] in receipt.json['html']
    assert {table: crm.store.list(table) for table in before} == before
    reminder = auth_client.get(f'/api/payments/{first}/send-preview').json
    assert 'Recordatorio de pago' in reminder['subject']
    assert 'saldo pendiente de Q7,500.00' in reminder['body']
    html = auth_client.get(f'/invoices/{first}').text
    assert f"sendPaymentReceipt(\"{first}\")" in html
    assert 'Enviar recordatorio de pago' in html


def test_partial_payment_can_send_a_receipt_and_still_request_the_balance(auth_client):
    import app as crm
    first, _ = setup_invoice(crm, paid=0)
    job_id = crm.store.get('payments', first)['job_id']
    recorded = auth_client.post(f'/api/jobs/{job_id}/record-payment', json={'amount': 2000, 'fecha_pago': '2026-10-31'})
    assert recorded.status_code == 200
    receipt = auth_client.get(f'/api/payments/{first}/send-preview?purpose=receipt')
    assert receipt.status_code == 200
    assert 'Hemos registrado tu pago' in receipt.json['body']
    assert 'está pagada' not in receipt.json['body']
    html = auth_client.get(f'/invoices/{first}').text
    assert f'sendPaymentReceipt("{first}")' in html
    assert f'sendInvoice("{first}")' in html
    assert 'Registrar pago' in html
    assert 'Generar link de pago' in html


@pytest.mark.parametrize('state', ['unpaid', 'credit', 'cancelled', 'team'])
def test_receipts_require_money_received_on_an_eligible_installment(auth_client, state):
    import app as crm
    first, _ = setup_invoice(crm, paid=0 if state in ('unpaid', 'credit') else 7500)
    row = crm.store.get('payments', first)
    if state == 'credit':
        row.update(status='Pagado', amount=0)
    elif state == 'cancelled':
        row['status'] = 'Cancelado'
    elif state == 'team':
        row['tipo'] = 'team_payment'
    crm.store.upsert('payments', row)
    before = deepcopy({table: crm.store.list(table) for table in ('payments', 'pending_emails', 'mail_log')})
    assert auth_client.get(f'/api/payments/{first}/send-preview?purpose=receipt').status_code == 400
    assert auth_client.post(f'/api/payments/{first}/send', json={'purpose': 'receipt'}).status_code == 400
    assert {table: crm.store.list(table) for table in before} == before


def test_receipt_template_and_payment_stay_in_their_account(auth_client):
    import app as crm
    first, _ = setup_invoice(crm)
    crm.store.upsert('email_templates', dict(id='foreign-receipt', tenant_id='tenant-foreign', name='PAGO RECIBIDO',
                    asunto='OTHER ACCOUNT', cuerpo='OTHER ACCOUNT', activo=True))
    crm.store.upsert('email_templates', dict(id='inactive-receipt', tenant_id='tenant-norkevin', name='PAGO RECIBIDO',
                    asunto='INACTIVE', cuerpo='INACTIVE', activo=False))
    preview = auth_client.get(f'/api/payments/{first}/send-preview?purpose=receipt').json
    assert 'Hemos registrado tu pago' in preview['body']
    login_as_tenant(auth_client, 'tenant-foreign', 'foreign@example.invalid')
    assert auth_client.get(f'/api/payments/{first}/send-preview?purpose=receipt').status_code == 404
    assert auth_client.post(f'/api/payments/{first}/send', json={'purpose': 'receipt'}).status_code == 404
    with auth_client.session_transaction() as session:
        session.clear()
    assert auth_client.get(f'/api/payments/{first}/send-preview?purpose=receipt').status_code in (302, 401)


def test_receipt_confirmation_preserves_edits_and_deduplicates_separately_from_reminders(auth_client):
    import app as crm
    first, _ = setup_invoice(crm)
    before = deepcopy(crm.store.get('payments', first))
    payload = dict(purpose='receipt', subject='Tu pago está registrado',
                   body='Gracias por el abono.\n[[INVOICE_LINK]]', send_request_id=str(uuid4()))
    response = auth_client.post(f'/api/payments/{first}/send', json=payload)
    assert response.status_code == 200, response.json
    mail = crm.store.get('pending_emails', response.json['mail_id'])
    assert mail['subject'] == payload['subject']
    assert mail['body'] == payload['body'].replace('[[INVOICE_LINK]]', response.json['invoice_url'])
    assert mail['source'] == 'manual:pago-receipt'
    assert crm.store.get('payments', first)['last_action'] == before['last_action']
    token = crm.store.get('payments', first)['public_token_hash']
    duplicate = auth_client.post(f'/api/payments/{first}/send', json=payload)
    assert duplicate.json['mail_id'] == mail['id']
    assert crm.store.get('payments', first)['public_token_hash'] == token
    reminder = auth_client.post(f'/api/payments/{first}/send', json={'send_request_id': payload['send_request_id']})
    assert reminder.json['mail_id'] != mail['id']
    after = crm.store.get('payments', first)
    for field in ('amount', 'paid_amount', 'original_amount', 'paid_date', 'status'):
        assert after[field] == before[field]


def test_unknown_email_purpose_is_rejected(auth_client):
    import app as crm
    first, _ = setup_invoice(crm)
    assert auth_client.get(f'/api/payments/{first}/send-preview?purpose=unknown').status_code == 400
    assert auth_client.post(f'/api/payments/{first}/send', json={'purpose': 'unknown'}).status_code == 400
