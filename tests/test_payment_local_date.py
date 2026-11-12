"""Evening payments must be counted today in Guatemala, for either brand."""
import json
import re
from datetime import datetime, timezone

import pytest
from conftest import login_as_tenant


@pytest.mark.parametrize('tenant', ['tenant-norkevin', 'tenant-norkevin-photography'])
@pytest.mark.parametrize('route', ['job', 'payment'])
def test_evening_payment_appears_in_month(client, monkeypatch, tenant, route):
    import app as crm

    class Evening(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = cls(2026, 11, 12, 1, 51, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    monkeypatch.setattr(crm, 'datetime', Evening)
    login_as_tenant(client, tenant)
    crm.store.upsert('jobs', dict(id='local-day-job', tenant_id=tenant, status='Confirmado'))
    payment = dict(id='local-day-pay', tenant_id=tenant, job_id='local-day-job',
                   invoice_id='LOCAL-DAY', amount=5000, original_amount=5000,
                   status='Pendiente', due_date='2026-11-16')
    crm.store.upsert('payments', payment)
    crm.store.upsert('payments', dict(payment, id='other-brand-pay', tenant_id='other-brand'))
    url = '/api/jobs/local-day-job/record-payment' if route == 'job' else '/api/payments/local-day-pay/pay'
    assert client.post(url, json={'amount': 5000}).status_code == 200
    paid = crm.store.get('payments', payment['id'])
    assert paid['paid_date'] == paid['fecha_pago'] == '2026-11-11'

    def dashboard_totals():
        response = client.get('/dashboard')
        assert response.status_code == 200
        data = json.loads(re.search(r'var DASHBOARD_DATA = (.+);', response.get_data(as_text=True)).group(1))
        return [data[key]['jobTypes']['All Job Types']['totals']['payments'] for key in ('7', '30', 'mtd', 'ytd')]

    assert dashboard_totals() == [5000] * 4
    assert client.get('/api/dashboard/custom-range?start=2026-11-11&end=2026-11-11').json['jobTypes']['All Job Types']['totals']['payments'] == 5000
    # Correcting an existing receipt must update both date aliases, without moving money.
    corrected = client.post('/api/payments/local-day-pay/update', json={'fecha_pago': '2026-10-31'})
    assert corrected.status_code == 200
    after = crm.store.get('payments', payment['id'])
    assert after['paid_date'] == after['fecha_pago'] == '2026-10-31'
    for key in paid.keys() - {'paid_date', 'fecha_pago', 'last_action', 'updated_at'}:
        assert after[key] == paid[key]
    assert dashboard_totals() == [0, 5000, 0, 5000]
    assert client.post('/api/payments/other-brand-pay/update', json={'fecha_pago': '2026-11-11'}).status_code == 404
    before = crm.store.get('payments', payment['id'])
    assert client.post('/api/payments/local-day-pay/update', json={'fecha_pago': '2026-02-30'}).status_code == 400
    assert crm.store.get('payments', payment['id']) == before


@pytest.mark.parametrize('route', ['job', 'payment'])
def test_explicit_collection_date_is_preserved(auth_client, sample_business, route):
    import app as crm
    url = '/api/jobs/job-fixture/record-payment' if route == 'job' else '/api/payments/payment-fixture/pay'
    response = auth_client.post(url, json={'amount': 100, 'fecha_pago': '2026-10-31'})
    assert response.status_code == 200
    paid = crm.store.get('payments', sample_business['payment']['id'])
    assert paid['paid_date'] == paid['fecha_pago'] == '2026-10-31'
