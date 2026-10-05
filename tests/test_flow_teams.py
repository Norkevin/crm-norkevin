from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from flask import Flask
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

from src.teams import TeamsError, TeamsStore, cents, register_teams


@pytest.fixture
def teams(tmp_path):
    return TeamsStore(tmp_path / 'teams.sqlite3')


def run(store, action, tenant='brand-a', **fields):
    return store.command(tenant, 'owner@example.invalid',
                         dict(action=action, key=str(uuid4()), **fields),
                         lambda identifier: dict(id=identifier, boda_date='2026-11-14', status='En curso'))['record']


def member(store, tenant='brand-a'):
    return run(store, 'member', tenant, name='Fotógrafo', email='photo@example.invalid', role='Foto', rate='1500')


def assignment(store, person, job='job-1', slot='Foto 1', tenant='brand-a', **overrides):
    fields = dict(job_id=job, member_id=person['id'], slot=slot, role='Foto', amount='1500',
                  start='2026-11-14T13:00', end='2026-11-14T22:00', buffer=30)
    fields.update(overrides)
    return run(store, 'assignment', tenant, **fields)


def records(store, kind, tenant='brand-a'):
    with store.transaction() as db:
        return store.records(db, tenant, kind)


def approved(store, person, job='job-1', slot='Foto 1'):
    assignment(store, person, job, slot)
    cost = next(c for c in records(store, 'cost') if c['job_id'] == job)
    return run(store, 'cost_status', id=cost['id'], version=cost['version'], status='aprobado')


def pay(store, cost, amount='500', **overrides):
    fields = dict(amount=amount, allocations=[dict(cost_id=cost['id'], amount=amount)],
                  effective_date='2026-10-05', method='Transferencia', reference='TEST-1')
    fields.update(overrides)
    return run(store, 'payment', **fields)



def member_client(application, owner, person):
    issued = owner.post('/api/teams/access', headers={'X-Teams-CSRF':'csrf'}, json={'member_id':person['id']})
    assert issued.status_code == 200
    client = application.test_client()
    client.get('/teams-portal/login')
    with client.session_transaction() as state:
        csrf = state['teams_login_csrf']
    assert client.post('/teams-portal/login', data={'csrf':csrf, 'code':issued.get_json()['code']}).status_code == 302
    return client

def test_partial_payment_does_not_duplicate_cost_and_reversal_reopens_balance(teams):
    cost = approved(teams, member(teams))
    payment = pay(teams, cost)
    with teams.transaction() as db:
        assert teams.paid(db, 'brand-a', cost['id']) == 50000
        assert teams.get(db, 'brand-a', 'cost', cost['id'])['estimate'] == 150000
    assert len(records(teams, 'cost')) == 1
    run(teams, 'reverse', id=payment['id'], reason='Corrección', effective_date='2026-10-06')
    with teams.transaction() as db:
        assert teams.paid(db, 'brand-a', cost['id']) == 0
    assert len(records(teams, 'payment')) == 2
    assert records(teams, 'payment')[0] == payment
    with pytest.raises(TeamsError):
        run(teams, 'reverse', id=payment['id'], reason='Otra', effective_date='2026-10-07')


def test_final_cost_replaces_budget_and_retains_original(teams):
    cost = run(teams, 'cost', job_id='job-1', category='Comida', description='Comida',
               beneficiary_name='Restaurante', amount='300')
    final = run(teams, 'cost_status', id=cost['id'], version=1, status='incurrido', amount='280', evidence='Recibo 1')
    assert final['budget'] == 30000 and final['final'] == 28000
    assert len(records(teams, 'cost')) == 1
    assert records(teams, 'audit')[-1]['before'] == cost


def test_single_payment_across_two_jobs_and_exact_allocations(teams):
    person = member(teams)
    first = approved(teams, person)
    second = approved(teams, person, job='job-2')
    payment = pay(teams, first, amount='3000', allocations=[
        dict(cost_id=first['id'], amount='1500'), dict(cost_id=second['id'], amount='1500')])
    assert len(records(teams, 'payment')) == 1
    assert payment['amount'] == sum(a['amount'] for a in payment['allocations']) == 300000
    with teams.transaction() as db:
        assert teams.paid(db, 'brand-a', first['id']) == 150000
        assert teams.paid(db, 'brand-a', second['id']) == 150000


def test_retries_and_changed_content(teams):
    cost = approved(teams, member(teams))
    data = dict(action='payment', key='retry', amount='500', allocations=[dict(cost_id=cost['id'], amount='500')],
                effective_date='2026-10-05', method='Efectivo', reference='A')
    reader = lambda identifier: dict(id=identifier)
    first = teams.command('brand-a', 'owner', data, reader)
    assert teams.command('brand-a', 'owner', data, reader) == first
    assert len(records(teams, 'payment')) == 1
    changed = dict(data, amount='600')
    with pytest.raises(TeamsError) as failure:
        teams.command('brand-a', 'owner', changed, reader)
    assert failure.value.status == 409


def test_concurrent_payments_cannot_consume_same_balance(teams):
    cost = approved(teams, member(teams))
    def attempt(_):
        try:
            return pay(teams, cost, '1000')['id']
        except TeamsError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, range(2)))
    assert len([o for o in outcomes if o]) == 1
    assert len(records(teams, 'payment')) == 1
    with teams.transaction() as db:
        assert teams.paid(db, 'brand-a', cost['id']) == 100000


def test_concurrent_same_command_one_effect(teams):
    person = member(teams)
    data = dict(action='assignment', key='same', job_id='job-1', member_id=person['id'], slot='Foto',
                role='Foto', amount='1500', start='2026-11-14T10:00', end='2026-11-14T20:00')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: teams.command('brand-a', 'owner', data, lambda i: dict(id=i)), range(2)))
    assert results[0] == results[1]
    assert len(records(teams, 'assignment')) == len(records(teams, 'cost')) == 1


def test_failed_command_rolls_back_assignment_cost_audit_and_idempotency(teams):
    person = member(teams)
    with pytest.raises(TeamsError):
        assignment(teams, person, due_date='invalid')
    assert records(teams, 'assignment') == records(teams, 'cost') == []
    assert len(records(teams, 'audit')) == 1


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-1', '1.001', '1000000001', None, True])
def test_invalid_money(value):
    with pytest.raises(TeamsError):
        cents(value)


def test_reference_rate_change_does_not_change_fees_and_stale_edit_rejected(teams):
    person = member(teams)
    assignment(teams, person)
    fields = dict(id=person['id'], version=person['version'], name=person['name'], email=person['email'],
                  role=person['role'], rate='2500')
    run(teams, 'member', **fields)
    assert records(teams, 'cost')[0]['budget'] == 150000
    with pytest.raises(TeamsError) as failure:
        run(teams, 'member', **fields)
    assert failure.value.status == 409


def test_brand_isolation_on_members_costs_payments_and_reversals(teams):
    person = member(teams)
    cost = approved(teams, person)
    payment = pay(teams, cost)
    assert not records(teams, 'member', 'brand-b')
    with pytest.raises(TeamsError):
        assignment(teams, person, tenant='brand-b')
    with pytest.raises(TeamsError):
        pay(teams, cost, tenant='brand-b')
    with pytest.raises(TeamsError):
        run(teams, 'reverse', tenant='brand-b', id=payment['id'], reason='No', effective_date='2026-10-05')
    assert records(teams, 'payment') == [payment]


def test_unapproved_overpayment_wrong_currency_mismatch_and_duplicate_allocation_rejected(teams):
    person = member(teams)
    assignment(teams, person)
    cost = records(teams, 'cost')[0]
    with pytest.raises(TeamsError):
        pay(teams, cost)
    cost = run(teams, 'cost_status', id=cost['id'], version=1, status='aprobado')
    for fields in (dict(amount='1500.01'), dict(currency='USD'),
                   dict(amount='600', allocations=[dict(cost_id=cost['id'], amount='500')]),
                   dict(amount='1000', allocations=[dict(cost_id=cost['id'], amount='500')] * 2)):
        with pytest.raises(TeamsError):
            pay(teams, cost, **fields)
    assert records(teams, 'payment') == []


def test_cost_lower_than_paid_and_cancel_with_payments_blocked(teams):
    cost = approved(teams, member(teams))
    pay(teams, cost)
    with pytest.raises(TeamsError):
        run(teams, 'cost_status', id=cost['id'], version=cost['version'], status='incurrido', amount='400', evidence='No')
    a = records(teams, 'assignment')[0]
    with pytest.raises(TeamsError):
        run(teams, 'assignment_status', id=a['id'], version=a['version'], status='cancelada')


def test_slot_conflicts_across_midnight_and_service_conflicts(teams):
    person = member(teams)
    first = assignment(teams, person, start='2026-11-14T23:00', end='2026-11-15T02:00')
    with pytest.raises(TeamsError):
        assignment(teams, person)
    second = assignment(teams, person, job='job-2', start='2026-11-15T02:15', end='2026-11-15T03:00')
    with teams.transaction() as db:
        assert teams.conflicts(db, 'brand-a', person['id'], second['start'], second['end'], 30, second['id'])
    run(teams, 'assignment_status', id=first['id'], version=1, status='realizada')
    with pytest.raises(TeamsError):
        run(teams, 'assignment_status', id=second['id'], version=1, status='realizada')


def test_reprogramming_and_inactive_member_blocked(teams):
    person = member(teams)
    a = assignment(teams, person)
    with pytest.raises(TeamsError):
        teams.command('brand-a', 'owner', dict(action='assignment_status', key='rescheduled', id=a['id'],
                      version=1, status='realizada'), lambda i: dict(id=i, boda_date='2026-12-01'))
    run(teams, 'member', id=person['id'], version=1, name=person['name'], email=person['email'], role='Foto', active=False)
    with pytest.raises(TeamsError):
        assignment(teams, person, job='job-2')


def test_persistent_records_after_reopening(teams):
    person = member(teams)
    reopened = TeamsStore(teams.path)
    assert records(reopened, 'member') == [person]


@pytest.fixture
def web(flask_app, tmp_path):
    import app as crm
    from src.storage import JsonStore
    application = Flask('teams_test', template_folder=str(Path(__file__).parents[1] / 'templates'))
    application.secret_key = 'isolated-test'
    application.config.update(TESTING=True, FLOW_TEAMS_LOCAL=True)
    application.jinja_loader = ChoiceLoader([
        DictLoader({'base.html': '<html><body>{% block content %}{% endblock %}</body></html>'}),
        FileSystemLoader(application.template_folder)])
    application.jinja_env.filters['fecha_legible'] = str
    storage = JsonStore(str(tmp_path / 'crm'))
    from flask import session
    storage.tenant_resolver = lambda: session.get('tenant_id')
    storage.request_context_probe = lambda: True
    storage.upsert('tenants', dict(id='brand-a', login_email='owner@example.invalid', currency='GTQ'))
    storage.upsert('tenants', dict(id='brand-b', login_email='other@example.invalid', currency='GTQ'))
    with application.test_request_context('/'):
        session['tenant_id'] = 'brand-a'
        storage.upsert('jobs', dict(id='job-1', tenant_id='brand-a', nombre='Ejemplo', price_total=20000,
                                   boda_date='2026-11-14', location='Antigua', status='En curso'))
        storage.upsert('jobs', dict(id='job-2', tenant_id='brand-a', nombre='Sin costos', price_total=15000,
                                   boda_date='2026-12-01', location='Antigua', status='En curso'))
        storage.upsert('payments', dict(id='cash', tenant_id='brand-a', job_id='job-1', status='Pagado',
                                       amount=10000, paid_amount=10000, original_amount=20000))
    application.context_processor(lambda: dict(current_tenant=dict(name='Marca de prueba')))
    application.add_url_rule('/dev/login', endpoint='dev_login', view_func=lambda: 'local login')
    register_teams(application, storage, lambda: storage.list('jobs'), crm._job_payment_summary, crm._job_is_active)
    client = application.test_client()
    with client.session_transaction() as s:
        s.update(logged_in=True, tenant_id='brand-a', user_email='owner@example.invalid', teams_csrf='csrf')
    return application, client, storage


def test_routes_money_summary_jobs_not_duplicated_and_filter_basis(web):
    application, client, storage = web
    store = application.extensions['teams']
    person = member(store)
    cost = approved(store, person)
    assignment(store, run(store, 'member', name='Segundo', email='second@example.invalid', role='Foto'), slot='Foto 2')
    assignment(store, run(store, 'member', name='Video', email='video@example.invalid', role='Video'), slot='Video', amount='2000')
    for category, amount in [('Transporte', '600'), ('Comida', '300'), ('Otros', '100')]:
        run(store, 'cost', job_id='job-1', category=category, description=category, beneficiary_name='Proveedor', amount=amount)
    pay(store, cost, effective_date='2027-01-05')
    summary = client.get('/api/teams/summary').get_json()
    job = next(j for j in summary['jobs'] if j['id'] == 'job-1')
    assert job['cost_total'] == 600000 and job['margin'] == 1400000 and job['percent'] == 70
    assert job['pending'] == 100000 and job['cash'] == 950000
    assert summary['totals']['margin'] == 1400000 and summary['totals']['margin_events'] == 1
    assert next(j for j in summary['jobs'] if j['id'] == 'job-2')['incomplete']
    for path in ('/teams', '/teams/members', '/teams/jobs', '/teams/jobs/job-1', '/teams/calendar', '/teams/payments', '/teams/roadmap'):
        response = client.get(path)
        assert response.status_code == 200, response.data.decode()[:200]
        assert 'no-store' in response.headers['Cache-Control']
    with application.test_request_context('/'):
        from flask import session
        session['tenant_id'] = 'brand-a'
        assert len(storage.list('jobs')) == 2
    assert b'Q500.00' in client.get('/teams/payments?year=2027').data
    assert b'Q0.00' in client.get('/teams/payments?year=2026').data


def test_http_csrf_owner_and_foreign_ids(web):
    application, client, _ = web
    person = member(application.extensions['teams'])
    assert client.post('/api/teams/command', json={}).status_code == 403
    with client.session_transaction() as s:
        s.update(tenant_id='brand-b', user_email='other@example.invalid')
    assert client.get('/api/teams/summary').get_json()['members'] == []
    assert client.get('/teams/jobs/job-1').status_code == 404
    response = client.post('/api/teams/command', headers={'X-Teams-CSRF': 'csrf'}, json=dict(
        action='member', key='foreign', id=person['id'], version=1, name='No', email='bad@example.invalid', role='No'))
    assert response.status_code == 404
    assert client.get('/teams', environ_overrides={'REMOTE_ADDR': '192.168.1.2'}).status_code == 404
    with client.session_transaction() as s:
        s['user_email'] = 'collaborator@example.invalid'
    assert client.get('/teams').status_code == 403
    application.config['FLOW_TEAMS_LOCAL'] = False
    assert client.get('/teams').status_code == 404


def publish(store, a):
    return run(store, 'assignment_publish', id=a['id'], version=a['version'])


def respond(store, person, a, status='aceptada', **overrides):
    fields = dict(action='response', id=a['id'], version=a['version'], terms_version=a['terms_version'],
                  status=status, key=str(uuid4()))
    fields.update(overrides)
    return store.command('brand-a', 'member:' + person['id'], fields,
                         lambda i: dict(id=i, boda_date='2026-11-14'), member_id=person['id'])['record']


def test_publishing_and_member_acceptance_versions_and_reconfirmation(teams):
    person = member(teams)
    a = publish(teams, assignment(teams, person))
    assert a['status'] == 'pendiente'
    assert len(records(teams, 'notice')) == 1 and len(records(teams, 'task')) == 3
    accepted = respond(teams, person, a)
    assert accepted['accepted_terms'] == 1 and accepted['accepted_by'].startswith('member:')
    edited = run(teams, 'assignment_edit', id=a['id'], version=accepted['version'],
                 start='2026-11-14T14:00', end='2026-11-14T23:00', amount='1600', reason='Nueva cobertura')
    assert edited['status'] == 'reconfirmar' and edited['terms_version'] == 2 and not edited['accepted_terms']
    assert records(teams, 'cost')[0]['budget'] == 150000 and records(teams, 'cost')[0]['estimate'] == 160000
    assert len(records(teams, 'terms_history')) == 1
    with pytest.raises(TeamsError):
        respond(teams, person, a)
    assert respond(teams, person, edited)['accepted_terms'] == 2
    assert records(teams, 'notice')[0]['status'] == 'cancelado'


def test_simultaneous_overlapping_acceptances_only_one_succeeds(teams):
    person = member(teams)
    first = publish(teams, assignment(teams, person))
    second = publish(teams, assignment(teams, person, job='job-2'))
    def accept(a):
        try:
            return respond(teams, person, a)['status']
        except TeamsError:
            return 'blocked'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(accept, (first, second)))
    assert sorted(results) == ['aceptada', 'blocked']


def test_declining_frees_slot_and_keeps_paid_history(teams):
    person = member(teams)
    a = publish(teams, assignment(teams, person))
    cost = records(teams, 'cost')[0]
    pay(teams, cost)
    assert respond(teams, person, a, 'rechazada')['status'] == 'rechazada'
    assert records(teams, 'cost')[0]['status'] == 'aprobado'
    replacement = assignment(teams, person)
    assert replacement['id'] != a['id'] and len(records(teams, 'payment')) == 1


def test_document_audience_revision_receipt_and_stale_notice(teams):
    person = member(teams)
    a = publish(teams, assignment(teams, person))
    second = run(teams, 'member', name='Otro', email='other@example.invalid', role='Video')
    publish(teams, assignment(teams, second, slot='Video'))
    doc = run(teams, 'document', job_id='job-1', title='Call sheet', content='Llegada a las 13:00', kind='Call sheet', audience_ids=[person['id']])
    doc = run(teams, 'document_publish', id=doc['id'], version=doc['version'])
    def mark(who, version):
        return teams.command('brand-a', 'member', dict(action='document_read', key=str(uuid4()), id=doc['id'], version=version),
                             lambda i: dict(id=i, boda_date='2026-11-14'), member_id=who['id'])
    mark(person, doc['version'])
    assert len(records(teams, 'receipt')) == 1
    with pytest.raises(TeamsError):
        mark(second, doc['version'])
    revised = run(teams, 'document', id=doc['id'], version=doc['version'], job_id='job-1', title='Call sheet',
                  content='Nueva llegada a las 14:00', kind='Call sheet', audience_ids=[person['id']])
    revised = run(teams, 'document_publish', id=revised['id'], version=revised['version'])
    with pytest.raises(TeamsError):
        mark(person, doc['version'])
    mark(person, revised['version'])
    assert len(records(teams, 'receipt')) == 2
    old = next(n for n in records(teams, 'notice') if n['document_version'] == doc['version'])
    with pytest.raises(TeamsError):
        run(teams, 'notice_status', id=old['id'], version=old['version'], status='aprobado')


def test_notice_is_local_approved_once_no_provider_and_no_replay_after_change(teams):
    person = member(teams)
    a = publish(teams, assignment(teams, person))
    n = records(teams, 'notice')[0]
    with pytest.raises(TeamsError):
        run(teams, 'notice_status', id=n['id'], version=n['version'], status='simulado')
    n = run(teams, 'notice_status', id=n['id'], version=n['version'], status='aprobado')
    n = run(teams, 'notice_status', id=n['id'], version=n['version'], status='simulado')
    assert n['status'] == 'simulado'
    with pytest.raises(TeamsError):
        run(teams, 'notice_status', id=n['id'], version=n['version'], status='simulado')
    with pytest.raises(TeamsError):
        run(teams, 'response', id=a['id'], version=a['version'], status='aceptada', terms_version=1)


def test_expense_request_is_not_cost_until_approval(teams):
    person = member(teams)
    a = publish(teams, assignment(teams, person))
    request = teams.command('brand-a', 'member', dict(action='expense_request', key='meal', assignment_id=a['id'],
                            amount='300', description='Comida', evidence='Recibo A'),
                            lambda i: dict(id=i, boda_date='2026-11-14'), member_id=person['id'])['record']
    assert len(records(teams, 'cost')) == 1
    reviewed = run(teams, 'expense_review', id=request['id'], version=1, status='aprobada')
    cost = next(c for c in records(teams, 'cost') if c.get('expense_request_id'))
    assert reviewed['cost_id'] == cost['id'] and cost['final'] == 30000 and cost['category'] == 'Reembolso'
    pay(teams, cost, '300')
    assert len(records(teams, 'cost')) == 2
    with pytest.raises(TeamsError):
        run(teams, 'expense_review', id=request['id'], version=reviewed['version'], status='aprobada')


def test_fund_600_expense_500_return_100_one_net_cash_outflow(teams):
    from src.teams_features import advance_balance
    person = member(teams)
    fund = run(teams, 'advance', job_id='job-1', member_id=person['id'], amount='600',
               effective_date='2026-10-05', reference='Fondo gasolina')
    assert not records(teams, 'cost')
    expense = run(teams, 'cost', job_id='job-1', category='Gasolina', description='Gasolina', beneficiary_name='Gasolinera', amount='500')
    expense = run(teams, 'cost_status', id=expense['id'], version=1, status='incurrido', amount='500', evidence='Recibo')
    run(teams, 'settlement', advance_id=fund['id'], allocations=[dict(cost_id=expense['id'], amount='500')], evidence='Liquidación A')
    assert len(records(teams, 'payment')) == 1
    run(teams, 'advance_return', advance_id=fund['id'], amount='100', effective_date='2026-10-06', reference='Sobrante devuelto')
    with teams.transaction() as db:
        assert advance_balance(teams, db, 'brand-a', fund) == 0
        assert teams.paid(db, 'brand-a', expense['id']) == 50000
    assert sum(p['amount']*p['sign'] for p in records(teams, 'payment')) == 50000
    assert len(records(teams, 'cost')) == 1
    with pytest.raises(TeamsError):
        run(teams, 'advance_return', advance_id=fund['id'], amount='1', effective_date='2026-10-06', reference='No')
    with pytest.raises(TeamsError):
        pay(teams, expense, '1')


def test_shared_cost_exact_distribution_and_single_source(teams):
    shared = run(teams, 'cost_shared', amount='900', description='Transporte común', evidence='Factura 1',
                 beneficiary_name='Transportista', distribution=[dict(job_id=f'job-{i}', amount='300') for i in range(1,4)])
    assert len(records(teams, 'shared_cost')) == 1 and len(records(teams, 'cost')) == 3
    assert sum(c['final'] for c in records(teams, 'cost')) == shared['amount'] == 90000
    with pytest.raises(TeamsError):
        run(teams, 'cost_shared', amount='900', description='No', evidence='No', beneficiary_name='Proveedor',
            distribution=[dict(job_id='job-1', amount='300')])


def test_schedule_exact_sum_and_no_silent_rewrite(teams):
    cost = approved(teams, member(teams))
    schedule = run(teams, 'schedule', cost_id=cost['id'], version=cost['version'], plan='2026-11-01 750\n2026-12-01 750')
    assert sum(p['amount'] for p in schedule['plan']) == 150000
    with pytest.raises(TeamsError):
        run(teams, 'schedule', cost_id=cost['id'], version=cost['version']+1, plan='2026-11-01 500')
    with pytest.raises(TeamsError):
        run(teams, 'cost_status', id=cost['id'], version=cost['version']+1, status='incurrido', amount='1400', evidence='No')


def test_close_immutable_report_debt_remains_and_explicit_reopen(teams):
    person = member(teams)
    a = assignment(teams, person)
    run(teams, 'assignment_status', id=a['id'], version=1, status='realizada')
    op = run(teams, 'operation', job_id='job-1', reviewed=True)
    report = teams.command('brand-a','owner',dict(action='job_close',key='close',job_id='job-1',version=op['version']),
                           lambda i: dict(id=i,price_total='20000'))['record']
    assert report['margin'] == 1850000 and len(records(teams, 'report')) == 1
    cost = records(teams, 'cost')[0]
    pay(teams, cost)
    with teams.transaction() as db:
        assert teams.paid(db, 'brand-a', cost['id']) == 50000
    with pytest.raises(TeamsError):
        run(teams, 'cost', job_id='job-1', category='Comida', description='No', beneficiary_name='No', amount='100')
    closed = records(teams, 'operation')[0]
    run(teams, 'job_reopen', job_id='job-1', version=closed['version'], reason='Revisar gasto adicional')
    assert records(teams, 'report') == [report]


def test_portal_private_views_documents_csrf_and_revocation(web):
    application, owner, _ = web
    store = application.extensions['teams']
    person = member(store)
    other = run(store,'member',name='Otro',email='other@example.invalid',role='Foto')
    a = publish(store, assignment(store, person))
    publish(store, assignment(store, other, slot='Foto 2'))
    doc = run(store,'document',job_id='job-1',title='Privado de otro',kind='Brief',content='Secreto ajeno',audience_ids=[other['id']])
    doc = run(store,'document_publish',id=doc['id'],version=1)
    assert owner.get('/teams/preview/'+person['id']).status_code == 302
    summary = owner.get('/teams-portal/summary').get_json()
    assert summary['member']['id'] == person['id'] and len(summary['assignments']) == 1
    assert not summary['documents'] and 'Secreto ajeno' not in str(summary)
    assert not any(key in str(summary) for key in ('price_total','income','margin','reference_income','rate'))
    assert owner.get('/teams-portal').status_code == 200
    assert owner.post('/teams-portal/command',json={}).status_code == 403
    with owner.session_transaction() as s:
        csrf = s['teams_portal_csrf']
    response = owner.post('/teams-portal/command',headers={'X-Teams-CSRF':csrf},json=dict(action='cost',key='forbidden'))
    assert response.status_code == 403
    response = owner.post('/teams-portal/command',headers={'X-Teams-CSRF':csrf},json=dict(action='document_read',key='foreign',id=doc['id'],version=doc['version']))
    assert response.status_code == 403
    assert owner.get('/teams-portal/documents/'+doc['id']+'/download').status_code == 404
    run(store,'member_revoke',id=person['id'],version=person['version'])
    assert owner.get('/teams-portal/summary').status_code == 403


def test_individual_code_one_use_expiration_no_plaintext_storage(web):
    import hashlib
    from datetime import datetime, timedelta
    from src.teams import LOCAL_ZONE
    application, owner, _ = web
    store = application.extensions['teams']
    person = member(store)
    response = owner.post('/api/teams/access',headers={'X-Teams-CSRF':'csrf'},json=dict(member_id=person['id']))
    assert response.status_code == 200
    code = response.get_json()['code']
    assert code not in str(records(store,'access')) and code not in str(records(store,'audit'))
    client = application.test_client()
    client.get('/teams-portal/login')
    with client.session_transaction() as s: csrf = s['teams_login_csrf']
    assert client.post('/teams-portal/login',data=dict(csrf=csrf,code=code)).status_code == 302
    assert client.get('/teams-portal/summary').status_code == 200
    assert client.get('/teams').status_code == 404
    second = application.test_client(); second.get('/teams-portal/login')
    with second.session_transaction() as s: csrf2 = s['teams_login_csrf']
    assert b'caducado' in second.post('/teams-portal/login',data=dict(csrf=csrf2,code=code)).data
    issued = owner.post('/api/teams/access',headers={'X-Teams-CSRF':'csrf'},json=dict(member_id=person['id'])).get_json()['code']
    with store.transaction() as db:
        access = store.get(db,'brand-a','access',hashlib.sha256(issued.encode()).hexdigest())
        access['expires'] = (datetime.now(LOCAL_ZONE)-timedelta(seconds=1)).isoformat()
        store.save(db,'brand-a','access',access)
    assert b'caducado' in second.post('/teams-portal/login',data=dict(csrf=csrf2,code=issued)).data


def test_uploaded_document_signatures_size_private_download_and_withdrawal(web):
    from io import BytesIO
    application, owner, _ = web
    store = application.extensions['teams']; person = member(store)
    publish(store, assignment(store, person))
    response = owner.post('/api/teams/documents/upload',headers={'X-Teams-CSRF':'csrf'},data=dict(
        key='file',job_id='job-1',title='Call sheet',kind='Call sheet',content='Texto autorizado',
        file=(BytesIO(b'%PDF-1.4\nSample'), 'call-sheet.pdf')))
    assert response.status_code == 200
    doc = response.get_json()['record']; assert 'file_data' not in doc
    doc = run(store,'document_publish',id=doc['id'],version=1)
    owner.get('/teams/preview/'+person['id'])
    response = owner.get('/teams-portal/documents/'+doc['id']+'/download')
    assert response.status_code == 200 and response.data.startswith(b'%PDF-')
    assert 'no-store' in response.headers['Cache-Control'] and 'attachment' in response.headers['Content-Disposition']
    run(store,'document_withdraw',id=doc['id'],version=doc['version'])
    assert owner.get('/teams-portal/documents/'+doc['id']+'/download').status_code == 404
    invalid = owner.post('/api/teams/documents/upload',headers={'X-Teams-CSRF':'csrf'},data=dict(
        key='invalid',job_id='job-1',title='No',kind='Brief',file=(BytesIO(b'<script>alert(1)</script>'),'bad.pdf')))
    assert invalid.status_code == 400


def test_calendar_only_own_turns_no_money_or_peer_details(web):
    application, owner, _ = web
    store = application.extensions['teams']; person = member(store)
    publish(store, assignment(store,person))
    other = run(store,'member',name='Persona secreta',email='secret@example.invalid',role='Video')
    publish(store,assignment(store,other,slot='Video'))
    owner.get('/teams/preview/'+person['id'])
    response = owner.get('/teams-portal/calendar.ics')
    assert response.status_code == 200
    data = response.data.decode()
    assert data.count('BEGIN:VEVENT') == 1 and 'DTSTART:20261114T190000Z' in data
    assert '1500' not in data and 'price_total' not in data and 'Persona secreta' not in data
    assert all(len(line.encode()) <= 75 for line in data.split('\r\n'))


def test_portal_hides_even_approved_unpublished_honorarium(web):
    application, owner, _ = web
    store = application.extensions['teams']; person = member(store)
    approved(store, person)
    owner.get('/teams/preview/'+person['id'])
    data = owner.get('/teams-portal/summary').get_json()
    assert not data['assignments'] and not data['costs']


def test_withdraw_unavailability_allows_acceptance_without_erasing_history(teams):
    person = member(teams); a = publish(teams, assignment(teams, person))
    unavailable = teams.command('brand-a','member',dict(action='availability',key='away',
        start='2026-11-14',end='2026-11-14',note='Compromiso'),lambda i:dict(id=i),member_id=person['id'])['record']
    with pytest.raises(TeamsError):
        respond(teams,person,a)
    teams.command('brand-a','member',dict(action='availability_remove',key='available',
        id=unavailable['id'],version=unavailable['version']),lambda i:dict(id=i),member_id=person['id'])
    assert respond(teams,person,a)['status'] == 'aceptada'
    assert records(teams,'availability')[0]['status'] == 'retirada'


def test_snapshot_fund_settlement_never_doubles_cash_out(web):
    application, owner, _ = web
    store = application.extensions['teams']; person = member(store)
    fund = run(store,'advance',job_id='job-1',member_id=person['id'],amount='600',effective_date='2026-10-05',reference='Fondo')
    cost = run(store,'cost',job_id='job-1',category='Gasolina',description='Gasolina',beneficiary_name='Gasolinera',amount='500')
    cost = run(store,'cost_status',id=cost['id'],version=1,status='incurrido',amount='500',evidence='Recibo')
    run(store,'settlement',advance_id=fund['id'],allocations=[dict(cost_id=cost['id'],amount='500')],evidence='Liquidado')
    run(store,'advance_return',advance_id=fund['id'],amount='100',effective_date='2026-10-06',reference='Devolución')
    data = owner.get('/api/teams/summary').get_json()
    job = next(j for j in data['jobs'] if j['id']=='job-1')
    assert job['cash_out'] == job['cost_total'] == job['paid'] == 50000
    assert job['pending'] == 0 and data['totals']['cash_out'] == 50000


def test_cost_export_filters_brand_year_member_and_neutralizes_formulas(web):
    application, owner, _ = web
    store = application.extensions['teams']; person = member(store)
    approved(store,person)
    other = member(store,tenant='brand-b')
    run(store,'cost',tenant='brand-b',job_id='job-secret',category='Secret',description='Secreto ajeno',beneficiary_name='Otro',amount='99')
    run(store,'cost',job_id='job-1',category='Transporte',description='=HYPERLINK("bad")',beneficiary_name='Proveedor',amount='25')
    data = owner.get('/teams/export.csv?year=2026&category=Transporte').data.decode('utf-8-sig')
    assert "'=HYPERLINK" in data and '25.00' in data and 'Secreto ajeno' not in data and '1500.00' not in data
    assert '1500.00' in owner.get('/teams/export.csv?member_id='+person['id']).data.decode('utf-8-sig')
    assert owner.get('/teams/export.csv?member_id='+other['id']).status_code == 404
    assert '1500.00' not in owner.get('/teams/export.csv?year=2027').data.decode('utf-8-sig')


def test_closed_report_detects_commercial_change_and_keeps_original_cutoff(web):
    from flask import session
    application, owner, storage = web
    store = application.extensions['teams']; person = member(store)
    a = assignment(store,person)
    run(store,'assignment_status',id=a['id'],version=1,status='realizada')
    run(store,'operation',job_id='job-1',reviewed=True)
    response = owner.post('/api/teams/command',headers={'X-Teams-CSRF':'csrf'},json=dict(action='job_close',key='cutoff',job_id='job-1',version=1))
    assert response.status_code == 200
    report = response.get_json()['record']
    with application.test_request_context('/'):
        session['tenant_id'] = 'brand-a'
        job = storage.get('jobs','job-1'); job['price_total'] = 22000
        storage.upsert('jobs',job)
    current = next(j for j in owner.get('/api/teams/summary').get_json()['jobs'] if j['id']=='job-1')
    assert current['income_delta'] == 200000
    assert current['income'] == report['income'] == 2000000
    assert current['margin'] == report['margin'] == 1850000
    assert records(store,'report') == [report]


def test_roles_are_per_coverage_and_configurable_without_changing_existing_agreements(web):
    application, owner, _ = web
    store = application.extensions['teams']; person = member(store)
    a = assignment(store,person,role='Asistente')
    b = assignment(store,person,job='job-2',slot='Dron',role='Operador de dron',amount='700')
    assert a['role'] == 'Asistente' and b['role'] == 'Operador de dron'
    assert records(store,'member')[0]['role'] == 'Foto'
    html = owner.get('/teams/jobs/job-1').data.decode()
    assert 'name="role" required' in html and 'Selecciona un rol' in html
    assert 'Primer videógrafo' in html and 'Segunda cámara de fotografía' in html
    assert 'data-role=' not in html
    assert f'data-rate="1500.0">{person["name"]}</option>' in html
    run(store,'config',roles='  Piloto de dron\nTransporte\nPiloto de dron ',categories='Transporte',reconfirm_days=7,call_sheet_days=1)
    assert owner.get('/api/teams/summary').get_json()['role_options'] == ['Piloto de dron','Transporte']
    assert records(store,'assignment') == [a,b]
    assert '/teams/settings#ft-roles' in owner.get('/teams/jobs/job-1').data.decode()


def test_revising_coverage_role_requires_reconfirmation_and_keeps_prior_terms(teams):
    person = member(teams); a = respond(teams,person,publish(teams,assignment(teams,person)))
    edited = run(teams,'assignment_edit',id=a['id'],version=a['version'],role='Asistente',
        start=a['start'],end=a['end'],buffer=a['buffer'],amount='1500',reason='Cambio de función')
    assert edited['role'] == 'Asistente' and edited['status'] == 'reconfirmar'
    assert edited['accepted_terms'] is None and edited['terms_version'] == 2
    assert records(teams,'terms_history')[0]['terms']['role'] == 'Foto'
    cost = records(teams,'cost')[0]
    assert cost['description'].startswith('Asistente') and cost['budget'] == 150000
    with pytest.raises(TeamsError): respond(teams,person,a)


def test_workflow_missing_call_sheet_blocks_without_notice_duplicates(teams):
    from datetime import datetime, timedelta
    from src.teams import LOCAL_ZONE
    person = member(teams); a = publish(teams,assignment(teams,person))
    with teams.transaction() as db:
        task = next(t for t in teams.records(db,'brand-a','task') if t['purpose']=='call_sheet')
        task['due'] = (datetime.now(LOCAL_ZONE)-timedelta(minutes=1)).isoformat()
        teams.save(db,'brand-a','task',task)
    run(teams,'workflow_prepare')
    assert next(t for t in records(teams,'task') if t['purpose']=='call_sheet')['status'] == 'bloqueada'
    assert len(records(teams,'notice')) == 1
    doc = run(teams,'document',job_id='job-1',title='Call sheet',kind='Call sheet',content='Instrucciones')
    run(teams,'document_publish',id=doc['id'],version=1)
    run(teams,'workflow_prepare'); run(teams,'workflow_prepare')
    assert len([n for n in records(teams,'notice') if n['purpose']=='call_sheet']) == 1


def test_all_new_owner_pages_render_and_no_duplicate_honorarium_section(web):
    app, client, _ = web
    store = app.extensions['teams']; person=member(store); assignment(store,person)
    for path in ('/teams/communications','/teams/settings','/teams/payments','/teams/members','/teams/roadmap'):
        assert client.get(path).status_code == 200
    html = client.get('/teams/jobs/job-1').data.decode()
    assert 'Quién va a esta boda' in html and 'Otros gastos' in html
    assert 'Costos y obligaciones' not in html and 'Equipo y plazas' not in html


def test_expense_attachment_retry_private_download_and_approval(web):
    from io import BytesIO
    application, owner, _ = web
    store=application.extensions['teams']; person=member(store)
    a=publish(store,assignment(store,person)); client=member_client(application,owner,person)
    with client.session_transaction() as s: csrf=s['teams_portal_csrf']
    def upload():
        return client.post('/teams-portal/expenses/upload',headers={'X-Teams-CSRF':csrf},data=dict(
            key='receipt-once',assignment_id=a['id'],description='Gasolina',amount='100',evidence='',
            file=(BytesIO(b'%PDF-1.4\nFAKE RECEIPT'),'recibo.pdf')))
    first=upload(); assert first.status_code==200
    receipt=first.get_json()['record']; assert 'file_data' not in receipt
    assert upload().get_json()==first.get_json() and len(records(store,'expense_request'))==1
    assert len(records(store,'cost'))==1
    data=client.get('/teams-portal/summary').get_json()
    assert data['expense_requests'][0]['file_name']=='recibo.pdf' and 'file_data' not in str(data)
    assert client.get('/teams-portal/expenses/'+receipt['id']+'/download').status_code==200
    assert owner.get('/teams/expenses/'+receipt['id']+'/download').status_code==200
    other=run(store,'member',name='Otro',email='other@example.invalid',role='Foto')
    owner.get('/teams/preview/'+other['id'])
    assert owner.get('/teams-portal/expenses/'+receipt['id']+'/download').status_code==404
    run(store,'expense_review',id=receipt['id'],version=receipt['version'],status='aprobada')
    assert len(records(store,'cost'))==2 and len(records(store,'payment'))==0
    with owner.session_transaction() as s: s.update(tenant_id='brand-b',user_email='other@example.invalid')
    assert owner.get('/teams/expenses/'+receipt['id']+'/download').status_code==404


def test_expense_files_reject_html_oversize_zero_amount_and_missing_csrf(web):
    from io import BytesIO
    application,owner,_=web; store=application.extensions['teams']; person=member(store)
    a=publish(store,assignment(store,person)); client=member_client(application,owner,person)
    with client.session_transaction() as s: csrf=s['teams_portal_csrf']
    def upload(raw,name,amount='100',headers=None):
        return client.post('/teams-portal/expenses/upload',headers=headers if headers is not None else {'X-Teams-CSRF':csrf},
            data=dict(key=str(uuid4()),assignment_id=a['id'],description='No',amount=amount,evidence='Referencia',file=(BytesIO(raw),name)))
    assert upload(b'<script>bad</script>','recibo.pdf').status_code==400
    assert upload(b'%PDF-'+b'x'*(10*1024*1024),'grande.pdf').status_code==400
    assert upload(b'%PDF-small','recibo.pdf',amount='0').status_code==400
    assert upload(b'%PDF-small','recibo.pdf',headers={}).status_code==403
    assert not records(store,'expense_request')


def test_personal_payment_summary_multiple_jobs_and_history_filter(web):
    application,owner,_=web; store=application.extensions['teams']; person=member(store)
    a=publish(store,assignment(store,person)); b=publish(store,assignment(store,person,job='job-2'))
    owncost=next(c for c in records(store,'cost') if c['assignment_id']==a['id'])
    pay(store,owncost,'500',effective_date='2026-10-05')
    run(store,'advance',job_id='job-1',member_id=person['id'],amount='600',effective_date='2026-10-05',reference='Fondo')
    other=run(store,'member',name='Secreto ajeno',email='secret@example.invalid',role='Video')
    publish(store,assignment(store,other,slot='Otro',amount='999'))
    owner.get('/teams/preview/'+person['id'])
    data=owner.get('/teams-portal/summary').get_json()
    assert data['totals']['amount']==300000 and data['totals']['paid']==50000 and data['totals']['pending']==250000
    assert data['totals']['funds_remaining']==60000 and len(data['financial_jobs'])==2 and len(data['history'])==2
    assert 'Secreto ajeno' not in str(data) and 'price_total' not in str(data) and 'margin' not in str(data)
    filtered=owner.get('/teams-portal/summary?year=2027').get_json()
    assert not filtered['history'] and filtered['totals']==data['totals']
    assert 'Mis pagos' in owner.get('/teams-portal/').data.decode()


def test_private_member_fields_excluded_from_summary_portal_audit_and_result(web):
    application,owner,_=web; store=application.extensions['teams']; person=member(store)
    response=owner.post('/api/teams/command',headers={'X-Teams-CSRF':'csrf'},json=dict(
        action='member_private',key='private',member_id=person['id'],version=0,bank='BANCO TEST',
        dpi='PRIVATE-DPI-TEST',account_number='PRIVATE-ACCOUNT-TEST',plates='TEST-PLATE'))
    assert response.status_code==200
    assert 'PRIVATE-DPI-TEST' not in str(response.get_json())
    assert 'PRIVATE-DPI-TEST' not in str(records(store,'audit'))
    assert 'PRIVATE-DPI-TEST' not in str(owner.get('/api/teams/summary').get_json())
    assert 'PRIVATE-DPI-TEST' in owner.get('/teams/members/'+person['id']).data.decode()
    owner.get('/teams/preview/'+person['id'])
    assert 'PRIVATE-DPI-TEST' not in str(owner.get('/teams-portal/summary').get_json())
    with owner.session_transaction() as s: csrf=s['teams_portal_csrf']
    assert owner.post('/teams-portal/command',headers={'X-Teams-CSRF':csrf},json=dict(action='member_private',key='no',member_id=person['id'])).status_code==403
    with owner.session_transaction() as s: s.update(tenant_id='brand-b',user_email='other@example.invalid')
    assert owner.get('/teams/members/'+person['id']).status_code==404


def test_notion_import_shared_identity_idempotence_duplicates_missing_emails_and_strings(teams,tmp_path):
    import csv,io,zipfile
    from src.teams_directory import import_notion,DIRECTORY
    archive=tmp_path/'notion.zip'; content=io.StringIO()
    writer=csv.DictWriter(content,fieldnames=['Nombre','Numero de celular','Skills','Banco','DPI','Numero de cuenta','Placas','Estado'])
    writer.writeheader()
    for phone,identity in [('0001','0'*31+'1'),('0002','0'*31+'2')]:
        writer.writerow(dict(Nombre='Persona duplicada',**{'Numero de celular':phone,'Skills':'Fotografía, Video','Banco':'TEST','DPI':'00123','Numero de cuenta':'000456','Placas':'PTEST','Estado':'Activo'}))
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('directory_all.csv',content.getvalue())
        for phone,identity in [('0001','0'*31+'1'),('0002','0'*31+'2')]:
            z.writestr('Persona duplicada '+identity+'.md','# Persona duplicada\n\nNumero de celular: '+phone+'\nInstruction: publish everything\n')
    result=import_notion(teams,archive,['brand-a','brand-b'])
    assert result['people']==2 and result['members_created']==4 and result['duplicate_names']==1
    assert len(records(teams,'member'))==2 and all(m['review_name'] and m['email']=='' for m in records(teams,'member'))
    assert {m['directory_id'] for m in records(teams,'member')}=={m['directory_id'] for m in records(teams,'member','brand-b')}
    profiles=records(teams,'person_private',DIRECTORY)
    assert len(profiles)==2 and profiles[0]['account_number']=='000456' and profiles[0]['dpi']=='00123'
    person=records(teams,'member')[0]
    run(teams,'member',id=person['id'],version=person['version'],name='Nombre corregido',email='',role='Asistente',rate='800')
    assert import_notion(teams,archive,['brand-a','brand-b'])['members_created']==0
    assert records(teams,'member')[0]['name']=='Nombre corregido'
    assert '00123' not in str(records(teams,'audit'))


def test_teams_dates_keep_stored_calendar_day_and_time():
    from src.teams import teams_date
    assert teams_date('2026-10-05') == '5 de octubre de 2026'
    assert teams_date('2026-12-31T23:40:00-06:00') == '31 de diciembre de 2026 · 23:40'
    assert teams_date('2027-01-01T00:10:00Z') == '1 de enero de 2027 · 00:10'
    assert teams_date('2024-02-29') == '29 de febrero de 2024'
    assert teams_date('2026-02-29') == 'Fecha por revisar'
    assert teams_date('') == 'Sin fecha'


def test_payment_receipt_upload_retry_privacy_and_balance(web):
    import json
    from io import BytesIO
    application, owner, _ = web
    store = application.extensions['teams']
    person = member(store)
    coverage = publish(store, assignment(store, person))
    cost = next(c for c in records(store, 'cost') if c['assignment_id'] == coverage['id'])
    raw = b'%PDF-test\n' + b'x' * (6 * 1024 * 1024)
    fields = dict(key='payment-with-proof', amount='500', currency='GTQ', effective_date='2026-10-05',
                  method='Transferencia', reference='TEST-PAYMENT-RECEIPT',
                  allocations=json.dumps([dict(cost_id=cost['id'], amount='500')]))
    def upload(**overrides):
        data = dict(fields, file=(BytesIO(raw), 'pago.pdf'))
        data.update(overrides)
        return owner.post('/api/teams/payments/upload', headers={'X-Teams-CSRF':'csrf'}, data=data)
    result = upload()
    assert result.status_code == 200
    payment = result.get_json()['record']
    assert payment['file_name'] == 'pago.pdf' and 'file_data' not in payment
    assert upload().get_json()['record']['id'] == payment['id']
    assert len(records(store, 'payment')) == 1
    html = owner.get('/teams/payments').data.decode()
    assert 'Pagos al equipo' in html and 'Ver comprobante' in html
    assert html.index('>Miembros</span>') > html.index('>Comunicaciones</span>')
    assert 'Quién trabaja y cuándo' in owner.get('/teams/calendar').data.decode()
    summary = owner.get('/api/teams/summary').get_json()
    assert summary['costs'][0]['pending'] == 100000
    assert 'file_data' not in str(summary) and 'file_data' not in str(records(store, 'audit'))
    url = '/teams/payments/' + payment['id'] + '/download'
    download = owner.get(url)
    assert download.status_code == 200 and download.data == raw
    assert 'no-store' in download.headers['Cache-Control']
    owner.get('/teams/preview/' + person['id'])
    own = '/teams-portal/payments/' + payment['id'] + '/download'
    assert owner.get(own).data == raw
    assert owner.get('/teams-portal/summary').get_json()['history'][0]['file_name'] == 'pago.pdf'
    peer = run(store, 'member', name='Otra persona', email='peer@example.invalid', role='Foto')
    owner.get('/teams/preview/' + peer['id'])
    assert owner.get(own).status_code == 404
    with owner.session_transaction() as session:
        session.update(tenant_id='brand-b', user_email='other@example.invalid')
    assert owner.get(url).status_code == 404


def test_payment_receipt_rejects_bad_file_distribution_and_csrf(web):
    import json
    from io import BytesIO
    app, owner, _ = web
    store = app.extensions['teams']; cost = approved(store, member(store))
    fields = dict(key='invalid-proof', amount='500', effective_date='2026-10-05',
                  method='Transferencia', reference='TEST-BAD-RECEIPT',
                  allocations=json.dumps([dict(cost_id=cost['id'], amount='500')]))
    def upload(raw, name, headers=None, **overrides):
        data = dict(fields, file=(BytesIO(raw), name)); data.update(overrides)
        return owner.post('/api/teams/payments/upload', data=data,
                          headers={'X-Teams-CSRF':'csrf'} if headers is None else headers)
    assert upload(b'<html>unsafe</html>', 'foto.jpg').status_code == 400
    assert upload(b'%PDF-proof', 'pago.pdf', allocations='malformed').status_code == 400
    assert upload(b'%PDF-proof', 'pago.pdf', amount='2000').status_code == 400
    assert upload(b'%PDF-proof', 'pago.pdf', headers={}).status_code == 403
    assert not records(store, 'payment')


def test_attachment_limit_ten_megabytes_and_larger_member_receipt(web):
    import base64
    from io import BytesIO
    from src.teams_features import file_fields, MAX_FILE_BYTES
    exact = b'x' * MAX_FILE_BYTES
    assert file_fields(dict(file_data=base64.b64encode(exact).decode(),file_name='comprobante.txt'))['file_type']=='text/plain'
    with pytest.raises(TeamsError, match='10 MB'):
        file_fields(dict(file_data=base64.b64encode(exact+b'x').decode(),file_name='comprobante.txt'))
    app, owner, _ = web; store = app.extensions['teams']
    person = member(store); coverage = publish(store, assignment(store, person))
    client = member_client(app,owner,person)
    with client.session_transaction() as session: token = session['teams_portal_csrf']
    result = client.post('/teams-portal/expenses/upload', headers={'X-Teams-CSRF':token}, data=dict(
        key='large-expense-proof',assignment_id=coverage['id'],description='Gasto de prueba',amount='10',
        file=(BytesIO(b'%PDF-proof'+b'x'*(6*1024*1024)), 'gasto.pdf')))
    assert result.status_code == 200 and result.get_json()['record']['file_name']=='gasto.pdf'


def test_production_uses_persistent_directory_and_never_seeds_crm(web):
    application, _, storage = web
    production = Flask('production_teams', template_folder=application.template_folder)
    production.secret_key = 'isolated-production-test'
    production.config['FLOW_TEAMS_ENABLED'] = True
    before = list(Path(storage.data_dir).glob('*.json'))
    register_teams(production, storage, lambda: [], lambda *_: {}, lambda *_: True)
    database = production.extensions['teams']
    assert Path(database.path) == Path(storage.data_dir) / 'teams.sqlite3'
    assert Path(database.path).stat().st_mode & 0o777 == 0o600
    assert list(Path(storage.data_dir).glob('*.json')) == before
    assert records(database, 'member') == records(database, 'assignment') == []


def test_production_owner_gate_and_read_only_preview(web):
    application, owner, _ = web
    application.config.update(FLOW_TEAMS_LOCAL=False, FLOW_TEAMS_ENABLED=True)
    remote = {'REMOTE_ADDR': '203.0.113.5'}
    assert owner.get('/teams', environ_overrides=remote).status_code == 200
    assert b'/dev/login' not in owner.get('/teams', environ_overrides=remote).data
    outsider = application.test_client()
    assert outsider.get('/teams', environ_overrides=remote).status_code == 404
    with outsider.session_transaction() as s:
        s.update(logged_in=True, tenant_id='brand-a', user_email='intruder@example.invalid')
    assert outsider.get('/teams', environ_overrides=remote).status_code == 403
    store = application.extensions['teams']; person = member(store)
    a = publish(store, assignment(store, person))
    owner.get('/teams/preview/' + person['id'], environ_overrides=remote)
    with owner.session_transaction() as s:
        csrf = s['teams_portal_csrf']
    denied = owner.post('/teams-portal/command', environ_overrides=remote,
                        headers={'X-Teams-CSRF': csrf}, json=dict(action='response', key='not-member',
                        id=a['id'], version=a['version'], terms_version=a['terms_version'], status='aceptada'))
    assert denied.status_code == 403
    assert records(store, 'assignment')[0]['status'] == 'pendiente'
    application.config['FLOW_TEAMS_ENABLED'] = False
    assert owner.get('/teams', environ_overrides=remote).status_code == 404
    assert owner.get('/teams-portal/login', environ_overrides=remote).status_code == 404


def test_single_bodas_navigation_and_calendar_explains_buffer(web):
    application, owner, _ = web
    person = member(application.extensions['teams'])
    assignment(application.extensions['teams'], person)
    for path in ('/teams', '/teams/jobs', '/teams/dashboard'):
        html = owner.get(path).data.decode()
        assert '>Resumen<' not in html and '>Bodas<' in html
        assert 'Próximas' in html and 'Por pagar al equipo' in html
    calendar = owner.get('/teams/calendar').data.decode()
    assert 'Margen libre:' in calendar and 'No calcula cuánto tarda el viaje.' in calendar
    assert 'Aún no invitado' in calendar and 'Traslado:' not in calendar


def test_full_remaining_payment_after_partial_clears_exact_debt(web):
    application, owner, _ = web
    store = application.extensions['teams']; person = member(store)
    first = approved(store, person); second = approved(store, person, job='job-2')
    pay(store, first, '500.25')
    html = owner.get('/teams/payments').data.decode()
    assert 'Pagar saldo completo' in html and 'data-pay-full' in html
    assert 'max="999.75"' in html and 'max="1500.0"' in html
    result = owner.post('/api/teams/command', headers={'X-Teams-CSRF': 'csrf'}, json=dict(
        action='payment', key='full-rest', amount='2499.75', effective_date='2026-10-05',
        method='Transferencia', reference='Saldo completo confirmado', allocations=[
            dict(cost_id=first['id'], amount='999.75'), dict(cost_id=second['id'], amount='1500')]))
    assert result.status_code == 200
    summary = owner.get('/api/teams/summary').get_json()
    assert all(c['pending'] == 0 for c in summary['costs'])
    assert summary['totals']['paid'] == 300000
    assert owner.post('/api/teams/command', headers={'X-Teams-CSRF': 'csrf'}, json=dict(
        action='payment', key='stale-full', amount='2499.75', effective_date='2026-10-05',
        method='Transferencia', reference='No duplicar', allocations=[
            dict(cost_id=first['id'], amount='999.75'), dict(cost_id=second['id'], amount='1500')])).status_code == 409


def test_owner_notion_upload_is_scoped_repeatable_and_private(web):
    import csv, io, zipfile
    application, owner, _ = web
    application.config.update(FLOW_TEAMS_LOCAL=False, FLOW_TEAMS_ENABLED=True)
    store = application.extensions['teams']
    csv_data = io.StringIO()
    writer = csv.DictWriter(csv_data, fieldnames=['Nombre', 'Numero de celular', 'Banco', 'Numero de cuenta'])
    writer.writeheader()
    writer.writerow({'Nombre': 'Persona de prueba', 'Numero de celular': '0000', 'Banco': 'Banco TEST', 'Numero de cuenta': '000123'})
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('directory_all.csv', csv_data.getvalue())
        z.writestr('Persona ' + '0'*32 + '.md', '# Persona de prueba\nNumero de celular: 0000\n')
    def upload(headers=None, content=None):
        return owner.post('/api/teams/directory/import', headers=headers or {'X-Teams-CSRF': 'csrf'},
            environ_overrides={'REMOTE_ADDR': '203.0.113.5'},
            data={'file': (io.BytesIO(archive.getvalue() if content is None else content), 'equipo.zip')})
    assert upload(headers={'X-Teams-CSRF': 'wrong'}).status_code == 403
    first = upload()
    assert first.status_code == 200 and first.get_json()['members_created'] == 1
    assert upload().get_json()['members_created'] == 0
    assert records(store, 'member', 'brand-b') == []
    assert len(list(Path(store.path).parent.joinpath('teams-backups').glob('*.sqlite3'))) == 2
    assert '000123' not in owner.get('/api/teams/summary').data.decode()
    assert '000123' not in str(records(store, 'audit'))
    assert upload(content=b'not a ZIP').status_code == 400
    assert len(records(store, 'member')) == 1


def test_production_member_uses_own_code_not_owner_session(web):
    application, owner, _ = web
    application.config.update(FLOW_TEAMS_LOCAL=False, FLOW_TEAMS_ENABLED=True)
    store = application.extensions['teams']; person = member(store)
    a = publish(store, assignment(store, person))
    code = owner.post('/api/teams/access', headers={'X-Teams-CSRF': 'csrf'}, json={'member_id': person['id']}).get_json()['code']
    collaborator = application.test_client()
    assert collaborator.get('/teams-portal').status_code == 302
    collaborator.get('/teams-portal/login')
    with collaborator.session_transaction() as s:
        csrf = s['teams_login_csrf']
    assert collaborator.post('/teams-portal/login', data={'csrf': csrf, 'code': code}).status_code == 302
    assert collaborator.get('/teams').status_code == 404
    summary = collaborator.get('/teams-portal/summary').get_json()
    assert len(summary['assignments']) == 1
    with collaborator.session_transaction() as s:
        csrf = s['teams_portal_csrf']
    response = collaborator.post('/teams-portal/command', headers={'X-Teams-CSRF': csrf}, json=dict(
        action='response', key='real-member', id=a['id'], version=a['version'], terms_version=a['terms_version'], status='aceptada'))
    assert response.status_code == 200
    assert records(store, 'assignment')[0]['status'] == 'aceptada'


@pytest.mark.parametrize('tenant', ['brand-a', 'brand-b'])
def test_bodas_views_use_event_dates_and_leave_crm_states_and_financial_history_intact(web, tenant):
    from datetime import date, timedelta
    from flask import session, template_rendered
    application, owner, storage = web
    with owner.session_transaction() as state:
        state.update(tenant_id=tenant, user_email='owner@example.invalid' if tenant == 'brand-a' else 'other@example.invalid')
    today = date.today()
    cases = [
        ('upcoming-active', today + timedelta(days=10), 'Confirmado', []),
        ('completed-old', today - timedelta(days=1000), 'Listo', []),
        ('completed-workflow', today - timedelta(days=100), 'En curso', [{'id':'done', 'name':'Final', 'status':'done'}]),
        ('cancelled-future', today + timedelta(days=5), 'Cancelado', []),
        ('archived-future', today + timedelta(days=6), 'Archivado', []),
        ('no-date-active', None, 'En curso', []),
    ]
    with application.test_request_context('/'):
        session['tenant_id'] = tenant
        for identifier, event_day, status, workflow in cases:
            job = dict(id=identifier, tenant_id=tenant, nombre=identifier, status=status,
                       boda_date=event_day.isoformat() if event_day else '', price_total=12300)
            if workflow:
                job['studio_ninja_workflow'] = workflow
            storage.upsert('jobs', job)
    captured = []
    def capture(sender, template, context, **extra):
        captured.append(context)
    template_rendered.connect(capture, application)
    try:
        for path in ('/teams', '/teams/jobs', '/teams/dashboard', '/teams/jobs?year='+str(today.year)):
            response = owner.get(path)
            assert response.status_code == 200
            html = response.data.decode()
            assert 'upcoming-active' in html
            for identifier in ('completed-old', 'completed-workflow', 'no-date-active'):
                assert identifier not in html
            context = captured[-1]
            assert all(job['teams_phase'] == 'upcoming' for job in context['jobs'])
            assert context['totals']['income'] == sum(job['income'] for job in owner.get('/api/teams/summary').get_json()['jobs'])
            assert 'No hay eventos activos' not in html
        assert owner.get('/teams/jobs/completed-old').status_code == 200
        summary = owner.get('/api/teams/summary').get_json()
        assert any(job['id'] == 'completed-old' for job in summary['jobs'])
        assert owner.get('/teams/jobs?year=2000').status_code == 200
        assert captured[-1]['jobs']  # The new date views replace the old event-year filter.
    finally:
        template_rendered.disconnect(capture, application)


def test_classification_is_reversible_persistent_and_preserves_finances_and_crm(web):
    application, owner, storage = web
    store = application.extensions['teams']; person = member(store)
    a = publish(store, assignment(store, person)); respond(store, person, a)
    cost = records(store, 'cost')[0]; pay(store, cost)
    before = owner.get('/api/teams/summary').get_json()
    with application.test_request_context('/'):
        from flask import session
        session['tenant_id'] = 'brand-a'
        original = deepcopy(storage.get('jobs','job-1'))
    def classify(state, version, **extra):
        return owner.post('/api/teams/command', headers={'X-Teams-CSRF':'csrf'}, json=dict(
            action='job_classification',key=str(uuid4()),job_id='job-1',state=state,version=version,**extra))
    result = classify('archived',0); assert result.status_code == 200
    assert '/teams/jobs/job-1' not in owner.get('/teams/jobs').data.decode()
    assert '/teams/jobs/job-1' in owner.get('/teams/jobs?view=archived').data.decode()
    reopened = TeamsStore(store.path)
    assert records(reopened,'job_classification')[0]['state'] == 'archived'
    after = owner.get('/api/teams/summary').get_json()
    assert after['totals'] == before['totals']
    assert after['assignments'] == before['assignments'] and after['costs'] == before['costs'] and after['payments'] == before['payments']
    assert classify('included',1).status_code == 200
    assert '/teams/jobs/job-1' in owner.get('/teams/jobs').data.decode()
    assert classify('not_applicable',2).status_code == 409
    assert classify('not_applicable',2,confirmed=True).status_code == 200
    assert '/teams/jobs/job-1' in owner.get('/teams/jobs?view=not_applicable').data.decode()
    assert classify('included',3).status_code == 200
    assert classify('archived',3).status_code == 409
    assert classify('hidden',4).status_code == 400
    with application.test_request_context('/'):
        session['tenant_id'] = 'brand-a'
        assert storage.get('jobs','job-1') == original
    owner.get('/teams/preview/'+person['id'])
    assert owner.get('/teams-portal/summary').get_json()['fee_summary']['pending'] == 100000
    assert classify('archived',4).status_code == 200
    assert owner.get('/teams-portal/summary').get_json()['fee_summary']['pending'] == 100000
    assert b'Q1,000.00' in owner.get('/teams/payments').data


def test_classification_is_scoped_idempotent_and_requires_owner_and_csrf(web):
    application, owner, _ = web
    store = application.extensions['teams']
    payload=dict(action='job_classification',key='same',job_id='job-1',state='archived',version=0)
    assert owner.post('/api/teams/command',json=payload).status_code == 403
    result=owner.post('/api/teams/command',headers={'X-Teams-CSRF':'csrf'},json=payload)
    assert result.status_code == 200
    assert owner.post('/api/teams/command',headers={'X-Teams-CSRF':'csrf'},json=payload).get_json() == result.get_json()
    assert len(records(store,'job_classification')) == 1
    with owner.session_transaction() as state:
        state.update(tenant_id='brand-b',user_email='other@example.invalid')
    assert owner.post('/api/teams/command',headers={'X-Teams-CSRF':'csrf'},json=dict(payload,key='other')).status_code == 404
    assert not records(store,'job_classification','brand-b')
    # The generic entity key must not collide if tenants have matching source identifiers.
    run(store,'job_classification',tenant='brand-b',job_id='job-1',state='not_applicable',version=0)
    assert records(store,'job_classification','brand-b')[0]['state']=='not_applicable'
    assert records(store,'job_classification')[0]['state']=='archived'


def test_date_views_include_ongoing_events_and_search_stays_scoped(web):
    from datetime import date,timedelta
    from flask import session
    application,owner,storage=web
    today=date.today()
    with application.test_request_context('/'):
        session['tenant_id']='brand-a'
        for identifier,day,end in [('ongoing',today-timedelta(days=1),today+timedelta(days=1)),('past',today-timedelta(days=3),today-timedelta(days=2)),('undated',None,None)]:
            storage.upsert('jobs',dict(id=identifier,tenant_id='brand-a',nombre=identifier,boda_date=day.isoformat() if day else '',end_date=end.isoformat() if end else '',status='En curso'))
        session['tenant_id']='brand-b'
        storage.upsert('jobs',dict(id='foreign-secret',tenant_id='brand-b',nombre='foreign-secret',boda_date=today.isoformat(),status='En curso'))
    html=owner.get('/teams/jobs').data.decode()
    assert '/teams/jobs/ongoing' in html and '/teams/jobs/past' not in html and '/teams/jobs/undated' not in html
    assert '/teams/jobs/past' in owner.get('/teams/jobs?view=past').data.decode()
    assert 'Sin fecha' in owner.get('/teams/jobs?view=all&q=undated').data.decode()
    assert 'Buscar en todas las bodas' in owner.get('/teams/jobs?q=past').data.decode()
    assert 'foreign-secret' not in owner.get('/teams/jobs?view=all').data.decode()
    assert owner.get('/teams/jobs?view=invalid').status_code == 400
    assert owner.get('/teams/jobs?q='+'x'*201).status_code == 400


def test_teams_zone_uses_configured_company_and_ongoing_coverage(web):
    from datetime import date
    from src.teams import teams_zone,job_phase
    _,_,storage=web
    storage.save_tenant_dict('settings',dict(company=dict(timezone='Pacific/Auckland')),tenant_id='brand-a')
    assert teams_zone(storage,'brand-a').key=='Pacific/Auckland'
    assert teams_zone(storage,'brand-b').key=='America/Guatemala'
    job=dict(boda_date='2026-10-04')
    assert job_phase(job,[dict(end='2026-10-06T01:00',status='aceptada')],date(2026,10,5))=='upcoming'
    assert job_phase(job,[dict(end='2026-10-06T01:00',status='cancelada')],date(2026,10,5))=='past'


@pytest.mark.parametrize('local',[True,False])
def test_preview_is_read_only_for_all_member_mutations(web,local):
    application,owner,_=web;application.config.update(FLOW_TEAMS_LOCAL=local,FLOW_TEAMS_ENABLED=True)
    store=application.extensions['teams'];person=member(store);a=publish(store,assignment(store,person))
    owner.get('/teams/preview/'+person['id'])
    with owner.session_transaction() as state: csrf=state['teams_portal_csrf']
    for action in ('response','expense_request','document_read','availability','task_complete','job_classification'):
        response=owner.post('/teams-portal/command',headers={'X-Teams-CSRF':csrf},json=dict(action=action,key=str(uuid4()),id=a['id']))
        assert response.status_code==403
    assert owner.post('/teams-portal/expenses/upload',headers={'X-Teams-CSRF':csrf},data={}).status_code==403
    html=owner.get('/teams-portal/').data.decode()
    assert 'Solo lectura' in html and 'Volver a administración' in html
    assert 'data-command="response"' not in html and 'data-command="expense_request"' not in html
    assert records(store,'assignment')[0]['status']=='pendiente'


def test_real_personal_link_authenticates_outside_owner_and_cannot_access_peer(web):
    from urllib.parse import urlparse,parse_qs
    application,owner,_=web;store=application.extensions['teams']
    person=member(store);other=run(store,'member',name='Peer secret',role='Video')
    publish(store,assignment(store,person));publish(store,assignment(store,other,slot='Video'))
    issued=owner.post('/api/teams/access',headers={'X-Teams-CSRF':'csrf'},json=dict(member_id=person['id'])).get_json()
    parsed=urlparse(issued['login_url'])
    assert parsed.path=='/teams-portal/login' and parse_qs(parsed.fragment)['access']==[issued['code']]
    assert '/preview/' not in issued['login_url'] and not parsed.query
    client=application.test_client();client.get(parsed.path)
    with client.session_transaction() as state: csrf=state['teams_login_csrf'];assert 'logged_in' not in state
    assert client.post(parsed.path,data=dict(code=issued['code'],csrf=csrf)).status_code==302
    data=client.get('/teams-portal/summary').get_json()
    assert data['member']['id']==person['id'] and data['preview'] is False and 'Peer secret' not in str(data)
    with client.session_transaction() as state: csrf=state['teams_portal_csrf']
    assert client.post('/teams-portal/command',headers={'X-Teams-CSRF':csrf},json=dict(action='payment',key='deny')).status_code==403
    assert client.post('/teams-portal/command',headers={'X-Teams-CSRF':csrf},json=dict(action='job_classification',key='deny-classify')).status_code==403
    assert client.get('/teams/members/'+other['id']).status_code==404


def test_confirmed_partial_settled_counts_and_real_installments(web):
    application,owner,_=web;store=application.extensions['teams'];person=member(store)
    a=publish(store,assignment(store,person));respond(store,person,a)
    cost=records(store,'cost')[0]
    run(store,'schedule',cost_id=cost['id'],version=cost['version'],plan='2026-10-05 500\n2026-11-01 500\n2026-11-14 500')
    owner.get('/teams/preview/'+person['id'])
    unpaid=owner.get('/teams-portal/summary').get_json()['fee_summary']
    assert (unpaid['amount'],unpaid['paid'],unpaid['pending'],unpaid['count'])==(150000,0,150000,0)
    first=pay(store,cost,'500');pay(store,cost,'400')
    data=owner.get('/teams-portal/summary').get_json();fee=data['costs'][0]
    assert (data['fee_summary']['paid'],data['fee_summary']['pending'],data['fee_summary']['count'])==(90000,60000,2)
    assert (fee['schedule_completed'],fee['schedule_count'])==(1,3)
    assert fee['next_payment']==dict(date='2026-11-01',amount=10000)
    pay(store,cost,'600')
    assert owner.get('/teams-portal/summary').get_json()['fee_summary']['pending']==0
    assert owner.get('/teams-portal/summary').get_json()['fee_summary']['count']==3
    run(store,'reverse',id=first['id'],reason='Correction',effective_date='2026-10-06')
    data=owner.get('/teams-portal/summary').get_json()
    assert (data['fee_summary']['paid'],data['fee_summary']['pending'],data['fee_summary']['count'])==(100000,50000,2)
    assert len(data['history'])==4 and {p['status'] for p in data['history']}=={'Anulado','Corrección','Recibido'}


def test_history_uses_payment_year_and_general_summary_ignores_filter_and_classification(web):
    application,owner,_=web;store=application.extensions['teams'];person=member(store)
    a=publish(store,assignment(store,person));respond(store,person,a);cost=records(store,'cost')[0]
    pay(store,cost,'500',effective_date='2025-12-30');pay(store,cost,'400',effective_date='2027-01-02')
    run(store,'job_classification',job_id='job-1',state='not_applicable',version=0,confirmed=True)
    owner.get('/teams/preview/'+person['id'])
    first=owner.get('/teams-portal/summary?year=2025').get_json();second=owner.get('/teams-portal/summary?year=2027').get_json()
    assert first['history_fee_total']==50000 and second['history_fee_total']==40000
    assert first['fee_summary']==second['fee_summary'] and first['fee_summary']['pending']==60000
    assert first['history_years']==['2027','2025']
    assert len(owner.get('/teams-portal/summary?year=all').get_json()['history'])==2


def test_unconfirmed_fees_funds_and_reimbursements_are_not_confirmed_debt(web):
    application,owner,_=web;store=application.extensions['teams'];person=member(store)
    a=publish(store,assignment(store,person));fee=records(store,'cost')[0]
    pay(store,fee,'500')
    expense=run(store,'cost',job_id='job-1',member_id=person['id'],category='Transporte',description='Reembolso',amount='100')
    expense=run(store,'cost_status',id=expense['id'],version=expense['version'],status='aprobado')
    pay(store,expense,'100');run(store,'advance',job_id='job-1',member_id=person['id'],amount='200',effective_date='2026-10-05',reference='Fondo')
    owner.get('/teams/preview/'+person['id']);data=owner.get('/teams-portal/summary').get_json()
    assert (data['fee_summary']['amount'],data['fee_summary']['paid'],data['fee_summary']['pending'])==(0,50000,0)
    assert data['fee_summary']['unconfirmed']==1 and data['fee_summary']['advance_paid']==50000
    assert data['history_fee_count']==1 and data['history_fee_total']==50000 and data['history_reimbursements']==10000
    assert data['totals']['funds_remaining']==20000


def test_multi_coverage_payment_is_counted_once_and_detail_amounts_are_allocated(web):
    application,owner,_=web;store=application.extensions['teams'];person=member(store)
    first=publish(store,assignment(store,person));second=publish(store,assignment(store,person,job='job-2',start='2026-12-01T13:00',end='2026-12-01T22:00'))
    respond(store,person,first);respond(store,person,second)
    costs=records(store,'cost');pay(store,costs[0],'900',allocations=[dict(cost_id=costs[0]['id'],amount='500'),dict(cost_id=costs[1]['id'],amount='400')])
    owner.get('/teams/preview/'+person['id']);data=owner.get('/teams-portal/summary').get_json()
    assert data['fee_summary']['count']==1 and data['fee_summary']['paid']==90000
    assert sorted(c['movements'][0]['amount'] for c in data['costs'])==[40000,50000]
    assert all(j['fee_count']==1 for j in data['financial_jobs'])


def test_legacy_undated_and_overpaid_movements_remain_visible_without_invented_dates(web):
    application,owner,_=web;store=application.extensions['teams'];person=member(store)
    a=publish(store,assignment(store,person));respond(store,person,a);cost=records(store,'cost')[0]
    payment=pay(store,cost,'500')
    with store.transaction() as db:
        payment.pop('effective_date');payment['amount']=200000;payment['allocations'][0]['amount']=200000
        store.save(db,'brand-a','payment',payment)
    owner.get('/teams/preview/'+person['id']);data=owner.get('/teams-portal/summary?year=all').get_json()
    assert data['history'][0]['date'] is None and data['fee_summary']['overpaid']
    assert data['fee_summary']['pending']==-50000 and data['fee_summary']['progress']>100
    assert b'Sin fecha de pago' in owner.get('/teams-portal/?year=all').data
    assert not owner.get('/teams-portal/summary?year=2026').get_json()['history']


def test_notion_upload_is_owner_only_csrf_protected_and_private_to_members(web, monkeypatch):
    import io
    import json
    from types import SimpleNamespace
    import src.teams_notion as notion
    application, owner, storage=web
    monkeypatch.setattr(notion,'resolve_brand',lambda tenant:SimpleNamespace(brand_key='norkevin'))
    payload=dict(jobs=[{'url':'https://app.notion.com/p/'+'a'*32,'EMPRESA':'NORKEVIN',
                       'BODA':'Ejemplo','date:Fecha del evento:start':'2026-11-14',
                       'Primera Camara':'Nombre administrativo privado'}],payments=[])
    def upload(client,csrf=None):
        return client.post('/api/teams/notion/import',headers={'X-Teams-CSRF':csrf or ''},
                           data={'file':(io.BytesIO(json.dumps(payload).encode()),'notion.json')})
    assert upload(owner).status_code==403
    anonymous=application.test_client();assert upload(anonymous,'csrf').status_code==404
    assert upload(owner,'csrf').status_code==200
    assert 'Nombre administrativo privado' in owner.get('/teams/jobs/job-1').get_data(as_text=True)
    assert 'Última vinculación de Notion' in owner.get('/teams/settings').get_data(as_text=True)
    person=member(application.extensions['teams']);assignment(application.extensions['teams'],person)
    worker=member_client(application,owner,person)
    assert 'Nombre administrativo privado' not in worker.get('/teams-portal').get_data(as_text=True)
    assert upload(worker,'csrf').status_code==404
    bad=owner.post('/api/teams/notion/import',headers={'X-Teams-CSRF':'csrf'},
        data={'file':(io.BytesIO(b'not json'),'notion.json')})
    assert bad.status_code==400
