"""
conftest.py - Fixtures compartidos para todo el test suite.

Reglas de seguridad de estos tests (no negociables):
  1. NUNCA tocan data/*.json real -- se copian a un directorio temporal y
     CRM_DATA_DIR se apunta ahi antes de que CUALQUIER modulo de la app se
     importe. Esto pasa en pytest_configure(), que corre antes de la
     coleccion de tests -- si se hiciera en un fixture normal, un archivo
     como test_full_route_sweep.py (que necesita "import app" para poder
     generar sus parametrize() a partir del url_map) lo importaria durante
     la coleccion, ANTES de que el fixture alcance a correr, y quedaria
     apuntando a los datos reales para el resto de la sesion (el store es
     un singleton de proceso).
  2. NUNCA mandan un correo real -- src.mail_tracker.send_email se
     reemplaza por un fake en cada test.
  3. NUNCA llaman a la API real de Recurrente -- las credenciales se vacian
     en el proceso de tests.
"""
import os
import shutil
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

_TMP_DATA_DIR = None


def pytest_configure(config):
    """Corre antes de que pytest coleccione (importe) ningun archivo de
    test, garantizando que CRM_DATA_DIR ya apunta al directorio aislado sin
    importar el orden de coleccion ni lo que cada archivo haga a nivel de
    modulo (parametrize, etc)."""
    global _TMP_DATA_DIR
    real_data_dir = os.path.join(REPO_ROOT, 'data')
    tmp_dir = tempfile.mkdtemp(prefix='crm_test_data_')
    if os.path.isdir(real_data_dir):
        for name in os.listdir(real_data_dir):
            src = os.path.join(real_data_dir, name)
            if os.path.isfile(src) and name.endswith('.json'):
                shutil.copy2(src, os.path.join(tmp_dir, name))

    os.environ['CRM_DATA_DIR'] = tmp_dir
    os.environ['RECURRENTE_SECRET_KEY'] = ''
    os.environ['RECURRENTE_SECRET_KEY_TEST'] = ''
    os.environ.pop('RECURRENTE_MODE', None)
    os.environ.setdefault('FLASK_SECRET', 'test-secret-not-for-production')
    # Fail-closed real en produccion (ver app.py::_ADMIN_ONE_TIME_TOKEN):
    # sin esto, los tests de tests/test_admin_capabilities.py y afines que
    # pasan app_module._ADMIN_ONE_TIME_TOKEN de vuelta en la URL fallarian
    # con 404 (string vacio nunca matchea a proposito).
    os.environ.setdefault('ADMIN_ONE_TIME_TOKEN', 'test-admin-token-not-for-production')
    os.environ.setdefault('ALLOWED_LOGIN_EMAILS', 'norkevinfoto@gmail.com,astralweddingsgt@gmail.com')

    _TMP_DATA_DIR = tmp_dir


def pytest_unconfigure(config):
    if _TMP_DATA_DIR:
        shutil.rmtree(_TMP_DATA_DIR, ignore_errors=True)


@pytest.fixture(scope='session', autouse=True)
def _isolated_environment():
    """El aislamiento real ya se hizo en pytest_configure(); este fixture
    solo expone el directorio temporal a quien lo necesite (flask_app) y
    documenta la dependencia para autouse."""
    assert _TMP_DATA_DIR, 'pytest_configure() deberia haber corrido antes que cualquier test'
    assert os.environ.get('CRM_DATA_DIR') == _TMP_DATA_DIR, \
        'CRM_DATA_DIR no apunta al directorio aislado -- no continuar, podria tocar datos reales'
    yield _TMP_DATA_DIR


@pytest.fixture(scope='session')
def flask_app(_isolated_environment):
    import app as app_module
    assert app_module.store.data_dir == _isolated_environment, (
        f'store.data_dir ({app_module.store.data_dir}) no es el directorio aislado '
        f'({_isolated_environment}) -- algun modulo importo app.py antes de tiempo'
    )
    app_module.app.config.update(TESTING=True)
    return app_module.app


@pytest.fixture(autouse=True)
def _restore_tenants_table(_isolated_environment):
    """login_as_tenant() hace upsert de tenants sinteticos para pasar la
    guarda de _require_login (app.py) -- el store es un singleton para
    toda la sesion de pytest, asi que sin este snapshot/restore esos
    tenants (o un login_email pisado sobre uno real) se filtran a
    cualquier test que corra despues en la misma sesion."""
    import app as app_module
    snapshot = [dict(t) for t in app_module.store.list('tenants')]
    yield
    app_module.store._save('tenants', snapshot)


class RealProviderCallBlocked(AssertionError):
    """Un test intento llegar a un proveedor de correo real (SMTP/Resend/
    Gmail). Esto NUNCA debe pasar en la suite de pytest, sin importar el
    kill switch (OUTBOUND_EMAIL_ENABLED/DISABLE_OUTBOUND_EMAIL) -- ese
    kill switch es la primera linea de defensa (src/email_delivery.py:
    send_email() lo revisa ANTES de mirar el proveedor), pero este guardia
    es la segunda: si un refactor futuro llegara a mover, saltarse o romper
    ese chequeo, este guardia sigue frenando la llamada real en vez de
    dejarla pasar en silencio."""


@pytest.fixture(scope='session', autouse=True)
def _block_real_email_providers():
    """Guardia de infraestructura (prioridad 6, cierre de brechas -- 'quiero
    una proteccion adicional en el runner/test environment... ninguna
    prueba debe depender de conexion externa'). Reemplaza las funciones de
    BAJO NIVEL que de verdad tocan red (smtplib, urlopen a Resend, la API
    de Gmail) por una que siempre explota -- para toda la sesion de
    pytest, sin importar que fixture use cada test. Los tests que SI
    quieren simular un envio exitoso siguen pudiendo parchar
    `src.mail_tracker.send_email` (una capa mas arriba, ver fixture
    `client`) con su propio fake; ese parche gana porque queda mas cerca
    de la llamada. Esto es ademas del kill switch de variables de entorno
    (OUTBOUND_EMAIL_ENABLED=0 / DISABLE_OUTBOUND_EMAIL=1, forzadas por
    pytest_configure arriba) -- doble candado, no un sustituto."""
    import src.email_delivery as email_delivery
    import src.gmail_delivery as gmail_delivery

    def _blocked(*args, **kwargs):
        raise RealProviderCallBlocked(
            'Un test intento invocar una funcion de entrega de correo REAL '
            '(SMTP/Resend/Gmail) durante la suite de pytest. Esto esta '
            'bloqueado a proposito -- ningun test debe depender de, ni '
            'poder alcanzar, un proveedor de correo real.'
        )

    # OJO: is_connected() NO se toca aca -- test_credential_isolation.py
    # verifica legitimamente su comportamiento real (guardar/borrar token,
    # aislamiento por tenant) sin que eso implique una llamada de red; solo
    # send_gmail() (la funcion que de verdad habla con la API de Gmail) se
    # bloquea. Si send_email() llegara a llamar a _send_gmail real porque
    # is_connected() dio True en algun test, este guardia la frena igual.
    for attr in ('_send_smtp', '_send_resend', '_send_gmail'):
        pytest.MonkeyPatch().setattr(email_delivery, attr, _blocked, raising=True)
    pytest.MonkeyPatch().setattr(gmail_delivery, 'send_gmail', _blocked, raising=True)
    yield


@pytest.fixture()
def client(flask_app, monkeypatch):
    """Cliente HTTP de pruebas. El envio de correo esta parchado a un fake
    que NUNCA toca Gmail/SMTP real, sin importar que ruta se ejercite."""
    from src.email_delivery import DeliveryResult

    def _fake_send_email(to_email, subject, body='', **kwargs):
        return DeliveryResult(ok=True, provider='test', message_id='test-msg', mode='test')

    monkeypatch.setattr('src.mail_tracker.send_email', _fake_send_email)

    with flask_app.test_client() as c:
        yield c


@pytest.fixture()
def auth_client(client):
    """Cliente HTTP ya autenticado (simula haber pasado el login de Google)
    para la cuenta Astral Weddings/tenant-norkevin -- el tenant_id que ya
    usan practicamente todos los fixtures de datos de este repo. Para un
    segundo tenant sintetico en un test de aislamiento, usar
    login_as_tenant() en vez de este fixture."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user_email'] = 'astralweddingsgt@gmail.com'
        sess['user_name'] = 'Test User'
        sess['tenant_id'] = 'tenant-norkevin'
    return client


def login_as_tenant(client, tenant_id, email='test@example.com', name='Test User'):
    """Loguea el mismo test client como una cuenta/tenant distinta -- para
    tests de aislamiento que necesitan probar 2+ cuentas en el mismo test
    sin el overhead de pasar por el flujo real de Google OAuth.

    _require_login (app.py) valida que exista un registro en `tenants` cuyo
    login_email coincida con la sesion -- se hace upsert de uno sintetico
    aca para que los tenants de prueba (que no existen en tenants.json)
    sigan pasando esa guarda."""
    import app as app_module
    app_module.store.upsert('tenants', {
        'id': tenant_id, 'name': tenant_id, 'login_email': email, 'active': True,
    })
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user_email'] = email
        sess['user_name'] = name
        sess['tenant_id'] = tenant_id
    return client
