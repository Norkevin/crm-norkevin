"""Synthetic active migration: exact state, reconciliation, isolation, no sends."""
from copy import deepcopy


def payload():
    return {'mode': 'active_jobs', 'jobs': [{
        'source_id': '12345', 'source_url': 'https://app.studioninja.co/jobs/view/12345',
        'job_name': 'Trabajo de prueba', 'boda_date': '2027-01-01',
        'clients': [{'first_name': 'Prueba', 'email': 'test@example.com'}],
        'workflow': [{'id': 'sn-1', 'name': 'Contrato', 'status': 'done', 'source_details': '2026-01-01'},
                     {'id': 'sn-2', 'name': 'Job complete', 'status': 'pending', 'is_job_complete': True}],
        'invoices': [{'total': 100, 'invoice_no': 'TEST', 'url': 'https://app.studioninja.co/invoices/1',
                      'payments': [{'amount': 40, 'due_date': '2026-01-01', 'paid_date': '2026-01-02', 'status': 'Pagado'},
                                   {'amount': 60, 'due_date': '2027-01-01', 'status': 'Pendiente'}]}],
    }]}


def run(client, data):
    return client.post('/api/admin/import-studio-ninja', json={'confirm': 'IMPORTAR', 'payload': data})


def test_exact_workflow_idempotent_and_no_documents_or_mail(auth_client):
    import app as crm
    before = {t: len(crm.store.list(t)) for t in ('pending_emails', 'mail_log', 'contracts')}
    data = payload()
    assert run(auth_client, data).status_code == 200
    jid = 'boda-sn-active-12345'
    job = crm.get_job(jid)
    assert job['studio_ninja_workflow'] == data['jobs'][0]['workflow']
    assert crm.compute_workflow_steps_for_job(job)[1] == 50
    assert len(run(auth_client, data).get_json()['skipped']) == 1
    payments = [p for p in crm.list_payments() if p.get('job_id') == jid]
    assert len(payments) == 2
    assert crm._job_payment_summary(job, payments)['pagado'] == 40
    assert {t: len(crm.store.list(t)) for t in before} == before
    assert auth_client.get('/jobs/' + jid).status_code == 200
    assert auth_client.post(f'/api/jobs/{jid}/imported-workflow/sn-2/complete').status_code == 200
    assert crm.get_job(jid)['status'] == 'Listo'
    assert {t: len(crm.store.list(t)) for t in before} == before


def test_reconcile_existing_row_preserves_audit(auth_client):
    import app as crm
    jid = 'test-active-reconcile'
    crm.store.upsert('jobs', {'id': jid, 'tenant_id': 'tenant-norkevin', 'nombre': 'Anterior'})
    crm.store.upsert('payments', {'id': 'test-reconcile-pay', 'job_id': jid, 'tenant_id': 'tenant-norkevin',
                                'status': 'Pagado', 'amount': 90, 'paid_date': '2025-01-01'})
    data = payload(); data['jobs'][0]['source_id'] = '54321'; data['jobs'][0]['existing_job_id'] = jid
    assert run(auth_client, data).status_code == 200
    job = crm.get_job(jid)
    assert job['studio_ninja_before_import']['payments'][0]['amount'] == 90
    payments = [p for p in crm.list_payments() if p.get('job_id') == jid]
    assert len(payments) == 2
    assert crm._job_payment_summary(job, payments)['pagado'] == 40
    assert crm._job_payment_summary(job, payments)['pendiente'] == 60


def test_validates_batch_before_write_and_rejects_other_tenant(auth_client):
    import app as crm
    before = len(crm.list_jobs())
    data = payload(); bad = deepcopy(data['jobs'][0]); bad['source_id'] = '23456'
    bad['invoices'][0]['total'] = 999; data['jobs'].append(bad)
    assert run(auth_client, data).status_code == 400
    assert len(crm.list_jobs()) == before
    data = payload(); data['jobs'][0]['existing_job_id'] = 'foreign-active-job'
    assert run(auth_client, data).status_code == 400
    assert len(crm.list_jobs()) == before


def test_document_copy_is_scoped_idempotent_and_not_a_signature(auth_client, monkeypatch, tmp_path):
    import app as crm
    monkeypatch.setattr(crm, 'UPLOADS_DIR', str(tmp_path))
    data = payload(); data['jobs'][0]['source_id'] = '98765'
    assert run(auth_client, data).status_code == 200
    docs = {'mode': 'active_documents', 'jobs': [{
        'source_id': '98765', 'existing_job_id': 'boda-sn-active-98765',
        'documents': [{'type': 'Contrato', 'url': 'https://app.studioninja.co/contracts/123',
                       'text': 'Texto de prueba <script>alert(1)</script>\nSigned on 1 January 2026'}]}]}
    before = len(crm.store.list('contracts'))
    assert len(run(auth_client, docs).get_json()['created']) == 1
    assert len(run(auth_client, docs).get_json()['skipped']) == 1
    html = next(tmp_path.iterdir()).read_text()
    assert '&lt;script&gt;' in html and '<script>' not in html
    assert len(crm.store.list('contracts')) == before
    docs['jobs'][0]['source_id'] = 'not-matching'
    assert run(auth_client, docs).status_code == 400
