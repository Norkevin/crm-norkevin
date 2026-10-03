import pytest


@pytest.fixture(autouse=True)
def form_brands(_restore_tenants_table):
    import app as module
    tables = ('leads', 'clients', 'pending_emails')
    snapshot = {table: module.store.list(table) for table in tables}
    for tenant in module._MULTI_TENANT_REAL_TENANTS:
        module.store.upsert('tenants', dict(tenant))
    yield
    for table, records in snapshot.items():
        module.store._save(table, records)


@pytest.mark.parametrize('slug,tenant_id', [
    ('norkevin-photography', 'tenant-norkevin-photography'),
    ('astral-weddings', 'tenant-norkevin'),
])
def test_notes_location_and_date_are_saved_in_correct_brand(client, slug, tenant_id):
    import app as module
    payload = {
        'tenant_slug': slug, 'nombre': 'Prueba Formulario',
        'nombre_pareja': '  Alex López  ',
        'email': f'{slug}@example.com', 'notas': '  Boda íntima.\nQueremos fotos al atardecer.  ',
        'locacion': 'Casa Santo Domingo, Antigua Guatemala', 'fecha_tentativa': '2027-11-20',
    }
    response = client.post('/api/captacion', json=payload)
    assert response.status_code == 200
    lead_id = response.get_json()['lead_id']
    with module.app.test_request_context():
        from flask import g
        g.public_tenant_id = tenant_id
        lead = module.get_lead(lead_id)
        assert lead['tenant_id'] == tenant_id
        assert lead['nombre_pareja'] == 'Alex López'
        assert lead['notas'] == payload['notas'].strip()
        assert lead['locacion'] == payload['locacion']
        assert lead['fecha_tentativa'] == payload['fecha_tentativa']
        assert module.get_client(lead['client_id'])
        messages = [m for m in module.store.list('pending_emails') if m.get('lead_id') == lead_id]
        assert messages and payload['notas'].strip() in messages[0]['body']
        assert 'Nombre de su pareja: Alex López' in messages[0]['body']
        g.public_tenant_id = 'tenant-norkevin' if tenant_id != 'tenant-norkevin' else 'tenant-norkevin-photography'
        assert module.get_lead(lead_id) is None


@pytest.mark.parametrize('notes', ['x' * 5001, ['unexpected'], 12])
def test_invalid_notes_do_not_create_leads(client, notes):
    response = client.post('/api/captacion', json={'nombre': 'Invalid', 'notas': notes})
    assert response.status_code == 400


@pytest.mark.parametrize('partner_name', ['x' * 201, ['unexpected'], 12, None])
def test_invalid_partner_name_does_not_create_lead(client, partner_name):
    import app as module
    before = module.store.list('leads')
    response = client.post('/api/captacion', json={
        'nombre': 'Invalid', 'nombre_pareja': partner_name,
        'tenant_slug': 'astral-weddings',
    })
    assert response.status_code == 400
    assert module.store.list('leads') == before


@pytest.mark.parametrize('slug', ['astral-weddings', 'norkevin-photography'])
def test_partner_name_is_optional(client, slug):
    response = client.post('/api/captacion', json={'nombre': 'Sin pareja', 'tenant_slug': slug})
    assert response.status_code == 200


def test_unknown_brand_does_not_fall_back_to_astral(client):
    response = client.post('/api/captacion', json={'nombre': 'Invalid brand', 'tenant_slug': 'unknown-brand'})
    assert response.status_code == 400


def test_brand_contact_details_and_widgets(client, monkeypatch):
    import app as module
    monkeypatch.setattr(module, 'get_settings', lambda **kwargs: {'company': {'phone': '+502 4567 8901', 'email': 'brand@example.com'}})
    norkevin = client.get('/captacion/norkevin-photography').get_data(as_text=True)
    astral = client.get('/captacion/astral-weddings').get_data(as_text=True)
    assert 'tel:+50231648254' in norkevin
    assert 'tel:+50232535549' in astral
    assert 'tel:+50231648254' not in astral
    assert 'AW-10866273491' in norkevin
    assert 'AW-10866273491' not in astral
    assert 'AW-18491511938' in astral
    assert 'AW-18491511938' not in norkevin
    assert 'norkevin-meta.js' in norkevin
    assert 'norkevin-meta.js' not in astral
    assert 'astral-meta.js' in astral
    assert 'astral-meta.js' not in norkevin
    assert 'data-meta-pixel="28915845924706844"' in astral
    assert 'data-meta-pixel="899434420809998"' in norkevin
    for html in [norkevin, astral]:
        assert 'name="nombre_pareja"' in html
        assert 'name="notas"' in html and 'maxlength="5000"' in html
        assert 'role="combobox"' in html and 'flatpickr.min.js' in html
        assert 'brand@example.com' in html
    legacy = client.get('/captacion/ramiro-cruz-photo').get_data(as_text=True)
    assert 'static/captacion.js' not in legacy


@pytest.mark.parametrize('route', ['/captacion/', '/contacto/'])
def test_public_forms_keep_ads_separate_from_other_brands_and_private_pages(client, route):
    norkevin = client.get(route + 'norkevin-photography').get_data(as_text=True)
    astral = client.get(route + 'astral-weddings').get_data(as_text=True)
    other = client.get(route + 'ramiro-cruz-photo').get_data(as_text=True)
    for html, own, foreign in [(norkevin, 'AW-10866273491', 'AW-18491511938'),
                               (astral, 'AW-18491511938', 'AW-10866273491')]:
        assert own in html and foreign not in html
        assert 'public-lead-ads.js' in html
    assert 'public-lead-ads.js' not in other
    assert 'googletagmanager.com' not in client.get('/login').get_data(as_text=True)


def test_fake_phone_is_not_shown_for_astral(client, monkeypatch):
    import app as module
    monkeypatch.setattr(module, 'get_settings', lambda **kwargs: {'company': {'phone': '+502 2222 3333'}})
    html = client.get('/captacion/astral-weddings').get_data(as_text=True)
    assert '2222 3333' not in html
