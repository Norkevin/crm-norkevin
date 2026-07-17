"""Kevin: 'borra todos los datos para seguir haciendo pruebas, prefiero
que este vacio'. /api/admin/reset-test-data vacia leads/clientes/jobs/
cotizaciones/pagos/contratos/cuestionarios/archivos/correos/calendario,
pero NUNCA debe tocar configuracion (plantillas de correo, paquetes,
equipo) -- eso tomo tiempo configurar y no es "dato de prueba"."""
import uuid


def _seed_business_data(app_module):
    suffix = uuid.uuid4().hex[:6]
    app_module.store.upsert('leads', {'id': f'lead-reset-{suffix}', 'nombre': 'Reset Test', 'tenant_id': 'tenant-norkevin'})
    app_module.store.upsert('clients', {'id': f'client-reset-{suffix}', 'first_name': 'Reset', 'tenant_id': 'tenant-norkevin'})
    app_module.store.upsert('jobs', {'id': f'job-reset-{suffix}', 'nombre': 'Reset Job', 'tenant_id': 'tenant-norkevin'})
    app_module.store.upsert('payments', {'id': f'pay-reset-{suffix}', 'amount': 100, 'tenant_id': 'tenant-norkevin'})
    return suffix


def test_reset_requires_typed_confirmation(auth_client):
    import app as app_module
    _seed_business_data(app_module)
    before = auth_client.get('/api/storage/status').get_json()['counts']

    resp = auth_client.post('/api/admin/reset-test-data', json={})
    assert resp.status_code == 400
    assert resp.get_json()['ok'] is False
    after = auth_client.get('/api/storage/status').get_json()['counts']
    assert after == before, 'sin confirmacion no debe borrar nada'

    resp = auth_client.post('/api/admin/reset-test-data', json={'confirm': 'borrar'})
    assert resp.status_code == 400, 'debe ser exactamente BORRAR (mayusculas)'


def test_reset_wipes_business_tables_but_keeps_config(auth_client):
    import app as app_module
    _seed_business_data(app_module)

    templates_before = list(app_module.store.list('email_templates'))
    packages_before = list(app_module.store.list('packages'))
    team_before = list(app_module.store.list('team'))
    assert templates_before, 'el entorno de pruebas ya deberia tener plantillas sembradas'

    resp = auth_client.post('/api/admin/reset-test-data', json={'confirm': 'BORRAR'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True

    for table in ('leads', 'clients', 'jobs', 'quotes', 'payments', 'contracts',
                  'questionnaires', 'files', 'mail_log', 'mail_outbox', 'calendar'):
        assert app_module.store.list(table) == [], f'{table} deberia quedar vacio'

    assert app_module.store.list('email_templates') == templates_before
    assert app_module.store.list('packages') == packages_before
    assert app_module.store.list('team') == team_before


def test_reset_clears_workflow_engine_instances(auth_client):
    import app as app_module
    import uuid as _uuid

    lead_id = 'lead-wf-reset-' + _uuid.uuid4().hex[:6]
    app_module.upsert_lead({
        'id': lead_id, 'nombre': 'WF Reset', 'email': 'wfreset@example.com',
        'status': 'Nuevo', 'tenant_id': 'tenant-norkevin',
    })
    app_module.trigger_workflow_for_lead(lead_id, 'WF Reset')
    assert app_module.workflow_engine.list_instances(subject_id=lead_id, subject_type='lead')

    resp = auth_client.post('/api/admin/reset-test-data', json={'confirm': 'BORRAR'})
    assert resp.status_code == 200
    assert app_module.workflow_engine.instances == {}
