from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import login_as_tenant


def create_job(crm, tenant, suffix):
    job = {'id': f'job-{suffix}', 'client_id': f'client-{suffix}', 'tenant_id': tenant,
           'nombre': 'Boda de prueba', 'price_total': 2500, 'boda_date': '2028-06-02'}
    crm.store.upsert('clients', {'id': job['client_id'], 'tenant_id': tenant, 'first_name': 'Cliente'})
    crm.store.upsert('jobs', job)
    return job


def test_brand_defaults_and_existing_drafts_are_independent(auth_client, monkeypatch):
    import app as crm

    astral = 'tenant-norkevin'
    job = create_job(crm, astral, 'original')
    legacy = {'id': 'contract-legacy', 'job_id': job['id'], 'client_id': job['client_id'], 'tenant_id': astral}
    crm.store.upsert('contracts', legacy)
    assert '/contracts/contract-legacy/edit' in auth_client.get('/jobs/job-original').text
    assert 'Compromiso del Fotografo' in auth_client.get('/contracts/contract-legacy/edit').text

    tpl = {'template_id': 'nuevo', 'revision': 0, 'name': 'Entrega privada', 'make_default': True,
           'clauses': [['Entrega', 'Material de %estudio% por %total%.']]}
    created = auth_client.post('/api/settings/contracts', json=tpl)
    assert created.status_code == 200
    type_id = parse_qs(urlsplit(created.json['url']).query)['tipo'][0]
    assert 'Entrega privada · Predeterminado' in auth_client.get('/settings/contracts').text
    wedding = create_job(crm, astral, 'nueva')
    contract_id = auth_client.post('/api/contracts/new', json={'job_id': wedding['id']}).json['contract_id']
    url = f'/contracts/{contract_id}'
    assert 'Q2,500.00' in auth_client.get(url).text
    assert crm.get_contract(contract_id)['template_id'] == type_id
    tpl.update(template_id=type_id, revision=1, clauses=[['Otra entrega', 'Nuevos términos']])
    assert auth_client.post('/api/settings/contracts', json=tpl).json['ok']
    assert 'Nuevos términos' not in auth_client.get(url).text
    assert 'Compromiso del Fotografo' in auth_client.get('/contracts/contract-legacy').text

    edit = {'template_id': type_id, 'revision': 0,
            'clauses': [['Entrega personal', 'Inicio\n* Copia privada\nDespués de la entrega.']]}
    assert auth_client.post('/api/contracts/contract-legacy/edit', json=edit).json['ok']
    assert auth_client.post('/api/contracts/contract-legacy/edit', json=edit).status_code == 409
    html = auth_client.get('/contracts/contract-legacy').text
    assert html.index('Inicio') < html.index('Copia privada') < html.index('Después de la entrega.')
    assert 'Copia privada' not in auth_client.get(url).text
    draw = crm.render_contract_pdf
    def capture(*args, **kwargs):
        assert kwargs['terms'] == [('Entrega personal', edit['clauses'][0][1])]
        return draw(*args, **kwargs)
    monkeypatch.setattr(crm, 'render_contract_pdf', capture)
    assert auth_client.get('/contracts/contract-legacy/pdf').data.startswith(b'%PDF')

    login_as_tenant(auth_client, 'tenant-norkevin-photography', 'norkevin@example.invalid')
    assert 'Entrega privada' not in auth_client.get('/settings/contracts').text
    assert auth_client.get('/contracts/contract-legacy/edit').status_code == 404
    assert auth_client.post('/api/contracts/contract-legacy/edit', json=edit).status_code == 404
    private_job = create_job(crm, 'tenant-norkevin-photography', 'photography')
    private_id = auth_client.post('/api/contracts/new', json={'job_id': private_job['id']}).json['contract_id']
    assert 'Compromiso del Fotografo' in auth_client.get(f'/contracts/{private_id}/edit').text
    assert auth_client.post(f'/api/contracts/{private_id}/edit', json=edit).status_code == 400


@pytest.mark.parametrize('signer', ['sign', 'sign-photographer'])
def test_each_signature_locks_the_reviewed_version(auth_client, signer):
    import app as crm

    job = create_job(crm, 'tenant-norkevin', signer)
    contract_id = auth_client.post('/api/contracts/new', json={'job_id': job['id']}).json['contract_id']
    url = f'/api/contracts/{contract_id}'
    edit = {'template_id': 'boda', 'revision': 0, 'clauses': [['Acuerdo', 'Valor %total%.']]}
    assert auth_client.post(url+'/edit', json=edit).status_code == 200
    assert auth_client.post(url+'/'+signer, json={'signature': 'previa'}).status_code == 409
    assert auth_client.post(url+'/'+signer, json={'signature': 'aceptada', 'terms_revision': 1}).status_code == 200
    edit['revision'] = 1
    assert auth_client.post(url+'/edit', json=edit).status_code == 409
    assert f'/contracts/{contract_id}/edit' not in auth_client.get('/jobs/'+job['id']).text
    crm.store.upsert('jobs', dict(job, price_total=8000))
    assert crm.get_contract(contract_id)['terms'] == [['Acuerdo', 'Valor Q2,500.00.']]


def test_editor_and_templates_require_owner_session(client):
    for url in ('/settings/contracts', '/contracts/contract-example/edit'):
        assert '/login' in client.get(url).location
    for url in ('/api/settings/contracts', '/api/contracts/contract-example/edit'):
        assert client.post(url, json={}).status_code == 401
