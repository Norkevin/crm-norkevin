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

    resp = auth_client.get(f'/api/payments/{pay1_id}/send-preview')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['to_email'] == 'invoicedyn@example.com'
    assert invoice_id in data['subject']

    pay = app_module.store.get('payments', pay1_id)
    assert not pay.get('sent_at'), 'la vista previa no debe marcar la factura como enviada'


def test_send_invoice_respects_edited_subject_and_body(auth_client):
    import app as app_module
    job_id, pay1_id, pay2_id, invoice_id = _make_job_with_two_installments(app_module, 'd')

    resp = auth_client.post(f'/api/payments/{pay1_id}/send', json={
        'subject': 'Asunto editado por Kevin',
        'body': 'Mensaje editado a mano.',
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True

    mail = next(m for m in app_module.store.list('mail_log') if m.get('id') == data['mail_id'])
    assert mail['subject'] == 'Asunto editado por Kevin'
    assert mail['body'] == 'Mensaje editado a mano.'

    pay = app_module.store.get('payments', pay1_id)
    assert pay.get('sent_at')
