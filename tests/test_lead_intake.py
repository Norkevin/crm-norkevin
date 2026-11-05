from copy import deepcopy
from html import unescape
import re

import pytest

from conftest import login_as_tenant
from src.lead_intake import ESTIMATED_BUDGETS, PREVIOUS_ESTIMATED_BUDGETS, lead_intake


def old_inquiry(message='100 invitados\nFotos al atardecer <3', collection=''):
    qualification = dict(version=1, service='Fotografía y video de boda',
                         budget_fit_declared='Necesitamos orientación', collection=collection)
    prefix = ('Servicio solicitado: Fotografía y video de boda\n'
              'Encaje de inversión declarado: Necesitamos orientación')
    if collection:
        prefix += '\nColección de interés: ' + collection
    prefix += '\nSolicitud: revisar disponibilidad y propuesta. Disponibilidad y encaje pendientes de revisión.'
    return dict(intake_qualification=qualification, notas=prefix + ('\n\n' + message if message else ''))


@pytest.mark.parametrize('message', ['100 invitados\nFotos al atardecer <3', ''])
@pytest.mark.parametrize('collection', ['', 'Cobertura completa'])
def test_old_prefix_is_separated_only_for_display(message, collection):
    lead = old_inquiry(message, collection)
    snapshot = deepcopy(lead)
    result = lead_intake(lead)
    assert result['message'] == message
    assert ('Servicio', 'Fotografía y video de boda') in result['answers']
    assert any(value == 'Necesitan ayuda para elegir una cobertura' for _, value in result['answers'])
    assert lead == snapshot


def test_edited_notes_and_customer_text_are_not_guessed_or_deleted():
    lead = old_inquiry()
    # Partial/edited prefixes are ambiguous and must remain intact.
    lead['notas'] = lead['notas'].replace('Solicitud:', 'Mi solicitud:')
    assert lead_intake(lead)['message'] == lead['notas']
    # Customers can type any text, including the exact words of the old prefix.
    raw = old_inquiry()['notas']
    assert lead_intake({'notas': raw})['message'] == raw
    for version in (2, 3):
        lead = old_inquiry()
        lead['intake_qualification']['version'] = version
        assert lead_intake(lead)['message'] == lead['notas']
    assert lead_intake({'notas': None, 'notes': 'Old value'})['message'] == ''
    assert lead_intake({'notes': 'Legacy English key'})['message'] == 'Legacy English key'


@pytest.mark.parametrize('slug,tenant_id', [
    ('norkevin-photography', 'tenant-norkevin-photography'),
    ('astral-weddings', 'tenant-norkevin'),
])
@pytest.mark.parametrize('budget', [*dict.fromkeys(ESTIMATED_BUDGETS + PREVIOUS_ESTIMATED_BUDGETS), ''])
def test_new_intake_separates_answers_message_and_owner_email(client, monkeypatch, slug, tenant_id, budget):
    import app as crm
    from flask import g

    notices = []
    monkeypatch.setattr('src.mail_tracker.MailTracker.send_new_lead_notification',
                        lambda self, **kwargs: notices.append(kwargs))
    payload = dict(tenant_slug=slug, nombre='Consulta sintética', email='test@example.invalid',
                   fecha_tentativa='2034-06-17', intake_version='3',
                   servicio_solicitado='Fotografía de bebé', presupuesto=budget,
                   notas='100 invitados\nMensaje propio <script>prueba</script>')
    response = client.post('/api/captacion', json=payload)
    assert response.status_code == 200
    with crm.app.test_request_context():
        g.public_tenant_id = tenant_id
        lead = crm.get_lead(response.json['lead_id'])
        expected_budget = budget or 'Aún no lo tenemos claro'
        assert lead['notas'] == payload['notas']
        assert lead['presupuesto'] == expected_budget
        assert lead['intake_qualification'] == dict(
            version=3, service='Fotografía de bebé', estimated_budget=expected_budget, status='Pendiente de revisión')
        assert 'catalog_url' not in lead['intake_qualification']
        g.public_tenant_id = 'tenant-norkevin' if slug == 'norkevin-photography' else 'tenant-norkevin-photography'
        assert crm.get_lead(lead['id']) is None

    assert len(notices) == 1
    notice = notices[0]
    assert notice['tenant_id'] == tenant_id
    assert 'Respuestas del formulario:' in notice['body']
    assert 'Presupuesto estimado: ' + expected_budget in notice['body']
    assert notice['body'].endswith('https://flowingcrm.com/leads/' + lead['id'])
    assert 'Mensaje del cliente:\n' + payload['notas'] in notice['body']
    assert '<script>' not in notice['html_body']
    assert 'Encaje de inversión' not in notice['body']


@pytest.mark.parametrize('slug', ['norkevin-photography', 'astral-weddings'])
def test_public_form_has_estimates_services_and_no_catalogs(client, slug):
    html = client.get('/captacion/' + slug).text
    assert '¿Tienen algún presupuesto estimado?' in html
    assert '¿Qué más les gustaría contarnos?' in html
    assert 'name="intake_version" value="3"' in html
    assert 'drive.google.com' not in html
    assert 'name="coleccion_interes"' not in html
    assert 'encajan con su inversión' not in html
    assert 'id="presupuesto"' in html
    assert 'required' not in re.search(r'<select id="presupuesto"[^>]*>', html).group()
    budget_select = re.search(r'<select id="presupuesto"[^>]*>(.*?)</select>', html, re.S).group(1)
    assert re.findall(r'<option[^>]*>(.*?)</option>', budget_select) == [
        'Selecciona un rango', 'Q10,000', 'Q15,000', 'Q20,000 o más', 'Aún no lo tenemos claro',
    ]
    for service in ('Fotografía de boda', 'Video de boda', 'Fotografía y video de boda',
                    'Sesión / Save the Date', 'Fotografía de bebé', 'Pedida de mano', 'Otro servicio'):
        assert service in html


@pytest.mark.parametrize('budget', [[], {}, 12, None, 'Sí, la inversión es compatible'])
def test_new_budget_validation_does_not_create_partial_records(client, monkeypatch, budget):
    import app as crm
    before = crm.store.list('leads')
    monkeypatch.setattr(crm, 'trigger_workflow_for_lead', lambda *a, **kw: pytest.fail('Invalid submission'))
    response = client.post('/api/captacion', json=dict(
        tenant_slug='astral-weddings', nombre='Invalid', fecha_tentativa='2034-06-17',
        intake_version='3', servicio_solicitado='Video de boda', presupuesto=budget))
    assert response.status_code == 400
    assert crm.store.list('leads') == before


@pytest.mark.parametrize('tenant_id', ['tenant-norkevin', 'tenant-norkevin-photography'])
def test_existing_leads_and_comparison_show_separate_answers_without_migration(client, tenant_id):
    import app as crm
    login_as_tenant(client, tenant_id)
    original = dict(old_inquiry(), id='intake-old', tenant_id=tenant_id, nombre='Consulta anterior',
                    email='repeat@example.invalid', fecha_tentativa='2034-06-17', status='Nuevo')
    newer = dict(id='intake-new', tenant_id=tenant_id, nombre='Consulta nueva',
                 email=original['email'], fecha_tentativa=original['fecha_tentativa'], status='Nuevo',
                 notas='Mi pedido: ceremonia a las 16:00.', presupuesto='Q10,000 a Q14,999',
                 intake_qualification=dict(version=3, service='Pedida de mano', estimated_budget='Q10,000 a Q14,999'))
    for lead in (original, newer):
        crm.store.upsert('leads', lead)
    html = client.get('/leads/intake-old').text
    assert html.count('Respuestas del formulario') >= 3  # Current and both repeated inquiries.
    assert 'Mensaje del cliente' in html
    assert 'Encaje de inversión declarado:' not in html
    assert 'Solicitud: revisar disponibilidad' not in html
    message = unescape(re.search(r'<textarea[^>]*id="lead-notes"[^>]*>(.*?)</textarea>', html, re.S).group(1))
    assert message == '100 invitados\nFotos al atardecer <3'
    assert 'Mi pedido: ceremonia a las 16:00.' in html
    assert 'Presupuesto estimado' in html
    assert 'Pedida de mano' in html
    assert crm.get_lead(original['id'])['notas'] == original['notas']
    # Saving or clearing the displayed message cannot remove its form answers.
    assert client.post('/api/leads/intake-old/update', json={'notas': 'Mensaje corregido'}).status_code == 200
    assert crm.get_lead(original['id'])['intake_qualification'] == original['intake_qualification']
    assert client.post('/api/leads/intake-old/update', json={'notas': ''}).status_code == 200
    html = client.get('/leads/intake-old').text
    assert re.search(r'<textarea[^>]*id="lead-notes"[^>]*>(.*?)</textarea>', html, re.S).group(1) == ''
