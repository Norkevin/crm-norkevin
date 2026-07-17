"""Kevin: 'quiero convertir el CRM en un sistema con tres cuentas
completamente independientes... la informacion de una cuenta no debe
mezclarse, mostrarse ni interferir con la informacion de las demas...
implementa correctamente esta separacion desde la base de datos... no
quiero que la separacion sea unicamente visual en el frontend'.

El bug original (encontrado en la auditoria): get_current_tenant_id()
leia un query param `?tenant=` en vez de la sesion -- cualquiera podia ver
datos de 'otra cuenta' cambiando la URL, y solo 4 de ~16 tablas tenian
algun filtro. Esta suite prueba que la version nueva (filtrado centralizado
en JsonStore, tenant_id resuelto SOLO de session['tenant_id']) realmente
aisla las cuentas, no solo visualmente."""
import uuid

from conftest import login_as_tenant

TENANT_B = 'tenant-isolation-test-b'


def _seed_full_dataset(app_module, tenant_id, suffix):
    """Crea un juego completo de registros (uno de cada tabla tenant-scoped)
    para un tenant dado, con IDs unicos por suffix."""
    client_id = f'client-iso-{suffix}'
    job_id = f'job-iso-{suffix}'
    lead_id = f'lead-iso-{suffix}'
    quote_id = f'quote-iso-{suffix}'
    pay_id = f'pay-iso-{suffix}'
    contract_id = f'contract-iso-{suffix}'
    tpl_id = f'tpl-iso-{suffix}'
    pkg_id = f'pkg-iso-{suffix}'
    cal_id = f'cal-iso-{suffix}'

    app_module.store.upsert('leads', {
        'id': lead_id, 'nombre': f'Lead Iso {suffix}', 'email': f'{suffix}@example.com',
        'status': 'Nuevo', 'tenant_id': tenant_id,
    })
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Iso', 'last_name': suffix,
        'email': f'{suffix}@example.com', 'tenant_id': tenant_id,
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'lead_id': lead_id,
        'nombre': f'Boda Iso {suffix}', 'status': 'Confirmado', 'tenant_id': tenant_id,
    })
    app_module.store.upsert('quotes', {
        'id': quote_id, 'job_id': job_id, 'client_id': client_id,
        'paquete_nombre': 'Paquete Iso', 'status': 'Aceptada', 'tenant_id': tenant_id,
    })
    app_module.store.upsert('payments', {
        'id': pay_id, 'job_id': job_id, 'client_id': client_id, 'invoice_id': f'INV-ISO-{suffix}',
        'amount': 1000, 'status': 'Pendiente', 'due_date': '2027-01-01', 'tenant_id': tenant_id,
    })
    app_module.store.upsert('contracts', {
        'id': contract_id, 'job_id': job_id, 'client_id': client_id,
        'tipo': 'boda', 'status': 'Enviado', 'tenant_id': tenant_id,
    })
    app_module.store.upsert('email_templates', {
        'id': tpl_id, 'name': f'Plantilla Iso {suffix}', 'asunto': 'x', 'cuerpo': 'x',
        'activo': True, 'tenant_id': tenant_id,
    })
    app_module.store.upsert('packages', {
        'id': pkg_id, 'name': f'Paquete Iso {suffix}', 'price': 5000,
        'active': True, 'tenant_id': tenant_id,
    })
    app_module.store.upsert('calendar', {
        'id': cal_id, 'title': f'Evento Iso {suffix}', 'date': '2027-02-01',
        'type': 'event', 'tenant_id': tenant_id,
    })
    return {
        'client_id': client_id, 'job_id': job_id, 'lead_id': lead_id,
        'quote_id': quote_id, 'pay_id': pay_id, 'contract_id': contract_id,
        'tpl_id': tpl_id, 'pkg_id': pkg_id, 'cal_id': cal_id,
    }


def test_listing_pages_never_show_the_other_tenants_records(client):
    import app as app_module
    suf_a = uuid.uuid4().hex[:6]
    suf_b = uuid.uuid4().hex[:6]
    ids_a = _seed_full_dataset(app_module, 'tenant-norkevin', suf_a)
    ids_b = _seed_full_dataset(app_module, TENANT_B, suf_b)

    login_as_tenant(client, 'tenant-norkevin')
    pages = ['/leads', '/clients', '/jobs', '/payments', '/calendar',
             '/settings/email-templates']
    for path in pages:
        resp = client.get(path)
        html = resp.get_data(as_text=True)
        assert f'Iso {suf_b}' not in html, f'{path} no debe mostrar datos de otra cuenta'

    login_as_tenant(client, TENANT_B)
    for path in pages:
        resp = client.get(path)
        html = resp.get_data(as_text=True)
        assert f'Iso {suf_a}' not in html, f'{path} no debe mostrar datos de la otra cuenta'


def test_direct_id_lookup_across_tenants_returns_404_not_the_record(client):
    import app as app_module
    suf_a = uuid.uuid4().hex[:6]
    ids_a = _seed_full_dataset(app_module, 'tenant-norkevin', suf_a)

    login_as_tenant(client, TENANT_B)
    resp = client.get(f"/clients/{ids_a['client_id']}")
    assert resp.status_code == 404, 'tenant B no debe poder ver un cliente de tenant-norkevin por ID directo'

    resp = client.get(f"/jobs/{ids_a['job_id']}")
    assert resp.status_code == 404


def test_api_endpoints_reject_cross_tenant_ids(client):
    import app as app_module
    suf_a = uuid.uuid4().hex[:6]
    ids_a = _seed_full_dataset(app_module, 'tenant-norkevin', suf_a)

    login_as_tenant(client, TENANT_B)
    resp = client.post(f"/api/jobs/{ids_a['job_id']}/delete-payments", json={'confirm': 'BORRAR'})
    assert resp.status_code == 404

    # y el pago de tenant-norkevin sigue intacto, tenant B no pudo tocarlo.
    # test_request_context() explicito en vez de una llamada "pelada" a
    # store.get() -- el test client de Flask deja colgado el contexto de la
    # ULTIMA request real (aqui, la de tenant B) para cualquier codigo Python
    # que corra despues dentro del mismo test, asi que login_as_tenant()
    # (que solo toca la cookie via session_transaction) no basta para que
    # una llamada pelada vea la sesion correcta.
    with app_module.app.test_request_context():
        from flask import session as _sess
        _sess['tenant_id'] = 'tenant-norkevin'
        assert app_module.store.get('payments', ids_a['pay_id']) is not None


def test_query_param_tenant_no_longer_switches_anything(client):
    """Regresion directa del bug original: ?tenant=X ya no debe cambiar
    absolutamente nada, el tenant sale exclusivamente de la sesion."""
    import app as app_module
    suf_a = uuid.uuid4().hex[:6]
    _seed_full_dataset(app_module, 'tenant-norkevin', suf_a)

    login_as_tenant(client, TENANT_B)
    resp = client.get(f'/leads?tenant=tenant-norkevin')
    html = resp.get_data(as_text=True)
    assert f'Iso {suf_a}' not in html, '?tenant= no debe poder colar datos de otra cuenta'

    resp = client.get('/leads?tenant=all')
    html = resp.get_data(as_text=True)
    assert f'Iso {suf_a}' not in html, '?tenant=all ya no debe ser un bypass'


def test_creating_a_record_auto_stamps_the_active_tenant(client):
    import app as app_module
    login_as_tenant(client, TENANT_B)
    resp = client.post('/api/leads/new', json={'nombre': 'Auto Stamp Test'})
    assert resp.status_code == 200
    lead_id = resp.get_json()['lead']['id']
    lead = app_module.store.get('leads', lead_id)
    assert lead is not None
    assert lead['tenant_id'] == TENANT_B


def test_cross_tenant_upsert_with_explicit_wrong_tenant_id_is_rejected(client):
    import app as app_module
    from src.storage import TenantMismatchError
    suf = uuid.uuid4().hex[:6]
    ids_a = _seed_full_dataset(app_module, 'tenant-norkevin', suf)

    login_as_tenant(client, TENANT_B)
    with client.session_transaction():
        pass
    # Simula un intento de escribir directamente un registro de OTRA cuenta
    # estando logueado como tenant B -- el storage debe rechazarlo.
    with app_module.app.test_request_context():
        from flask import session as _sess
        _sess['tenant_id'] = TENANT_B
        try:
            app_module.store.upsert('clients', {'id': ids_a['client_id'], 'first_name': 'Hacked', 'tenant_id': 'tenant-norkevin'})
            raised = False
        except TenantMismatchError:
            raised = True
    assert raised, 'escribir un registro de otra cuenta debe lanzar TenantMismatchError'


def test_reset_test_data_only_wipes_the_active_tenant(client):
    """Kevin: cada cuenta debe funcionar como un CRM independiente --
    'Vaciar datos de prueba' de una cuenta no debe tocar las otras."""
    import app as app_module
    suf_a = uuid.uuid4().hex[:6]
    suf_b = uuid.uuid4().hex[:6]
    ids_a = _seed_full_dataset(app_module, 'tenant-norkevin', suf_a)
    ids_b = _seed_full_dataset(app_module, TENANT_B, suf_b)

    login_as_tenant(client, TENANT_B)
    resp = client.post('/api/admin/reset-test-data', json={'confirm': 'BORRAR'})
    assert resp.status_code == 200

    with app_module.app.test_request_context():
        from flask import session as _sess
        _sess['tenant_id'] = TENANT_B
        assert app_module.store.get('leads', ids_b['lead_id']) is None, 'tenant B se vacio a si mismo'

    with app_module.app.test_request_context():
        from flask import session as _sess
        _sess['tenant_id'] = 'tenant-norkevin'
        assert app_module.store.get('leads', ids_a['lead_id']) is not None, 'tenant-norkevin NO debe verse afectado'


class _FakeRecurrenteResponse:
    def __init__(self, body):
        self._body = body
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        import json
        return json.dumps(self._body).encode('utf-8')


def test_recurrente_credentials_are_isolated_per_tenant(client, monkeypatch):
    from src import recurrente

    login_as_tenant(client, 'tenant-norkevin')
    resp = client.post('/api/settings/recurrente/connect', json={'secret_key': 'sk_live_astral_secret', 'mode': 'live'})
    assert resp.status_code == 200

    login_as_tenant(client, TENANT_B)
    resp = client.post('/api/settings/recurrente/connect', json={'secret_key': 'sk_live_tenantb_secret', 'mode': 'live'})
    assert resp.status_code == 200

    used_keys = []

    def fake_urlopen(req, timeout=15):
        used_keys.append(req.headers.get('X-secret-key') or req.headers.get('X-SECRET-KEY'))
        return _FakeRecurrenteResponse({'checkout_url': 'https://pay.example/x', 'id': 'chk_1'})

    monkeypatch.setattr('src.recurrente.urlrequest.urlopen', fake_urlopen)

    result_a = recurrente.create_checkout(name='x', amount_in_cents=10000, tenant_id='tenant-norkevin')
    result_b = recurrente.create_checkout(name='x', amount_in_cents=10000, tenant_id=TENANT_B)

    assert result_a.get('ok') is True
    assert result_b.get('ok') is True
    assert used_keys[0] == 'sk_live_astral_secret'
    assert used_keys[1] == 'sk_live_tenantb_secret'
    assert used_keys[0] != used_keys[1]


def test_recurrente_connect_never_returns_the_raw_key(client):
    login_as_tenant(client, 'tenant-norkevin')
    resp = client.post('/api/settings/recurrente/connect', json={'secret_key': 'sk_live_super_secret_value', 'mode': 'live'})
    body = resp.get_data(as_text=True)
    assert 'sk_live_super_secret_value' not in body, 'la llave completa nunca debe volver en la respuesta'


def test_recurrente_credentials_stored_encrypted_at_rest(client):
    import app as app_module
    login_as_tenant(client, 'tenant-norkevin')
    client.post('/api/settings/recurrente/connect', json={'secret_key': 'sk_live_plaintext_check', 'mode': 'live'})
    raw = app_module.store.get_tenant_dict('recurrente_credentials', tenant_id='tenant-norkevin')
    assert 'sk_live_plaintext_check' not in json_dump(raw), 'la llave no debe quedar en texto plano en el archivo'
    assert raw.get('secret_key_encrypted')


def json_dump(obj):
    import json
    return json.dumps(obj)


def test_gmail_token_paths_are_isolated_per_tenant():
    from src import gmail_delivery
    path_a = gmail_delivery._token_path(tenant_id='tenant-norkevin')
    path_b = gmail_delivery._token_path(tenant_id=TENANT_B)
    assert path_a != path_b
    assert 'tenant-norkevin' in str(path_a)
    assert TENANT_B in str(path_b)


def test_public_contact_form_tags_lead_with_the_slugs_tenant(client):
    import app as app_module
    resp = client.get('/contacto/norkevin-photography')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'value="norkevin-photography"' in html

    resp = client.post('/api/leads/nuevo', json={
        'nombre': 'Public', 'apellido': 'Test', 'email': f'{uuid.uuid4().hex[:6]}@example.com',
        'pais': 'Guatemala', 'fecha_boda': '2027-05-01', 'tenant_slug': 'norkevin-photography',
    })
    assert resp.status_code == 200
    lead_id = resp.get_json()['lead_id']

    # Logueado como la cuenta correcta (la del slug), el lead aparece con el
    # tenant_id correcto.
    with app_module.app.test_request_context():
        from flask import session as _sess
        _sess['tenant_id'] = 'tenant-norkevin-photography'
        lead = app_module.store.get('leads', lead_id)
        assert lead is not None
        assert lead['tenant_id'] == 'tenant-norkevin-photography'

    # Logueado como OTRA cuenta, el lead publico de norkevin-photography no
    # debe aparecer -- esta es la comprobacion real de aislamiento (un
    # tenant_id sin sesion resuelta devuelve todo sin filtrar a proposito,
    # por eso esta prueba usa una sesion activa de otra cuenta en vez de
    # "sin sesion").
    with app_module.app.test_request_context():
        from flask import session as _sess
        _sess['tenant_id'] = 'tenant-norkevin'
        assert app_module.store.get('leads', lead_id) is None


def test_unknown_contact_form_slug_is_404(client):
    resp = client.get('/contacto/marca-que-no-existe')
    assert resp.status_code == 404


def _purge_synthetic_tenant_ids(app_module):
    """El endpoint de migracion aborta si encuentra CUALQUIER tenant_id que
    no reconoce -- correcto en produccion, pero dentro de esta suite el
    store del proceso es compartido entre tests, y otros tests de este
    mismo archivo (test_listing_pages_..., test_reset_test_data_...) dejan
    registros de TENANT_B sin limpiar. Se purgan antes de probar el reporte
    'limpio' del dry run, para no confundir contaminacion de tests con un
    fallo real del endpoint."""
    from src.storage import TENANT_SCOPED_TABLES
    known = {None, '', 'tenant-norkevin', 'tenant-astral',
             'tenant-norkevin-photography', 'tenant-ramiro-cruz'}
    for table in TENANT_SCOPED_TABLES:
        records = app_module.store._read_raw(table)
        kept = [r for r in records if r.get('tenant_id') in known]
        if len(kept) != len(records):
            app_module.store._save(table, kept)


def test_migration_dry_run_does_not_write_anything(client):
    import app as app_module
    _purge_synthetic_tenant_ids(app_module)
    login_as_tenant(client, 'tenant-norkevin')
    before = app_module.store.list('tenants')
    resp = client.post('/api/admin/migrate-to-multi-tenant', json={'confirm': 'MIGRAR', 'dry_run': True})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['dry_run'] is True
    assert app_module.store.list('tenants') == before, 'dry_run no debe escribir nada'


def test_migration_requires_confirm_keyword(client):
    login_as_tenant(client, 'tenant-norkevin')
    resp = client.post('/api/admin/migrate-to-multi-tenant', json={'dry_run': True})
    assert resp.status_code == 400


def test_migration_aborts_on_unrecognized_tenant_id(client):
    import app as app_module
    login_as_tenant(client, 'tenant-norkevin')

    weird_id = 'client-weird-' + uuid.uuid4().hex[:6]
    with app_module.app.test_request_context():
        from flask import session as _sess
        _sess['tenant_id'] = None
        app_module.store.upsert('clients', {'id': weird_id, 'first_name': 'Weird', 'tenant_id': 'tenant-completamente-desconocido'})

    resp = client.post('/api/admin/migrate-to-multi-tenant', json={'confirm': 'MIGRAR', 'dry_run': True})
    assert resp.status_code == 400
    data = resp.get_json()
    assert 'unexpected' in data
    assert any('tenant-completamente-desconocido' in v for v in data['unexpected'].get('clients', []))
