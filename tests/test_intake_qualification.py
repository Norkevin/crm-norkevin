import pytest


@pytest.mark.parametrize('year', ['2027', '2028'])
@pytest.mark.parametrize('slug,tenant_id,own,foreign', [
    ('norkevin-photography', 'tenant-norkevin-photography', '1BQFU3oq_3WImnKose6L8rFTPE5Mh-kXf', '1irm5P-Oru9fUo55B58y4XBuUrMj76y3H'),
    ('astral-weddings', 'tenant-norkevin', '1irm5P-Oru9fUo55B58y4XBuUrMj76y3H', '1BQFU3oq_3WImnKose6L8rFTPE5Mh-kXf'),
])
def test_qualification_is_saved_in_own_brand_without_claiming_verified_fit(client, slug, tenant_id, own, foreign, year):
    import app as module
    from flask import g
    html = client.get('/captacion/' + slug).get_data(as_text=True)
    assert own in html and foreign not in html
    assert 'name="presupuesto"' in html and 'name="servicio_solicitado"' in html
    payload = {
        'tenant_slug': slug, 'nombre': 'Consulta sintética', 'email': 'test@example.com',
        'fecha_tentativa': year + '-11-20', 'intake_version': '1',
        'servicio_solicitado': 'Fotografía y video de boda',
        'presupuesto': 'Necesitamos orientación',
        'coleccion_interes': '  Colección por definir  ', 'notas': 'Notas de la pareja',
    }
    response = client.post('/api/captacion', json=payload)
    assert response.status_code == 200
    with module.app.test_request_context():
        g.public_tenant_id = tenant_id
        lead = module.get_lead(response.json['lead_id'])
        assert lead['presupuesto'] == payload['presupuesto']
        assert lead['intake_qualification']['collection'] == 'Colección por definir'
        assert lead['intake_qualification']['status'] == 'Pendiente de revisión'
        assert (('18TR7KhEcRDpGOKsUnJNOaRvTna5l9edS' if slug == 'norkevin-photography' and year == '2028' else own)
                in lead['intake_qualification']['catalog_url'])
        assert payload['servicio_solicitado'] in lead['notas']
        assert lead['notas'].endswith(payload['notas'])
        assert lead['status'] == 'Nuevo'
        g.public_tenant_id = 'tenant-norkevin' if slug == 'norkevin-photography' else 'tenant-norkevin-photography'
        assert module.get_lead(response.json['lead_id']) is None


@pytest.mark.parametrize('extra', [
    {'servicio_solicitado': ''}, {'presupuesto': []}, {'coleccion_interes': 'x' * 161},
    {'coleccion_interes': None},
])
def test_invalid_qualification_creates_no_lead_or_workflow(client, monkeypatch, extra):
    import app as module
    before = module.store.list('leads')
    monkeypatch.setattr(module, 'trigger_workflow_for_lead', lambda *a, **k: pytest.fail('No workflow for invalid input'))
    payload = {
        'tenant_slug': 'astral-weddings', 'nombre': 'Invalid', 'fecha_tentativa': '2027-11-20',
        'intake_version': '1', 'servicio_solicitado': 'Video de boda',
        'presupuesto': 'Sí, la inversión es compatible',
    }
    response = client.post('/api/captacion', json={**payload, **extra})
    assert response.status_code == 400
    assert module.store.list('leads') == before
