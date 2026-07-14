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
    """Cliente HTTP ya autenticado (simula haber pasado el login de Google)."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user_email'] = 'norkevinfoto@gmail.com'
        sess['user_name'] = 'Test User'
    return client
