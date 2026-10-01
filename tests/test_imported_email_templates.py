def test_studio_ninja_tokens_use_the_clients_own_portal(auth_client, monkeypatch):
    import app as crm
    monkeypatch.setenv('APP_BASE_URL', 'https://flowingcrm.com')
    with crm.app.test_request_context('/'):
        text = crm._render_message_template(
            '%client_name% y %2nd_client_name% | $jobName$ | %quote_link% | %contract_link% | %invoice_link% | %questionnaire_link% | %gallery_link%',
            client={'id': 'client-test', 'first_name': 'Ana', 'galeria_url': 'https://example.com/gallery'},
            job={'nombre': 'Boda Ana'},
        )
    assert text.startswith('Ana | Boda Ana |')
    for section in ('quotes', 'contracts', 'invoices', 'questionnaires'):
        assert 'https://flowingcrm.com/portal/client-test#' + section in text
    assert text.endswith('https://example.com/gallery')
    assert '%' not in text


def test_missing_client_does_not_create_a_broken_portal(flask_app):
    import app as crm
    with crm.app.test_request_context('/'):
        assert crm._render_message_template('%quote_link%') == '%quote_link%'


def test_tokens_work_without_request_context(flask_app, monkeypatch):
    import app as crm
    monkeypatch.setenv('APP_BASE_URL', 'https://flowingcrm.com')
    with crm.app.app_context():
        assert crm._render_message_template('%invoice_link%', client={'id': 'client-test'}) == 'https://flowingcrm.com/portal/client-test#invoices'


def test_missing_gallery_link_blocks_delivery_but_a_discount_does_not(monkeypatch):
    from src import email_delivery as delivery
    monkeypatch.setenv('OUTBOUND_EMAIL_ENABLED', '1')
    monkeypatch.delenv('DISABLE_OUTBOUND_EMAIL', raising=False)
    monkeypatch.setattr('src.gmail_delivery.is_connected', lambda **kw: True)
    calls = []
    monkeypatch.setattr(delivery, '_send_gmail', lambda *a, **kw: calls.append(a) or delivery.DeliveryResult(ok=True, provider='test'))
    result = delivery.send_email('test@example.invalid', 'Galería', '%gallery_link%')
    assert result.status == 'blocked' and '%gallery_link%' in result.error
    assert not calls
    assert delivery.send_email('test@example.invalid', 'Oferta 20%', 'https://example.com/gallery').ok
    assert len(calls) == 1
