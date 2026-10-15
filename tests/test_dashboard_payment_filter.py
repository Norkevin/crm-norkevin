"""Opening the dashboard includes every event type, even numeric names."""
import json
import re
from datetime import datetime
from html.parser import HTMLParser


def test_default_filter_and_payment_periods(auth_client, monkeypatch):
    import app as crm

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 14, 12, tzinfo=tz)

    jobs = [{'id': 'wedding', 'type': 'Boda'}, {'id': 'birthday', 'type': '15 años'}]
    payments = [
        {'job_id': job, 'status': status, 'paid_date': day, 'amount': amount}
        for job, status, day, amount in [
            ('wedding', 'Pagado', '2026-10-12', 1200),
            ('wedding', 'Pagado', '2026-10-01', 300),
            ('wedding', 'Pagado', '2026-09-20', 500),
            ('birthday', 'Pagado', '2026-10-14', 150),
            ('wedding', 'Pendiente', '2026-10-14', 9999),
        ]
    ]
    monkeypatch.setattr(crm, 'datetime', Clock)
    monkeypatch.setattr(crm, '_canonical_jobs', lambda: jobs)
    monkeypatch.setattr(crm, '_open_leads', lambda: [])
    monkeypatch.setattr(crm, '_visible_billable_payments', lambda: payments)
    html = auth_client.get('/dashboard').get_data(as_text=True)

    class FilterOptions(HTMLParser):
        in_filter = False
        options = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'select':
                self.in_filter = attrs.get('id') == 'dashboard-job-type'
            if self.in_filter and tag == 'option':
                self.options.append(attrs)

        def handle_endtag(self, tag):
            if tag == 'select':
                self.in_filter = False

    parser = FilterOptions()
    parser.options = []
    parser.feed(html)
    assert parser.options[0]['value'] == 'All Job Types'
    assert [o['value'] for o in parser.options if 'selected' in o] == ['All Job Types']
    assert [o['value'] for o in parser.options] == ['All Job Types', '15 años', 'Boda']

    data = json.loads(re.search(r'var DASHBOARD_DATA = (.+);', html).group(1))
    for period, total in [('7', 1350), ('30', 2150), ('mtd', 1650), ('ytd', 2150)]:
        types = data[period]['jobTypes']
        assert types['All Job Types']['totals']['payments'] == total
        assert sum(types['All Job Types']['payments']) == total
        assert types['Boda']['totals']['payments'] == total - 150
        assert types['15 años']['totals']['payments'] == 150
    daily = auth_client.get('/api/dashboard/custom-range?start=2026-10-12&end=2026-10-12').get_json()
    assert daily['jobTypes']['All Job Types']['payments'] == [1200]
