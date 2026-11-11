from copy import deepcopy

from src.teams import payment_agenda
from test_flow_teams import web, member, approved, run, pay, records
from test_payment_receipts import setup_invoice


def test_agenda_counts_only_the_pending_installment_and_orders_deadlines():
    base = dict(beneficiary_name='Equipo', job_name='Boda', due_date='2026-12-31',
                schedule=[], pending=10000)
    costs = [dict(base, id='future', due_date='2026-11-26'),
             dict(base, id='today', due_date='2026-11-11'),
             dict(base, id='late-new', due_date='2026-11-10'),
             dict(base, id='late-old', due_date='2026-10-01'),
             dict(base, id='undated', due_date=''),
             dict(base, id='paid', pending=0, due_date='2026-01-01'),
             dict(base, id='plan', pending=18000, schedule=[
                 dict(due_date='2026-11-01', pending=0),
                 dict(due_date='2026-11-25', pending=3000),
                 dict(due_date='2026-12-20', pending=15000)])]
    before = deepcopy(costs)
    agenda = payment_agenda(costs, '2026-11-11')
    late, today, soon = agenda['groups']
    assert [r['id'] for r in late['rows']] == ['late-old', 'late-new']
    assert [g['total'] for g in agenda['groups']] == [20000, 10000, 3000]
    assert soon['rows'][0]['due_amount'] == 3000
    assert soon['rows'][0]['pending'] == 18000
    assert agenda['undated'] == 10000
    assert costs == before


def test_agenda_keeps_cost_adjustments_and_deadline_caps_visible():
    base = dict(beneficiary_name='Equipo', job_name='Boda', due_date='2026-11-01',
                schedule=[dict(due_date='2026-12-01', pending=10000)])
    raised = payment_agenda([dict(base, pending=15000)], '2026-11-11')['groups'][0]
    assert raised['total'] == 15000
    lowered = payment_agenda([dict(base, pending=5000)], '2026-11-11')['groups'][0]
    assert lowered['total'] == 5000


def test_teams_deadlines_use_paid_installments_and_preserve_person_view(web):
    application, client, _ = web
    store = application.extensions['teams']
    cost = approved(store, member(store))
    run(store, 'schedule', cost_id=cost['id'], version=cost['version'],
        plan='2026-01-01 500\n2026-12-01 1000')
    payment = pay(store, cost, amount='200')
    before = deepcopy(records(store, 'payment'))
    html = client.get('/teams/payments').text
    assert 'Vencimientos' in html and 'Por persona' in html
    assert 'data-pay-amount="300.0"' in html
    assert 'Saldo del concepto: Q1,300.00' in html
    assert 'data-teams-back' in html
    assert records(store, 'payment') == before
    run(store, 'reverse', id=payment['id'], reason='Prueba', effective_date='2026-11-11')
    assert 'data-pay-amount="500.0"' in client.get('/teams/payments').text
    assert client.get('/teams/payments?days=30&tab=due').status_code == 200
    assert client.get('/teams/payments?days=-1').status_code == 400


def test_job_receipt_and_reminder_target_received_and_pending_installments(auth_client):
    import app as crm
    first, second = setup_invoice(crm)
    job_id = crm.store.get('payments', first)['job_id']
    html = auth_client.get(f'/jobs/{job_id}').text
    assert f'sendPaymentReceipt("{first}")' in html
    assert f'sendInvoice("{second}")' in html
    assert f'openRecordPayment("{second}", 7500.0)' in html
    assert 'Enviar recibo de pago' in html
    preview = auth_client.get(f'/api/payments/{first}/send-preview').json
    assert 'saldo pendiente de Q7,500.00' in preview['body']
    assert 'Cuota pendiente: Q7,500.00' in preview['body']
    assert 'febrero de 2027' in preview['body']


def test_partial_reminder_separates_total_balance_from_installment(auth_client):
    import app as crm
    first, second = setup_invoice(crm, paid=2000)
    for endpoint in ('send-preview', 'reminder-preview'):
        preview = auth_client.get(f'/api/payments/{first}/{endpoint}')
        assert preview.status_code == 200
        assert 'Q13,000.00' in preview.json['body']
        assert 'Cuota pendiente: Q5,500.00' in preview.json['body']
    job_id = crm.store.get('payments', first)['job_id']
    html = auth_client.get(f'/jobs/{job_id}').text
    assert f'sendPaymentReceipt("{first}")' in html
    assert f'openRecordPayment("{first}", 5500)' in html
    # An individual reminder still honors the explicitly selected later installment.
    preview = auth_client.get(f'/api/payments/{second}/send-preview').json
    assert 'Cuota pendiente: Q7,500.00' in preview['body']
    assert 'febrero de 2027' in preview['body']
