"""Source installments, write-offs, tenant isolation and repeat imports."""
from copy import deepcopy
import json
import re


def reconciliation():
    return {'mode': 'reconcile_payments', 'jobs': [{
        'existing_job_id': 'reconcile-test', 'source_id': '12345',
        'source_url': 'https://app.studioninja.co/jobs/view/12345',
        'invoices': [
            {'invoice_no': 'TEST-1', 'url': 'https://app.studioninja.co/invoices/123', 'total': 100,
             'payments': [{'amount': 30, 'status': 'Pagado', 'due_date': '2025-01-01', 'paid_date': '2025-02-01'},
                          {'amount': 50, 'status': 'Pagado', 'due_date': '2026-01-01', 'paid_date': '2026-02-01'}],
             'writeoffs': [{'amount': 20, 'due_date': '2026-03-01', 'date': '2026-02-02'}]},
            {'invoice_no': 'TEST-2', 'url': 'https://app.studioninja.co/invoices/124', 'total': 40,
             'payments': [{'amount': 10, 'status': 'Pagado', 'due_date': '2026-02-01', 'paid_date': '2026-02-01'},
                          {'amount': 30, 'status': 'Pendiente', 'due_date': '2027-01-01'}], 'writeoffs': []}
        ]}]}


def post(client, data):
    return client.post('/api/admin/import-studio-ninja', json={'confirm': 'IMPORTAR', 'payload': data})


def seed():
    import app as crm
    crm.store.upsert('jobs', {'id': 'reconcile-test', 'tenant_id': 'tenant-norkevin', 'nombre': 'Test',
                            'status': 'Listo', 'boda_date': '2026-01-01', 'price_total': 100})
    crm.store.upsert('payments', {'id': 'reconcile-old', 'job_id': 'reconcile-test', 'tenant_id': 'tenant-norkevin',
                                'amount': 100, 'status': 'Pagado', 'paid_date': '2026-02-01'})


def test_source_installments_writeoffs_audit_idempotence_and_dashboard(auth_client):
    import app as crm
    seed(); data = reconciliation()
    baseline_html = auth_client.get('/dashboard').get_data(as_text=True)
    baseline = {s['year']: s['total'] for s in json.loads(re.search(r'var REVENUE_SERIES = (.*);', baseline_html).group(1))}
    mail_before = {t: len(crm.store.list(t)) for t in ('pending_emails', 'mail_log', 'contracts')}
    preview = deepcopy(data); preview['dry_run'] = True
    validated = post(auth_client, preview).get_json()
    assert validated['payments'] == 4 and validated['skipped'] == []
    assert len([p for p in crm.list_payments() if p.get('job_id') == 'reconcile-test']) == 1
    assert post(auth_client, data).status_code == 200
    rows = [p for p in crm.list_payments() if p.get('job_id') == 'reconcile-test']
    assert len(rows) == 4 and 'reconcile-old' in {p['id'] for p in rows}
    job = crm.get_job('reconcile-test')
    summary = crm._job_payment_summary(job, rows)
    assert (summary['total'], summary['pagado'], summary['pendiente'], summary['condonado'], summary['descuadre_cotizado_vs_cuotas']) == (140, 90, 30, 20, 0)
    assert job['status'] == 'Listo'
    assert job['studio_ninja_payment_reconciliation_before']['payments'][0]['amount'] == 100
    assert post(auth_client, data).status_code == 200
    again = [p for p in crm.list_payments() if p.get('job_id') == 'reconcile-test']
    assert {p['id'] for p in again} == {p['id'] for p in rows}
    assert {t: len(crm.store.list(t)) for t in mail_before} == mail_before
    html = auth_client.get('/dashboard').get_data(as_text=True)
    series = json.loads(re.search(r'var REVENUE_SERIES = (.*);', html).group(1))
    after = {s['year']: s['total'] for s in series}
    assert {y: after.get(y, 0) - baseline.get(y, 0) for y in (2025, 2026, 2027)} == {2025: 30, 2026: -40, 2027: 30}
    assert 'maximumFractionDigits: 2' in html
    assert 'Condonado en Studio Ninja' in auth_client.get('/jobs/reconcile-test').get_data(as_text=True)


def test_entire_batch_validation_and_foreign_tenant(auth_client):
    import app as crm
    seed(); data = reconciliation(); bad = deepcopy(data['jobs'][0])
    bad['existing_job_id'] = 'foreign-job'; data['jobs'].append(bad)
    assert post(auth_client, data).status_code == 400
    assert crm.store.get('payments', 'reconcile-old')['amount'] == 100
    data = reconciliation(); data['jobs'][0]['invoices'][0]['writeoffs'][0]['amount'] = 21
    assert post(auth_client, data).status_code == 400
    assert crm.store.get('payments', 'reconcile-old')['amount'] == 100
    data = reconciliation(); data['jobs'][0]['invoices'][0]['payments'][0]['paid_date'] = ''
    assert post(auth_client, data).status_code == 400
