import uuid


def test_editor_shows_only_own_saved_packages_and_preserves_package_fields(auth_client):
    import app as crm
    suffix = uuid.uuid4().hex[:8]
    tenant = 'tenant-norkevin'
    package = {'id': 'pkg-' + suffix, 'tenant_id': tenant, 'name': 'Gold <&> ' + suffix,
               'price': 13500, 'duration_hours': 8, 'description': 'Cobertura completa',
               'category': 'Fotografía', 'includes': ['2 fotógrafos', '500 fotos', 'Save the Date']}
    crm.store.upsert('packages', package)
    crm.store.upsert('packages', dict(package, id='other-' + suffix, name='Otra marca ' + suffix, tenant_id='tenant-other'))
    crm.store.upsert('jobs', {'id': 'job-' + suffix, 'tenant_id': tenant, 'nombre': 'Prueba'})
    draft = auth_client.post('/api/quotes/draft', json={'job_id': 'job-' + suffix}).get_json()
    page = auth_client.get(draft['edit_url'])
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'quick-package-search' in html and 'Gold &lt;&amp;&gt;' in html
    assert 'Otra marca ' + suffix not in html
    quote_id = draft['quote_id']
    result = auth_client.post('/api/quotes/' + quote_id + '/options', json={
        'name': package['name'], 'precio_total': package['price'], 'horas': package['duration_hours'],
        'description': package['description'], 'incluye': '\n'.join(package['includes']),
    })
    assert result.get_json()['ok']
    saved = crm.store.get('quotes', quote_id)['options'][0]
    assert saved['incluye'] == package['includes']
    assert saved['horas'] == 8 and saved['precio_total'] == 13500
    assert saved['description'] == package['description']
