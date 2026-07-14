"""No se puede crear un job sin seleccionar un cliente EXISTENTE (no texto
libre) -- Kevin encontro que se podia crear un job suelto sin cliente
llenando el formulario con basura ('asdfasdfasdf'). Esto lo prueba.

Nota: la regla original exigia un lead_id, pero Kevin pidio suavizarla --
un job se puede crear directo, siempre y cuando tenga un cliente real."""


def test_job_creation_without_client_id_is_rejected(auth_client):
    resp = auth_client.post('/api/jobs/new', json={
        'nombre': 'asdfasdfasdf',
        'boda_date': '2026-08-01',
        'location': 'sadfasdfas',
    })
    assert resp.status_code == 400
    data = resp.get_json()
    assert data['ok'] is False
    assert 'cliente' in data['error'].lower()


def test_job_creation_with_unknown_client_id_is_rejected(auth_client):
    resp = auth_client.post('/api/jobs/new', json={
        'nombre': 'Boda de prueba',
        'client_id': 'client-no-existe',
    })
    assert resp.status_code == 404


def test_job_creation_with_real_client_succeeds(auth_client):
    import app as app_module

    clients = app_module.store.list('clients')
    assert clients, 'necesita al menos un client en los datos de prueba'
    client = clients[0]

    resp = auth_client.post('/api/jobs/new', json={
        'nombre': f"Boda {client.get('first_name', 'Test')}",
        'client_id': client['id'],
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['job']['client_id'] == client['id']


def test_job_creation_from_lead_still_converts_the_lead(auth_client):
    import app as app_module

    leads = app_module.store.list('leads')
    clients = app_module.store.list('clients')
    assert leads and clients

    lead = leads[0]
    client = clients[0]

    resp = auth_client.post('/api/jobs/new', json={
        'nombre': f"Boda {lead.get('nombre', 'Test')}",
        'client_id': client['id'],
        'lead_id': lead['id'],
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['job']['lead_id'] == lead['id']

    updated_lead = app_module.get_lead(lead['id'])
    assert updated_lead['status'] == 'Convertido'
