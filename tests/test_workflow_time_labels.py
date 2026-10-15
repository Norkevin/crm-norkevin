import os
import time
from types import SimpleNamespace
import pytest


@pytest.mark.parametrize('server_zone,stored', [('UTC','2026-10-01T19:43:00'),('America/Guatemala','2026-10-01T13:43:00')])
def test_schedule_displays_guatemala_time_on_either_server_clock(monkeypatch, server_zone, stored):
    import app as a
    previous = os.environ.get('TZ')
    try:
        os.environ['TZ'] = server_zone
        time.tzset()
        step = {'scheduled':stored, 'status':'pending', 'action_type':'send_email', 'email_template_id':'test'}
        monkeypatch.setattr(a, '_get_email_template', lambda _: {'cuerpo':'Hola'})
        instance = SimpleNamespace(auto_prepare=True,status=a.WorkflowStatus.ACTIVE)
        result = a._workflow_time_labels([step], instance)[0]
        assert result['scheduled_display'] == 'jueves, 1 de octubre de 2026, 1:43 p. m.'
        assert result['scheduled_epoch'] == 1790883780000
        assert result['auto_prepare'] is True
    finally:
        if previous is None: os.environ.pop('TZ', None)
        else: os.environ['TZ'] = previous
        time.tzset()


@pytest.mark.parametrize('status,enabled,template', [('queued',True,True),('done',True,True),('skipped',True,True),('pending',False,True),('pending',True,False)])
def test_only_automatic_pending_mail_shows_countdown(monkeypatch, status, enabled, template):
    import app as a
    monkeypatch.setattr(a, '_get_email_template', lambda _: {'cuerpo':'Hola'} if template else None)
    step = {'scheduled':'2026-10-01T19:43:00','status':status,'action_type':'send_email'}
    instance = SimpleNamespace(auto_prepare=enabled,status=a.WorkflowStatus.ACTIVE)
    assert not a._workflow_time_labels([step],instance)[0]['auto_prepare']


@pytest.mark.parametrize('automatic', [True, False])
@pytest.mark.parametrize('status', ['pending', 'queued'])
def test_workflow_timing_only_requires_approval_in_manual_mode(flask_app, automatic, status):
    step = dict(status=status, auto_prepare=status == 'pending', scheduled_epoch=1792025220000)
    html = flask_app.jinja_env.get_template('workflow_timing.html').render(
        step=step, workflow_auto_mail=automatic)
    if automatic:
        assert 'aprobar el envío' not in html and 'aprueba el envío' not in html
        assert 'automáticamente' in html
    else:
        assert 'aprueba el envío' in html or 'aprobar el envío' in html


@pytest.mark.parametrize('server_zone', ['UTC', 'America/Guatemala'])
def test_calendar_based_deadline_displays_its_actual_local_hour(monkeypatch, server_zone):
    import app as a
    previous = os.environ.get('TZ')
    try:
        os.environ['TZ'] = server_zone
        time.tzset()
        step = dict(scheduled='2026-10-18T14:05:00', schedule_basis='calendar', status='queued')
        result = a._workflow_time_labels([step], None)[0]
        assert result['scheduled_display'] == 'domingo, 18 de octubre de 2026, 2:05 p. m.'
        assert result['scheduled_epoch'] == 1792353900000
    finally:
        if previous is None:os.environ.pop('TZ', None)
        else:os.environ['TZ'] = previous
        time.tzset()
