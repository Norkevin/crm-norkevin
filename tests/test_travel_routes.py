"""Owner venue workflow, isolated from customer data and external providers."""
import re

import pytest

from conftest import login_as_tenant


@pytest.fixture
def travel_ui(auth_client, monkeypatch):
    import app as crm
    tenant = 'tenant-norkevin-photography'
    login_as_tenant(auth_client, tenant)
    token = crm._workflow_tenant.set(tenant)
    monkeypatch.setattr('src.travel._GEOCODE_LAST_REQUEST', 0.0)
    monkeypatch.setattr(crm.travel_service, '_geocode', lambda query: [])
    crm.travel_service.ensure_catalog(tenant)
    yield crm, auth_client, tenant
    crm._workflow_tenant.reset(token)


def venue_payload(**changes):
    return dict(name='Jardín de Prueba Ámbar', aliases=['Jardin Ambar Prueba'],
                municipality='Antigua Guatemala', department='Sacatepéquez', country='GT',
                address='Dirección sintética para pruebas', source_url='https://example.invalid/venue',
                verification_status='verified', approval_status='approved', travel_level=0,
                **changes)


def test_admin_renders_catalog_rates_and_controlled_activation(travel_ui):
    crm, client, tenant = travel_ui
    response = client.get('/settings/travel')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for text in ('Finca San Cayetano', 'Hotel Atitlán', 'Q1,500', 'Q3,500', 'Q4,500',
                 'Verificación de ubicación', 'Aprobación comercial', 'Activar en correos',
                 'https://www.openstreetmap.org/copyright', 'OpenStreetMap contributors (ODbL)',
                 '13 de octubre de 2026', 'id="travel-rates-title" tabindex="-1"'):
        assert text in html
    assert crm.travel_service.settings(tenant)['enabled'] is False
    assert not crm.store.list('pending_emails')


def test_pending_catalog_import_exposes_provenance_without_implying_a_rate(travel_ui):
    crm, client, tenant = travel_ui
    notes = 'Catalogo_Bodas.xlsx; Fuentes fila 7; fuente historica, confirmar vigencia.'
    imported = dict(name='Hacienda del Archivo de Pruebas', aliases=[], country='GT', department='Zacapa',
                    municipality='', address='', source_url='https://example.invalid/historical',
                    source_urls=['https://example.invalid/historical', 'https://www.openstreetmap.org/node/123'],
                    verification_status='pending', approval_status='proposed', travel_level=None,
                    verification_notes=notes)
    response = client.post('/api/travel/venues', json=imported)
    assert response.status_code == 200
    saved = response.get_json()['venue']
    assert saved['verification_notes'] == notes and saved['travel_level'] is None
    html = client.get('/settings/travel').get_data(as_text=True)
    row = next(row for row in re.findall(r'<tr data-search=.*?</tr>', html, re.S)
               if imported['name'] in row)
    assert 'Por definir' in row and 'Regla del destino' not in row
    assert 'Pendiente' in row and notes in row
    assert 'Notas de verificación y procedencia' in html and 'readonly' in html
    assert 'Fuentes adicionales' in html and 'https://www.openstreetmap.org/node/123' in row
    catalog = crm.travel_service.catalog(tenant)
    verified = sum(venue['verification_status'] == 'verified' for venue in catalog)
    assert f'{len(catalog)} lugares · {verified} ubicaciones verificadas · {len(catalog) - verified} pendientes de verificar' in html
    result = crm.travel_service.resolve(dict(tenant_id=tenant, locacion=imported['name']))
    assert result['requires_review'] and result['level'] is None


def test_unknown_research_review_approval_and_reuse(travel_ui, monkeypatch):
    crm, client, tenant = travel_ui
    lead = dict(id='lead-travel-ui', nombre='Prueba', tenant_id=tenant, locacion='Jardin Ambar Prueba',
                email='test@example.invalid', status='Nuevo', tipo_evento='Boda')
    crm.store.upsert('leads', lead)
    monkeypatch.setattr(crm.travel_service, '_geocode', lambda query: [{
        'name':'Jardín de Prueba Ámbar', 'display_name':'Dirección sintética para pruebas',
        'address':{'country_code':'gt', 'city':'Antigua Guatemala', 'state':'Sacatepéquez'},
        'osm_type':'node', 'osm_id':123, 'lat':'14.55', 'lon':'-90.73',
    }])
    response = client.post('/api/leads/lead-travel-ui/travel/research', json={})
    assert response.status_code == 200
    result = response.get_json()['travel']
    assert result['requires_review'] and result['candidates']
    review = next(r for r in crm.travel_service.reviews(tenant) if r['original_location'] == lead['locacion'])
    assert 'Jardín de Prueba Ámbar' in client.get('/settings/travel').get_data(as_text=True)
    candidate_id = result['candidates'][0]['id']
    response = client.post('/api/travel/venues', json=venue_payload(id=candidate_id, review_id=review['id']))
    assert response.status_code == 200
    resolved = crm.travel_service.resolve(lead)
    assert resolved['confidence'] == 'verified' and resolved['level'] == 0
    assert not resolved['requires_review']
    assert next(r for r in crm.travel_service.reviews(tenant) if r['id'] == review['id'])['status'] == 'resolved'
    assert 'Pendientes (0)' in client.get('/settings/travel').get_data(as_text=True)
    assert not crm.store.list('pending_emails')


def test_provisional_rule_requires_explicit_approval(travel_ui):
    crm, client, tenant = travel_ui
    data = dict(name='Destino sintético', scope='municipality', value='Municipio sintético',
                department='Departamento sintético', aliases=[], level=2, approval_status='proposed')
    response = client.post('/api/travel/rules', json=data)
    assert response.status_code == 200
    lead = dict(tenant_id=tenant, locacion='Municipio sintético', departamento='Departamento sintético')
    assert crm.travel_service.resolve(lead)['requires_review']
    data.update(id=response.get_json()['rule']['id'], approval_status='approved')
    assert client.post('/api/travel/rules', json=data).status_code == 200
    result = crm.travel_service.resolve(lead)
    assert result['level'] == 2 and result['photo_amount'] == 2000


@pytest.mark.parametrize('path', ['/api/travel/venues', '/api/travel/rules', '/api/travel/settings'])
def test_travel_mutations_validate_json_and_cross_site_requests(travel_ui, path):
    _, client, _ = travel_ui
    assert client.post(path, json=[]).status_code == 400
    assert client.post(path, data='invalid', content_type='application/json').status_code == 400
    assert client.post(path, json={}, headers={'Origin':'https://external.invalid'}).status_code == 403
    assert client.post(path, json={'tenant_id':'tenant-norkevin'}).status_code == 404


def test_bad_source_coordinates_and_unverified_approval_do_not_write(travel_ui):
    crm, client, tenant = travel_ui
    before = len(crm.travel_service.catalog(tenant))
    for changes in ({'source_url':'javascript:alert(1)'}, {'latitude':91}, {'address':''}, {'travel_level':9}):
        data = venue_payload()
        data.update(changes)
        assert client.post('/api/travel/venues', json=data).status_code == 400
    assert len(crm.travel_service.catalog(tenant)) == before
    assert client.post('/api/travel/venues', json=dict(venue_payload(), id='foreign-id')).status_code == 404


def test_settings_and_records_are_isolated_from_astral(travel_ui):
    crm, client, tenant = travel_ui
    assert client.post('/api/travel/settings', json={'enabled':True}).status_code == 200
    venue = client.post('/api/travel/venues', json=venue_payload()).get_json()['venue']
    crm.store.upsert('leads', dict(id='nork-travel-only', tenant_id=tenant, nombre='Prueba', locacion='Antigua'))
    login_as_tenant(client, 'tenant-norkevin')
    token = crm._workflow_tenant.set('tenant-norkevin')
    try:
        html = client.get('/settings/travel').get_data(as_text=True)
        assert 'Activar en correos' not in html and 'pendiente de confirmación' in html
        assert client.post('/api/travel/settings', json={'enabled':True}).status_code == 400
        assert client.post('/api/travel/venues', json=venue).status_code == 404
        assert client.post('/api/leads/nork-travel-only/travel/research', json={}).status_code == 404
    finally:
        crm._workflow_tenant.reset(token)
    assert crm.travel_service.settings(tenant)['enabled'] is True
