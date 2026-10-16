"""Automatic package delivery uses the same tenant checks and never retries failures."""
import pytest

from src.email_delivery import DeliveryResult
from src.mail_tracker import MailTracker

NORKEVIN = 'tenant-norkevin-photography'
ASTRAL = 'tenant-norkevin'
SOURCE = 'workflow:lead-step:envio_paquetes'


@pytest.fixture
def packages(client, monkeypatch):
    import app as crm
    from src import mail_tracker as module
    state = {'enabled': True, 'calls': [], 'outcome': 'sent'}
    for tenant in (NORKEVIN, ASTRAL):
        crm.store.upsert('leads', dict(id='auto-package-' + tenant, tenant_id=tenant,
            nombre='Paquetes de prueba', email='packages@example.invalid'))
    crm.store.upsert('clients', dict(id='foreign-package-client', tenant_id=ASTRAL,
        first_name='Cliente ajeno', email='packages@example.invalid'))
    crm.store.upsert('files', dict(id='foreign-package-file', tenant_id=ASTRAL))
    token = crm._workflow_tenant.set(NORKEVIN)
    monkeypatch.setattr(module, 'commercial_prepare', None)
    monkeypatch.setattr(module, 'commercial_validate', None)
    monkeypatch.setattr(module, 'package_auto_delivery', lambda entry:
        state['enabled'] and entry.get('tenant_id') == NORKEVIN and entry.get('source') == SOURCE)

    def deliver(*args, **kwargs):
        state['calls'].append((args, kwargs))
        if state['outcome'] == 'uncertain':
            raise RuntimeError('Respuesta perdida del proveedor falso')
        return DeliveryResult(ok=state['outcome'] == 'sent', provider='test', mode='test',
            error=None if state['outcome'] == 'sent' else 'Fallo del proveedor falso')
    monkeypatch.setattr(module, 'send_email', deliver)

    def queue(tenant=NORKEVIN, **changes):
        options = dict(tenant_id=tenant, lead_id='auto-package-' + tenant,
                       source=SOURCE, idempotency_key='package-auto:' + tenant)
        options.update(changes)
        body = options.pop('body', 'Información de paquetes de prueba')
        active = crm._workflow_tenant.set(tenant)
        try:
            return MailTracker().queue_email('packages@example.invalid', 'Paquetes', body, **options)
        finally:
            crm._workflow_tenant.reset(active)
    state.update(crm=crm, module=module, queue=queue, tracker=MailTracker())
    yield state
    crm._workflow_tenant.reset(token)


def test_authorized_packages_send_once_with_source_snapshot_and_history(packages, monkeypatch):
    monkeypatch.setattr(packages['module'], 'commercial_prepare', lambda entry:
        {'body': entry['body'], 'travel_snapshot': {'mode': 'packages_without_travel'}})
    entry = packages['queue']()
    assert entry['status'] == 'sent' and len(packages['calls']) == 1
    assert [item['a'] for item in entry['historial']] == ['sending', 'sent']
    assert all(item['actor'] == 'sistema:paquetes-automaticos' for item in entry['historial'])
    logged = packages['crm'].store.get('mail_log', entry['mail_id'])
    assert logged['source'] == SOURCE
    assert logged['travel_snapshot'] == entry['travel_snapshot']
    assert packages['calls'][0][1]['metadata']['tenant_id'] == NORKEVIN
    duplicate = packages['queue']()
    assert duplicate['status'] == 'sent' and len(packages['calls']) == 1
    assert len(packages['crm'].store.list('pending_emails')) == 1


@pytest.mark.parametrize('outcome', ['failed', 'uncertain'])
def test_provider_failure_or_uncertain_result_is_never_retried_automatically(packages, outcome):
    packages['outcome'] = outcome
    entry = packages['queue']()
    assert entry['status'] == 'failed' and entry['attempted_at']
    assert entry['historial'][-1]['a'] == 'failed'
    packages['outcome'] = 'sent'
    duplicate = packages['queue']()
    assert duplicate['id'] == entry['id'] and duplicate['status'] == 'failed'
    assert len(packages['calls']) == 1
    assert len(packages['crm'].store.list('pending_emails')) == 1


def test_disabled_package_auto_delivery_keeps_manual_approval(packages):
    packages['enabled'] = False
    entry = packages['queue']()
    assert entry['status'] == 'pending' and not packages['calls']
    assert not entry.get('package_auto_attempted_at')


def test_astral_and_other_messages_remain_pending(packages):
    assert packages['queue'](tenant=ASTRAL)['status'] == 'pending'
    assert packages['queue'](source='manual:other-message', idempotency_key='other-mail')['status'] == 'pending'
    assert not packages['calls']


def test_matching_existing_pending_can_be_dispatched_after_authorization(packages):
    packages['enabled'] = False
    original = packages['queue']()
    packages['enabled'] = True
    dispatched = packages['queue']()
    assert dispatched['id'] == original['id'] and dispatched['status'] == 'sent'
    assert len(packages['calls']) == 1


def test_changed_existing_pending_preserves_original_and_blocks_stale_content(packages):
    packages['enabled'] = False
    original = packages['queue']()
    packages['enabled'] = True
    changed = packages['queue'](body='Contenido distinto')
    assert changed['id'] == original['id'] and changed['body'] == original['body']
    assert changed['status'] == 'blocked' and changed['package_auto_requires_review']
    assert not packages['calls']
    # Historical warnings also keep stale content blocked.
    changed.pop('package_auto_requires_review')
    packages['crm'].store.upsert('pending_emails', changed)
    assert packages['queue']()['status'] == 'blocked' and not packages['calls']


def test_cancelled_packages_are_not_recreated_or_sent_automatically(packages):
    packages['enabled'] = False
    original = packages['queue']()
    packages['tracker'].discard_pending(original['id'], actor='prueba')
    packages['enabled'] = True
    duplicate = packages['queue']()
    assert duplicate['id'] == original['id'] and duplicate['status'] == 'discarded'
    assert len(packages['crm'].store.list('pending_emails')) == 1
    assert not packages['calls']


def test_preexisting_manual_failure_is_not_automatically_retried(packages):
    packages['enabled'] = False
    original = packages['queue']()
    packages['outcome'] = 'failed'
    result = packages['tracker'].approve_and_send(original['id'], sender_tenant_id=NORKEVIN, actor='prueba')
    assert result['pendiente']['status'] == 'failed'
    packages['enabled'] = True
    packages['outcome'] = 'sent'
    duplicate = packages['queue']()
    assert duplicate['id'] == original['id'] and duplicate['status'] == 'failed'
    assert len(packages['calls']) == 1


@pytest.mark.parametrize('changes', [
    {'lead_id': 'auto-package-' + ASTRAL},
    {'client_id': 'foreign-package-client'},
    {'attachments': [{'file_id': 'foreign-package-file'}]},
])
def test_cross_tenant_links_recipients_and_attachments_block_automatic_packages(packages, changes):
    entry = packages['queue'](**changes)
    assert entry['status'] == 'blocked' and entry['blocked_reason']
    assert not packages['calls']


def test_commercial_validation_still_blocks_automatic_packages(packages, monkeypatch):
    monkeypatch.setattr(packages['module'], 'commercial_validate', lambda entry: 'La fecha está ocupada')
    entry = packages['queue']()
    assert entry['status'] == 'blocked' and 'ocupada' in entry['blocked_reason']
    assert not packages['calls']


def test_failed_policy_callback_does_not_send(packages, monkeypatch):
    def broken(entry):
        raise RuntimeError('Clasificación no disponible')
    monkeypatch.setattr(packages['module'], 'package_auto_delivery', broken)
    entry = packages['queue']()
    assert entry['status'] == 'pending' and not packages['calls']
