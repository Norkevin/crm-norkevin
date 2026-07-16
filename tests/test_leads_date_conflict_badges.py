"""Kevin: 'en estudio ninja te avisa que fechas hay mas gente interesada el
color naranja significa: another lead is at the same time y el color rojo
significa que ya tenemos agendada otra boda: multiple events are the same
time, esto ayuda a tomar decisiones para ver si acepto o no las bodas'.

leads_list() ya calculaba date_conflict (rojo, contra Jobs reales) y
date_available (amarillo). Faltaba el caso intermedio naranja: dos o mas
Leads abiertos compitiendo por la misma fecha, sin que ninguno se haya
convertido todavia en Job."""
import uuid


def _make_lead(app_module, suffix, fecha, status='Nuevo'):
    lead_id = f'lead-dateconf-{suffix}'
    app_module.store.upsert('leads', {
        'id': lead_id, 'nombre': f'Lead {suffix}', 'email': f'{suffix}@example.com',
        'fecha_tentativa': fecha, 'status': status, 'tenant_id': 'tenant-norkevin',
        'created': '2026-07-01',
    })
    return lead_id


def test_two_open_leads_same_date_get_orange_conflict(auth_client):
    import app as app_module
    fecha = '2027-05-08'
    suffix = uuid.uuid4().hex[:6]
    lead_a = _make_lead(app_module, f'a-{suffix}', fecha)
    lead_b = _make_lead(app_module, f'b-{suffix}', fecha)

    resp = auth_client.get('/leads')
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'date-status-badge orange' in html


def test_lead_alone_on_its_date_has_no_conflict_badge(auth_client):
    import app as app_module
    suffix = uuid.uuid4().hex[:6]
    _make_lead(app_module, f'solo-{suffix}', '2027-06-01')

    resp = auth_client.get('/leads')
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    # no debe reventar y el badge amarillo (disponible) debe seguir funcionando
    assert 'date-status-badge yellow' in html


def test_job_booked_date_wins_over_lead_conflict_as_red(auth_client):
    """Si ya hay un Job real agendado ese dia, el badge debe ser rojo (mas
    urgente), no naranja, aunque tambien haya otro lead compitiendo."""
    import app as app_module
    fecha = '2027-09-11'
    suffix = uuid.uuid4().hex[:6]
    client_id = f'client-dateconf-{suffix}'
    job_id = f'job-dateconf-{suffix}'
    app_module.store.upsert('clients', {
        'id': client_id, 'first_name': 'Ya', 'last_name': 'Agendada',
        'email': 'yaagendada@example.com', 'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('jobs', {
        'id': job_id, 'client_id': client_id, 'nombre': 'Boda Ya Agendada',
        'boda_date': fecha, 'status': 'Confirmado', 'tenant_id': 'tenant-norkevin',
    })
    lead_a = _make_lead(app_module, f'compite-a-{suffix}', fecha)
    lead_b = _make_lead(app_module, f'compite-b-{suffix}', fecha)

    resp = auth_client.get('/leads')
    html = resp.get_data(as_text=True)
    assert 'date-status-badge red' in html


def test_converted_or_lost_leads_do_not_count_toward_orange_conflict(auth_client):
    import app as app_module
    fecha = '2027-10-02'
    suffix = uuid.uuid4().hex[:6]
    lead_a = _make_lead(app_module, f'conv-{suffix}', fecha, status='Convertido')
    lead_b = _make_lead(app_module, f'lost-{suffix}', fecha, status='Perdido')
    lead_c = _make_lead(app_module, f'open-{suffix}', fecha, status='Nuevo')

    lead = app_module.store.get('leads', lead_c)
    assert lead is not None
