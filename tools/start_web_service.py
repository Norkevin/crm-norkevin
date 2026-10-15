"""Run Web Push on the CRM's existing disk; keep worker failures visible."""
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path


def supervise(commands):
    children = {}
    received = None

    def forward(number, frame=None):
        nonlocal received
        received = received or number
        for child in list(children.values()):
            try:
                os.killpg(child.pid, number)
            except PermissionError:
                try:
                    child.send_signal(number)
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass

    previous = {number: signal.signal(number, forward) for number in (signal.SIGTERM, signal.SIGINT)}
    result = 0
    worker_name, worker_command = commands[0]
    web_name, web_command = commands[1]
    retry_at, delay, started = 0, 30, 0
    try:
        children[web_name] = subprocess.Popen(web_command, start_new_session=True)
        while not received:
            status = children[web_name].poll()
            if status is not None and not received:
                print(f'{web_name} exited with status {status}; stopping the service.', file=sys.stderr, flush=True)
                result = status if status >= 0 else 128 - status
                break
            child = children.get(worker_name)
            now = time.monotonic()
            if child is not None and child.poll() is not None:
                print(f'{worker_name} exited with status {child.returncode}; web stays online. Retrying in {delay}s.', file=sys.stderr, flush=True)
                children.pop(worker_name)
                retry_at = now + delay
                delay = min(delay * 2, 300)
            elif child is not None and now - started >= 60:
                delay = 30
            if worker_name not in children and now >= retry_at and not received:
                try:
                    children[worker_name] = subprocess.Popen(worker_command, start_new_session=True)
                    started = now
                except Exception as exc:
                    print(f'{worker_name} start failed: {type(exc).__name__}; web stays online. Retrying in {delay}s.', file=sys.stderr, flush=True)
                    retry_at = now + delay
                    delay = min(delay * 2, 300)
            if not received:
                time.sleep(0.25)
    finally:
        forward(signal.SIGTERM)
        deadline = time.monotonic() + 10
        while any(child.poll() is None for child in children.values()) and time.monotonic() < deadline:
            time.sleep(0.05)
        for child in children.values():
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except PermissionError:
                    child.kill()
                except ProcessLookupError:
                    pass
            child.wait()
        for number, handler in previous.items():
            signal.signal(number, handler)
    return result


def main():
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    # Import inside the web worker: preloaded scheduler threads see only the
    # master's startup snapshot, never the leads created by HTTP requests.
    options = shlex.split(os.environ.get('GUNICORN_CMD_ARGS', ''))
    os.environ['GUNICORN_CMD_ARGS'] = shlex.join(arg for arg in options if arg != '--preload')
    gunicorn = ['gunicorn', '--workers', '1', '--bind', '0.0.0.0:' + os.environ.get('PORT', '5000'), 'wsgi:app']
    worker = [sys.executable, str(root / 'tools/run_web_push_worker.py')]
    if os.environ.get('FLOW_WEB_PUSH_ENABLED') != '1':
        # Run pause in a separate process so its scheduler flags cannot disable
        # existing web workflows when this process execs Gunicorn.
        if os.environ.get('CRM_DATA_DIR'):
            try:
                subprocess.run(worker + ['--pause'], check=True)
            except Exception as exc:
                print(f'Web Push pause failed: {type(exc).__name__}; sending stays disabled and web starts normally.', file=sys.stderr, flush=True)
        os.execvp(gunicorn[0], gunicorn)
    return supervise([('Web Push worker', worker), ('Gunicorn', gunicorn)])


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(f'Web service startup failed: {type(exc).__name__}.', file=sys.stderr, flush=True)
        sys.exit(1)
