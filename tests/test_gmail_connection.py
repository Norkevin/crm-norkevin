"""La autorizacion de Gmail pertenece a una sesion y a una marca concreta."""

from src import email_delivery, gmail_delivery
from src.tenant_brand_map import sender_email_for_tenant
from conftest import login_as_tenant

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'


def test_google_de_otra_marca_no_sustituye_el_token(monkeypatch, tmp_path):
    monkeypatch.setenv('CRM_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(gmail_delivery, '_post_form', lambda *args: {
        'access_token': 'fake', 'refresh_token': 'fake', 'expires_in': 3600,
    })
    monkeypatch.setattr(gmail_delivery, '_fetch_email', lambda *args: 'norkevinfoto@gmail.com')

    try:
        gmail_delivery.exchange_code_for_token('code', 'http://localhost/callback', tenant_id=ASTRAL)
    except ValueError as exc:
        assert sender_email_for_tenant(ASTRAL) in str(exc)
    else:
        raise AssertionError('Google de Norkevin no debe conectarse a Astral')

    assert not list(tmp_path.glob('google_token_*.json'))


def test_token_de_otra_marca_no_parece_conectado(monkeypatch, tmp_path):
    monkeypatch.setenv('CRM_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('OUTBOUND_EMAIL_ENABLED', '1')
    monkeypatch.delenv('DISABLE_OUTBOUND_EMAIL', raising=False)
    gmail_delivery.save_token({
        'access_token': 'fake', 'refresh_token': 'fake', 'email': 'norkevinfoto@gmail.com',
    }, tenant_id=ASTRAL)

    assert gmail_delivery.is_connected(tenant_id=ASTRAL) is False
    result = email_delivery.send_email('cliente@example.com', 'Hola', 'Mensaje',
                                       metadata={'tenant_id': ASTRAL})
    assert result.ok is False
    assert result.status == 'blocked'


def test_render_no_marca_el_outbox_local_como_enviado(monkeypatch):
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.setenv('OUTBOUND_EMAIL_ENABLED', '1')
    monkeypatch.delenv('DISABLE_OUTBOUND_EMAIL', raising=False)
    monkeypatch.setenv('EMAIL_DELIVERY_MODE', 'test')
    monkeypatch.setattr(gmail_delivery, 'is_connected', lambda **kwargs: False)
    monkeypatch.setattr(email_delivery, '_send_local',
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('No debe escribir outbox')))

    result = email_delivery.send_email('cliente@example.com', 'Hola', 'Mensaje',
                                       metadata={'tenant_id': ASTRAL})
    assert result.ok is False
    assert result.status == 'blocked'


def test_oauth_de_dos_sesiones_no_se_pisa(client, flask_app, monkeypatch):
    monkeypatch.setattr(gmail_delivery, 'is_configured', lambda: True)
    monkeypatch.setattr(gmail_delivery, 'exchange_code_for_token',
                        lambda code, redirect_uri, tenant_id: {
                            'email': sender_email_for_tenant(tenant_id),
                        })

    login_as_tenant(client, ASTRAL, email='astralweddingsgt@gmail.com')
    other = flask_app.test_client()
    login_as_tenant(other, NORKEVIN, email='norkevinfoto@gmail.com')
    assert client.get('/auth/google/start').status_code == 302
    assert other.get('/auth/google/start').status_code == 302

    with client.session_transaction() as session:
        state_astral = session['gmail_oauth_state']
    with other.session_transaction() as session:
        state_norkevin = session['gmail_oauth_state']
    assert state_astral != state_norkevin

    first = client.get('/auth/google/callback', query_string={'code': 'fake', 'state': state_astral})
    second = other.get('/auth/google/callback', query_string={'code': 'fake', 'state': state_norkevin})
    assert 'google_status=connected' in first.headers['Location']
    assert 'google_status=connected' in second.headers['Location']
