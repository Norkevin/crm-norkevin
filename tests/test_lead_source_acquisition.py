"""Capture sources remain in the dashboard after a lead becomes a wedding."""
import pytest
from flask import template_rendered
from conftest import login_as_tenant


@pytest.fixture(params=[('tenant-norkevin', 'astral-weddings'),
                        ('tenant-norkevin-photography', 'norkevin-photography')])
def account(client, request, monkeypatch):
    import app as m
    tenant, slug = request.param
    tables = ('leads', 'jobs', 'clients', 'quotes', 'payments', 'payment_schedules', 'questionnaires')
    saved = {t: m.store._read_raw(t) for t in tables}
    for table in tables:
        m.store._save(table, [])
    login_as_tenant(client, tenant)
    monkeypatch.setattr(m, '_notify_new_lead', lambda *a, **kw: None)
    monkeypatch.setattr(m, 'trigger_workflow_for_lead', lambda *a, **kw: None)
    yield client, m, tenant, slug
    for table, rows in saved.items():
        m.store._save(table, rows)


def dashboard_stats(client, m):
    captured = []
    def rendered(sender, template, context, **extra):
        captured.append(context)
    with template_rendered.connected_to(rendered, m.app):
        response = client.get('/dashboard')
    assert response.status_code == 200
    return {s['label']: s for s in captured[-1]['lead_source_stats']}


def test_capture_to_accepted_wedding_keeps_source_and_counts_once(account):
    client, m, tenant, slug = account
    assert 'name="fuente"' in client.get('/captacion/' + slug).get_data(as_text=True)
    for source in ('Instagram', 'Wedding Planner'):
        response = client.post('/api/captacion', json={
            'tenant_slug': slug, 'nombre': 'Source test', 'email': 'source@example.invalid',
            'fecha_tentativa': '2027-06-12', 'fuente': source,
        })
        assert response.status_code == 200
        lead_id = response.get_json()['lead_id']
        login_as_tenant(client, tenant)
        stats = dashboard_stats(client, m)
        assert stats[source]['leads'] == 1 and stats[source]['jobs'] == 0
        assert client.post('/api/leads/' + lead_id + '/accept', json={}).status_code == 200
        stats = dashboard_stats(client, m)
        assert stats[source]['leads'] == stats[source]['jobs'] == 1
        assert stats[source]['bar_pct'] == 100
        # A historic duplicate job must not duplicate an acquisition/conversion.
        job = next(j for j in m.list_jobs() if j.get('lead_id') == lead_id)
        m.upsert_job(dict(job, id='duplicate-' + job['id']))
    stats = dashboard_stats(client, m)
    assert sum(s['leads'] for s in stats.values()) == 2
    assert sum(s['jobs'] for s in stats.values()) == 2
    assert stats['Instagram']['pct'] == stats['Wedding Planner']['pct'] == .5
    exported = client.get('/api/leads/export.xls').get_data(as_text=True)
    assert '<td>Instagram</td><td>1</td><td>1</td>' in exported
    assert '<td>Wedding Planner</td><td>1</td><td>1</td>' in exported


def test_imported_jobs_and_lost_leads_use_recorded_sources_without_crossing_accounts(account):
    client, m, tenant, _ = account
    other = 'tenant-norkevin' if tenant != 'tenant-norkevin' else 'tenant-norkevin-photography'
    m.store._save('leads', [
        {'id':'lost', 'fuente':'Facebook', 'status':'Perdido', 'tenant_id':tenant},
        {'id':'other-lead', 'fuente':'Instagram', 'tenant_id':other},
    ])
    m.store._save('jobs', [
        {'id':'imported', 'lead_id':'missing', 'lead_source':'Wedding Planner', 'tenant_id':tenant},
        {'id':'unknown', 'tenant_id':tenant},
        {'id':'other-job', 'lead_source':'Google', 'tenant_id':other},
    ])
    stats = dashboard_stats(client, m)
    assert stats['Wedding Planner']['leads'] == stats['Wedding Planner']['jobs'] == 1
    assert stats['Facebook']['leads'] == 1 and stats['Facebook']['jobs'] == 0
    assert stats['Sin fuente']['leads'] == stats['Sin fuente']['jobs'] == 1
    assert stats.get('Instagram', {}).get('leads', 0) == 0
    assert stats.get('Google', {}).get('leads', 0) == 0
    assert sum(s['leads'] for s in stats.values()) == 3
