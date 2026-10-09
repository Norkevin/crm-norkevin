import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from test_flow_teams import web, records
from src.teams_directory import DIRECTORY


@pytest.fixture
def enrollment(web):
    app, owner, storage = web
    response = owner.post('/api/teams/enrollment-link', json={}, headers={'X-Teams-CSRF': 'csrf'})
    assert response.status_code == 200
    path = urlsplit(response.get_json()['url']).path
    visitor = app.test_client()
    page = visitor.get(path)
    assert page.status_code == 200 and 'no-store' in page.headers['Cache-Control']
    nonce = re.search(r'name="submission" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    with visitor.session_transaction() as session:
        csrf = session['teams_enrollment_csrf']
    data = dict(csrf=csrf, submission=nonce, first_name='Ana', last_name='López',
                email='ana@example.invalid', phone='+502 5555 1234', skills='Fotografía, video, Fotografía',
                instagram='@ana', consent='yes')
    return app, owner, app.extensions['teams'], visitor, path, data


def test_enrollment_creates_member_with_private_bank_data_and_no_portal_access(enrollment):
    app, owner, store, visitor, path, data = enrollment
    data.update(bank='Banco privado', account_type='Ahorro', account_number='001234567890',
                account_holder='Ana López', tenant_id='brand-b', role='Administrador', rate='999999', active='false')
    response = visitor.post(path, data=data)
    assert response.status_code == 303
    member = records(store, 'member')[0]
    assert member['name'] == 'Ana López' and member['skills'] == ['Fotografía', 'video']
    assert member['role'] == 'Por definir' and member['rate'] == 0 and member['rate_missing'] and member['active']
    assert not records(store, 'member', 'brand-b') and not records(store, 'access')
    with visitor.session_transaction() as session:
        assert not session.get('teams_member_id') and not session.get('logged_in')
    assert visitor.get('/api/teams/summary').status_code == 404
    assert 'Recibimos tus datos' in visitor.get(response.headers['Location']).get_data(as_text=True)
    for content in (str(records(store, 'audit')), str(owner.get('/api/teams/summary').get_json()),
                    visitor.get(path).get_data(as_text=True), visitor.get(response.headers['Location']).get_data(as_text=True)):
        assert '001234567890' not in content and 'Banco privado' not in content
    private = owner.get('/teams/members/' + member['id']).get_data(as_text=True)
    assert '001234567890' in private and 'Titular de la cuenta' in private
    with owner.session_transaction() as session:
        session.update(tenant_id='brand-b', user_email='other@example.invalid')
    assert owner.get('/teams/members/' + member['id']).status_code == 404


def test_enrollment_retries_and_duplicate_email_never_overwrite(enrollment):
    app, owner, store, visitor, path, data = enrollment
    assert visitor.post(path, data=data).status_code == 303
    assert visitor.post(path, data=data).status_code == 303
    assert len(records(store, 'member')) == 1 and not records(store, 'person_private', DIRECTORY)
    assert visitor.post(path, data=dict(data, phone='9999')).status_code == 409
    assert visitor.post(path, data=dict(data, submission=str(uuid4()), email='ANA@example.invalid')).status_code == 409
    assert records(store, 'member')[0]['phone'] == '+502 5555 1234'


@pytest.mark.parametrize('changes', [
    {'first_name': ''}, {'last_name': 'x' * 75}, {'email': 'not-email'}, {'phone': 'abcd'},
    {'skills': ', ,'}, {'consent': ''}, {'website': 'bot'}, {'bank': 'Banco incompleto'},
    {'account_type': 'inventado'}, {'instagram': 'x' * 301},
])
def test_enrollment_validates_fields_without_partial_records(enrollment, changes):
    app, owner, store, visitor, path, data = enrollment
    assert visitor.post(path, data=dict(data, **changes)).status_code == 400
    assert not records(store, 'member') and not records(store, 'person_private', DIRECTORY)


def test_enrollment_csrf_size_tampering_and_owner_permissions(enrollment):
    app, owner, store, visitor, path, data = enrollment
    assert visitor.post(path, data=dict(data, csrf='wrong')).status_code == 403
    assert visitor.post(path, data=dict(data, skills='x' * 17000)).status_code == 413
    assert visitor.get(path + 'tampered').status_code == 404
    assert visitor.post('/api/teams/enrollment-link', json={}).status_code == 404
    assert owner.post('/api/teams/enrollment-link', json={}).status_code == 403
    app.config.update(FLOW_TEAMS_LOCAL=False, FLOW_TEAMS_ENABLED=False)
    assert visitor.get(path).status_code == 404
    app.config['FLOW_TEAMS_ENABLED'] = True
    assert visitor.get(path).status_code == 200
    import app as crm
    assert crm._is_public_path(path)
    assert not crm._is_public_path(path + '/members')
    assert not crm._is_public_path('/api/teams/enrollment-link')


@pytest.mark.parametrize('field,value', [('account_holder', 'Ana López'), ('account_type', 'Ahorro')])
def test_incomplete_bank_details_stay_open_for_correction(enrollment, field, value):
    app, owner, store, visitor, path, data = enrollment
    response = visitor.post(path, data=dict(data, **{field: value}))
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert '<details open>' in html and value in html


def test_enrollment_link_is_reusable_rotatable_and_brand_scoped(enrollment):
    app, owner, store, visitor, path, data = enrollment
    def link(**body):
        response = owner.post('/api/teams/enrollment-link', json=body, headers={'X-Teams-CSRF': 'csrf'})
        assert response.status_code == 200
        return urlsplit(response.get_json()['url']).path
    assert link() == path
    assert visitor.post(path, data=data).status_code == 303
    replacement = link(renew=True)
    assert replacement != path and visitor.get(path).status_code == 404
    assert visitor.post(path, data=data).status_code == 404
    assert len(records(store, 'member')) == 1
    with owner.session_transaction() as session:
        session.update(tenant_id='brand-b', user_email='other@example.invalid')
    other = link()
    assert other != replacement
    assert visitor.post(other, data=dict(data, submission=str(uuid4()), tenant_id='brand-a')).status_code == 303
    assert len(records(store, 'member', 'brand-b')) == 1


def test_enrollment_rolls_back_private_data_when_member_creation_fails(enrollment, monkeypatch):
    app, owner, store, visitor, path, data = enrollment
    save = store.save
    def fail(db, tenant, kind, record):
        if kind == 'member':
            raise RuntimeError('synthetic storage failure')
        return save(db, tenant, kind, record)
    monkeypatch.setattr(store, 'save', fail)
    with pytest.raises(RuntimeError, match='synthetic storage failure'):
        visitor.post(path, data=dict(data, bank='Banco', account_type='Ahorro', account_number='00123', account_holder='Ana'))
    assert not records(store, 'person_private', DIRECTORY) and not records(store, 'enrollment_receipt')


def test_enrollment_concurrent_retry_creates_one_member(enrollment):
    app, owner, store, visitor, path, data = enrollment
    cookie = visitor.get_cookie('session').value
    def send(_):
        client = app.test_client()
        client.set_cookie('session', cookie)
        return client.post(path, data=data).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(send, range(2))) == [303, 303]
    assert len(records(store, 'member')) == len(records(store, 'enrollment_receipt')) == 1


def test_enrollment_registration_limit(enrollment):
    app, owner, store, visitor, path, data = enrollment
    assert visitor.post(path, data=data).status_code == 303
    with store.transaction() as db:
        attempt = store.records(db, 'brand-a', 'enrollment_attempt')[0]
        attempt['count'] = 20
        store.save(db, 'brand-a', 'enrollment_attempt', attempt)
    assert visitor.post(path, data=dict(data, submission=str(uuid4()), email='another@example.invalid')).status_code == 429
    assert len(records(store, 'member')) == 1


def test_short_enrollment_link_preserves_legacy_links_and_rotation(enrollment):
    from itsdangerous import URLSafeSerializer
    app, owner, store, visitor, path, data = enrollment
    assert re.fullmatch(r'/equipo/[A-Za-z0-9_-]{22}', path)
    link = records(store, 'enrollment_link')[0]
    legacy_token = URLSafeSerializer(app.secret_key, salt='teams-enrollment').dumps(dict(tenant='brand-a', id=link['id']))
    legacy_path = '/teams/join/' + legacy_token
    assert visitor.get(legacy_path).status_code == 200
    response = visitor.post(legacy_path, data=data)
    assert response.status_code == 303 and response.headers['Location'] == path + '?submitted=1'
    assert visitor.post(path, data=data).status_code == 303
    assert len(records(store, 'member')) == 1
    owner.post('/api/teams/enrollment-link', json={'renew': True}, headers={'X-Teams-CSRF': 'csrf'})
    assert visitor.get(path).status_code == visitor.get(legacy_path).status_code == 404


def test_short_enrollment_link_rejects_unknown_and_noncanonical_codes(enrollment):
    from src.teams_enrollment import enrollment_code
    app, owner, store, visitor, path, data = enrollment
    assert visitor.get('/equipo/' + enrollment_code(str(uuid4()))).status_code == 404
    code = path.rsplit('/', 1)[-1]
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
    alias = code[:-1] + alphabet[alphabet.index(code[-1]) + 1]
    assert visitor.get('/equipo/' + alias).status_code == 404
    for invalid in ('short', 'A' * 23, '!' * 22):
        assert visitor.get('/equipo/' + invalid).status_code == 404
