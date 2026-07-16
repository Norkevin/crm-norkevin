"""Kevin: 'un cliente me cancelo su boda, solo me habia pagado una parte,
quiero una opcion para poder eliminar los pagos, escribiendo la palabra
BORRAR'. Mismo patron de seguridad que /api/admin/reset-test-data (que ya
usaba la palabra BORRAR), pero acotado a un solo job en vez de todo el CRM."""
import uuid


def _make_job_with_payments(app_module, suffix):
    client_id = f'client-delpay-{suffix}'
    job_id = f'job-delpay-{suffix}'
    other_job_id = f'job-delpay-other-{suffix}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Cancel', 'last_name': 'Test',
        'email': 'canceltest@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Cancelada Test',
        'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': other_job_id, 'client_id': client_id, 'nombre': 'Otra Boda Test',
        'tenant_id': 'tenant-norkevin',
    })
    pay_ids = []
    for i, (amt, status) in enumerate([(3000, 'Pagado'), (3000, 'Pendiente'), (3000, 'Pendiente')]):
        pid = 'pay-delpay-' + uuid.uuid4().hex[:6]
        app_module.store.upsert('payments', {
            'id': pid, 'invoice_id': f'INV-DELPAY-{suffix}', 'client_id': client_id, 'job_id': job_id,
            'amount': amt, 'status': status, 'due_date': '2026-01-01',
            'cuota': i + 1, 'concepto': 'Cuota', 'tenant_id': 'tenant-norkevin',
        })
        pay_ids.append(pid)
    other_pay_id = 'pay-delpay-other-' + uuid.uuid4().hex[:6]
    app_module.store.upsert('payments', {
        'id': other_pay_id, 'invoice_id': f'INV-DELPAY-OTHER-{suffix}', 'client_id': client_id,
        'job_id': other_job_id, 'amount': 5000, 'status': 'Pendiente', 'due_date': '2026-01-01',
        'cuota': 1, 'concepto': 'Cuota', 'tenant_id': 'tenant-norkevin',
    })
    return job_id, other_job_id, pay_ids, other_pay_id


def test_delete_payments_requires_confirm_keyword(auth_client):
    import app as app_module
    job_id, other_job_id, pay_ids, other_pay_id = _make_job_with_payments(app_module, uuid.uuid4().hex[:6])

    resp = auth_client.post(f'/api/jobs/{job_id}/delete-payments', json={})
    assert resp.status_code == 400
    assert resp.get_json()['ok'] is False

    # nada se borro
    remaining = [p for p in app_module.store.list('payments') if p.get('job_id') == job_id]
    assert len(remaining) == 3


def test_delete_payments_removes_paid_and_pending_for_this_job_only(auth_client):
    import app as app_module
    job_id, other_job_id, pay_ids, other_pay_id = _make_job_with_payments(app_module, uuid.uuid4().hex[:6])

    resp = auth_client.post(f'/api/jobs/{job_id}/delete-payments', json={'confirm': 'BORRAR'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['deleted'] == 3

    remaining_this_job = [p for p in app_module.store.list('payments') if p.get('job_id') == job_id]
    assert remaining_this_job == []

    # el otro job del mismo cliente no se toco
    other_job_payments = [p for p in app_module.store.list('payments') if p.get('job_id') == other_job_id]
    assert len(other_job_payments) == 1
    assert app_module.store.get('payments', other_pay_id) is not None


def test_delete_payments_does_not_touch_client_or_job_records(auth_client):
    import app as app_module
    job_id, other_job_id, pay_ids, other_pay_id = _make_job_with_payments(app_module, uuid.uuid4().hex[:6])

    auth_client.post(f'/api/jobs/{job_id}/delete-payments', json={'confirm': 'BORRAR'})

    assert app_module.get_job(job_id) is not None
    client_id = app_module.get_job(job_id)['client_id']
    assert app_module.get_client(client_id) is not None


def test_job_page_shows_delete_payments_button_only_when_there_are_payments(auth_client):
    import app as app_module
    job_id, other_job_id, pay_ids, other_pay_id = _make_job_with_payments(app_module, uuid.uuid4().hex[:6])

    trigger = "onclick=\"openModal('delete-payments-modal')\""
    resp = auth_client.get(f'/jobs/{job_id}')
    html = resp.get_data(as_text=True)
    assert trigger in html

    empty_job_id = f'job-delpay-empty-{uuid.uuid4().hex[:6]}'
    app_module.store.upsert('jobs', {
        'id': empty_job_id, 'client_id': app_module.get_job(job_id)['client_id'],
        'nombre': 'Boda Sin Pagos', 'tenant_id': 'tenant-norkevin',
    })
    resp2 = auth_client.get(f'/jobs/{empty_job_id}')
    html2 = resp2.get_data(as_text=True)
    assert trigger not in html2, 'sin pagos, no debe mostrarse el boton de eliminarlos'
