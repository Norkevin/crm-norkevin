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

    with auth_client.session_transaction() as sess:
        sess.clear()

    public_resp = auth_client.get(f'/questionnaires/{questionnaire_id}')
    assert public_resp.status_code == 200
    assert 'CUESTIONARIO TEST' in public_resp.get_data(as_text=True)

    submit_resp = auth_client.post(f'/api/questionnaires/{questionnaire_id}/submit', json={
        'answers': {
            'ceremony_location': 'Antigua Guatemala',
            'must_have_photos': 'Familia y ceremonia',
        }
    })
    assert submit_resp.status_code == 200
    stored = app_module.store.get('questionnaires', questionnaire_id)
    assert stored['status'] == 'Respondido'
    assert stored['answers']['ceremony_location'] == 'Antigua Guatemala'
