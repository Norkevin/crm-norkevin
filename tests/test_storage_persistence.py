import json
import os


def test_json_store_creates_backup_before_overwrite(_isolated_environment):
    from src.storage import JsonStore

    store = JsonStore(_isolated_environment)
    table = 'backup_probe'
    store.upsert(table, {'id': 'record-test', 'nombre': 'Antes'})
    store.upsert(table, {'id': 'record-test', 'nombre': 'Despues'})

    backup_root = os.path.join(_isolated_environment, 'backups')
    backups = []
    for root, _dirs, files in os.walk(backup_root):
        backups.extend(os.path.join(root, f) for f in files if f.startswith(f'{table}_'))

    assert backups, 'cada overwrite de datos reales debe dejar un backup recuperable'
    backup_payload = json.load(open(backups[-1], encoding='utf-8'))
    assert backup_payload == [{'id': 'record-test', 'nombre': 'Antes'}]


def test_storage_status_endpoint_reports_counts(auth_client):
    import app as app_module

    app_module.store.upsert('clients', {'id': 'client-test', 'nombre': 'Cliente Test'})
    app_module.store.upsert('jobs', {'id': 'job-test', 'nombre': 'Job Test'})

    resp = auth_client.get('/api/storage/status')

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['ok'] is True
    assert payload['counts']['clients'] >= 1
    assert payload['counts']['jobs'] >= 1
    assert payload['storage']['uses_env_data_dir'] is True
