"""Kevin (feedback de diseño, revisando el rediseño mobile como product
designer): 'todo pesa igual... dale una jerarquia mucho mas clara al
Dashboard para que lo mas importante destaque primero'. El dashboard ya
calculaba total_pending/total_late para un pie chart que nunca se
renderizaba -- se reutiliza esa data para un banner destacado arriba de
todo, y Proximos eventos / Actividad reciente se movieron antes que la
seccion de Ventas y graficas."""
import uuid


def test_pending_payments_banner_hidden_when_nothing_pending(auth_client):
    """Con datos de prueba vacios (fixture limpio) no debe haber pagos
    pendientes, asi que el banner no debe aparecer -- no se debe mostrar
    un 'Q0 pendiente' vacio."""
    import app as app_module
    for p in app_module.store.list('payments'):
        app_module.store.delete('payments', p['id'])

    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert 'class="dashboard-pending-banner' not in html


def test_pending_payments_banner_shows_amount_and_links_to_payments(auth_client):
    import app as app_module
    suffix = uuid.uuid4().hex[:6]
    app_module.store.upsert('payments', {
        'id': f'pay-hier-{suffix}', 'invoice_id': f'INV-{suffix}',
        'amount': 3200, 'status': 'Pendiente', 'due_date': '2027-01-01',
        'tenant_id': 'tenant-norkevin',
    })

    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert 'class="dashboard-pending-banner' in html
    assert 'href="/payments"' in html
    assert 'Q3,200' in html
    banner_tag_start = html.index('<a class="dashboard-pending-banner')
    banner_tag_end = html.index('>', banner_tag_start)
    assert 'urgent' not in html[banner_tag_start:banner_tag_end], \
        'sin pagos atrasados, el banner no debe usar el estilo rojo urgente'


def test_pending_payments_banner_is_urgent_when_late(auth_client):
    import app as app_module
    suffix = uuid.uuid4().hex[:6]
    app_module.store.upsert('payments', {
        'id': f'pay-hier-late-{suffix}', 'invoice_id': f'INV-LATE-{suffix}',
        'amount': 1500, 'status': 'Late', 'due_date': '2026-01-01',
        'tenant_id': 'tenant-norkevin',
    })

    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    assert 'dashboard-pending-banner urgent' in html
    assert 'atrasado' in html


def test_upcoming_events_and_recent_activity_come_before_sales_and_charts(auth_client):
    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)
    events_idx = html.find('Proximos eventos y actividad reciente')
    sales_idx = html.find('Ventas y graficas')
    assert events_idx != -1 and sales_idx != -1
    assert events_idx < sales_idx, 'proximos eventos/actividad debe ir antes que ventas/graficas'

    lists_idx = html.find('<div class="dashboard-lists">')
    panel_idx = html.find('<div class="dashboard-panel">')
    assert lists_idx != -1 and panel_idx != -1
    assert lists_idx < panel_idx, 'el bloque de listas debe renderizarse antes que el panel de analitica'
