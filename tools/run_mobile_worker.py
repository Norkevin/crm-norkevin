"""Explicit mobile worker. Never starts with the CRM or sends any email."""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['ENABLE_WORKFLOW_QUEUE'] = '0'
os.environ['ENABLE_REMINDER_SCHEDULER'] = '0'


def tick(app, store):
    from src.mobile import enabled, journal, source_events
    from src.mobile_push import drain
    if not enabled(app):
        raise RuntimeError('FLOW_MOBILE_ENABLED must be enabled in an approved environment')
    mobile = journal(app, store)
    keys = {}
    for tenant in store.list('tenants'):
        recipient = (tenant.get('login_email') or '').strip().lower()
        if not recipient or tenant.get('active') is False:
            continue
        events = source_events(store, tenant['id'], app.extensions.get('teams'))
        keys[tenant['id']] = {e['key'] for e in events}
        mobile.observe(tenant['id'], recipient, events)
    return drain(mobile, store, keys)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    from wsgi import app, store
    while True:
        try:
            accepted = tick(app, store)
            print(f'Mobile scan complete; provider accepted: {accepted}', flush=True)
        except Exception as exc:
            print(f'Mobile worker failed: {type(exc).__name__}; review configuration', file=sys.stderr, flush=True)
            if args.once:
                sys.exit(1)
        if args.once:
            break
        time.sleep(30)
