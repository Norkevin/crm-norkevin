import uuid


def test_samples_only_send_to_connected_owner_and_do_not_touch_jobs(auth_client, monkeypatch):
    import app as crm
    from src.email_delivery import DeliveryResult
    from flask import session
    from unittest.mock import patch
    with auth_client.session_transaction() as state:
        tenant = state['tenant_id']
    template_id = 'tpl-owner-sample-test'
    with crm.app.test_request_context('/'):
        session['tenant_id'] = tenant
        crm.store.upsert('email_templates', {'id': template_id, 'name': 'Muestra', 'asunto': 'Hola %client_name%',
            'cuerpo': '%job_date%\n%invoice_link%\n%contract_link%\n%quote_link%\n%questionnaire_link%\n%gallery_link%\nhttps://example.com/packages'})
    monkeypatch.setattr('src.gmail_delivery.connected_email', lambda **kw: 'owner@example.invalid')
    monkeypatch.setattr('src.gmail_delivery.is_connected', lambda **kw: True)
    calls = []
    def fake_send(to, subject, body, **kwargs):
        calls.append((to, subject, body, kwargs))
        return DeliveryResult(True, 'gmail', 'sample-test-msg', mode='real')
    monkeypatch.setattr('src.mail_tracker.send_email', fake_send)
    route = f'/api/settings/email-templates/{template_id}/send-sample'
    batch = str(uuid.uuid4())
    with patch.object(crm, 'upsert_job', side_effect=AssertionError('Sample touched a job')):
        response = auth_client.post(route, json={'batch': batch, 'to': 'customer@example.invalid'})
        assert response.status_code == 200 and response.json['to'] == 'owner@example.invalid'
        assert auth_client.post(route, json={'batch': batch}).status_code == 200
    assert len(calls) == 1
    to, subject, body, metadata = calls[0]
    assert 'Kevin Lemus' in subject and '[Muestra: Muestra]' in subject
    assert '2027' in body and '%invoice_link%' not in body
    assert 'https://example.com/packages' in body
    for kind in ('invoice', 'contract', 'quote', 'questionnaire', 'gallery'):
        path = '/settings/email-template-sample/' + kind
        assert path in body
        assert auth_client.get(path).status_code == 200
    assert metadata['metadata']['job_id'] is None
    assert metadata['metadata']['lead_id'] is None
    assert auth_client.post(route, json={}).status_code == 400
    assert auth_client.post('/api/settings/email-templates/not-found/send-sample', json={'batch': batch}).status_code == 404
    monkeypatch.setattr('src.gmail_delivery.is_connected', lambda **kw: False)
    assert auth_client.post(route, json={'batch': str(uuid.uuid4())}).status_code == 400
    with crm.app.test_request_context('/'):
        session['tenant_id'] = tenant
        crm.store.delete('email_templates', template_id)
        for mail in crm.store.list('mail_log'):
            if mail.get('template_id') == template_id:
                crm.store.delete('mail_log', mail['id'])
