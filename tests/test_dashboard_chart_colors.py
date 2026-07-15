"""Kevin: la paleta anterior eran puros tonos azules/grises (#0284C7,
#111827, #94A3B8, #075985, #6B7280, #334155) -- cuando dos o tres anos se
superponen en el grafico de Revenue Comparison, no se distinguian bien.
Mando una captura de Wavespace como referencia: colores bien distintos
(indigo, morado, naranja, rojo, verde) por serie."""
import json
import re
import uuid


def test_dashboard_chart_uses_distinct_high_contrast_colors_per_year(auth_client):
    import app as app_module

    for offset, yr in enumerate([2023, 2024, 2025]):
        app_module.store.upsert('payments', {
            'id': 'pay-color-' + uuid.uuid4().hex[:8],
            'invoice_id': 'INV-COLOR-' + str(offset),
            'client_id': 'client-color-test',
            'amount': 1000.0,
            'status': 'Pagado',
            'paid_date': f'{yr}-03-01',
            'due_date': f'{yr}-03-01',
            'tenant_id': 'tenant-norkevin',
        })

    resp = auth_client.get('/dashboard')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    match = re.search(r'var REVENUE_SERIES = (.*?);', html)
    assert match
    series = json.loads(match.group(1))

    colors = [row['color'] for row in series]
    assert len(colors) == len(set(colors)), 'cada anio debe tener un color distinto'

    # La paleta vieja (todo azules/grises apagados) ya no debe estar en uso.
    old_muted_palette = {'#0284C7', '#111827', '#94A3B8', '#075985', '#6B7280', '#334155'}
    assert not (set(colors) & old_muted_palette), 'la paleta debe reemplazar los tonos azul/gris apagados'
