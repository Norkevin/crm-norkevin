from copy import deepcopy
from datetime import date, datetime

import pytest


@pytest.fixture
def ledger():
    return [
        {'id': 'paid-prior', 'amount': 100, 'status': 'Pagado', 'paid_date': '2025-12-31', 'due_date': '2026-01-01'},
        {'id': 'paid-current', 'amount': 50, 'status': 'Pagado', 'paid_date': '2026-01-01', 'due_date': '2025-12-31'},
        {'id': 'pending-current', 'amount': 30, 'status': 'Pendiente', 'due_date': '2026-11-01'},
        {'id': 'late-current', 'amount': 20, 'status': 'Pendiente', 'due_date': '2026-09-01'},
        {'id': 'future', 'amount': 90, 'status': 'Pendiente', 'due_date': '2027-01-01'},
        {'id': 'old-due', 'amount': 5, 'status': 'Pendiente', 'due_date': '2025-12-31'},
        {'id': 'undated', 'amount': 7, 'status': 'Pagado', 'due_date': '2026-01-01'},
    ]


def test_actual_cash_dates_due_year_and_undated_history(ledger):
    import app as crm
    original = deepcopy(ledger)
    current = crm._payment_financial_summary(ledger, date(2026, 10, 3), 2026)
    assert (current['paid'], current['due'], current['late'], current['pending']) == (50, 50, 20, 30)
    assert current['paid_count'] == 1 and current['due_count'] == 2
    historical = crm._payment_financial_summary(ledger, date(2026, 10, 3))
    assert (historical['paid'], historical['due'], historical['late']) == (157, 145, 25)
    assert historical['undated_paid'] == 7
    assert ledger == original


def test_payments_and_dashboard_use_same_year_with_explicit_history(auth_client, monkeypatch, ledger):
    import app as crm
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 3, 23, 40, tzinfo=tz)
    monkeypatch.setattr(crm, 'datetime', FrozenDateTime)
    monkeypatch.setattr(crm, '_visible_billable_payments', lambda *args, **kwargs: deepcopy(ledger))
    contexts = []
    render = crm.render_template
    def capture(template, **context):
        contexts.append(context)
        return render(template, **context)
    monkeypatch.setattr(crm, 'render_template', capture)
    payment_before = crm.store.list('payments')
    for url in ('/payments', '/dashboard'):
        response = auth_client.get(url)
        assert response.status_code == 200
        ctx = contexts[-1]
        assert ctx['total_paid'] == 50
        assert (ctx.get('total_due') if url == '/payments' else ctx['total_unpaid']) == 50
        assert ctx['total_late'] == 20
        assert ctx['financial_history']['paid'] == 157
        html = response.get_data(as_text=True)
        assert '<summary>Ver total histórico' in html and 'Cobrado · 2026' in html
    auth_client.get('/payments?year=2027')
    ctx = contexts[-1]
    assert ctx['total_paid'] == 0 and ctx['total_due'] == 90
    assert [p['id'] for p in ctx['payments']] == ['future']
    auth_client.get('/payments?year=all')
    ctx = contexts[-1]
    assert ctx['selected_year'] is None
    assert ctx['total_paid'] == 157 and ctx['total_due'] == 145
    assert len(ctx['payments']) == len(ledger)
    assert crm.store.list('payments') == payment_before
