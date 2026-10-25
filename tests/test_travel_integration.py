"""Real template snapshots, synthetic leads, isolated persistence, no real delivery."""
import json
from pathlib import Path

import pytest

from src.travel_mail import compose_travel, legacy_travel_conflict, strip_travel_block

TID = 'tenant-norkevin-photography'
FIXTURE = json.loads((Path(__file__).parent / 'fixtures/travel_email_templates.json').read_text())


@pytest.fixture
def travel_flow(auth_client, monkeypatch):
    import app as crm
    from conftest import login_as_tenant
    login_as_tenant(auth_client, TID)
    token = crm._workflow_tenant.set(TID)
    monkeypatch.setattr('src.travel._GEOCODE_LAST_REQUEST', 0.0)
    monkeypatch.setattr(crm.travel_service, '_geocode', lambda query: [])
    crm.travel_service.ensure_catalog(TID)
    crm.travel_service.save_settings(TID, {'enabled': True}, actor='test')
    for year in ('2027', '2028'):
        crm.store.upsert('email_templates', {
            'id': 'test-travel-' + year, 'tenant_id': TID,
            'name': 'Paquetes de boda Norkevin' + (' 2028' if year == '2028' else ''),
            'asunto': FIXTURE['subject'], 'activo': True,
            'cuerpo': FIXTURE['body'].replace('PACKAGE_LINK', FIXTURE['links'][year]),
        })
    crm.store.upsert('email_templates', {
        'id': 'test-travel-unavailable', 'tenant_id': TID,
        'name': 'Paquetes de boda Norkevin (FECHA NO DISPONIBLE)', 'activo': True,
        'asunto': FIXTURE['unavailable_subject'], 'cuerpo': FIXTURE['unavailable_body'],
    })
    workflow = crm.LEAD_WORKFLOW(TID).to_dict()
    step = next(s for s in workflow['steps'] if s['id'] == 'envio_paquetes')
    step['email_template_id'] = 'test-travel-2027'
    crm.store.save_tenant_dict('workflow_templates', {workflow['id']: workflow}, TID)

    def make_lead(location='Hotel Atitlán', year='2028', suffix='1'):
        lead = dict(id='test-travel-lead-' + suffix, tenant_id=TID, nombre='Ana y Luis (prueba)',
                    email='ana-luis@example.invalid', locacion=location,
                    fecha_tentativa=year + '-06-17', created='2026-10-13', status='Nuevo')
        crm.store.upsert('leads', lead)
        crm.trigger_workflow_for_lead(lead['id'], lead['nombre'], TID)
        return lead
    yield crm, auth_client, make_lead
    crm._workflow_tenant.reset(token)


def queue(crm, lead):
    result = crm._complete_lead_workflow_step(lead, 'envio_paquetes')
    assert result.get('mail_id'), result
    return crm.store.get('pending_emails', result['mail_id'])


@pytest.mark.parametrize('place,year,level', [
    ('Finca San Cayetano', '2027', 0), ('Hotel Atitlán', '2028', 1),
    ('La Casa del Mundo', '2027', 1), ('IRTRA Retalhuleu', '2027', 2),
    ('Flores, Petén', '2028', 3), ('Tecpán', '2027', 0), ('Antigua Guatemala', '2027', 0),
])
def test_full_real_template_year_and_fees(travel_flow, place, year, level):
    crm, client, make = travel_flow
    lead = make(place, year)
    mail = queue(crm, lead)
    assert mail['status'] == 'pending', mail
    assert mail['travel_snapshot']['level'] == level
    assert FIXTURE['links'][year] in mail['body']
    assert FIXTURE['links']['2028' if year == '2027' else '2027'] not in mail['body']
    expected = crm._render_message_template(FIXTURE['body'].replace('PACKAGE_LINK', FIXTURE['links'][year]), lead=lead)
    assert strip_travel_block(mail['body']) == expected
    assert mail['body'].endswith('¡Saludos!')
    assert mail['body'].count('se aplicarían los siguientes viáticos:') == (1 if level else 0)
    assert crm._validate_travel_email(mail) is None
    if level:
        from src.travel import TRAVEL_LEVELS
        for field in ('photo_amount', 'photo_video_amount'):
            assert f"Q{TRAVEL_LEVELS[level][field]:,}" in mail['body']
        assert compose_travel(mail['body'], mail['travel_snapshot']) == mail['body']
    response = client.get('/api/pending-emails/' + mail['id'])
    assert response.status_code == 200 and response.json['motivo'] is None


def test_approved_package_delivery_preserves_notification_source_and_travel_snapshot(travel_flow, monkeypatch):
    crm, client, make = travel_flow
    from src.email_delivery import DeliveryResult
    from src.mail_tracker import get_tracker
    calls = []
    monkeypatch.setattr('src.mail_tracker.send_email', lambda *args, **kwargs:
                        calls.append((args, kwargs)) or DeliveryResult(ok=True, provider='test', mode='test'))
    pending = queue(crm, make('Hotel Atitlán', '2028'))
    result = get_tracker().approve_and_send(pending['id'], sender_tenant_id=TID, actor='test-approval')
    assert result['ok'] and len(calls) == 1
    assert result['mail']['source'] == pending['source'] == 'workflow:lead-step:envio_paquetes'
    assert result['mail']['travel_snapshot'] == pending['travel_snapshot']
    assert 'Q1,500' in calls[0][0][2] and 'Q2,500' in calls[0][0][2]


@pytest.mark.parametrize('place', ['Venue desconocido 938472', 'Finca San Cayetano, Argentina', 'Salamá'])
def test_unresolved_uses_normal_packages_and_can_deliver(travel_flow, monkeypatch, place):
    crm, client, make = travel_flow
    from src.mail_tracker import get_tracker
    mail = queue(crm, make(place))
    assert mail['status'] == 'pending'
    assert 'Q1,500' not in mail['body'] and 'Q0' not in mail['body']
    called = []
    from src.email_delivery import DeliveryResult
    monkeypatch.setattr('src.mail_tracker.send_email', lambda *a, **k: called.append(a) or DeliveryResult(ok=True, provider='test', mode='test'))
    assert client.get('/api/pending-emails/' + mail['id']).json['motivo'] is None
    result = get_tracker().approve_and_send(mail['id'], sender_tenant_id=TID, actor='test')
    assert result['ok'] and len(called) == 1
    assert FIXTURE['links']['2028'] in mail['body']


def test_venue_change_recalculates_and_blocks_old_snapshot(travel_flow):
    crm, client, make = travel_flow
    lead = make()
    mail = queue(crm, lead)
    response = client.post('/api/leads/' + lead['id'] + '/update', json={'ubicacion': 'IRTRA Retalhuleu'})
    assert response.status_code == 200
    assert response.json['lead']['travel_resolution']['level'] == 2
    assert 'cambió' in crm._validate_travel_email(mail)
    from src.mail_tracker import get_tracker
    assert not get_tracker().approve_and_send(mail['id'], sender_tenant_id=TID)['ok']
    get_tracker().discard_pending(mail['id'], actor='test')
    new_mail = queue(crm, crm.get_lead(lead['id']))
    assert new_mail['travel_snapshot']['level'] == 2
    assert 'Q2,000' in new_mail['body'] and 'Q1,500' not in new_mail['body']


def test_manual_template_path_without_complete_step_is_protected(travel_flow):
    crm, client, make = travel_flow
    lead = make('IRTRA Retalhuleu', '2027')
    response = client.post('/api/leads/' + lead['id'] + '/send-email', json={'template_id':'test-travel-2027'})
    assert response.status_code == 200
    mail = crm.store.get('pending_emails', response.json['mail_id'])
    assert mail['travel_snapshot']['level'] == 2 and 'Q3,500' in mail['body']
    duplicate = queue(crm, lead)
    regenerated = queue(crm, lead)
    assert regenerated['id'] == duplicate['id']
    assert regenerated['body'].count('se aplicarían los siguientes viáticos:') == 1


def test_no_availability_keeps_astral_reply_and_followups(travel_flow):
    crm, client, make = travel_flow
    crm.store.upsert('jobs', dict(id='test-other-booking', tenant_id=TID,
                     nombre='Boda ya reservada', boda_date='2028-06-17', status='Confirmado'))
    lead = make('Venue desconocido')
    mail = next(m for m in crm.store.list('pending_emails') if m.get('lead_id') == lead['id'])
    assert mail['template_id'] == 'test-travel-unavailable'
    assert mail['status'] == 'pending' and not mail.get('travel_snapshot')
    assert mail['body'] == crm._render_message_template(FIXTURE['unavailable_body'], lead=lead)
    assert crm._validate_travel_email(mail) is None


def test_availability_changed_after_draft_requires_review(travel_flow):
    crm, client, make = travel_flow
    lead = make()
    mail = queue(crm, lead)
    crm.store.upsert('jobs', dict(id='test-new-booking', tenant_id=TID,
                     nombre='Otra boda', boda_date=lead['fecha_tentativa'], status='Confirmado'))
    assert 'ocupada' in crm._validate_travel_email(mail)


def test_tariff_change_and_disabled_feature_invalidate_snapshot(travel_flow):
    crm, client, make = travel_flow
    mail = queue(crm, make())
    venue = next(v for v in crm.travel_service.catalog(TID) if v['name'] == 'Hotel Atitlán')
    crm.travel_service.save_venue(TID, dict(venue, travel_level=2), actor='test')
    assert 'cambió' in crm._validate_travel_email(mail)
    crm.travel_service.save_settings(TID, {'enabled': False})
    assert 'configuración' in crm._validate_travel_email(mail)


def test_unapproved_historical_and_direct_sends_cannot_bypass_guard(travel_flow, monkeypatch):
    crm, client, make = travel_flow
    from src.mail_tracker import get_tracker
    lead = make()
    called = []
    monkeypatch.setattr('src.mail_tracker.send_email', lambda *a, **k: called.append(a))
    result = get_tracker().log_email(lead['email'], 'Paquetes', 'Sin tarifa',
                     template_id='test-travel-2028', lead_id=lead['id'], tenant_id=TID)
    assert result['status'] == 'blocked' and not called
    mail = queue(crm, lead)
    mail['body'] += '\nQ1,700'
    assert 'contenido' in crm._validate_travel_email(mail)


def test_known_legacy_fees_are_replaced_without_touching_saved_template(travel_flow):
    crm, client, make = travel_flow
    lead = make('IRTRA Retalhuleu', '2027')
    template = crm.store.get('email_templates', 'test-travel-2027')
    legacy = ('Para bodas en la distancia que me indicas se cobra un extra de viáticos según el paquete contratado '
              '(traslados, comidas *no incluye cena el día de la boda* y hospedaje)\n\n'
              'Paquete de fotografía: Q2,500\nPaquete de foto y video: Q4,000')
    template['cuerpo'] = template['cuerpo'].replace('\n\nSi tienes', '\n\n' + legacy + '\n\nSi tienes')
    crm.store.upsert('email_templates', template)
    mail = queue(crm, lead)
    assert 'Q4,000' not in mail['body'] and 'Q3,500' in mail['body']
    assert crm.store.get('email_templates', template['id'])['cuerpo'] == template['cuerpo']
    assert legacy_travel_conflict('Viáticos de traslado: Q1,700')


def test_geography_failure_uses_normal_package_email(travel_flow, monkeypatch):
    crm, client, make = travel_flow
    lead = make()
    monkeypatch.setattr(crm.travel_service, 'resolve', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('failed')))
    mail = queue(crm, lead)
    assert mail['status'] == 'pending' and not mail['blocked_reason']
    assert crm._validate_travel_email(mail) is None
    assert 'Q1,500' not in mail['body']


def test_new_unknown_is_researched_once_and_remains_a_proposal(travel_flow, monkeypatch):
    crm, client, make = travel_flow
    calls = []
    def provider(query):
        calls.append(query)
        return [{'name': 'Finca Nueva de Prueba', 'address': {'country_code': 'gt',
                  'city': 'Panajachel', 'state': 'Sololá'}, 'display_name': 'Finca Nueva de Prueba, Panajachel',
                  'osm_type': 'node', 'osm_id': '100', 'lat': '14.7', 'lon': '-91.1'}]
    monkeypatch.setattr(crm.travel_service, '_geocode', provider)
    lead = make('Finca Nueva de Prueba')
    mail = queue(crm, lead)
    assert mail['status'] == 'pending' and len(calls) == 1
    assert crm.travel_service.resolve(lead, research=True)['requires_review']
    assert len(calls) == 1
    venues = crm.travel_service.catalog(TID)
    venue = next(v for v in venues if v['name'] == 'Finca Nueva de Prueba')
    assert venue['approval_status'] == 'proposed' and venue['verification_status'] == 'pending'


@pytest.mark.parametrize('link', [FIXTURE['links']['2027'],
    FIXTURE['links']['2027'].replace('?usp=sharing', '?usp=drive_link#preview'),
    'https://drive.google.com/open?id=1BQFU3oq_3WImnKose6L8rFTPE5Mh-kXf'])
def test_copy_pasted_packages_with_unknown_venue_use_normal_email(travel_flow, monkeypatch, link):
    crm, client, make = travel_flow
    lead = make('Venue desconocido sin verificar', '2027')
    response = client.post('/api/leads/' + lead['id'] + '/send-email', json={
        'subject': 'Mis paquetes de boda', 'body': FIXTURE['body'].replace('PACKAGE_LINK', link)})
    assert response.status_code == 200
    mail = crm.store.get('pending_emails', response.json['mail_id'])
    assert mail['status'] == 'pending' and mail.get('travel_snapshot')
    assert crm._validate_travel_email(mail) is None
    assert 'se aplicarían los siguientes viáticos:' not in mail['body']


def test_manual_wrong_year_without_complete_step_uses_correct_link(travel_flow):
    crm, client, make = travel_flow
    lead = make('Hotel Atitlán', '2028')
    response = client.post('/api/leads/' + lead['id'] + '/send-email', json={'template_id': 'test-travel-2027'})
    assert response.status_code == 200
    mail = crm.store.get('pending_emails', response.json['mail_id'])
    assert mail['template_id'] == 'test-travel-2028'
    assert FIXTURE['links']['2028'] in mail['body'] and FIXTURE['links']['2027'] not in mail['body']
    assert 'Q1,500' in mail['body']


def test_unlinked_package_step_does_not_add_fees_to_unrelated_message(travel_flow):
    crm, client, make = travel_flow
    lead = make('Hotel Atitlán', '2027')
    workflow = crm.LEAD_WORKFLOW(TID).to_dict()
    next(s for s in workflow['steps'] if s['id'] == 'envio_paquetes')['email_template_id'] = None
    crm.store.save_tenant_dict('workflow_templates', {workflow['id']: workflow}, TID)
    response = client.post('/api/leads/' + lead['id'] + '/send-email', json={'subject': 'Reunión', 'body': '¿Podemos hablar mañana?'})
    mail = crm.store.get('pending_emails', response.json['mail_id'])
    assert mail['status'] == 'pending'
    assert mail['body'] == '¿Podemos hablar mañana?' and not mail.get('travel_snapshot')


def test_resolver_failure_keeps_pending_review_screen_available(travel_flow, monkeypatch):
    crm, client, make = travel_flow
    mail = queue(crm, make())
    monkeypatch.setattr(crm.travel_service, 'resolve', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('failed')))
    response = client.get('/api/pending-emails/' + mail['id'])
    assert response.status_code == 200
    assert not response.json['enviable'] and 'cambió' in response.json['motivo']
    assert client.get('/emails').status_code == 200


@pytest.mark.parametrize('location,amount', [('Monterrico, Santa Rosa', 'Q1,000'), ('Hotel Atitlán', 'Q1,500'), ('Departamento de Petén', 'Q2,500'), ('Lugar no confirmado 314159', None), ('Sacatepéquez', None), ('Boda en Argentina', None)])
def test_authorized_norkevin_packages_auto_deliver_normal_or_with_approved_fees(travel_flow, monkeypatch, location, amount):
    crm, client, make = travel_flow
    from src.email_delivery import DeliveryResult
    sent = []
    monkeypatch.setattr('src.mail_tracker.send_email', lambda *a, **k:
                        sent.append((a, k)) or DeliveryResult(ok=True, provider='test', mode='test'))
    lead = make(location)
    crm.travel_service.save_settings(TID, {'auto_send_packages': True}, actor='owner-test')
    mail = queue(crm, lead)
    assert mail['status'] == 'sent' and len(sent) == 1
    assert mail['historial'][-1]['actor'] == 'sistema:paquetes-automaticos'
    assert FIXTURE['links']['2028'] in sent[0][0][2]
    if amount:
        assert amount in sent[0][0][2]
    else:
        assert 'se aplicarían los siguientes viáticos:' not in sent[0][0][2]
    assert crm._complete_lead_workflow_step(lead, 'envio_paquetes').get('already_done')
    assert len(sent) == 1


def test_auto_packages_geography_error_sends_normal_without_leaving_pending(travel_flow, monkeypatch):
    crm, client, make = travel_flow
    from src.email_delivery import DeliveryResult
    sent = []
    lead = make('Hotel Atitlán')
    crm.travel_service.save_settings(TID, {'auto_send_packages': True})
    monkeypatch.setattr(crm.travel_service, 'resolve', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('provider unavailable')))
    monkeypatch.setattr('src.mail_tracker.send_email', lambda *a, **k:
                        sent.append(a) or DeliveryResult(ok=True, provider='test', mode='test'))
    mail = queue(crm, lead)
    assert mail['status'] == 'sent' and len(sent) == 1
    assert 'se aplicarían los siguientes viáticos:' not in sent[0][2]


def test_auto_packages_does_not_send_modified_or_obsolete_draft(travel_flow, monkeypatch):
    crm, client, make = travel_flow
    lead = make('Lugar incierto')
    crm.travel_service.save_settings(TID, {'auto_send_packages': True})
    from src.mail_tracker import get_tracker
    sent = []
    monkeypatch.setattr('src.mail_tracker.send_email', lambda *a, **k: sent.append(a))
    old_copy = crm._render_message_template(FIXTURE['body'].replace('PACKAGE_LINK', FIXTURE['links']['2028']), lead=lead) + '\nTexto antiguo editado'
    mail = get_tracker().queue_email(lead['email'], FIXTURE['subject'], old_copy,
        template_id='test-travel-2028', lead_id=lead['id'], tenant_id=TID,
        source='workflow:lead-step:envio_paquetes', idempotency_key='obsolete-package-copy')
    assert mail['status'] == 'pending' and not sent


def test_department_chiquimula_approved_region_adds_level_two_fees(travel_flow):
    crm, client, make = travel_flow
    crm.travel_service.save_rule(TID, {'name':'Chiquimula', 'value':'Chiquimula', 'scope':'region',
        'level':2, 'approval_status':'approved', 'reason':'Política de departamento confirmada por el usuario.'})
    mail = queue(crm, make('Boda en Chiquimula'))
    assert mail['status'] == 'pending' and mail['travel_snapshot']['level'] == 2
    assert 'Q2,000' in mail['body'] and 'Q3,500' in mail['body']


@pytest.mark.parametrize('location', ['Lugar sin confirmar', 'Boda en Argentina'])
def test_normal_package_notice_is_conditional_and_not_duplicated(travel_flow, location):
    crm, client, make = travel_flow
    mail = queue(crm, make(location))
    notice = 'Si su boda se realiza fuera de Ciudad de Guatemala, Antigua Guatemala, Tecpán o sus alrededores'
    assert notice in mail['body'] and mail['body'].count(notice) == 1
    assert 'Consúltenos para confirmarlo.' in mail['body']
    assert compose_travel(mail['body'], mail['travel_snapshot']) == mail['body']
    assert notice not in strip_travel_block(mail['body'])


def test_known_exempt_and_known_charged_destinations_do_not_get_uncertain_notice(travel_flow):
    crm, client, make = travel_flow
    for index, place in enumerate(('Finca San Cayetano', 'Hotel Atitlán')):
        mail = queue(crm, make(place, suffix='notice-' + str(index)))
        assert 'podrían aplicarse cargos adicionales' not in mail['body']


@pytest.mark.parametrize('violation', ['unavailable', 'wrong_year'])
def test_auto_package_guards_still_apply_when_travel_fees_disabled(travel_flow, monkeypatch, violation):
    crm, client, make = travel_flow
    from src.mail_tracker import get_tracker
    lead = make('Lugar incierto')
    crm.travel_service.save_settings(TID, {'enabled':False, 'auto_send_packages':True})
    if violation == 'unavailable':
        crm.store.upsert('jobs', dict(id='occupied-auto-date',tenant_id=TID,status='Confirmado',boda_date=lead['fecha_tentativa']))
    year = '2027' if violation == 'wrong_year' else '2028'
    template = crm.store.get('email_templates','test-travel-' + year)
    body = crm._render_message_template(template['cuerpo'],lead=lead)
    sent = []
    monkeypatch.setattr('src.mail_tracker.send_email', lambda *a, **k: sent.append(a))
    entry = get_tracker().queue_email(lead['email'],FIXTURE['subject'],body,template_id=template['id'],
        lead_id=lead['id'],tenant_id=TID,source='workflow:lead-step:envio_paquetes')
    assert entry['status']=='blocked' and not sent
    assert crm._validate_travel_email(entry)



def test_norkevin_automatic_policy_never_rewrites_or_blocks_astral_packages(travel_flow):
    crm, client, make = travel_flow
    from src.tenant_brand_map import resolve_brand
    astral=resolve_brand('tenant-norkevin').internal_tenant_id
    lead=dict(id='astral-package-isolation',tenant_id=astral,nombre='Pareja Astral',email='astral-package@example.invalid',fecha_tentativa='2028-05-12',locacion='Lugar incierto')
    template=dict(id='astral-packages-2028',tenant_id=astral,name='Paquetes Astral 2028',activo=True,cuerpo='Texto propio de Astral: viáticos Q4,500')
    token=crm._workflow_tenant.set(astral)
    try:
        crm.store.upsert('leads',lead);crm.store.upsert('email_templates',template)
        entry=dict(tenant_id=astral,lead_id=lead['id'],template_id=template['id'],body=template['cuerpo'],subject='Astral',source='workflow:lead-step:envio_paquetes')
        assert crm._prepare_travel_email(entry)=={}
        assert crm._validate_travel_email(entry) is None
        assert not crm._should_auto_send_package_email(entry)
    finally:
        crm._workflow_tenant.reset(token)


@pytest.mark.parametrize('delivered_status', ['sent', 'opened', 'clicked'])
def test_discarding_old_package_duplicates_preserves_delivered_step(travel_flow, monkeypatch, delivered_status):
    crm, client, make = travel_flow
    from src.email_delivery import DeliveryResult
    from src.mail_tracker import get_tracker
    sent = []
    monkeypatch.setattr('src.mail_tracker.send_email', lambda *a, **k:
                        sent.append(a) or DeliveryResult(ok=True, provider='test', mode='test'))
    lead = make('Lugar incierto', '2027')
    old = queue(crm, lead)
    old['status'] = 'blocked'
    old['blocked_reason'] = 'Ubicación sin verificar (regla antigua)'
    legacy = [dict(old, id=old['id']), dict(old, id='pend-legacy-duplicate')]
    for draft in legacy:
        crm.store.upsert('pending_emails', draft)
    instance = crm._workflow_instance_for('lead', lead['id'], tenant_id=TID)
    crm._apply_workflow_delivery(instance, 'envio_paquetes', old)
    assert instance.step_states['envio_paquetes'] == crm.StepStatus.FAILED

    crm.travel_service.save_settings(TID, {'auto_send_packages': True})
    current = queue(crm, lead)
    assert current['status'] == 'sent' and len(sent) == 1
    assert current['id'] not in [draft['id'] for draft in legacy]
    assert FIXTURE['links']['2027'] in sent[0][2]
    for table in ('mail_log', 'pending_emails'):
        for entry in crm.store.list(table):
            if entry.get('idempotency_key') == current['idempotency_key'] and entry.get('status') == 'sent':
                entry['status'] = delivered_status
                crm.store.upsert(table, entry)
    for status in ('pending', 'blocked', 'failed'):
        crm._apply_workflow_delivery(instance, 'envio_paquetes', dict(old, status=status))
        assert instance.step_states['envio_paquetes'] == crm.StepStatus.DONE
    for draft in legacy:
        assert get_tracker().discard_pending(draft['id'], actor='owner-test')['ok']
        assert crm.store.get('pending_emails', draft['id'])['status'] == 'discarded'
        assert instance.step_states['envio_paquetes'] == crm.StepStatus.DONE
    steps, _, _ = crm.compute_workflow_steps_for_lead(lead)
    assert next(step for step in steps if step['id'] == 'envio_paquetes')['status'] == 'done'
    assert crm._complete_lead_workflow_step(lead, 'envio_paquetes')['already_done']
    assert len(sent) == 1


@pytest.mark.parametrize('status,expected', [('pending', 'queued'), ('blocked', 'failed'),
                                             ('failed', 'failed'), ('discarded', 'failed')])
def test_other_tenant_delivery_does_not_hide_legitimate_package_failure(travel_flow, status, expected):
    crm, client, make = travel_flow
    lead = make('Lugar incierto')
    mail = queue(crm, lead)
    other_tenant = 'tenant-norkevin'
    token = crm._workflow_tenant.set(other_tenant)
    try:
        crm.store.upsert('mail_log', dict(id='other-tenant-sent', tenant_id=other_tenant,
            idempotency_key=mail['idempotency_key'], status='sent'))
    finally:
        crm._workflow_tenant.reset(token)
    instance = crm._workflow_instance_for('lead', lead['id'], tenant_id=TID)
    crm._apply_workflow_delivery(instance, 'envio_paquetes', dict(mail, status=status))
    assert instance.step_states['envio_paquetes'].value == expected


def test_monterrico_email_has_custom_rates_and_rejects_obsolete_amounts(travel_flow):
    crm, client, make = travel_flow
    lead = make('Monterrico, Santa Rosa', '2027')
    mail = queue(crm, lead)
    assert mail['status'] == 'pending'
    assert 'fotografía: Q1,000.' in mail['body']
    assert 'fotografía y video: Q1,500.' in mail['body']
    assert FIXTURE['links']['2027'] in mail['body']
    assert 'podrían aplicarse' not in mail['body']
    assert crm._validate_travel_email(mail) is None
    assert compose_travel(mail['body'], mail['travel_snapshot']) == mail['body']
    rule = next(row for row in crm.travel_service.rules(TID) if row['value'] == 'Monterrico')
    crm.travel_service.save_rule(TID, {'id': rule['id'], 'photo_video_amount': 1800})
    assert 'tarifa cambió' in crm._validate_travel_email(mail)
    from src.mail_tracker import get_tracker
    get_tracker().discard_pending(mail['id'], actor='test')
    new_mail = queue(crm, lead)
    assert 'fotografía y video: Q1,800.' in new_mail['body']
    assert new_mail['body'].count('se aplicarían los siguientes viáticos:') == 1
    assert crm._validate_travel_email(new_mail) is None


def test_unknown_to_custom_rate_invalidates_old_notice(travel_flow):
    crm, client, make = travel_flow
    mail = queue(crm, make('Playa de ejemplo'))
    crm.travel_service.save_rule(TID, dict(value='Playa de ejemplo', scope='region',
        photo_amount=1000, photo_video_amount=1500, approval_status='approved'))
    assert 'tarifa cambió' in crm._validate_travel_email(mail)
