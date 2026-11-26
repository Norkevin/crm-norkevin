"""Kevin: 'quiero que solo haya un contrato porque se generaron 3 de la nada'.
El boton + de la tarjeta de Contracts y el trigger del workflow step
'Firma de contrato' llamaban a /api/contracts/new cada vez, creando un
registro (y un link) nuevo en cada click/disparo. El endpoint ahora es
idempotente por job_id, y se agrego un DELETE para limpiar los duplicados
que ya existan (las versiones firmadas requieren confirmación)."""


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


def test_deleting_a_signed_contract_requires_confirmation(auth_client):
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


def test_contract_roles_retirement_and_replacement(auth_client, sample_business):
    import app as crm
    from conftest import login_as_tenant

    job = sample_business['job']
    guest = crm.app.test_client()
    def create():
        return auth_client.post('/api/contracts/new', json={'job_id': job['id']}).json['contract_id']

    cid = create()
    url = '/contracts/' + cid
    api = '/api' + url
    mark = {'signature': 'firma de prueba'}
    confirm = {'confirm_signed': True}
    for surface in (url, url + '/estudio'):
        studio = auth_client.get(surface).text
        assert 'Firmar como fotógrafo' in studio and 'Firmar como cliente' not in studio
        assert 'Borrar contrato' in studio
    portal_url = '/portal/' + job['client_id']
    assert 'id="modal-sign"' not in auth_client.get(portal_url).text
    assert 'Firmar como fotógrafo' in auth_client.get(portal_url).text
    assert 'Firmar como cliente' in guest.get(portal_url).text
    public = guest.get(url).text
    assert 'Firmar como cliente' in public and 'Firmar como fotógrafo' not in public
    assert 'Borrar contrato' not in public
    assert '/login' in guest.get(url + '/estudio').location
    assert guest.post(api + '/sign-photographer', json=mark).status_code == 401
    assert guest.delete(api, json=confirm).status_code == 401
    assert auth_client.post(api + '/sign', json=mark).status_code == 403
    assert auth_client.post(api + '/sign-photographer', json=mark).status_code == 200
    assert not crm.get_contract(cid)['signed']
    assert 'Pendiente de la firma del cliente' in auth_client.get(url).text
    assert guest.post(api + '/sign', json=mark).status_code == 200

    other = crm.app.test_client()
    login_as_tenant(other, 'tenant-norkevin-photography', 'otra@example.invalid')
    assert other.delete(api, json=confirm).status_code == 404
    assert other.post(api + '/sign-photographer', json=mark).status_code == 404
    assert other.get(url + '/estudio').status_code == 404

    mail = dict(id='pend-retirar', tenant_id=job['tenant_id'], job_id=job['id'],
                idempotency_key='contract:' + cid + ':workflow:1', status='sending')
    crm.store.upsert('pending_emails', mail)
    assert auth_client.delete(api, json=confirm).status_code == 409
    crm.store.upsert('pending_emails', dict(mail, status='pending'))
    before = crm.get_contract(cid)
    assert auth_client.delete(api, json=confirm).json['ok']
    saved = crm.store.get('contracts', cid, include_archived=True)
    assert saved['archived_at'] and saved['archived_by']
    assert {k: saved[k] for k in before} == before
    assert crm.store.get('pending_emails', mail['id'])['status'] == 'discarded'
    for path in (url, url+'/pdf'):
        assert guest.get(path).status_code == 404
    assert guest.post(api+'/sign', json=mark).status_code == 404
    assert auth_client.post(api+'/sign-photographer', json=mark).status_code == 404
    assert auth_client.post(api+'/send', json={}).status_code == 404
    assert cid not in guest.get('/portal/' + job['client_id']).text
    new_id = create()
    assert new_id != cid and not crm.get_contract(new_id)['signed']
    assert 'Firmar como cliente' in guest.get('/contracts/' + new_id).text
    delivery = auth_client.post('/api/contracts/' + new_id + '/send', json={})
    assert delivery.status_code == 200 and delivery.json['contract_url'].endswith('/' + new_id)
