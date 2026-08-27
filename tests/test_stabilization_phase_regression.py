"""Suite de regresion de la fase de estabilizacion (agosto 2026).

Cubre lo que test_job_requires_lead.py, test_credential_isolation.py y
test_incident_cross_company_email.py TODAVIA no cubrian cuando se audito el
repo en esta fase:

  1. Duplicacion de job via /api/jobs/new para el mismo lead (root cause de
     Camila Rios) -- llamadas secuenciales repetidas y concurrentes.
  2. idempotency_key en la capa de correo (queue_email/log_email): un
     idempotency_key ya ENVIADO nunca vuelve a salir.
  3. Retry: solo aplica sobre FALLO, nunca sobre ENVIADO/BLOQUEADO/DESCARTADO.
  4. Matriz de tenant que pide Kevin explicitamente para esta fase.

Nota sobre el test de concurrencia real (5 requests simultaneos): el fixture
`client` de este repo comparte un unico `app_module.store` (singleton de
proceso) entre threads del mismo proceso de test, con JsonStore respaldado
en archivo. Un test con threads reales SI puede reproducir una carrera de
verdad a nivel de Python (el GIL no protege la secuencia
leer-decidir-escribir de _find_job_for_lead/upsert_job), y por eso el test
de abajo la ejercita con threads reales.

ACTUALIZADO (agosto 2026): esta nota decia antes que la garantia dura
"todavia depende" del constraint que llega con V5.2, y por eso el test
aceptaba hasta 2 jobs. Ya no. La identidad de la conversion se materializa
en un PRIMARY KEY de SQLite HOY, sin esperar al cutover -- ver
src/conversion_registry.py. El criterio es EXACTAMENTE 1 job, y las 5
respuestas deben devolver el mismo job_id.
"""
import threading
import uuid

import pytest

from src.mail_tracker import (
    BLOQUEADO, ENVIADO, FALLO, PENDIENTE, MailTracker, MailStatus,
)

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'


def _seed(app_module, tabla, tenant_id, **campos):
    record = {'id': f'{tabla[:4]}-{uuid.uuid4().hex[:8]}', 'tenant_id': tenant_id}
    record.update(campos)
    app_module.store.upsert(tabla, record)
    return record


# ============================================================
# 1. Duplicacion de job via /api/jobs/new (Camila Rios)
# ============================================================

def test_repetir_api_jobs_new_con_mismo_lead_no_duplica(auth_client):
    """El caso simple: 2, 5, 20 llamadas secuenciales con el mismo lead_id
    deben producir exactamente UN job."""
    import app as app_module

    client_rec = _seed(app_module, 'clients', ASTRAL, first_name='Camila', last_name='Test')
    lead = _seed(app_module, 'leads', ASTRAL, nombre='Camila Test',
                 client_id=client_rec['id'])

    job_ids = set()
    for _ in range(5):
        resp = auth_client.post('/api/jobs/new', json={
            'nombre': 'Boda Camila Test',
            'client_id': client_rec['id'],
            'lead_id': lead['id'],
        })
        assert resp.status_code == 200
        job_ids.add(resp.get_json()['job_id'])

    assert len(job_ids) == 1, f'se crearon {len(job_ids)} jobs distintos para el mismo lead: {job_ids}'
    jobs_for_lead = [j for j in app_module.store.list('jobs') if j.get('lead_id') == lead['id']]
    assert len(jobs_for_lead) == 1


def test_api_jobs_new_con_lead_delega_en_funcion_canonica_de_conversion(auth_client):
    """Consolidacion (prioridad 8, cierre de brechas -- agosto 2026):
    /api/jobs/new y /api/leads/<id>/accept-quote ya NO tienen dos copias
    parecidas de la logica de conversion. Antes /api/jobs/new solo creaba
    el job (upsert_job) y nada mas -- ni workflow_instance ni cuestionario.
    Ahora ambas rutas llaman a _convert_lead_to_job, asi que un job creado
    via /api/jobs/new con lead_id debe traer los mismos efectos colaterales
    que uno creado via accept-quote."""
    import app as app_module

    client_rec = _seed(app_module, 'clients', ASTRAL, first_name='Consolida', last_name='Test')
    lead = _seed(app_module, 'leads', ASTRAL, nombre='Consolida Test', client_id=client_rec['id'])

    resp = auth_client.post('/api/jobs/new', json={
        'nombre': 'Boda Consolida', 'client_id': client_rec['id'], 'lead_id': lead['id'],
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['job_created'] is True
    assert body['workflow_created'] is True

    job_id = body['job_id']
    # OJO: workflow_instances NO es una tabla-lista del JsonStore, es un
    # dict (se guarda con save_dict/get_dict, ver src/workflow/engine.py
    # _save_to_storage). store.list() sobre esa clave itera las LLAVES y
    # devuelve strings, no registros -- por eso se consulta a traves del
    # engine, que es la unica fuente de verdad de estas instancias.
    workflow_instances = app_module.workflow_engine.list_instances(
        subject_id=job_id, subject_type='job')
    assert len(workflow_instances) == 1, (
        'la ruta consolidada debe crear exactamente una workflow_instance de produccion, '
        'igual que accept-quote -- antes /api/jobs/new no creaba ninguna'
    )

    # Repetir la misma llamada no debe crear una segunda workflow_instance
    # ni un segundo job -- misma garantia de idempotencia que accept-quote,
    # porque ahora comparten la misma funcion canonica.
    resp2 = auth_client.post('/api/jobs/new', json={
        'nombre': 'Boda Consolida', 'client_id': client_rec['id'], 'lead_id': lead['id'],
    })
    body2 = resp2.get_json()
    assert body2['already_converted'] is True
    assert body2['job_id'] == job_id
    workflow_instances_after = app_module.workflow_engine.list_instances(
        subject_id=job_id, subject_type='job')
    assert len(workflow_instances_after) == 1


def test_segunda_llamada_devuelve_already_converted(auth_client):
    import app as app_module

    client_rec = _seed(app_module, 'clients', ASTRAL, first_name='Ana', last_name='Dup')
    lead = _seed(app_module, 'leads', ASTRAL, nombre='Ana Dup', client_id=client_rec['id'])

    primera = auth_client.post('/api/jobs/new', json={
        'nombre': 'Boda Ana', 'client_id': client_rec['id'], 'lead_id': lead['id'],
    }).get_json()
    assert primera.get('job_created') is True

    segunda = auth_client.post('/api/jobs/new', json={
        'nombre': 'Boda Ana', 'client_id': client_rec['id'], 'lead_id': lead['id'],
    }).get_json()
    assert segunda['already_converted'] is True
    assert segunda.get('job_created') is False
    assert segunda['job_id'] == primera['job_id']


def test_cinco_requests_concurrentes_mismo_lead_un_solo_job(auth_client, flask_app):
    """5 requests simultaneos contra el mismo lead_id -> EXACTAMENTE 1 job.

    CRITERIO ENDURECIDO (agosto 2026). La version anterior de este test
    aceptaba `<= 2` jobs, con el argumento de que el guardia de aplicacion
    no podia cerrar la ventana de carrera sin un constraint de base de
    datos. Ese criterio estaba MAL por dos razones:

      1. No es el criterio de negocio. La causa raiz del incidente de
         Camila Rios fue precisamente una conversion produciendo varios
         jobs. Aceptar 2 es aceptar el bug en version pequena.
      2. Nunca llego a probar nada. En la corrida del 20-ago-2026, 4 de
         los 5 hilos MURIERON con PermissionError [WinError 5] al escribir
         el JSON (todos compartian el mismo archivo .tmp), asi que solo 1
         hilo llego a crear un job. El test daba verde porque los otros
         cuatro se cayeron, no porque la idempotencia funcionara.

    Ambas cosas estan corregidas: `JsonStore._save()` usa un temporal
    unico por escritor + os.replace() con reintento, y la identidad de la
    conversion vive en un PRIMARY KEY de SQLite
    (src/conversion_registry.py). Ahora se exige lo que siempre debio
    exigirse: 1 job, y las 5 respuestas apuntando al mismo."""
    import app as app_module

    client_rec = _seed(app_module, 'clients', ASTRAL, first_name='Concurrencia', last_name='Test')
    lead = _seed(app_module, 'leads', ASTRAL, nombre='Concurrencia Test', client_id=client_rec['id'])

    resultados = []
    errores = []
    lock = threading.Lock()

    def _hacer_request():
        try:
            with flask_app.test_client() as c:
                with c.session_transaction() as sess:
                    sess['logged_in'] = True
                    sess['user_email'] = 'astralweddingsgt@gmail.com'
                    sess['user_name'] = 'Test'
                    sess['tenant_id'] = ASTRAL
                resp = c.post('/api/jobs/new', json={
                    'nombre': 'Boda Concurrencia', 'client_id': client_rec['id'],
                    'lead_id': lead['id'],
                })
                with lock:
                    resultados.append((resp.status_code, resp.get_json()))
        except Exception as exc:  # un hilo que muere NO puede pasar como verde
            with lock:
                errores.append(repr(exc))

    threads = [threading.Thread(target=_hacer_request) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Las 5 peticiones deben haber COMPLETADO. Si alguna se cae, el
    # escenario no se probo: es justo lo que enmascaraba el verde anterior.
    assert not errores, f'hilos que murieron durante la concurrencia: {errores}'
    assert len(resultados) == 5, f'solo completaron {len(resultados)} de 5 requests'
    assert all(code == 200 for code, _ in resultados), \
        f'status codes: {[c for c, _ in resultados]}'

    job_ids = {body['job_id'] for _code, body in resultados}
    assert len(job_ids) == 1, (
        f'las 5 respuestas devolvieron {len(job_ids)} job_ids distintos: {job_ids}. '
        'Todas deben referenciar la MISMA conversion canonica.')

    with app_module.app.test_request_context('/'):
        from flask import session
        session['tenant_id'] = ASTRAL
        jobs_for_lead = [j for j in app_module.store.list('jobs')
                         if j.get('lead_id') == lead['id']]
    assert len(jobs_for_lead) == 1, (
        f'se persistieron {len(jobs_for_lead)} jobs para un mismo lead con 5 '
        f'requests concurrentes: {[j["id"] for j in jobs_for_lead]}. '
        'El criterio es EXACTAMENTE 1.')
    assert jobs_for_lead[0]['id'] == job_ids.pop()


# ============================================================
# 2. idempotency_key en la capa de correo
# ============================================================

def test_idempotency_key_ya_enviado_no_se_reenvia(monkeypatch):
    import app as app_module
    from src.email_delivery import DeliveryResult

    llamadas = []

    def _fake_send(to_email, subject, body='', **kwargs):
        llamadas.append(to_email)
        return DeliveryResult(ok=True, provider='test', message_id=f'm-{len(llamadas)}', mode='test')

    monkeypatch.setattr('src.mail_tracker.send_email', _fake_send)

    lead = _seed(app_module, 'leads', ASTRAL, nombre='Idempotencia Test')
    tracker = MailTracker()
    key = f'reminder:{lead["id"]}:2026-08-16'

    primero = tracker.log_email('cliente@example.com', 'Recordatorio', 'cuerpo',
                                 lead_id=lead['id'], tenant_id=ASTRAL, idempotency_key=key)
    assert primero['status'] == MailStatus.SENT.value
    assert len(llamadas) == 1

    segundo = tracker.log_email('cliente@example.com', 'Recordatorio', 'cuerpo',
                                 lead_id=lead['id'], tenant_id=ASTRAL, idempotency_key=key)
    assert segundo['id'] == primero['id'], 'debio devolver el mismo registro ENVIADO, no crear otro'
    assert len(llamadas) == 1, 'send_email NO debio llamarse una segunda vez para la misma idempotency_key'


def test_idempotency_key_distinta_si_se_envia(monkeypatch):
    import app as app_module
    from src.email_delivery import DeliveryResult

    monkeypatch.setattr(
        'src.mail_tracker.send_email',
        lambda *a, **k: DeliveryResult(ok=True, provider='test', message_id='m', mode='test'),
    )
    lead = _seed(app_module, 'leads', ASTRAL, nombre='Idempotencia Test 2')
    tracker = MailTracker()

    a = tracker.log_email('cliente@example.com', 'Recordatorio', 'cuerpo',
                          lead_id=lead['id'], tenant_id=ASTRAL, idempotency_key='key-a')
    b = tracker.log_email('cliente@example.com', 'Recordatorio', 'cuerpo',
                          lead_id=lead['id'], tenant_id=ASTRAL, idempotency_key='key-b')
    assert a['id'] != b['id']


def test_idempotency_key_en_cola_de_aprobacion_bloquea_reenvio(monkeypatch):
    import app as app_module
    from src.email_delivery import DeliveryResult

    monkeypatch.setattr(
        'src.mail_tracker.send_email',
        lambda *a, **k: DeliveryResult(ok=True, provider='test', message_id='m', mode='test'),
    )
    client_rec = _seed(app_module, 'clients', ASTRAL, first_name='Pend', last_name='Test',
                       email='pend@example.com')
    job = _seed(app_module, 'jobs', ASTRAL, nombre='Boda Pend', client_id=client_rec['id'])
    tracker = MailTracker()
    key = f'invoice:{job["id"]}:reminder-1'

    pendiente = tracker.queue_email('pend@example.com', 'Factura', 'cuerpo',
                                     client_id=client_rec['id'], job_id=job['id'],
                                     tenant_id=ASTRAL, idempotency_key=key)
    enviado = tracker.approve_and_send(pendiente['id'], sender_tenant_id=ASTRAL)
    assert enviado['ok'] is True

    otra_vez = tracker.queue_email('pend@example.com', 'Factura', 'cuerpo',
                                    client_id=client_rec['id'], job_id=job['id'],
                                    tenant_id=ASTRAL, idempotency_key=key)
    assert otra_vez.get('status') == ENVIADO, (
        'un segundo queue_email con la misma idempotency_key ya ENVIADA debe '
        'devolver el registro existente, no encolar un pendiente nuevo'
    )


# ============================================================
# 3. Retry: solo sobre FALLO
# ============================================================

def test_retry_no_funciona_sobre_bloqueado():
    """Corregido tras la validacion real en Windows (agosto 2026): la
    version anterior de este test usaba job_id='job-...-que-no-existe' y
    esperaba BLOQUEADO. Eso contradice el contrato explicito de
    check_same_tenant(), que a proposito NO bloquea por un registro
    inexistente ('dueno None = registro inexistente o sin cuenta: no se
    bloquea por eso'). Un id inexistente no prueba nada sobre aislamiento;
    lo que hay que probar es el caso REAL: un job que pertenece a la OTRA
    empresa. Eso si debe bloquear."""
    import app as app_module
    tracker = MailTracker()
    job_de_norkevin = _seed(app_module, 'jobs', NORKEVIN, nombre='Boda Norkevin')
    pendiente = tracker.queue_email('x@example.com', 'Cobro', 'cuerpo',
                                     job_id=job_de_norkevin['id'],
                                     tenant_id=ASTRAL)
    assert pendiente['status'] == BLOQUEADO, (
        'un correo de Astral que apunta a un job de Norkevin debe quedar BLOQUEADO')
    resultado = tracker.retry_failed(pendiente['id'])
    assert resultado['ok'] is False
    assert 'fallo por' in resultado['error'].lower() or 'tecnico' in resultado['error'].lower()


def test_retry_no_funciona_sobre_enviado(monkeypatch):
    import app as app_module
    from src.email_delivery import DeliveryResult
    monkeypatch.setattr(
        'src.mail_tracker.send_email',
        lambda *a, **k: DeliveryResult(ok=True, provider='test', message_id='m', mode='test'),
    )
    lead = _seed(app_module, 'leads', ASTRAL, nombre='Retry Enviado')
    tracker = MailTracker()
    pendiente = tracker.queue_email('x@example.com', 'Cobro', 'cuerpo',
                                     lead_id=lead['id'], tenant_id=ASTRAL)
    enviado = tracker.approve_and_send(pendiente['id'], sender_tenant_id=ASTRAL)
    assert enviado['ok'] is True
    resultado = tracker.retry_failed(pendiente['id'])
    assert resultado['ok'] is False


def test_retry_si_funciona_sobre_fallo(monkeypatch):
    import app as app_module
    from src.email_delivery import DeliveryResult
    intentos = {'n': 0}

    def _fake_send(to_email, subject, body='', **kwargs):
        intentos['n'] += 1
        if intentos['n'] == 1:
            return DeliveryResult(ok=False, provider='test', status='failed', error='timeout', mode='test')
        return DeliveryResult(ok=True, provider='test', message_id='m2', mode='test')

    monkeypatch.setattr('src.mail_tracker.send_email', _fake_send)
    lead = _seed(app_module, 'leads', ASTRAL, nombre='Retry Fallo')
    tracker = MailTracker()
    pendiente = tracker.queue_email('x@example.com', 'Cobro', 'cuerpo',
                                     lead_id=lead['id'], tenant_id=ASTRAL)
    primero = tracker.approve_and_send(pendiente['id'], sender_tenant_id=ASTRAL)
    assert primero['ok'] is False
    assert primero['pendiente']['status'] == FALLO

    # sender_tenant_id explicito: fuera de una peticion HTTP no hay sesion,
    # asi que approve_and_send no puede resolver la cuenta activa sola. Antes
    # retry_failed ni siquiera aceptaba este parametro y el reintento fallaba
    # SIEMPRE con "Sin cuenta activa" en cualquier contexto sin sesion
    # (bug real encontrado en la primera corrida en Windows, agosto 2026).
    segundo = tracker.retry_failed(pendiente['id'], sender_tenant_id=ASTRAL)
    assert segundo['ok'] is True, segundo.get('error')
    assert segundo['pendiente']['status'] == ENVIADO


# ============================================================
# 4. Matriz de tenant pedida por Kevin
# ============================================================

def test_mismo_email_cliente_en_ambas_marcas_no_se_confunde(monkeypatch):
    import app as app_module
    from src.email_delivery import DeliveryResult
    monkeypatch.setattr(
        'src.mail_tracker.send_email',
        lambda *a, **k: DeliveryResult(ok=True, provider='test', message_id='m', mode='test'),
    )
    email_compartido = 'compartido@example.com'
    cliente_astral = _seed(app_module, 'clients', ASTRAL, first_name='Cliente', last_name='Astral',
                           email=email_compartido)
    cliente_norkevin = _seed(app_module, 'clients', NORKEVIN, first_name='Cliente', last_name='Norkevin',
                             email=email_compartido)
    job_astral = _seed(app_module, 'jobs', ASTRAL, nombre='Boda Astral', client_id=cliente_astral['id'])

    tracker = MailTracker()
    enviado = tracker.log_email(email_compartido, 'Factura', 'cuerpo',
                                job_id=job_astral['id'], tenant_id=ASTRAL)
    assert enviado['status'] == MailStatus.SENT.value

    motivo, aviso = __import__('src.mail_tracker', fromlist=['check_recipient_identity']) \
        .check_recipient_identity(ASTRAL, email_compartido, cliente_norkevin['id'])
    assert motivo is not None, 'un client_id de Norkevin nunca debe pasar la verificacion bajo tenant Astral'


def test_mismo_nombre_en_ambas_marcas_no_se_confunde(monkeypatch):
    import app as app_module
    from src.mail_tracker import check_recipient_identity
    cliente_astral = _seed(app_module, 'clients', ASTRAL, first_name='Maria', last_name='Lopez',
                           email='maria.astral@example.com')
    cliente_norkevin = _seed(app_module, 'clients', NORKEVIN, first_name='Maria', last_name='Lopez',
                             email='maria.norkevin@example.com')
    motivo, _ = check_recipient_identity(ASTRAL, 'maria.norkevin@example.com', cliente_astral['id'])
    assert motivo is None or 'no es la que tiene registrada' in (motivo or '')


def test_client_id_correcto_pero_email_de_la_otra_marca_no_bloquea_duro_pero_avisa(monkeypatch):
    """No es un bloqueo (un cliente puede pedir que le escriban a otra
    direccion), pero debe quedar marcado como aviso para revision -- ya
    cubierto por check_recipient_identity, este test solo fija el
    comportamiento explicitamente para esta fase."""
    import app as app_module
    from src.mail_tracker import check_recipient_identity
    cliente = _seed(app_module, 'clients', ASTRAL, first_name='Test', last_name='Aviso',
                    email='real@example.com')
    motivo, aviso = check_recipient_identity(ASTRAL, 'otra-direccion@example.com', cliente['id'])
    assert motivo is None
    assert aviso is not None


def test_tenant_id_inexistente_bloquea(monkeypatch):
    """Corregido tras la validacion real en Windows (agosto 2026).

    La version anterior esperaba que check_same_tenant() devolviera
    'sin cuenta identificada' para un tenant_id inexistente-pero-no-vacio.
    No es su trabajo: check_same_tenant compara que lead/job/plantilla
    pertenezcan a la MISMA cuenta, y sin ninguno de esos tres no hay nada
    que comparar (devuelve None correctamente). Solo devuelve
    'sin cuenta identificada' cuando el tenant_id viene vacio.

    La capa que SI bloquea un tenant desconocido es la resolucion canonica
    de marca (src/tenant_brand_map): un tenant que no existe no tiene
    remitente, y sender_email_for_tenant falla duro en vez de adivinar.
    Este test verifica AMBAS mitades para que el contrato quede fijado."""
    from src.mail_tracker import check_same_tenant
    from src.tenant_brand_map import resolve_brand, UnresolvedBrandError

    # Mitad 1: sin cuenta -> ese si es el caso de check_same_tenant.
    assert check_same_tenant('', lead_id=None, job_id=None, template_id=None) == \
        'sin cuenta identificada'

    # Mitad 2: un tenant inexistente no tiene identidad de marca ni
    # remitente -- nunca se le inventa uno.
    with pytest.raises(UnresolvedBrandError):
        resolve_brand('tenant-que-no-existe-jamas')


def test_email_connection_id_inexistente_bloquea():
    from src.tenant_brand_map import is_connection_owned_by_tenant
    assert is_connection_owned_by_tenant(ASTRAL, 'connection-que-no-existe') is False
    assert is_connection_owned_by_tenant(ASTRAL, None) is False
    assert is_connection_owned_by_tenant(None, 'algo') is False


def test_conexion_astral_no_sirve_para_norkevin_por_mapeo_canonico():
    from src.tenant_brand_map import is_connection_owned_by_tenant, resolve_brand
    conexion_astral = resolve_brand(ASTRAL).email_connection_id
    assert is_connection_owned_by_tenant(NORKEVIN, conexion_astral) is False


def test_sender_hardcodeado_ya_no_se_usa_para_resolver_empresa(monkeypatch):
    """Regresion directa de los 3 hardcodes 'ASTRAL WEDDINGS' encontrados en
    _ensure_job_for_lead, la notificacion de leads recientes, y
    /api/jobs/new -- un job creado bajo el tenant de Norkevin Photography
    NUNCA debe quedar marcado como 'Astral Weddings' en el campo empresa."""
    import app as app_module
    from conftest import login_as_tenant

    with app_module.app.test_client() as c:
        login_as_tenant(c, NORKEVIN, email='norkevinfoto@gmail.com')
        cliente = _seed(app_module, 'clients', NORKEVIN, first_name='Cliente', last_name='Norkevin')
        resp = c.post('/api/jobs/new', json={
            'nombre': 'Boda Norkevin', 'client_id': cliente['id'],
        })
        data = resp.get_json()
        assert data['ok'] is True
        assert data['job']['empresa'] != 'ASTRAL WEDDINGS', (
            'el job quedo marcado como Astral Weddings aunque se creo bajo '
            'el tenant de Norkevin Photography -- volvio el hardcode'
        )


# ============================================================
# 5. Evidencia especifica pedida para la validacion final
#    (agosto 2026): regresion de retry_failed() y counts
#    reales de la conversion lead->job.
# ============================================================

def _contar_efectos(app_module, job_id, lead_id):
    """Counts reales de TODOS los efectos colaterales de una conversion.
    Se cuenta dentro de un request context explicito para que el alcance
    de tenant sea el mismo en todas las mediciones (ver la nota larga en
    tests/test_reset_endpoint_hardening.py::_contar sobre por que un
    conteo sin contexto y otro con contexto no son comparables)."""
    with app_module.app.test_request_context('/'):
        from flask import session
        session['tenant_id'] = ASTRAL
        return {
            'jobs_del_lead': len([j for j in app_module.store.list('jobs')
                                  if j.get('lead_id') == lead_id]),
            'workflows': len(app_module.workflow_engine.list_instances(
                subject_id=job_id, subject_type='job')) if job_id else 0,
            'payments': len([p for p in app_module.store.list('payments')
                             if p.get('job_id') == job_id]) if job_id else 0,
            'questionnaires': len([q for q in app_module.store.list('questionnaires')
                                   if q.get('job_id') == job_id]) if job_id else 0,
            'contracts': len([c for c in app_module.store.list('contracts')
                              if c.get('job_id') == job_id]) if job_id else 0,
        }


def test_retry_cross_tenant_bloqueado_con_cero_llamadas_al_proveedor(monkeypatch):
    """CASO CROSS-TENANT de la regresion de retry_failed().

    El pendiente pertenece a Astral. El que reintenta se identifica como
    Norkevin. Debe bloquearse SIN tocar el proveedor: provider_calls == 0.

    Este es justamente el escenario que el parametro sender_tenant_id
    podria haber abierto si se hubiera implementado tomando el tenant del
    propio pendiente como default -- por eso NO se implemento asi."""
    import app as app_module
    from src.email_delivery import DeliveryResult

    provider_calls = {'n': 0}

    def _proveedor(to_email, subject, body='', **kwargs):
        provider_calls['n'] += 1
        return DeliveryResult(ok=False, provider='test', status='failed',
                              error='timeout', mode='test')

    monkeypatch.setattr('src.mail_tracker.send_email', _proveedor)

    lead = _seed(app_module, 'leads', ASTRAL, nombre='Retry CrossTenant')
    tracker = MailTracker()
    pendiente = tracker.queue_email('x@example.com', 'Cobro', 'cuerpo',
                                     lead_id=lead['id'], tenant_id=ASTRAL)
    # Primer envio (legitimo, como Astral) -> queda en FALLO.
    primero = tracker.approve_and_send(pendiente['id'], sender_tenant_id=ASTRAL)
    assert primero['ok'] is False
    assert primero['pendiente']['status'] == FALLO
    llamadas_tras_el_fallo = provider_calls['n']
    assert llamadas_tras_el_fallo == 1

    # Reintento identificandose como la OTRA empresa -> bloqueado.
    cruzado = tracker.retry_failed(pendiente['id'], sender_tenant_id=NORKEVIN)
    assert cruzado['ok'] is False, 'Norkevin no debe poder reintentar un pendiente de Astral'
    assert provider_calls['n'] == llamadas_tras_el_fallo, (
        f"provider_calls subio de {llamadas_tras_el_fallo} a {provider_calls['n']} -- "
        'un reintento cross-tenant NUNCA debe alcanzar la capa de envio')


def test_retry_sin_sender_tenant_falla_cerrado(monkeypatch):
    """CASO SIN SENDER TENANT de la regresion de retry_failed().

    Contrato implementado y documentado: retry_failed NO infiere la
    identidad del pendiente. Sin cuenta activa (fuera de una peticion) y
    sin sender_tenant_id explicito, approve_and_send corta con
    'Sin cuenta activa' y no envia nada. Falla CERRADO."""
    import app as app_module
    from src.email_delivery import DeliveryResult

    provider_calls = {'n': 0}

    def _proveedor(to_email, subject, body='', **kwargs):
        provider_calls['n'] += 1
        return DeliveryResult(ok=False, provider='test', status='failed',
                              error='timeout', mode='test')

    monkeypatch.setattr('src.mail_tracker.send_email', _proveedor)

    lead = _seed(app_module, 'leads', ASTRAL, nombre='Retry SinTenant')
    tracker = MailTracker()
    pendiente = tracker.queue_email('x@example.com', 'Cobro', 'cuerpo',
                                     lead_id=lead['id'], tenant_id=ASTRAL)
    tracker.approve_and_send(pendiente['id'], sender_tenant_id=ASTRAL)
    llamadas_tras_el_fallo = provider_calls['n']

    sin_tenant = tracker.retry_failed(pendiente['id'])
    assert sin_tenant['ok'] is False, (
        'sin identidad de emisor el reintento debe fallar cerrado, no adivinar')
    assert provider_calls['n'] == llamadas_tras_el_fallo, \
        'un reintento sin cuenta identificada no debe alcanzar el proveedor'


def test_conversion_idempotente_counts_reales_before_after(auth_client):
    """CONTEO REAL before/after de /api/jobs/new (idempotencia).

    Primera llamada: se crean job + workflow. Segunda llamada identica:
    exactamente los MISMOS counts, ningun efecto colateral duplicado."""
    import app as app_module

    client_rec = _seed(app_module, 'clients', ASTRAL, first_name='Counts', last_name='Test')
    lead = _seed(app_module, 'leads', ASTRAL, nombre='Counts Test', client_id=client_rec['id'])

    antes = _contar_efectos(app_module, None, lead['id'])
    assert antes['jobs_del_lead'] == 0

    primera = auth_client.post('/api/jobs/new', json={
        'nombre': 'Boda Counts', 'client_id': client_rec['id'], 'lead_id': lead['id'],
    }).get_json()
    job_id = primera['job_id']
    despues_1 = _contar_efectos(app_module, job_id, lead['id'])

    assert despues_1['jobs_del_lead'] == 1
    assert despues_1['workflows'] == 1

    segunda = auth_client.post('/api/jobs/new', json={
        'nombre': 'Boda Counts', 'client_id': client_rec['id'], 'lead_id': lead['id'],
    }).get_json()
    assert segunda['job_id'] == job_id
    despues_2 = _contar_efectos(app_module, job_id, lead['id'])

    assert despues_2 == despues_1, (
        f'la segunda llamada cambio los counts: {despues_1} -> {despues_2}. '
        'Ningun efecto colateral debe duplicarse.')


def test_cinco_llamadas_concurrentes_estado_final_identico_counts(auth_client, flask_app):
    """CONCURRENCIA con counts reales: 5 llamadas simultaneas al mismo
    lead. Se mide el estado completo (jobs/workflows/payments/
    questionnaires/contracts) y se compara contra el de UNA sola llamada.

    Criterio: EXACTAMENTE 1 job canonico, las 5 respuestas apuntando a el,
    y CERO efectos colaterales duplicados (workflow, cuotas, cuestionario,
    contrato). Sin margen de tolerancia: aceptar 2 seria aceptar el bug de
    Camila Rios en version pequena."""
    import app as app_module

    client_rec = _seed(app_module, 'clients', ASTRAL, first_name='ConcCounts', last_name='Test')
    lead = _seed(app_module, 'leads', ASTRAL, nombre='ConcCounts Test', client_id=client_rec['id'])

    resultados = []
    errores = []
    lock = threading.Lock()

    def _hacer_request():
        try:
            with flask_app.test_client() as c:
                with c.session_transaction() as sess:
                    sess['logged_in'] = True
                    sess['user_email'] = 'astralweddingsgt@gmail.com'
                    sess['user_name'] = 'Test'
                    sess['tenant_id'] = ASTRAL
                resp = c.post('/api/jobs/new', json={
                    'nombre': 'Boda ConcCounts', 'client_id': client_rec['id'],
                    'lead_id': lead['id'],
                })
                with lock:
                    resultados.append((resp.status_code, resp.get_json()))
        except Exception as exc:
            with lock:
                errores.append(repr(exc))

    threads = [threading.Thread(target=_hacer_request) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errores, f'hilos que murieron durante la concurrencia: {errores}'
    assert len(resultados) == 5, f'solo completaron {len(resultados)} de 5 requests'

    job_ids = {body['job_id'] for _c, body in resultados}
    assert len(job_ids) == 1, (
        f'las 5 respuestas devolvieron {len(job_ids)} job_ids distintos: {job_ids}')
    job_id = job_ids.pop()

    with app_module.app.test_request_context('/'):
        from flask import session
        session['tenant_id'] = ASTRAL
        jobs_del_lead = [j for j in app_module.store.list('jobs')
                         if j.get('lead_id') == lead['id']]
        payments = [p for p in app_module.store.list('payments')
                    if p.get('job_id') == job_id]
        questionnaires = [q for q in app_module.store.list('questionnaires')
                          if q.get('job_id') == job_id]
        contracts = [c for c in app_module.store.list('contracts')
                     if c.get('job_id') == job_id]
    workflows = app_module.workflow_engine.list_instances(
        subject_id=job_id, subject_type='job')

    # EXACTAMENTE 1 job canonico -- el criterio de negocio, sin margen.
    assert len(jobs_del_lead) == 1, (
        f'5 requests concurrentes produjeron {len(jobs_del_lead)} jobs: '
        f'{[j["id"] for j in jobs_del_lead]}. El criterio es EXACTAMENTE 1.')
    assert jobs_del_lead[0]['id'] == job_id

    # Y ni un solo efecto colateral duplicado.
    assert len(workflows) == 1, (
        f'{len(workflows)} workflow_instances para un solo job -- se duplico '
        'un efecto colateral bajo concurrencia')
    assert len(questionnaires) <= 1, (
        f'{len(questionnaires)} cuestionarios para un solo job -- duplicado')
    assert len(contracts) <= 1, (
        f'{len(contracts)} contratos para un solo job -- duplicado')
    # Sin quote no se genera calendario de pagos por esta ruta; lo que no
    # puede pasar es que aparezcan cuotas duplicadas de la nada.
    cuotas_por_grupo = {}
    for p in payments:
        cuotas_por_grupo.setdefault(p.get('cuota'), []).append(p['id'])
    duplicadas = {k: v for k, v in cuotas_por_grupo.items() if len(v) > 1}
    assert not duplicadas, f'cuotas duplicadas bajo concurrencia: {duplicadas}'
