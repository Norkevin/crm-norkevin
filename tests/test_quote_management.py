import copy
import uuid
import pytest
from conftest import login_as_tenant


@pytest.fixture(params=['tenant-norkevin', 'tenant-norkevin-photography'])
def accepted(client, request):
    import app as module
    tables = ('leads', 'clients', 'jobs', 'quotes', 'payments', 'payment_schedules', 'contracts', 'questionnaires')
    snapshots = {t: module.store._read_raw(t) for t in tables}
    tenant = request.param
    login_as_tenant(client, tenant)
    suffix = uuid.uuid4().hex[:8]
    lead = {'id': 'lead-manage-' + suffix, 'nombre': 'Plan Test', 'email': 'plan@example.invalid', 'fecha_tentativa': '2027-06-12', 'tenant_id': tenant}
    module.store.upsert('leads', lead)
    quote = {'id': 'quote-manage-' + suffix, 'lead_id': lead['id'], 'tenant_id': tenant,
             'precio_total': 29000, 'plan_pago': 2, 'paquete_nombre': 'Paquete de prueba',
             'options': [{'id': 'opt-a', 'name': 'Paquete de prueba', 'precio_total': 29000}],
             'snapshot_aceptado': {'total': 29000, 'plan_pago': 2}, 'plan_pago_opciones': [1, 2]}
    module.store.upsert('quotes', quote)
    with module.app.test_request_context():
        from flask import g
        g.public_tenant_id = tenant
        result = module._convert_lead_to_job(lead, quote=quote)
    yield client, module, quote['id'], result['job']['id'], tenant
    for table, records in snapshots.items():
        module.store._save(table, records)


def change(accepted, action, **extra):
    client, module, quote_id, _, _ = accepted
    q = module.store.get('quotes', quote_id, include_archived=True)
    return client.post(f'/api/quotes/{quote_id}/manage', json={'action': action, 'revision': len(q.get('revision_history') or []), **extra})


def test_two_to_five_preserves_total_history_and_active_invoices(accepted):
    client, module, qid, jid, _ = accepted
    old = [p for p in module.store.list('payments') if p.get('quote_id') == qid]
    response = change(accepted, 'payment-plan', plan_pago=5)
    assert response.status_code == 200
    quote = module.store.get('quotes', qid)
    assert quote['plan_pago'] == quote['snapshot_aceptado']['plan_pago'] == 5
    assert quote['precio_total'] == 29000
    assert quote['revision_history'][0]['quote']['plan_pago'] == 2
    rows = [p for p in module.store.list('payments') if p.get('quote_id') == qid]
    assert len(rows) == 5 and all(p['amount'] == 5800 for p in rows)
    summary = module._job_payment_summary(module.get_job(jid), rows)
    assert summary['total'] == summary['pendiente'] == 29000 and summary['cuotas'] == 5
    archived = [p for p in module.store.list('payments', include_archived=True) if p.get('quote_id') == qid and p.get('archived_at')]
    assert len(archived) == 2 and {p['id'] for p in archived} == {p['id'] for p in old}
    assert all(module.store.get('payments', p['id']) is None for p in old)
    schedules = module._job_schedules(jid)
    assert sum(s['status'] == 'active' for s in schedules) == 1
    assert sum(s['status'] == 'superseded' for s in schedules) == 1
    assert change(accepted, 'payment-plan', plan_pago=5).get_json()['unchanged']
    assert len([p for p in module.store.list('payments') if p.get('quote_id') == qid]) == 5
    assert client.get('/quotes/' + qid + '/manage').status_code == 200
    assert 'Guardar plan de pagos' in client.get('/quotes/' + qid + '/manage').get_data(as_text=True)
    assert '5 pagos' in client.get('/quotes/' + qid).get_data(as_text=True)
    assert 'Q29,000.00' in client.get('/jobs/' + jid).get_data(as_text=True)


@pytest.mark.parametrize('action', ['payment-plan', 'archive', 'reopen'])
def test_paid_or_partially_paid_quote_is_never_replaced(accepted, action):
    _, module, qid, _, _ = accepted
    row = next(p for p in module.store.list('payments') if p.get('quote_id') == qid)
    row['paid_amount'] = 100
    module.store.upsert('payments', row)
    before = copy.deepcopy([p for p in module.store.list('payments', include_archived=True) if p.get('quote_id') == qid])
    response = change(accepted, action, plan_pago=5)
    assert response.status_code == 409
    assert [p for p in module.store.list('payments', include_archived=True) if p.get('quote_id') == qid] == before
    assert not module.store.get('quotes', qid).get('archived_at')


def test_archive_and_restore_is_recoverable_without_reactivating_old_debt(accepted):
    client, module, qid, jid, _ = accepted
    token, q = module.public_tokens.emitir_para(module.store.get('quotes', qid))
    module.store.upsert('quotes', q)
    assert change(accepted, 'archive').status_code == 200
    assert module.store.get('quotes', qid) is None
    assert client.get('/q/' + token).status_code == 404
    assert not [p for p in module.store.list('payments') if p.get('quote_id') == qid]
    assert module.get_job(jid)['price_total'] == 0
    assert 'Sin cotización aceptada' in client.get('/jobs/' + jid).get_data(as_text=True)
    assert qid in client.get('/quotes?trash=1').get_data(as_text=True)
    assert change(accepted, 'restore').status_code == 200
    restored = module.store.get('quotes', qid)
    assert restored['status'] == 'Borrador' and not restored.get('public_token_hash')
    assert not [p for p in module.store.list('payments') if p.get('quote_id') == qid]
    assert client.get(f'/quotes/{qid}/edit').status_code == 200
    # Owner can edit/preview; an old public URL cannot expose the revised draft.
    with client.session_transaction() as session:
        session.clear()
    assert client.get('/quotes/' + qid).status_code == 404
    assert client.post('/quotes/' + qid + '/accept', data={}).status_code == 404
    assert client.get('/quotes/' + qid + '/pdf').status_code == 404
    assert client.post('/quotes/' + qid + '/decline').status_code == 404


def test_reopen_allows_full_edit_and_reacceptance_without_duplicate_payments(accepted):
    client, module, qid, jid, _ = accepted
    assert change(accepted, 'reopen').status_code == 200
    assert module.store.get('quotes', qid)['status'] == 'Borrador'
    assert client.get(f'/quotes/{qid}/edit').status_code == 200
    assert module.get_job(jid)['price_total'] == 0
    q = module.store.get('quotes', qid)
    q['plan_pago_opciones'] = [5]
    module.store.upsert('quotes', q)
    response = client.post('/quotes/' + qid + '/accept', data={'option_id': 'opt-a', 'plan_pago': '5'})
    assert response.status_code == 200
    assert module.get_job(jid)['price_total'] == 29000
    assert len([p for p in module.store.list('payments') if p.get('quote_id') == qid]) == 5


def test_quote_management_requires_authentication_and_same_tenant(accepted):
    client, module, qid, _, tenant = accepted
    other = 'tenant-norkevin' if tenant != 'tenant-norkevin' else 'tenant-norkevin-photography'
    login_as_tenant(client, other)
    assert client.get(f'/quotes/{qid}/manage').status_code == 404
    assert client.post(f'/api/quotes/{qid}/manage', json={'action':'archive','revision':0}).status_code == 404
    assert not next(q for q in module.store._read_raw('quotes') if q['id'] == qid).get('archived_at')
    with client.session_transaction() as session:
        session.clear()
    assert client.post(f'/api/quotes/{qid}/manage', json={'action':'archive','revision':0}).status_code in (401, 302)


@pytest.mark.parametrize('plan', [0, -1, 25, 1.5, True, '5', None])
def test_invalid_plan_does_not_modify_records(accepted, plan):
    _, module, qid, _, _ = accepted
    assert change(accepted, 'payment-plan', plan_pago=plan).status_code == 400
    assert module.store.get('quotes', qid)['plan_pago'] == 2


def test_signed_contract_blocks_revisions(accepted):
    _, module, qid, jid, tenant = accepted
    module.store.upsert('contracts', {'id': 'contract-' + qid, 'job_id': jid, 'tenant_id': tenant, 'signed': True})
    assert change(accepted, 'payment-plan', plan_pago=5).status_code == 409


def test_write_failure_restores_quote_payments_schedule_and_job(accepted, monkeypatch):
    _, module, qid, _, _ = accepted
    tables = ('quotes', 'payments', 'payment_schedules', 'jobs')
    before = {t: module.store._read_raw(t) for t in tables}
    original = module.store.upsert
    def fail_new_payment(table, row):
        if table == 'payments' and not row.get('archived_at'):
            raise OSError('Simulated write failure')
        return original(table, row)
    monkeypatch.setattr(module.store, 'upsert', fail_new_payment)
    with pytest.raises(OSError, match='Simulated'):
        change(accepted, 'payment-plan', plan_pago=5)
    assert {t: module.store._read_raw(t) for t in tables} == before


def test_stale_page_cannot_overwrite_new_revision(accepted):
    client, module, qid, _, _ = accepted
    assert change(accepted, 'payment-plan', plan_pago=5).status_code == 200
    assert client.post(f'/api/quotes/{qid}/manage', json={'action':'archive','revision':0}).status_code == 409
    assert module.store.get('quotes', qid)['plan_pago'] == 5
