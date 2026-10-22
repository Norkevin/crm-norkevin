"""Questionnaire exports retain text and the dashboard's authentication boundary."""
import shutil
import subprocess
from io import BytesIO
from copy import deepcopy
from urllib.parse import quote

import pytest

from src.pdf_questionnaire import render_questionnaire_pdf


def _seed(questionnaire_id='questionnaire-pdf', **extra):
    import app as crm
    record = {
        'id': questionnaire_id, 'tenant_id': 'tenant-norkevin',
        'name': 'Cuestionario original de Ana y José', 'status': 'Respondido',
        'description': 'Detalles de nuestra celebración.', 'answered_at': '2026-10-22T14:35:00',
        'questions': [{
            'group': 'Nuestra boda original', 'description': 'Horarios y preferencias.',
            'fields': [
                {'id': 'venue', 'label': '¿Dónde será la ceremonia?', 'type': 'text', 'help': 'Incluye dirección.'},
                {'id': 'dance', 'label': '¿Habrá baile?', 'type': 'radio', 'options': ['Sí', 'No'],
                 'details': {'label': 'Canciones y detalles', 'when': ['Sí']}},
                {'id': 'empty', 'label': 'Datos por confirmar', 'type': 'text'},
            ],
        }],
        'answers': {'venue': 'Jardín <Principal> & Salón', 'dance': 'Sí', 'dance__details': 'Vals\nCanción favorita'},
        'answer_history': [{'id': 'old-venue', 'label': 'Lugar anterior', 'value': 'Capilla original'}],
    }
    record.update(extra)
    crm.store.upsert('questionnaires', record)
    return record


def _text(pdf, tmp_path):
    try:
        from pypdf import PdfReader
    except ImportError:
        pass
    else:
        return '\n'.join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
    executable = shutil.which('pdftotext')
    if not executable:
        pytest.skip('pypdf or pdftotext is required for PDF content verification')
    path = tmp_path / 'questionnaire.pdf'
    path.write_bytes(pdf)
    return subprocess.run([executable, '-enc', 'UTF-8', str(path), '-'],
                          check=True, capture_output=True, text=True).stdout


def test_owner_download_is_attachment_with_all_original_content(auth_client, sample_business, tmp_path):
    import app as crm
    q = _seed(job_id=sample_business['job']['id'], client_id=sample_business['client']['id'])
    response = auth_client.get(f"/questionnaires/{q['id']}/pdf")
    assert response.status_code == 200
    assert response.mimetype == 'application/pdf'
    assert response.data.startswith(b'%PDF')
    assert response.headers['Content-Disposition'] == 'attachment; filename=cuestionario-questionnaire-pdf.pdf'
    assert response.headers['Cache-Control'] == 'private, no-store'
    text = _text(response.data, tmp_path)
    for expected in (
        'Astral Weddings', q['name'], 'Nuestra boda original', 'Horarios y preferencias.',
        '¿Dónde será la ceremonia?', 'Incluye dirección.', 'Jardín <Principal> & Salón',
        '¿Habrá baile?', 'Sí', 'Canciones y detalles', 'Vals', 'Canción favorita',
        'Datos por confirmar', 'Sin respuesta', 'Lugar anterior', 'Capilla original',
        'Cliente Prueba', 'Boda de prueba', '22 de octubre de 2026', 'Página 1',
    ):
        assert expected in text
    assert 'Nombre completo de la novia' not in text, 'Completed questions must not be replaced by today\'s template'
    assert crm.store.get('questionnaires', q['id']) == q, 'Export must not alter saved answers'


def test_anonymous_download_requires_login_but_client_form_stays_public(client):
    q = _seed()
    response = client.get(f"/questionnaires/{q['id']}/pdf")
    assert response.status_code == 302
    assert '/login' in response.headers['Location']
    assert not response.data.startswith(b'%PDF')
    public = client.get(f"/questionnaires/{q['id']}")
    assert public.status_code == 200
    assert 'Descargar PDF' not in public.get_data(as_text=True)


def test_foreign_and_unknown_questionnaires_are_not_exposed(auth_client):
    _seed('questionnaire-pdf-foreign', tenant_id='tenant-norkevin-photography')
    for qid in ('questionnaire-pdf-foreign', 'questionnaire-pdf-missing'):
        assert auth_client.get(f'/questionnaires/{qid}/pdf').status_code == 404


def test_foreign_linked_client_and_job_are_not_exported(auth_client, tmp_path):
    import app as crm
    crm.store.upsert('clients', {'id': 'foreign-pdf-client', 'tenant_id': 'tenant-norkevin-photography',
                                'first_name': 'SECRET FOREIGN CLIENT'})
    crm.store.upsert('jobs', {'id': 'foreign-pdf-job', 'tenant_id': 'tenant-norkevin-photography',
                             'nombre': 'SECRET FOREIGN EVENT', 'client_id': 'foreign-pdf-client'})
    q = _seed(client_id='foreign-pdf-client', job_id='foreign-pdf-job')
    response = auth_client.get(f"/questionnaires/{q['id']}/pdf")
    assert response.status_code == 200
    assert 'SECRET FOREIGN' not in _text(response.data, tmp_path)


def test_download_links_are_available_on_owner_form_job_and_lead(auth_client, sample_business):
    q = _seed(job_id=sample_business['job']['id'], lead_id=sample_business['lead']['id'],
              client_id=sample_business['client']['id'])
    for path in (f"/questionnaires/{q['id']}", f"/jobs/{q['job_id']}", f"/leads/{q['lead_id']}"):
        response = auth_client.get(path)
        assert response.status_code == 200
        assert f'href="/questionnaires/{q["id"]}/pdf" download' in response.get_data(as_text=True)


def test_filename_is_sanitized(auth_client):
    q = _seed('questionnaire-";bad=.pdf')
    response = auth_client.get('/questionnaires/' + quote(q['id'], safe='') + '/pdf')
    assert response.status_code == 200
    disposition = response.headers['Content-Disposition']
    assert disposition.startswith('attachment; filename=cuestionario-')
    assert '"' not in disposition and disposition.count(';') == 1


def test_long_answers_lists_unknown_fields_and_history_are_not_truncated(tmp_path):
    q = _seed()
    original = deepcopy(q)
    long_answer = '\n'.join(f'Línea {i:03d}: fotografía y música, Ana & José <3.' for i in range(180))
    q['answers'].update({'venue': long_answer, 'guests': ['Ana', 'José'],
                         'preferences': {'Fotografía': True, 'Video': False, 'Cantidad': 0},
                         'markup': '<b>Texto literal</b> <script>alert(1)</script>',
                         'long-token': 'UNBROKEN' * 1500 + 'ULTIMO_TOKEN'})
    pdf = render_questionnaire_pdf(q, q['questions'], {'display_name': 'Norkevin Photography'},
                                   client_name='Ana y José', event_name='Nuestra boda',
                                   event_date='24 de octubre de 2026', completed_at='22 de octubre de 2026')
    text = _text(pdf, tmp_path)
    for expected in ('Línea 000', 'Línea 179', 'Respuestas adicionales guardadas', 'Ana', 'José',
                     'Fotografía: Sí', 'Video: No', 'Cantidad: 0', '<b>Texto literal</b>',
                     '<script>alert(1)</script>', 'ULTIMO_TOKEN', 'Lugar anterior', 'Capilla original'):
        assert expected in text
    assert text.count('Cuestionario - Preguntas y respuestas') >= 5
    assert original['answers']['venue'] == 'Jardín <Principal> & Salón'


def test_draft_export_preserves_removed_answers_without_completing_form(auth_client, tmp_path):
    import app as crm
    q = _seed(status='Draft', answered_at=None)
    response = auth_client.get(f"/questionnaires/{q['id']}/pdf")
    assert response.status_code == 200
    text = _text(response.data, tmp_path)
    assert 'Estado: Draft' in text
    assert 'Jardín <Principal> & Salón' in text
    assert crm.store.get('questionnaires', q['id']) == q


def test_download_uses_each_tenants_saved_theme_without_brand_leakage(client, tmp_path):
    import app as crm
    from conftest import login_as_tenant
    from reportlab.lib.colors import HexColor
    pypdf = pytest.importorskip('pypdf')

    brands = (
        ('tenant-norkevin', 'Astral Weddings', '#86205D'),
        ('tenant-norkevin-photography', 'Norkevin Photography', '#147B68'),
    )
    for tenant_id, name, color in brands:
        crm.store.save_tenant_dict('settings', {
            'quote_theme': {'primary': color, 'primary_dark': color, 'accent': color,
                            'display_name': 'OTHER ACCOUNT MUST NOT APPEAR'},
        }, tenant_id=tenant_id)

    documents = []
    for tenant_id, name, color in brands:
        login_as_tenant(client, tenant_id, email=f'{tenant_id}@example.invalid')
        q = _seed(f'questionnaire-{tenant_id}', tenant_id=tenant_id)
        response = client.get(f"/questionnaires/{q['id']}/pdf")
        assert response.status_code == 200
        text = _text(response.data, tmp_path)
        reader = pypdf.PdfReader(BytesIO(response.data))
        colors = {
            tuple(round(float(channel), 4) for channel in operands)
            for page in reader.pages
            for operands, operator in page.get_contents().operations
            if operator in (b'rg', b'RG')
        }
        documents.append((text, colors))

    for index, (_, name, color) in enumerate(brands):
        text, colors = documents[index]
        other_name = brands[1 - index][1]
        own_rgb = tuple(round(value, 4) for value in HexColor(color).rgb())
        other_rgb = tuple(round(value, 4) for value in HexColor(brands[1 - index][2]).rgb())
        assert name in text
        assert other_name not in text and 'OTHER ACCOUNT MUST NOT APPEAR' not in text
        assert own_rgb in colors, 'The exported PDF must use this account\'s saved visual theme'
        assert other_rgb not in colors, 'Another account\'s theme must not leak into the export'


def test_long_section_descriptions_and_event_metadata_can_span_pages(tmp_path):
    q = _seed()
    q['questions'][0]['description'] = '\n'.join(
        f'Preferencia {index:03d}: fotografía de nuestra celebración.' for index in range(110)
    )
    event_name = '\n'.join(f'Celebración {index:03d}: Ana y José.' for index in range(110))
    original = deepcopy(q)
    pdf = render_questionnaire_pdf(q, q['questions'], {'display_name': 'Astral Weddings'},
                                   client_name='Ana y José', event_name=event_name)
    text = _text(pdf, tmp_path)
    for expected in ('Celebración 000', 'Celebración 109', 'Preferencia 000', 'Preferencia 109',
                     '¿Dónde será la ceremonia?', 'Jardín <Principal> & Salón', 'Capilla original'):
        assert expected in text
    assert q == original
