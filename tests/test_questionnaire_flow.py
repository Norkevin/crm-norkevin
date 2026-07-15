"""Kevin: 'al presionar el boton de Cuestionario, unicamente se envia un
correo' -- el modulo real existe (link publico + formulario + respuestas),
pero el link solo se inyectaba al correo si una frase magica en ingles
sobrevivia en el texto editable. Estas pruebas garantizan que el correo
SIEMPRE lleve el link y que el flujo completo funcione."""
import uuid


def _setup_job(app_module):
    cid = 'client-' + uuid.uuid4().hex[:8]
    app_module.store.upsert('clients', {
        'id': cid, 'first_name': 'Q', 'last_name': 'Flow',
        'email': 'qflow@example.com', 'tenant_id': 'tenant-norkevin',
    })
    jid = 'boda-' + uuid.uuid4().hex[:8]
    app_module.upsert_job({
        'id': jid, 'nombre': 'Boda QFlow', 'boda_date': '2027-06-01',
        'client_id': cid, 'status': 'Confirmado', 'tenant_id': 'tenant-norkevin',
    })
    return jid


def test_full_questionnaire_flow(auth_client, client):
    import app as app_module
    jid = _setup_job(app_module)

    resp = auth_client.post(f'/api/jobs/{jid}/questionnaires',
                            json={'name': 'Cuestionario Flow', 'send_email': True})
    assert resp.status_code == 200
    data = resp.get_json()
    qid = data['questionnaire']['id']
    assert data['mail_id'], 'debe registrar el correo enviado'

    resp2 = client.get(f'/questionnaires/{qid}')
    assert resp2.status_code == 200
    assert 'nombre_novia' in resp2.get_data(as_text=True)

    resp3 = client.post(f'/api/questionnaires/{qid}/submit',
                        json={'answers': {'nombre_novia': 'Maria Flow'}})
    assert resp3.status_code == 200
    assert resp3.get_json()['questionnaire']['status'] == 'Respondido'

    resp4 = client.get(f'/questionnaires/{qid}')
    assert 'Maria Flow' in resp4.get_data(as_text=True)


def test_email_always_carries_link_even_if_placeholder_deleted(auth_client):
    import app as app_module
    jid = _setup_job(app_module)

    resp = auth_client.post(f'/api/jobs/{jid}/questionnaires', json={
        'name': 'Sin Placeholder',
        'send_email': True,
        'body': 'Hola, llena el cuestionario porfa. Saludos.',
    })
    assert resp.status_code == 200
    data = resp.get_json()
    qid = data['questionnaire']['id']

    mail = next(m for m in app_module.store.list('mail_log') if m.get('id') == data['mail_id'])
    assert f'/questionnaires/{qid}' in (mail.get('body') or ''), \
        'el correo debe llevar el link al cuestionario aunque el usuario haya borrado el placeholder'


def test_spanish_placeholder_is_replaced(auth_client):
    import app as app_module
    jid = _setup_job(app_module)

    resp = auth_client.post(f'/api/jobs/{jid}/questionnaires', json={
        'name': 'Con Marcador',
        'send_email': True,
        'body': 'Hola,\n\nAca esta tu cuestionario:\n\n[LINK AL CUESTIONARIO]\n\nSaludos.',
    })
    assert resp.status_code == 200
    data = resp.get_json()

    mail = next(m for m in app_module.store.list('mail_log') if m.get('id') == data['mail_id'])
    body = mail.get('body') or ''
    assert '[LINK AL CUESTIONARIO]' not in body
    assert f"/questionnaires/{data['questionnaire']['id']}" in body
