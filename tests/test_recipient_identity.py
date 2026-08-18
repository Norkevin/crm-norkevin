"""El escenario que Kevin pidio explicitamente (punto 6).

Las dos empresas tienen un cliente con la MISMA direccion de correo. El
sistema tiene que decidir a quien le esta escribiendo mirando el `client_id`,
nunca la direccion -- y demostrar que si se cambia solo el client_id, sin
tocar una letra del correo, el envio se bloquea.

Esa es la prueba de que la direccion dejo de ser una identidad.
"""
import uuid

import pytest

from src.mail_tracker import BLOQUEADO, MailTracker, check_recipient_identity

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'
COMPARTIDO = 'cliente@gmail.com'


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


@pytest.fixture
def escenario(client):
    """La misma direccion como cliente en las dos empresas, mas el job y la
    factura de Astral."""
    import app as app_module

    cliente_astral = _seed(app_module, 'clients', ASTRAL,
                           first_name='Ana', last_name='De Astral',
                           email=COMPARTIDO)
    cliente_norkevin = _seed(app_module, 'clients', NORKEVIN,
                             first_name='Beto', last_name='De Norkevin',
                             email=COMPARTIDO)
    job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda de Astral',
                client_id=cliente_astral['id'])
    factura = 'INV-' + uuid.uuid4().hex[:6].upper()
    _seed(app_module, 'payments', ASTRAL, invoice_id=factura, amount=5000,
          job_id=job['id'])

    return {'app': app_module, 'astral': cliente_astral,
            'norkevin': cliente_norkevin, 'job': job, 'factura': factura}


def _ok(monkeypatch):
    from src.email_delivery import DeliveryResult
    monkeypatch.setattr('src.mail_tracker.send_email',
                        lambda *a, **k: DeliveryResult(ok=True, provider='test',
                                                       message_id='m1', mode='test'))


# ------------------------------------------------------------------ positivo

def test_con_el_client_id_de_astral_el_correo_sale(escenario, monkeypatch):
    """Todo de Astral: cliente, job, factura y la cuenta que envia."""
    app_module = escenario['app']
    _ok(monkeypatch)

    ctx = _ctx(app_module, ASTRAL)
    try:
        tracker = MailTracker()
        pendiente = tracker.queue_email(
            COMPARTIDO, 'Factura de tu boda', 'Adjunto la factura',
            job_id=escenario['job']['id'],
            client_id=escenario['astral']['id'],
            attachments=[{'invoice_id': escenario['factura']}])
        assert pendiente['status'] == 'pending', pendiente.get('blocked_reason')
        resultado = tracker.approve_and_send(pendiente['id'], actor='kevin')
    finally:
        ctx.pop()

    assert resultado['ok'] is True, resultado.get('error')


# ------------------------------------------------------------------ negativo

def test_cambiar_solo_el_client_id_bloquea_el_envio(escenario, monkeypatch):
    """LA prueba. Mismo destinatario, mismo asunto, mismo cuerpo, misma
    empresa que envia. Lo unico distinto es el client_id."""
    app_module = escenario['app']
    _ok(monkeypatch)

    ctx = _ctx(app_module, ASTRAL)
    try:
        pendiente = MailTracker().queue_email(
            COMPARTIDO, 'Factura de tu boda', 'Adjunto la factura',
            job_id=escenario['job']['id'],
            client_id=escenario['norkevin']['id'])  # <- lo unico que cambia
    finally:
        ctx.pop()

    assert pendiente['status'] == BLOQUEADO, \
        'la direccion es identica: si no bloquea, el sistema sigue confiando en el email'
    assert 'no pertenece a esta empresa' in pendiente['blocked_reason']
    assert NORKEVIN not in pendiente['blocked_reason'], \
        'el motivo no debe revelar de que empresa es'


def test_reasignar_el_cliente_despues_del_draft_tambien_bloquea(escenario, monkeypatch):
    """El draft se genero bien; el cliente se movio a la otra empresa
    despues. Al presionar Enviar tiene que volver a validarse."""
    app_module = escenario['app']
    _ok(monkeypatch)

    ctx = _ctx(app_module, ASTRAL)
    try:
        pendiente = MailTracker().queue_email(
            COMPARTIDO, 'Factura de tu boda', 'Cuerpo',
            job_id=escenario['job']['id'], client_id=escenario['astral']['id'])
        assert pendiente['status'] == 'pending'
    finally:
        ctx.pop()

    # El cliente se reasigna. La direccion no cambia.
    cliente = escenario['astral']
    cliente['tenant_id'] = NORKEVIN
    app_module.store.upsert('clients', cliente)

    ctx = _ctx(app_module, ASTRAL)
    try:
        resultado = MailTracker().approve_and_send(pendiente['id'], actor='kevin')
    finally:
        ctx.pop()

    assert resultado['ok'] is False
    assert resultado['pendiente']['status'] == BLOQUEADO


def test_un_cliente_que_no_existe_no_se_da_por_bueno(escenario):
    """Si no se puede verificar de quien es, no sale."""
    motivo, _ = check_recipient_identity(ASTRAL, COMPARTIDO, 'client-inventado')
    assert motivo and 'no se pudo verificar' in motivo


# ---------------------------------------------------------------- avisos

def test_la_direccion_compartida_queda_marcada_como_ambigua(escenario):
    """No bloquea -- el client_id manda -- pero Kevin tiene que verlo antes
    de aprobar."""
    app_module = escenario['app']

    ctx = _ctx(app_module, ASTRAL)
    try:
        motivo, aviso = check_recipient_identity(
            ASTRAL, COMPARTIDO, escenario['astral']['id'])
    finally:
        ctx.pop()

    assert motivo is None, 'no debe bloquear un envio legitimo'
    assert aviso and 'otra empresa' in aviso


def test_escribir_a_una_direccion_distinta_avisa_pero_no_bloquea(escenario):
    """Un cliente puede pedir que le escriban a otro correo. Cortar eso seria
    bloquear de mas."""
    app_module = escenario['app']

    ctx = _ctx(app_module, ASTRAL)
    try:
        motivo, aviso = check_recipient_identity(
            ASTRAL, 'otra-direccion@gmail.com', escenario['astral']['id'])
    finally:
        ctx.pop()

    assert motivo is None
    assert aviso and 'registrada' in aviso
