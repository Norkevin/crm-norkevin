"""Navegacion entre entidades y jerarquia de la ficha del job.

Kevin trabaja saltando: dashboard -> boda -> cliente -> sus otras bodas,
y lead -> cotizacion -> boda. Si cada salto necesita el boton atras del
navegador, el CRM se vuelve lento de usar aunque cada pagina cargue
rapido.

Ademas, al abrir una boda lo primero que se mira es cuanto falta por
cobrar. Ese resumen estaba DENTRO de la pestana "Facturas": al cambiar a
Cotizaciones o Contratos, el saldo desaparecia de la pantalla.
"""
import re

import pytest

from conftest import login_as_tenant

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'
AMBAS = [ASTRAL, NORKEVIN]


def _boda_completa(app_module, tenant_id, sufijo='nav'):
    """Una boda con dos clientes, cotizacion y cuotas."""
    novio = {'id': f'cli-nav-a-{sufijo}-{tenant_id}', 'tenant_id': tenant_id,
             'first_name': 'Novio', 'last_name': 'Nav',
             'email': f'novio-{sufijo}@example.invalid'}
    novia = {'id': f'cli-nav-b-{sufijo}-{tenant_id}', 'tenant_id': tenant_id,
             'first_name': 'Novia', 'last_name': 'Nav',
             'email': f'novia-{sufijo}@example.invalid'}
    for c in (novio, novia):
        app_module.store.upsert('clients', c)

    job = {'id': f'job-nav-{sufijo}-{tenant_id}', 'tenant_id': tenant_id,
           'nombre': f'Boda Navegacion {sufijo}', 'status': 'Confirmado',
           'boda_date': '2026-11-14', 'price_total': 12000,
           'client_id': novio['id']}
    app_module.store.upsert('jobs', job)
    app_module._set_job_clients(job, [(novio['id'], app_module.ROL_PRINCIPAL),
                                      (novia['id'], app_module.ROL_PAREJA)],
                                tenant_id=tenant_id)
    app_module.store.upsert('payments', {
        'id': f'pay-nav-{sufijo}-{tenant_id}', 'tenant_id': tenant_id,
        'job_id': job['id'], 'client_id': novio['id'],
        'amount': 12000, 'paid_amount': 4000, 'status': 'Pendiente',
        'due_date': '2026-10-01',
    })
    return job, novio, novia


# ============================================================
# Jerarquia de la ficha del job
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_el_resumen_de_pagos_no_esta_escondido_en_una_pestana(auth_client, tenant_id):
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
    job, _novio, _novia = _boda_completa(app_module, tenant_id, 'jer')

    html = auth_client.get(f"/jobs/{job['id']}").get_data(as_text=True)

    pos_resumen = html.find('Resumen de pagos')
    pos_tabs = html.find('id="job-detail-tabs-nav"')
    assert pos_resumen != -1, 'no aparece el resumen de pagos'
    assert pos_tabs != -1, 'no aparecen las pestanas'
    assert pos_resumen < pos_tabs, (
        'el resumen financiero volvio a quedar dentro de las pestanas: al '
        'cambiar de pestana el saldo desaparece de la pantalla'
    )


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_la_ficha_del_job_abre_con_lo_esencial(auth_client, tenant_id):
    """Titulo, gente y dinero en la misma pantalla, sin abrir nada."""
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
    job, novio, novia = _boda_completa(app_module, tenant_id, 'esencial')

    html = auth_client.get(f"/jobs/{job['id']}").get_data(as_text=True)

    assert job['nombre'] in html
    assert novio['first_name'] in html and novia['first_name'] in html
    assert 'Resumen de pagos' in html


# ============================================================
# Saltos entre entidades
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_de_la_boda_se_llega_al_cliente_y_del_cliente_a_sus_bodas(auth_client, tenant_id):
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
    job, novio, novia = _boda_completa(app_module, tenant_id, 'salto')

    html_job = auth_client.get(f"/jobs/{job['id']}").get_data(as_text=True)
    assert f"/clients/{novio['id']}" in html_job, 'la boda no enlaza a su cliente'

    # Y desde la ficha de la novia (rol pareja) se vuelve a la boda.
    html_cli = auth_client.get(f"/clients/{novia['id']}").get_data(as_text=True)
    assert f"/jobs/{job['id']}" in html_cli, 'el cliente no enlaza de vuelta a la boda'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_la_ficha_del_job_ofrece_las_secciones_de_trabajo(auth_client, tenant_id):
    """Cotizaciones, contratos, cuestionarios y archivos accesibles sin
    salir de la boda."""
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
    job, _a, _b = _boda_completa(app_module, tenant_id, 'secciones')

    html = auth_client.get(f"/jobs/{job['id']}").get_data(as_text=True)
    for seccion in ('Cotizaciones', 'Contratos', 'Cuestionarios', 'Archivos'):
        assert seccion in html, f'falta la seccion {seccion} en la ficha del job'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_las_pantallas_de_uso_diario_responden(auth_client, tenant_id):
    """Recorrido minimo: si alguna de estas tira 500, el dia se traba."""
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
    job, novio, _novia = _boda_completa(app_module, tenant_id, 'recorrido')

    for ruta in ('/dashboard', '/jobs', '/clients', '/leads', '/calendar',
                 f"/jobs/{job['id']}", f"/clients/{novio['id']}"):
        resp = auth_client.get(ruta)
        assert resp.status_code == 200, f'{ruta} respondio {resp.status_code}'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_el_menu_esta_en_todas_las_pantallas(auth_client, tenant_id):
    """Sin menu no hay forma de salir de una pantalla salvo el boton atras."""
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    for ruta in ('/dashboard', '/jobs', '/clients', '/calendar'):
        html = auth_client.get(ruta).get_data(as_text=True)
        for destino in ('/jobs', '/clients', '/leads'):
            assert re.search(rf'href="{destino}[/"]', html), \
                f'{ruta} no ofrece como ir a {destino}'


# ============================================================
# Backlog B (27-ago-2026): lead -> cliente -> job -> cotizacion -> contrato
# -> pagos. Los saltos de arriba (boda <-> cliente) ya existian; estos
# cuatro son los que le faltaban al recorrido completo que pidio Kevin.
# quote_view.html y contract_view.html quedan afuera a proposito: son las
# paginas que ve el CLIENTE final (ver app.py, quote_view()/contract_view(),
# sin @login_required y sin resolver tenant de la sesion) -- agregarles
# navegacion interna del CRM les mostraria a los clientes links que no
# pueden usar. Por eso los saltos hacia el contrato se agregan en el ORIGEN
# (lead/cliente), no en el documento en si.
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_de_la_boda_se_llega_al_lead_que_le_dio_origen(auth_client, tenant_id):
    """Antes no habia forma de volver del job al lead, aunque `lead` ya
    viene calculado en el contexto de job_detail() (app.py)."""
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    lead = {'id': f'lead-nav-origen-{tenant_id}', 'tenant_id': tenant_id,
            'nombre': 'Lead Origen Nav', 'status': 'Convertido'}
    app_module.store.upsert('leads', lead)
    job = {'id': f'job-nav-desde-lead-{tenant_id}', 'tenant_id': tenant_id,
           'nombre': 'Boda Desde Lead Nav', 'status': 'Confirmado',
           'lead_id': lead['id']}
    app_module.store.upsert('jobs', job)

    html = auth_client.get(f"/jobs/{job['id']}").get_data(as_text=True)
    assert f"/leads/{lead['id']}" in html, \
        'el job no enlaza de vuelta al lead que le dio origen'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_del_lead_se_llega_a_la_vista_del_contrato_no_solo_al_pdf(auth_client, tenant_id):
    """El boton decia "View" pero llevaba directo al PDF -- nunca a la
    vista del contrato (estado de firma, terminos)."""
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    lead = {'id': f'lead-nav-contrato-{tenant_id}', 'tenant_id': tenant_id,
            'nombre': 'Lead Con Contrato Nav', 'status': 'Convertido'}
    app_module.store.upsert('leads', lead)
    contract_id = f'contract-nav-lead-{tenant_id}'
    app_module.store.upsert('contracts', {
        'id': contract_id, 'tenant_id': tenant_id,
        'lead_id': lead['id'], 'tipo': 'boda', 'status': 'Enviado',
    })

    html = auth_client.get(f"/leads/{lead['id']}").get_data(as_text=True)
    # Comilla de cierre pegada al id: distingue el link a la vista
    # (/contracts/{id}") del link al PDF (/contracts/{id}/pdf"), que ya
    # existia y no hay que confundir con el nuevo.
    assert f'href="/contracts/{contract_id}"' in html, \
        'falta el link a la vista del contrato (no solo al PDF)'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_del_cliente_se_llega_a_la_vista_del_contrato_no_solo_al_pdf(auth_client, tenant_id):
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    client = {'id': f'cli-nav-contrato-{tenant_id}', 'tenant_id': tenant_id,
              'first_name': 'Cliente', 'last_name': 'ConContratoNav'}
    app_module.store.upsert('clients', client)
    contract_id = f'contract-nav-cliente-{tenant_id}'
    app_module.store.upsert('contracts', {
        'id': contract_id, 'tenant_id': tenant_id,
        'client_id': client['id'], 'tipo': 'boda', 'status': 'Enviado',
    })

    html = auth_client.get(f"/clients/{client['id']}").get_data(as_text=True)
    assert f'href="/contracts/{contract_id}"' in html, \
        'falta el link a la vista del contrato (no solo al PDF)'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_del_editor_de_cotizacion_se_llega_al_job_cliente_y_lead(auth_client, tenant_id):
    """quote_edit() es la pagina interna (con sesion) para armar opciones
    de paquete -- a diferencia de quote_view.html, que es la que ve el
    cliente. Antes no tenia ningun link de salida."""
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    lead = {'id': f'lead-nav-quote-{tenant_id}', 'tenant_id': tenant_id,
            'nombre': 'Lead Quote Nav', 'status': 'Nuevo'}
    client = {'id': f'cli-nav-quote-{tenant_id}', 'tenant_id': tenant_id,
              'first_name': 'Cliente', 'last_name': 'QuoteNav'}
    job = {'id': f'job-nav-quote-{tenant_id}', 'tenant_id': tenant_id,
           'nombre': 'Boda Quote Nav', 'status': 'Confirmado',
           'client_id': client['id'], 'lead_id': lead['id']}
    quote = {'id': f'quote-nav-{tenant_id}', 'tenant_id': tenant_id, 'status': 'Borrador',
             'job_id': job['id'], 'client_id': client['id'], 'lead_id': lead['id']}
    app_module.store.upsert('leads', lead)
    app_module.store.upsert('clients', client)
    app_module.store.upsert('jobs', job)
    app_module.store.upsert('quotes', quote)

    resp = auth_client.get(f"/quotes/{quote['id']}/edit")
    assert resp.status_code == 200, 'no deberia redirigir: el quote esta en Borrador'
    html = resp.get_data(as_text=True)
    assert f"/jobs/{job['id']}" in html, 'el editor de cotizacion no enlaza al job'
    assert f"/clients/{client['id']}" in html, 'el editor de cotizacion no enlaza al cliente'
    assert f"/leads/{lead['id']}" in html, 'el editor de cotizacion no enlaza al lead'
