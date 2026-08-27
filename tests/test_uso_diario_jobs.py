"""Uso diario: clientes multiples, pagos, locacion y orden (agosto 2026).

Cubre los puntos 2, 3, 4 y 5 de la lista de Kevin. Cada bloque documenta
el bug concreto que corrige. Todo se prueba en las DOS marcas.
"""
import pytest

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'
AMBAS = [ASTRAL, NORKEVIN]
EMAIL = {ASTRAL: 'astralweddingsgt@gmail.com', NORKEVIN: 'norkevinfoto@gmail.com'}


def _cliente(app_module, tenant_id, sufijo, **extra):
    c = {'id': f'client-{sufijo}-{tenant_id}', 'tenant_id': tenant_id,
         'first_name': f'Nombre{sufijo}', 'last_name': 'Test'}
    c.update(extra)
    app_module.store.upsert('clients', c)
    return c


# ============================================================
# PUNTO 2 -- multiples clientes por job
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_job_con_dos_novios_y_planner_los_muestra_a_todos(tenant_id):
    """Antes la lista de jobs mostraba SOLO el cliente principal: los otros
    (la otra mitad de la pareja, el wedding planner) desaparecian."""
    import app as app_module

    a = _cliente(app_module, tenant_id, 'novioA')
    b = _cliente(app_module, tenant_id, 'noviaB')
    p = _cliente(app_module, tenant_id, 'planner')
    job = {'id': f'job-multi-{tenant_id}', 'tenant_id': tenant_id,
           'client_id': a['id'], 'secondary_client_id': b['id'],
           'planner_client_id': p['id']}
    by_id = {c['id']: c for c in (a, b, p)}

    clientes = app_module._job_clients(job, by_id)
    assert len(clientes) == 3
    # Etiquetas del modelo canonico (ETIQUETA_ROL). Antes eran las del
    # modelo de 3 campos fijos ('Cliente adicional'); ahora el rol se llama
    # 'pareja' y se muestra como 'Pareja'.
    assert [c['rol'] for c in clientes] == ['Principal', 'Pareja', 'Wedding planner']
    assert [c['role'] for c in clientes] == ['principal', 'pareja', 'wedding_planner']
    assert [c['id'] for c in clientes] == [a['id'], b['id'], p['id']]

    display = app_module._job_clients_display(job, by_id)
    for c in (a, b, p):
        assert c['first_name'] in display


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_mismo_cliente_en_dos_roles_no_se_duplica(tenant_id):
    """Un import puede dejar el mismo client_id en dos roles. No puede
    aparecer dos veces en la interfaz ni dos veces en el To: de un correo."""
    import app as app_module

    a = _cliente(app_module, tenant_id, 'repetido')
    job = {'id': f'job-dup-{tenant_id}', 'tenant_id': tenant_id,
           'client_id': a['id'], 'secondary_client_id': a['id']}

    clientes = app_module._job_clients(job, {a['id']: a})
    assert len(clientes) == 1, 'el mismo cliente aparecio dos veces'
    assert clientes[0]['rol'] == 'Principal'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_job_sin_clientes_no_revienta(tenant_id):
    import app as app_module
    job = {'id': 'job-vacio', 'tenant_id': tenant_id}
    assert app_module._job_clients(job, {}) == []
    assert app_module._job_clients_display(job, {}) == 'Sin cliente'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_cliente_faltante_se_omite_sin_romper(tenant_id):
    """Si secondary_client_id apunta a un cliente borrado, se omite en vez
    de reventar la lista entera."""
    import app as app_module
    a = _cliente(app_module, tenant_id, 'existe')
    job = {'id': 'job-huerfano', 'tenant_id': tenant_id,
           'client_id': a['id'], 'secondary_client_id': 'client-que-no-existe'}
    clientes = app_module._job_clients(job, {a['id']: a})
    assert len(clientes) == 1


# ============================================================
# PUNTO 3 -- pagos: una sola formula
# ============================================================

def _cuota(monto, status='Pendiente', due=None, pagado=0):
    return {'id': f'pay-{monto}-{status}', 'amount': monto,
            'original_amount': monto if status != 'Pagado' else monto,
            'paid_amount': pagado, 'status': status, 'due_date': due}


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_resumen_de_pagos_total_pagado_pendiente(tenant_id):
    import app as app_module

    job = {'id': 'job-pagos', 'tenant_id': tenant_id, 'price_total': 15000}
    pagos = [
        _cuota(5000, 'Pagado', '2026-01-01', pagado=5000),
        _cuota(5000, 'Pendiente', '2026-06-01'),
        _cuota(5000, 'Pendiente', '2026-12-01'),
    ]
    r = app_module._job_payment_summary(job, pagos)

    assert r['total'] == 15000
    assert r['pagado'] == 5000
    assert r['pendiente'] == 10000
    assert r['cuotas'] == 3
    assert r['cuotas_pagadas'] == 1
    assert r['proximo_pago_fecha'] == '2026-06-01'
    assert r['proximo_pago_monto'] == 5000
    assert r['esta_pagado'] is False


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_job_totalmente_pagado(tenant_id):
    import app as app_module
    job = {'id': 'job-pagado', 'tenant_id': tenant_id, 'price_total': 10000}
    pagos = [_cuota(10000, 'Pagado', '2026-01-01', pagado=10000)]
    r = app_module._job_payment_summary(job, pagos)
    assert r['pendiente'] == 0
    assert r['esta_pagado'] is True
    assert r['proximo_pago_fecha'] is None


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_descuadre_entre_cotizado_y_cuotas_se_expone_no_se_corrige(tenant_id):
    """Si el total cotizado no coincide con la suma de las cuotas puede ser
    un descuento legitimo. No se corrige solo: se reporta."""
    import app as app_module
    job = {'id': 'job-descuadre', 'tenant_id': tenant_id, 'price_total': 15000}
    pagos = [_cuota(5000, 'Pendiente'), _cuota(5000, 'Pendiente')]
    r = app_module._job_payment_summary(job, pagos)
    assert r['total'] == 15000, 'manda lo cotizado'
    assert r['descuadre_cotizado_vs_cuotas'] == 5000


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_job_sin_cotizacion_usa_la_suma_de_cuotas_como_total(tenant_id):
    import app as app_module
    job = {'id': 'job-sin-quote', 'tenant_id': tenant_id}
    r = app_module._job_payment_summary(job, [_cuota(3000, 'Pendiente')])
    assert r['total'] == 3000


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_cuotas_vencidas_se_cuentan(tenant_id):
    import app as app_module
    job = {'id': 'job-late', 'tenant_id': tenant_id, 'price_total': 5000}
    r = app_module._job_payment_summary(job, [_cuota(5000, 'Late', '2020-01-01')])
    assert r['vencidas'] == 1
    assert r['pendiente'] == 5000


def test_el_estado_del_job_y_el_resumen_de_pagos_no_se_contradicen():
    """El chip de estado y el resumen financiero deben salir de la misma
    nocion de saldo -- si no, la interfaz dice dos cosas distintas."""
    import app as app_module
    job = {'id': 'job-coherente', 'dias_restantes': -5,
           'workflow_progress': 100, 'price_total': 8000}
    pagos = [_cuota(8000, 'Pendiente', '2026-01-01')]

    _l, _t, key = app_module._job_estado_label(job, pagos)
    resumen = app_module._job_payment_summary(job, pagos)

    assert key == 'por_cobrar'
    assert resumen['pendiente'] > 0
    assert resumen['esta_pagado'] is False


# ============================================================
# PUNTO 4 -- locacion no se contamina con datos de contacto
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_convertir_lead_no_copia_el_venue_a_la_direccion_del_cliente(tenant_id):
    """Causa raiz del punto 4: el venue del evento se copiaba a
    client['address'] y despues volvia al reves, creando un bucle por el
    que emails y telefonos terminaban dentro de location."""
    import app as app_module

    lead = {'id': f'lead-venue-{tenant_id}', 'tenant_id': tenant_id,
            'nombre': 'Pareja Venue', 'email': 'pareja@example.invalid',
            'telefono': '5555-1234', 'locacion': 'Casa del Mundo, Atitlan'}
    app_module.store.upsert('leads', lead)

    cliente, _creado = app_module._ensure_client_for_lead(lead)
    assert cliente.get('address', '') != 'Casa del Mundo, Atitlan', \
        'el venue del evento no puede terminar como direccion de facturacion'
    assert cliente.get('email') == 'pareja@example.invalid'
    assert cliente.get('phone') == '5555-1234'


@pytest.mark.parametrize('valor,esperado', [
    ('Casa del Mundo, Atitlan', 'LIMPIO'),
    ('Antigua Guatemala', 'LIMPIO'),
    ('novios@example.com', 'CONTIENE_EMAIL'),
    ('Hotel X - contacto: 5555-1234', 'CONTIENE_TELEFONO'),
    ('https://maps.google.com/x', 'SOSPECHOSO_NO_LUGAR'),
    ('', 'VACIO'),
    (None, 'VACIO'),
])
def test_clasificador_de_locacion(valor, esperado):
    """El clasificador NO corrige: separa lo limpio de lo sospechoso para
    que un humano decida. Kevin: 'no borres informacion legacy ambigua'."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'tools'))
    from audit_location_contamination import clasificar

    categoria, _detalles = clasificar(valor)
    assert categoria == esperado, f'{valor!r} -> {categoria}, esperado {esperado}'


# ============================================================
# PUNTO 5 -- orden por relevancia
# ============================================================

def _j(nombre, dias, created='2026-01-01'):
    return {'id': nombre, 'nombre': nombre, 'dias_restantes': dias, 'created': created}


def test_orden_pone_lo_urgente_primero_y_lo_viejo_al_final():
    """Dos bugs del orden anterior (`sorted(key=dias_restantes or 999)`):
      1. los dias de un evento pasado son NEGATIVOS, asi que la boda MAS
         VIEJA quedaba primera;
      2. `or 999` mandaba la boda de HOY (dias=0, falsy) al FINAL."""
    import app as app_module

    jobs = [_j('vieja', -500), _j('reciente', -3), _j('hoy', 0),
            _j('en10', 10), _j('en90', 90), _j('sinfecha', None)]
    orden = [j['nombre'] for j in sorted(jobs, key=app_module._job_orden_relevancia)]

    assert orden == ['hoy', 'en10', 'en90', 'reciente', 'vieja', 'sinfecha'], orden
    assert orden[0] == 'hoy', 'la boda de HOY debe ir primera, no al final'
    assert orden.index('reciente') < orden.index('vieja'), \
        'entre las pasadas, la mas reciente primero'


def test_el_orden_es_estable_para_jobs_equivalentes():
    """Mismo dia y misma fecha de creacion: el desempate por id mantiene el
    orden estable entre servidor e interfaz."""
    import app as app_module
    a = {'id': 'a', 'dias_restantes': 5, 'created': '2026-01-01'}
    b = {'id': 'b', 'dias_restantes': 5, 'created': '2026-01-01'}
    assert [j['id'] for j in sorted([b, a], key=app_module._job_orden_relevancia)] == ['a', 'b']


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_la_vista_de_jobs_ordena_y_muestra_todos_los_clientes(auth_client, tenant_id):
    import app as app_module
    from conftest import login_as_tenant

    login_as_tenant(auth_client, tenant_id, email=EMAIL[tenant_id])
    a = _cliente(app_module, tenant_id, 'vistaA')
    b = _cliente(app_module, tenant_id, 'vistaB')
    app_module.store.upsert('jobs', {
        'id': f'job-vista-{tenant_id}', 'tenant_id': tenant_id,
        'nombre': 'Boda Vista', 'client_id': a['id'],
        'secondary_client_id': b['id'], 'boda_date': '2027-05-05',
        'status': 'Confirmado',
    })

    resp = auth_client.get('/jobs')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert a['first_name'] in html
    assert b['first_name'] in html, 'el segundo cliente no aparece en la lista'
    assert 'value="relevancia"' in html, 'falta el orden por defecto en el selector'
