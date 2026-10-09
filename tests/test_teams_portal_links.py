import hashlib
from urllib.parse import urlsplit

import pytest

from test_flow_teams import web, member, assignment, publish, records, run
from src.teams_calendar import calendar_portal_code


@pytest.fixture
def invitation(web):
    app, owner, storage = web
    store = app.extensions['teams']
    person = member(store)
    coverage = publish(store, assignment(store, person))
    with store.transaction() as db:
        code = calendar_portal_code(store, db, app.secret_key, 'brand-a', person, coverage)
    return app, owner, storage, store, person, coverage, code


def test_short_calendar_link_enters_own_portal_and_lists_only_own_weddings(invitation):
    app, owner, storage, store, person, coverage, code = invitation
    publish(store, assignment(store, person, job='job-2'))
    other = run(store, 'member', name='Persona ajena', role='Video')
    publish(store, assignment(store, other, slot='Video'))
    visitor = app.test_client()
    for _ in range(2):
        response = visitor.get('/p/' + code)
        assert response.status_code == 302 and response.location == '/teams-portal/?job_id=job-1'
        assert response.headers['Referrer-Policy'] == 'no-referrer'
        assert 'no-store' in response.headers['Cache-Control']
    with visitor.session_transaction() as session:
        assert session['teams_member_id'] == person['id']
        assert not session.get('logged_in') and not session['teams_member_preview']
        csrf = session['teams_portal_csrf']
    summary = visitor.get('/teams-portal/summary').get_json()
    assert summary['member']['id'] == person['id'] and 'Persona ajena' not in str(summary)
    assert {j['id'] for j in summary['wedding_options']} == {'job-1', 'job-2'}
    assert visitor.get(response.location).status_code == 200
    assert visitor.get('/teams/jobs/job-1').status_code == 404
    assert visitor.post('/teams-portal/command', json={'action': 'payment', 'key': 'deny'},
                        headers={'X-Teams-CSRF': csrf}).status_code == 403
    row = records(store, 'calendar_access')[0]
    assert row['id'] == hashlib.sha256(code.encode()).hexdigest() and code not in str(row)
    assert len(code) == 22


@pytest.mark.parametrize('invalid', ['expired', 'revoked', 'inactive', 'email', 'reassigned', 'cancelled', 'job', 'deleted'])
def test_short_calendar_link_keeps_all_authorization_checks(invitation, invalid):
    app, owner, storage, store, person, coverage, code = invitation
    with store.transaction() as db:
        if invalid == 'expired':
            access = store.records(db, 'brand-a', 'calendar_access')[0]
            access['expires'] = 0
            store.save(db, 'brand-a', 'calendar_access', access)
        if invalid in ('revoked', 'inactive', 'email'):
            person.update({'access_version': 2} if invalid == 'revoked' else {'active': False} if invalid == 'inactive' else {'email': 'changed@example.invalid'})
            store.save(db, 'brand-a', 'member', person)
        if invalid in ('reassigned', 'cancelled'):
            coverage.update({'member_id': 'other'} if invalid == 'reassigned' else {'status': 'cancelada'})
            store.save(db, 'brand-a', 'assignment', coverage)
        if invalid == 'deleted':
            db.execute("DELETE FROM entities WHERE kind='member' AND id=?", (person['id'],))
    if invalid == 'job':
        from flask import session
        with app.test_request_context('/'):
            session['tenant_id'] = 'brand-a'
            job = storage.get('jobs', 'job-1')
            storage.upsert('jobs', dict(job, status='Cancelado'))
    visitor = app.test_client()
    response = visitor.get('/p/' + code)
    assert response.status_code == 200 and 'Enlace inválido o caducado' in response.get_data(as_text=True)
    with visitor.session_transaction() as session:
        assert not session.get('teams_member_id')


def test_short_entry_does_not_skip_feature_gate_or_expose_unknown_member(invitation):
    app, owner, storage, store, person, coverage, code = invitation
    visitor = app.test_client()
    app.config['FLOW_TEAMS_LOCAL'] = False
    assert visitor.get('/p/' + code).status_code == 404
    app.config['FLOW_TEAMS_ENABLED'] = True
    assert visitor.get('/p/' + 'X' * 22).status_code == 200
    assert visitor.get('/p/' + 'short').status_code == 404
    assert visitor.get('/p/' + code).status_code == 302


def test_short_personal_link_auto_post_retains_single_use_and_same_browser_reentry(web):
    app, owner, storage = web
    person = member(app.extensions['teams'])
    issued = owner.post('/api/teams/access', json={'member_id': person['id']},
                        headers={'X-Teams-CSRF': 'csrf'}).get_json()
    path = urlsplit(issued['login_url']).path
    assert path == '/p/' + issued['code'] and len(issued['code']) == 22
    visitor = app.test_client()
    page = visitor.get(path)
    assert b'data-auto-login="true"' in page.data
    with visitor.session_transaction() as session:
        csrf = session['teams_login_csrf']
    assert visitor.post('/teams-portal/login', data={'code': issued['code'], 'csrf': csrf}).status_code == 302
    assert visitor.get(path).status_code == 302
    another = app.test_client()
    assert 'Enlace inválido o caducado' in another.get(path).get_data(as_text=True)
    with another.session_transaction() as session:
        assert not session.get('teams_member_id')


def test_portal_login_uses_own_csrf_even_when_browser_has_owner_session(invitation):
    import app as crm
    from src.teams_calendar import portal_token
    app, owner, storage, store, person, coverage, code = invitation
    app.before_request(crm._reject_cross_site_owner_changes)
    owner.get('/teams-portal/login')
    with owner.session_transaction() as session:
        csrf = session['teams_login_csrf']
    legacy = portal_token(app.secret_key, 'brand-a', person, coverage)
    # Sandboxed mail/browser views can omit a normal Origin. Member CSRF remains mandatory.
    assert owner.post('/teams-portal/login', data={'csrf': csrf, 'code': legacy}, headers={'Origin': 'null'}).status_code == 302
    assert owner.post('/teams-portal/login', data={'csrf': 'wrong', 'code': legacy}, headers={'Origin': 'null'}).status_code == 403
    response = owner.post('/api/teams/command', json={'action': 'member'},
                          headers={'Origin': 'null', 'X-Teams-CSRF': 'csrf'})
    assert response.status_code == 403 and 'otro sitio' in response.get_json()['error']
