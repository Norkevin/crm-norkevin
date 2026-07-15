"""Kevin: 'en la primera cuota pague 7,000 y en vez de bajar [avanzar] todas
las demas cuotas se mantuvieron... tuve que marcarlas todas a mano'. La
causa: /payments (Payments Overview, la pagina general del nav) tenia su
PROPIO modal de 'Record Payment' que llamaba directo al endpoint viejo de
una sola fila (/api/payments/<id>/pay), sin pasar por la distribucion
inteligente -- aunque ya se habia arreglado este mismo bug en /invoices/<id>
y en el Job. Un tercer lugar con el mismo bug que se me habia escapado."""
import uuid


def _make_job_with_two_installments(app_module, suffix):
    client_id = f'client-povr-{suffix}'
    job_id = f'job-povr-{suffix}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Overview', 'last_name': 'Test',
        'email': 'povrtest@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Overview Test',
        'tenant_id': 'tenant-norkevin',
    })
    quote_id = f'quote-povr-{suffix}'
    app_module.store.upsert('quotes', {'id': quote_id, 'status': 'Aceptada', 'client_id': client_id, 'job_id': job_id})
    pay_ids = []
    for i, (amt, due) in enumerate([(4125, '2026-07-15'), (4125, '2027-01-03')]):
        pid = 'pay-povr-' + uuid.uuid4().hex[:6]
        app_module.store.upsert('payments', {
            'id': pid, 'invoice_id': f'INV-POVR-{suffix}', 'client_id': client_id, 'job_id': job_id,
            'quote_id': quote_id, 'amount': amt, 'status': 'Pendiente', 'due_date': due,
            'cuota': i + 1, 'concepto': 'Cuota', 'tenant_id': 'tenant-norkevin',
        })
        pay_ids.append(pid)
    return job_id, pay_ids


def test_payments_overview_page_exposes_job_id_for_smart_record_payment(auth_client):
    import app as app_module
    job_id, pay_ids = _make_job_with_two_installments(app_module, 'a')

    resp = auth_client.get('/payments')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "openRecordPaymentModal(paymentId, amount, jobId)" in html
    assert "/api/jobs/' + jobId + '/record-payment" in html


def test_overpayment_from_payments_overview_flow_redistributes_surplus(auth_client):
    """No pasa por el HTML -- confirma que el endpoint que ahora usa
    /payments (el job-level record-payment) reparte el sobrante en vez de
    dejar las demas cuotas intactas."""
    import app as app_module
    job_id, pay_ids = _make_job_with_two_installments(app_module, 'b')

    resp = auth_client.post(f'/api/jobs/{job_id}/record-payment', json={
        'amount': 7000, 'fecha_pago': '2026-07-15',
    })
    assert resp.status_code == 200

    rows = sorted(
        [p for p in app_module.store.list('payments') if p.get('job_id') == job_id],
        key=lambda p: p.get('due_date') or ''
    )
    row1, row2 = rows
    assert row1['status'] == 'Pagado' and row1['amount'] == 7000.0, 'la cuota pagada muestra el monto REAL recibido'
    assert row2['status'] == 'Pendiente', 'la segunda cuota NO debe quedarse intacta -- debe recibir el sobrante'
    assert row2['amount'] == 1250.0, '4125 - (7000-4125) = 1250 de saldo tras el credito'
