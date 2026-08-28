"""Kevin: comparo el modal 'Send Email' de Leads (con selector de Template)
contra 'Send Contract' de Jobs (sin selector, texto fijo) y pidio que las
pantallas sean iguales. El modal ahora tiene el mismo selector de Template
que Send Email; si Kevin elige una plantilla de Settings que no trae el
link de firma, el backend igual lo garantiza (mismo patron que
_inject_link ya usa para cuestionarios) para no repetir el bug de mandar
un contrato sin forma de firmarlo."""
import uuid


def _make_job_with_client(app_module, suffix):
    client_id = f'client-contract-{suffix}'
    job_id = f'job-contract-{suffix}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Contract', 'last_name': 'Test',
        'email': 'contracttest@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Contract Test',
        'tenant_id': 'tenant-norkevin',
    })
    return client_id, job_id


def test_job_detail_contract_modal_has_template_selector(auth_client):
    import app as app_module
    client_id, job_id = _make_job_with_client(app_module, 'a')

    resp = auth_client.get(f'/jobs/{job_id}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="contract-template"' in html
    assert 'applyContractTemplate' in html


def test_contract_send_keeps_signing_link_even_with_a_template_missing_it(auth_client):
    import app as app_module
    client_id, job_id = _make_job_with_client(app_module, 'b')

    tpl_id = 'tpl-contract-test-' + uuid.uuid4().hex[:6]
    app_module.store.upsert('email_templates', {
        'id': tpl_id, 'name': 'Plantilla sin link', 'activo': True,
        'asunto': 'Tu contrato ya esta listo',
        'cuerpo': 'Hola %client_name%,\n\nTu contrato de bodas esta listo para revisar.\n\nSaludos.',
        'tenant_id': 'tenant-norkevin',
    })

    resp = auth_client.post('/api/contracts/new', json={'job_id': job_id})
    assert resp.status_code == 200
    contract_id = resp.get_json()['contract_id']

    resp = auth_client.post(f'/api/contracts/{contract_id}/send', json={
        'subject': 'Tu contrato ya esta listo',
        'body': 'Hola Contract Test,\n\nTu contrato de bodas esta listo para revisar.\n\nSaludos.',
        'template_id': tpl_id,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True

    # STAGE 2 (agosto 2026): encola en vez de entregar de inmediato.
    mail = next(m for m in app_module.store.list('pending_emails') if m.get('id') == data['mail_id'])
    assert f'/contracts/{contract_id}' in mail['body'], (
        'el link de firma debe estar presente aunque la plantilla elegida no lo traiga'
    )


def test_contract_send_surfaces_mail_delivery_warning(auth_client):
    """STAGE 2 (agosto 2026): api_contract_send ya no entrega de inmediato
    -- todo envio queda esperando aprobacion en /emails, sin importar si
    Gmail esta conectado o no. El mail_warning ahora avisa siempre eso
    (antes, avisaba solo si el proveedor caia al outbox local); el
    patch de send_email que probaba ese caso especifico ya no aplica
    porque queue_email() no llega a llamar a send_email()."""
    import app as app_module

    client_id, job_id = _make_job_with_client(app_module, 'c')
    resp = auth_client.post('/api/contracts/new', json={'job_id': job_id})
    contract_id = resp.get_json()['contract_id']

    resp = auth_client.post(f'/api/contracts/{contract_id}/send', json={})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data['mail_warning'], 'debe avisar que el correo quedo en la cola, no se entrego solo'
    assert 'cola de aprobacion' in data['mail_warning']
