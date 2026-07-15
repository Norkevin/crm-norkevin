"""Lead sources configurables: formulario, settings y dashboard conectados."""
import uuid


def test_contact_form_uses_configured_lead_sources(auth_client):
    resp = auth_client.get('/captacion')

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Wedding Planner' in html
    assert 'name="fuente"' in html


def test_new_lead_source_appears_in_contact_form_and_dashboard(auth_client):
    import app as app_module

    source_name = 'TikTok ' + uuid.uuid4().hex[:4]
    save_resp = auth_client.post('/api/settings/lead-sources', json={
        'name': source_name,
        'color': '#8b5cf6',
        'active': True,
    })
    assert save_resp.status_code == 200
    assert save_resp.get_json()['ok'] is True

    form_resp = auth_client.get('/captacion')
    assert source_name in form_resp.get_data(as_text=True)

    lead_id = 'lead-source-' + uuid.uuid4().hex[:8]
    app_module.upsert_lead({
        'id': lead_id,
        'Nombre': 'Lead Fuente Configurable',
        'Email': 'fuente-configurable@example.com',
        'Fuente': source_name,
        'Estado': 'Nuevo',
        'tenant_id': 'tenant-norkevin',
    })

    dashboard_resp = auth_client.get('/dashboard')
    assert dashboard_resp.status_code == 200
    assert source_name in dashboard_resp.get_data(as_text=True)


def test_dashboard_revenue_legend_shows_only_year(auth_client):
    resp = auth_client.get('/dashboard')

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'projectedNote' not in html
    assert " + ' pendiente'" not in html
