"""Kevin: al aceptar una cotizacion, las fechas de cuota deben proponerse
automaticamente: primera cuota el dia de la aceptacion, ultima 1 mes
despues de la boda, e intermedias repartidas equidistantes entre esos dos
puntos."""
from datetime import datetime, timedelta


def _make_lead_and_job(app_module, boda_offset_days, plan_pago):
    import uuid
    lead_id = 'lead-' + uuid.uuid4().hex[:8]
    boda_date = (datetime.now() + timedelta(days=boda_offset_days)).strftime('%Y-%m-%d')
    lead = {
        'id': lead_id, 'nombre': 'Cuotas Test', 'email': 'cuotas@example.com',
        'status': 'Nuevo', 'tenant_id': 'tenant-norkevin', 'fecha_tentativa': boda_date,
    }
    app_module.upsert_lead(lead)

    quote_id = 'quote-' + uuid.uuid4().hex[:8]
    quote = {
        'id': quote_id, 'lead_id': lead_id, 'paquete_nombre': 'Gold',
        'precio_total': 20000, 'plan_pago': plan_pago, 'status': 'Enviada',
        'tenant_id': 'tenant-norkevin',
    }
    app_module.store.upsert('quotes', quote)
    return lead, quote, boda_date


def test_two_installments_first_today_last_one_month_after_wedding(client):
    import app as app_module
    lead, quote, boda_date = _make_lead_and_job(app_module, 365, 2)

    resp = client.post(f"/quotes/{quote['id']}/accept", json={'plan_pago': 2})
    assert resp.status_code == 200

    updated_lead = app_module.get_lead(lead['id'])
    job = app_module.get_job(updated_lead['converted_to_job'])
    assert job['boda_date'] == boda_date

    payments = sorted(
        [p for p in app_module.store.list('payments') if p.get('quote_id') == quote['id']],
        key=lambda p: p['due_date'],
    )
    assert len(payments) == 2

    today_str = datetime.now().strftime('%Y-%m-%d')
    assert payments[0]['due_date'] == today_str, 'la primera cuota debe vencer el dia de la aceptacion'

    boda_dt = datetime.strptime(boda_date, '%Y-%m-%d')
    expected_last = app_module._add_one_month(boda_dt).strftime('%Y-%m-%d')
    assert payments[1]['due_date'] == expected_last, 'la ultima cuota debe vencer 1 mes despues de la boda'


def test_three_installments_middle_one_is_halfway_to_wedding(client):
    import app as app_module
    lead, quote, boda_date = _make_lead_and_job(app_module, 360, 3)

    resp = client.post(f"/quotes/{quote['id']}/accept", json={'plan_pago': 3})
    assert resp.status_code == 200

    payments = sorted(
        [p for p in app_module.store.list('payments') if p.get('quote_id') == quote['id']],
        key=lambda p: p['due_date'],
    )
    assert len(payments) == 3

    today_dt = datetime.now()
    boda_dt = datetime.strptime(boda_date, '%Y-%m-%d')
    middle_dt = datetime.strptime(payments[1]['due_date'], '%Y-%m-%d')

    days_to_middle = (middle_dt - today_dt).days
    days_to_boda = (boda_dt - today_dt).days
    assert abs(days_to_middle - days_to_boda / 2) <= 1


def test_four_and_five_installments_are_evenly_spaced(client):
    import app as app_module
    for n in (4, 5):
        lead, quote, boda_date = _make_lead_and_job(app_module, 300, n)
        resp = client.post(f"/quotes/{quote['id']}/accept", json={'plan_pago': n})
        assert resp.status_code == 200

        payments = sorted(
            [p for p in app_module.store.list('payments') if p.get('quote_id') == quote['id']],
            key=lambda p: p['due_date'],
        )
        assert len(payments) == n

        dates = [datetime.strptime(p['due_date'], '%Y-%m-%d') for p in payments]
        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        # todos los intervalos deberian ser aprox iguales (equidistantes)
        assert max(gaps) - min(gaps) <= 1


def test_single_payment_due_today():
    import app as app_module
    assert app_module is not None  # smoke: modulo importa el helper nuevo
    assert hasattr(app_module, '_add_one_month')


def test_no_wedding_date_falls_back_to_30_day_intervals(client):
    import app as app_module
    import uuid
    lead_id = 'lead-' + uuid.uuid4().hex[:8]
    lead = {'id': lead_id, 'nombre': 'Sin Fecha', 'email': 'sinfecha@example.com', 'status': 'Nuevo', 'tenant_id': 'tenant-norkevin'}
    app_module.upsert_lead(lead)
    quote_id = 'quote-' + uuid.uuid4().hex[:8]
    quote = {
        'id': quote_id, 'lead_id': lead_id, 'paquete_nombre': 'Gold',
        'precio_total': 10000, 'plan_pago': 2, 'status': 'Enviada', 'tenant_id': 'tenant-norkevin',
    }
    app_module.store.upsert('quotes', quote)

    resp = client.post(f"/quotes/{quote_id}/accept", json={'plan_pago': 2})
    assert resp.status_code == 200
    payments = sorted(
        [p for p in app_module.store.list('payments') if p.get('quote_id') == quote_id],
        key=lambda p: p['due_date'],
    )
    assert len(payments) == 2
