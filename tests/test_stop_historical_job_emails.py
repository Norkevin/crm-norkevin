"""Kevin: 'porque se puso a enviar correos?' -- confirmo que eran pasos de
workflow (contrato/cuestionario) que se disparaban solos para bodas viejas
importadas de Studio Ninja. La importacion en si ya se corrigio para dejar
todo SKIPPED desde el momento en que crea el job (ver
test_studio_ninja_import.py), pero eso no arregla los jobs que YA estaban
creados en produccion ANTES de ese fix -- para esos hace falta este endpoint
de remediacion de una sola vez."""
import uuid


def _make_historical_job_with_pending_steps(app_module, slug):
    """Simula exactamente el estado roto: un job 'boda-sn-*' con una
    instancia de workflow creada al vuelo (como hacia _auto_fire_due_job_steps
    antes del fix) con todos los steps en PENDING."""
    from src.workflow.models import StepStatus

    job_id = f'boda-sn-{slug}'
    client_id = f'client-sn-{slug}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Historico', 'last_name': 'Test',
        'email': f'{slug}@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Historica Test',
        'boda_date': '2025-01-15', 'created': '2024-10-01',
        'status': 'Confirmado', 'tenant_id': 'tenant-norkevin',
    })
    job = app_module.get_job(job_id)
    instance = app_module.workflow_engine.start_workflow(
        workflow=app_module.PRODUCTION_WORKFLOW(),
        subject_type='job', subject_id=job_id, subject_name=job['nombre'],
        trigger_event='job.created', auto_execute_first=False,
    )
    for step in app_module.PRODUCTION_WORKFLOW().steps:
        instance.step_states[step.id] = StepStatus.PENDING
    app_module.workflow_engine._save_to_storage()
    return job_id


def test_requires_confirm_keyword(auth_client):
    resp = auth_client.post('/api/admin/stop-historical-job-emails', json={})
    assert resp.status_code == 400
    assert resp.get_json()['ok'] is False


def test_skips_all_pending_steps_for_boda_sn_jobs(auth_client):
    import app as app_module
    from src.workflow.models import StepStatus

    job_id = _make_historical_job_with_pending_steps(app_module, uuid.uuid4().hex[:6])

    resp = auth_client.post('/api/admin/stop-historical-job-emails', json={'confirm': 'PARAR'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert job_id in data['fixed']

    instances = app_module.workflow_engine.list_instances(subject_id=job_id, subject_type='job')
    assert instances
    assert all(state == StepStatus.SKIPPED for state in instances[0].step_states.values())


def test_no_longer_auto_fires_after_fix(auth_client):
    """_auto_fire_due_job_steps() (el que realmente manda los correos, cada
    6h en produccion) lee el status via compute_workflow_steps_for_job(),
    no via get_due_steps() del engine generico -- ese es el camino real que
    hay que blindar."""
    import app as app_module

    job_id = _make_historical_job_with_pending_steps(app_module, uuid.uuid4().hex[:6])
    job = app_module.get_job(job_id)

    steps_before, _, _ = app_module.compute_workflow_steps_for_job(job)
    assert any(s['status'] == 'pending' for s in steps_before), 'precondicion: debia estar roto antes del fix'

    auth_client.post('/api/admin/stop-historical-job-emails', json={'confirm': 'PARAR'})

    steps_after, _, _ = app_module.compute_workflow_steps_for_job(job)
    assert all(s['status'] == 'skipped' for s in steps_after), (
        'ningun step de un job historico debe seguir en pending, o _auto_fire_due_job_steps lo va a disparar'
    )


def test_does_not_touch_jobs_that_are_not_studio_ninja_imports(auth_client):
    import app as app_module
    from src.workflow.models import StepStatus

    normal_job_id = f'job-normal-{uuid.uuid4().hex[:6]}'
    client_id = f'client-normal-{uuid.uuid4().hex[:6]}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Normal', 'last_name': 'Test',
        'email': 'normal@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': normal_job_id, 'client_id': client_id, 'nombre': 'Boda Normal',
        'tenant_id': 'tenant-norkevin',
    })
    instance = app_module.workflow_engine.start_workflow(
        workflow=app_module.PRODUCTION_WORKFLOW(),
        subject_type='job', subject_id=normal_job_id, subject_name='Boda Normal',
        trigger_event='job.created', auto_execute_first=False,
    )
    first_step_id = app_module.PRODUCTION_WORKFLOW().steps[0].id
    instance.step_states[first_step_id] = StepStatus.PENDING
    app_module.workflow_engine._save_to_storage()

    resp = auth_client.post('/api/admin/stop-historical-job-emails', json={'confirm': 'PARAR'})
    assert normal_job_id not in resp.get_json()['fixed']

    instances = app_module.workflow_engine.list_instances(subject_id=normal_job_id, subject_type='job')
    assert instances[0].step_states.get(first_step_id) == StepStatus.PENDING, (
        'un job que no viene del import de Studio Ninja no debe tocarse'
    )


def test_already_done_steps_stay_done_not_skipped(auth_client):
    import app as app_module
    from src.workflow.models import StepStatus

    job_id = _make_historical_job_with_pending_steps(app_module, uuid.uuid4().hex[:6])
    instances = app_module.workflow_engine.list_instances(subject_id=job_id, subject_type='job')
    first_step_id = app_module.PRODUCTION_WORKFLOW().steps[0].id
    instances[0].step_states[first_step_id] = StepStatus.DONE
    app_module.workflow_engine._save_to_storage()

    auth_client.post('/api/admin/stop-historical-job-emails', json={'confirm': 'PARAR'})

    instances = app_module.workflow_engine.list_instances(subject_id=job_id, subject_type='job')
    assert instances[0].step_states.get(first_step_id) == StepStatus.DONE, (
        'un step que ya se completo de verdad no debe reescribirse a SKIPPED'
    )
