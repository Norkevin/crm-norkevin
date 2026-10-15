"""Service supervision uses short local processes, not a server or push provider."""
import os
import shlex
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools import start_web_service as service
from tools import run_web_push_worker as worker


@pytest.mark.parametrize('push_enabled', ['0', '1'])
def test_render_preload_cannot_separate_the_mail_scheduler_from_new_http_leads(monkeypatch, push_enabled):
    monkeypatch.setenv('FLOW_WEB_PUSH_ENABLED', push_enabled)
    monkeypatch.delenv('CRM_DATA_DIR', raising=False)
    monkeypatch.setenv('GUNICORN_CMD_ARGS', '--preload --access-logfile - --bind=0.0.0.0:10000')
    monkeypatch.setenv('WEB_CONCURRENCY', '4')
    launched = []

    class Executed(Exception):
        pass

    def capture(command):
        launched.append(command)
        raise Executed()

    monkeypatch.setattr(service.os, 'execvp', lambda _, command: capture(command))
    monkeypatch.setattr(service, 'supervise', lambda commands: capture(commands[1][1]))
    with pytest.raises(Executed):
        service.main()
    from gunicorn.config import Config
    config = Config()
    options = shlex.split(os.environ['GUNICORN_CMD_ARGS'])
    parsed = config.parser().parse_args(options)
    web = config.parser().parse_args(launched[0][1:])
    assert parsed.preload_app is not True, 'Scheduler must import with the HTTP worker'
    assert web.workers == 1, 'JSON workflows require one shared in-memory engine'
    assert parsed.accesslog == '-' and parsed.bind == ['0.0.0.0:10000']


@pytest.mark.skipif(os.name != 'posix', reason='Gunicorn uses POSIX worker processes')
def test_http_lead_created_after_boot_is_visible_to_the_worker_scheduler(tmp_path):
    """Real fork regression, with an in-memory WSGI app and no mail provider."""
    import socket
    import time
    from pathlib import Path
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    (tmp_path / 'wsgi.py').write_text('''
import threading, time
leads = {}
def process():
    while True:
        for key in list(leads): leads[key] = 'processed'
        time.sleep(0.02)
threading.Thread(target=process, daemon=True).start()
def app(environ, start_response):
    if environ['REQUEST_METHOD'] == 'POST': leads['new'] = 'waiting'
    start_response('200 OK', [('Content-Type', 'text/plain')])
    return [leads.get('new', 'empty').encode()]
''')
    with socket.socket() as available:
        available.bind(('127.0.0.1', 0))
        port = available.getsockname()[1]
    env = dict(os.environ, FLOW_WEB_PUSH_ENABLED='0', PORT=str(port),
               GUNICORN_CMD_ARGS='--preload --access-logfile -',
               PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    env.pop('CRM_DATA_DIR', None)
    bootstrap = f'from tools import start_web_service as s;s.__file__={str(tmp_path / "tools/start_web_service.py")!r};s.main()'
    url = f'http://127.0.0.1:{port}'
    with (tmp_path / 'gunicorn.log').open('w') as log:
        server = subprocess.Popen([sys.executable, '-c', bootstrap], env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 8
            while True:
                try:
                    with urlopen(url, timeout=1) as response:
                        assert response.read() == b'empty'
                    break
                except URLError:
                    assert server.poll() is None and time.monotonic() < deadline, (tmp_path / 'gunicorn.log').read_text()
                    time.sleep(0.05)
            with urlopen(Request(url, data=b'', method='POST'), timeout=1):
                pass
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with urlopen(url, timeout=1) as response:
                    if response.read() == b'processed': break
                time.sleep(0.02)
            else:
                pytest.fail('Scheduler cannot see leads created by the HTTP worker after boot')
        finally:
            server.terminate()
            server.wait(timeout=4)


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
