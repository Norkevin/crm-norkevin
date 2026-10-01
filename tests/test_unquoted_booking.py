import uuid
import pytest
from conftest import login_as_tenant


@pytest.mark.parametrize('tenant_id', ['tenant-norkevin', 'tenant-norkevin-photography'])
@pytest.mark.parametrize('path,payload', [('accept', {}), ('trigger-step', {'step_id': 'job_accepted', 'send_email': False})])
def test_accepting_without_quote_never_invents_money(client, tenant_id, path, payload):
    import app as module
    login_as_tenant(client, tenant_id)
    lead = {'id': 'unquoted-' + uuid.uuid4().hex[:8], 'nombre': 'Sin cotización', 'email': 'unquoted@example.invalid', 'tenant_id': tenant_id}
    module.store.upsert('leads', lead)
    response = client.post(f'/api/leads/{lead["id"]}/{path}', json=payload)
    assert response.status_code == 200
    job = module.get_job(module.get_lead(lead['id'])['job_id'])
    assert job['price_total'] == job['price_paid'] == job['cuota_monto'] == job['plan_pago'] == 0
    assert not job['boda_date'], 'An undated booking must not invent an event today'
    assert not job['package'] and not job['accepted_quote_id']
    assert not [p for p in module.store.list('payments') if p.get('job_id') == job['id']]
    html = client.get('/jobs/' + job['id']).get_data(as_text=True)
    assert 'Por definir' in html and 'Sin cotización aceptada' in html
    assert '15,000.00' not in html
    # A repeat acceptance does not invent a second job or reset a manual price.
    job['price_total'] = 4200
    module.upsert_job(job)
    client.post(f'/api/leads/{lead["id"]}/{path}', json=payload)
    assert module.get_job(job['id'])['price_total'] == 4200


@pytest.mark.parametrize('total', [0, 12345])
def test_quote_after_manual_acceptance_uses_actual_total(auth_client, total):
    import app as module
    login_as_tenant(auth_client, 'tenant-norkevin')
    lead = {'id': 'latequote-' + uuid.uuid4().hex[:8], 'nombre': 'Cotización posterior', 'tenant_id': 'tenant-norkevin'}
    module.store.upsert('leads', lead)
    with module.app.test_request_context():
        from flask import g
        g.public_tenant_id = lead['tenant_id']
        result = module._convert_lead_to_job(lead, create_payments=False)
        quote = {'id': 'quote-' + lead['id'], 'lead_id': lead['id'], 'precio_total': total, 'plan_pago': 5, 'paquete_nombre': 'Elegido', 'tenant_id': lead['tenant_id']}
        accepted = module._convert_lead_to_job(lead, quote=quote)
        assert accepted['job']['id'] == result['job']['id']
        assert accepted['job']['price_total'] == total
        assert accepted['job']['plan_pago'] == 5
        assert accepted['job']['package'] == 'Elegido'
        payments = [p for p in module.store.list('payments') if p.get('job_id') == result['job']['id']]
        assert len(payments) == 5 and sum(p['amount'] for p in payments) == total
