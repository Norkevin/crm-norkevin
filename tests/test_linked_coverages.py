from src.linked_coverages import linked_coverages


def test_linked_shoots_are_stable_deduplicated_and_exclude_appointments():
    job = dict(id='main',nombre='Principal',tenant_id='brand',boda_date='2027-01-10',status='En curso',
        manual_workflow_tasks=[dict(id='civil',step_id='imported-civil-step',type='extra-event',name='Civil',calendar_event_id='mirror',start_date='2026-11-07'),
                              dict(id='meet',type='appointment',name='Reunión',calendar_event_id='zoom',start_date='2026-11-07')])
    calendar = [dict(id='mirror',type='event',job_id='main',title='Civil - Principal',date='2026-11-07'),
                dict(id='zoom',type='event',job_id='main',title='Reunión',date='2026-11-07'),
                dict(id='legacy-civil',type='event',job_id='main',title='Boda civil - Principal',date='2026-11-08'),
                dict(id='generic-duplicate',type='event',job_id='main',title='Boda civil - Principal',date='2026-11-07'),
                dict(id='other-brand',type='event',job_id='other-main',title='Boda civil',date='2026-11-07')]
    rows = linked_coverages([job],calendar)
    assert {j['id'] for j in rows} == {'secondary:mirror','secondary:legacy-civil'}
    assert all(j['parent_job_id'] == 'main' and j['price_total'] == 0 for j in rows)
    job['manual_workflow_tasks'][0]['start_date'] = '2026-11-08'
    changed = linked_coverages([job],calendar)
    assert changed[0]['id'] == rows[0]['id'] and changed[0]['boda_date'] == '2026-11-08'


def test_completion_comes_from_own_task_or_imported_step():
    job = dict(id='main', status='Confirmado', studio_ninja_workflow=[dict(id='import', status='done')],
               manual_workflow_tasks=[dict(id='own', type='extra-event', start_date='2020-01-01', status='done'),
                                      dict(id='imported', step_id='import', type='extra-event', start_date='2020-02-01'),
                                      dict(id='next', type='extra-event', start_date='2027-01-01', status='pending')])
    rows = linked_coverages([job], [])
    assert [r['coverage_completed'] for r in rows] == [True, True, False]
    assert [r['status'] for r in rows] == ['Listo', 'Listo', 'Confirmado']
