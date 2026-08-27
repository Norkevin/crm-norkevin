"""Smoke tests post-cutover -- paquete de controlled cutover (agosto 2026).

Se corren INMEDIATAMENTE DESPUES del cutover, contra el CRM ya operativo,
para confirmar que las dos empresas funcionan de punta a punta y que
siguen aisladas entre si.

REGLA CENTRAL (Kevin, explicita): las dos empresas son first-class
tenants. Una NO se considera validada porque la otra funcione. El
recorrido completo se ejecuta DOS VECES, identico, una por marca:

    Astral Weddings      -> tenant-norkevin        / astralweddingsgt@gmail.com
    Norkevin Photography -> tenant-norkevin-photography / norkevinfoto@gmail.com

y despues se corren los tests NEGATIVOS de cruce entre ambas. El cutover
solo se considera exitoso si TODO pasa para las dos.

SEGURIDAD DE ESTOS TESTS:
  - Corren sobre el CRM_DATA_DIR aislado de conftest.py (tempdir), NUNCA
    contra data/ real -- igual que el resto de la suite. Si algun dia se
    quisiera correr contra produccion de verdad, habria que hacerlo
    deliberadamente y con datos marcados, ver NOTA DE LIMPIEZA abajo.
  - El proveedor de correo esta mockeado Y bloqueado por partida doble:
    el fixture `client` parcha src.mail_tracker.send_email, y
    `_block_real_email_providers` (autouse de sesion) hace explotar
    cualquier intento de alcanzar SMTP/Resend/Gmail real. Ningun test de
    aca puede mandar un correo de verdad, pase lo que pase.

NOTA DE LIMPIEZA (dato sintetico): todos los registros que crean estos
tests llevan el prefijo SMOKE_PREFIX y `es_dato_sintetico: True`. En el
tempdir de pytest se destruyen solos al terminar la sesion
(pytest_unconfigure borra el directorio). Si estos tests se adaptaran
alguna vez para correr contra un entorno real, NO existe hoy un mecanismo
de borrado seguro por registro (`/api/admin/reset-test-data` borra tablas
COMPLETAS, no registros individuales -- seria destructivo), asi que en
ese caso los datos deben CONSERVARSE marcados como TEST y limpiarse a
mano despues, nunca borrarse con el endpoint destructivo.
"""
import uuid

import pytest

from conftest import login_as_tenant  # noqa: E402  (mismo estilo que el resto de la suite)

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'

BRANDS = {
    ASTRAL: {
        'display_name': 'Astral Weddings',
        'login_email': 'astralweddingsgt@gmail.com',
        'otra_marca_needle': 'Norkevin',
    },
    NORKEVIN: {
        'display_name': 'Norkevin Photography',
        'login_email': 'norkevinfoto@gmail.com',
        'otra_marca_needle': 'Astral',
    },
}

SMOKE_PREFIX = 'SMOKE_TEST_'


def _smoke_id(kind):
    return f'{SMOKE_PREFIX}{kind}-{uuid.uuid4().hex[:8]}'


def _seed(app_module, tabla, tenant_id, **campos):
    """Crea un registro sintetico, siempre marcado como tal."""
    record = {
        'id': _smoke_id(tabla[:4]),
        'tenant_id': tenant_id,
        'es_dato_sintetico': True,
        'origen': 'post_cutover_smoke_test',
    }
    record.update(campos)
    app_module.store.upsert(tabla, record)
    return record


@pytest.fixture()
def brand_client(client, request):
    """Cliente HTTP logueado como la marca del parametro."""
    tenant_id = request.param
    login_as_tenant(client, tenant_id, email=BRANDS[tenant_id]['login_email'],
                    name=f'Smoke {BRANDS[tenant_id]["display_name"]}')
    return client, tenant_id


# ============================================================
# RECORRIDO COMPLETO -- se ejecuta identico para AMBAS marcas
# ============================================================

@pytest.mark.parametrize('brand_client', [ASTRAL, NORKEVIN], indirect=True)
def test_smoke_login_y_dashboard(brand_client):
    """Paso 1-2: el login quedo valido y el dashboard carga sin 500."""
    client, tenant_id = brand_client
    resp = client.get('/')
    assert resp.status_code in (200, 302), \
        f'dashboard devolvio {resp.status_code} para {tenant_id}'
    if resp.status_code == 200:
        body = resp.get_data(as_text=True)
        assert BRANDS[tenant_id]['otra_marca_needle'] not in body, \
            f'el dashboard de {tenant_id} menciona la OTRA marca'


@pytest.mark.parametrize('brand_client', [ASTRAL, NORKEVIN], indirect=True)
def test_smoke_recorrido_completo_lead_a_job(brand_client):
    """Pasos 3-9: lead -> cliente -> quote -> aceptar quote -> job ->
    workflow -> payment schedule. Un solo test para que el recorrido sea
    verificable como una unidad: si un paso intermedio rompe, el resto no
    tiene sentido evaluarlo por separado."""
    import app as app_module
    client, tenant_id = brand_client

    # 3. Lead sintetico
    lead = _seed(app_module, 'leads', tenant_id,
                 nombre=f'{SMOKE_PREFIX}Pareja Smoke',
                 email=f'smoke-{uuid.uuid4().hex[:6]}@example.invalid',
                 telefono='0000-0000',
                 fecha_tentativa='2027-06-12',
                 status='Nuevo')

    # 4. Cliente
    client_rec = _seed(app_module, 'clients', tenant_id,
                       first_name=f'{SMOKE_PREFIX}Pareja', last_name='Smoke',
                       email=lead['email'], phone=lead['telefono'])

    # 5. Quote
    quote = _seed(app_module, 'quotes', tenant_id,
                  lead_id=lead['id'], client_id=client_rec['id'],
                  paquete_nombre=f'{SMOKE_PREFIX}Paquete', precio_total=15000,
                  plan_pago=3, status='Enviada')

    # 6. Aceptar la quote -> convierte lead en job
    resp = client.post(f'/api/leads/{lead["id"]}/accept-quote', json={})
    assert resp.status_code == 200, \
        f'accept-quote fallo para {tenant_id}: {resp.status_code} {resp.get_data(as_text=True)[:300]}'
    body = resp.get_json()
    assert body['ok'] is True
    job_id = body['job_id']

    # 7. El job existe, es del tenant correcto y quedo ligado al lead
    job = next((j for j in app_module.store.list('jobs') if j.get('id') == job_id), None)
    assert job is not None, f'el job {job_id} no aparece en el store para {tenant_id}'
    assert job.get('tenant_id') == tenant_id, \
        f'el job quedo con tenant_id={job.get("tenant_id")}, esperado {tenant_id}'
    assert job.get('lead_id') == lead['id']

    # 8. Workflow de produccion creado, exactamente uno
    # workflow_instances es un dict en el store (save_dict/get_dict), no una
    # tabla-lista: se consulta por el engine, no por store.list().
    workflows = app_module.workflow_engine.list_instances(
        subject_id=job_id, subject_type='job')
    assert len(workflows) == 1, \
        f'se esperaba 1 workflow_instance para el job, hay {len(workflows)}'
    assert body.get('workflow_instance_id')

    # 9. Payment schedule: 3 cuotas, sumando el total de la cotizacion
    payments = [p for p in app_module.store.list('payments') if p.get('job_id') == job_id]
    assert len(payments) == quote['plan_pago'], \
        f'se esperaban {quote["plan_pago"]} cuotas, hay {len(payments)}'
    total_cuotas = sum(float(p.get('original_amount') or p.get('amount') or 0) for p in payments)
    assert abs(total_cuotas - float(quote['precio_total'])) < 0.05, \
        f'las cuotas suman {total_cuotas}, la cotizacion dice {quote["precio_total"]}'
    assert all(p.get('tenant_id') == tenant_id for p in payments), \
        'alguna cuota quedo con el tenant equivocado'

    # Repetir la conversion no debe duplicar nada (idempotencia post-cutover)
    resp2 = client.post(f'/api/leads/{lead["id"]}/accept-quote', json={})
    assert resp2.get_json()['job_id'] == job_id
    assert len([j for j in app_module.store.list('jobs')
                if j.get('lead_id') == lead['id']]) == 1


@pytest.mark.parametrize('brand_client', [ASTRAL, NORKEVIN], indirect=True)
def test_smoke_contrato_y_pdf_con_branding_correcto(brand_client):
    """Paso 10-12: generar contrato + PDF y confirmar que el branding es
    el de ESTA marca y que la otra marca no aparece por ningun lado."""
    import app as app_module
    from src.pdf_generator import resolve_pdf_brand, generate_contract_pdf, contract_terms
    client, tenant_id = brand_client
    expected = BRANDS[tenant_id]

    # La identidad de marca se resuelve por tenant_id canonico
    brand = resolve_pdf_brand(tenant_id)
    assert brand['display_name'] == expected['display_name'], \
        f'resolve_pdf_brand({tenant_id}) dio {brand["display_name"]!r}'
    assert expected['otra_marca_needle'] not in brand['display_name']

    # Los terminos del contrato usan esta marca y NO la otra
    job = {'price_total': 15000, 'plan_pago': 3, 'cuota_monto': 5000}
    terms_text = ' '.join(body for _t, body in contract_terms(job, brand=brand))
    assert expected['display_name'] in terms_text
    assert expected['otra_marca_needle'] not in terms_text, \
        f'los terminos del contrato de {tenant_id} mencionan la OTRA marca'

    # El PDF se genera de verdad
    client_rec = {'first_name': f'{SMOKE_PREFIX}Pareja', 'last_name': 'Smoke',
                  'phone': '', 'email': 'smoke@example.invalid', 'address': ''}
    pdf_bytes = generate_contract_pdf({'id': _smoke_id('contract')}, job, client_rec, brand=brand)
    assert pdf_bytes.startswith(b'%PDF')
    assert len(pdf_bytes) > 500

    # Y el contrato queda registrado con el tenant correcto
    contract = _seed(app_module, 'contracts', tenant_id,
                     job_id=_smoke_id('job'), client_id=_smoke_id('client'),
                     tipo='boda', status='Borrador', signed=False)
    assert contract['tenant_id'] == tenant_id


@pytest.mark.parametrize('brand_client', [ASTRAL, NORKEVIN], indirect=True)
def test_smoke_correo_preparado_pero_nunca_enviado(brand_client, monkeypatch):
    """Paso 13: preparar un correo con el proveedor MOCK/BLOQUEADO.

    Post-cutover STAGE 1 (ver CONTROLLED_CUTOVER_PLAN.md seccion 7) el CRM
    corre con OUTBOUND_EMAIL_ENABLED=false: el correo se ARMA y se
    registra, pero NUNCA sale. Este test confirma las dos mitades: que el
    mensaje se construye con el remitente/marca correctos, y que el
    numero de llamadas al proveedor real es exactamente CERO."""
    from src import email_delivery
    client, tenant_id = brand_client
    expected = BRANDS[tenant_id]

    provider_calls = []

    def _contar_llamada(*args, **kwargs):
        provider_calls.append((args, kwargs))
        raise AssertionError('Se intento alcanzar un proveedor de correo REAL')

    for attr in ('_send_smtp', '_send_resend', '_send_gmail'):
        monkeypatch.setattr(email_delivery, attr, _contar_llamada)

    # Kill switch en el estado de STAGE 1
    monkeypatch.setenv('DISABLE_OUTBOUND_EMAIL', '1')
    monkeypatch.delenv('OUTBOUND_EMAIL_ENABLED', raising=False)

    assert email_delivery.outbound_email_enabled() is False, \
        'STAGE 1 exige que el correo saliente este apagado'

    result = email_delivery.send_email(
        'destinatario-smoke@example.invalid',
        f'Smoke {expected["display_name"]}',
        'Cuerpo de prueba.',
        metadata={'tenant_id': tenant_id},
    )

    assert result.ok is False
    assert result.status == 'blocked'
    assert len(provider_calls) == 0, \
        f'provider_calls={len(provider_calls)}, debe ser 0 -- ningun correo real puede salir'


# ============================================================
# CROSS-TENANT NEGATIVE TESTS -- todos deben BLOQUEAR
# ============================================================

def _crear_par_de_marcas(app_module):
    """Un juego completo de datos en CADA marca, con el mismo nombre y
    email a proposito: el caso peligroso es que el sistema los confunda
    por nombre/email en vez de por tenant_id + id."""
    datos = {}
    email_compartido = f'mismo-cliente-{uuid.uuid4().hex[:6]}@example.invalid'
    for tenant_id in (ASTRAL, NORKEVIN):
        cli = _seed(app_module, 'clients', tenant_id,
                    first_name=f'{SMOKE_PREFIX}Homonimo', last_name='Cruzado',
                    email=email_compartido)
        job = _seed(app_module, 'jobs', tenant_id,
                    nombre=f'{SMOKE_PREFIX}Boda Homonima', client_id=cli['id'],
                    status='Confirmado')
        pago = _seed(app_module, 'payments', tenant_id,
                     job_id=job['id'], client_id=cli['id'],
                     amount=5000, status='Pendiente')
        contrato = _seed(app_module, 'contracts', tenant_id,
                         job_id=job['id'], client_id=cli['id'],
                         tipo='boda', status='Borrador')
        datos[tenant_id] = {'client': cli, 'job': job, 'payment': pago, 'contract': contrato}
    return datos


@pytest.mark.parametrize('sesion,ajeno', [
    (ASTRAL, NORKEVIN),
    (NORKEVIN, ASTRAL),
])
def test_smoke_cross_tenant_recursos_ajenos_bloqueados(client, sesion, ajeno):
    """Con sesion en una marca, NINGUN recurso de la otra debe ser
    visible ni alcanzable: ni client_id, ni job_id, ni payment, ni
    contract. Se prueba en las DOS direcciones."""
    import app as app_module

    datos = _crear_par_de_marcas(app_module)
    login_as_tenant(client, sesion, email=BRANDS[sesion]['login_email'])

    recursos_ajenos = datos[ajeno]
    recursos_propios = datos[sesion]

    # 1. Los listados con scope de tenant solo devuelven lo propio.
    #
    # OJO (corregido tras la primera corrida real en Windows): esto DEBE
    # hacerse dentro de un request context con la sesion puesta. El
    # aislamiento de JsonStore es deliberadamente condicional -- ver
    # src/storage.py::_tenant_scope(): "Fuera de una peticion (scripts de
    # migracion, tests que siembran datos) se mantiene el comportamiento
    # sin aislamiento: ese codigo no es alcanzable desde la web y necesita
    # ver el archivo completo". Llamar a store.list() suelto, sin request
    # context, devuelve TODO por diseno y no prueba nada sobre aislamiento
    # (la primera version de este test lo hacia y reportaba una "fuga"
    # que no existia). El mismo patron con test_request_context() lo usa
    # tests/test_tenant_isolation.py, que pasa 41/41.
    with app_module.app.test_request_context('/'):
        from flask import session as _sess
        _sess['tenant_id'] = sesion
        for tabla, clave in (('clients', 'client'), ('jobs', 'job'),
                             ('payments', 'payment'), ('contracts', 'contract')):
            visibles = {r.get('id') for r in app_module.store.list(tabla)}
            assert recursos_propios[clave]['id'] in visibles, \
                f'{tabla}: no se ve el recurso PROPIO de {sesion}'
            assert recursos_ajenos[clave]['id'] not in visibles, \
                (f'FUGA CROSS-TENANT: con sesion en {sesion} se ve el {clave} '
                 f'de {ajeno} ({recursos_ajenos[clave]["id"]})')

    # 2. Acceso directo por id ajeno via HTTP -> nunca 200 con el dato
    for ruta in (f'/jobs/{recursos_ajenos["job"]["id"]}',
                 f'/clients/{recursos_ajenos["client"]["id"]}'):
        resp = client.get(ruta)
        assert resp.status_code != 200, \
            (f'FUGA CROSS-TENANT: {ruta} devolvio 200 con sesion en {sesion} '
             f'(el recurso es de {ajeno})')


@pytest.mark.parametrize('sesion,ajeno', [
    (ASTRAL, NORKEVIN),
    (NORKEVIN, ASTRAL),
])
def test_smoke_cross_tenant_crear_job_con_cliente_ajeno_bloqueado(client, sesion, ajeno):
    """El caso mas peligroso: crear un job en MI marca usando el
    client_id de la OTRA. Debe rechazarse -- si pasara, el job quedaria
    facturando a un cliente que no es de esta empresa."""
    import app as app_module

    datos = _crear_par_de_marcas(app_module)
    login_as_tenant(client, sesion, email=BRANDS[sesion]['login_email'])

    resp = client.post('/api/jobs/new', json={
        'nombre': f'{SMOKE_PREFIX}Job con cliente ajeno',
        'client_id': datos[ajeno]['client']['id'],
    })
    assert resp.status_code != 200 or resp.get_json().get('ok') is False, \
        (f'FUGA CROSS-TENANT: se pudo crear un job en {sesion} con el client_id '
         f'de {ajeno} -- {resp.get_data(as_text=True)[:300]}')


@pytest.mark.parametrize('sesion,ajeno', [
    (ASTRAL, NORKEVIN),
    (NORKEVIN, ASTRAL),
])
def test_smoke_cross_tenant_identidad_se_resuelve_por_id_no_por_email(client, sesion, ajeno):
    """Dos clientes con el MISMO email, uno en cada marca. La identidad
    debe resolverse por (tenant_id, client_id), nunca por el email --
    si se resolviera por email, una sesion podria terminar operando sobre
    el registro de la otra empresa sin darse cuenta."""
    import app as app_module

    datos = _crear_par_de_marcas(app_module)
    login_as_tenant(client, sesion, email=BRANDS[sesion]['login_email'])

    email_compartido = datos[sesion]['client']['email']
    assert email_compartido == datos[ajeno]['client']['email'], \
        'el fixture debe crear el MISMO email en ambas marcas'
    assert datos[sesion]['client']['id'] != datos[ajeno]['client']['id']

    # Dentro de un request context con la sesion puesta -- ver la nota
    # extensa en test_smoke_cross_tenant_recursos_ajenos_bloqueados sobre
    # por que store.list() suelto no prueba aislamiento.
    with app_module.app.test_request_context('/'):
        from flask import session as _sess
        _sess['tenant_id'] = sesion
        coincidencias = [c for c in app_module.store.list('clients')
                         if c.get('email') == email_compartido]
        assert len(coincidencias) == 1, \
            (f'con sesion en {sesion} se ven {len(coincidencias)} clientes con el email '
             f'compartido -- deberia verse solo el propio')
        assert coincidencias[0]['id'] == datos[sesion]['client']['id']
        assert coincidencias[0]['tenant_id'] == sesion
