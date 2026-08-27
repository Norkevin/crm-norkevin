"""Modelo canonico: N clientes por job + calendarios de pago con estado.

Cierra los dos limites estructurales que Kevin quiso resolver ANTES de
cargar clientes reales (agosto 2026):

  1. El tope de 3 clientes por job (client_id / secondary_client_id /
     planner_client_id). Un cuarto cliente no cabia y la unica salida
     hubiera sido seguir agregando client_4_id, client_5_id...
     Ahora la tabla `job_clients` guarda 0..N relaciones con rol.

  2. Calendarios de pago sin identidad ni estado, que permitian que la
     misma cotizacion generara dos juegos de cuotas (la sobrefacturacion
     del caso Camila/Daniel). Ahora `payment_schedules` tiene una
     identidad logica (tenant + job + cotizacion) y estados explicitos,
     con maximo UN activo por identidad.

Todo se prueba en las DOS marcas.
"""
import pytest

from src.storage import TenantMismatchError

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'
AMBAS = [ASTRAL, NORKEVIN]


def _cliente(app_module, tenant_id, n):
    c = {'id': f'cli-{tenant_id}-{n}', 'tenant_id': tenant_id,
         'first_name': f'Cliente{n}', 'last_name': 'Test',
         'email': f'cliente{n}@example.invalid'}
    app_module.store.upsert('clients', c)
    return c


def _job(app_module, tenant_id, sufijo='n'):
    j = {'id': f'job-{tenant_id}-{sufijo}', 'tenant_id': tenant_id,
         'nombre': 'Boda N Clientes', 'status': 'Confirmado'}
    app_module.store.upsert('jobs', j)
    return j


# ============================================================
# 1. N clientes -- el limite de 3 ya no existe
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
@pytest.mark.parametrize('cantidad', [1, 2, 3, 4, 6])
def test_job_soporta_n_clientes(auth_client, tenant_id, cantidad):
    """1, 2, 3, 4 y 6 clientes. Con el modelo viejo, 4 y 6 perdian gente."""
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, f'n{cantidad}')
    clientes = [_cliente(app_module, tenant_id, f'{cantidad}_{i}') for i in range(cantidad)]

    roles = [app_module.ROL_PRINCIPAL, app_module.ROL_PAREJA,
             app_module.ROL_PLANNER, app_module.ROL_CONTACTO,
             app_module.ROL_OTRO, app_module.ROL_CONTACTO]
    app_module._set_job_clients(
        job, [(c['id'], roles[i]) for i, c in enumerate(clientes)],
        tenant_id=tenant_id)

    relaciones = app_module._job_client_relations(job)
    assert len(relaciones) == cantidad, \
        f'se esperaban {cantidad} relaciones y hay {len(relaciones)}'
    assert [r['client_id'] for r in relaciones] == [c['id'] for c in clientes], \
        'el orden de los clientes no se conserva'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_misma_persona_en_dos_roles_no_se_duplica(auth_client, tenant_id):
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'dup')
    c = _cliente(app_module, tenant_id, 'dup')
    app_module._set_job_clients(
        job, [(c['id'], app_module.ROL_PRINCIPAL), (c['id'], app_module.ROL_PAREJA)],
        tenant_id=tenant_id)

    relaciones = app_module._job_client_relations(job)
    assert len(relaciones) == 1
    assert relaciones[0]['role'] == app_module.ROL_PRINCIPAL, 'gana el primer rol'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_cliente_eliminado_se_omite_sin_romper(auth_client, tenant_id):
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'borrado')
    c = _cliente(app_module, tenant_id, 'borrado')
    app_module._set_job_clients(job, [(c['id'], app_module.ROL_PRINCIPAL)],
                                tenant_id=tenant_id)
    app_module.store.delete('clients', c['id'])

    # La relacion sigue, pero la vista no revienta: omite al que no existe.
    assert app_module._job_clients(job) == []
    assert app_module._job_clients_display(job) == 'Sin cliente'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_set_job_clients_es_idempotente(auth_client, tenant_id):
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'idem')
    cs = [_cliente(app_module, tenant_id, f'idem{i}') for i in range(3)]
    rel = [(c['id'], app_module.ROL_CONTACTO) for c in cs]

    app_module._set_job_clients(job, rel, tenant_id=tenant_id)
    primera = app_module._job_client_relations(job)
    app_module._set_job_clients(job, rel, tenant_id=tenant_id)
    segunda = app_module._job_client_relations(job)

    assert primera == segunda
    todas = [r for r in app_module.store.list('job_clients')
             if r.get('job_id') == job['id']]
    assert len(todas) == 3, f'se duplicaron relaciones: {len(todas)}'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_quitar_un_cliente_elimina_solo_esa_relacion(auth_client, tenant_id):
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'quitar')
    a = _cliente(app_module, tenant_id, 'q_a')
    b = _cliente(app_module, tenant_id, 'q_b')
    app_module._set_job_clients(job, [(a['id'], app_module.ROL_PRINCIPAL),
                                      (b['id'], app_module.ROL_PAREJA)],
                                tenant_id=tenant_id)
    app_module._set_job_clients(job, [(a['id'], app_module.ROL_PRINCIPAL)],
                                tenant_id=tenant_id)

    relaciones = app_module._job_client_relations(job)
    assert [r['client_id'] for r in relaciones] == [a['id']]


# ============================================================
# 2. Adapter legacy
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_job_legacy_con_3_campos_se_lee_como_relaciones(tenant_id):
    """Un job creado ANTES de este cambio sigue mostrando a su gente."""
    import app as app_module

    job = {'id': f'job-legacy-{tenant_id}', 'tenant_id': tenant_id,
           'client_id': 'c1', 'secondary_client_id': 'c2',
           'planner_client_id': 'c3'}
    relaciones = app_module._job_client_relations(job)

    assert [r['client_id'] for r in relaciones] == ['c1', 'c2', 'c3']
    assert [r['role'] for r in relaciones] == [
        app_module.ROL_PRINCIPAL, app_module.ROL_PAREJA, app_module.ROL_PLANNER]


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_las_relaciones_nuevas_ganan_sobre_los_campos_legacy(auth_client, tenant_id):
    """Si el job ya tiene relaciones nuevas, los campos viejos se ignoran:
    si no, la misma persona aparaceria dos veces."""
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    c = _cliente(app_module, tenant_id, 'gana')
    job = {'id': f'job-gana-{tenant_id}', 'tenant_id': tenant_id,
           'client_id': 'viejo-1', 'secondary_client_id': 'viejo-2'}
    app_module.store.upsert('jobs', job)
    app_module._set_job_clients(job, [(c['id'], app_module.ROL_PRINCIPAL)],
                                tenant_id=tenant_id)

    relaciones = app_module._job_client_relations(job)
    assert [r['client_id'] for r in relaciones] == [c['id']]


# ============================================================
# 3. Cross-tenant: bloqueado
# ============================================================

@pytest.mark.parametrize('propio,ajeno', [(ASTRAL, NORKEVIN), (NORKEVIN, ASTRAL)])
def test_no_se_puede_asociar_un_cliente_de_otra_empresa(auth_client, propio, ajeno):
    """Aunque alguien arme el request a mano: un job de una marca no puede
    quedar asociado a un cliente de la otra."""
    import app as app_module
    from conftest import login_as_tenant

    ajeno_cli = {'id': f'cli-ajeno-{ajeno}', 'tenant_id': ajeno,
                 'first_name': 'Ajeno', 'last_name': 'Test'}
    app_module.store.upsert('clients', ajeno_cli)

    login_as_tenant(auth_client, propio, email=f'{propio}@example.invalid')
    job = _job(app_module, propio, 'cross')

    with pytest.raises(TenantMismatchError):
        app_module._set_job_clients(job, [(ajeno_cli['id'], app_module.ROL_PRINCIPAL)],
                                    tenant_id=propio)

    assert app_module._job_client_relations(job) == [], \
        'no debe haber quedado ninguna relacion cross-tenant'


# ============================================================
# 4. Miembros del job != destinatarios de un documento
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_el_wedding_planner_no_recibe_documentos_por_defecto(auth_client, tenant_id):
    """Kevin: 'no mandar accidentalmente un contrato a un wedding
    planner'. Es miembro del job, pero no destinatario por defecto."""
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'roles')
    novio = _cliente(app_module, tenant_id, 'novio')
    novia = _cliente(app_module, tenant_id, 'novia')
    planner = _cliente(app_module, tenant_id, 'planner')
    app_module._set_job_clients(job, [
        (novio['id'], app_module.ROL_PRINCIPAL),
        (novia['id'], app_module.ROL_PAREJA),
        (planner['id'], app_module.ROL_PLANNER),
    ], tenant_id=tenant_id)

    miembros = app_module._job_clients(job)
    destinatarios = app_module._job_recipient_clients(job)

    assert len(miembros) == 3, 'el planner SI es miembro del job'
    ids_dest = [c['id'] for c in destinatarios]
    assert planner['id'] not in ids_dest, 'el planner no debe recibir documentos'
    assert set(ids_dest) == {novio['id'], novia['id']}


# ============================================================
# 5. Payment schedules: identidad, estados, idempotencia
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_generar_dos_veces_el_mismo_schedule_no_duplica(auth_client, tenant_id):
    """Aceptar dos veces la misma cotizacion no puede producir dos juegos
    de cuotas -- es exactamente la sobrefacturacion del caso Camila."""
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'sched')
    job['price_total'] = 9000
    app_module.store.upsert('jobs', job)
    cli = _cliente(app_module, tenant_id, 'sched')
    quote = {'id': f'quote-{tenant_id}-sched', 'tenant_id': tenant_id,
             'precio_total': 9000, 'plan_pago': 3, 'paquete_nombre': 'P'}
    app_module.store.upsert('quotes', quote)

    # La cotizacion se limpia al final: el store es un singleton de sesion
    # y tests/test_public_client_pages.py toma "la primera cotizacion que
    # exista" para probar la vista publica. Una cotizacion de prueba
    # incompleta sembrada aca le hacia devolver 404 -- contaminacion mia
    # sobre un test ajeno que estaba bien.
    try:
        ids1, creado1 = app_module._ensure_payments_for_quote(
            quote, cli['id'], job['id'], tenant_id)
        ids2, creado2 = app_module._ensure_payments_for_quote(
            quote, cli['id'], job['id'], tenant_id)

        assert creado1 is True
        assert creado2 is False, 'la segunda llamada NO debe crear cuotas nuevas'

        cuotas = [p for p in app_module.store.list('payments')
                  if p.get('job_id') == job['id']]
        assert len(cuotas) == 3, f'se generaron {len(cuotas)} cuotas en vez de 3'

        activos = [s for s in app_module._job_schedules(job['id'])
                   if s['status'] == app_module.SCHEDULE_ACTIVE]
        assert len(activos) == 1, 'debe haber exactamente UN schedule activo'
        assert activos[0]['suma_cuotas'] == 9000
        assert activos[0]['avisos'] == [], f"schedule del CRM con avisos: {activos[0]['avisos']}"
    finally:
        app_module.store.delete('quotes', quote['id'])


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_supersede_conserva_el_viejo_y_activa_el_nuevo(auth_client, tenant_id):
    """Cambiar de cotizacion NO borra el calendario anterior ni reasigna
    dinero: el viejo queda 'superseded' con puntero al nuevo."""
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'super')
    viejo, _ = app_module._crear_schedule(
        tenant_id, job['id'], 'quote-vieja', total_plan=5000, cuotas=2,
        suma_cuotas=5000, payment_ids=['pay-a', 'pay-b'])
    nuevo, _ = app_module._crear_schedule(
        tenant_id, job['id'], 'quote-nueva', total_plan=7000, cuotas=2,
        suma_cuotas=7000, payment_ids=['pay-c', 'pay-d'])

    app_module.supersede_schedule(viejo['id'], nuevo['id'], motivo='cambio de paquete')

    viejo_final = app_module.store.get('payment_schedules', viejo['id'])
    nuevo_final = app_module.store.get('payment_schedules', nuevo['id'])

    assert viejo_final is not None, 'el schedule viejo NO puede borrarse'
    assert viejo_final['status'] == app_module.SCHEDULE_SUPERSEDED
    assert viejo_final['superseded_by'] == nuevo['id']
    assert viejo_final['payment_ids'] == ['pay-a', 'pay-b'], \
        'los pagos del schedule viejo conservan su historia'
    assert nuevo_final['status'] == app_module.SCHEDULE_ACTIVE


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_un_solo_schedule_activo_por_identidad_logica(auth_client, tenant_id):
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'unico')
    a, creado_a = app_module._crear_schedule(
        tenant_id, job['id'], 'quote-x', total_plan=1000, cuotas=1, suma_cuotas=1000)
    b, creado_b = app_module._crear_schedule(
        tenant_id, job['id'], 'quote-x', total_plan=1000, cuotas=1, suma_cuotas=1000)

    assert creado_a is True
    assert creado_b is False, 'no puede crearse un segundo activo para la misma identidad'
    assert a['id'] == b['id']


def test_discrepancia_de_plan_se_reporta_no_se_corrige():
    import app as app_module
    avisos = app_module._validar_schedule(
        total_plan=10000, cuotas=3, suma_cuotas=9000, price_total=12000)
    assert len(avisos) == 2, avisos
    assert any('9,000' in a for a in avisos)
    assert any('12,000' in a for a in avisos)


def test_plan_correcto_no_genera_avisos():
    import app as app_module
    assert app_module._validar_schedule(
        total_plan=9000, cuotas=3, suma_cuotas=9000, price_total=9000) == []


def test_todos_los_estados_de_schedule_estan_declarados():
    import app as app_module
    for estado in ('active', 'superseded', 'completed', 'cancelled', 'legacy_quarantined'):
        assert estado in app_module.ESTADOS_SCHEDULE


# ============================================================
# 6. Edicion real desde el CRM (rutas link-client / unlink-client)
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_editar_job_hasta_6_clientes_por_la_ruta_real(auth_client, tenant_id):
    """4, 5 y 6 clientes agregados por la ruta que usa el CRM, no por
    llamada directa al helper. Con el modelo viejo, del 4to en adelante no
    habia donde guardarlos."""
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'ruta6')
    clientes = [_cliente(app_module, tenant_id, f'r6_{i}') for i in range(6)]
    roles = ['principal', 'pareja', 'wedding_planner', 'contacto', 'otro', 'contacto']

    for c, rol in zip(clientes, roles):
        resp = auth_client.post(f'/api/jobs/{job["id"]}/link-client',
                                json={'client_id': c['id'], 'role': rol})
        assert resp.status_code == 200, resp.get_data(as_text=True)[:200]
        assert resp.get_json()['ok'] is True

    relaciones = app_module._job_client_relations(job)
    assert len(relaciones) == 6, f'quedaron {len(relaciones)} de 6 clientes'
    assert len(app_module._job_clients(job)) == 6


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_alias_de_rol_legacy_siguen_funcionando(auth_client, tenant_id):
    """El frontend existente manda 'secondary'/'planner'."""
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'alias')
    a = _cliente(app_module, tenant_id, 'alias_a')
    b = _cliente(app_module, tenant_id, 'alias_b')

    auth_client.post(f'/api/jobs/{job["id"]}/link-client',
                     json={'client_id': a['id'], 'role': 'secondary'})
    auth_client.post(f'/api/jobs/{job["id"]}/link-client',
                     json={'client_id': b['id'], 'role': 'planner'})

    roles = {r['client_id']: r['role'] for r in app_module._job_client_relations(job)}
    assert roles[a['id']] == app_module.ROL_PAREJA
    assert roles[b['id']] == app_module.ROL_PLANNER


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_cambiar_el_rol_de_un_cliente_ya_asociado(auth_client, tenant_id):
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'cambiorol')
    c = _cliente(app_module, tenant_id, 'cambiorol')
    auth_client.post(f'/api/jobs/{job["id"]}/link-client',
                     json={'client_id': c['id'], 'role': 'contacto'})
    auth_client.post(f'/api/jobs/{job["id"]}/link-client',
                     json={'client_id': c['id'], 'role': 'wedding_planner'})

    relaciones = app_module._job_client_relations(job)
    assert len(relaciones) == 1, 'cambiar el rol no puede duplicar la relacion'
    assert relaciones[0]['role'] == app_module.ROL_PLANNER


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_quitar_relacion_no_borra_el_cliente(auth_client, tenant_id):
    """Quitar a alguien de un job no puede borrar a la persona: sigue
    existiendo con toda su historia."""
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'quitar_ruta')
    a = _cliente(app_module, tenant_id, 'qr_a')
    b = _cliente(app_module, tenant_id, 'qr_b')
    for c, rol in ((a, 'principal'), (b, 'pareja')):
        auth_client.post(f'/api/jobs/{job["id"]}/link-client',
                         json={'client_id': c['id'], 'role': rol})

    resp = auth_client.post(f'/api/jobs/{job["id"]}/unlink-client',
                            json={'client_id': b['id']})
    assert resp.status_code == 200

    relaciones = app_module._job_client_relations(job)
    assert [r['client_id'] for r in relaciones] == [a['id']]
    assert app_module.store.get('clients', b['id']) is not None, \
        'el cliente NO puede haberse borrado al quitarlo del job'


@pytest.mark.parametrize('propio,ajeno', [(ASTRAL, NORKEVIN), (NORKEVIN, ASTRAL)])
def test_la_ruta_bloquea_cliente_de_otra_empresa(auth_client, propio, ajeno):
    import app as app_module
    from conftest import login_as_tenant

    ajeno_cli = {'id': f'cli-ruta-ajeno-{ajeno}', 'tenant_id': ajeno,
                 'first_name': 'Ajeno', 'last_name': 'Ruta'}
    app_module.store.upsert('clients', ajeno_cli)

    login_as_tenant(auth_client, propio, email=f'{propio}@example.invalid')
    job = _job(app_module, propio, 'ruta_cross')

    resp = auth_client.post(f'/api/jobs/{job["id"]}/link-client',
                            json={'client_id': ajeno_cli['id'], 'role': 'pareja'})
    assert resp.status_code in (403, 404), \
        f'se permitio asociar un cliente de {ajeno} a un job de {propio}'
    assert app_module._job_client_relations(job) == []


# ============================================================
# 7. Una sola fuente de verdad para pagos
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_listado_y_detalle_muestran_los_mismos_totales(auth_client, tenant_id):
    """El detalle calculaba su propia formula. Ahora los dos salen de
    _job_payment_summary()."""
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'totales')
    job['price_total'] = 12000
    job['boda_date'] = '2027-03-03'
    app_module.store.upsert('jobs', job)
    cli = _cliente(app_module, tenant_id, 'totales')
    app_module._set_job_clients(job, [(cli['id'], app_module.ROL_PRINCIPAL)],
                                tenant_id=tenant_id)
    for i, (monto, estado) in enumerate([(4000, 'Pagado'), (8000, 'Pendiente')]):
        app_module.store.upsert('payments', {
            'id': f'pay-tot-{tenant_id}-{i}', 'tenant_id': tenant_id,
            'job_id': job['id'], 'amount': monto, 'original_amount': monto,
            'paid_amount': monto if estado == 'Pagado' else 0,
            'status': estado, 'due_date': f'2026-0{i+1}-01',
        })

    pagos = [p for p in app_module.store.list('payments') if p.get('job_id') == job['id']]
    resumen = app_module._job_payment_summary(job, pagos)

    assert resumen['total'] == 12000
    assert resumen['pagado'] == 4000
    assert resumen['pendiente'] == 8000

    # Las dos vistas responden y usan ese mismo resumen.
    assert auth_client.get('/jobs').status_code == 200
    assert auth_client.get(f'/jobs/{job["id"]}').status_code == 200


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_un_schedule_superseded_no_cuenta_como_activo(auth_client, tenant_id):
    """El calendario reemplazado se conserva, pero no puede sumarse como
    vigente ni aparecer como el schedule activo del job."""
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'super_activo')
    viejo, _ = app_module._crear_schedule(
        tenant_id, job['id'], 'quote-v', total_plan=5000, cuotas=1, suma_cuotas=5000)
    nuevo, _ = app_module._crear_schedule(
        tenant_id, job['id'], 'quote-n', total_plan=8000, cuotas=1, suma_cuotas=8000)
    app_module.supersede_schedule(viejo['id'], nuevo['id'])

    todos = app_module._job_schedules(job['id'])
    activos = [s for s in todos if s['status'] == app_module.SCHEDULE_ACTIVE]

    assert len(todos) == 2, 'el schedule viejo debe conservarse'
    assert len(activos) == 1
    assert activos[0]['id'] == nuevo['id']


# ============================================================
# 8. Interfaz de job_detail con el modelo N-clientes
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_job_detail_muestra_todos_los_clientes_y_sus_roles(auth_client, tenant_id):
    """La tarjeta de clientes recorre job_clients (0..N), no los 3 campos
    fijos. Con 5 clientes deben verse los 5, cada uno con su rol."""
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'ui5')
    job['boda_date'] = '2027-09-09'
    app_module.store.upsert('jobs', job)
    clientes = [_cliente(app_module, tenant_id, f'ui5_{i}') for i in range(5)]
    roles = [app_module.ROL_PRINCIPAL, app_module.ROL_PAREJA,
             app_module.ROL_PLANNER, app_module.ROL_CONTACTO, app_module.ROL_OTRO]
    app_module._set_job_clients(
        job, [(c['id'], roles[i]) for i, c in enumerate(clientes)], tenant_id=tenant_id)

    resp = auth_client.get(f'/jobs/{job["id"]}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    for c in clientes:
        assert c['first_name'] in html, f"{c['first_name']} no aparece en job_detail"
    assert 'Clientes (5)' in html
    assert 'cambiarRolCliente' in html, 'falta el selector de rol'
    assert 'quitarClienteDelJob' in html, 'falta el boton de quitar'
    assert 'Agregar cliente' in html
    assert 'no recibe documentos' in html, \
        'debe advertirse que planner/contacto/otro no reciben documentos'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_job_detail_muestra_el_resumen_financiero_unico(auth_client, tenant_id):
    """total / pagado / pendiente / proximo pago vienen de
    _job_payment_summary, la misma fuente que la lista y el dashboard."""
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'uipagos')
    job.update({'price_total': 12000, 'boda_date': '2027-04-04'})
    app_module.store.upsert('jobs', job)
    for i, (monto, estado) in enumerate([(4000, 'Pagado'), (8000, 'Pendiente')]):
        app_module.store.upsert('payments', {
            'id': f'pay-ui-{tenant_id}-{i}', 'tenant_id': tenant_id,
            'job_id': job['id'], 'amount': monto, 'original_amount': monto,
            'paid_amount': monto if estado == 'Pagado' else 0,
            'status': estado, 'due_date': f'2027-0{i+1}-01'})

    resp = auth_client.get(f'/jobs/{job["id"]}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'Resumen de pagos' in html
    assert '12,000.00' in html, 'falta el total'
    assert '4,000.00' in html, 'falta lo pagado'
    assert '8,000.00' in html, 'falta el pendiente'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_job_detail_muestra_descuadre_sin_corregirlo(auth_client, tenant_id):
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'uidesc')
    job.update({'price_total': 15000, 'boda_date': '2027-05-05'})
    app_module.store.upsert('jobs', job)
    app_module.store.upsert('payments', {
        'id': f'pay-desc-{tenant_id}', 'tenant_id': tenant_id,
        'job_id': job['id'], 'amount': 10000, 'original_amount': 10000,
        'status': 'Pendiente', 'due_date': '2027-01-01'})

    html = auth_client.get(f'/jobs/{job["id"]}').get_data(as_text=True)
    assert 'Descuadre' in html
    # Y el total sigue siendo el cotizado: no se "arreglo" solo.
    assert '15,000.00' in html


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_job_detail_no_suma_schedules_reemplazados(auth_client, tenant_id):
    """Un schedule superseded se conserva como historial pero no puede
    presentarse como el calendario vigente."""
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'uisched')
    job['boda_date'] = '2027-06-06'
    app_module.store.upsert('jobs', job)
    viejo, _ = app_module._crear_schedule(
        tenant_id, job['id'], 'q-vieja', total_plan=5000, cuotas=2, suma_cuotas=5000)
    nuevo, _ = app_module._crear_schedule(
        tenant_id, job['id'], 'q-nueva', total_plan=9000, cuotas=3, suma_cuotas=9000)
    app_module.supersede_schedule(viejo['id'], nuevo['id'])

    html = auth_client.get(f'/jobs/{job["id"]}').get_data(as_text=True)
    assert 'Calendario activo' in html
    assert '9,000.00' in html, 'el calendario activo debe ser el nuevo'
    assert 'superseded' in html, 'el historial debe seguir visible'


# ============================================================
# 9. Import Studio Ninja: ningun cliente se pierde
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
@pytest.mark.parametrize('cantidad', [1, 2, 3, 4, 6])
def test_import_studio_ninja_conserva_todos_los_clientes(auth_client, tenant_id, cantidad):
    """El import mapeaba solo principal/secundario/planner: del cuarto en
    adelante se perdian. Ahora escribe la relacion N completa."""
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, f'sn{cantidad}')
    clientes = [_cliente(app_module, tenant_id, f'sn{cantidad}_{i}') for i in range(cantidad)]

    # Lo mismo que hace el import: los 3 primeros a roles conocidos, el
    # resto como contacto adicional.
    roles_import = [app_module.ROL_PRINCIPAL, app_module.ROL_PAREJA, app_module.ROL_PLANNER]
    app_module._set_job_clients(job, [
        (c['id'], roles_import[i] if i < len(roles_import) else app_module.ROL_CONTACTO)
        for i, c in enumerate(clientes)
    ], tenant_id=tenant_id)

    ids = app_module.get_job_client_ids(job)
    assert len(ids) == cantidad, \
        f'el import perdio clientes: {len(ids)} de {cantidad}'
    assert ids == [c['id'] for c in clientes]


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_get_job_client_ids_ya_no_topa_en_tres(auth_client, tenant_id):
    """Regresion: get_job_client_ids() leia SOLO los 3 campos legacy, asi
    que un job con 5 clientes devolvia 3 aunque el modelo los tuviera."""
    import app as app_module
    from conftest import login_as_tenant
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    job = _job(app_module, tenant_id, 'tope3')
    clientes = [_cliente(app_module, tenant_id, f'tope_{i}') for i in range(5)]
    app_module._set_job_clients(
        job, [(c['id'], app_module.ROL_CONTACTO) for c in clientes], tenant_id=tenant_id)

    assert len(app_module.get_job_client_ids(job)) == 5
    assert len(app_module.get_job_clients(job)) == 5


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_ninguna_ruta_mete_el_venue_en_la_direccion_del_cliente(tenant_id):
    """Habia CUATRO puertas que copiaban el venue del evento a
    client['address']: _ensure_client_for_lead, api_lead_create y dos
    caminos del import de Studio Ninja. Por ahi entraban emails y
    telefonos al campo de ubicacion."""
    import ast
    import os
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app.py')
    src = open(ruta, encoding='utf-8').read()

    sospechosas = [ln.strip() for ln in src.splitlines()
                   if "'address'" in ln and ('locacion' in ln or 'location' in ln)
                   and not ln.strip().startswith('#')]
    assert not sospechosas, f'volvieron a aparecer puertas de contaminacion: {sospechosas}'
