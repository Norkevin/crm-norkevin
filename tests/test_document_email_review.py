from copy import deepcopy
from uuid import uuid4

import pytest

from conftest import login_as_tenant
from test_workflow_automatic_delivery import automatic


def documents(crm, auth_client, business):
    job = business['job']
    job['lead_id'] = business['lead']['id']
    crm.store.upsert('jobs', job)
    quote = dict(id='quote-review', number='COT-REVIEW', status='Borrador',
                 tenant_id=job['tenant_id'], job_id=job['id'], client_id=business['client']['id'],
                 lead_id=job['lead_id'], options=[dict(id='option-review', name='Paquete de prueba', precio_total=100)])
    _, quote = crm.public_tokens.emitir_para(quote)
    crm.store.upsert('quotes', quote)
    contract = auth_client.post('/api/contracts/new', json={'job_id': job['id']}).json['contract_id']
    return {'quote': quote['id'], 'contract': contract}


@pytest.mark.parametrize('kind,path', [('quote', 'quotes'), ('contract', 'contracts')])
def test_document_review_does_not_publish_rotate_or_queue(auth_client, sample_business, kind, path):
    import app as crm
    ids = documents(crm, auth_client, sample_business)
    tables = ('quotes', 'contracts', 'payments', 'pending_emails', 'mail_log', 'jobs')
    before = deepcopy({table: crm.store.list(table) for table in tables})
    response = auth_client.get(f'/api/{path}/{ids[kind]}/send-preview')
    assert response.status_code == 200
    preview = response.json
    assert preview['to_email'] == sample_business['client']['email']
    assert preview['preview_url'] in preview['body']
    assert preview['preview_url'] in preview['html']
    assert not preview['unresolved']
    assert {table: crm.store.list(table) for table in tables} == before


@pytest.mark.parametrize('kind,path', [('quote', 'quotes'), ('contract', 'contracts')])
def test_confirmation_delivers_exact_review_and_reuses_same_composition(auth_client, sample_business, kind, path):
    import app as crm
    ids = documents(crm, auth_client, sample_business)
    url = f'/api/{path}/{ids[kind]}'
    reviewed = auth_client.post(url + '/send-preview', json={
        'subject': 'Documento para %client_name%', 'body': 'Hola %client_name%,\n\nTu documento está listo.'}).json
    assert '%client_name%' not in reviewed['body']
    assert reviewed['body'].count(reviewed['preview_url']) == 1
    payload = {key: reviewed[key] for key in ('to_email', 'subject', 'body')}
    payload['send_request_id'] = str(uuid4())
    first = auth_client.post(url + '/send', json=payload)
    assert first.status_code == 200, first.json
    token = crm.store.get('quotes', ids['quote']).get('public_token_hash')
    repeated = auth_client.post(url + '/send', json=payload).json
    assert repeated['mail_id'] == first.json['mail_id']
    assert crm.store.get('quotes', ids['quote']).get('public_token_hash') == token
    mail = crm.store.get('pending_emails', first.json['mail_id'])
    final_url = first.json['quote_url' if kind == 'quote' else 'contract_url']
    assert mail['body'] == reviewed['body'].replace(reviewed['preview_url'], final_url)
    assert mail['subject'] == reviewed['subject']
    assert mail['to'] == reviewed['to_email']
    if kind == 'quote':
        assert '/q/' in final_url
    else:
        assert not crm.store.get('contracts', ids['contract']).get('signed')


def test_contract_template_preview_resolves_names_and_includes_signing_link(auth_client, sample_business):
    import app as crm
    contract = documents(crm, auth_client, sample_business)['contract']
    crm.store.upsert('email_templates', dict(id='review-template', tenant_id=sample_business['job']['tenant_id'],
        name='Contrato de prueba', asunto='Hola %client_name%', cuerpo='Por favor revisa tu contrato.', activo=True))
    url = f'/api/contracts/{contract}/send-preview'
    selected = auth_client.get(url + '?template_id=review-template').json
    assert selected['subject'] == 'Hola Cliente Prueba'
    assert selected['body'].startswith('Por favor revisa tu contrato.')
    assert selected['preview_url'] in selected['body']
    default = auth_client.post(url, json={'use_template': True, 'template_id': '', 'body': 'Anterior'}).json
    assert 'Anterior' not in default['body']
    assert 'Aqui esta el contrato' in default['body']


@pytest.mark.parametrize('kind,path', [('quote', 'quotes'), ('contract', 'contracts')])
def test_preview_requires_login_and_the_document_tenant(auth_client, sample_business, kind, path):
    import app as crm
    ids = documents(crm, auth_client, sample_business)
    url = f'/api/{path}/{ids[kind]}/send-preview'
    login_as_tenant(auth_client, 'tenant-norkevin-photography', 'norkevinfoto@gmail.com')
    assert auth_client.get(url).status_code == 404
    with auth_client.session_transaction() as state:
        state.clear()
    assert auth_client.get(url).status_code in (302, 401)


def test_confirmed_contract_completes_its_workflow_without_sending_twice(automatic, auth_client):
    crm, setup, sent = automatic
    tenant = 'tenant-norkevin'
    job, instance = setup(tenant, 'job')
    workflow = crm.PRODUCTION_WORKFLOW(tenant).to_dict()
    workflow['steps'][0]['action_type'] = 'send_contract'
    crm.store.save_tenant_dict('workflow_templates', {workflow['id']: workflow}, tenant)
    contract = auth_client.post('/api/contracts/new', json={'job_id': job['id']}).json['contract_id']
    reviewed = auth_client.get(f'/api/contracts/{contract}/send-preview').json
    assert sent == []
    payload = {key: reviewed[key] for key in ('subject', 'body')}
    payload.update(workflow_step_id='auto_step', send_request_id=str(uuid4()))
    response = auth_client.post(f'/api/contracts/{contract}/send', json=payload)
    assert response.status_code == 200, response.json
    assert len(sent) == 1
    assert instance.step_states['auto_step'] == crm.StepStatus.DONE
    assert crm.store.get('pending_emails', response.json['mail_id'])['workflow_step_id'] == 'auto_step'
