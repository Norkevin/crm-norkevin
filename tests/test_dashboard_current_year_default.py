"""Kevin: 'aca por defecto que se quede marcado solo el año en curso, y el
grafico de al lado que tenga los graficos correspondientes, la info deberia
estar ahi' -- el grafico de Revenue Comparison mostraba las lineas de TODOS
los años superpuestas desde el arranque (2025/2026/2027 juntas), y el tile
'Revenue Comparison' (junto a Leads/Sesion/Payments) quedaba vacio porque
updateDashboard() siempre le ponia textContent = ''. Ahora solo el año en
curso se ve por defecto (los demas quedan disponibles en la leyenda, un
click los prende), y el tile muestra el total del año en curso."""
import re
import uuid
from datetime import date


def test_dashboard_passes_current_year_to_the_template(auth_client):
    resp = auth_client.get('/dashboard')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    match = re.search(r'var CURRENT_YEAR = (\d+);', html)
    assert match, 'CURRENT_YEAR debe estar embebido en el JS del dashboard'
    assert int(match.group(1)) == date.today().year


def test_dashboard_hides_non_current_years_by_default(auth_client):
    import app as app_module
    current_year = date.today().year

    for offset, yr in enumerate([current_year - 1, current_year, current_year + 1]):
        app_module.store.upsert('payments', {
            'id': 'pay-curyear-' + uuid.uuid4().hex[:8],
            'invoice_id': 'INV-CURYEAR-' + str(offset),
            'client_id': 'client-curyear-test',
            'amount': 1000.0,
            'status': 'Pagado',
            'paid_date': f'{yr}-03-01',
            'due_date': f'{yr}-03-01',
            'tenant_id': 'tenant-norkevin',
        })

    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)

    assert 'REVENUE_SERIES.forEach(function(s) {' in html
    assert "if (String(s.year) !== String(CURRENT_YEAR)) hiddenRevenueYears.add(String(s.year));" in html


def test_dashboard_revenue_tile_shows_current_year_total(auth_client):
    """El tile 'Revenue Comparison' ya no queda en blanco -- muestra el
    total del año en curso, tomado de REVENUE_SERIES."""
    import app as app_module
    current_year = date.today().year

    app_module.store.upsert('payments', {
        'id': 'pay-curyeartotal-' + uuid.uuid4().hex[:8],
        'invoice_id': 'INV-CURYEARTOTAL',
        'client_id': 'client-curyeartotal-test',
        'amount': 4200.0,
        'status': 'Pagado',
        'paid_date': f'{current_year}-05-01',
        'due_date': f'{current_year}-05-01',
        'tenant_id': 'tenant-norkevin',
    })

    resp = auth_client.get('/dashboard')
    html = resp.get_data(as_text=True)

    assert "dashboard-revenue-value').textContent = '';" not in html, (
        'el tile de Revenue Comparison ya no debe quedar vacio a proposito'
    )
    assert "currentYearSeries ? money(currentYearSeries.total) : money(0)" in html
