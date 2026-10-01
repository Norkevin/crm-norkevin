"""The job summary excludes the lead-conversion marker and finished work."""
import re
import pytest


@pytest.mark.parametrize('steps,tasks,expected', [
    ([{'id': 'job_accepted', 'name': 'Trabajo aceptado', 'status': 'pending'},
      {'id': 'finished', 'name': 'Completado', 'status': 'done'},
      {'id': 'omit', 'name': 'Omitido', 'status': 'skipped'},
      {'id': 'mail', 'name': 'Reserva confirmada', 'status': 'queued'}], [], 'Reserva confirmada'),
    ([], [{'id': 'civil', 'name': 'Boda civil', 'status': 'pending'}], 'Boda civil'),
    ([], [{'id': 'civil', 'name': 'Boda civil', 'status': 'done'}], 'Todo al día'),
])
def test_job_next_action(auth_client, monkeypatch, steps, tasks, expected):
    import app as a
    a.store.upsert('jobs', {
        'id': 'job-hierarchy', 'nombre': 'Prueba de jerarquía',
        'tenant_id': 'tenant-norkevin', 'manual_workflow_tasks': tasks,
    })
    monkeypatch.setattr(a, 'compute_workflow_steps_for_job', lambda job: (steps, 0, 'Producción'))
    response = auth_client.get('/jobs/job-hierarchy')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert re.search(r'<h2 id="job-next-title">(.*?)</h2>', html).group(1) == expected
    if expected == 'Reserva confirmada':
        summary = html.split('aria-labelledby="job-next-title"', 1)[1].split('</section>', 1)[0]
        assert 'href="/emails"' in summary
        assert 'Preparación automática' not in summary


@pytest.mark.parametrize('days,expected', [(10, 'proxima'), (0, 'hoy'), (-10, 'por_cobrar')])
def test_raw_job_date_uses_same_status_as_enriched_job(days, expected):
    import app as a
    from datetime import datetime, timedelta
    job = {'boda_date': (datetime.now().date() + timedelta(days=days)).isoformat()}
    payments = [{'amount': 1000, 'status': 'Pendiente'}]
    assert a._job_estado_label(job, payments)[2] == expected
    assert a._job_estado_label(job, payments) == a._job_estado_label(dict(job, dias_restantes=days), payments)
