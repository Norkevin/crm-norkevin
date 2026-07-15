"""Kevin: 'al crear el job creo el cuestionario deberia estar creado y que
se envie cuando el workflow lo diga'. Antes: (1) nada creaba el cuestionario
hasta que alguien entrara manualmente al Job, y (2) NADA disparaba un step
de Job por fecha -- se quedaba 'pending' para siempre. Ademas, el calculo de
fecha para steps 'antes de la boda' usaba la fecha de creacion del job en
vez de boda_date, asi que 'Cuestionario: 1 mes antes de la boda' se
calculaba mal (casi inmediato) para bodas lejanas."""
from datetime import datetime, timedelta
from unittest.mock import patch

from src.email_delivery import DeliveryResult


def _ok_send(to_email, subject, body='', **kwargs):
    return DeliveryResult(ok=True, provider='test', message_id='test-msg', mode='test')


def test_job_creation_pre_creates_a_draft_questionnaire(auth_client):
    import app as app_module
    import uuid

    lead_id = 'lead-autofire-' + uuid.uuid4().hex[:6]
    app_module.upsert_lead({
        'id': lead_id, 'nombre': 'Auto Fire', 'email': 'autofire@example.com',
        'status': 'Nuevo', 'tenant_id': 'tenant-norkevin', 'fecha_boda': '2028-01-31',
    })
    lead = app_module.get_lead(lead_id)

    with patch('src.mail_tracker.send_email', side_effect=_ok_send):
        result = app_module._convert_lead_to_job(lead, quote=None, status='Confirmado', create_payments=False)

    job_id = result['job']['id']
    questionnaires = [q for q in app_module.store.list('questionnaires') if q.get('job_id') == job_id]
    assert len(questionnaires) == 1
    assert questionnaires[0]['status'] == 'Draft'


def test_before_boda_step_schedules_relative_to_wedding_not_job_creation(auth_client):
    """El bug real: boda muy en el futuro (tipico en este negocio), el step
    'Cuestionario: 1 month before boda' se calculaba ~1 mes despues de HOY
    en vez de ~1 mes antes de la boda real."""
    import app as app_module

    job = {'id': 'job-sched-test', 'created': datetime.now().isoformat(), 'boda_date': '2028-06-15'}
    steps, _, _ = app_module.compute_workflow_steps_for_job(job)
    cuestionario = next(s for s in steps if s['id'] == 'cuestionario_cliente')

    scheduled = datetime.fromisoformat(cuestionario['scheduled'])
    boda = datetime(2028, 6, 15)
    # Deberia caer cerca de 1 mes (30 dias) antes de la boda, no cerca de hoy.
    assert abs((boda - scheduled).days - 30) <= 2
    assert scheduled > datetime.now() + timedelta(days=300), (
        'con una boda tan lejana, el step NO deberia calcularse para ~ahora'
    )


def test_auto_fire_sends_due_questionnaire_step_for_real(auth_client):
    import app as app_module
    import uuid

    job_id = 'job-fire-' + uuid.uuid4().hex[:6]
    client_id = 'client-fire-' + uuid.uuid4().hex[:6]
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Fire', 'last_name': 'Test',
        'email': 'firetest@example.com', 'tenant_id': 'tenant-norkevin',
    })
    # Boda ya pasada -> el step 'antes de la boda' cae claramente en el pasado -> debe dispararse ya.
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Fire Test',
        'boda_date': '2020-01-01', 'status': 'Confirmado',
        'created': '2019-01-01T00:00:00', 'tenant_id': 'tenant-norkevin',
    })
    # Pre-crear el draft como haria _convert_lead_to_job.
    app_module.store.upsert('questionnaires', {
        'id': 'questionnaire-predraft', 'job_id': job_id, 'client_id': client_id,
        'name': 'Cuestionario de Bodas Generico', 'status': 'Draft',
        'questions': app_module.QUESTIONNAIRE_QUESTIONS, 'created': '2019-01-01',
        'tenant_id': 'tenant-norkevin',
    })

    with patch('src.mail_tracker.send_email', side_effect=_ok_send):
        fired = app_module._auto_fire_due_job_steps()

    fired_step_ids = [step_id for (jid, step_id) in fired if jid == job_id]
    assert 'cuestionario_cliente' in fired_step_ids

    # No debe haber creado un SEGUNDO cuestionario -- reutiliza el draft.
    questionnaires = [q for q in app_module.store.list('questionnaires') if q.get('job_id') == job_id]
    assert len(questionnaires) == 1
    assert questionnaires[0]['status'] == 'Sent'

    mail = [m for m in app_module.store.list('mail_log') if m.get('job_id') == job_id]
    assert mail, 'debe haber quedado un correo real registrado en mail_log'


def test_auto_fire_does_not_mark_done_when_delivery_fails(auth_client):
    """Si Gmail esta desconectado (fallback a local_outbox), el step NO debe
    marcarse 'done' -- debe reintentarse en el proximo ciclo."""
    import app as app_module
    import uuid

    job_id = 'job-fire-fail-' + uuid.uuid4().hex[:6]
    client_id = 'client-fire-fail-' + uuid.uuid4().hex[:6]
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Fail', 'last_name': 'Test',
        'email': 'failtest@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Fire Fail',
        'boda_date': '2020-01-01', 'status': 'Confirmado',
        'created': '2019-01-01T00:00:00', 'tenant_id': 'tenant-norkevin',
    })

    def _local_outbox_fallback(to_email, subject, body='', **kwargs):
        return DeliveryResult(ok=True, provider='local_outbox', message_id='outbox-fake', mode='test')

    with patch('src.mail_tracker.send_email', side_effect=_local_outbox_fallback):
        fired = app_module._auto_fire_due_job_steps()

    fired_step_ids = [step_id for (jid, step_id) in fired if jid == job_id]
    assert 'cuestionario_cliente' not in fired_step_ids

    instances = app_module.workflow_engine.list_instances(subject_id=job_id, subject_type='job')
    if instances:
        assert instances[0].step_states.get('cuestionario_cliente') != app_module.StepStatus.DONE
