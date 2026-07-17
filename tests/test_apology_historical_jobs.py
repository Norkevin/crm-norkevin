"""Kevin: 'hay que mandar un disculpa a los correos que mando' -- despues de
que _auto_fire_due_job_steps mando correos reales de contrato/cuestionario a
clientes reales por jobs historicos importados de Studio Ninja (ver
test_stop_historical_job_emails.py). Antes de mandar nada hace falta saber
EXACTAMENTE a quien le llego de verdad (no lo que quedo en el outbox local
por Gmail desconectado, eso nunca salio)."""
import uuid


def _log_mail_for_job(app_module, job_id, to_email, delivered=True, status='sent'):
    from src.mail_tracker import get_tracker
    tracker = get_tracker()
    entry = {
        'id': 'mail-' + uuid.uuid4().hex[:8],
        'to': to_email,
        'subject': 'Firma tu contrato',
        'body': 'hola',
        'job_id': job_id,
        'lead_id': None,
        'status': status,
        'sent_at': '2026-07-15T10:00:00',
        'delivery_provider': 'gmail_api' if delivered else 'local_outbox',
        'tenant_id': 'tenant-norkevin',
    }
    app_module.store.upsert('mail_log', entry)
    return entry


def _make_boda_sn_job(app_module, suffix, nombre='Boda Historica'):
    job_id = f'boda-sn-{suffix}'
    app_module.store.upsert('jobs', {'id': job_id, 'nombre': nombre, 'tenant_id': 'tenant-norkevin'})
    return job_id


def test_mail_log_lists_only_boda_sn_jobs(auth_client):
    import app as app_module
    suffix = uuid.uuid4().hex[:6]
    job_id = _make_boda_sn_job(app_module, suffix)
    other_job_id = f'job-normal-{suffix}'
    app_module.store.upsert('jobs', {'id': other_job_id, 'nombre': 'Boda Normal', 'tenant_id': 'tenant-norkevin'})

    _log_mail_for_job(app_module, job_id, 'real1@example.com')
    _log_mail_for_job(app_module, other_job_id, 'notrelevant@example.com')

    resp = auth_client.get('/api/admin/historical-job-mail-log')
    assert resp.status_code == 200
    tos = [r['to'] for r in resp.get_json()['recipients']]
    assert 'real1@example.com' in tos
    assert 'notrelevant@example.com' not in tos


def test_mail_log_marks_local_outbox_as_not_delivered(auth_client):
    import app as app_module
    suffix = uuid.uuid4().hex[:6]
    job_id = _make_boda_sn_job(app_module, suffix)
    _log_mail_for_job(app_module, job_id, 'fake@example.com', delivered=False)

    resp = auth_client.get('/api/admin/historical-job-mail-log')
    row = next(r for r in resp.get_json()['recipients'] if r['to'] == 'fake@example.com')
    assert row['delivered'] is False


def test_send_apology_requires_confirm_keyword(auth_client):
    resp = auth_client.post('/api/admin/send-apology-historical-jobs', json={'subject': 'x', 'body': 'y'})
    assert resp.status_code == 400


def test_send_apology_requires_subject_and_body(auth_client):
    resp = auth_client.post('/api/admin/send-apology-historical-jobs', json={'confirm': 'DISCULPA'})
    assert resp.status_code == 400


def test_send_apology_reaches_only_real_delivered_recipients(auth_client):
    import app as app_module
    suffix = uuid.uuid4().hex[:6]
    job_id = _make_boda_sn_job(app_module, suffix)
    _log_mail_for_job(app_module, job_id, 'real2@example.com', delivered=True)
    _log_mail_for_job(app_module, job_id, 'notreal@example.com', delivered=False)

    resp = auth_client.post('/api/admin/send-apology-historical-jobs', json={
        'confirm': 'DISCULPA', 'subject': 'Disculpa', 'body': 'Perdon por el correo anterior',
    })
    assert resp.status_code == 200
    sent_to = [s['to'] for s in resp.get_json()['sent']]
    assert 'real2@example.com' in sent_to
    assert 'notreal@example.com' not in sent_to


def test_send_apology_does_not_touch_non_studio_ninja_jobs(auth_client):
    import app as app_module
    suffix = uuid.uuid4().hex[:6]
    normal_job_id = f'job-normal-{suffix}'
    app_module.store.upsert('jobs', {'id': normal_job_id, 'nombre': 'Boda Normal', 'tenant_id': 'tenant-norkevin'})
    _log_mail_for_job(app_module, normal_job_id, 'shouldnotget@example.com', delivered=True)

    resp = auth_client.post('/api/admin/send-apology-historical-jobs', json={
        'confirm': 'DISCULPA', 'subject': 'Disculpa', 'body': 'Perdon',
    })
    sent_to = [s['to'] for s in resp.get_json()['sent']]
    assert 'shouldnotget@example.com' not in sent_to
