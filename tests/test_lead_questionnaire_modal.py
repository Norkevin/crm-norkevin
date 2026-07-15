"""Kevin: 'Crear cuestionario' desde el Lead no hacia nada, pero el mismo
paso desde el Workflow si mandaba el correo -- la causa era que el lead
usaba un prompt() nativo del navegador (que Chrome puede bloquear en
silencio) en vez de un modal real como en Jobs. Ahora ambos comparten
modal y endpoint."""


def test_lead_detail_has_questionnaire_modal_not_native_prompt(auth_client):
    import app as app_module
    import uuid
    lead_id = 'lead-' + uuid.uuid4().hex[:8]
    app_module.upsert_lead({
        'id': lead_id, 'nombre': 'Modal Test', 'email': 'modal@example.com',
        'status': 'Nuevo', 'tenant_id': 'tenant-norkevin',
    })
    resp = auth_client.get(f'/leads/{lead_id}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'lead-questionnaire-modal' in html
    assert 'submitLeadQuestionnaire' in html
    assert "prompt('Questionnaire name'" not in html


def test_lead_questionnaire_creation_sends_email_with_link(auth_client):
    import app as app_module
    import uuid
    lead_id = 'lead-' + uuid.uuid4().hex[:8]
    app_module.upsert_lead({
        'id': lead_id, 'nombre': 'Envio Test', 'email': 'envio@example.com',
        'status': 'Nuevo', 'tenant_id': 'tenant-norkevin',
    })
    resp = auth_client.post(f'/api/leads/{lead_id}/questionnaires', json={
        'name': 'Cuestionario de Bodas Generico',
        'subject': '¡Esto es importante para tu boda!',
        'body': 'Hola,\n\n[LINK AL CUESTIONARIO]\n\nSaludos.',
        'send_email': True,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['mail_id']

    mail = next(m for m in app_module.store.list('mail_log') if m.get('id') == data['mail_id'])
    assert f"/questionnaires/{data['questionnaire']['id']}" in (mail.get('body') or '')
