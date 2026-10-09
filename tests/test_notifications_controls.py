import uuid
import copy
import pytest
from conftest import login_as_tenant


@pytest.fixture(autouse=True)
def restore_notification_data():
    import app as m
    snapshots = {table: copy.deepcopy(m.store._read_raw(table))
                 for table in ('tenants', 'leads', 'jobs', 'mail_log', 'notification_reads', 'pending_emails')}
    yield
    for table, rows in snapshots.items():
        m.store._save(table, rows)


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


def test_mail_notification_uses_existing_job_when_lead_was_deleted(client):
    import app as m
    tenant = 'tenant-norkevin-photography'
    login_as_tenant(client, tenant)
    unique = uuid.uuid4().hex
    m.store.upsert('jobs', {'id': unique, 'tenant_id': tenant, 'nombre': 'Trabajo de prueba'})
    m.store.upsert('mail_log', {'id': unique, 'tenant_id': tenant, 'lead_id': 'deleted-' + unique,
                              'job_id': unique, 'status': 'sent', 'sent_at': '2099-02-01'})
    notification = next(n for n in client.get('/api/notifications/recent').get_json()['notifications']
                        if n['id'] == 'mail-' + unique)
    assert notification['url'] == '/jobs/' + unique


def test_notifications_sort_full_activity_time_across_types(client):
    import app as m
    tenant = 'tenant-norkevin-photography'
    login_as_tenant(client, tenant)
    prefix = uuid.uuid4().hex
    lead_id = prefix + '-lead'
    m.store.upsert('leads', dict(id=lead_id, tenant_id=tenant, nombre='Orden QA', status='Nuevo',
                               created='2099-01-09', created_time='2099-01-09T10:00:00-06:00'))
    for name, fields in [('old', dict(sent_at='2099-01-09T15:00:00Z')),
                         ('new', dict(sent_at='2099-01-09T17:00:00')),
                         ('opened', dict(sent_at='2099-01-09T14:00:00', opened_at='2099-01-09T18:00:00Z')),
                         ('failed', dict(status='failed', attempted_at='2099-01-09T19:00:00Z'))]:
        m.store.upsert('mail_log', dict(id=prefix+'-'+name, tenant_id=tenant, lead_id=lead_id, **fields))
    data = client.get('/api/notifications/recent').get_json()['notifications']
    own = [n for n in data if prefix in n['id']]
    assert [n['id'] for n in own] == ['mail-'+prefix+'-failed', 'mail-'+prefix+'-opened', 'mail-'+prefix+'-new', 'lead-'+lead_id, 'mail-'+prefix+'-old']
    assert own[0]['time'] == '13:00'
    assert all(n['date'] == '2099-01-09' for n in own)
    assert m._build_recent_notifications(tenant, limit=5) == data[:5]


def test_new_lead_keeps_arrival_time_when_edited():
    import app as m
    from datetime import date
    lead = dict(id=uuid.uuid4().hex, tenant_id='tenant-norkevin-photography', created=date.today().isoformat())
    m.upsert_lead(lead)
    original = m.store.get('leads', lead['id'])['created_time']
    m.upsert_lead(dict(lead, created_time='', nombre='Editado'))
    assert m.store.get('leads', lead['id'])['created_time'] == original
    historical = dict(lead, id=uuid.uuid4().hex, created='2020-01-01', created_time='')
    m.upsert_lead(historical)
    assert not m.store.get('leads', historical['id']).get('created_time')


def test_legacy_lead_arrival_uses_original_internal_notice_time(client):
    import app as m
    tenant = 'tenant-norkevin-photography'
    login_as_tenant(client, tenant)
    identifier = uuid.uuid4().hex
    m.store.upsert('leads', dict(id=identifier, tenant_id=tenant, status='Nuevo', created='2099-01-09'))
    m.store.upsert('pending_emails', dict(id=identifier, tenant_id=tenant, lead_id=identifier,
                                         source='auto:new-lead-notify', created_at='2099-01-09T01:03:00'))
    item = next(n for n in client.get('/api/notifications/recent').get_json()['notifications'] if n['id'] == 'lead-'+identifier)
    assert item['date'] == '2099-01-08' and item['time'] == '19:03'
