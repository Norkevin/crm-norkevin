"""Kevin: 'ahora en todo el workflow me gustaria una opcion por cada paso,
al presionar en los 3 puntos... es util por si no quieres mandar contrato
por ejemplo' (referencia: menu de 3 puntos de Studio Ninja con Edit/
Duplicate/Delete/Show in Client Portal). Antes el boton de 3 puntos de cada
step solo disparaba el step de inmediato -- no habia forma de saltarlo.

get_due_steps() del engine (src/workflow/engine.py) ya ignora cualquier
step_state que no sea PENDING, asi que marcar un step como SKIPPED alcanza
para que nunca se dispare solo -- no hizo falta tocar el engine."""
import uuid


def _make_job(app_module, suffix):
    client_id = f'client-stepskip-{suffix}'
    job_id = f'job-stepskip-{suffix}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Skip', 'last_name': 'Test',
        'email': 'skiptest@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Skip Test',
        'tenant_id': 'tenant-norkevin',
    })
    return job_id


def test_skip_step_marks_it_skipped_and_hides_it_from_due_steps(auth_client):
    import app as app_module
    from src.workflow.models import StepStatus

    job_id = _make_job(app_module, uuid.uuid4().hex[:6])
    tmpl = app_module.PRODUCTION_WORKFLOW()
    step_id = next(s.id for s in tmpl.steps if 'contrato' in s.name.lower() or 'firma' in s.id.lower())

    resp = auth_client.post(f'/api/jobs/{job_id}/steps/{step_id}/skip', json={})
    assert resp.status_code == 200

    instances = app_module.workflow_engine.list_instances(subject_id=job_id, subject_type='job')
    assert instances
    assert instances[0].step_states.get(step_id) == StepStatus.SKIPPED

    due = app_module.workflow_engine.get_due_steps()
    assert not any(inst.subject_id == job_id and s.id == step_id for inst, s in due), (
        'un step SKIPPED no debe volver a aparecer como pendiente de disparar'
    )


def test_job_detail_shows_skipped_step_as_omitido(auth_client):
    import app as app_module
    job_id = _make_job(app_module, uuid.uuid4().hex[:6])
    tmpl = app_module.PRODUCTION_WORKFLOW()
    step_id = tmpl.steps[1].id  # cualquier step que no sea 'job_accepted'

    auth_client.post(f'/api/jobs/{job_id}/steps/{step_id}/skip', json={})

    resp = auth_client.get(f'/jobs/{job_id}')
    html = resp.get_data(as_text=True)
    assert 'Omitido' in html
    assert 'workflow-step skipped' in html or 'skipped' in html


def test_unskip_step_returns_it_to_pending(auth_client):
    import app as app_module
    from src.workflow.models import StepStatus

    job_id = _make_job(app_module, uuid.uuid4().hex[:6])
    tmpl = app_module.PRODUCTION_WORKFLOW()
    step_id = tmpl.steps[1].id

    auth_client.post(f'/api/jobs/{job_id}/steps/{step_id}/skip', json={})
    resp = auth_client.post(f'/api/jobs/{job_id}/steps/{step_id}/unskip', json={})
    assert resp.status_code == 200

    instances = app_module.workflow_engine.list_instances(subject_id=job_id, subject_type='job')
    assert instances[0].step_states.get(step_id) == StepStatus.PENDING


def test_cannot_skip_a_step_already_done(auth_client):
    import app as app_module
    job_id = _make_job(app_module, uuid.uuid4().hex[:6])
    tmpl = app_module.PRODUCTION_WORKFLOW()
    step_id = tmpl.steps[1].id

    auth_client.post('/api/jobs/' + job_id + '/trigger-step', json={'step_id': step_id})
    resp = auth_client.post(f'/api/jobs/{job_id}/steps/{step_id}/skip', json={})
    assert resp.status_code == 400


def test_editing_an_extra_shoot_updates_its_calendar_event_too(auth_client):
    import app as app_module
    job_id = _make_job(app_module, uuid.uuid4().hex[:6])

    create = auth_client.post(f'/api/jobs/{job_id}/workflow-task', json={
        'type': 'extra-event', 'name': 'Boda civil',
        'start_date': '2026-07-18', 'location': 'Hotel Atitlan',
    })
    task_id = create.get_json()['task']['id']
    event_id = create.get_json()['calendar_event']['id']

    update = auth_client.post(f'/api/jobs/{job_id}/workflow-task/{task_id}/update', json={
        'name': 'Boda civil (reprogramada)', 'start_date': '2026-08-01', 'location': 'Otro salon',
    })
    assert update.status_code == 200

    job = app_module.get_job(job_id)
    task = next(t for t in job['manual_workflow_tasks'] if t['id'] == task_id)
    assert task['name'] == 'Boda civil (reprogramada)'
    assert task['start_date'] == '2026-08-01'
    assert task['location'] == 'Otro salon'

    event = app_module.store.get('calendar', event_id)
    assert event['date'] == '2026-08-01'
    assert event['location'] == 'Otro salon'
    assert 'reprogramada' in event['title']


def test_deleting_an_extra_shoot_also_removes_its_calendar_event(auth_client):
    import app as app_module
    job_id = _make_job(app_module, uuid.uuid4().hex[:6])

    create = auth_client.post(f'/api/jobs/{job_id}/workflow-task', json={
        'type': 'extra-event', 'name': 'Save the Date',
        'start_date': '2026-09-01',
    })
    task_id = create.get_json()['task']['id']
    event_id = create.get_json()['calendar_event']['id']
    assert app_module.store.get('calendar', event_id) is not None

    resp = auth_client.post(f'/api/jobs/{job_id}/workflow-task/{task_id}/delete', json={})
    assert resp.status_code == 200

    job = app_module.get_job(job_id)
    assert not any(t['id'] == task_id for t in job.get('manual_workflow_tasks', []))
    assert app_module.store.get('calendar', event_id) is None


def test_toggle_portal_flips_the_flag(auth_client):
    import app as app_module
    job_id = _make_job(app_module, uuid.uuid4().hex[:6])

    create = auth_client.post(f'/api/jobs/{job_id}/workflow-task', json={
        'type': 'appointment', 'name': 'Reunion', 'start_date': '2026-10-01',
        'show_in_portal': False,
    })
    task_id = create.get_json()['task']['id']

    resp = auth_client.post(f'/api/jobs/{job_id}/workflow-task/{task_id}/toggle-portal', json={})
    assert resp.status_code == 200
    assert resp.get_json()['show_in_portal'] is True

    job = app_module.get_job(job_id)
    task = next(t for t in job['manual_workflow_tasks'] if t['id'] == task_id)
    assert task['show_in_portal'] is True
