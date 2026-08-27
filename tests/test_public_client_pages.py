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


# ============================================================
# El portal respeta el rol de cada cliente en el job (26-ago)
# ============================================================
#
# client_portal() buscaba los jobs de un cliente mirando solo job.client_id
# (el principal). La pareja -- que SI recibe documentos, segun
# ROLES_DESTINATARIOS_DOCUMENTOS -- entraba a SU PROPIO link de portal y lo
# veia completamente vacio: sin su boda, sin su cotizacion, sin su
# contrato, sin sus cuotas. Ademas, pagos y contrato se crean siempre con
# el client_id del principal (ver _ensure_payments_for_quote /
# api_contract_new), asi que ni siquiera alcanzaba con reconocer el job:
# tambien hacia falta el fallback por job_id en esas dos listas.
#
# El wedding planner, en cambio, NO esta en ROLES_DESTINATARIOS_DOCUMENTOS
# a proposito (la regla de "el planner nunca recibe contratos" tambien
# aplica aca): su portal debe seguir vacio, y eso no es un bug.

def _boda_con_tres_roles(app_module, sufijo):
    tenant_id = 'tenant-norkevin'
    principal = {'id': f'cli-portal-principal-{sufijo}', 'tenant_id': tenant_id,
                 'first_name': 'Principal', 'last_name': 'Rol'}
    pareja = {'id': f'cli-portal-pareja-{sufijo}', 'tenant_id': tenant_id,
              'first_name': 'Pareja', 'last_name': 'Rol'}
    planner = {'id': f'cli-portal-planner-{sufijo}', 'tenant_id': tenant_id,
               'first_name': 'Planner', 'last_name': 'Rol'}
    for c in (principal, pareja, planner):
        app_module.store.upsert('clients', c)

    job = {'id': f'job-portal-roles-{sufijo}', 'tenant_id': tenant_id,
           'nombre': 'Boda Portal Roles', 'client_id': principal['id'],
           'status': 'Confirmado', 'price_total': 8000,
           'location': f'Salon Portal Roles {sufijo}'}
    app_module.store.upsert('jobs', job)
    app_module._set_job_clients(job, [
        (principal['id'], app_module.ROL_PRINCIPAL),
        (pareja['id'], app_module.ROL_PAREJA),
        (planner['id'], app_module.ROL_PLANNER),
    ], tenant_id=tenant_id)

    contract_id = f'contract-portal-roles-{sufijo}'
    app_module.store.upsert('contracts', {
        'id': contract_id, 'job_id': job['id'], 'client_id': principal['id'],
        'tenant_id': tenant_id, 'status': 'Borrador', 'signed': False,
        'created': '2026-08-26',
    })
    app_module.store.upsert('payments', {
        'id': f'pay-portal-roles-{sufijo}', 'invoice_id': f'INV-ROLES-{sufijo.upper()}',
        'client_id': principal['id'], 'job_id': job['id'], 'amount': 8000,
        'status': 'Pendiente', 'due_date': '2027-01-01', 'concepto': 'Cuota unica',
        'tenant_id': tenant_id,
    })
    return principal, pareja, planner, job, contract_id


def test_la_pareja_ve_su_propia_boda_en_su_portal(client):
    import app as app_module
    _principal, pareja, _planner, job, contract_id = _boda_con_tres_roles(app_module, 'a')

    resp = client.get(f'/portal/{pareja["id"]}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # client_portal.html no imprime job.nombre en ningun lado (ni para el
    # principal): muestra location/package/fecha. location si se imprime
    # cuando primary_job esta seteado, asi que es la señal correcta de que
    # el job de la pareja se encontro y quedo como primary_job.
    assert job['location'] in html, 'la pareja no ve su propia boda en su portal'
    assert 'INV-ROLES-A' in html, 'la pareja no ve la cuota (creada con el client_id del principal)'
    assert f'/contracts/{contract_id}' in html, 'la pareja no ve el contrato (creado con el client_id del principal)'


def test_el_wedding_planner_no_ve_documentos_en_su_portal(client):
    """No es un bug: el planner no esta en ROLES_DESTINATARIOS_DOCUMENTOS."""
    import app as app_module
    _principal, _pareja, planner, job, contract_id = _boda_con_tres_roles(app_module, 'b')

    resp = client.get(f'/portal/{planner["id"]}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert job['location'] not in html
    assert 'INV-ROLES-B' not in html
    assert f'/contracts/{contract_id}' not in html
