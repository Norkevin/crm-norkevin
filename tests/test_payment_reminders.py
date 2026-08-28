"""Pruebas del texto de recordatorio de pago y la vista previa editable
(el flujo de 'Generar link de pago' -> preview -> enviar)."""


def test_reminder_email_mentions_amount_and_due_date(flask_app):
    import app as app_module

    pay = {'amount': 9833.34, 'due_date': '2026-09-11', 'concepto': 'Pago 3 de 3'}
    subject, body = app_module._payment_reminder_email_text(pay, client=None, job=None, payment_link=None)

    assert 'Q9,833.34' in body
    assert '2026-09-11' in body
    assert 'Transferencia bancaria' in body


def test_reminder_email_includes_payment_link_when_present(flask_app):
    import app as app_module

    pay = {'amount': 5000, 'due_date': '2026-08-01'}
    link = 'https://app.recurrente.com/checkout-session/ch_test123'
    subject, body = app_module._payment_reminder_email_text(pay, client=None, job=None, payment_link=link)

    assert link in body
    assert 'Pago en linea con tarjeta' in body


def test_reminder_email_omits_payment_link_when_absent(flask_app):
    import app as app_module

    pay = {'amount': 5000, 'due_date': '2026-08-01'}
    subject, body = app_module._payment_reminder_email_text(pay, client=None, job=None, payment_link=None)

    assert 'checkout-session' not in body


def test_reminder_preview_endpoint_requires_login(client):
    resp = client.get('/api/payments/pay-does-not-exist/reminder-preview')
    assert resp.status_code == 401


def test_reminder_preview_returns_404_for_unknown_payment(auth_client):
    resp = auth_client.get('/api/payments/pay-does-not-exist/reminder-preview')
    assert resp.status_code == 404


def test_send_reminder_respects_edited_text_instead_of_regenerating(auth_client, monkeypatch):
    """Si Kevin edito el mensaje en la vista previa antes de enviarlo, el
    correo que queda esperando aprobacion debe llevar EXACTAMENTE ese
    texto, no uno regenerado.

    STAGE 2 (agosto 2026): api_payment_send_reminder ya no llega a
    send_email() en este paso -- el mensaje editado se congela en
    pending_emails y espera aprobacion en /emails. El monkeypatch de
    send_email (para capturar el envio real) ya no aplica aca; lo que
    hay que revisar es el pendiente que quedo encolado."""
    import app as app_module

    payments = app_module.store.list('payments')
    pending = next((p for p in payments if p.get('status') in ('Pendiente', 'Late') and p.get('client_id')), None)
    assert pending, 'necesita al menos un payment pendiente con client_id en los datos de prueba'

    resp = auth_client.post(f'/api/payments/{pending["id"]}/send-reminder', json={
        'to_email': 'override@example.com',
        'subject': '[EDITADO] Asunto de prueba',
        'body': 'Cuerpo editado a mano por Kevin, no debe regenerarse.',
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True

    encolado = app_module.store.get('pending_emails', data['mail_id'])
    assert encolado['status'] == 'pending'
    assert encolado['to'] == 'override@example.com'
    assert encolado['subject'] == '[EDITADO] Asunto de prueba'
    assert encolado['body'] == 'Cuerpo editado a mano por Kevin, no debe regenerarse.'
