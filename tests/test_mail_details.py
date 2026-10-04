import json
import re

import pytest


@pytest.mark.parametrize('entity', ['lead', 'job'])
def test_mail_view_shows_saved_body_without_simulating_open(auth_client, sample_business, entity):
    import app as crm

    record = sample_business[entity]
    body = 'Hola "Javier"\nPaquetes y precios: <script>alert("x")</script>'
    crm.store.upsert('mail_log', {
        'id': 'mail-view-test', 'tenant_id': 'tenant-norkevin',
        f'{entity}_id': record['id'], 'to': 'fixture@example.invalid',
        'subject': 'Paquetes & precios', 'body': body,
        'status': 'sent', 'sent_at': '2026-10-04T09:00:00',
    })
    response = auth_client.get(f'/{entity}s/{record["id"]}')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    payload = re.search(r'<script type="application/json" id="mail-body-mail-view-test">(.*?)</script>', html, re.S).group(1)
    assert json.loads(payload) == body
    assert '<script>alert' not in payload
    assert 'onclick="openMailDetails(this)"' in html
    assert 'id="mail-detail-modal"' in html
    assert 'function openMailDetails(button)' in html
    assert 'simulateOpen(' not in html
    assert crm.store.get('mail_log', 'mail-view-test')['status'] == 'sent'
