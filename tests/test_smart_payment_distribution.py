"""Kevin: 'no quiero tener que modificar manualmente los pagos'. Al
registrar un pago para el job, el sistema debe repartirlo solo entre las
cuotas pendientes en orden de vencimiento, sin intervencion manual.

Kevin (2do reporte): 'el total paso de 22,500 a 18,125... el total siempre
es 22,500... si pago mas en un pago se deberia reducir la cuota en todos
los demas, no bajar el total'. La primera version de este feature REDUCIA
row['amount'] directamente en la cuota parcialmente cubierta, encogiendo
el Subtotal con cada pago -- exactamente el bug.

Kevin (3er reporte, version FINAL confirmada por el mismo): 'pague 6,000 y
no me marca el pago... quiero que me marque todo el pago completo, por
ejemplo aunque el sistema pida 3750 si el cliente paga 4600 que me lo
marque, y todas las demas cuotas se deben adaptar para que el total
restante llegue al final' -- y al preguntarle si el sobrante se reparte
solo en la siguiente cuota o entre TODAS las restantes, eligio: entre
TODAS, en partes iguales.

Diseno final:
  - 'original_amount': fijo, inmutable, el monto del contrato para esa
    cuota. Se usa SOLO para calcular el Subtotal de la factura (que nunca
    cambia).
  - 'amount': dinamico. Para una cuota Pagada, muestra lo REALMENTE
    recibido (puede ser mayor a su monto original). Para una cuota
    pendiente, muestra su saldo actual (reducido por abonos directos o
    por credito repartido desde el sobrepago de otra cuota).
  - 'paid_amount': cuanto dinero se recibio DIRECTAMENTE en esa fila (no
    cuenta el credito recibido de otras) -- se usa para el 'Paid' agregado
    de la factura.
"""
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
            'quote_id': 'q1', 'concepto': f'Pago {i+1}', 'amount': amt, 'original_amount': amt,
            'due_date': due_dates[i], 'status': 'Pendiente', 'cuota': f'{i+1}/{len(amounts)}',
            'tenant_id': 'tenant-norkevin',
        })
        ids.append(pid)
    return jid, ids


def _payments_for(app_module, jid):
    rows = [p for p in app_module.store.list('payments') if p.get('job_id') == jid]
    return sorted(rows, key=lambda p: p.get('due_date') or '')


def test_overpayment_marks_target_at_real_amount_and_spreads_surplus_to_all(auth_client):
    """2 cuotas de Q10,000 (total Q20,000). Paga Q15,000 en la primera:
    cuota 1 se marca Pagada por Q15,000 (el monto REAL recibido, no capado
    a 10,000), y el sobrante (Q5,000) se resta de la cuota 2 (la unica
    'otra' cuota que hay), quedando en Q5,000."""
    import app as app_module
    jid, _ = _make_job_with_payments(app_module, [10000, 10000])

    resp = auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 15000, 'fecha_pago': '2026-03-01'})
    assert resp.status_code == 200

    rows = _payments_for(app_module, jid)
    assert rows[0]['status'] == 'Pagado'
    assert rows[0]['amount'] == 15000, 'la cuota pagada debe mostrar el monto REAL recibido'
    assert rows[1]['status'] == 'Pendiente'
    assert rows[1]['amount'] == 5000, 'el sobrante de 5000 se resta de la unica otra cuota'

    total_original = sum(app_module._row_original_amount(r) for r in rows)
    assert total_original == 20000, 'el Subtotal (suma de original_amount) nunca cambia'


def test_surplus_spreads_evenly_across_all_remaining_installments(auth_client):
    """Q10,000 / Q5,000 / Q5,000 (total Q20,000). Paga Q15,000 en la
    primera -> se marca Pagada por Q15,000, y el sobrante (Q5,000) se
    reparte EN PARTES IGUALES entre las 2 cuotas restantes (Q2,500 cada
    una), no solo en la inmediata siguiente."""
    import app as app_module
    jid, _ = _make_job_with_payments(app_module, [10000, 5000, 5000])

    resp = auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 15000, 'fecha_pago': '2026-03-01'})
    assert resp.status_code == 200

    rows = _payments_for(app_module, jid)
    assert rows[0]['status'] == 'Pagado' and rows[0]['amount'] == 15000
    assert rows[1]['status'] == 'Pendiente' and rows[1]['amount'] == 2500
    assert rows[2]['status'] == 'Pendiente' and rows[2]['amount'] == 2500

    total_original = sum(app_module._row_original_amount(r) for r in rows)
    assert total_original == 20000


def test_two_separate_payments_each_apply_correctly_including_earlier_credit(auth_client):
    """3 cuotas de Q10,000 (total Q30,000). Primer pago Q12,000: cuota 1
    Pagada por Q12,000, sobrante Q2,000 repartido entre cuotas 2 y 3
    (Q1,000 cada una -> quedan en Q9,000). Segundo pago Q8,000 (menos que
    el saldo actual de la cuota 2, que ya es Q9,000 por el credito
    anterior): debe ser un abono PARCIAL sobre esos Q9,000, no marcarla
    Pagada -- confirma que el saldo se calcula sobre el monto ACTUAL de la
    fila, no sobre su original_amount menos paid_amount."""
    import app as app_module
    jid, _ = _make_job_with_payments(app_module, [10000, 10000, 10000])

    auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 12000, 'fecha_pago': '2026-01-01'})
    rows = _payments_for(app_module, jid)
    assert rows[0]['status'] == 'Pagado' and rows[0]['amount'] == 12000
    assert rows[1]['status'] == 'Pendiente' and rows[1]['amount'] == 9000
    assert rows[2]['status'] == 'Pendiente' and rows[2]['amount'] == 9000

    auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 8000, 'fecha_pago': '2026-02-01'})
    rows = _payments_for(app_module, jid)
    assert rows[0]['status'] == 'Pagado' and rows[0]['amount'] == 12000
    assert rows[1]['status'] == 'Pendiente', 'Q8,000 no alcanza a cubrir el saldo actual de Q9,000 -- sigue pendiente'
    assert rows[1]['amount'] == 1000, 'saldo restante: 9000 - 8000 = 1000'
    assert rows[1]['paid_amount'] == 8000
    assert rows[2]['status'] == 'Pendiente' and rows[2]['amount'] == 9000, 'la cuota 3 no se toca en el segundo pago'

    total_original = sum(app_module._row_original_amount(r) for r in rows)
    assert total_original == 30000, 'el total del contrato jamas cambia'


def test_overpayment_beyond_all_pending_rows_saldas_everything(auth_client):
    """Si el cliente paga MUCHO mas de lo que debe en total, no debe
    explotar ni crear saldo negativo -- la cuota objetivo absorbe el pago
    completo (incluso mas alla del contrato) y las demas quedan saldadas
    por credito en Q0, tambien marcadas Pagadas."""
    import app as app_module
    jid, _ = _make_job_with_payments(app_module, [5000, 5000])

    resp = auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 50000, 'fecha_pago': '2026-01-01'})
    assert resp.status_code == 200
    rows = _payments_for(app_module, jid)
    assert all(r['status'] == 'Pagado' for r in rows)
    assert rows[0]['amount'] == 50000, 'la cuota objetivo muestra el monto real recibido, aunque exceda el contrato'
    assert rows[1]['amount'] == 0, 'la otra cuota queda saldada por credito, no absorbe mas de lo que debia'

    total_original = sum(app_module._row_original_amount(r) for r in rows)
    assert total_original == 10000, 'el Subtotal del contrato original sigue siendo fijo'


def test_no_pending_rows_returns_error_not_crash(auth_client):
    import app as app_module
    jid, ids = _make_job_with_payments(app_module, [5000])
    auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 5000, 'fecha_pago': '2026-01-01'})

    resp = auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 1000, 'fecha_pago': '2026-01-02'})
    assert resp.status_code == 400
    assert resp.get_json()['ok'] is False


def test_job_price_paid_reflects_partial_payments_too(auth_client):
    """price_paid debe contar el dinero que de verdad entro (paid_amount,
    solo abonos DIRECTOS -- no cuenta credito recibido de otras cuotas,
    para no duplicar la plata)."""
    import app as app_module
    jid, _ = _make_job_with_payments(app_module, [10000, 10000])
    auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 15000, 'fecha_pago': '2026-01-01'})
    job = app_module.get_job(jid)
    assert job['price_paid'] == 15000


def test_invoice_view_subtotal_stays_fixed_after_overpayment(auth_client):
    """Reproduce el reporte de Kevin: factura de Q22,500 (3 cuotas de
    Q7,500), paga Q10,000 en la primera -- /invoices/<id> debe seguir
    mostrando Subtotal Q22,500 (el original_amount fijo), Paid Q10,000
    (lo realmente recibido)."""
    import app as app_module

    quote_id = 'quote-' + uuid.uuid4().hex[:8]
    app_module.store.upsert('quotes', {
        'id': quote_id, 'paquete_nombre': 'Test Package', 'status': 'Aceptada',
        'tenant_id': 'tenant-norkevin',
    })
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
    assert 'Q10,000.00' in html, 'el Paid debe reflejar lo realmente cobrado'


def test_invoice_view_shows_partial_badge_on_every_credited_row(auth_client):
    """Kevin: 'pague 6,000 y no me marca el pago'. Con 5 cuotas de Q4,500,
    pagar Q6,000 en la primera la marca Pagada por Q6,000 y reparte el
    sobrante (Q1,500) ENTRE LAS 4 restantes (Q375 cada una) -- las 4 deben
    mostrar el badge 'Partial', ninguna debe quedar en 'Unpaid' plano."""
    import app as app_module

    quote_id = 'quote-' + uuid.uuid4().hex[:8]
    app_module.store.upsert('quotes', {
        'id': quote_id, 'paquete_nombre': 'Test Package', 'status': 'Aceptada',
        'tenant_id': 'tenant-norkevin',
    })
    jid, ids = _make_job_with_payments(app_module, [4500, 4500, 4500, 4500, 4500])
    for pid in ids:
        p = app_module.store.get('payments', pid)
        p['quote_id'] = quote_id
        app_module.store.upsert('payments', p)

    resp = auth_client.post(f'/api/jobs/{jid}/record-payment', json={'amount': 6000, 'fecha_pago': '2026-01-01'})
    assert resp.status_code == 200

    rows = _payments_for(app_module, jid)
    assert rows[0]['amount'] == 6000
    for r in rows[1:]:
        assert r['amount'] == 4125, '4500 - (1500/4) = 4125 para cada una de las 4 cuotas restantes'

    resp = auth_client.get(f'/invoices/{ids[0]}')
    html = resp.get_data(as_text=True)
    assert html.count('>Partial<') == 4, 'las 4 cuotas restantes recibieron credito -- todas deben marcarse Partial'
    assert '>Unpaid<' not in html
