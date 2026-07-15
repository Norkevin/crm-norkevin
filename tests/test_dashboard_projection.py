"""El dashboard debe mostrar dinero futuro agendado, no solo lo cobrado."""
import json
import re
import uuid


def test_dashboard_revenue_comparison_includes_projected_unpaid_payments(auth_client):
    import app as app_module

    due_date = '2035-02-15'
    payment_id = 'pay-projected-' + uuid.uuid4().hex[:8]
    app_module.store.upsert('payments', {
        'id': payment_id,
        'invoice_id': 'INV-PROJ-' + uuid.uuid4().hex[:4],
        'client_id': 'client-projected',
        'job_id': 'job-projected',
        'quote_id': 'quote-projected',
        'amount': 1234.0,
        'status': 'Pendiente',
        'due_date': due_date,
        'tenant_id': 'tenant-norkevin',
    })

    resp = auth_client.get('/dashboard')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '"projected"' in html
    match = re.search(r'var REVENUE_SERIES = (.*?);', html)
    assert match, 'el dashboard debe exponer la serie de Revenue Comparison'
    series = json.loads(match.group(1))
    projected_2035 = next(row for row in series if row['year'] == 2035)
    assert projected_2035['projected'][1] >= 1234.0
    assert 'Proyectado (pagos agendados, sin cobrar)' in html
