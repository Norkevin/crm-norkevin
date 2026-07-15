"""Verifica que las paginas que los CLIENTES usan (sin login) sigan
funcionando: portal, ver cotizacion/contrato, descargar PDFs."""
import json
import os
import uuid

import pytest


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
    import app as app_module

    quote_id = _first_id('quotes')
    if not quote_id:
        client_id = 'client-public-quote-' + uuid.uuid4().hex[:8]
        lead_id = 'lead-public-quote-' + uuid.uuid4().hex[:8]
        quote_id = 'quote-public-' + uuid.uuid4().hex[:8]
        app_module.store.upsert('clients', {
            'id': client_id,
            'first_name': 'Cliente',
            'last_name': 'Cotizacion',
            'email': 'cliente-cotizacion@example.com',
            'tenant_id': 'tenant-norkevin',
        })
        app_module.store.upsert('leads', {
            'id': lead_id,
            'client_id': client_id,
            'nombre': 'Boda cotizacion publica',
            'client_name': 'Cliente Cotizacion',
            'tipo': 'BODAS',
            'tenant_id': 'tenant-norkevin',
        })
        app_module.store.upsert('quotes', {
            'id': quote_id,
            'lead_id': lead_id,
            'client_id': client_id,
            'status': 'Pendiente',
            'quote_kind': 'fixed',
            'paquete_nombre': 'Paquete prueba',
            'total': 1000.0,
            'options': [{'name': 'Paquete prueba', 'price': 1000.0, 'description': 'Servicio de prueba'}],
            'tenant_id': 'tenant-norkevin',
        })
    resp = client.get(f'/quotes/{quote_id}')
    assert resp.status_code == 200
    resp = client.get(f'/quotes/{quote_id}/pdf')
    assert resp.status_code == 200
    assert resp.mimetype == 'application/pdf'


def test_contract_view_and_pdf_are_public(client):
    contract_id = _first_id('contracts')
    if not contract_id:
        pytest.skip('los datos de prueba actuales no tienen contratos')
    resp = client.get(f'/contracts/{contract_id}')
    assert resp.status_code == 200
    resp = client.get(f'/contracts/{contract_id}/pdf')
    assert resp.status_code == 200
    assert resp.mimetype == 'application/pdf'


def test_invoice_admin_view_requires_login_but_pdf_is_public(client):
    import app as app_module

    client_id = 'client-public-invoice-' + uuid.uuid4().hex[:8]
    job_id = 'job-public-invoice-' + uuid.uuid4().hex[:8]
    invoice_id = 'INV-PUBLIC-' + uuid.uuid4().hex[:6].upper()
    app_module.store.upsert('clients', {
        'id': client_id,
        'first_name': 'Cliente',
        'last_name': 'Factura',
        'email': 'cliente-factura@example.com',
        'tenant_id': 'tenant-norkevin',
    })
    app_module.upsert_job({
        'id': job_id,
        'nombre': 'Boda factura publica',
        'client_id': client_id,
        'status': 'Confirmado',
        'tenant_id': 'tenant-norkevin',
    })
    app_module.store.upsert('payments', {
        'id': 'pay-public-invoice-' + uuid.uuid4().hex[:8],
        'invoice_id': invoice_id,
        'client_id': client_id,
        'job_id': job_id,
        'amount': 1000.0,
        'status': 'Pendiente',
        'due_date': '2035-01-10',
        'tenant_id': 'tenant-norkevin',
    })

    resp = client.get(f'/invoices/{invoice_id}')
    assert resp.status_code == 302, 'la vista interna de factura debe exigir login'

    resp = client.get(f'/invoices/{invoice_id}/pdf')
    assert resp.status_code == 200, 'el PDF de la factura debe ser publico'
    assert resp.mimetype == 'application/pdf'
