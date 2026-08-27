"""Uso diario: la ficha del cliente tiene que mostrar SUS bodas.

Bug concreto que cubre este archivo (agosto 2026): `/clients` y
`/clients/<id>` decidian que jobs eran de un cliente mirando UNICAMENTE
`job['client_id']`, es decir el rol `principal`.

En una boda hay dos novios. El segundo entra al job con rol `pareja` (o,
en jobs viejos, por `secondary_client_id`). Nunca como `client_id`. Asi
que al abrir la ficha de la novia el CRM decia que no tenia ninguna boda
-- justo la pantalla que se usa cuando ella llama por telefono.

Lo mismo con el wedding planner, que ademas suele ser quien mas escribe.

Todo se prueba en las DOS marcas.
"""
import pytest

from conftest import login_as_tenant

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'
AMBAS = [ASTRAL, NORKEVIN]

# TRAMPA DE ESTE REPO, documentada aqui porque cuesta dos corridas
# descubrirla: 'tenant-norkevin' es PREFIJO de
# 'tenant-norkevin-photography'. Si los ids de prueba terminan con el
# tenant_id, la ficha de Norkevin contiene su propio id
# (...-tenant-norkevin-photography), que a su vez CONTIENE el id de Astral
# como substring. Cualquier `assert id_ajeno not in html` da entonces un
# falso positivo de fuga entre marcas -- y solo en una direccion, que es lo
# que mas confunde.
#
# Por eso los ids se construyen con un token corto por marca, elegido para
# que ninguno sea prefijo del otro.
TOKEN = {ASTRAL: 'astral', NORKEVIN: 'norkph'}


def _tk(tenant_id):
    """Token de marca sin colision de prefijos, para ids de prueba."""
    return TOKEN.get(tenant_id, tenant_id.replace('tenant-', ''))


def _cliente(app_module, tenant_id, sufijo, **extra):
    c = {'id': f'cli-uc-{sufijo}-{_tk(tenant_id)}', 'tenant_id': tenant_id,
         'first_name': f'Nombre{sufijo}', 'last_name': 'Apellido',
         'email': f'{sufijo}@example.invalid'}
    c.update(extra)
    app_module.store.upsert('clients', c)
    return c


def _job(app_module, tenant_id, sufijo, **extra):
    j = {'id': f'job-uc-{sufijo}-{_tk(tenant_id)}', 'tenant_id': tenant_id,
         'nombre': f'Boda {sufijo}', 'status': 'Confirmado'}
    j.update(extra)
    app_module.store.upsert('jobs', j)
    return j


# ============================================================
# La trampa de los prefijos, con guarda propia
# ============================================================

def test_los_tokens_de_marca_no_colisionan_por_prefijo():
    """Si un token fuera prefijo del otro, TODOS los `assert ajeno not in
    html` de este archivo darian falsos positivos de fuga entre marcas.

    Paso de verdad: con los ids terminados en `tenant_id`, la ficha de
    Norkevin contenia '...-tenant-norkevin-photography', que contiene
    '...-tenant-norkevin'. Dos corridas de Windows en rojo por eso, y el
    error decia "se vio un cliente de la otra marca" -- que es lo peor que
    puede decir un falso positivo en este proyecto.
    """
    a, n = _tk(ASTRAL), _tk(NORKEVIN)
    assert a != n
    assert not n.startswith(a), (
        f'el token de Norkevin ({n!r}) empieza con el de Astral ({a!r}): '
        'los asserts de aislamiento darian falsos positivos')
    assert not a.startswith(n), (
        f'el token de Astral ({a!r}) empieza con el de Norkevin ({n!r})')

    # Y los tenant_id REALES si colisionan: por eso existe el token.
    assert NORKEVIN.startswith(ASTRAL), (
        'si esto deja de ser cierto, revisar si el token sigue haciendo falta')


# ============================================================
# El indice cliente -> jobs, en cualquier rol
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_la_pareja_ve_su_propia_boda(auth_client, tenant_id):
    """El caso que motivo todo esto."""
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    novio = _cliente(app_module, tenant_id, 'novio')
    novia = _cliente(app_module, tenant_id, 'novia')
    job = _job(app_module, tenant_id, 'pareja')
    app_module._set_job_clients(job, [(novio['id'], app_module.ROL_PRINCIPAL),
                                      (novia['id'], app_module.ROL_PAREJA)],
                                tenant_id=tenant_id)

    indice = app_module._jobs_por_cliente()
    assert [j['id'] for j in indice.get(novio['id'], [])] == [job['id']]
    assert [j['id'] for j in indice.get(novia['id'], [])] == [job['id']], \
        'la novia no ve su propia boda'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_el_wedding_planner_tambien_aparece(auth_client, tenant_id):
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    planner = _cliente(app_module, tenant_id, 'wp')
    job = _job(app_module, tenant_id, 'conwp')
    app_module._set_job_clients(job, [(planner['id'], app_module.ROL_PLANNER)],
                                tenant_id=tenant_id)

    indice = app_module._jobs_por_cliente()
    assert [j['id'] for j in indice.get(planner['id'], [])] == [job['id']]


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_jobs_viejos_con_campos_legacy_siguen_contando(tenant_id):
    """Un job creado antes del modelo N-clientes no tiene filas en
    job_clients: se leen sus 3 campos viejos."""
    import app as app_module

    job = _job(app_module, tenant_id, 'legacy',
               client_id=f'viejo-a-{_tk(tenant_id)}',
               secondary_client_id=f'viejo-b-{_tk(tenant_id)}',
               planner_client_id=f'viejo-c-{_tk(tenant_id)}')

    indice = app_module._jobs_por_cliente()
    for cid in (f'viejo-a-{_tk(tenant_id)}', f'viejo-b-{_tk(tenant_id)}', f'viejo-c-{_tk(tenant_id)}'):
        assert job['id'] in [j['id'] for j in indice.get(cid, [])], \
            f'{cid} perdio su job legacy'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_la_misma_persona_en_dos_roles_no_cuenta_la_boda_dos_veces(auth_client, tenant_id):
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    c = _cliente(app_module, tenant_id, 'doblerol')
    job = _job(app_module, tenant_id, 'doblerol')
    # _set_job_clients ya deduplica; se fuerza el caso escribiendo tambien
    # los campos legacy, que es como llegan los imports de Studio Ninja.
    app_module._set_job_clients(job, [(c['id'], app_module.ROL_PRINCIPAL)],
                                tenant_id=tenant_id)
    job['secondary_client_id'] = c['id']
    app_module.store.upsert('jobs', job)

    indice = app_module._jobs_por_cliente()
    assert len(indice.get(c['id'], [])) == 1, 'la boda se conto dos veces'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_las_relaciones_nuevas_ganan_sobre_los_campos_legacy(auth_client, tenant_id):
    """Si el job ya tiene relaciones nuevas, los campos viejos se ignoran:
    si no, alguien que fue QUITADO del job seguiria viendolo en su ficha."""
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    quedo = _cliente(app_module, tenant_id, 'quedo')
    job = _job(app_module, tenant_id, 'quitado',
               secondary_client_id=f'ya-no-esta-{_tk(tenant_id)}')
    app_module._set_job_clients(job, [(quedo['id'], app_module.ROL_PRINCIPAL)],
                                tenant_id=tenant_id)

    indice = app_module._jobs_por_cliente()
    assert job['id'] in [j['id'] for j in indice.get(quedo['id'], [])]
    assert f'ya-no-esta-{_tk(tenant_id)}' not in indice, \
        'alguien que ya no esta en el job sigue viendolo'


# ============================================================
# Aislamiento entre marcas
# ============================================================

def test_el_indice_no_cruza_marcas(auth_client):
    """La misma direccion de correo en las dos marcas son dos personas
    distintas, y cada una ve solo la boda de su marca."""
    import app as app_module

    creados = {}
    for tenant_id in AMBAS:
        login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
        c = _cliente(app_module, tenant_id, 'cruce', email='mismo@example.invalid')
        job = _job(app_module, tenant_id, 'cruce')
        app_module._set_job_clients(job, [(c['id'], app_module.ROL_PRINCIPAL)],
                                    tenant_id=tenant_id)
        creados[tenant_id] = (c['id'], job['id'])

    assert creados[ASTRAL][0] != creados[NORKEVIN][0], \
        'el mismo email colapso en un solo cliente entre marcas'

    # El aislamiento del store vale DENTRO de una peticion. Fuera de una
    # (scripts de migracion, siembra de datos) el store devuelve el archivo
    # completo a proposito -- esta documentado en JsonStore._tenant_scope().
    # Por eso esto se comprueba pidiendo la pagina, que es como llega un
    # usuario de verdad, y no llamando al helper suelto: eso probaria algo
    # que no es el contrato.
    for tenant_id in AMBAS:
        login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
        propio_cid, propio_jid = creados[tenant_id]
        otro_cid, otro_jid = creados[NORKEVIN if tenant_id == ASTRAL else ASTRAL]

        html = auth_client.get(f'/clients/{propio_cid}').get_data(as_text=True)
        assert propio_jid in html, 'no ve su propia boda'
        assert otro_cid not in html, 'se vio un cliente de la otra marca'
        assert otro_jid not in html, 'se vio un job de la otra marca'

        # Y la ficha del cliente ajeno no se puede abrir desde esta marca.
        ajena = auth_client.get(f'/clients/{otro_cid}')
        assert ajena.status_code in (404, 302), \
            f'se pudo abrir la ficha de un cliente de la otra marca ({ajena.status_code})'


# ============================================================
# Las pantallas reales
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_la_ficha_de_la_pareja_lista_la_boda(auth_client, tenant_id):
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    novio = _cliente(app_module, tenant_id, 'fnovio')
    novia = _cliente(app_module, tenant_id, 'fnovia')
    job = _job(app_module, tenant_id, 'ficha', nombre='Boda Ficha Pareja')
    app_module._set_job_clients(job, [(novio['id'], app_module.ROL_PRINCIPAL),
                                      (novia['id'], app_module.ROL_PAREJA)],
                                tenant_id=tenant_id)

    resp = auth_client.get(f"/clients/{novia['id']}")
    assert resp.status_code == 200
    assert 'Boda Ficha Pareja' in resp.get_data(as_text=True), \
        'la ficha de la novia no muestra su boda'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_la_lista_de_clientes_carga_y_cuenta_todos_los_roles(auth_client, tenant_id):
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    novia = _cliente(app_module, tenant_id, 'lnovia')
    job = _job(app_module, tenant_id, 'lista')
    app_module._set_job_clients(job, [(novia['id'], app_module.ROL_PAREJA)],
                                tenant_id=tenant_id)

    resp = auth_client.get('/clients')
    assert resp.status_code == 200

    clientes = app_module._canonical_clients()
    # _canonical_clients deduplica por email; se busca la ficha por id.
    fila = next((c for c in clientes if c['id'] == novia['id']), None)
    assert fila is not None, 'la novia desaparecio de la lista canonica'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_cliente_sin_jobs_no_revienta_ninguna_pantalla(auth_client, tenant_id):
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    solo = _cliente(app_module, tenant_id, 'solo')
    assert app_module._jobs_por_cliente().get(solo['id'], []) == []
    assert auth_client.get('/clients').status_code == 200
    assert auth_client.get(f"/clients/{solo['id']}").status_code == 200


# ============================================================
# La ficha del cliente muestra lo que se pregunta por telefono
# ============================================================

def _boda_con_pareja(app_module, tenant_id, sufijo='fd'):
    """Boda con novio (principal), novia (pareja), planner y una cuota."""
    novio = _cliente(app_module, tenant_id, f'{sufijo}-novio')
    novia = _cliente(app_module, tenant_id, f'{sufijo}-novia')
    planner = _cliente(app_module, tenant_id, f'{sufijo}-wp')
    # El nombre lleva el tenant: si las dos marcas usaran el mismo nombre,
    # el test de aislamiento seria imposible de pasar (buscaria un texto que
    # tambien es el de su propia boda).
    job = _job(app_module, tenant_id, sufijo,
               nombre=f'Boda Ficha {sufijo} {_tk(tenant_id)}', boda_date='2026-12-20',
               price_total=20000, client_id=novio['id'])
    app_module._set_job_clients(
        job,
        [(novio['id'], app_module.ROL_PRINCIPAL),
         (novia['id'], app_module.ROL_PAREJA),
         (planner['id'], app_module.ROL_PLANNER)],
        tenant_id=tenant_id)
    # OJO con la semantica: en este CRM el `amount` de una cuota PENDIENTE
    # ya es su saldo actual (se ajusta con cada abono), por eso
    # _job_saldo_pendiente no le vuelve a restar lo pagado. Para modelar
    # "20000 en total, 5000 ya abonados, 15000 pendientes" van dos filas:
    # la cobrada y la que queda.
    app_module.store.upsert('payments', {
        'id': f'pay-{sufijo}-a-{_tk(tenant_id)}', 'tenant_id': tenant_id,
        'job_id': job['id'], 'client_id': novio['id'],
        'amount': 5000, 'paid_amount': 5000, 'status': 'Pagado',
        'due_date': '2026-09-01',
    })
    app_module.store.upsert('payments', {
        'id': f'pay-{sufijo}-b-{_tk(tenant_id)}', 'tenant_id': tenant_id,
        'job_id': job['id'], 'client_id': novio['id'],
        'amount': 15000, 'paid_amount': 0, 'status': 'Pendiente',
        'due_date': '2026-11-01',
    })
    return job, novio, novia, planner


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_la_ficha_muestra_el_rol_del_cliente_en_la_boda(auth_client, tenant_id):
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
    job, _novio, novia, planner = _boda_con_pareja(app_module, tenant_id, 'rol')

    html = auth_client.get(f"/clients/{novia['id']}").get_data(as_text=True)
    assert 'Pareja' in html, 'no dice que rol tiene en la boda'

    html_wp = auth_client.get(f"/clients/{planner['id']}").get_data(as_text=True)
    assert 'Wedding planner' in html_wp
    assert 'no recibe documentos' in html_wp, \
        'el planner tiene que verse marcado como que NO recibe documentos'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_la_ficha_muestra_el_saldo_de_la_boda(auth_client, tenant_id):
    """La misma plata que la lista de jobs y la ficha del job: 20000 - 5000."""
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
    job, _novio, novia, _wp = _boda_con_pareja(app_module, tenant_id, 'saldo')

    html = auth_client.get(f"/clients/{novia['id']}").get_data(as_text=True)
    assert '15,000' in html, 'no aparece el pendiente calculado por el helper canonico'

    # Y no puede contradecir a la fuente unica.
    pagos = [p for p in app_module.list_payments() if p.get('job_id') == job['id']]
    resumen = app_module._job_payment_summary(job, pagos)
    assert resumen['pendiente'] == 15000


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_la_ficha_muestra_con_quien_comparte_la_boda(auth_client, tenant_id):
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
    _job, novio, novia, planner = _boda_con_pareja(app_module, tenant_id, 'acomp')

    html = auth_client.get(f"/clients/{novia['id']}").get_data(as_text=True)
    assert novio['first_name'] in html, 'no muestra a la otra mitad de la pareja'
    assert f"/clients/{novio['id']}" in html, 'no se puede saltar a su ficha'
    assert planner['first_name'] in html
    # Y no se lista a si misma como acompanante.
    assert html.count(f"/clients/{novia['id']}") <= 1


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_el_estado_sale_del_helper_canonico_no_del_workflow(auth_client, tenant_id):
    """Una boda que ya paso pero sigue con saldo es "por cobrar", no
    "completada" -- aunque el workflow este al 100%."""
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')

    c = _cliente(app_module, tenant_id, 'estado')
    job = _job(app_module, tenant_id, 'estado', nombre='Boda Ya Ocurrida',
               boda_date='2026-01-10', dias_restantes=-30,
               workflow_progress=100, price_total=9000)
    app_module._set_job_clients(job, [(c['id'], app_module.ROL_PRINCIPAL)],
                                tenant_id=tenant_id)
    app_module.store.upsert('payments', {
        'id': f'pay-estado-{_tk(tenant_id)}', 'tenant_id': tenant_id,
        'job_id': job['id'], 'amount': 9000, 'paid_amount': 0,
        'status': 'Pendiente', 'due_date': '2026-02-01',
    })

    pagos = [p for p in app_module.list_payments() if p.get('job_id') == job['id']]
    _label, _tono, key = app_module._job_estado_label(job, pagos)
    assert key == 'por_cobrar'

    html = auth_client.get(f"/clients/{c['id']}").get_data(as_text=True)
    assert '100%' not in html.split('Boda Ya Ocurrida')[-1][:600], \
        'la ficha volvio a mostrar el avance del workflow como si fuera el estado'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_la_ficha_de_un_cliente_sin_bodas_lo_dice_claro(auth_client, tenant_id):
    import app as app_module
    login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
    solo = _cliente(app_module, tenant_id, 'sinbodas')

    resp = auth_client.get(f"/clients/{solo['id']}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'todavia no esta en ninguna boda' in html


def test_la_ficha_no_filtra_bodas_de_la_otra_marca(auth_client):
    import app as app_module

    creados = {}
    for tenant_id in AMBAS:
        login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
        job, _n, novia, _wp = _boda_con_pareja(app_module, tenant_id, 'aisl')
        creados[tenant_id] = (job, novia)

    for tenant_id in AMBAS:
        otro = NORKEVIN if tenant_id == ASTRAL else ASTRAL
        login_as_tenant(auth_client, tenant_id, email=f'{tenant_id}@example.invalid')
        _job_propio, novia_propia = creados[tenant_id]
        job_ajeno, novia_ajena = creados[otro]

        html = auth_client.get(f"/clients/{novia_propia['id']}").get_data(as_text=True)
        assert job_ajeno['nombre'] not in html, 'aparecio una boda de la otra marca'
        assert novia_ajena['id'] not in html, 'aparecio un cliente de la otra marca'
