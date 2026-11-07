"""Sales follow-ups must recheck the event date immediately before delivery."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4

import pytest


@pytest.fixture
def mail_context(auth_client, monkeypatch):
    import app as a
    import src.mail_tracker as mail
    from src.email_delivery import DeliveryResult

    tenant = 'tenant-norkevin'
    token = a._workflow_tenant.set(tenant)
    calls = []

    def deliver(*args, **kwargs):
        calls.append((args, kwargs))
        return DeliveryResult(ok=True, provider='test', message_id='synthetic', mode='test')

    monkeypatch.setattr(mail, 'send_email', deliver)
    monkeypatch.setattr(mail, 'commercial_prepare', None)
    monkeypatch.setattr(mail, 'commercial_validate', None)
    monkeypatch.setattr(mail, 'package_auto_delivery', None)
    monkeypatch.setattr(mail, 'requested_auto_delivery', lambda entry: True)
    lead = dict(id='past-date-' + uuid4().hex[:8], tenant_id=tenant,
                email='synthetic@example.invalid', nombre='Prueba sintética',
                fecha_tentativa=(datetime.now(ZoneInfo('America/Guatemala')).date()
                                 + timedelta(days=30)).isoformat(), status='Nuevo')
    a.store.upsert('leads', lead)
    yield a, mail, tenant, lead, calls
    a._workflow_tenant.reset(token)


@pytest.mark.parametrize('event_date', ['2026-10-10', '2026-10-18'])
def test_reported_past_dates_are_blocked(mail_context, event_date):
    a, mail, tenant, lead, _ = mail_context
    lead['fecha_tentativa'] = event_date
    a.store.upsert('leads', lead)
    reason = mail.check_lead_event_date(dict(tenant_id=tenant, lead_id=lead['id']),
                                      now=datetime.fromisoformat('2026-11-07T02:00:00-06:00'))
    assert 'ya pasó' in reason


def test_calendar_date_uses_guatemala_at_utc_midnight(mail_context):
    a, mail, tenant, lead, _ = mail_context
    lead['fecha_tentativa'] = '2026-11-07'
    a.store.upsert('leads', lead)
    entry = dict(tenant_id=tenant, lead_id=lead['id'])
    assert mail.check_lead_event_date(entry, datetime.fromisoformat('2026-11-08T01:00:00+00:00')) is None
    assert 'ya pasó' in mail.check_lead_event_date(entry, datetime.fromisoformat('2026-11-08T06:00:00+00:00'))


def test_auto_and_direct_delivery_both_block_past_leads(mail_context):
    a, mail, tenant, lead, calls = mail_context
    lead['fecha_tentativa'] = '2000-01-01'
    a.store.upsert('leads', lead)
    tracker = mail.get_tracker()
    for send in (tracker.queue_email, tracker.log_email):
        result = send(lead['email'], 'Se liberó tu fecha', 'Oferta de captación',
                      lead_id=lead['id'], tenant_id=tenant,
                      source='workflow:lead-step:seguimiento_final')
        assert result['status'] == 'blocked'
        assert 'ya pasó' in result['blocked_reason']
    assert calls == []


def test_queued_mail_is_revalidated_after_date_change(mail_context):
    a, mail, tenant, lead, calls = mail_context
    tracker = mail.get_tracker()
    entry = tracker.queue_email(lead['email'], 'Seguimiento', 'Oferta',
        tenant_id=tenant, lead_id=lead['id'], defer_delivery=True)
    assert entry['status'] == 'pending'
    lead['fecha_tentativa'] = '2000-01-01'
    a.store.upsert('leads', lead)
    result = tracker.approve_and_send(entry['id'], sender_tenant_id=tenant)
    assert not result['ok'] and result['pendiente']['status'] == 'blocked'
    assert calls == []


def test_future_lead_still_sends(mail_context):
    _, mail, tenant, lead, calls = mail_context
    entry = mail.get_tracker().queue_email(lead['email'], 'Paquetes', 'Información',
        tenant_id=tenant, lead_id=lead['id'])
    assert entry['status'] == 'sent' and len(calls) == 1


def test_past_inquiry_still_notifies_owner(mail_context):
    a, mail, tenant, lead, calls = mail_context
    lead['fecha_tentativa'] = '2000-01-01'
    a.store.upsert('leads', lead)
    a.store.save_tenant_dict('settings', {'company': {'email': 'owner@example.invalid'}}, tenant)
    result = mail.get_tracker().send_new_lead_notification(
        to_email='owner@example.invalid', subject='Nuevo lead', body='Revisar fecha pasada',
        lead_id=lead['id'], tenant_id=tenant)
    assert result['status'] == 'sent' and len(calls) == 1


def test_post_event_invoice_or_gallery_for_job_still_sends(mail_context):
    a, mail, tenant, lead, calls = mail_context
    lead['fecha_tentativa'] = '2000-01-01'
    a.store.upsert('leads', lead)
    job = dict(id='job-' + lead['id'], tenant_id=tenant, boda_date='2000-01-01')
    a.store.upsert('jobs', job)
    entry = mail.get_tracker().queue_email(lead['email'], 'Factura del trabajo', 'Saldo pendiente',
        tenant_id=tenant, lead_id=lead['id'], job_id=job['id'], source='invoice')
    assert entry['status'] == 'sent' and len(calls) == 1


def test_invalid_date_is_not_silently_treated_as_future(mail_context):
    a, mail, tenant, lead, calls = mail_context
    lead['fecha_tentativa'] = '2026-99-10'
    a.store.upsert('leads', lead)
    entry = mail.get_tracker().queue_email(lead['email'], 'Seguimiento', 'Oferta',
        tenant_id=tenant, lead_id=lead['id'])
    assert entry['status'] == 'blocked' and 'no es válida' in entry['blocked_reason']
    assert calls == []
