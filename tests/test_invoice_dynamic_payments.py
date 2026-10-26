"""Kevin: en la pagina de la factura (/invoices/<id>), el boton 'Record
Payment' llamaba al endpoint viejo /api/payments/<id>/pay, que solo
sobreescribe el monto de ESA fila -- si el cliente pagaba mas que esa
cuota, el sobrante se perdia en vez de abonar a las siguientes, y el
total dejaba de sumar el contrato original. El Job ya usaba el endpoint
inteligente /api/jobs/<job_id>/record-payment; la factura ahora tambien."""
import uuid


def _make_job_with_two_installments(app_module, suffix):
    client_id = f'client-inv-dyn-{suffix}'
    job_id = f'job-inv-dyn-{suffix}'
    quote_id = f'quote-inv-dyn-{suffix}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Invoice', 'last_name': 'Dyn',
        'email': 'invoicedyn@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Invoice Dyn',
        'tenant_id': 'tenant-norkevin',
    })
    pay1_id = 'pay-inv-dyn-1-' + uuid.uuid4().hex[:6]
    pay2_id = 'pay-inv-dyn-2-' + uuid.uuid4().hex[:6]
    invoice_id = 'INV-DYN-' + suffix
    for pid, due, cuota in [(pay1_id, '2027-01-01', 1), (pay2_id, '2027-02-01', 2)]:
        app_module.store.upsert('payments', {
            'id': pid, 'client_id': client_id, 'job_id': job_id, 'quote_id': quote_id,
            'invoice_id': invoice_id, 'amount': 7500.0, 'status': 'Pendiente',
            'due_date': due, 'cuota': cuota, 'concepto': 'Cuota', 'tenant_id': 'tenant-norkevin',
        })
    return job_id, pay1_id, pay2_id, invoice_id


def test_invoice_page_exposes_job_id_for_smart_record_payment(auth_client):
    import app as app_module
    job_id, pay1_id, pay2_id, invoice_id = _make_job_with_two_installments(app_module, 'a')

    resp = auth_client.get(f'/invoices/{invoice_id}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert f"var JOB_ID = {job_id!r}".replace("'", '"') in html or f'"{job_id}"' in html
    assert "/api/jobs/' + JOB_ID + '/record-payment" in html


def test_invoice_record_payment_endpoint_redistributes_surplus(auth_client):
    """No pasa por el HTML -- pega directo al endpoint que ahora usa la
    factura, confirmando que un pago de mas marca la cuota pagada por el
    monto REAL recibido y reparte el sobrante en la otra cuota (version
    final confirmada -- ver test_smart_payment_distribution.py)."""
    import app as app_module
    job_id, pay1_id, pay2_id, invoice_id = _make_job_with_two_installments(app_module, 'b')

    resp = auth_client.post(f'/api/jobs/{job_id}/record-payment', json={
        'amount': 10000, 'fecha_pago': '2027-01-01',
    })
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True

    rows = [p for p in app_module.store.list('payments') if p.get('job_id') == job_id]
    row1 = next(r for r in rows if r['id'] == pay1_id)
    row2 = next(r for r in rows if r['id'] == pay2_id)
    assert row1['status'] == 'Pagado'
    assert row1['amount'] == 10000.0, 'la cuota pagada muestra el monto REAL recibido'
    assert row2['status'] == 'Pendiente'
    assert row2['amount'] == 5000.0, 'el sobrante de Q2500 se resta de la otra cuota (7500-2500)'

    # El total original del contrato (suma de original_amount) se mantiene fijo siempre.
    assert sum(app_module._row_original_amount(r) for r in rows) == 15000.0


def test_send_invoice_preview_does_not_send(auth_client):
    import app as app_module
    job_id, pay1_id, pay2_id, invoice_id = _make_job_with_two_installments(app_module, 'c')

    before = {table: app_module.store.list(table) for table in ('payments', 'pending_emails', 'mail_log')}
    resp = auth_client.get(f'/api/payments/{pay1_id}/send-preview')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['to_email'] == 'invoicedyn@example.com'
    assert invoice_id in data['subject']
    assert 'Q15,000.00' in data['body']
    assert 'recordatorio automático' in data['body']
    assert 'pagarla' not in data['body']
    assert data['preview_url'].endswith(f'/invoices/{pay1_id}/documento')
    assert '[[INVOICE_LINK]]' not in data['html']
    for table, records in before.items():
        assert app_module.store.list(table) == records

    pay = app_module.store.get('payments', pay1_id)
    assert not pay.get('sent_at'), 'la vista previa no debe marcar la factura como enviada'


def test_send_invoice_respects_edited_subject_and_body(auth_client):
    """STAGE 2 (agosto 2026): api_pago_send ya no entrega de inmediato --
    encola con queue_email(), asi que el mensaje editado a mano vive en
    pending_emails (esperando aprobacion) en vez de mail_log."""
    import app as app_module
    job_id, pay1_id, pay2_id, invoice_id = _make_job_with_two_installments(app_module, 'd')

    resp = auth_client.post(f'/api/payments/{pay1_id}/send', json={
        'subject': 'Asunto editado por Kevin',
        'body': 'Mensaje editado a mano.',
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True

    mail = next(m for m in app_module.store.list('pending_emails') if m.get('id') == data['mail_id'])
    assert mail['status'] == 'pending'
    assert mail['subject'] == 'Asunto editado por Kevin'
    assert mail['body'] == 'Mensaje editado a mano.'

    pay = app_module.store.get('payments', pay1_id)
    assert pay.get('queued_at')
    assert not pay.get('sent_at')


def test_reminder_uses_invoice_balance_even_when_selected_installment_is_paid(auth_client):
    import app as crm
    _, first_id, second_id, _ = _make_job_with_two_installments(crm, 'balance')
    first = crm.store.get('payments', first_id)
    first.update(status='Pagado', paid_amount=7500)
    crm.store.upsert('payments', first)
    second = crm.store.get('payments', second_id)
    second.update(amount=1900, original_amount=7500)
    crm.store.upsert('payments', second)

    preview = auth_client.get(f'/api/payments/{first_id}/send-preview').get_json()
    assert 'saldo pendiente de Q1,900.00' in preview['body']
    assert 'Q7,500.00' not in preview['body']
    assert 'comprobante' in preview['body']

    second.update(status='Pagado', amount=0)
    crm.store.upsert('payments', second)
    paid = auth_client.get(f'/api/payments/{first_id}/send-preview').get_json()
    assert 'está pagada' in paid['body']
    assert 'recordatorio' not in paid['subject'].lower()
    assert 'saldo pendiente' not in paid['body']


def test_invoice_confirmation_preserves_reviewed_message_and_does_not_rotate_link_twice(auth_client):
    import app as crm
    _, first_id, _, _ = _make_job_with_two_installments(crm, 'confirmed')
    preview = auth_client.get(f'/api/payments/{first_id}/send-preview').get_json()
    payload = {key: preview[key] for key in ('to_email', 'subject', 'body')}
    payload['send_request_id'] = str(uuid.uuid4())
    first = auth_client.post(f'/api/payments/{first_id}/send', json=payload).get_json()
    token_hash = crm.store.get('payments', first_id)['public_token_hash']
    second = auth_client.post(f'/api/payments/{first_id}/send', json=payload).get_json()
    assert first['mail_id'] == second['mail_id']
    assert crm.store.get('payments', first_id)['public_token_hash'] == token_hash
    queued = crm.store.get('pending_emails', first['mail_id'])
    assert queued['status'] in ('pending', 'sent')
    assert queued['subject'] == preview['subject']
    assert '[[INVOICE_LINK]]' not in queued['body']
    assert '/i/' in queued['body']

    if queued['status'] == 'pending':
        result = auth_client.post(f"/api/pending-emails/{first['mail_id']}/send", json={})
        assert result.status_code == 200
        assert result.get_json()['email']['estado'] == 'sent'
    assert crm.store.get('payments', first_id)['delivery_status'] == 'sent'
