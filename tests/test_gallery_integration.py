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
