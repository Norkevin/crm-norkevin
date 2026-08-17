"""Cola de aprobacion: ningun correo sale sin una accion consciente de Kevin.

Cada guarda se prueba en los DOS sentidos, como pidio Kevin: que bloquee lo
que tiene que bloquear, y que NO bloquee el uso legitimo. Una seguridad que
corta todo tampoco sirve -- de hecho la primera version de la validacion
cross-company bloqueaba el 100% de los envios y eso solo se vio por tener el
caso positivo.
"""
import uuid

from src.mail_tracker import MailTracker, MailStatus

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'


def _seed(app_module, tabla, tenant_id, **campos):
    record = {'id': f'{tabla[:4]}-{uuid.uuid4().hex[:8]}', 'tenant_id': tenant_id}
    record.update(campos)
    app_module.store.upsert(tabla, record)
    return record


def _ctx(app_module, tenant_id):
    """Peticion con una cuenta activa."""
    ctx = app_module.app.test_request_context('/')
    ctx.push()
    from flask import session
    session['tenant_id'] = tenant_id
    return ctx


# ------------------------------------------------------------ encolar, no enviar

def test_encolar_no_envia_nada(client, monkeypatch):
    """El punto central: generar el correo NO debe entregarlo."""
    import app as app_module

    enviados = []
    monkeypatch.setattr('src.mail_tracker.send_email',
                        lambda *a, **k: enviados.append(a) or None)

    ctx = _ctx(app_module, ASTRAL)
    try:
        job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda propia')
        pendiente = MailTracker().queue_email(
            'cliente@ejemplo.com', 'Recordatorio de pago', 'Hola...',
            job_id=job['id'], source='workflow:recordatorio',
        )
    finally:
        ctx.pop()

    assert pendiente['status'] == 'pending'
    assert enviados == [], 'encolar no debe entregar el correo'


def test_el_pendiente_guarda_copia_de_lo_generado(client):
    """Si manana cambia la plantilla, el pendiente debe seguir mostrando lo
    que se genero hoy."""
    import app as app_module

    ctx = _ctx(app_module, ASTRAL)
    try:
        job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda propia')
        pendiente = MailTracker().queue_email(
            'cliente@ejemplo.com', 'Asunto original', 'Cuerpo original',
            job_id=job['id'], client_id='client-x', source='workflow:x',
        )
    finally:
        ctx.pop()

    for campo in ('tenant_id', 'to', 'client_id', 'job_id', 'subject',
                  'body', 'attachments', 'created_at', 'source'):
        assert campo in pendiente, f'falta {campo} en la copia guardada'
    assert pendiente['subject'] == 'Asunto original'
    assert pendiente['body'] == 'Cuerpo original'


# ------------------------------------------------------------------- aprobar

def test_aprobar_envia_el_correo(client, monkeypatch):
    """Caso POSITIVO: con todo en orden, aprobar si debe enviar."""
    import app as app_module
    from src.email_delivery import DeliveryResult

    monkeypatch.setattr(
        'src.mail_tracker.send_email',
        lambda *a, **k: DeliveryResult(ok=True, provider='test', message_id='m1', mode='test'),
    )

    ctx = _ctx(app_module, ASTRAL)
    try:
        job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda propia')
        tracker = MailTracker()
        pendiente = tracker.queue_email('cliente@ejemplo.com', 'Hola', 'Cuerpo',
                                        job_id=job['id'])
        resultado = tracker.approve_and_send(pendiente['id'])
    finally:
        ctx.pop()

    assert resultado['ok'] is True
    assert resultado['pendiente']['status'] == 'sent'
    assert resultado['mail']['status'] == MailStatus.SENT.value


def test_no_se_puede_aprobar_un_pendiente_de_otra_cuenta(client):
    """Un pendiente de Astral no debe poder aprobarse desde Norkevin."""
    import app as app_module

    ctx = _ctx(app_module, ASTRAL)
    try:
        job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda de Astral')
        pendiente = MailTracker().queue_email('cliente@ejemplo.com', 'Hola', 'Cuerpo',
                                              job_id=job['id'])
    finally:
        ctx.pop()

    ctx = _ctx(app_module, NORKEVIN)
    try:
        resultado = MailTracker().approve_and_send(pendiente['id'])
    finally:
        ctx.pop()

    assert resultado['ok'] is False


def test_revalida_al_enviar_no_solo_al_crear(client, monkeypatch):
    """Kevin: entre crear y enviar pueden cambiar las relaciones. Si el job
    se movio a otra cuenta despues de generar el pendiente, al aprobar tiene
    que bloquearse igual."""
    import app as app_module
    from src.email_delivery import DeliveryResult

    monkeypatch.setattr(
        'src.mail_tracker.send_email',
        lambda *a, **k: DeliveryResult(ok=True, provider='test', message_id='m1', mode='test'),
    )

    ctx = _ctx(app_module, ASTRAL)
    try:
        job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda que se movera')
        pendiente = MailTracker().queue_email('cliente@ejemplo.com', 'Hola', 'Cuerpo',
                                              job_id=job['id'])
        assert pendiente['status'] == 'pending'
    finally:
        ctx.pop()

    # El job cambia de cuenta despues de haberse generado el pendiente.
    job['tenant_id'] = NORKEVIN
    app_module.store.upsert('jobs', job)

    ctx = _ctx(app_module, ASTRAL)
    try:
        resultado = MailTracker().approve_and_send(pendiente['id'])
    finally:
        ctx.pop()

    assert resultado['ok'] is False
    assert 'cross-company' in resultado['error']


def test_descartar_no_envia_y_deja_rastro(client, monkeypatch):
    import app as app_module

    enviados = []
    monkeypatch.setattr('src.mail_tracker.send_email',
                        lambda *a, **k: enviados.append(a) or None)

    ctx = _ctx(app_module, ASTRAL)
    try:
        job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda propia')
        tracker = MailTracker()
        pendiente = tracker.queue_email('cliente@ejemplo.com', 'Hola', 'Cuerpo',
                                        job_id=job['id'])
        resultado = tracker.discard_pending(pendiente['id'])
    finally:
        ctx.pop()

    assert resultado['ok'] is True
    assert resultado['pendiente']['status'] == 'discarded'
    assert resultado['pendiente'].get('discarded_at')
    assert enviados == []


def test_un_pendiente_no_se_envia_dos_veces(client, monkeypatch):
    import app as app_module
    from src.email_delivery import DeliveryResult

    monkeypatch.setattr(
        'src.mail_tracker.send_email',
        lambda *a, **k: DeliveryResult(ok=True, provider='test', message_id='m1', mode='test'),
    )

    ctx = _ctx(app_module, ASTRAL)
    try:
        job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda propia')
        tracker = MailTracker()
        pendiente = tracker.queue_email('cliente@ejemplo.com', 'Hola', 'Cuerpo',
                                        job_id=job['id'])
        assert tracker.approve_and_send(pendiente['id'])['ok'] is True
        segundo = tracker.approve_and_send(pendiente['id'])
    finally:
        ctx.pop()

    assert segundo['ok'] is False
    assert 'ya fue enviado' in segundo['error']


def test_los_pendientes_no_se_ven_desde_otra_cuenta(client):
    """Aislamiento tambien para la cola."""
    import app as app_module

    ctx = _ctx(app_module, ASTRAL)
    try:
        job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda de Astral')
        MailTracker().queue_email('cliente@ejemplo.com', 'Solo Astral', 'Cuerpo',
                                  job_id=job['id'])
        propios = {p['subject'] for p in app_module.store.list('pending_emails')}
    finally:
        ctx.pop()
    assert 'Solo Astral' in propios

    ctx = _ctx(app_module, NORKEVIN)
    try:
        ajenos = {p['subject'] for p in app_module.store.list('pending_emails')}
    finally:
        ctx.pop()
    assert 'Solo Astral' not in ajenos
