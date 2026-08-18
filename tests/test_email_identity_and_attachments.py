"""Adjuntos, identidad del destinatario e historial de aprobacion.

Puntos 2, 3, 5, 6 y 7 del ultimo plan de Kevin.

La idea de fondo: el destinatario de un correo no es una direccion de correo,
es un CLIENTE de una empresa concreta. Dos personas distintas pueden compartir
la misma direccion en dos negocios distintos, y confundirlas seria repetir el
incidente por otro camino.
"""
import uuid

from src.mail_tracker import (BLOQUEADO, ENVIADO, ENVIANDO, FALLO, MailTracker,
                              check_attachments_same_tenant)

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


def _ok(monkeypatch):
    from src.email_delivery import DeliveryResult
    monkeypatch.setattr('src.mail_tracker.send_email',
                        lambda *a, **k: DeliveryResult(ok=True, provider='test',
                                                       message_id='m1', mode='test'))


# --------------------------------------------------- adjuntos (punto 5)

def test_una_factura_propia_se_puede_adjuntar(client):
    """Caso POSITIVO: sin el, una validacion que bloquea todo pasaria."""
    import app as app_module

    factura = 'INV-' + uuid.uuid4().hex[:6].upper()
    _seed(app_module, 'payments', ASTRAL, invoice_id=factura, amount=100)

    assert check_attachments_same_tenant(ASTRAL, [{'invoice_id': factura}]) is None


def test_una_factura_de_otra_empresa_bloquea_el_envio(client):
    """Aunque el destinatario fuera correcto, mandarle la factura de otra
    empresa seria tan grave como el incidente original."""
    import app as app_module

    factura = 'INV-' + uuid.uuid4().hex[:6].upper()
    _seed(app_module, 'payments', NORKEVIN, invoice_id=factura, amount=100)

    motivo = check_attachments_same_tenant(ASTRAL, [{'invoice_id': factura}])
    assert motivo and 'no pertenece' in motivo
    assert NORKEVIN not in motivo, 'no debe revelar de que empresa es'


def test_un_contrato_propio_se_puede_adjuntar(client):
    import app as app_module

    contrato = _seed(app_module, 'contracts', ASTRAL, estado='Firmado')
    assert check_attachments_same_tenant(ASTRAL, [{'contract_id': contrato['id']}]) is None


def test_un_contrato_de_otra_empresa_bloquea_el_envio(client):
    import app as app_module

    contrato = _seed(app_module, 'contracts', NORKEVIN, estado='Firmado')
    assert check_attachments_same_tenant(ASTRAL, [{'contract_id': contrato['id']}])


def test_un_adjunto_que_no_existe_bloquea_el_envio(client):
    """Si no se puede verificar de quien es, no sale."""
    assert check_attachments_same_tenant(ASTRAL, [{'contract_id': 'no-existe-123'}])


def test_un_adjunto_sin_referencias_no_bloquea(client):
    """Un archivo suelto sin id de registro no se puede validar contra nada,
    pero tampoco es un documento de otra empresa."""
    assert check_attachments_same_tenant(ASTRAL, ['archivo-suelto.pdf']) is None
    assert check_attachments_same_tenant(ASTRAL, [{'filename': 'foto.jpg'}]) is None


def test_el_adjunto_se_revalida_al_enviar_no_solo_al_crear(client, monkeypatch):
    """El caso que pidio Kevin: el job del adjunto cambia de empresa DESPUES
    de generar el pendiente."""
    import app as app_module
    _ok(monkeypatch)

    ctx = _ctx(app_module, ASTRAL)
    try:
        contrato = _seed(app_module, 'contracts', ASTRAL, estado='Firmado')
        job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda con contrato')
        tracker = MailTracker()
        pendiente = tracker.queue_email(
            'cliente@ejemplo.com', 'Tu contrato', 'Adjunto va',
            job_id=job['id'], attachments=[{'contract_id': contrato['id']}])
        assert pendiente['status'] == 'pending'
    finally:
        ctx.pop()

    # El contrato se mueve de empresa.
    contrato['tenant_id'] = NORKEVIN
    app_module.store.upsert('contracts', contrato)

    ctx = _ctx(app_module, ASTRAL)
    try:
        resultado = MailTracker().approve_and_send(pendiente['id'])
    finally:
        ctx.pop()

    assert resultado['ok'] is False, 'el adjunto ajeno debe bloquear el envio'
    assert resultado['pendiente']['status'] == BLOQUEADO


# ------------------------------------- identidad por client_id (6 y 7)

def test_el_mismo_correo_en_las_dos_empresas_son_dos_personas(client):
    """Kevin: puede existir cliente@gmail.com en Astral y en Norkevin. Son
    identidades independientes y no deben mezclarse nunca."""
    import app as app_module

    compartido = 'compartido@gmail.com'
    a = _seed(app_module, 'clients', ASTRAL, first_name='Ana', email=compartido)
    b = _seed(app_module, 'clients', NORKEVIN, first_name='Beto', email=compartido)

    assert a['id'] != b['id']

    # Cada empresa ve solo el suyo, aunque el correo sea identico.
    for tenant, propio, ajeno in ((ASTRAL, a, b), (NORKEVIN, b, a)):
        ctx = _ctx(app_module, tenant)
        try:
            visibles = {c['id'] for c in app_module.store.list('clients')
                        if c.get('email') == compartido}
        finally:
            ctx.pop()
        assert propio['id'] in visibles
        assert ajeno['id'] not in visibles, \
            'coincidir en el correo no puede mezclar dos personas distintas'


def test_un_job_solo_puede_usar_el_cliente_de_su_empresa(client):
    """El job de Astral no debe poder enlazarse al cliente de Norkevin que
    comparte el correo."""
    import app as app_module

    compartido = 'mismo@gmail.com'
    _seed(app_module, 'clients', ASTRAL, first_name='Ana', email=compartido)
    de_norkevin = _seed(app_module, 'clients', NORKEVIN, first_name='Beto',
                        email=compartido)
    job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda de Astral')

    ctx = _ctx(app_module, ASTRAL)
    try:
        # Intentar apuntar el job de Astral al cliente de Norkevin: el
        # cliente ajeno ni siquiera es visible.
        assert app_module.store.get('clients', de_norkevin['id']) is None
        guardado = app_module.store.get('jobs', job['id'])
    finally:
        ctx.pop()

    assert guardado is not None


# --------------------------------------- historial y estados (2 y 3)

def test_el_historial_conserva_toda_la_secuencia(client, monkeypatch):
    """Kevin: "no quiero sobreescribir el historial... quiero poder
    reconstruir toda la secuencia"."""
    import app as app_module
    _ok(monkeypatch)

    ctx = _ctx(app_module, ASTRAL)
    try:
        job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda con historial')
        tracker = MailTracker()
        pendiente = tracker.queue_email('cliente@ejemplo.com', 'Hola', 'Cuerpo',
                                        job_id=job['id'])
        resultado = tracker.approve_and_send(pendiente['id'], actor='kevin@astral.com')
    finally:
        ctx.pop()

    historial = resultado['pendiente']['historial']
    secuencia = [h['a'] for h in historial]
    assert secuencia == [ENVIANDO, ENVIADO], \
        f'la secuencia completa debe quedar registrada, quedo {secuencia}'
    assert all(h.get('cuando') for h in historial)
    assert historial[-1]['actor'] == 'kevin@astral.com', \
        'debe quedar quien aprobo'
    assert historial[0]['de'] == 'pending'


def test_un_fallo_del_proveedor_no_se_marca_como_bloqueado(client, monkeypatch):
    """Bloqueado = seguridad. Fallo = infraestructura. Confundirlos haria
    ver un problema de red como un problema de seguridad."""
    import app as app_module
    from src.email_delivery import DeliveryResult

    monkeypatch.setattr(
        'src.mail_tracker.send_email',
        lambda *a, **k: DeliveryResult(ok=False, provider='gmail', mode='real',
                                       error='timeout hablando con Gmail'))

    ctx = _ctx(app_module, ASTRAL)
    try:
        job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda que falla')
        tracker = MailTracker()
        pendiente = tracker.queue_email('cliente@ejemplo.com', 'Hola', 'Cuerpo',
                                        job_id=job['id'])
        resultado = tracker.approve_and_send(pendiente['id'], actor='kevin')
    finally:
        ctx.pop()

    assert resultado['ok'] is False
    assert resultado['pendiente']['status'] == FALLO, \
        'un error de Gmail es FALLO, no BLOQUEADO'
    assert [h['a'] for h in resultado['pendiente']['historial']] == [ENVIANDO, FALLO]


def test_un_bloqueo_de_seguridad_no_se_marca_como_fallo(client, monkeypatch):
    """El simetrico del anterior."""
    import app as app_module
    _ok(monkeypatch)

    ctx = _ctx(app_module, ASTRAL)
    try:
        job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda que se mueve')
        tracker = MailTracker()
        pendiente = tracker.queue_email('cliente@ejemplo.com', 'Hola', 'Cuerpo',
                                        job_id=job['id'])
    finally:
        ctx.pop()

    job['tenant_id'] = NORKEVIN
    app_module.store.upsert('jobs', job)

    ctx = _ctx(app_module, ASTRAL)
    try:
        resultado = MailTracker().approve_and_send(pendiente['id'], actor='kevin')
    finally:
        ctx.pop()

    assert resultado['pendiente']['status'] == BLOQUEADO, \
        'un cruce de empresas es BLOQUEADO, no FALLO'
