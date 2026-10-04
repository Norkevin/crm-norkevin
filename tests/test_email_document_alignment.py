import uuid
from flask import session
from src.email_delivery import build_email_message, render_email_html, render_email_proposal_html


def test_document_button_uses_selected_document_and_does_not_duplicate(flask_app):
    import app as crm
    for section, token in [('contracts', '%contract_link%'), ('questionnaires', '%questionnaire_link%'), ('quotes', '%quote_link%')]:
        url = 'https://flowingcrm.com/' + section + '/selected-document'
        for body in ['Abrir documento\n' + token,
                     'Abrir documento\nhttps://flowingcrm.com/portal/client-one#' + section,
                     'Abrir documento\n' + url]:
            result = crm._inject_link(body, url, ['[LINK DEL DOCUMENTO]'], 'Abrir documento')
            assert result == 'Abrir documento\n' + url
            assert result.count(url) == 1
        assert url in crm._inject_link('Sin enlace', url, [], 'Abrir documento')


def test_contract_and_questionnaire_routes_resolve_imported_placeholders(auth_client):
    import app as crm
    from test_contract_template_sync import _make_job_with_client
    cid, jid = _make_job_with_client(crm, uuid.uuid4().hex[:8])
    contract = auth_client.post('/api/contracts/new', json={'job_id':jid}).json['contract_id']
    result = auth_client.post(f'/api/contracts/{contract}/send', json={'subject':'Hola %client_name%',
        'body':'Hola %client_name%\n\nFirmar mi contrato\n%contract_link%'}).json
    mail = next(m for m in crm.store.list('pending_emails') if m['id']==result['mail_id'])
    assert 'Hola Contract Test' in mail['body']
    assert '%contract_link%' not in mail['body']
    assert mail['body'].count('/contracts/' + contract) == 1
    result = auth_client.post(f'/api/jobs/{jid}/questionnaires', json={'name':'Formulario',
        'body':'Completar mi formulario\n%questionnaire_link%', 'send_email':True}).json
    mail = next(m for m in crm.store.list('pending_emails') if m['id']==result['mail_id'])
    assert '%questionnaire_link%' not in mail['body']
    assert '#questionnaires' not in mail['body']
    assert mail['body'].count('/questionnaires/' + result['questionnaire']['id']) == 1


def test_removing_a_template_is_recoverable_and_updates_workflow(auth_client):
    import app as crm
    suffix = uuid.uuid4().hex
    old, new = 'tpl-remove-' + suffix, 'tpl-retain-' + suffix
    with auth_client.session_transaction() as state:
        tenant = state['tenant_id']
    with crm.app.test_request_context('/'):
        session['tenant_id'] = tenant
        snapshot = crm.store.get_tenant_dict('workflow_templates')
        for tid in (old,new):
            crm.store.upsert('email_templates', {'id':tid, 'name':tid, 'activo':True, 'cuerpo':'Hola'})
        workflow = crm.PRODUCTION_WORKFLOW().to_dict()
        workflow['steps'][0]['email_template_id'] = old
        crm._persist_workflow_template(crm._workflow_from_dict(workflow))
    try:
        assert auth_client.delete('/api/settings/email-templates/' + old).status_code == 400
        assert auth_client.delete('/api/settings/email-templates/' + old,json={'replacement_id':new}).json['recoverable']
        with crm.app.test_request_context('/'):
            session['tenant_id'] = tenant
            assert crm.store.get('email_templates', old) is None
            assert crm.store.get('email_templates', old, include_archived=True)['archived_at']
            assert crm.PRODUCTION_WORKFLOW().steps[0].email_template_id == new
        assert 'Plantillas retiradas' in auth_client.get('/settings/email-templates').text
        assert auth_client.post('/api/settings/email-templates/' + old + '/restore').status_code == 200
        with crm.app.test_request_context('/'):
            session['tenant_id'] = tenant
            assert crm.store.get('email_templates', old)['activo'] is True
    finally:
        with crm.app.test_request_context('/'):
            session['tenant_id'] = tenant
            crm.store.save_tenant_dict('workflow_templates',snapshot)
            for tid in (old,new): crm.store.delete('email_templates',tid)


def test_proposal_is_optional_escaped_and_preserves_the_same_links():
    body='Hola Kevin,\n\nVer mi galería\nhttps://example.com/gallery\n\n1. Abre tu galería.\n2. Descarga las fotos.'
    proposal=render_email_proposal_html('Tu galería',body,'Norkevin <Photography>')
    classic=render_email_html('Tu galería',body)
    assert 'Georgia,serif' in proposal and 'Georgia,serif' not in classic
    assert 'Norkevin &lt;Photography&gt;' in proposal
    assert 'href="https://example.com/gallery"' in proposal
    message=build_email_message('owner@example.invalid','Tu galería',body,'owner@example.invalid',html_body=proposal)
    assert message.get_payload()[1].get_content().strip()==proposal
    assert message.get_payload()[0].get_content().strip()==body


def test_proposal_sample_is_one_owner_email_with_optional_html(auth_client, monkeypatch):
    from types import SimpleNamespace
    import app as crm
    calls = []
    monkeypatch.setattr(crm, '_get_email_template', lambda tid: {'id':tid,'name':'Galería',
        'asunto':'Tu galería %client_name%','cuerpo':'Hola %client_name%,\n\nVer mi galería\n%gallery_link%'})
    monkeypatch.setattr('src.gmail_delivery.connected_email',lambda **kw:'owner@example.invalid')
    monkeypatch.setattr('src.gmail_delivery.is_connected',lambda **kw:True)
    def log_email(*args, **kwargs):
        calls.append((args,kwargs))
        return {'status':'sent','delivery_mode':'real','delivery_message_id':'proposal-id'}
    monkeypatch.setattr('src.mail_tracker.get_tracker',lambda:SimpleNamespace(log_email=log_email))
    result=auth_client.post('/api/settings/email-templates/proposal/send-sample',json={'batch':str(uuid.uuid4()),'design':'proposal','to':'other@example.invalid'})
    assert result.status_code==200 and len(calls)==1
    assert calls[0][0][0]=='owner@example.invalid'
    assert 'Georgia,serif' in calls[0][1]['html_body']
    assert '[Muestra:' not in calls[0][1]['html_body']
    assert '/settings/email-template-sample/gallery' in calls[0][1]['html_body']
    assert 'Norkevin' not in result.json.get('error','')
