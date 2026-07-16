"""Kevin: 'el fotografo tambien debe poder firmar los contratos con firma
dibujada y con texto, y si encuentras una mejor tipografia para firmar
mejor, mas cursiva'. El endpoint de firma del CLIENTE ya soportaba
signature_type/signature_text (dibujar o escribir el nombre, renderizado
en un canvas con una fuente cursiva) -- el del FOTOGRAFO solo aceptaba una
imagen dibujada, sin la opcion de escribir el nombre."""
import uuid


def _make_contract(app_module, suffix):
    contract_id = f'contract-sigtype-{suffix}'
    app_module.store.upsert('contracts', {
        'id': contract_id, 'job_id': f'job-sigtype-{suffix}',
        'client_id': f'client-sigtype-{suffix}', 'status': 'Borrador',
        'signed': False, 'photographer_signed': False, 'tenant_id': 'tenant-norkevin',
    })
    return contract_id


def test_photographer_can_sign_with_a_typed_name(auth_client):
    import app as app_module
    contract_id = _make_contract(app_module, uuid.uuid4().hex[:6])

    resp = auth_client.post(f'/api/contracts/{contract_id}/sign-photographer', json={
        'signature': 'data:image/png;base64,fakepngdata',
        'signature_type': 'type',
        'signature_text': 'Kevin Lemus',
    })
    assert resp.status_code == 200

    contract = app_module.store.get('contracts', contract_id)
    assert contract['photographer_signed'] is True
    assert contract['photographer_signature_type'] == 'type'
    assert contract['photographer_signature_text'] == 'Kevin Lemus'


def test_photographer_signature_defaults_to_draw_when_type_not_sent(auth_client):
    """Compatibilidad: firmas ya guardadas (o llamadas viejas al endpoint)
    sin signature_type no deben romper, se asumen dibujadas."""
    import app as app_module
    contract_id = _make_contract(app_module, uuid.uuid4().hex[:6])

    resp = auth_client.post(f'/api/contracts/{contract_id}/sign-photographer', json={
        'signature': 'data:image/png;base64,fakepngdata',
    })
    assert resp.status_code == 200
    contract = app_module.store.get('contracts', contract_id)
    assert contract['photographer_signature_type'] == 'draw'
    assert contract['photographer_signature_text'] == ''


def test_job_page_offers_draw_and_type_signature_modes(auth_client):
    import app as app_module
    client_id = 'client-sigmode-a'
    job_id = 'job-sigmode-a'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Sig', 'last_name': 'Mode',
        'email': 'sigmode@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Sig Mode Test',
        'tenant_id': 'tenant-norkevin',
    })

    resp = auth_client.get(f'/jobs/{job_id}')
    html = resp.get_data(as_text=True)
    assert "setPhotogSigMode('draw')" in html
    assert "setPhotogSigMode('type')" in html
    assert 'photog-typed-sig-name' in html
    assert 'Great Vibes' in html
