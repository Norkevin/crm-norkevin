"""Kevin: 'no quiero tener que modificar manualmente los pagos'. Al
registrar un pago para el job, el sistema debe repartirlo solo entre las
cuotas pendientes en orden de vencimiento, sin intervencion manual.

Kevin (segundo reporte, mas grave): 'el total paso de 22,500 a 18,125...
no tiene logica, el total siempre es 22,500... si pago mas en un pago se
deberia reducir la cuota en todos los demas, no bajar el total'. La
primera version de este feature REDUCIA row['amount'] directamente en la
cuota parcialmente cubierta -- eso hacia que la suma de amounts (el
Subtotal de la factura) se encogiera con cada pago parcial. Ahora 'amount'
es SIEMPRE el monto fijo original de la cuota; lo que se acumula es
'paid_amount' (cuanto se ha abonado), y el saldo es amount - paid_amount."""
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


def test_two_installments_partial_payment_never_shrinks_the_total(auth_client):
    """Total Q20,000 en 2 cuotas de Q10,000. Cliente paga Q15,000 -> Pago 1
    Pagado, Pago 2 con Q5,000 abonados y Q5,000 de saldo -- pero el total
    (suma de 'amount' fijos) sigue siendo Q20,000, nunca Q15,000."""
    import app as app_module
    jid, _ = _make_job_with_payments(app_module, [10000, 10000])

    resp = auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 15000, 'fecha_pago': '2026-03-01'})
    assert resp.status_code == 200

    rows = _payments_for(app_module, jid)
    assert rows[0]['status'] == 'Pagado'
    assert rows[0]['amount'] == 10000
    assert rows[1]['status'] == 'Pendiente'
    assert rows[1]['amount'] == 10000, 'amount NUNCA debe reducirse, solo paid_amount avanza'
    assert rows[1]['paid_amount'] == 5000

    total = sum(r['amount'] for r in rows)
    assert total == 20000, 'el total del contrato debe mantenerse fijo sin importar como se reparten los pagos'


def test_three_installments_exact_boundary(auth_client):
    """Q10,000 / Q5,000 / Q5,000. Cliente paga Q15,000 -> Pago 1 y 2
    Pagados exactos, Pago 3 sigue Pendiente sin tocar."""
    import app as app_module
    jid, _ = _make_job_with_payments(app_module, [10000, 5000, 5000])

    resp = auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 15000, 'fecha_pago': '2026-03-01'})
    assert resp.status_code == 200

    rows = _payments_for(app_module, jid)
    assert rows[0]['status'] == 'Pagado' and rows[0]['amount'] == 10000
    assert rows[1]['status'] == 'Pagado' and rows[1]['amount'] == 5000
    assert rows[2]['status'] == 'Pendiente' and rows[2]['amount'] == 5000
    assert sum(r['amount'] for r in rows) == 20000


def test_two_partial_payments_to_the_same_row_accumulate_correctly(auth_client):
    """Dos pagos sucesivos: cada uno debe seguir avanzando la fila
    pendiente vía paid_amount, sin que 'amount' se mueva nunca, y sin
    volver a cobrar una fila ya Pagada."""
    import app as app_module
    jid, _ = _make_job_with_payments(app_module, [10000, 10000, 10000])

    auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 12000, 'fecha_pago': '2026-01-01'})
    rows = _payments_for(app_module, jid)
    assert rows[0]['status'] == 'Pagado' and rows[0]['amount'] == 10000
    assert rows[1]['status'] == 'Pendiente' and rows[1]['amount'] == 10000 and rows[1]['paid_amount'] == 2000
    assert rows[2]['status'] == 'Pendiente' and rows[2]['amount'] == 10000 and rows[2].get('paid_amount', 0) == 0

    auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 8000, 'fecha_pago': '2026-02-01'})
    rows = _payments_for(app_module, jid)
    assert rows[0]['status'] == 'Pagado' and rows[0]['amount'] == 10000
    assert rows[1]['status'] == 'Pagado' and rows[1]['amount'] == 10000, (
        'la fila 2 se termino de cubrir entre los dos pagos (2000 + 8000 = 10000) -- '
        'amount debe seguir siendo el original, no 8000 como en el bug reportado'
    )
    assert rows[2]['status'] == 'Pendiente' and rows[2]['amount'] == 10000

    assert sum(r['amount'] for r in rows) == 30000, 'el total del contrato jamas cambia'


def test_overpayment_beyond_all_pending_rows_is_capped(auth_client):
    """Si el cliente paga de mas (mas de lo que debe en total), no debe
    explotar ni crear saldo negativo -- simplemente todo queda Pagado."""
    import app as app_module
    jid, _ = _make_job_with_payments(app_module, [5000, 5000])

    resp = auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 50000, 'fecha_pago': '2026-01-01'})
    assert resp.status_code == 200
    rows = _payments_for(app_module, jid)
    assert all(r['status'] == 'Pagado' for r in rows)
    assert sum(r['amount'] for r in rows) == 10000


def test_no_pending_rows_returns_error_not_crash(auth_client):
    import app as app_module
    jid, ids = _make_job_with_payments(app_module, [5000])
    auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 5000, 'fecha_pago': '2026-01-01'})

    resp = auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 1000, 'fecha_pago': '2026-01-02'})
    assert resp.status_code == 400
    assert resp.get_json()['ok'] is False


def test_job_price_paid_reflects_partial_payments_too(auth_client):
    """price_paid debe contar el dinero que de verdad entro, incluyendo
    abonos parciales -- antes solo sumaba filas 100% 'Pagado', asi que un
    abono parcial de Q5,000 desaparecia del total cobrado del job."""
    import app as app_module
    jid, _ = _make_job_with_payments(app_module, [10000, 10000])
    auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 15000, 'fecha_pago': '2026-01-01'})
    job = app_module.get_job(jid)
    assert job['price_paid'] == 15000


def test_invoice_view_total_stays_fixed_after_partial_payment(auth_client):
    """Reproduce exactamente el reporte de Kevin: factura de Q22,500 (aqui
    simplificada a 3 cuotas de Q7,500), paga Q10,000 en la primera cuota
    -- /invoices/<id> debe seguir mostrando Subtotal Q22,500, nunca menos."""
    import app as app_module

    quote_id = 'quote-' + uuid.uuid4().hex[:8]
    app_module.store.upsert('quotes', {'id': quote_id, 'paquete_nombre': 'Test Package', 'status': 'Aceptada'})
    jid, ids = _make_job_with_payments(app_module, [7500, 7500, 7500])
    for pid in ids:
        p = app_module.store.get('payments', pid)
        p['quote_id'] = quote_id
        app_module.store.upsert('payments', p)

    resp = auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 10000, 'fecha_pago': '2026-01-01'})
    assert resp.status_code == 200

    # invoice_view acepta el id de cualquier fila individual de la factura
    # (busca por invoice_id O por id) -- usamos el id unico de la primera
    # cuota para no chocar con 'INV-0' de otros jobs creados en otros tests.
    resp = auth_client.get(f'/invoices/{ids[0]}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Q22,500.00' in html, 'el Subtotal debe seguir siendo el total original del contrato'
    assert 'Q10,000.00' in html, 'el Paid debe reflejar lo realmente cobrado (incluyendo el abono parcial)'


def test_invoice_view_shows_partial_badge_not_plain_unpaid(auth_client):
    """Kevin: 'pague 6,000 y no me marca el pago' -- la fila que recibio el
    abono parcial mostraba el mismo chip gris 'Unpaid' que una fila sin
    ningun pago, sin distinguir que SI se registro dinero. Ahora debe
    mostrar un chip 'Partial' distinto."""
    import app as app_module

    quote_id = 'quote-' + uuid.uuid4().hex[:8]
    app_module.store.upsert('quotes', {'id': quote_id, 'paquete_nombre': 'Test Package', 'status': 'Aceptada'})
    jid, ids = _make_job_with_payments(app_module, [4500, 4500, 4500, 4500, 4500])
    for pid in ids:
        p = app_module.store.get('payments', pid)
        p['quote_id'] = quote_id
        app_module.store.upsert('payments', p)

    resp = auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 6000, 'fecha_pago': '2026-01-01'})
    assert resp.status_code == 200

    resp = auth_client.get(f'/invoices/{ids[0]}')
    html = resp.get_data(as_text=True)
    assert '>Partial<' in html, 'la cuota con abono parcial debe distinguirse de una sin ningun pago'
    # Las cuotas 3, 4 y 5 (sin ningun abono) deben seguir mostrando Unpaid.
    assert html.count('>Unpaid<') == 3
