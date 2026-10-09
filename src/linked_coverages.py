"""Operational views of extra shoots; their client income belongs to the parent job."""
import re


def linked_coverages(jobs, calendar):
    rows = []
    parents = {j['id']: j for j in jobs}
    by_lead = {j['lead_id']: j for j in jobs if j.get('lead_id')}
    mirrored = set()
    def add(parent, source, identifier, task_id=None):
        start = source.get('start_date') or source.get('date')
        if not start or not identifier or source.get('released_at') or source.get('status') in ('cancelled', 'cancelado'):
            return
        name = source.get('name') or source.get('title') or 'Cobertura adicional'
        suffix = ' - ' + (parent.get('nombre') or 'Job')
        if name.endswith(suffix):
            name = name[:-len(suffix)]
        rows.append(dict(id='secondary:'+identifier, parent_job_id=parent['id'], parent_name=parent.get('nombre') or 'Trabajo principal',
                         source_task_id=task_id, source_step_id=source.get('step_id'), source_event_id=source.get('calendar_event_id') or (source.get('id') if not task_id else None),
                         nombre=name, boda_date=start, end_date=source.get('end_date') or start,
                         start_time=source.get('start_time') or '', end_time=source.get('end_time') or '',
                         location=source.get('location') or '', type='Trabajo secundario', price_total=0,
                         status=parent.get('status'), currency=parent.get('currency', 'GTQ'), tenant_id=parent.get('tenant_id'),
                         client_id=parent.get('client_id'), created=source.get('created') or parent.get('created') or '',
                         manual_workflow_tasks=[], secondary=True))
    for parent in jobs:
        for task in parent.get('manual_workflow_tasks') or []:
            if task.get('calendar_event_id'):
                mirrored.add(task['calendar_event_id'])
            if task.get('type') == 'extra-event':
                add(parent, task, task.get('calendar_event_id') or task.get('id'), task.get('id'))
    for event in calendar:
        if event.get('type') != 'event' or event.get('id') in mirrored:
            continue
        parent = parents.get(event.get('job_id')) or by_lead.get(event.get('lead_id'))
        coverage = ('extra-event' in (event.get('notes') or '') or
                    re.search(r'civil|save\s*(the)?\s*date|trash\s*(the)?\s*dress|welcome\s*party', event.get('title') or '', re.I))
        if parent and coverage:
            # An old generic Calendar mirror can coexist with the named civil in the workflow.
            name = (event.get('title') or '').split(' - ', 1)[0].strip().casefold()
            if name == 'boda civil' and any(r['parent_job_id'] == parent['id'] and r['boda_date'] == event.get('date')
                                           and r.get('source_step_id') and 'civil' in r['nombre'].casefold() for r in rows):
                continue
            add(parent, event, event.get('id'))
    return rows


def resolve_job(store, identifier):
    if identifier.startswith('secondary:'):
        return next((j for j in linked_coverages(store.list('jobs'), store.list('calendar')) if j['id'] == identifier), None)
    return store.get('jobs', identifier)
