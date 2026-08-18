"""Reintento manual y rastro de quien hizo que.

Kevin: "nada de fallo -> enviar automaticamente otra vez". El incidente
produjo el MISMO correo tres veces; un reintento automatico es exactamente
el mecanismo que multiplica envios cuando algo va mal.
"""
import uuid

import pytest

from src.email_delivery import DeliveryResult
from src.mail_tracker import (BLOQUEADO, CANCELADO, ENVIADO, ENVIANDO, FALLO,
                              PENDIENTE, MailTracker)

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'


def _seed(app_module, tabla, tenant_id, **campos):
    record = {'id': f'{tabla[:4]}-{uuid.uuid4().hex[:8]}', 'tenant_id': tenant_id}
    record.update(campos)
    app_module.store.upsert(tabla, record)
    return record


def _ctx(app_module, tenant_id):
    ctx = app_module.app.test_request_context('/')
    ctx.push()
    from flask import session
    session['tenant_id'] = tenant_id
    return ctx


class _Proveedor:
    """Falla las primeras N veces y despues funciona."""

    def __init__(self, fallos):
        self.fallos, self.intentos = fallos, 0

    def __call__(self, *a, **k):
        self.intentos += 1
        if self.intentos <= self.fallos:
            return DeliveryResult(ok=False, provider='gmail', mode='real',
                                  error='timeout hablando con Gmail')
        return DeliveryResult(ok=True, provider='gmail', mode='real',
                              message_id='m-ok')


@pytest.fixture
def pendiente_fallido(client, monkeypatch):
    import app as app_module

    proveedor = _Proveedor(fallos=1)
    monkeypatch.setattr('src.mail_tracker.send_email', proveedor)

    ctx = _ctx(app_module, ASTRAL)
    try:
        job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda que falla')
        tracker = MailTracker()
        p = tracker.queue_email('cliente@ejemplo.com', 'Hola', 'Cuerpo',
                                job_id=job['id'])
        resultado = tracker.approve_and_send(p['id'], actor='kevin@astral.com')
    finally:
        ctx.pop()

    assert resultado['pendiente']['status'] == FALLO
    return {'app': app_module, 'id': p['id'], 'proveedor': proveedor}


# ------------------------------------------------------- no hay automatismo

def test_un_fallo_no_se_reintenta_solo(pendiente_fallido):
    """Queda en FALLO y ahi se queda hasta que alguien decida."""
    app_module = pendiente_fallido['app']

    ctx = _ctx(app_module, ASTRAL)
    try:
        p = app_module.store.get('pending_emails', pendiente_fallido['id'])
    finally:
        ctx.pop()

    assert p['status'] == FALLO
    assert pendiente_fallido['proveedor'].intentos == 1, \
        'nadie debe volver a intentar por su cuenta'


def test_el_reintento_manual_si_envia(pendiente_fallido):
    """Caso POSITIVO: el proveedor ya se recupero y Kevin reintenta."""
    app_module = pendiente_fallido['app']

    ctx = _ctx(app_module, ASTRAL)
    try:
        resultado = MailTracker().retry_failed(pendiente_fallido['id'],
                                               actor='kevin@astral.com')
    finally:
        ctx.pop()

    assert resultado['ok'] is True
    assert resultado['pendiente']['status'] == ENVIADO
    assert pendiente_fallido['proveedor'].intentos == 2


def test_un_bloqueado_no_se_puede_reintentar(client, monkeypatch):
    """La razon del bloqueo sigue ahi: reintentar seria saltarse la
    validacion, no recuperarse de un error."""
    import app as app_module
    monkeypatch.setattr('src.mail_tracker.send_email', _Proveedor(fallos=0))

    ctx = _ctx(app_module, ASTRAL)
    try:
        job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda que se mueve')
        p = MailTracker().queue_email('cliente@ejemplo.com', 'Hola', 'Cuerpo',
                                      job_id=job['id'])
    finally:
        ctx.pop()

    job['tenant_id'] = NORKEVIN
    app_module.store.upsert('jobs', job)

    ctx = _ctx(app_module, ASTRAL)
    try:
        tracker = MailTracker()
        assert tracker.approve_and_send(p['id'], actor='kevin')['ok'] is False
        resultado = tracker.retry_failed(p['id'], actor='kevin')
    finally:
        ctx.pop()

    assert resultado['ok'] is False
    assert 'fallo por un problema tecnico' in resultado['error']


def test_el_reintento_vuelve_a_validar_todo(pendiente_fallido):
    """Si entre el fallo y el reintento el job cambio de empresa, el
    reintento NO puede pasar por alto la validacion."""
    app_module = pendiente_fallido['app']

    ctx = _ctx(app_module, ASTRAL)
    try:
        p = app_module.store.get('pending_emails', pendiente_fallido['id'])
        job = app_module.store.get('jobs', p['job_id'])
    finally:
        ctx.pop()

    job['tenant_id'] = NORKEVIN
    app_module.store.upsert('jobs', job)

    ctx = _ctx(app_module, ASTRAL)
    try:
        resultado = MailTracker().retry_failed(pendiente_fallido['id'], actor='kevin')
    finally:
        ctx.pop()

    assert resultado['ok'] is False
    assert resultado['pendiente']['status'] == BLOQUEADO


# ------------------------------------------------------------ rastro completo

def test_el_historial_reconstruye_toda_la_secuencia(pendiente_fallido):
    """Kevin: "quiero poder reconstruir toda la secuencia". Un correo que
    fallo y despues se reintento tiene que verse entero."""
    app_module = pendiente_fallido['app']

    ctx = _ctx(app_module, ASTRAL)
    try:
        resultado = MailTracker().retry_failed(pendiente_fallido['id'],
                                               actor='kevin@astral.com')
    finally:
        ctx.pop()

    secuencia = [h['a'] for h in resultado['pendiente']['historial']]
    assert secuencia == [ENVIANDO, FALLO, PENDIENTE, ENVIANDO, ENVIADO], \
        f'la secuencia quedo incompleta: {secuencia}'
    assert all(h['actor'] == 'kevin@astral.com' for h in resultado['pendiente']['historial'])
    assert all(h.get('cuando') for h in resultado['pendiente']['historial'])


def test_descartar_deja_quien_lo_descarto(client, monkeypatch):
    import app as app_module
    monkeypatch.setattr('src.mail_tracker.send_email', _Proveedor(fallos=0))

    ctx = _ctx(app_module, ASTRAL)
    try:
        job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda descartada')
        tracker = MailTracker()
        p = tracker.queue_email('cliente@ejemplo.com', 'Hola', 'Cuerpo',
                                job_id=job['id'])
        resultado = tracker.discard_pending(p['id'], actor='kevin@astral.com')
    finally:
        ctx.pop()

    historial = resultado['pendiente']['historial']
    assert resultado['pendiente']['status'] == CANCELADO
    assert historial[-1]['a'] == CANCELADO
    assert historial[-1]['actor'] == 'kevin@astral.com'


# ------------------------------------------------------------ desde la ruta

def test_la_ruta_de_reintento_registra_quien_fue(client, monkeypatch):
    """El actor tiene que salir de la sesion, no ser el "sistema"."""
    import app as app_module
    from conftest import login_as_tenant

    proveedor = _Proveedor(fallos=1)
    monkeypatch.setattr('src.mail_tracker.send_email', proveedor)

    ctx = _ctx(app_module, ASTRAL)
    try:
        job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda por ruta')
        p = MailTracker().queue_email('cliente@ejemplo.com', 'Hola', 'Cuerpo',
                                      job_id=job['id'])
    finally:
        ctx.pop()

    login_as_tenant(client, ASTRAL, email='kevin@astral.com')
    assert client.post(f'/api/pending-emails/{p["id"]}/send').status_code == 400
    resp = client.post(f'/api/pending-emails/{p["id"]}/retry')

    assert resp.status_code == 200, resp.get_data(as_text=True)
    historial = resp.get_json()['email']['historial']
    actores = {h['actor'] for h in historial}
    assert 'sistema' not in actores, 'debe quedar la persona, no "sistema"'
    assert 'kevin@astral.com' in actores, f'actores: {actores}'


def test_no_se_puede_reintentar_un_pendiente_de_otra_empresa(client, monkeypatch):
    """El aislamiento aplica tambien al reintento."""
    import app as app_module
    from conftest import login_as_tenant

    monkeypatch.setattr('src.mail_tracker.send_email', _Proveedor(fallos=1))

    ctx = _ctx(app_module, ASTRAL)
    try:
        job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda de Astral')
        p = MailTracker().queue_email('cliente@ejemplo.com', 'Hola', 'Cuerpo',
                                      job_id=job['id'])
        MailTracker().approve_and_send(p['id'], actor='kevin')
    finally:
        ctx.pop()

    login_as_tenant(client, NORKEVIN, email='kevin@norkevin.com')
    resp = client.post(f'/api/pending-emails/{p["id"]}/retry')
    assert resp.status_code == 400
    assert 'No encontrado' in resp.get_json()['error']


def test_un_fallo_dice_por_que_fallo(client, monkeypatch):
    """"No se pudo enviar" y nada mas no le sirve a quien aprueba: no puede
    distinguir un Gmail caido de un bloqueo de seguridad."""
    import app as app_module

    monkeypatch.setattr(
        'src.mail_tracker.send_email',
        lambda *a, **k: DeliveryResult(ok=False, provider='gmail', mode='real',
                                       error='timeout hablando con Gmail'))

    ctx = _ctx(app_module, ASTRAL)
    try:
        job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda con detalle')
        tracker = MailTracker()
        p = tracker.queue_email('cliente@ejemplo.com', 'Hola', 'Cuerpo',
                                job_id=job['id'])
        resultado = tracker.approve_and_send(p['id'], actor='kevin')
    finally:
        ctx.pop()

    assert resultado['ok'] is False
    assert 'timeout' in resultado['error']
    assert 'No se pudo entregar' in resultado['error'], \
        'un fallo tecnico no debe leerse como un bloqueo de seguridad'
    assert 'BLOCKED' not in resultado['error']
