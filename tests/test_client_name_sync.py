"""Kevin: 'llene el formulario con Angel Lemus pero el cliente que quedo
vinculado a la cotizacion aceptada decia Kevin Lemus' -- la causa era que
_ensure_client_for_lead encontraba un cliente existente por email/telefono
(coincidencia real o de pruebas) y SOLO llenaba campos vacios, nunca
corregia un nombre que ya estaba puesto. El lead mas reciente ahora
sincroniza nombre/telefono/email/direccion sobre el cliente encontrado."""
import uuid


def test_matching_client_by_email_gets_its_name_updated_to_the_new_lead(auth_client):
    import app as app_module

    shared_email = f'shared-{uuid.uuid4().hex[:6]}@example.com'
    app_module.store.upsert('clients', {
        'id': 'client-old-' + uuid.uuid4().hex[:6],
        'first_name': 'Kevin', 'last_name': 'Lemus',
        'email': shared_email, 'phone': '', 'tenant_id': 'tenant-norkevin',
    })

    lead = {
        'id': 'lead-' + uuid.uuid4().hex[:6],
        'nombre': 'Angel Lemus', 'email': shared_email,
        'telefono': '', 'locacion': '', 'tenant_id': 'tenant-norkevin',
    }
    client, created = app_module._ensure_client_for_lead(lead)

    assert created is False, 'debe reutilizar el cliente existente (mismo email), no crear uno duplicado'
    assert client['first_name'] == 'Angel'
    assert client['last_name'] == 'Lemus'

    stored = app_module.get_client(client['id'])
    assert stored['first_name'] == 'Angel'
    assert stored['last_name'] == 'Lemus'


def test_matching_client_by_phone_also_gets_name_synced(auth_client):
    import app as app_module

    shared_phone = '55512345'
    app_module.store.upsert('clients', {
        'id': 'client-old2-' + uuid.uuid4().hex[:6],
        'first_name': 'Old', 'last_name': 'Name',
        'email': '', 'phone': shared_phone, 'tenant_id': 'tenant-norkevin',
    })

    lead = {
        'id': 'lead-' + uuid.uuid4().hex[:6],
        'nombre': 'Nuevo Nombre', 'email': '',
        'telefono': shared_phone, 'locacion': '', 'tenant_id': 'tenant-norkevin',
    }
    client, created = app_module._ensure_client_for_lead(lead)

    assert created is False
    assert client['first_name'] == 'Nuevo'
    assert client['last_name'] == 'Nombre'


def test_quote_acceptance_flow_reflects_correct_client_name(auth_client):
    """Reproduce el flujo completo: lead con nombre A, acepta cotizacion,
    el job resultante debe quedar vinculado a un cliente con nombre A, no
    con el nombre de un cliente viejo que compartia contacto."""
    import app as app_module

    shared_email = f'flow-{uuid.uuid4().hex[:6]}@example.com'
    app_module.store.upsert('clients', {
        'id': 'client-flowold-' + uuid.uuid4().hex[:6],
        'first_name': 'Kevin', 'last_name': 'Lemus',
        'email': shared_email, 'phone': '', 'tenant_id': 'tenant-norkevin',
    })

    lead_id = 'lead-flow-' + uuid.uuid4().hex[:6]
    app_module.upsert_lead({
        'id': lead_id, 'nombre': 'Angel Lemus', 'email': shared_email,
        'status': 'Nuevo', 'tenant_id': 'tenant-norkevin', 'fecha_boda': '2028-05-01',
    })
    lead = app_module.get_lead(lead_id)

    result = app_module._convert_lead_to_job(lead, quote=None, status='Confirmado', create_payments=False)
    job = result['job']
    client = app_module.get_client(job['client_id'])

    assert client['first_name'] == 'Angel'
    assert client['last_name'] == 'Lemus'
