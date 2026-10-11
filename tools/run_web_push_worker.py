"""Read existing CRM activity; never import wsgi or start Calendar/mail schedulers."""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['ENABLE_WORKFLOW_QUEUE'] = '0'
os.environ['ENABLE_REMINDER_SCHEDULER'] = '0'


def load_app():
    from app import app, store
    from src.teams import TeamsStore
    path = Path(store.data_dir) / 'teams.sqlite3'
    if os.environ.get('FLOW_TEAMS_ENABLED', '1') == '1' and path.is_file():
        app.extensions['teams'] = TeamsStore(path)
    return app, store


def sources(app, store):
    from flask import g
    from app import _workflow_tenant
    from src.linked_coverages import resolve_job
    from src.web_push import safe_target
    reports = []
    for tenant in store.list('tenants'):
        recipient = (tenant.get('login_email') or '').strip().lower()
        if not recipient or tenant.get('active') is False:
            continue
        context = _workflow_tenant.set(tenant['id'])
        try:
            with app.test_request_context('/_internal/web-push-source'):
                g.mobile_identity = dict(tenant_id=tenant['id'], recipient=recipient)
                events = []
                for notice in app.extensions['web_push_source'](tenant['id'], limit=None, strict=True):
                    target = safe_target(notice.get('url'))
                    if target != '/notifications':
                        kind, identifier = target.rsplit('/', 1)
                        record = store.get('leads', identifier) if kind == '/leads' else resolve_job(store, identifier)
                        if not record or record.get('tenant_id') not in (None, tenant['id']):
                            continue
                    events.append(dict(notice, url=target))
                reports.append((tenant['id'], recipient, events))
        finally:
            _workflow_tenant.reset(context)
    return reports


def tick(app, store, *, sender=None):
    from src.web_push import enabled, journal, drain
    if not enabled(app):
        journal(app, store).pause()
        return 0
    journal(app, store).material()
    reports = sources(app, store)
    journal(app, store).observe(reports, sending=True)
    return drain(app, store, reports, **({'sender': sender} if sender else {}))


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--once', action='store_true')
    group.add_argument('--pause', action='store_true')
    args = parser.parse_args()
    try:
        if args.pause:
            # No Flask/Teams imports and no keys are needed to record a pause.
            from src.web_push import WebPushJournal
            directory = os.environ.get('CRM_DATA_DIR')
            if not directory:
                raise ValueError('CRM_DATA_DIR is required for a durable push pause')
            WebPushJournal(directory).pause()
            print('Web Push paused; pending deliveries cancelled.', flush=True)
            return 0
        app, store = load_app()
        while True:
            accepted = tick(app, store)
            print(f'Web Push scan complete; provider accepted: {accepted}', flush=True)
            if args.once:
                return 0
            time.sleep(30)
    except Exception as exc:
        print(f'Web Push worker failed: {type(exc).__name__}; review configuration.', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
