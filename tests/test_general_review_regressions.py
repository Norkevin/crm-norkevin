import pytest


@pytest.mark.parametrize('path', ['/payments', '/invoices', '/quotes', '/api/payments/export.csv'])
def test_financial_lists_accept_clients_without_a_last_name(auth_client, monkeypatch, path):
    import app as crm
    monkeypatch.setattr(crm, 'list_clients', lambda: [{'id': 'review-client', 'first_name': 'Kevin'}])
    monkeypatch.setattr(crm, 'list_jobs', lambda: [{'id': 'review-job', 'nombre': 'Boda revisión'}])
    monkeypatch.setattr(crm, 'list_quotes', lambda: [{'id': 'review-quote', 'client_id': 'review-client'}])
    monkeypatch.setattr(crm, '_visible_billable_payments', lambda *args, **kwargs: [
        {'id': 'review-payment', 'client_id': 'review-client', 'job_id': 'review-job',
         'invoice_id': 'review-invoice', 'amount': 100, 'status': 'Pendiente', 'due_date': '2026-10-10'}])
    response = auth_client.get(path)
    assert response.status_code == 200
    assert 'Kevin' in response.get_data(as_text=True)


def test_cached_email_partner_keeps_the_correct_name(flask_app, monkeypatch):
    import app as crm
    def unexpected_read(*args, **kwargs):
        raise AssertionError('La plantilla relee los clientes del job')
    monkeypatch.setattr(crm, '_job_client_relations', unexpected_read)
    body = 'Hola %client_name% y %2nd_client_name%'
    assert crm._render_message_template(body, client={'first_name': 'Kevin'},
        partner_cache={'client': {'first_name': 'Astrid'}}) == 'Hola Kevin y Astrid'
    assert crm._render_message_template(body, client={'first_name': 'Kevin'},
        partner_cache={'client': None}) == 'Hola Kevin'


def test_cached_questionnaire_preserves_previous_answers(flask_app, monkeypatch):
    import app as crm
    monkeypatch.setattr(crm, '_questionnaire_template', lambda *args: pytest.fail('Relectura de plantilla'))
    questionnaire = {'name': 'Original', 'status': 'Respondido', 'answers': {'name': 'Kevin'}}
    result = crm._linked_questionnaire(questionnaire, template_cache={'name': 'Nueva'})
    assert result == questionnaire
