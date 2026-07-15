"""Cuestionarios: deben ser formularios reales y publicos para el cliente."""
import uuid


def test_job_questionnaire_can_be_created_opened_and_submitted(auth_client):
    import app as app_module

    client_id = 'client-questionnaire-' + uuid.uuid4().hex[:8]
    job_id = 'job-questionnaire-' + uuid.uuid4().hex[:8]

    app_module.store.upsert('clients', {
        'id': client_id,
        'first_name': 'Cliente',
        'last_name': 'Cuestionario',
        'email': 'cliente-cuestionario@example.com',
        'tenant_id': 'tenant-norkevin',
    })
    app_module.upsert_job({
        'id': job_id,
        'nombre': 'Boda cuestionario',
        'client_id': client_id,
        'status': 'Confirmado',
        'tenant_id': 'tenant-norkevin',
    })

    resp = auth_client.post(f'/api/jobs/{job_id}/questionnaires', json={
        'name': 'CUESTIONARIO TEST',
        'subject': 'Detalles de boda',
        'body': 'Hola %client_name%, responde tu cuestionario.',
        'send_email': False,
    })
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['ok'] is True
    questionnaire_id = payload['questionnaire']['id']
    assert payload['questionnaire_path'] == f'/questionnaires/{questionnaire_id}'

    with auth_client.session_transaction() as sess:
        sess.clear()

    public_resp = auth_client.get(f'/questionnaires/{questionnaire_id}')
    assert public_resp.status_code == 200
    html = public_resp.get_data(as_text=True)
    assert 'CUESTIONARIO TEST' in html
    assert 'Nombre de la novia *' in html
    assert 'Cual es la direccion exacta de la recepcion?' in html
    assert 'Tendras vals?' in html

    submit_resp = auth_client.post(f'/api/questionnaires/{questionnaire_id}/submit', json={
        'answers': {
            'nombre_novia': 'Ana',
            'ubicacion_ceremonia_boda': 'Antigua Guatemala',
            'tendra_vals': 'Yes',
        }
    })
    assert submit_resp.status_code == 200
    stored = app_module.store.get('questionnaires', questionnaire_id)
    assert stored['status'] == 'Respondido'
    assert stored['answers']['ubicacion_ceremonia_boda'] == 'Antigua Guatemala'
    assert stored['answers']['tendra_vals'] == 'Yes'


def test_lead_questionnaire_uses_same_real_form(auth_client):
    import app as app_module

    lead_id = 'lead-questionnaire-' + uuid.uuid4().hex[:8]
    client_id = 'client-lead-questionnaire-' + uuid.uuid4().hex[:8]
    app_module.store.upsert('clients', {
        'id': client_id,
        'first_name': 'Lead',
        'last_name': 'Questionnaire',
        'email': 'lead-questionnaire@example.com',
        'tenant_id': 'tenant-norkevin',
    })
    app_module.upsert_lead({
        'id': lead_id,
        'Nombre': 'Lead con cuestionario',
        'Email': 'lead-questionnaire@example.com',
        'client_id': client_id,
        'tenant_id': 'tenant-norkevin',
    })

    resp = auth_client.post(f'/api/leads/{lead_id}/questionnaires', json={
        'name': 'CUESTIONARIO LEAD TEST',
        'send_email': False,
    })
    assert resp.status_code == 200
    payload = resp.get_json()
    questionnaire = payload['questionnaire']
    assert questionnaire['questions']
    assert payload['questionnaire_path'] == f"/questionnaires/{questionnaire['id']}"

    public_resp = auth_client.get(payload['questionnaire_path'])
    html = public_resp.get_data(as_text=True)
    assert public_resp.status_code == 200
    assert 'CUESTIONARIO LEAD TEST' in html
    assert 'Cual es la direccion donde la novia se estara preparando?' in html
