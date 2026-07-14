"""Kevin: 'cree un lead desde el formulario pero no se creo como cliente,
todo debe ir conectado'. Un lead nuevo (de cualquier canal) debe quedar
enlazado a un client_id desde el momento en que se crea, no solo cuando se
convierte a job."""


def test_public_contact_form_lead_creates_a_linked_client(client):
    resp = client.post('/api/leads/nuevo', json={
        'nombre': 'Sofia', 'apellido': 'Enlace', 'email': 'sofia.enlace@example.com',
        'pais': 'Guatemala', 'fecha_boda': '2027-08-01',
    })
    assert resp.status_code == 200
    lead_id = resp.get_json()['lead_id']

    import app as app_module
    lead = app_module.get_lead(lead_id)
    assert lead.get('client_id'), 'el lead deberia quedar enlazado a un cliente'

    linked_client = app_module.get_client(lead['client_id'])
    assert linked_client, 'el client_id del lead deberia apuntar a un cliente real'
    assert linked_client.get('email') == 'sofia.enlace@example.com'


def test_captacion_form_lead_creates_a_linked_client(client):
    resp = client.post('/api/captacion', json={
        'nombre': 'Diego Formulario', 'email': 'diego.formulario@example.com',
    })
    assert resp.status_code == 200
    lead_id = resp.get_json()['lead_id']

    import app as app_module
    lead = app_module.get_lead(lead_id)
    assert lead.get('client_id')
    assert app_module.get_client(lead['client_id'])


def test_admin_created_lead_without_client_gets_one_linked(auth_client):
    resp = auth_client.post('/api/leads/new', json={
        'nombre': 'Lead Sin Cliente', 'email': 'sincliente@example.com',
    })
    assert resp.status_code == 200
    lead = resp.get_json()['lead']
    assert lead.get('client_id')

    import app as app_module
    assert app_module.get_client(lead['client_id'])
