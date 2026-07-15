"""Kevin: 'que sea una grafica de barras en vez de pie' para Lead Sources
en el Dashboard. Los datos vienen de lead.fuente -- llenado por el
formulario publico de contacto (crear_lead_publico) o por el formulario
de alta manual de leads en el admin, ambos usan el mismo campo."""
import uuid


def test_dashboard_uses_bar_chart_not_pie_for_lead_sources(auth_client):
    import app as app_module

    source_name = 'Referido ' + uuid.uuid4().hex[:4]
    app_module.upsert_lead({
        'id': 'lead-barchart-' + uuid.uuid4().hex[:6],
        'nombre': 'Bar Chart Test', 'email': 'barcharttest@example.com',
        'fuente': source_name, 'status': 'Nuevo', 'tenant_id': 'tenant-norkevin',
    })

    resp = auth_client.get('/dashboard')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'source-bars' in html
    assert 'source-bar-fill' in html
    assert 'class="source-pie"' not in html, 'el pie chart viejo no debe seguir en el HTML'
    assert source_name in html


def test_lead_source_bar_width_is_normalized_to_the_largest_source(auth_client):
    """La fuente con mas leads debe llenar el 100% de la barra; las demas
    se escalan proporcionalmente a ella (no al total general)."""
    import app as app_module

    big_source = 'Instagram ' + uuid.uuid4().hex[:4]
    small_source = 'TikTok ' + uuid.uuid4().hex[:4]
    for i in range(4):
        app_module.upsert_lead({
            'id': f'lead-big-{i}-' + uuid.uuid4().hex[:6],
            'nombre': f'Big {i}', 'email': f'big{i}@example.com',
            'fuente': big_source, 'status': 'Nuevo', 'tenant_id': 'tenant-norkevin',
        })
    app_module.upsert_lead({
        'id': 'lead-small-' + uuid.uuid4().hex[:6],
        'nombre': 'Small', 'email': 'small@example.com',
        'fuente': small_source, 'status': 'Nuevo', 'tenant_id': 'tenant-norkevin',
    })

    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert 'width:100.0%' in html or 'width:100%' in html, 'la fuente con mas leads debe llenar toda la barra'
    assert 'width:25.0%' in html, 'la fuente con 1/4 de los leads del maximo debe llenar 25%'
