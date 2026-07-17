"""Verifica que el login con Google protege las paginas internas sin romper
las paginas publicas que los clientes necesitan sin iniciar sesion."""

PROTECTED_PAGES = [
    '/dashboard', '/clients', '/leads', '/jobs', '/calendar',
    '/payments', '/settings', '/workflow-editor',
]

PUBLIC_PAGES = [
    '/login', '/contacto', '/captacion',
]


def test_protected_pages_redirect_to_login_when_logged_out(client):
    for path in PROTECTED_PAGES:
        resp = client.get(path)
        assert resp.status_code == 302, f'{path} deberia redirigir sin sesion'
        assert '/login' in resp.headers['Location'], f'{path} deberia mandar a /login'


def test_protected_pages_work_when_logged_in(auth_client):
    for path in PROTECTED_PAGES:
        resp = auth_client.get(path)
        assert resp.status_code == 200, f'{path} deberia cargar con sesion iniciada'


def test_public_pages_never_require_login(client):
    for path in PUBLIC_PAGES:
        resp = client.get(path)
        assert resp.status_code == 200, f'{path} debe ser publica'


def test_protected_api_returns_json_401_not_html_redirect(client):
    """Las llamadas fetch/apiPost del frontend esperan JSON -- si la sesion
    expira a medio uso, un redirect HTML rompe el JS. Debe ser un 401 JSON."""
    resp = client.get('/api/payments/some-id/reminder-preview')
    assert resp.status_code == 401
    assert resp.is_json
    assert resp.get_json()['ok'] is False


def test_logout_clears_session(auth_client):
    resp = auth_client.get('/dashboard')
    assert resp.status_code == 200

    auth_client.get('/logout')

    resp = auth_client.get('/dashboard')
    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']


def test_login_only_allows_configured_emails(client, monkeypatch):
    """Simula el callback de Google devolviendo un email NO autorizado --
    no debe iniciar sesion."""
    from src import google_login

    monkeypatch.setattr(google_login, 'exchange_code_for_email',
                         lambda code, redirect_uri: ('intruso@gmail.com', 'Intruso', ''))

    with client.session_transaction() as sess:
        sess['login_state'] = 'abc123'

    resp = client.get('/auth/google/login/callback?code=fake&state=abc123')
    assert resp.status_code == 302
    assert 'cuenta_no_autorizada' in resp.headers['Location']

    # Sigue sin poder entrar al dashboard
    resp = client.get('/dashboard')
    assert resp.status_code == 302


def test_login_allows_whitelisted_email(client, monkeypatch):
    from src import google_login

    monkeypatch.setattr(google_login, 'exchange_code_for_email',
                         lambda code, redirect_uri: ('norkevinfoto@gmail.com', 'Kevin', ''))

    with client.session_transaction() as sess:
        sess['login_state'] = 'xyz789'

    resp = client.get('/auth/google/login/callback?code=fake&state=xyz789')
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/dashboard')

    resp = client.get('/dashboard')
    assert resp.status_code == 200


def test_login_bootstrap_works_when_tenants_json_never_existed(client, monkeypatch):
    """Regresion de un bloqueo real en produccion: Kevin quedo fuera de su
    propia cuenta porque el disco de Render nunca tuvo un data/tenants.json
    (nada lo necesitaba antes de multi-tenant) -- store.list('tenants')
    devolvia [] y el bootstrap exigia que la lista NO estuviera vacia antes
    de activarse. El fixture de tests SI trae un tenants.json con los 3
    tenants reales ya migrados, asi que hay que borrarlo a mano aca para
    reproducir el estado real de un deploy fresco."""
    import app as app_module
    from src import google_login

    # monkeypatch en vez de store._save('tenants', []): el store es un
    # singleton compartido por TODA la sesion de pytest (no por test), asi
    # que escribir la tabla de verdad dejaria 'tenants' vacio para
    # cualquier test que corra despues de este.
    real_list = app_module.store.list

    def _fake_list(table):
        if table == 'tenants':
            return []
        return real_list(table)

    monkeypatch.setattr(app_module.store, 'list', _fake_list)

    monkeypatch.setattr(google_login, 'exchange_code_for_email',
                         lambda code, redirect_uri: ('astralweddingsgt@gmail.com', 'Kevin', ''))

    with client.session_transaction() as sess:
        sess['login_state'] = 'boot123'

    resp = client.get('/auth/google/login/callback?code=fake&state=boot123')
    assert resp.status_code == 302
    assert 'cuenta_no_autorizada' not in resp.headers['Location'], (
        'un email en ALLOWED_LOGIN_EMAILS no debe quedar bloqueado solo '
        'porque tenants.json todavia no existe en el disco'
    )
    assert resp.headers['Location'].endswith('/dashboard')

    resp = client.get('/dashboard')
    assert resp.status_code == 200
