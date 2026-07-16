"""Kevin: 'quiero que por cada job se pueda agregar hasta 3 clientes, el
cliente principal el secundario y el tercero seria la wedding planner, esto
me sirve porque le mandaria los correos a los 3 y asi no se le pasa a
nadie'. Confirma un patron que ya aparecia en su propio export de Studio
Ninja (BODA CON GERALDINE tenia un contacto "(WP)" -- wedding planner --
metido a la fuerza en el last_name porque no habia donde mas ponerlo)."""
import uuid


def _make_job_with_primary_client(app_module, suffix):
    client_id = f'client-multi-{suffix}'
    job_id = f'job-multi-{suffix}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Ana', 'last_name': 'Principal',
        'email': 'ana.principal@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Multi Client Test',
        'tenant_id': 'tenant-norkevin',
    })
    return job_id, client_id


def test_link_existing_client_as_secondary(auth_client):
    import app as app_module
    job_id, client_id = _make_job_with_primary_client(app_module, uuid.uuid4().hex[:6])
    secondary_id = f'client-existing-{uuid.uuid4().hex[:6]}'
    app_module.store.upsert('clients', {
        'id': secondary_id, 'first_name': 'Beto', 'last_name': 'Secundario',
        'email': 'beto@example.com', 'tenant_id': 'tenant-norkevin',
    })

    resp = auth_client.post(f'/api/jobs/{job_id}/link-client', json={
        'role': 'secondary', 'client_id': secondary_id,
    })
    assert resp.status_code == 200

    job = app_module.get_job(job_id)
    assert job['secondary_client_id'] == secondary_id


def test_link_new_client_as_wedding_planner_creates_a_client_record(auth_client):
    import app as app_module
    job_id, client_id = _make_job_with_primary_client(app_module, uuid.uuid4().hex[:6])

    resp = auth_client.post(f'/api/jobs/{job_id}/link-client', json={
        'role': 'planner', 'first_name': 'Geraldine', 'last_name': 'Barberena',
        'email': 'geraldine@weddingplanner.com', 'phone': '5555-9999',
    })
    assert resp.status_code == 200
    new_client_id = resp.get_json()['client']['id']

    job = app_module.get_job(job_id)
    assert job['planner_client_id'] == new_client_id

    stored = app_module.store.get('clients', new_client_id)
    assert stored['first_name'] == 'Geraldine'
    assert stored['email'] == 'geraldine@weddingplanner.com'


def test_link_client_requires_a_valid_role(auth_client):
    import app as app_module
    job_id, client_id = _make_job_with_primary_client(app_module, uuid.uuid4().hex[:6])

    resp = auth_client.post(f'/api/jobs/{job_id}/link-client', json={
        'role': 'not-a-real-role', 'first_name': 'Someone',
    })
    assert resp.status_code == 400


def test_unlink_client_removes_the_link_but_not_the_client_record(auth_client):
    import app as app_module
    job_id, client_id = _make_job_with_primary_client(app_module, uuid.uuid4().hex[:6])
    secondary_id = f'client-unlink-{uuid.uuid4().hex[:6]}'
    app_module.store.upsert('clients', {
        'id': secondary_id, 'first_name': 'Cari', 'last_name': 'Test',
        'email': 'cari@example.com', 'tenant_id': 'tenant-norkevin',
    })
    auth_client.post(f'/api/jobs/{job_id}/link-client', json={'role': 'secondary', 'client_id': secondary_id})

    resp = auth_client.post(f'/api/jobs/{job_id}/unlink-client', json={'role': 'secondary'})
    assert resp.status_code == 200

    job = app_module.get_job(job_id)
    assert not job.get('secondary_client_id')
    assert app_module.store.get('clients', secondary_id) is not None, 'no debe borrar el cliente, solo desvincularlo'


def test_sending_a_job_email_reaches_all_three_linked_clients(auth_client):
    """El objetivo central de Kevin: mandar el correo a los 3 a la vez."""
    import app as app_module
    job_id, client_id = _make_job_with_primary_client(app_module, uuid.uuid4().hex[:6])

    secondary_id = f'client-sec-{uuid.uuid4().hex[:6]}'
    planner_id = f'client-plan-{uuid.uuid4().hex[:6]}'
    app_module.store.upsert('clients', {
        'id': secondary_id, 'first_name': 'Beto', 'last_name': 'Secundario',
        'email': 'beto.secundario@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('clients', {
        'id': planner_id, 'first_name': 'Geraldine', 'last_name': 'Planner',
        'email': 'geraldine.planner@example.com', 'tenant_id': 'tenant-norkevin',
    })
    job = app_module.get_job(job_id)
    job['secondary_client_id'] = secondary_id
    job['planner_client_id'] = planner_id
    app_module.upsert_job(job)

    resp = auth_client.post(f'/api/jobs/{job_id}/send-email', json={
        'subject': 'Aviso importante', 'body': 'Hola a todos',
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'ana.principal@example.com' in data['to']
    assert 'beto.secundario@example.com' in data['to']
    assert 'geraldine.planner@example.com' in data['to']


def test_job_page_shows_add_buttons_for_empty_secondary_and_planner_slots(auth_client):
    import app as app_module
    job_id, client_id = _make_job_with_primary_client(app_module, uuid.uuid4().hex[:6])

    resp = auth_client.get(f'/jobs/{job_id}')
    html = resp.get_data(as_text=True)
    assert '+ Add Secondary' in html
    assert '+ Add Wedding Planner' in html


def test_job_page_shows_linked_secondary_and_planner_with_their_info(auth_client):
    import app as app_module
    job_id, client_id = _make_job_with_primary_client(app_module, uuid.uuid4().hex[:6])
    secondary_id = f'client-show-{uuid.uuid4().hex[:6]}'
    app_module.store.upsert('clients', {
        'id': secondary_id, 'first_name': 'Wendy', 'last_name': 'Morales',
        'email': 'wendy@example.com', 'phone': '4444-5555', 'tenant_id': 'tenant-norkevin',
    })
    job = app_module.get_job(job_id)
    job['secondary_client_id'] = secondary_id
    app_module.upsert_job(job)

    resp = auth_client.get(f'/jobs/{job_id}')
    html = resp.get_data(as_text=True)
    assert 'Wendy Morales' in html
    assert 'wendy@example.com' in html
    assert '(Secondary)' in html
