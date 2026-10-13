"""Synthetic, reproducible work budget; never reads production data."""
from collections import Counter
from time import perf_counter
from conftest import login_as_tenant


def test_notification_work_is_bounded(client, monkeypatch):
    import app as crm
    tenant = 'tenant-norkevin-photography'
    login_as_tenant(client, tenant)
    crm.store._save('leads', [dict(id=f'bench-{i}', tenant_id=tenant, status='Nuevo',
        nombre='QA', created='2026-01-01', fecha_tentativa='2027-01-01') for i in range(100)])
    crm.store._save('jobs', [dict(id=f'job-{i}', tenant_id=tenant, nombre='QA',
        status='Confirmado', boda_date='2028-01-01') for i in range(200)])
    crm.store._save('mail_log', [dict(id=f'mail-{i}', tenant_id=tenant, lead_id=f'bench-{i % 100}',
        subject='QA', status='failed' if i % 100 == 0 else 'sent', body='x' * 1000,
        sent_at='2026-01-02T12:00:00') for i in range(600)])
    original = crm.store.list
    calls = Counter()
    def counted(table, **kwargs):
        calls[table] += 1
        return original(table, **kwargs)
    monkeypatch.setattr(crm.store, 'list', counted)
    start = perf_counter()
    response = client.get('/api/notifications/recent')
    elapsed = perf_counter() - start
    assert response.status_code == 200
    print(f'BENCH notifications: {elapsed:.4f}s; table reads={sum(calls.values())}; '
          f'bytes={len(response.data)}; notices={len(response.json["notifications"])}; {dict(calls)}')
    assert sum(calls.values()) <= 20
    calls.clear()
    limited = client.get('/api/notifications/recent?limit=5')
    assert limited.json['count'] == 106 and len(limited.json['notifications']) == 5
    assert sum(calls.values()) <= 20 and len(limited.data) < 1600
    print(f'BENCH bell payload: {len(limited.data)} bytes; table reads={sum(calls.values())}; count={limited.json["count"]}')
