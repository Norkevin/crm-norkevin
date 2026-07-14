"""Verifica que las paginas que los CLIENTES usan (sin login) sigan
funcionando: portal, ver cotizacion/contrato, descargar PDFs."""
import json
import os


def _load(table):
    data_dir = os.environ['CRM_DATA_DIR']
    path = os.path.join(data_dir, f'{table}.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _first_id(table):
    records = _load(table)
    return records[0]['id'] if records else None


def test_client_portal_loads_for_real_client(client):
    client_id = _first_id('clients')
    assert client_id, 'necesita al menos un client en los datos de prueba'
    resp = client.get(f'/portal/{client_id}')
    assert resp.status_code == 200
    assert b'Client Portal' in resp.data or 'Portal'.encode() in resp.data


def test_client_portal_404s_for_unknown_client(client):
    resp = client.get('/portal/no-existe-este-cliente')
    assert resp.status_code == 404


def test_quote_view_and_pdf_are_public(client):
    quote_id = _first_id('quotes')
    assert quote_id
    resp = client.get(f'/quotes/{quote_id}')
    assert resp.status_code == 200
    resp = client.get(f'/quotes/{quote_id}/pdf')
    assert resp.status_code == 200
    assert resp.mimetype == 'application/pdf'


def test_contract_view_and_pdf_are_public(client):
    contract_id = _first_id('contracts')
    assert contract_id
    resp = client.get(f'/contracts/{contract_id}')
    assert resp.status_code == 200
    resp = client.get(f'/contracts/{contract_id}/pdf')
    assert resp.status_code == 200
    assert resp.mimetype == 'application/pdf'


def test_invoice_admin_view_requires_login_but_pdf_is_public(client):
    payments = _load('payments')
    invoice_id = next((p['invoice_id'] for p in payments if p.get('invoice_id')), None)
    assert invoice_id, 'necesita al menos un payment con invoice_id'

    resp = client.get(f'/invoices/{invoice_id}')
    assert resp.status_code == 302, 'la vista interna de factura debe exigir login'

    resp = client.get(f'/invoices/{invoice_id}/pdf')
    assert resp.status_code == 200, 'el PDF de la factura debe ser publico'
    assert resp.mimetype == 'application/pdf'
