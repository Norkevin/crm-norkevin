"""STAGE 2 (agosto 2026): validacion exhaustiva de que los 13 puntos de
envio de produccion realmente quedan detras de la cola de aprobacion.

La cola en si (queue_email/approve_and_send/retry_failed/discard_pending,
bloqueo cross-tenant, identidad del destinatario, adjuntos, kill switch,
historial) ya tenia su propia suite profunda antes de este bloque
(test_pending_email_approval.py, test_manual_retry_and_audit.py,
test_recipient_identity.py, test_email_identity_and_attachments.py,
test_incident_cross_company_email.py) -- ese mecanismo no se vuelve a
probar aca desde cero.

Lo que faltaba probar, y es el objetivo de este archivo, es la CONEXION:
que los endpoints reales (los que Kevin realmente usa desde la pantalla)
efectivamente encolan en vez de entregar, que dos clicks seguidos sobre el
mismo endpoint no apilan dos pendientes, que un cruce de cuenta se detecta
tambien cuando pasa por el endpoint real (no solo llamando a MailTracker
directo), que Ramiro queda tan aislado como Astral/Norkevin, y que el kill
switch global sigue siendo la ultima palabra incluso para un pendiente que
ya paso por aprobacion.
"""
import uuid
from datetime import date, timedelta

import pytest

from conftest import login_as_tenant
from src.email_delivery import DeliveryResult

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'
RAMIRO = 'tenant-ramiro-cruz'


def _ctx(app_module, tenant_id):
    ctx = app_module.app.test_request_context('/')
    ctx.push()
    from flask import session
    session['tenant_id'] = tenant_id
    return ctx


def _make_job_with_client(app_module, tenant_id, suffix, email='wiring@example.com'):
    client_id = f'client-wiring-{suffix}'
    job_id = f'job-wiring-{suffix}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Wiring', 'last_name': 'Test',
        'email': email, 'tenant_id': tenant_id,
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Wiring Test',
        'tenant_id': tenant_id,
    })
    return client_id, job_id


def _pending_by_prefix(app_module, prefix):
    return [p for p in app_module.store.list('pending_emails')
            if (p.get('idempotency_key') or '').startswith(prefix)]


# ============================================================
# 1. Cada endpoint real encola, nunca entrega directo
# ============================================================

def test_quote_send_never_reaches_the_provider(auth_client, monkeypatch):
    """El punto central de todo STAGE 2: aunque el proveedor este disponible
    y listo para aceptar, encolar no debe llamarlo. Se cuenta cuantas veces
    se invoca send_email (la version fake que ya instala el fixture
    `client`) en vez de solo confiar en el resultado."""
    import app as app_module

    llamadas = []

    def _contando(*a, **k):
        llamadas.append(a)
        return DeliveryResult(ok=True, provider='test', message_id='m', mode='test')

    monkeypatch.setattr('src.mail_tracker.send_email', _contando)

    lead_id = 'lead-wiring-' + uuid.uuid4().hex[:6]
    app_module.upsert_lead({
        'id': lead_id, 'nombre': 'Quote Wiring', 'email': 'quotewiring@example.com',
        'status': 'Nuevo', 'tenant_id': ASTRAL,
    })
    r = auth_client.post('/api/quotes/draft', json={'lead_id': lead_id})
    quote_id = r.get_json()['quote_id']
    auth_client.post(f'/api/quotes/{quote_id}/options', json={'name': 'Paquete', 'precio_total': 5000})

    r = auth_client.post(f'/api/quotes/{quote_id}/send', json={})
    assert r.status_code == 200
    data = r.get_json()
    assert data['delivery_status'] == 'pending'

    assert llamadas == [], 'queue_email() nunca debe llamar a send_email() -- eso es trabajo de approve_and_send()'
    pendiente = app_module.store.get('pending_emails', data['mail_id'])
    assert pendiente and pendiente['status'] == 'pending'


@pytest.mark.parametrize('endpoint', [
    '/api/payments/{id}/send',
    '/api/payments/{id}/send-reminder',
])
def test_payment_endpoints_queue_not_send(auth_client, endpoint):
    import app as app_module

    suffix = uuid.uuid4().hex[:6]
    client_id, job_id = _make_job_with_client(app_module, ASTRAL, suffix, email='paywiring@example.com')
    pay_id = f'pay-wiring-{suffix}'
    app_module.store.upsert('payments', {
        'id': pay_id, 'client_id': client_id, 'job_id': job_id,
        'invoice_id': f'INV-WIRING-{suffix}', 'amount': 1000.0, 'status': 'Pendiente',
        'due_date': '2027-01-01', 'concepto': 'Cuota', 'tenant_id': ASTRAL,
    })

    r = auth_client.post(endpoint.format(id=pay_id), json={})
    assert r.status_code == 200
    data = r.get_json()
    assert data['ok'] is True
    assert data['delivery_status'] == 'pending'

    pendiente = app_module.store.get('pending_emails', data['mail_id'])
    assert pendiente['status'] == 'pending'
    assert pendiente['to'] == 'paywiring@example.com'


# ============================================================
# 2. Cruce de cuenta detectado tambien via el endpoint real
# ============================================================

def test_contract_send_blocks_when_job_belongs_to_other_tenant(auth_client):
    """El contrato es de Astral y su destinatario (via lead_id) resuelve
    bien -- pero su job_id apunta a un job de Norkevin. Exactamente la
    clase de dato inconsistente que check_same_tenant existe para atrapar,
    ahora ejercitado a traves del endpoint real de contratos, no llamando
    a MailTracker directo."""
    import app as app_module

    _, job_norkevin = _make_job_with_client(app_module, NORKEVIN, 'ctr-' + uuid.uuid4().hex[:6])
    lead_astral = 'lead-ctr-astral-' + uuid.uuid4().hex[:6]
    app_module.upsert_lead({
        'id': lead_astral, 'nombre': 'Lead Contrato Astral', 'email': 'ctrastral@example.com',
        'status': 'Nuevo', 'tenant_id': ASTRAL,
    })
    contract_id = 'contract-wiring-' + uuid.uuid4().hex[:6]
    app_module.store.upsert('contracts', {
        'id': contract_id, 'job_id': job_norkevin, 'lead_id': lead_astral,
        'estado': 'Pendiente', 'tenant_id': ASTRAL,
    })

    r = auth_client.post(f'/api/contracts/{contract_id}/send', json={
        'subject': 'x', 'body': 'y',
    })
    assert r.status_code == 200
    data = r.get_json()
    assert data['delivery_status'] == 'blocked'
    assert 'no pertenece a esta empresa' in (data.get('mail_warning') or '')

    pendiente = app_module.store.get('pending_emails', data['mail_id'])
    assert pendiente['status'] == 'blocked'
    assert NORKEVIN not in (pendiente.get('blocked_reason') or ''), 'no debe revelar de que empresa es'


def test_pago_send_blocks_when_client_belongs_to_other_tenant(auth_client):
    """Mismo caso que el contrato, pero para facturas: el pago es de
    Astral, el cliente al que dice pertenecer es de Norkevin."""
    import app as app_module

    cliente_norkevin = 'client-ajeno-' + uuid.uuid4().hex[:6]
    app_module.store.upsert('clients', {
        'id': cliente_norkevin, 'first_name': 'Ajeno', 'last_name': 'Norkevin',
        'email': 'ajeno@example.com', 'tenant_id': NORKEVIN,
    })
    pay_id = 'pay-ajeno-' + uuid.uuid4().hex[:6]
    app_module.store.upsert('payments', {
        'id': pay_id, 'client_id': cliente_norkevin, 'amount': 500.0,
        'status': 'Pendiente', 'due_date': '2027-01-01', 'concepto': 'Cuota',
        'tenant_id': ASTRAL,
    })

    r = auth_client.post(f'/api/payments/{pay_id}/send', json={'to_email': 'ajeno@example.com'})
    assert r.status_code == 200
    data = r.get_json()
    assert data['delivery_status'] == 'blocked'
    pendiente = app_module.store.get('pending_emails', data['mail_id'])
    assert pendiente['status'] == 'blocked'
    assert NORKEVIN not in (pendiente.get('blocked_reason') or ''), 'no debe revelar de que empresa es'


# ============================================================
# 3. Doble click / doble disparo no duplica el pendiente
# ============================================================

def test_double_click_on_invoice_send_does_not_create_two_pending(auth_client):
    import app as app_module

    suffix = uuid.uuid4().hex[:6]
    client_id, job_id = _make_job_with_client(app_module, ASTRAL, suffix, email='dclick@example.com')
    pay_id = f'pay-dclick-{suffix}'
    app_module.store.upsert('payments', {
        'id': pay_id, 'client_id': client_id, 'job_id': job_id,
        'invoice_id': f'INV-DCLICK-{suffix}', 'amount': 2000.0, 'status': 'Pendiente',
        'due_date': '2027-01-01', 'concepto': 'Cuota', 'tenant_id': ASTRAL,
    })

    r1 = auth_client.post(f'/api/payments/{pay_id}/send', json={})
    r2 = auth_client.post(f'/api/payments/{pay_id}/send', json={})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.get_json()['mail_id'] == r2.get_json()['mail_id'], \
        'el segundo click dentro del mismo minuto debe devolver el MISMO pendiente'

    encolados = _pending_by_prefix(app_module, f"pago:{pay_id}:invoice:")
    assert len(encolados) == 1, 'no debe haber quedado un segundo pendiente identico'


def test_workflow_step_double_submit_reuses_same_pending(auth_client):
    """api_workflow_step marca el step DONE sin revisar si ya estaba
    hecho -- la unica red de seguridad contra un doble-submit real (doble
    click, request duplicado del navegador) es la idempotency_key dentro
    de queue_email(). 'envio_paquetes' se elige porque ya lo usa
    test_workflow_editor_connected.py::test_manual_lead_step_uses_configured_workflow_template,
    que confirma que trae un email_template_id por defecto sin necesitar
    configuracion extra en el entorno de pruebas."""
    import app as app_module

    r0 = auth_client.post('/api/leads/nuevo', json={
        'nombre': 'Doble', 'apellido': 'Step', 'email': 'dstep@example.com',
        'pais': 'Guatemala', 'fecha_boda': '2027-06-01',
    })
    assert r0.status_code == 200
    lead_id = r0.get_json()['lead_id']

    payload = {'lead_id': lead_id, 'step_id': 'envio_paquetes'}
    r1 = auth_client.post('/api/workflow/step', json=payload)
    r2 = auth_client.post('/api/workflow/step', json=payload)
    assert r1.status_code == 200, r1.get_json()
    assert r2.status_code == 200, r2.get_json()

    encolados = _pending_by_prefix(app_module, f'leadstep:{lead_id}:envio_paquetes')
    assert len(encolados) == 1, 'dos disparos seguidos del mismo step no deben apilar dos pendientes'


def test_two_manual_reminder_clicks_same_day_share_one_pending(auth_client):
    """api_payment_send_reminder no tiene throttle propio (a proposito: es
    el boton 'ahora mismo', se salta el gap de 5 dias que si respeta el
    scheduler automatico) -- lo unico que evita que dos clicks el mismo
    dia apilen dos pendientes identicos es la idempotency_key por
    pago+fecha, compartida ademas con check_and_send_payment_reminders
    para que el aviso automatico del mismo dia tampoco duplique."""
    import app as app_module

    suffix = uuid.uuid4().hex[:6]
    client_id, job_id = _make_job_with_client(app_module, ASTRAL, suffix, email='remind@example.com')
    pay_id = f'pay-remind-{suffix}'
    due = (date.today() + timedelta(days=3)).isoformat()
    app_module.store.upsert('payments', {
        'id': pay_id, 'client_id': client_id, 'job_id': job_id,
        'invoice_id': f'INV-REMIND-{suffix}', 'amount': 1500.0, 'status': 'Pendiente',
        'due_date': due, 'concepto': 'Cuota', 'tenant_id': ASTRAL,
    })

    r1 = auth_client.post(f'/api/payments/{pay_id}/send-reminder', json={})
    r2 = auth_client.post(f'/api/payments/{pay_id}/send-reminder', json={})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.get_json()['mail_id'] == r2.get_json()['mail_id']

    encolados = _pending_by_prefix(app_module, f'pago:{pay_id}:reminder:')
    assert len(encolados) == 1, 'dos clicks manuales el mismo dia no deben duplicar el recordatorio'


# ============================================================
# 4. Ramiro Cruz tan aislado como Astral/Norkevin
# ============================================================

def test_ramiro_pending_emails_isolated_from_astral_and_norkevin(client):
    import app as app_module

    login_as_tenant(client, RAMIRO, email='ramiro-wiring@example.com')
    with _ctx(app_module, RAMIRO):
        app_module.store.upsert('jobs', {
            'id': 'job-ramiro-wiring', 'nombre': 'Boda Ramiro', 'tenant_id': RAMIRO,
        })
        pendiente_ramiro = app_module.get_tracker().queue_email(
            'cliente@example.com', 'Solo Ramiro', 'cuerpo',
            job_id='job-ramiro-wiring', tenant_id=RAMIRO, source='test',
        )
        assert pendiente_ramiro['status'] == 'pending'

    for otro in (ASTRAL, NORKEVIN):
        with _ctx(app_module, otro):
            visibles = {p['id'] for p in app_module.store.list('pending_emails')}
            assert pendiente_ramiro['id'] not in visibles, \
                f'{otro} no debe ver el pendiente de Ramiro'

    with _ctx(app_module, RAMIRO):
        visibles_propio = {p['id'] for p in app_module.store.list('pending_emails')}
        assert pendiente_ramiro['id'] in visibles_propio


# ============================================================
# 5. Kill switch: prevalece incluso para un pendiente ya aprobado
# ============================================================

def test_kill_switch_prevails_even_through_full_approval_cycle(monkeypatch):
    """A diferencia de los tests con el fixture `client` (que reemplaza
    send_email por un fake que siempre 'entrega'), este test usa el
    send_email REAL de src/email_delivery.py -- para probar que
    approve_and_send() no tiene ningun atajo que se salte
    outbound_email_enabled(). Sin OUTBOUND_EMAIL_ENABLED=1 explicito
    (ausente por default en pytest_configure), ni un pendiente 100%
    aprobado y sin ningun problema de identidad debe terminar 'sent'."""
    import app as app_module

    monkeypatch.delenv('OUTBOUND_EMAIL_ENABLED', raising=False)
    monkeypatch.delenv('DISABLE_OUTBOUND_EMAIL', raising=False)

    with _ctx(app_module, ASTRAL):
        app_module.store.upsert('jobs', {
            'id': 'job-killswitch-wiring', 'nombre': 'Boda Killswitch', 'tenant_id': ASTRAL,
        })
        pendiente = app_module.get_tracker().queue_email(
            'cliente@example.com', 'Prueba kill switch', 'cuerpo',
            job_id='job-killswitch-wiring', tenant_id=ASTRAL, source='test',
        )
        assert pendiente['status'] == 'pending'

        resultado = app_module.get_tracker().approve_and_send(pendiente['id'], sender_tenant_id=ASTRAL)

    assert resultado['ok'] is False, 'sin el kill switch en 1, nada deberia poder marcarse enviado'
    assert resultado['pendiente']['status'] == 'failed', (
        'un bloqueo por kill switch se registra como FALLO (problema de '
        'infraestructura), no como BLOQUEADO (problema de seguridad de cuenta) -- '
        'ver la distincion explicita en mail_tracker.py'
    )


# ============================================================
# 6. Audit trail completo a traves de un endpoint real
# ============================================================

def test_audit_trail_end_to_end_through_a_real_endpoint(auth_client):
    import app as app_module

    suffix = uuid.uuid4().hex[:6]
    client_id, job_id = _make_job_with_client(app_module, ASTRAL, suffix, email='audit@example.com')
    pay_id = f'pay-audit-{suffix}'
    app_module.store.upsert('payments', {
        'id': pay_id, 'client_id': client_id, 'job_id': job_id,
        'invoice_id': f'INV-AUDIT-{suffix}', 'amount': 800.0, 'status': 'Pendiente',
        'due_date': '2027-01-01', 'concepto': 'Cuota', 'tenant_id': ASTRAL,
    })

    r = auth_client.post(f'/api/payments/{pay_id}/send', json={})
    mail_id = r.get_json()['mail_id']

    r2 = auth_client.post(f'/api/pending-emails/{mail_id}/send', json={})
    assert r2.status_code == 200
    assert r2.get_json()['email']['estado'] == 'sent'

    pendiente = app_module.store.get('pending_emails', mail_id)
    historial_estados = [h['a'] for h in pendiente['historial']]
    assert historial_estados == ['sending', 'sent'], (
        f'la secuencia completa debe quedar registrada, no solo el ultimo estado: {historial_estados}'
    )
    assert all(h.get('actor') for h in pendiente['historial']), \
        'cada paso del historial debe decir quien lo hizo'
