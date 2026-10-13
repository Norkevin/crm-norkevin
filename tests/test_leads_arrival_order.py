"""Lead arrival order is independent of event dates and later edits."""
from flask import template_rendered


def arrivals():
    return [
        dict(id='arrival-later', nombre='Later', created='2026-10-13', created_time='2026-10-13T15:00:00-06:00', fecha_tentativa='2027-12-01'),
        dict(id='arrival-earlier', nombre='Earlier', created='2026-10-13', created_time='2026-10-13T19:00:00+00:00', fecha_tentativa='2026-11-01'),
        dict(id='arrival-legacy-old', nombre='Legacy old', created='2026-10-12', fecha_tentativa='2029-01-01'),
        dict(id='arrival-legacy-new', nombre='Legacy new', created='2026-10-12', fecha_tentativa='2026-11-02'),
        dict(id='arrival-unknown', nombre='Unknown', created=None, fecha_tentativa='2026-10-01'),
    ]


def test_lead_list_and_export_use_arrival_time_with_latest_inserted_legacy_ties(auth_client, monkeypatch):
    import app as crm
    leads = arrivals()
    monkeypatch.setattr(crm, '_open_leads', lambda *args: [dict(lead) for lead in leads])
    monkeypatch.setattr(crm, 'compute_workflow_steps_for_lead', lambda *args, **kwargs: ([], 0, None))
    contexts = []
    def capture(sender, template, context, **extra):
        if template.name == 'leads.html':
            contexts.append(context)
    with template_rendered.connected_to(capture, crm.app):
        response = auth_client.get('/leads')
    assert response.status_code == 200
    assert [lead['id'] for lead in contexts[-1]['leads']] == [
        'arrival-later', 'arrival-earlier', 'arrival-legacy-new', 'arrival-legacy-old', 'arrival-unknown']
    exported = auth_client.get('/api/leads/export.csv').get_data(as_text=True)
    assert exported.index('Later') < exported.index('Earlier') < exported.index('Legacy new') < exported.index('Legacy old') < exported.index('Unknown')
    # Editing the event or activity does not make an earlier inquiry recent.
    leads[1].update(fecha_tentativa='2030-01-01', updated_at='2026-10-14T10:00:00')
    assert crm._leads_newest_first(leads)[0]['id'] == 'arrival-later'
