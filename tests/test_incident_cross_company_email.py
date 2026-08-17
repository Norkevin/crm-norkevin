"""Pruebas del incidente real: el CRM mando correos firmados como ASTRAL
WEDDINGS a clientes de Norkevin Photography, incluyendo cobros.

Cada test de aca corresponde a un escenario que Kevin pidio verificar. No son
pruebas de estilo: cada una cubre una de las fallas que se combinaron para
producir el incidente, y estan escritas para fallar si alguien reabre el
agujero mas adelante.
"""
import uuid

import pytest

from src import gmail_delivery
from src.mail_tracker import MailStatus, check_same_tenant
from src.storage import TenantMismatchError

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'


def _seed(app_module, tabla, tenant_id, **campos):
    """Siembra fuera de peticion (ahi no aplica el aislamiento) con el
    tenant_id puesto a mano."""
    record = {'id': f'{tabla[:4]}-{uuid.uuid4().hex[:8]}', 'tenant_id': tenant_id}
    record.update(campos)
    app_module.store.upsert(tabla, record)
    return record


# ---------------------------------------------------------------- aislamiento

def test_sin_cuenta_activa_no_se_ve_ningun_registro(client):
    """LA causa raiz. Antes, sin cuenta activa, list() devolvia los registros
    de TODOS los negocios; asi una rutina sin sesion recorrio las bodas de
    Astral y Norkevin juntas."""
    import app as app_module

    _seed(app_module, 'clients', ASTRAL, first_name='Cliente', last_name='Astral')
    _seed(app_module, 'clients', NORKEVIN, first_name='Cliente', last_name='Norkevin')

    with app_module.app.test_request_context('/'):
        from flask import session
        session.pop('tenant_id', None)
        assert app_module.store.list('clients') == [], \
            'sin cuenta activa no se debe ver ni un registro'


def test_cada_cuenta_solo_ve_lo_suyo(client):
    """Test 6 de Kevin: al cambiar de cuenta no debe aparecer info cruzada."""
    import app as app_module

    de_astral = _seed(app_module, 'clients', ASTRAL, first_name='Solo', last_name='Astral')
    de_norkevin = _seed(app_module, 'clients', NORKEVIN, first_name='Solo', last_name='Norkevin')

    for tenant, propio, ajeno in ((ASTRAL, de_astral, de_norkevin),
                                  (NORKEVIN, de_norkevin, de_astral)):
        with app_module.app.test_request_context('/'):
            from flask import session
            session['tenant_id'] = tenant
            ids = {c['id'] for c in app_module.store.list('clients')}
            assert propio['id'] in ids
            assert ajeno['id'] not in ids, 'se filtro un cliente de la otra cuenta'


def test_no_se_puede_leer_un_registro_de_otra_cuenta_por_id(client):
    """Test 1 de Kevin: pedir por id un cliente de la otra cuenta no debe
    devolverlo. get() se comporta como si no existiera."""
    import app as app_module

    ajeno = _seed(app_module, 'clients', NORKEVIN, first_name='Ajeno')

    with app_module.app.test_request_context('/'):
        from flask import session
        session['tenant_id'] = ASTRAL
        assert app_module.store.get('clients', ajeno['id']) is None


def test_no_se_puede_escribir_sin_cuenta_activa(client):
    """Escribir sin cuenta dejaba registros huerfanos, visibles desde
    cualquier negocio."""
    import app as app_module

    with app_module.app.test_request_context('/'):
        from flask import session
        session.pop('tenant_id', None)
        with pytest.raises(TenantMismatchError):
            app_module.store.upsert('clients', {'id': 'client-huerfano', 'first_name': 'X'})


def test_no_se_puede_borrar_un_registro_de_otra_cuenta(client):
    import app as app_module

    ajeno = _seed(app_module, 'clients', NORKEVIN, first_name='Intocable')

    with app_module.app.test_request_context('/'):
        from flask import session
        session['tenant_id'] = ASTRAL
        assert app_module.store.delete('clients', ajeno['id']) is False

    assert app_module.store.get('clients', ajeno['id']) is not None, \
        'el registro de la otra cuenta no debe haberse borrado'


# --------------------------------------------------------------------- correo

def test_envio_cruzado_entre_cuentas_queda_bloqueado():
    """Test 2 de Kevin: estando en Astral, mandar a un job de Norkevin debe
    bloquearse en el servidor."""
    import app as app_module

    job_norkevin = _seed(app_module, 'jobs', NORKEVIN, nombre='Boda de Norkevin')

    motivo = check_same_tenant(ASTRAL, job_id=job_norkevin['id'])
    assert motivo, 'un job de otra cuenta debe bloquear el envio'
    assert NORKEVIN in motivo and ASTRAL in motivo


def test_envio_dentro_de_la_misma_cuenta_pasa():
    """El bloqueo no puede ser tan amplio que impida el uso normal."""
    import app as app_module

    job_propio = _seed(app_module, 'jobs', ASTRAL, nombre='Boda de Astral')
    assert check_same_tenant(ASTRAL, job_id=job_propio['id']) is None


def test_sin_cuenta_no_se_envia_nada():
    """El caso exacto del hilo de fondo: sin cuenta identificada, no sale."""
    assert check_same_tenant(None) == 'sin cuenta identificada'


def test_el_intento_bloqueado_queda_registrado(client):
    """Test 17 de Kevin: cada intento debe dejar rastro, tambien los
    bloqueados y con su motivo."""
    import app as app_module
    from src.mail_tracker import MailTracker

    job_norkevin = _seed(app_module, 'jobs', NORKEVIN, nombre='Boda ajena')

    with app_module.app.test_request_context('/'):
        from flask import session
        session['tenant_id'] = ASTRAL
        entry = MailTracker().log_email(
            'cliente@ejemplo.com', 'Recordatorio de pago',
            job_id=job_norkevin['id'], tenant_id=ASTRAL,
        )

    assert entry['status'] == MailStatus.BLOCKED.value
    assert 'cross-company' in entry['delivery_error']
    assert entry['blocked_reason']


# ---------------------------------------------------------------------- gmail

def test_sin_cuenta_gmail_no_esta_conectado(monkeypatch):
    """Test 3 de Kevin. Este era el agujero exacto: _token_path caia a un
    'google_token.json' global cuando no habia cuenta, y ese token si podia
    enviar. Por eso la banda decia "Gmail no conectado" mientras salian
    correos."""
    monkeypatch.setattr(gmail_delivery, 'tenant_resolver', lambda: None)
    assert gmail_delivery._token_path() is None
    assert gmail_delivery.is_connected() is False


def test_desconectar_gmail_sobrevive_un_reinicio(monkeypatch, tmp_path):
    """El test que pidio Kevin: conectar, enviar, desconectar, reiniciar el
    proceso, intentar enviar otra vez -> imposible.

    El "reinicio" se simula recargando el modulo: si la desconexion solo
    limpiara memoria, al recargar volveria a estar conectado. Como el token
    vive en un archivo y desconectar lo borra, no vuelve.
    """
    import importlib

    monkeypatch.setenv('CRM_DATA_DIR', str(tmp_path))
    gd = importlib.reload(gmail_delivery)
    monkeypatch.setattr(gd, 'tenant_resolver', lambda: None)

    # 1-2. Conectada y en condiciones de enviar.
    gd.save_token({'access_token': 'a', 'refresh_token': 'r', 'email': 'astral@x.com'},
                  tenant_id=ASTRAL)
    assert gd.is_connected(tenant_id=ASTRAL) is True

    # 3. Desconectar.
    gd.disconnect(tenant_id=ASTRAL)
    assert gd.is_connected(tenant_id=ASTRAL) is False

    # 4. "Reinicio" del proceso.
    gd2 = importlib.reload(gmail_delivery)
    monkeypatch.setattr(gd2, 'tenant_resolver', lambda: None)

    # 5. Sigue desconectada, y no aparece ninguna credencial de repuesto.
    assert gd2.is_connected(tenant_id=ASTRAL) is False
    assert gd2.load_token(tenant_id=ASTRAL) is None
    assert gd2.is_connected() is False, 'no debe haber fallback global'


def test_una_cuenta_no_usa_las_credenciales_de_la_otra(monkeypatch):
    """Test 5 de Kevin: cada empresa con su propia conexion de Gmail."""
    monkeypatch.setattr(gmail_delivery, 'tenant_resolver', lambda: None)
    ruta_astral = gmail_delivery._token_path(tenant_id=ASTRAL)
    ruta_norkevin = gmail_delivery._token_path(tenant_id=NORKEVIN)
    assert ruta_astral != ruta_norkevin
    assert ASTRAL in str(ruta_astral)
    assert NORKEVIN in str(ruta_norkevin)


# ------------------------------------------------------------------ scheduler

def test_el_scheduler_automatico_no_arranca_por_defecto(monkeypatch):
    """Test 4 de Kevin: un paso 'Auto send email' no debe enviar solo.
    El hilo que lo hacia queda apagado salvo que se pida explicitamente."""
    import app as app_module

    monkeypatch.delenv('ENABLE_REMINDER_SCHEDULER', raising=False)
    monkeypatch.setattr(app_module, '_reminder_thread_started', False, raising=False)
    app_module.start_reminder_scheduler()
    assert app_module._reminder_thread_started is False, \
        'el scheduler no debe arrancar sin ENABLE_REMINDER_SCHEDULER=1'


def test_freno_global_bloquea_cualquier_envio(monkeypatch):
    """DISABLE_OUTBOUND_EMAIL corta incluso los envios manuales."""
    from src import email_delivery

    monkeypatch.setenv('DISABLE_OUTBOUND_EMAIL', '1')
    resultado = email_delivery.send_email('alguien@ejemplo.com', 'Prueba', 'cuerpo')
    assert resultado.ok is False
    assert resultado.status == 'blocked'


# ------------------------------------------------------- multiples clientes

def test_un_job_con_dos_clientes_los_conserva_en_la_misma_cuenta(client):
    """Test 7 de Kevin: las bodas con novio, novia y wedding planner deben
    conservar todas sus relaciones, y todas dentro de la misma cuenta."""
    import app as app_module

    novia = _seed(app_module, 'clients', ASTRAL, first_name='Novia')
    novio = _seed(app_module, 'clients', ASTRAL, first_name='Novio')
    planner = _seed(app_module, 'clients', ASTRAL, first_name='Planner')
    job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda con 3 contactos',
                client_id=novia['id'], secondary_client_id=novio['id'],
                planner_client_id=planner['id'])

    with app_module.app.test_request_context('/'):
        from flask import session
        session['tenant_id'] = ASTRAL
        guardado = app_module.get_job(job['id'])
        ids = app_module.get_job_client_ids(guardado)

    assert ids == [novia['id'], novio['id'], planner['id']]
