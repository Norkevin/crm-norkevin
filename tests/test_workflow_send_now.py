from datetime import datetime, timedelta

import pytest

from conftest import login_as_tenant
from src.email_delivery import DeliveryResult
from test_workflow_automatic_delivery import automatic


@pytest.mark.parametrize('tenant', ['tenant-norkevin', 'tenant-norkevin-photography'])
@pytest.mark.parametrize('endpoint', ['trigger-step', 'workflow'])
@pytest.mark.parametrize('already_queued', [False, True])
def test_send_now_delivers_before_due_once(automatic, auth_client, tenant, endpoint, already_queued):
    app, setup, sent = automatic
    lead, instance = setup(tenant, created=datetime.now())
    login_as_tenant(auth_client, tenant)
    url = f"/api/leads/{lead['id']}/trigger-step" if endpoint == 'trigger-step' else '/api/workflow/step'
    payload = dict(lead_id=lead['id'], step_id='auto_step')
    due = instance.trigger_at + timedelta(hours=3)
    if already_queued:
        with app.app.test_request_context('/'):
            token = app._workflow_tenant.set(tenant)
            try:
                queued = app._complete_lead_workflow_step(lead, 'auto_step')
            finally:
                app._workflow_tenant.reset(token)
        assert queued['delivery_status'] == 'pending'
    assert sent == []

    response = auth_client.post(url, json=payload)
    assert response.status_code == 200, response.json
    assert response.json['delivery_status'] == 'sent'
    assert len(sent) == 1
    assert sent[0][1]['metadata']['tenant_id'] == tenant
    assert instance.step_states['auto_step'] == app.StepStatus.DONE
    if already_queued:
        assert response.json['mail_id'] == queued['mail_id']
    assert 'aprobaci' not in response.json.get('message', '')
    auth_client.post(url, json=payload)
    app._prepare_due_workflow_emails(tenant_id=tenant, now=due)
    app._send_due_workflow_emails(tenant_id=tenant, now=due)
    assert len(sent) == 1


@pytest.mark.parametrize('outcome', ['failure', 'uncertain', 'blocked'])
def test_send_now_reports_failure_without_retrying(automatic, auth_client, monkeypatch, outcome):
    app, setup, sent = automatic
    tenant = 'tenant-norkevin-photography'
    lead, instance = setup(tenant, created=datetime.now())
    login_as_tenant(auth_client, tenant)

    def delivery(*args, **kwargs):
        sent.append(args)
        if outcome == 'uncertain':
            raise RuntimeError('Resultado incierto de prueba')
        return DeliveryResult(ok=False, provider='test', status='failed', error='Fallo de prueba')

    monkeypatch.setattr('src.mail_tracker.send_email', delivery)
    if outcome == 'blocked':
        monkeypatch.setattr('src.mail_tracker.commercial_validate', lambda mail: 'Bloqueo de prueba')
    url = f"/api/leads/{lead['id']}/trigger-step"
    first = auth_client.post(url, json={'step_id': 'auto_step'})
    assert first.status_code == 400
    assert first.json['ok'] is False
    assert 'prueba' in first.json['error']
    assert instance.step_states['auto_step'] != app.StepStatus.DONE
    auth_client.post(url, json={'step_id': 'auto_step'})
    app._send_due_workflow_emails(tenant_id=tenant, now=instance.trigger_at + timedelta(hours=4))
    assert len(sent) == (0 if outcome == 'blocked' else 1)


def test_send_now_preserves_tenant_and_skip_guards(automatic, auth_client):
    app, setup, sent = automatic
    lead, instance = setup('tenant-norkevin-photography', created=datetime.now())
    login_as_tenant(auth_client, 'tenant-norkevin')
    url = f"/api/leads/{lead['id']}/trigger-step"
    assert auth_client.post(url, json={'step_id': 'auto_step'}).status_code == 404
    login_as_tenant(auth_client, lead['tenant_id'])
    instance.step_states['auto_step'] = app.StepStatus.SKIPPED
    assert auth_client.post(url, json={'step_id': 'auto_step'}).status_code == 400
    assert sent == []
