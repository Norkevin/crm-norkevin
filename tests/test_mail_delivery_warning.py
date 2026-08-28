"""Kevin: 'le di a enviar cuestionario y no le llego nada al cliente' --
root cause: send_email() en src/email_delivery.py cae en silencio a
_send_local() (escribe en data/mail_outbox.json, un archivo que nadie lee)
cada vez que Gmail no esta conectado y no hay SMTP/Resend configurado,
pero devuelve ok=True -- asi que el backend y el frontend mostraban
'enviado con exito' sin que el correo llegara nunca al cliente. Estos
tests fuerzan ese mismo fallback (monkeypatch a nivel de src.email_delivery
para imitar exactamente lo que pasa en produccion sin Gmail conectado) y
confirman que la API ahora expone 'mail_warning' en vez de fingir exito."""
from unittest.mock import patch

from src.email_delivery import DeliveryResult


def _local_outbox_fallback(to_email, subject, body='', **kwargs):
    return DeliveryResult(ok=True, provider='local_outbox', message_id='outbox-fake', mode='test')


def test_job_questionnaire_surfaces_warning_when_gmail_disconnected(auth_client):
    """STAGE 2 (agosto 2026): el correo ya no llega hasta send_email() al
    momento de crear el cuestionario -- queda encolado en pending_emails
    esperando aprobacion en /emails, sin importar si Gmail esta conectado o
    no. El patch de send_email (el caso original de este test, 'Gmail
    desconectado cae a outbox local en silencio') ya no se alcanza desde
    aca; ese escenario lo sigue cubriendo approve_and_send() y los tests de
    mail_tracker. Lo que este test verifica ahora es que la API avisa
    igual de claro que antes: nunca 'ok:true' fingiendo que ya salio."""
    import app as app_module
    import uuid

    job_id = 'job-mailwarn-' + uuid.uuid4().hex[:6]
    client_id = 'client-mailwarn-' + uuid.uuid4().hex[:6]
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Mail', 'last_name': 'Warn',
        'email': 'mailwarn@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Mail Warn',
        'tenant_id': 'tenant-norkevin',
    })

    resp = auth_client.post(f'/api/jobs/{job_id}/questionnaires', json={
        'name': 'Cuestionario', 'send_email': True,
    })

    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['mail_id'], 'pending_emails si debe tener una entrada (se encolo, aunque no se entrego)'
    assert data['mail_warning'], 'debe avisar que NO se entrego de verdad'
    assert 'cola de aprobacion' in data['mail_warning']

    pendiente = app_module.store.get('pending_emails', data['mail_id'])
    assert pendiente['status'] == 'pending'


def test_job_send_email_surfaces_warning_when_gmail_disconnected(auth_client):
    """Ver docstring del test anterior: STAGE 2 encola siempre, asi que
    mail_warning avisa 'en cola' en vez de reflejar el estado real de
    Gmail -- el patch de send_email ya no aplica aca."""
    import app as app_module
    import uuid

    job_id = 'job-mailwarn2-' + uuid.uuid4().hex[:6]
    client_id = 'client-mailwarn2-' + uuid.uuid4().hex[:6]
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Mail', 'last_name': 'Warn2',
        'email': 'mailwarn2@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Mail Warn 2',
        'tenant_id': 'tenant-norkevin',
    })

    resp = auth_client.post(f'/api/jobs/{job_id}/send-email', json={
        'subject': 'Hola', 'body': 'Mensaje',
    })

    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['mail_warning']


def test_questionnaire_with_no_client_email_warns_instead_of_silently_succeeding(auth_client):
    """Antes: si el cliente no tenia email, mail_id quedaba None y la API
    igual devolvia ok:True sin ninguna pista de que no se mando nada."""
    import app as app_module
    import uuid

    job_id = 'job-noemail-' + uuid.uuid4().hex[:6]
    app_module.store.upsert('jobs', {
        'id': job_id, 'nombre': 'Boda Sin Email', 'tenant_id': 'tenant-norkevin',
    })

    resp = auth_client.post(f'/api/jobs/{job_id}/questionnaires', json={
        'name': 'Cuestionario', 'send_email': True,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['mail_id'] is None
    assert data['mail_warning']
    assert 'email registrado' in data['mail_warning']


def test_mail_delivery_warning_helper_ignores_successful_gmail_send():
    import app as app_module
    entry = {'status': 'sent', 'delivery_provider': 'gmail', 'delivery_error': ''}
    assert app_module._mail_delivery_warning(entry) is None


def test_mail_delivery_warning_helper_flags_failed_delivery():
    import app as app_module
    entry = {'status': 'failed', 'delivery_provider': 'gmail', 'delivery_error': 'token expired'}
    warning = app_module._mail_delivery_warning(entry)
    assert warning and 'token expired' in warning
