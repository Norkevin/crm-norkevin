"""Kevin: 'quiero que solo haya un contrato porque se generaron 3 de la nada'.
El boton + de la tarjeta de Contracts y el trigger del workflow step
'Firma de contrato' llamaban a /api/contracts/new cada vez, creando un
registro (y un link) nuevo en cada click/disparo. El endpoint ahora es
idempotente por job_id, y se agrego un DELETE para limpiar los duplicados
que ya existan (bloqueado si el contrato ya fue firmado)."""


def _make_job_with_client(app_module, suffix):
    client_id = f'client-onecontract-{suffix}'
    job_id = f'job-onecontract-{suffix}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'One', 'last_name': 'Contract',
        'email': 'onecontract@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda One Contract Test',
        'tenant_id': 'tenant-norkevin',
    })
    return client_id, job_id


def test_creating_a_contract_twice_for_the_same_job_reuses_the_same_record(auth_client):
    import app as app_module
    client_id, job_id = _make_job_with_client(app_module, 'a')

    first = auth_client.post('/api/contracts/new', json={'job_id': job_id})
    second = auth_client.post('/api/contracts/new', json={'job_id': job_id})
    third = auth_client.post('/api/contracts/new', json={'job_id': job_id})

    assert first.status_code == 200 and second.status_code == 200 and third.status_code == 200
    ids = {first.get_json()['contract_id'], second.get_json()['contract_id'], third.get_json()['contract_id']}
    assert len(ids) == 1, 'los 3 clicks deben resolver al mismo contrato, no crear 3 registros distintos'

    contracts_for_job = [c for c in app_module.store.list('contracts') if c.get('job_id') == job_id]
    assert len(contracts_for_job) == 1


def test_job_detail_hides_add_button_once_a_contract_exists(auth_client):
    import app as app_module
    client_id, job_id = _make_job_with_client(app_module, 'b')

    resp = auth_client.get(f'/jobs/{job_id}')
    assert 'onclick="createContract()"' in resp.get_data(as_text=True), 'sin contratos, el + debe estar visible'

    auth_client.post('/api/contracts/new', json={'job_id': job_id})

    resp = auth_client.get(f'/jobs/{job_id}')
    assert 'onclick="createContract()"' not in resp.get_data(as_text=True), (
        'con un contrato ya creado, no debe haber boton para agregar otro'
    )


def test_deleting_an_unsigned_contract_works(auth_client):
    import app as app_module
    client_id, job_id = _make_job_with_client(app_module, 'c')

    resp = auth_client.post('/api/contracts/new', json={'job_id': job_id})
    contract_id = resp.get_json()['contract_id']

    del_resp = auth_client.delete(f'/api/contracts/{contract_id}')
    assert del_resp.status_code == 200
    assert del_resp.get_json()['ok'] is True
    assert app_module.get_contract(contract_id) is None

    # Habiendo eliminado el unico contrato, el + debe volver a aparecer.
    resp = auth_client.get(f'/jobs/{job_id}')
    assert 'onclick="createContract()"' in resp.get_data(as_text=True)


def test_deleting_a_signed_contract_is_blocked(auth_client):
    import app as app_module
    client_id, job_id = _make_job_with_client(app_module, 'd')

    resp = auth_client.post('/api/contracts/new', json={'job_id': job_id})
    contract_id = resp.get_json()['contract_id']
    contract = app_module.store.get('contracts', contract_id)
    contract['signed'] = True
    app_module.store.upsert('contracts', contract)

    del_resp = auth_client.delete(f'/api/contracts/{contract_id}')
    assert del_resp.status_code == 400
    assert del_resp.get_json()['ok'] is False
    assert app_module.get_contract(contract_id) is not None, 'un contrato firmado no debe poder eliminarse'


def test_workflow_step_trigger_reuses_the_same_contract_across_multiple_fires(auth_client):
    """Simula lo que pasaba en produccion: el step 'Firma de contrato' del
    workflow crea un contrato via /api/contracts/new cada vez que se dispara.
    Si el usuario lo dispara mas de una vez (doble click, reintentos), debe
    seguir siendo el mismo contrato -- mismo link -- no uno nuevo cada vez."""
    import app as app_module
    client_id, job_id = _make_job_with_client(app_module, 'e')

    contract_ids = []
    for _ in range(3):
        resp = auth_client.post('/api/contracts/new', json={'job_id': job_id})
        contract_ids.append(resp.get_json()['contract_id'])

    assert len(set(contract_ids)) == 1
