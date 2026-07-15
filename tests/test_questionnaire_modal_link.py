"""Kevin: comparo el modal de cuestionario contra el de contrato (ya con
Template + link real) y pidio 'que este igual... con el link automatico
puesto del cuestionario' -- antes el modal mostraba el placeholder literal
[LINK AL CUESTIONARIO] sin resolver porque el cuestionario recien se
creaba al ENVIAR, no al abrir el modal. Ahora, igual que los contratos,
se prepara (crea/reutiliza en Draft) antes de abrir el modal para tener
un id/link real desde el primer momento."""
import uuid


def _make_job_with_client(app_module, suffix):
    client_id = f'client-qprep-{suffix}'
    job_id = f'job-qprep-{suffix}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Prep', 'last_name': 'Test',
        'email': 'preptest@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Prep Test',
        'tenant_id': 'tenant-norkevin',
    })
    return client_id, job_id


def test_job_detail_questionnaire_modal_has_template_selector(auth_client):
    import app as app_module
    client_id, job_id = _make_job_with_client(app_module, 'a')

    resp = auth_client.get(f'/jobs/{job_id}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="questionnaire-template"' in html
    assert 'applyQuestionnaireTemplate' in html
    assert '/questionnaires/prepare' in html


def test_prepare_endpoint_creates_draft_with_real_url_and_sends_nothing(auth_client):
    import app as app_module
    client_id, job_id = _make_job_with_client(app_module, 'b')

    resp = auth_client.post(f'/api/jobs/{job_id}/questionnaires/prepare', json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['questionnaire']['status'] == 'Draft'
    assert data['questionnaire_url'].endswith('/questionnaires/' + data['questionnaire']['id'])
    assert data['mail_id'] is None, 'prepare no debe mandar ningun correo'

    mail = [m for m in app_module.store.list('mail_log') if m.get('job_id') == job_id]
    assert not mail


def test_prepare_reuses_the_same_draft_on_repeated_calls(auth_client):
    """Abrir el modal varias veces (sin enviar) no debe crear cuestionarios
    duplicados -- reutiliza el mismo Draft."""
    import app as app_module
    client_id, job_id = _make_job_with_client(app_module, 'c')

    resp1 = auth_client.post(f'/api/jobs/{job_id}/questionnaires/prepare', json={})
    resp2 = auth_client.post(f'/api/jobs/{job_id}/questionnaires/prepare', json={})
    id1 = resp1.get_json()['questionnaire']['id']
    id2 = resp2.get_json()['questionnaire']['id']
    assert id1 == id2

    all_q = [q for q in app_module.store.list('questionnaires') if q.get('job_id') == job_id]
    assert len(all_q) == 1


def test_submitting_with_questionnaire_id_sends_that_exact_prepared_record(auth_client):
    """El flujo completo: prepare (obtiene id real) -> submit referenciando
    ese mismo questionnaire_id -- no debe crear un segundo registro."""
    import app as app_module
    client_id, job_id = _make_job_with_client(app_module, 'd')

    prepared = auth_client.post(f'/api/jobs/{job_id}/questionnaires/prepare', json={}).get_json()
    qid = prepared['questionnaire']['id']
    real_url = prepared['questionnaire_url']

    resp = auth_client.post(f'/api/jobs/{job_id}/questionnaires', json={
        'questionnaire_id': qid,
        'subject': 'Asunto de prueba',
        'body': f'Hola,\n\nAqui esta tu cuestionario: {real_url}\n\nSaludos.',
        'send_email': True,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['questionnaire']['id'] == qid, 'debe reusar exactamente el mismo registro preparado'

    all_q = [q for q in app_module.store.list('questionnaires') if q.get('job_id') == job_id]
    assert len(all_q) == 1
    assert all_q[0]['status'] == 'Sent'

    mail = next(m for m in app_module.store.list('mail_log') if m.get('id') == data['mail_id'])
    assert real_url in mail['body']


def test_resending_existing_non_draft_questionnaire_does_not_touch_other_drafts(auth_client):
    """Si el job tiene un cuestionario YA enviado y aparte un Draft
    pendiente, reenviar el que ya esta 'Sent' (boton Send de la fila) no
    debe crear un tercer registro ni tocar el Draft."""
    import app as app_module
    client_id, job_id = _make_job_with_client(app_module, 'e')

    sent_id = 'questionnaire-sent-' + uuid.uuid4().hex[:6]
    app_module.store.upsert('questionnaires', {
        'id': sent_id, 'job_id': job_id, 'client_id': client_id,
        'name': 'Cuestionario ya enviado', 'status': 'Sent',
        'questions': app_module.QUESTIONNAIRE_QUESTIONS, 'created': '2026-01-01',
        'tenant_id': 'tenant-norkevin',
    })
    draft_id = 'questionnaire-draft-' + uuid.uuid4().hex[:6]
    app_module.store.upsert('questionnaires', {
        'id': draft_id, 'job_id': job_id, 'client_id': client_id,
        'name': 'Cuestionario de Bodas Generico', 'status': 'Draft',
        'questions': app_module.QUESTIONNAIRE_QUESTIONS, 'created': '2026-01-02',
        'tenant_id': 'tenant-norkevin',
    })

    resp = auth_client.post(f'/api/jobs/{job_id}/questionnaires', json={
        'questionnaire_id': sent_id,
        'subject': 'Reenvio', 'body': 'Reenvio del cuestionario', 'send_email': True,
    })
    assert resp.status_code == 200
    assert resp.get_json()['questionnaire']['id'] == sent_id

    all_q = [q for q in app_module.store.list('questionnaires') if q.get('job_id') == job_id]
    assert len(all_q) == 2, 'no debe crear un tercer cuestionario'
    draft_still = next(q for q in all_q if q['id'] == draft_id)
    assert draft_still['status'] == 'Draft', 'el draft aparte no debe verse afectado'
