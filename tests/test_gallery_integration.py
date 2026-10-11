import app as app_module


def _records(table):
    fixtures = {
        'jobs': [
            {
                'id': 'job-astral-1',
                'tenant_id': 'tenant-norkevin',
                'nombre': 'Boda Ana y Luis',
                'boda_date': '2026-09-12',
                'status': 'Confirmado',
                'client_id': 'client-1',
                'secondary_client_id': 'client-2',
            },
            {
                'id': 'job-other-tenant',
                'tenant_id': 'tenant-norkevin-photography',
                'nombre': 'No debe aparecer',
                'client_id': 'client-other',
            },
        ],
        'clients': [
            {'id': 'client-1', 'tenant_id': 'tenant-norkevin', 'first_name': 'Ana', 'last_name': 'Pérez', 'email': 'ANA@example.com'},
            {'id': 'client-2', 'tenant_id': 'tenant-norkevin', 'first_name': 'Luis', 'last_name': 'López', 'email': 'luis@example.com'},
            {'id': 'client-other', 'tenant_id': 'tenant-norkevin-photography', 'email': 'private@example.com'},
        ],
        'leads': [],
    }
    if table == 'tenants':
        return app_module._MULTI_TENANT_REAL_TENANTS
    return fixtures.get(table, [])


def test_gallery_job_search_requires_service_token(client, monkeypatch):
    monkeypatch.setenv('GALLERY_INTEGRATION_TOKEN', 'test-gallery-token')
    response = client.get('/api/integrations/gallery/jobs?q=ana')
    assert response.status_code == 401


def test_gallery_job_search_returns_only_astral_contacts(client, monkeypatch):
    monkeypatch.setenv('GALLERY_INTEGRATION_TOKEN', 'test-gallery-token')
    # El endpoint lee con _read_raw y filtra por empresa el mismo: no tiene
    # sesion (es servidor-a-servidor con token), y con el aislamiento
    # cerrado store.list() le devolveria vacio. Lo que se sigue probando
    # es lo importante: que solo salgan los datos de Astral.
    monkeypatch.setattr(app_module.store, 'list', _records)
    monkeypatch.setattr(app_module.store, '_read_raw', _records)
    response = client.get(
        '/api/integrations/gallery/jobs?q=ana',
        headers={'Authorization': 'Bearer test-gallery-token'},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['source'] == 'Astral Weddings'
    assert [job['id'] for job in payload['jobs']] == ['job-astral-1']
    assert [contact['email'] for contact in payload['jobs'][0]['contacts']] == [
        'ana@example.com',
        'luis@example.com',
    ]
    assert 'private@example.com' not in str(payload)


def test_gallery_job_search_rejects_short_query(client, monkeypatch):
    monkeypatch.setenv('GALLERY_INTEGRATION_TOKEN', 'test-gallery-token')
    response = client.get(
        '/api/integrations/gallery/jobs?q=a',
        headers={'Authorization': 'Bearer test-gallery-token'},
    )
    assert response.status_code == 400


def test_gallery_job_browse_returns_dated_jobs_without_query(client, monkeypatch):
    monkeypatch.setenv('GALLERY_INTEGRATION_TOKEN', 'test-gallery-token')
    # El endpoint lee con _read_raw y filtra por empresa el mismo: no tiene
    # sesion (es servidor-a-servidor con token), y con el aislamiento
    # cerrado store.list() le devolveria vacio. Lo que se sigue probando
    # es lo importante: que solo salgan los datos de Astral.
    monkeypatch.setattr(app_module.store, 'list', _records)
    monkeypatch.setattr(app_module.store, '_read_raw', _records)
    response = client.get(
        '/api/integrations/gallery/jobs?all=1&limit=50',
        headers={'Authorization': 'Bearer test-gallery-token'},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert [job['id'] for job in payload['jobs']] == ['job-astral-1']
    assert payload['jobs'][0]['eventDate'] == '2026-09-12'
    assert payload['total'] == 1
    assert payload['hasMore'] is False


def test_gallery_norkevin_returns_only_its_account(client, monkeypatch):
    monkeypatch.setenv('GALLERY_INTEGRATION_TOKEN', 'test-gallery-token')
    monkeypatch.setattr(app_module.store, 'list', _records)
    monkeypatch.setattr(app_module.store, '_read_raw', _records)
    response = client.get('/api/integrations/gallery/jobs?brand=norkevin&all=1',
                          headers={'Authorization': 'Bearer test-gallery-token'})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['brand'] == 'norkevin'
    assert payload['source'] == 'Norkevin Photography'
    assert [job['id'] for job in payload['jobs']] == ['job-other-tenant']
    assert payload['jobs'][0]['contacts'][0]['email'] == 'private@example.com'
    assert 'ana@example.com' not in str(payload)
    assert 'luis@example.com' not in str(payload)


def test_gallery_brand_is_allowlisted_and_cannot_override_tenant(client, monkeypatch):
    monkeypatch.setenv('GALLERY_INTEGRATION_TOKEN', 'test-gallery-token')
    monkeypatch.setattr(app_module.store, 'list', _records)
    monkeypatch.setattr(app_module.store, '_read_raw', _records)
    headers = {'Authorization': 'Bearer test-gallery-token'}
    response = client.get('/api/integrations/gallery/jobs?brand=ramiro&all=1', headers=headers)
    assert response.status_code == 400
    response = client.get('/api/integrations/gallery/jobs?brand=astral&tenant_id=tenant-norkevin-photography&all=1', headers=headers)
    assert response.status_code == 200
    assert response.get_json()['brand'] == 'astral'
    assert 'private@example.com' not in str(response.get_json())


def test_gallery_brand_missing_registry_fails_closed(client, monkeypatch):
    monkeypatch.setenv('GALLERY_INTEGRATION_TOKEN', 'test-gallery-token')
    monkeypatch.setattr(app_module, '_tenant_for_login_email', lambda email: None)
    response = client.get('/api/integrations/gallery/jobs?brand=norkevin&all=1',
                          headers={'Authorization': 'Bearer test-gallery-token'})
    assert response.status_code == 503


def test_active_gallery_browse_prioritizes_deliveries_and_search_keeps_inactive(client, monkeypatch):
    from datetime import timedelta
    today = app_module.datetime.now(app_module.ZoneInfo('America/Guatemala')).date()
    tenant = 'tenant-norkevin-photography'
    def job(identifier, days, status='Confirmado', progress=0):
        return {'id': identifier, 'tenant_id': tenant, 'nombre': identifier,
                'boda_date': (today + timedelta(days=days)).isoformat() if days is not None else '',
                'status': status, 'workflow_progress': progress}
    jobs = [job('far-future', 365), job('completed', -30, 'Listo', 100),
            job('older', -10), job('upcoming', 3), job('recent', -1),
            job('debt', -20, 'Listo', 100), job('archived', 1, 'Archivado'),
            job('cancelled', 0, 'Cancelado'), job('without-date', None)]
    payments = [{'job_id': 'debt', 'tenant_id': tenant, 'amount': 100, 'status': 'Pendiente'},
                {'job_id': 'completed', 'tenant_id': 'tenant-norkevin', 'amount': 99999, 'status': 'Pendiente'}]
    def records(table):
        if table == 'jobs': return jobs
        if table == 'payments': return payments
        return _records(table)
    monkeypatch.setenv('GALLERY_INTEGRATION_TOKEN', 'test-gallery-token')
    monkeypatch.setattr(app_module.store, 'list', records)
    monkeypatch.setattr(app_module.store, '_read_raw', records)
    headers = {'Authorization': 'Bearer test-gallery-token'}
    response = client.get('/api/integrations/gallery/jobs?brand=norkevin&all=1&active=1&limit=3', headers=headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert [j['id'] for j in payload['jobs']] == ['recent', 'older', 'debt']
    assert payload['total'] == 6
    assert payload['hasMore'] is True
    page2 = client.get('/api/integrations/gallery/jobs?brand=norkevin&all=1&active=1&limit=3&offset=3', headers=headers).get_json()
    assert [j['id'] for j in page2['jobs']] == ['upcoming', 'far-future', 'without-date']
    assert page2['hasMore'] is False
    search = client.get('/api/integrations/gallery/jobs?brand=norkevin&q=completed&active=1', headers=headers).get_json()
    assert [j['id'] for j in search['jobs']] == ['completed']
    assert 'amount' not in str(payload)
    assert 'amount' not in str(search)
