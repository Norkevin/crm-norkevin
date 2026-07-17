"""Fase 2 del rediseño mobile (Kevin: 'perfil de cliente dividido en
pestañas: Información, Jobs, Cotizaciones, Contratos, Facturas, Pagos,
Correos y Notas'). client_detail.html no tenia ninguna estructura de
pestañas antes -- todo el contenido vivia apilado en una sola pagina
larga. Estas pruebas confirman que las 8 pestañas existen con su panel
correspondiente, y que la pestaña de Correos (nueva, antes no existia
ninguna vista de mail_log a nivel cliente) recibe datos reales."""
import uuid


def _seed_client_with_email(app_module):
    suffix = uuid.uuid4().hex[:6]
    client_id = f'client-tabs-{suffix}'
    lead_id = f'lead-tabs-{suffix}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Tabs', 'last_name': 'Test',
        'email': f'{suffix}@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('leads', {
        'id': lead_id, 'nombre': 'Tabs Test', 'client_id': client_id,
        'email': f'{suffix}@example.com', 'status': 'Nuevo', 'tenant_id': 'tenant-norkevin',
        # fuente unico y distinto de cualquier otro test -- un lead con
        # fuente en blanco se agrupa en un bucket compartido que puede
        # inflar el conteo "maximo" que otras pruebas del dashboard
        # (test_dashboard_lead_sources_bar_chart.py) asumen exclusivo.
        'fuente': 'ClientTabsTest',
    })
    app_module.store.upsert('mail_log', {
        'id': f'mail-tabs-{suffix}', 'to': f'{suffix}@example.com',
        'subject': 'Asunto de prueba', 'lead_id': lead_id, 'job_id': None,
        'status': 'sent', 'sent_at': '2026-07-01T10:00:00', 'tenant_id': 'tenant-norkevin',
    })
    return client_id


def test_client_detail_has_all_eight_tabs(auth_client):
    import app as app_module
    client_id = _seed_client_with_email(app_module)
    resp = auth_client.get(f'/clients/{client_id}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    for tab in ('info', 'jobs', 'cotizaciones', 'contratos', 'facturas', 'pagos', 'correos', 'notas'):
        assert f'data-tab="{tab}"' in html, f'falta la pestaña {tab}'
        assert f'id="tab-{tab}"' in html, f'falta el panel de la pestaña {tab}'


def test_client_detail_correos_tab_shows_related_mail(auth_client):
    import app as app_module
    client_id = _seed_client_with_email(app_module)
    resp = auth_client.get(f'/clients/{client_id}')
    html = resp.get_data(as_text=True)
    assert 'Asunto de prueba' in html


def test_client_detail_only_first_tab_visible_by_default(auth_client):
    import app as app_module
    client_id = _seed_client_with_email(app_module)
    resp = auth_client.get(f'/clients/{client_id}')
    html = resp.get_data(as_text=True)
    assert 'client-tab-panel active" id="tab-info"' in html or 'id="tab-info"' in html
    assert html.count('client-tab-panel active') == 1, 'solo un panel debe empezar visible'
