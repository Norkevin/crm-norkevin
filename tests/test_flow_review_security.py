"""Read-only/mocked regressions for the October 2026 security and navigation audit."""
import json
import time
import uuid

import pytest


def test_owner_changes_reject_foreign_origins_and_allow_same_site(auth_client):
    for headers in ({'Origin': 'https://evil.example'}, {'Origin':'null'},
                    {'Origin':'https://['}, {'Referer':'https://evil.example/form'},
                    {'Sec-Fetch-Site':'cross-site'}):
        response = auth_client.post('/api/notifications/read', json={'ids':[]}, headers=headers)
        assert response.status_code == 403
    assert auth_client.post('/api/notifications/read', json={'ids':[]}, headers={'Origin':'http://localhost'}).status_code == 200


@pytest.mark.parametrize('path', ['//evil.example', '/\\evil.example', 'https://evil.example', '/\nevil.example'])
def test_login_return_stays_inside_flow_and_state_cannot_replay(client, monkeypatch, path):
    import app as m
    monkeypatch.setattr('src.google_login.exchange_code_for_email', lambda *_: ('astralweddingsgt@gmail.com', 'Owner', ''))
    with client.session_transaction() as s:
        s.update(login_state='audit-state', login_next=path)
    response = client.get('/auth/google/login/callback?code=mock&state=audit-state')
    assert response.status_code == 302 and response.headers['Location'] == '/dashboard'
    assert 'state_invalido' in client.get('/auth/google/login/callback?code=mock&state=audit-state').headers['Location']
    assert m._safe_return_path('/jobs?year=2027') == '/jobs?year=2027'


def test_gmail_connection_is_bound_to_browser_and_consumed_once(auth_client, monkeypatch):
    import app as m
    from src import gmail_delivery
    monkeypatch.setattr(gmail_delivery, 'is_configured', lambda: True)
    sent = []
    monkeypatch.setattr(gmail_delivery, 'exchange_code_for_token', lambda *args: sent.append(args) or {'email':'astralweddingsgt@gmail.com'})
    auth_client.get('/auth/google/start')
    with auth_client.session_transaction() as s:
        state = s['gmail_oauth']['state']
    other = m.app.test_client()
    from conftest import login_as_tenant
    login_as_tenant(other, 'tenant-norkevin', email='astralweddingsgt@gmail.com')
    assert 'state+invalido' in other.get('/auth/google/callback?code=mock&state='+state).headers['Location']
    assert not sent
    assert 'connected' in auth_client.get('/auth/google/callback?code=mock&state='+state).headers['Location']
    assert len(sent) == 1
    auth_client.get('/auth/google/callback?code=mock&state='+state)
    assert len(sent) == 1
    with auth_client.session_transaction() as s:
        s['gmail_oauth'] = dict(state='expired', tenant=s['tenant_id'], expires=time.time()-1)
    auth_client.get('/auth/google/callback?code=mock&state=expired')
    assert len(sent) == 1


def test_security_headers_preserve_same_origin_previews(client):
    response = client.get('/login', base_url='https://localhost')
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['X-Frame-Options'] == 'SAMEORIGIN'
    assert response.headers['Referrer-Policy'] == 'strict-origin-when-cross-origin'
    assert response.headers['Strict-Transport-Security'].startswith('max-age=')
    assert client.get('/api/payments').headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('status', ['Pagado', 'Cancelado'])
def test_settled_payments_cannot_generate_another_checkout(auth_client, monkeypatch, status):
    import app as m
    from src import recurrente
    identifier = 'audit-pay-' + uuid.uuid4().hex
    m.store.upsert('payments', dict(id=identifier, tenant_id='tenant-norkevin', amount=1000, status=status))
    monkeypatch.setattr(recurrente, 'is_configured', lambda **kw: True)
    monkeypatch.setattr(recurrente, 'create_checkout', lambda **kw: pytest.fail('No settled charge may reach provider'))
    response = auth_client.post('/api/payments/'+identifier+'/payment-link')
    assert response.status_code == 400


@pytest.mark.parametrize('owner', [True, False])
def test_document_return_links_are_only_for_its_owner(flask_app, owner):
    from flask import render_template, session
    with flask_app.test_request_context('/'):
        if owner: session.update(logged_in=True, tenant_id='tenant-norkevin')
        html = render_template('_owner_document_navigation.html', navigation_document=dict(tenant_id='tenant-norkevin', lead_id='synthetic-lead'))
        assert ('href="/dashboard"' in html) is owner
        assert ('href="/leads/synthetic-lead"' in html) is owner


@pytest.mark.parametrize('verified', [True, False])
def test_google_login_requires_verified_identity(monkeypatch, verified):
    from src import google_login
    monkeypatch.setattr(google_login, '_post_form', lambda *_: {'access_token':'mock-token'})
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self): return json.dumps(dict(email='owner@example.invalid', verified_email=verified)).encode()
    monkeypatch.setattr(google_login.urlrequest, 'urlopen', lambda *a, **kw: Response())
    if verified:
        assert google_login.exchange_code_for_email('code', 'https://localhost/callback')[0] == 'owner@example.invalid'
    else:
        with pytest.raises(ValueError): google_login.exchange_code_for_email('code', 'https://localhost/callback')


def test_gmail_rejects_wrong_brand_without_overwriting_credentials(auth_client, monkeypatch):
    from src import gmail_delivery
    monkeypatch.setattr(gmail_delivery, 'tenant_resolver', lambda: 'tenant-norkevin')
    monkeypatch.setattr(gmail_delivery, '_post_form', lambda *_: {'access_token':'mock', 'refresh_token':'mock'})
    monkeypatch.setattr(gmail_delivery, '_fetch_email', lambda _: 'other-brand@example.invalid')
    monkeypatch.setattr(gmail_delivery, 'save_token', lambda *_: pytest.fail('Must preserve existing Gmail connection'))
    with auth_client.application.test_request_context('/'):
        from flask import session
        session['tenant_id'] = 'tenant-norkevin'
        with pytest.raises(ValueError): gmail_delivery.exchange_code_for_token('code','https://localhost/callback')


def test_recurrente_invalid_reply_and_timeout_never_return_a_payment_link(monkeypatch):
    from src import recurrente
    monkeypatch.setattr(recurrente, '_secret_key', lambda **kw: 'synthetic-key')
    class Response:
        def __init__(self, data): self.data = data
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps(self.data).encode()
    for data in ({}, {'id':'test','checkout_url':'javascript:alert(1)'}, []):
        monkeypatch.setattr(recurrente.urlrequest, 'urlopen', lambda *a, data=data, **kw: Response(data))
        assert not recurrente.create_checkout(name='Synthetic', amount_in_cents=10000)['ok']
    monkeypatch.setattr(recurrente.urlrequest, 'urlopen', lambda *a, **kw: Response({'id':'test','checkout_url':'https://app.recurrente.com/checkout-session/test'}))
    assert recurrente.create_checkout(name='Synthetic', amount_in_cents=10000)['ok']
    def timeout(*args, **kwargs): raise TimeoutError('synthetic timeout')
    monkeypatch.setattr(recurrente.urlrequest, 'urlopen', timeout)
    assert not recurrente.create_checkout(name='Synthetic', amount_in_cents=10000)['ok']
    for amount, currency in ((True,'GTQ'), (10.5,'GTQ'), (100,'GTQ'), (10000,'EUR')):
        assert not recurrente.create_checkout(name='Synthetic', amount_in_cents=amount, currency=currency)['ok']
    assert recurrente._mask('abc') == '***'


def test_oauth_untrusted_unicode_does_not_crash(client):
    with client.session_transaction() as s: s['login_state'] = 'safe-ascii'
    response = client.get('/auth/google/login/callback', query_string={'code':'mock','state':'é'})
    assert response.status_code == 302 and 'state_invalido' in response.headers['Location']


def test_security_diagnostics_require_login_and_never_expose_the_secret(client, auth_client):
    import app as m
    anonymous = m.app.test_client()
    assert anonymous.get('/api/storage/status').status_code == 401
    response = auth_client.get('/api/storage/status')
    security = response.get_json()['security']
    assert security['session_secret_configured'] is True
    assert security['session_same_site'] == 'Lax'
    assert str(m.app.secret_key) not in response.get_data(as_text=True)
