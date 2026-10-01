"""Events use their scheduled dates, including each extra event once."""
import json
import re
from datetime import date, timedelta

import pytest
from conftest import login_as_tenant


@pytest.mark.parametrize('tenant', ['tenant-norkevin', 'tenant-norkevin-photography'])
def test_events_follow_selected_range_and_tenant(client, tenant):
    import app as m
    today = date.today()
    day = today.isoformat()
    next_year = f'{today.year + 1}-01-09'
    snapshot = {table: m.store.list(table) for table in ('jobs', 'leads', 'calendar', 'payments')}
    try:
        for table in snapshot:
            m.store._save(table, [])
        login_as_tenant(client, tenant)
        job = {'id': 'event-job', 'nombre': 'Boda de prueba', 'tenant_id': tenant,
               'boda_date': next_year, 'created': day, 'status': 'Confirmado',
               'type': 'Boda religiosa', 'price_total': 29000,
               'manual_workflow_tasks': [
                   {'id': 'civil', 'type': 'extra-event', 'name': 'Boda civil', 'start_date': day, 'end_date': next_year, 'calendar_event_id': 'mirror'},
                   {'id': 'save', 'type': 'extra-event', 'name': 'Save the date', 'start_date': day, 'status': 'done'},
                   {'type': 'appointment', 'start_date': day},
                   {'type': 'email', 'start_date': day},
                   {'type': 'extra-event', 'start_date': 'invalid'},
                   {'type': 'extra-event', 'start_date': day, 'status': 'skipped'},
               ]}
        m.store.upsert('jobs', job)
        m.store.upsert('calendar', {'id': 'mirror', 'type': 'event', 'date': day, 'job_id': job['id'], 'tenant_id': tenant})
        for id_, fields in [
            ('undated', {'boda_date': '', 'manual_workflow_tasks': [], 'price_total': 0}),
            ('cancelled', {'status': 'Cancelado'}),
            ('archived', {'status': 'Archivado'}),
            ('other-tenant', {'tenant_id': 'tenant-other'}),
        ]:
            m.store.upsert('jobs', {**job, 'id': id_, **fields})

        def custom(start, end):
            response = client.get('/api/dashboard/custom-range', query_string={'start': start, 'end': end})
            assert response.status_code == 200
            return response.get_json()['jobTypes']['All Job Types']

        payload = custom(day, day)
        assert payload['sessions'] == [2]
        assert payload['totals']['sessions'] == 2
        assert payload['totals']['revenue'] == 0
        assert custom(next_year, next_year)['totals']['sessions'] == 1
        yesterday = (today - timedelta(days=1)).isoformat()
        assert custom(yesterday, yesterday)['totals']['sessions'] == 0

        response = client.get('/dashboard')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        data = json.loads(re.search(r'var DASHBOARD_DATA = (.+);', html).group(1))
        for range_key in ('7', '30', 'mtd', 'ytd'):
            assert data[range_key]['jobTypes']['All Job Types']['totals']['sessions'] == 2
            assert data[range_key]['jobTypes']['Boda religiosa']['totals']['sessions'] == 2
        assert '31 diciembre' in data['ytd']['dateLabel']
        assert '<div class="metric-label">Eventos</div>' in html

        # A rescheduled/deleted extra event immediately leaves this range.
        job['manual_workflow_tasks'][0]['start_date'] = next_year
        job['manual_workflow_tasks'] = [t for t in job['manual_workflow_tasks'] if t.get('id') != 'save']
        m.store.upsert('jobs', job)
        assert custom(day, day)['totals']['sessions'] == 0
        assert custom(next_year, next_year)['totals']['sessions'] == 2
    finally:
        for table, records in snapshot.items():
            m.store._save(table, records)
