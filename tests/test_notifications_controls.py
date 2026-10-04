import uuid
from conftest import login_as_tenant


def test_notifications_history_read_state_and_tenant_isolation(client):
    import app as m
    tenant = 'tenant-norkevin-photography'
    login_as_tenant(client, tenant)
    prefix = uuid.uuid4().hex
    for i in range(7):
        m.store.upsert('leads', {'id': f'{prefix}-{i}', 'tenant_id': tenant,
                                'nombre': f'Notificación {i}', 'status': 'Nuevo',
                                'created': f'2099-01-0{i+1}'})
    lead_id = f'{prefix}-6'
    m.store.upsert('mail_log', {'id': prefix, 'tenant_id': tenant, 'lead_id': lead_id,
                              'subject': 'Falló el correo', 'status': 'failed',
                              'sent_at': '2099-02-01T12:00:00'})
    data = client.get('/api/notifications/recent').get_json()
    ids = {n['id'] for n in data['notifications']}
    assert all(f'lead-{prefix}-{i}' in ids for i in range(7))
    notice = next(n for n in data['notifications'] if n['id'] == f'mail-{prefix}')
    assert notice['alert'] and not notice['read']
    assert client.post('/api/notifications/read', json={'ids': [notice['id']]}).status_code == 200
    assert next(n for n in client.get('/api/notifications/recent').get_json()['notifications'] if n['id'] == notice['id'])['read']
    assert client.get('/notifications').status_code == 200
    assert b'notif-history-list' in client.get('/notifications').data
    assert client.post('/api/notifications/read', json={'ids': 'invalid'}).status_code == 400
    login_as_tenant(client, 'tenant-astral')
    assert notice['id'] not in {n['id'] for n in client.get('/api/notifications/recent').get_json()['notifications']}
    assert client.post('/api/notifications/read', json={'ids': [notice['id']]}).status_code == 404
