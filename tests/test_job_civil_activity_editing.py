"""Civil activities stay editable on imported and native job details."""
import re
from html import unescape

import pytest


def _create_civil(auth_client, *, imported=True):
    import app as crm

    client = dict(id='client-civil-edit', first_name='Cliente', last_name='Sintético',
                  email='civil-edit@example.invalid', tenant_id='tenant-norkevin')
    job = dict(id='job-civil-edit', client_id=client['id'], nombre='Boda sintética',
               boda_date='2026-11-14', status='Confirmado', tenant_id='tenant-norkevin')
    if imported:
        job['studio_ninja_workflow'] = [dict(
            id='imported-confirm-details', name='Confirmar detalles', status='pending',
            source_stage='PRODUCTION', source_details='Tarea manual')]
    crm.store.upsert('clients', client)
    crm.store.upsert('jobs', job)
    response = auth_client.post(f'/api/jobs/{job["id"]}/workflow-task', json={
        'type': 'extra-event', 'name': 'Boda civil', 'start_date': '2026-10-25',
        'start_time': '10:00', 'end_time': '12:00', 'location': 'Lugar civil sintético',
    })
    assert response.status_code == 200
    return crm, job['id'], response.get_json()['task']


@pytest.mark.parametrize('imported', [True, False], ids=['studio-ninja', 'native'])
def test_civil_details_offer_edit_and_delete_for_the_source_task(auth_client, imported):
    _, job_id, task = _create_civil(auth_client, imported=imported)
    response = auth_client.get(f'/jobs/{job_id}')
    assert response.status_code == 200
    html = unescape(response.get_data(as_text=True))
    workflow = re.search(r'<aside\b[^>]*id="job-workflow".*?</aside>', html, re.S).group(0)
    secondary = re.search(r'<section\b[^>]*aria-label="Trabajos secundarios".*?</section>', workflow, re.S).group(0)
    manual_workflow = workflow.replace(secondary, '')

    assert '25 de octubre de 2026' in manual_workflow
    assert '10:00' in manual_workflow and '12:00' in manual_workflow
    assert 'Lugar civil sintético' in manual_workflow
    menus = [button for button in re.findall(r'<button\b.*?</button>', manual_workflow, re.S)
             if 'openManualTaskActions(' in button]
    assert len(menus) == 1
    assert 'aria-label="Opciones de Boda civil"' in menus[0]
    assert task['id'] in menus[0]

    buttons = re.findall(r'<button\b.*?</button>', secondary, re.S)
    edit = next(button for button in buttons if re.search(r'>\s*Editar\s*</button>', button))
    delete = next(button for button in buttons if re.search(r'>\s*Eliminar\s*</button>', button))
    assert 'openEditTaskForm(' in edit and task['id'] in edit
    assert 'confirmDeleteManualTask(' in delete and task['id'] in delete


def test_correcting_and_deleting_civil_updates_calendar_and_secondary_job(auth_client):
    from src.linked_coverages import linked_coverages

    crm, job_id, task = _create_civil(auth_client)
    event_id = task['calendar_event_id']
    secondary_id = 'secondary:' + event_id
    response = auth_client.post(f'/api/jobs/{job_id}/workflow-task/{task["id"]}/update', json={
        'name': task['name'], 'start_date': '2026-10-24', 'start_time': '10:00',
        'end_time': '12:00', 'location': task['location'],
    })
    assert response.status_code == 200
    job = crm.store.get('jobs', job_id)
    updated = next(item for item in job['manual_workflow_tasks'] if item['id'] == task['id'])
    event = crm.store.get('calendar', event_id)
    coverages = linked_coverages([job], crm.store.list('calendar'))
    assert updated['start_date'] == updated['end_date'] == '2026-10-24'
    assert updated['calendar_event_id'] == event_id
    assert event['date'] == event['end_date'] == '2026-10-24'
    assert len(coverages) == 1
    assert coverages[0]['id'] == secondary_id
    assert coverages[0]['source_task_id'] == task['id']
    assert coverages[0]['boda_date'] == '2026-10-24'
    html = auth_client.get('/jobs').get_data(as_text=True)
    row = re.search(r'<tr\b[^>]*data-name="boda civil".*?</tr>', html, re.S).group(0)
    assert secondary_id in row
    assert '24 de octubre de 2026' in row
    assert '25 de octubre de 2026' not in row

    response = auth_client.post(f'/api/jobs/{job_id}/workflow-task/{task["id"]}/delete')
    assert response.status_code == 200
    job = crm.store.get('jobs', job_id)
    assert not job['manual_workflow_tasks']
    assert crm.store.get('calendar', event_id) is None
    assert not linked_coverages([job], crm.store.list('calendar'))
    assert secondary_id not in auth_client.get('/jobs').get_data(as_text=True)


def test_correcting_civil_restores_a_missing_calendar_event(auth_client):
    crm, job_id, task = _create_civil(auth_client)
    crm.store.delete('calendar', task['calendar_event_id'])
    response = auth_client.post(f'/api/jobs/{job_id}/workflow-task/{task["id"]}/update', json={
        'name': task['name'], 'start_date': '2026-10-24', 'location': task['location'],
    })
    assert response.status_code == 200
    updated = response.get_json()['task']
    assert updated['id'] == task['id']
    event = crm.store.get('calendar', updated['calendar_event_id'])
    assert event['date'] == '2026-10-24'
    assert event['job_id'] == job_id
