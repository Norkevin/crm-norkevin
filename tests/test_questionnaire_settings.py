"""Editable tenant templates, frozen answers and resumable public forms."""
import copy
import uuid

import pytest
from flask import session

from conftest import login_as_tenant


@pytest.fixture
def template(auth_client):
    import app as m
    previous = copy.deepcopy(m.get_settings('tenant-norkevin'))
    data = {'name': 'Nuestra boda', 'description': 'Cuéntennos su historia.', 'questions': [
        {'group': 'Detalles', 'columns': 2, 'fields': [
            {'id': 'nombre', 'label': 'Nombre', 'type': 'text', 'required': True},
            {'id': 'momento', 'label': 'Momento favorito', 'type': 'textarea', 'help': 'Si lo desean.'},
            {'id': 'baile', 'label': '¿Habrá baile?', 'type': 'radio', 'options': ['Sí', 'No']},
        ]},
    ]}
    yield data
    m.store.save_tenant_dict('settings', previous, tenant_id='tenant-norkevin')


def create_job_questionnaire(auth_client):
    import app as m
    jid = 'job-test-q-' + uuid.uuid4().hex
    m.upsert_job({'id': jid, 'nombre': 'Boda de prueba', 'tenant_id': 'tenant-norkevin'})
    response = auth_client.post(f'/api/jobs/{jid}/questionnaires', json={'send_email': False})
    assert response.status_code == 200
    return response.get_json()['questionnaire']


def test_template_applies_to_jobs_leads_and_preview(auth_client, template):
    import app as m
    assert auth_client.post('/api/settings/questionnaires', json=template).status_code == 200
    q = create_job_questionnaire(auth_client)
    assert q['name'] == template['name']
    assert q['questions'][0]['fields'][1]['help'] == 'Si lo desean.'
    lid = 'lead-test-q-' + uuid.uuid4().hex
    m.upsert_lead({'id': lid, 'Nombre': 'Pareja de prueba', 'tenant_id': 'tenant-norkevin'})
    lead_q = auth_client.post(f'/api/leads/{lid}/questionnaires', json={'send_email': False}).get_json()['questionnaire']
    assert lead_q['questions'] == q['questions']
    assert b'/settings/questionnaires' in auth_client.get('/settings').data
    assert auth_client.get('/settings/questionnaires').status_code == 200
    html = auth_client.get('/settings/questionnaires/preview').get_data(as_text=True)
    assert 'Vista previa' in html and 'Momento favorito' in html
    assert 'Guardar avance' in html and '--font-sans:' in html


def test_saves_do_not_change_existing_unless_requested_or_other_tenants(auth_client, template):
    import app as m
    auth_client.post('/api/settings/questionnaires', json=template)
    pending = create_job_questionnaire(auth_client)
    answered = create_job_questionnaire(auth_client)
    m.store.upsert('questionnaires', {**pending, 'answers': {'nombre': 'Ana'}})
    m.store.upsert('questionnaires', {**answered, 'status': 'Respondido', 'answers': {'nombre': 'Luis'}})
    other = {**pending, 'id': 'q-other-' + uuid.uuid4().hex, 'tenant_id': 'tenant-q-other'}
    with m.app.test_request_context('/'):
        session['tenant_id'] = 'tenant-q-other'
        m.store.upsert('questionnaires', other)
    template['questions'][0]['fields'] = template['questions'][0]['fields'][:1]
    template['name'] = 'Plantilla editada'
    assert auth_client.post('/api/settings/questionnaires', json=template).status_code == 200
    assert m.store.get('questionnaires', pending['id'])['name'] == 'Nuestra boda'
    auth_client.post('/api/settings/questionnaires', json={**template, 'apply_pending': True})
    assert m.store.get('questionnaires', pending['id'])['name'] == 'Plantilla editada'
    assert m.store.get('questionnaires', pending['id'])['answers'] == {'nombre': 'Ana'}
    assert m.store.get('questionnaires', answered['id'])['name'] == 'Nuestra boda'
    with m.app.test_request_context('/'):
        session['tenant_id'] = 'tenant-q-other'
        assert m.store.get('questionnaires', other['id'])['name'] == 'Nuestra boda'
    login_as_tenant(auth_client, 'tenant-q-other')
    assert 'Plantilla editada' not in auth_client.get('/settings/questionnaires').get_data(as_text=True)


def test_public_draft_final_validation_and_frozen_answers(auth_client, template):
    import app as m
    auth_client.post('/api/settings/questionnaires', json=template)
    q = create_job_questionnaire(auth_client)
    with auth_client.session_transaction() as session:
        session.clear()
    url = f"/api/questionnaires/{q['id']}/submit"
    assert auth_client.post(url, json={'answers': {'momento': 'El abrazo'}, 'draft': True}).status_code == 200
    saved = m.store.get('questionnaires', q['id'])
    assert saved['status'] != 'Respondido' and not saved.get('answered_at')
    assert 'El abrazo' in auth_client.get(f"/questionnaires/{q['id']}").get_data(as_text=True)
    assert auth_client.post(url, json={'answers': {}}).status_code == 400
    assert auth_client.post(url, json={'answers': {'nombre': 'Ana', 'baile': 'invalid'}}).status_code == 400
    assert auth_client.post(url, json={'answers': {'nombre': 'Ana'}}).status_code == 200
    assert auth_client.post(url, json={'answers': {'nombre': 'Cambio'}, 'draft': True}).status_code == 409
    html = auth_client.get(f"/questionnaires/{q['id']}").get_data(as_text=True)
    assert 'Respuestas enviadas' in html and 'El abrazo' in html
    assert 'id="q-save"' not in html
    assert auth_client.get('/settings/questionnaires').status_code in (302, 401)
    assert auth_client.post('/api/settings/questionnaires', json=template).status_code == 401


def test_invalid_templates_do_not_overwrite_saved_template(auth_client, template):
    import app as m
    auth_client.post('/api/settings/questionnaires', json=template)
    before = copy.deepcopy(m.get_settings()['questionnaire_template'])
    invalid = copy.deepcopy(template)
    invalid['questions'][0]['fields'][1]['id'] = 'nombre'
    for payload in (invalid, [], {}, {**template, 'questions': []}):
        assert auth_client.post('/api/settings/questionnaires', json=payload).status_code == 400
    assert m.get_settings()['questionnaire_template'] == before


def test_conditional_details_save_and_do_not_survive_a_no_answer(auth_client, template):
    import app as m
    template['questions'][0]['fields'][2]['details'] = {
        'label': 'Detalles del baile', 'help': 'Si ya los saben.', 'when': ['Sí'],
    }
    response = auth_client.post('/api/settings/questionnaires', json=template)
    assert response.status_code == 200
    assert response.get_json()['template']['questions'][0]['fields'][2]['details']['when'] == ['Sí']
    q = create_job_questionnaire(auth_client)
    url = f"/api/questionnaires/{q['id']}/submit"
    assert auth_client.post(url, json={
        'draft': True, 'answers': {'baile': 'Sí', 'baile__details': 'Primero bailaremos con nuestros padres'},
    }).status_code == 200
    html = auth_client.get(f"/questionnaires/{q['id']}").get_data(as_text=True)
    assert 'data-details-for="baile"' in html
    assert 'Primero bailaremos con nuestros padres' in html
    assert auth_client.post(url, json={'draft': True, 'answers': {'baile': 'No'}}).status_code == 200
    assert m.store.get('questionnaires', q['id'])['answers']['baile__details'] == ''
    assert auth_client.post(url, json={'answers': {'nombre': 'Ana'}}).status_code == 200
    assert m.store.get('questionnaires', q['id'])['status'] == 'Respondido'


def test_details_rules_must_match_options_and_have_unique_ids(auth_client, template):
    choice = template['questions'][0]['fields'][2]
    choice['details'] = {'label': 'Detalles', 'when': ['Tal vez']}
    assert auth_client.post('/api/settings/questionnaires', json=template).status_code == 400
    choice['details']['when'] = ['Sí']
    template['questions'][0]['fields'].append({'id': 'baile__details', 'label': 'Conflicto', 'type': 'text'})
    assert auth_client.post('/api/settings/questionnaires', json=template).status_code == 400


def test_curated_default_has_unique_fields_and_pdf_topics():
    from src.questionnaire_templates import DEFAULT_QUESTIONS, validate_template
    data = validate_template({'name': 'Boda', 'questions': DEFAULT_QUESTIONS})
    fields = [f for g in data['questions'] for f in g['fields']]
    assert len(fields) == 44
    assert len(data['questions']) == 9
    assert {'nombre_novia', 'nombre_novio', 'hora_inicio_cobertura', 'privacidad_publicacion', 'audio_momentos'} <= {f['id'] for f in fields}
