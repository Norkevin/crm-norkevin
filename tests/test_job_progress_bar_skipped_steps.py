"""Kevin (feedback de modo oscuro, punto 5): 'en Jobs hay elementos que
muestran Completado, pero la barra de progreso aparece vacia. Visualmente
es contradictorio'.

jobs_list() decide 'Completado' cuando no queda ningun step 'pending' (ver
app.py: pending = [s for s in steps if s['status']=='pending']). Pero un
step SALTADO (feature de los 3 puntos que agregue antes en esta sesion,
StepStatus.SKIPPED) tambien deja de ser 'pending' sin ser 'done' -- si
compute_workflow_steps_for_job() solo contaba 'done' para el %, saltar
steps hacia bajar el progreso mientras el texto ya decia 'Completado'."""
import uuid


def _make_job(app_module, suffix):
    client_id = f'client-progress-{suffix}'
    job_id = f'job-progress-{suffix}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Progress', 'last_name': 'Test',
        'email': f'{suffix}@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Progress Test',
        'tenant_id': 'tenant-norkevin',
    })
    return job_id


def test_skipping_all_steps_shows_full_progress_bar(auth_client):
    import app as app_module
    job_id = _make_job(app_module, uuid.uuid4().hex[:6])
    tmpl = app_module.PRODUCTION_WORKFLOW()

    for step in tmpl.steps:
        resp = auth_client.post(f'/api/jobs/{job_id}/steps/{step.id}/skip', json={})
        assert resp.status_code == 200

    job = app_module.get_job(job_id)
    steps, progress, _ = app_module.compute_workflow_steps_for_job(job)
    assert progress == 100, 'si todos los steps estan resueltos (saltados), la barra debe verse llena'
    assert all(s['status'] == 'skipped' for s in steps)


def test_mix_of_done_and_skipped_gives_partial_progress_matching_completado_text(auth_client):
    import app as app_module
    job_id = _make_job(app_module, uuid.uuid4().hex[:6])
    tmpl = app_module.PRODUCTION_WORKFLOW()

    auth_client.post('/api/jobs/' + job_id + '/trigger-step', json={'step_id': tmpl.steps[0].id})
    for step in tmpl.steps[1:]:
        auth_client.post(f'/api/jobs/{job_id}/steps/{step.id}/skip', json={})

    job = app_module.get_job(job_id)
    steps, progress, _ = app_module.compute_workflow_steps_for_job(job)
    pending = [s for s in steps if s['status'] == 'pending']
    assert not pending, 'no debe quedar ningun step pendiente (uno done, el resto skipped)'
    assert progress == 100, 'done+skipped deben sumar el 100% del progreso, igual que el texto Completado'


def test_jobs_page_shows_full_bar_when_next_task_is_completado(auth_client):
    import app as app_module
    job_id = _make_job(app_module, uuid.uuid4().hex[:6])
    tmpl = app_module.PRODUCTION_WORKFLOW()
    for step in tmpl.steps:
        auth_client.post(f'/api/jobs/{job_id}/steps/{step.id}/skip', json={})

    resp = auth_client.get('/jobs')
    html = resp.get_data(as_text=True)
    marker = f"goJob(event, '{job_id}')"
    start = html.index(marker)
    end = html.index('</tr>', start)
    row = html[start:end]
    assert 'Completado' in row
    assert 'width: 100%' in row, 'la barra debe mostrarse llena cuando el next task dice Completado'
