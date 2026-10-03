from src.email_delivery import render_email_html, unresolved_email_fields


def test_layout_escapes_content_and_preserves_paragraphs_and_links():
    html = render_email_html('Paquetes <b>', 'Hola Ana,\n\nFecha: 12 de octubre\nLugar: Antigua\n\nhttps://example.com/packages?a=1&b=2\n\n<script>alert(1)</script>')
    assert '<h1 ' in html and 'Paquetes &lt;b&gt;' in html
    assert html.count('<p ') == 4 and '<br>' in html
    assert 'href="https://example.com/packages?a=1&amp;b=2"' in html
    assert '<script>' not in html
    assert 'max-width:600px' in html


def test_all_unfinished_placeholder_formats_are_detected():
    assert unresolved_email_fields('Hola {{nombre}}', '%job_date% [LINK: Google Review] $jobName$') == [
        '$jobName$', '%job_date%', '[LINK: Google Review]', '{{nombre}}']
    assert not unresolved_email_fields('Descuento 20%', 'https://example.com')


def test_date_is_resolved_and_missing_date_stays_visible(flask_app):
    import app as crm
    with crm.app.test_request_context('/'):
        text = crm._render_message_template('%job_date%', lead={'nombre': 'Ana', 'fecha_tentativa': '2027-10-12'})
        assert '2027' in text and '%' not in text
        assert crm._render_message_template('%job_date%', lead={'nombre': 'Ana'}) == '%job_date%'


def test_preview_matches_delivery_renderer_and_never_sends(auth_client):
    response = auth_client.post('/api/email-preview', json={'subject': 'Paquetes', 'body': 'Hola Ana,\n\n%job_date%'})
    assert response.status_code == 200
    data = response.get_json()
    assert data['html'] == render_email_html('Paquetes', 'Hola Ana,\n\n%job_date%')
    assert data['unresolved'] == ['%job_date%']
    assert auth_client.post('/api/email-preview', json={'body': []}).status_code == 400


def test_delivery_message_contains_text_and_same_html():
    from src.email_delivery import build_email_message
    message = build_email_message('client@example.invalid', 'Paquetes', 'Hola Ana,\n\nDetalles', 'owner@example.invalid')
    parts = message.get_payload()
    assert [p.get_content_type() for p in parts] == ['text/plain', 'text/html']
    assert parts[0].get_content().strip() == 'Hola Ana,\n\nDetalles'
    assert parts[1].get_content().strip() == render_email_html('Paquetes', 'Hola Ana,\n\nDetalles')


def test_editing_template_preserves_attachments(auth_client):
    import app as crm
    with auth_client.application.test_request_context('/'):
        from flask import session
        with auth_client.session_transaction() as current:
            session.update(dict(current))
        crm.store.upsert('email_templates', {'id': 'tpl-presentation-attachments', 'name': 'Adjuntos', 'adjuntos': ['packages.pdf']})
    response = auth_client.post('/api/settings/email-templates', json={
        'id': 'tpl-presentation-attachments', 'name': 'Adjuntos', 'cuerpo': 'Hola,\n\nPaquetes'})
    assert response.status_code == 200
    assert response.get_json()['template']['adjuntos'] == ['packages.pdf']


def test_standalone_labeled_links_become_readable_buttons():
    html = render_email_html('Paquetes', 'Paquetes de boda\nhttps://example.com/packages')
    assert 'href="https://example.com/packages"' in html
    assert '>Paquetes de boda</a>' in html
    assert '>https://example.com/packages</a>' not in html
    assert 'background:#38596b' in html
