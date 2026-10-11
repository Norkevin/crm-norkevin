"""Service supervision uses short local processes, not a server or push provider."""
import os
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools import start_web_service as service
from tools import run_web_push_worker as worker


def test_worker_failure_keeps_web_alive_until_web_exits(capsys, tmp_path):
    failing = [sys.executable, '-c', 'import sys;sys.exit(7)']
    marker = tmp_path / 'web-still-online'
    web = [sys.executable, '-c', 'import time;from pathlib import Path;time.sleep(.5);Path(' + repr(str(marker)) + ').write_text("online")']
    assert service.supervise([('test worker', failing), ('test web', web)]) == 0
    assert marker.read_text() == 'online'
    assert 'web stays online' in capsys.readouterr().err


def test_sigterm_is_forwarded_and_children_exit_cleanly():
    script = '''
import sys
from tools.start_web_service import supervise
command=[sys.executable,'-c','import time;print("ready",flush=True);time.sleep(60)']
sys.exit(supervise([('worker',command),('web',command)]))
'''
    process = subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        assert process.stdout.readline().strip() == 'ready'
        process.send_signal(signal.SIGTERM)
        result = process.wait(timeout=5)
        assert result == 0, process.stderr.read()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def test_disabled_wrapper_records_pause_then_execs_without_changing_web_scheduler_flags(monkeypatch):
    calls = []
    monkeypatch.setenv('FLOW_WEB_PUSH_ENABLED', '0')
    monkeypatch.setenv('CRM_DATA_DIR', '/tmp/fictional-not-used')
    monkeypatch.setenv('ENABLE_WORKFLOW_QUEUE', '1')
    monkeypatch.setenv('ENABLE_REMINDER_SCHEDULER', '1')
    monkeypatch.setattr(service.subprocess, 'run', lambda command, **kwargs: calls.append((command, kwargs)))
    class Executed(Exception):
        pass
    def fake_exec(name, command):
        calls.append((name, command))
        raise Executed()
    monkeypatch.setattr(service.os, 'execvp', fake_exec)
    monkeypatch.setattr(service, 'supervise', lambda *args: pytest.fail('Disabled push must not start a daemon'))
    with pytest.raises(Executed):
        service.main()
    assert calls[0][0][-1] == '--pause' and calls[0][1] == {'check': True}
    assert calls[1][0] == 'gunicorn'
    assert os.environ['ENABLE_WORKFLOW_QUEUE'] == os.environ['ENABLE_REMINDER_SCHEDULER'] == '1'


def test_pause_is_one_shot_without_app_teams_calendar_or_private_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'argv', ['run_web_push_worker.py', '--pause'])
    monkeypatch.setenv('CRM_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(worker, 'load_app', lambda: pytest.fail('Pause must not import CRM or Calendar'))
    assert worker.main() == 0
    assert (tmp_path / 'webpush/webpush.sqlite3').is_file()
    assert not (tmp_path / 'webpush/vapid.pem').exists()
    assert not (tmp_path / 'webpush/subscription.key').exists()


def test_pause_failure_never_prevents_web_start_and_logs_no_command_secrets(monkeypatch, capsys):
    monkeypatch.setenv('FLOW_WEB_PUSH_ENABLED', '0')
    monkeypatch.setenv('CRM_DATA_DIR', '/tmp/fictional-not-used')
    def broken(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ['SECRET-fixture-command'])
    monkeypatch.setattr(service.subprocess, 'run', broken)
    class Executed(Exception):
        pass
    monkeypatch.setattr(service.os, 'execvp', lambda *args: (_ for _ in ()).throw(Executed()))
    with pytest.raises(Executed):
        service.main()
    text = capsys.readouterr().err
    assert 'sending stays disabled' in text and 'SECRET' not in text


def test_worker_restarts_with_bounded_backoff_without_leaking_exception_text(monkeypatch, capsys):
    clock, attempts = [0.0], []
    monkeypatch.setattr(service, 'time', SimpleNamespace(
        monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)))
    monkeypatch.setattr(service.os, 'killpg', lambda *args: None)
    class Web:
        pid = 987654
        def poll(self):
            return 0 if clock[0] >= 1000 else None
        def wait(self):
            return 0
    def popen(command, **kwargs):
        if command == ['fake-web']:
            return Web()
        attempts.append(clock[0])
        raise OSError('SECRET-fixture-endpoint')
    monkeypatch.setattr(service.subprocess, 'Popen', popen)
    assert service.supervise([('worker', ['fake-worker']), ('web', ['fake-web'])]) == 0
    delays = [new - old for old, new in zip(attempts, attempts[1:])]
    assert len(attempts) >= 5 and 30 <= min(delays) <= max(delays) <= 300.25
    text = capsys.readouterr().err
    assert 'web stays online' in text and 'OSError' in text and 'SECRET' not in text
