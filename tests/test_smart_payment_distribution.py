"""Kevin: 'no quiero tener que modificar manualmente los pagos'. Al
registrar un pago para el job, el sistema debe repartirlo solo entre las
cuotas pendientes en orden de vencimiento, sin intervencion manual."""
import uuid


def _make_job_with_payments(app_module, amounts, due_dates=None):
    cid = 'client-' + uuid.uuid4().hex[:8]
    app_module.store.upsert('clients', {
        'id': cid, 'first_name': 'Pay', 'last_name': 'Test', 'tenant_id': 'tenant-norkevin',
    })
    jid = 'boda-' + uuid.uuid4().hex[:8]
    app_module.upsert_job({
        'id': jid, 'nombre': 'Boda Pagos', 'boda_date': '2027-06-01',
        'client_id': cid, 'status': 'Confirmado', 'tenant_id': 'tenant-norkevin',
    })
    due_dates = due_dates or [f'2026-0{i+1}-01' for i in range(len(amounts))]
    ids = []
    for i, amt in enumerate(amounts):
        pid = 'pay-' + uuid.uuid4().hex[:8]
        app_module.store.upsert('payments', {
            'id': pid, 'invoice_id': f'INV-{i}', 'client_id': cid, 'job_id': jid,
            'quote_id': 'q1', 'concepto': f'Pago {i+1}', 'amount': amt,
            'due_date': due_dates[i], 'status': 'Pendiente', 'cuota': f'{i+1}/{len(amounts)}',
            'tenant_id': 'tenant-norkevin',
        })
        ids.append(pid)
    return jid, ids


def _payments_for(app_module, jid):
    rows = [p for p in app_module.store.list('payments') if p.get('job_id') == jid]
    return sorted(rows, key=lambda p: p.get('due_date') or '')


def test_two_installments_partial_payment_reduces_the_next_one(auth_client):
    """Total Q20,000 en 2 cuotas de Q10,000. Cliente paga Q15,000 ->
    Pago 1 Pagado, Pago 2 queda en Q5,000 pendiente."""
    import app as app_module
    jid, _ = _make_job_with_payments(app_module, [10000, 10000])

    resp = auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 15000, 'fecha_pago': '2026-03-01'})
    assert resp.status_code == 200

    rows = _payments_for(app_module, jid)
    assert rows[0]['status'] == 'Pagado'
    assert rows[0]['amount'] == 10000
    assert rows[1]['status'] == 'Pendiente'
    assert rows[1]['amount'] == 5000


def test_three_installments_exact_boundary(auth_client):
    """Q10,000 / Q5,000 / Q5,000. Cliente paga Q15,000 -> Pago 1 y 2
    Pagados, Pago 3 sigue en Q5,000 sin tocar."""
    import app as app_module
    jid, _ = _make_job_with_payments(app_module, [10000, 5000, 5000])

    resp = auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 15000, 'fecha_pago': '2026-03-01'})
    assert resp.status_code == 200

    rows = _payments_for(app_module, jid)
    assert rows[0]['status'] == 'Pagado' and rows[0]['amount'] == 10000
    assert rows[1]['status'] == 'Pagado' and rows[1]['amount'] == 5000
    assert rows[2]['status'] == 'Pendiente' and rows[2]['amount'] == 5000


def test_payment_never_applied_twice_to_a_fully_paid_row(auth_client):
    """Dos pagos sucesivos: cada uno debe seguir avanzando la fila
    pendiente, nunca re-cobrar una fila que ya quedo en Pagado."""
    import app as app_module
    jid, _ = _make_job_with_payments(app_module, [10000, 10000, 10000])

    auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 12000, 'fecha_pago': '2026-01-01'})
    rows = _payments_for(app_module, jid)
    assert rows[0]['status'] == 'Pagado'
    assert rows[1]['status'] == 'Pendiente' and rows[1]['amount'] == 8000
    assert rows[2]['status'] == 'Pendiente' and rows[2]['amount'] == 10000

    auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 8000, 'fecha_pago': '2026-02-01'})
    rows = _payments_for(app_module, jid)
    assert rows[0]['status'] == 'Pagado'
    assert rows[1]['status'] == 'Pagado' and rows[1]['amount'] == 8000
    assert rows[2]['status'] == 'Pendiente' and rows[2]['amount'] == 10000


def test_overpayment_beyond_all_pending_rows_is_capped(auth_client):
    """Si el cliente paga de mas (mas de lo que debe en total), no debe
    explotar ni crear saldo negativo -- simplemente todo queda Pagado."""
    import app as app_module
    jid, _ = _make_job_with_payments(app_module, [5000, 5000])

    resp = auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 50000, 'fecha_pago': '2026-01-01'})
    assert resp.status_code == 200
    rows = _payments_for(app_module, jid)
    assert all(r['status'] == 'Pagado' for r in rows)


def test_no_pending_rows_returns_error_not_crash(auth_client):
    import app as app_module
    jid, ids = _make_job_with_payments(app_module, [5000])
    auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 5000, 'fecha_pago': '2026-01-01'})

    resp = auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 1000, 'fecha_pago': '2026-01-02'})
    assert resp.status_code == 400
    assert resp.get_json()['ok'] is False


def test_job_price_paid_reflects_total_paid_after_distribution(auth_client):
    import app as app_module
    jid, _ = _make_job_with_payments(app_module, [10000, 10000])
    auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 15000, 'fecha_pago': '2026-01-01'})
    job = app_module.get_job(jid)
    assert job['price_paid'] == 10000
