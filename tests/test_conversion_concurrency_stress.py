"""CONVERSION_CONCURRENCY_EXACTLY_ONCE -- stress de carrera real.

Una sola ejecucion verde de un test de concurrencia no prueba nada: las
carreras son probabilisticas. Este archivo repite el escenario de 5
peticiones simultaneas sobre la MISMA conversion logica 20 veces
seguidas, y exige el mismo resultado las 20:

    canonical_jobs = 1
    los 5 requests devuelven el MISMO job_id
    0 workflows duplicados
    0 calendarios de pago duplicados
    0 hilos caidos

Ademas escribe evidencia estructurada por iteracion en
`artifacts/concurrency_stress_evidence.json`, que es lo que
`pre_cutover_gate.py` lee para poder afirmar la propiedad de negocio
("exactamente 1") en vez de conformarse con el exit code de pytest.

CONTEXTO -- por que existe este archivo
---------------------------------------
El test de concurrencia anterior aceptaba `<= 2` jobs. Ese criterio era
incorrecto (el invariante de negocio es 1) y ademas nunca llego a
ejercitarse: en la corrida del 20-ago-2026, 4 de los 5 hilos murieron con
PermissionError [WinError 5] porque JsonStore._save() usaba un unico
archivo temporal compartido, asi que solo 1 hilo creaba algo y el test
pasaba por accidente.

Dos correcciones lo cierran:
  - `JsonStore._save()`: temporal unico por escritor + os.replace() con
    reintento (escrituras concurrentes ya no se pisan ni explotan).
  - `src/conversion_registry.py`: la identidad (tenant_id, lead_id) es
    PRIMARY KEY en SQLite. De N llamadas simultaneas gana exactamente una;
    las demas reciben conflicto, reconsultan y devuelven el job ganador.
"""
import json
import os
import threading

import pytest

# TENANTS SINTETICOS DEDICADOS (corregido tras la corrida 20260820_134955).
#
# La primera version usaba los tenants REALES ('tenant-norkevin' y
# 'tenant-norkevin-photography'). Al sembrar 20 iteraciones x 1 job cada
# una, dejaba ~20 jobs extra visibles para el resto de la suite y rompia
# tests/test_dashboard_upcoming_sessions.py, que verifica que una boda
# concreta aparezca en "Upcoming Sessions" (la lista tiene un tope y los
# jobs del stress la desplazaban). Eso es contaminacion del stress sobre
# datos compartidos: el test de dashboard NO estaba mal, no se toca.
#
# Estos tenants sinteticos ejercitan exactamente el mismo codigo -- la
# clave de conversion es (tenant_id, lead_id), no depende de que el
# tenant sea uno de los reales-- pero sus datos no los mira ningun otro
# test. Se conservan DOS para seguir demostrando que el aislamiento entre
# marcas se mantiene bajo concurrencia.
ASTRAL = 'tenant-stress-marca-a'
NORKEVIN = 'tenant-stress-marca-b'

ITERACIONES = 20
REQUESTS_SIMULTANEOS = 5

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCIA_PATH = os.path.join(REPO_ROOT, 'artifacts', 'concurrency_stress_evidence.json')


def _seed(app_module, tabla, tenant_id, **campos):
    import uuid
    record = {'id': f'{tabla[:4]}-{uuid.uuid4().hex[:8]}', 'tenant_id': tenant_id}
    record.update(campos)
    app_module.store.upsert(tabla, record)
    return record


def _una_iteracion(app_module, flask_app, numero, tenant_id, login_email):
    """Ejecuta UNA rafaga de 5 peticiones simultaneas y devuelve el
    registro de evidencia de esa iteracion."""
    client_rec = _seed(app_module, 'clients', tenant_id,
                       first_name=f'Stress{numero}', last_name='Test')
    lead = _seed(app_module, 'leads', tenant_id,
                 nombre=f'Stress {numero}', client_id=client_rec['id'])

    respuestas = []
    errores = []
    lock = threading.Lock()
    listos = threading.Barrier(REQUESTS_SIMULTANEOS)

    def _hacer_request():
        try:
            with flask_app.test_client() as c:
                with c.session_transaction() as sess:
                    sess['logged_in'] = True
                    sess['user_email'] = login_email
                    sess['user_name'] = 'Stress'
                    sess['tenant_id'] = tenant_id
                # Barrera: maximiza el solapamiento real. Sin esto los
                # hilos arrancan escalonados y la carrera casi no ocurre.
                listos.wait(timeout=10)
                resp = c.post('/api/jobs/new', json={
                    'nombre': f'Boda Stress {numero}',
                    'client_id': client_rec['id'],
                    'lead_id': lead['id'],
                })
                with lock:
                    respuestas.append((resp.status_code, resp.get_json()))
        except Exception as exc:
            with lock:
                errores.append(repr(exc))

    hilos = [threading.Thread(target=_hacer_request) for _ in range(REQUESTS_SIMULTANEOS)]
    for t in hilos:
        t.start()
    for t in hilos:
        t.join()

    job_ids_respuesta = sorted({b['job_id'] for _c, b in respuestas
                                if b and b.get('job_id')})

    with app_module.app.test_request_context('/'):
        from flask import session
        session['tenant_id'] = tenant_id
        jobs = [j for j in app_module.store.list('jobs')
                if j.get('lead_id') == lead['id']]
        job_id = jobs[0]['id'] if len(jobs) == 1 else None
        payments = [p for p in app_module.store.list('payments')
                    if job_id and p.get('job_id') == job_id]
        questionnaires = [q for q in app_module.store.list('questionnaires')
                          if job_id and q.get('job_id') == job_id]
        contracts = [c for c in app_module.store.list('contracts')
                     if job_id and c.get('job_id') == job_id]

    workflows = (app_module.workflow_engine.list_instances(
        subject_id=job_id, subject_type='job') if job_id else [])

    return {
        'iteracion': numero,
        'tenant_id': tenant_id,
        'lead_id': lead['id'],
        'requests_lanzados': REQUESTS_SIMULTANEOS,
        'respuestas_completadas': len(respuestas),
        'status_codes': [c for c, _b in respuestas],
        'job_ids_devueltos': job_ids_respuesta,
        'all_requests_same_job_id': len(job_ids_respuesta) == 1,
        'canonical_job_count': len(jobs),
        'canonical_job_id': job_id,
        'persisted_job_ids': [j['id'] for j in jobs],
        'workflow_count': len(workflows),
        'payment_schedule_count': len(payments),
        'questionnaire_count': len(questionnaires),
        'contract_count': len(contracts),
        'errors': errores,
    }


def _evaluar(ev):
    """Devuelve la lista de violaciones del criterio para una iteracion."""
    fallos = []
    if ev['errors']:
        fallos.append(f"hilos caidos: {ev['errors']}")
    if ev['respuestas_completadas'] != REQUESTS_SIMULTANEOS:
        fallos.append(f"solo completaron {ev['respuestas_completadas']}/{REQUESTS_SIMULTANEOS}")
    if any(c != 200 for c in ev['status_codes']):
        fallos.append(f"status codes != 200: {ev['status_codes']}")
    if ev['canonical_job_count'] != 1:
        fallos.append(f"canonical_job_count={ev['canonical_job_count']} "
                      f"(jobs: {ev['persisted_job_ids']}) -- debe ser exactamente 1")
    if not ev['all_requests_same_job_id']:
        fallos.append(f"job_ids distintos entre respuestas: {ev['job_ids_devueltos']}")
    if ev['workflow_count'] != 1:
        fallos.append(f"workflow_count={ev['workflow_count']} -- debe ser exactamente 1")
    if ev['questionnaire_count'] > 1:
        fallos.append(f"cuestionarios duplicados: {ev['questionnaire_count']}")
    if ev['contract_count'] > 1:
        fallos.append(f"contratos duplicados: {ev['contract_count']}")
    return fallos


@pytest.mark.parametrize('tenant_id,login_email', [
    (ASTRAL, 'stress-marca-a@example.invalid'),
    (NORKEVIN, 'stress-marca-b@example.invalid'),
])
def test_stress_conversion_concurrente_exactamente_un_job(flask_app, tenant_id, login_email):
    """20 iteraciones x 5 peticiones simultaneas, por cada marca sintetica.

    Se corre para DOS tenants: el registro de conversiones usa
    (tenant_id, lead_id) como clave, asi que hay que demostrar que
    funciona para los dos y que uno no interfiere con el otro.

    Los tenants son sinteticos y dedicados a proposito -- ver la nota en
    la cabecera del modulo sobre por que no se usan los reales."""
    import app as app_module
    from conftest import login_as_tenant  # noqa: F401  (garantiza tenant sintetico valido)

    app_module.store.upsert('tenants', {
        'id': tenant_id, 'name': tenant_id, 'login_email': login_email, 'active': True,
    })

    evidencia = []
    iteraciones_fallidas = []

    for n in range(1, ITERACIONES + 1):
        ev = _una_iteracion(app_module, flask_app, n, tenant_id, login_email)
        fallos = _evaluar(ev)
        ev['pass'] = not fallos
        ev['violaciones'] = fallos
        evidencia.append(ev)
        if fallos:
            iteraciones_fallidas.append((n, fallos))

    resumen = {
        'tenant_id': tenant_id,
        'iteraciones': ITERACIONES,
        'requests_por_iteracion': REQUESTS_SIMULTANEOS,
        'iteraciones_pass': sum(1 for e in evidencia if e['pass']),
        'iteraciones_fail': len(iteraciones_fallidas),
        'canonical_job_count_siempre_1': all(e['canonical_job_count'] == 1 for e in evidencia),
        'all_requests_same_job_id_siempre': all(e['all_requests_same_job_id'] for e in evidencia),
        'duplicate_workflows': sum(max(0, e['workflow_count'] - 1) for e in evidencia),
        'duplicate_payment_schedules': sum(
            max(0, e['payment_schedule_count'] - 1) for e in evidencia),
        'total_errors': sum(len(e['errors']) for e in evidencia),
        'detalle_por_iteracion': evidencia,
    }

    # Evidencia en disco para el gate. Se acumula por tenant en el mismo
    # archivo para que una corrida contenga las dos marcas.
    os.makedirs(os.path.dirname(EVIDENCIA_PATH), exist_ok=True)
    try:
        with open(EVIDENCIA_PATH, 'r', encoding='utf-8-sig') as fh:
            todo = json.load(fh)
    except (OSError, json.JSONDecodeError):
        todo = {}
    # El archivo se acumula entre los dos tenants parametrizados, que
    # corren en el MISMO proceso de pytest. Pero si viene de una corrida
    # ANTERIOR (otro pid), hay que empezar de cero: si no, quedaban
    # mezclados resultados de tenants que ya ni se usan -- en la corrida
    # 20260820_175708 el archivo todavia mostraba tenant-norkevin 0/20 de
    # una corrida vieja junto a las marcas sinteticas 20/20 de la actual.
    # Evidencia de dos corridas distintas en el mismo archivo es evidencia
    # que no se puede citar.
    pid_actual = os.getpid()
    if (not isinstance(todo, dict) or 'por_tenant' not in todo
            or todo.get('pid') != pid_actual):
        todo = {'pid': pid_actual, 'por_tenant': {}}
    todo['por_tenant'][tenant_id] = resumen
    with open(EVIDENCIA_PATH, 'w', encoding='utf-8') as fh:
        json.dump(todo, fh, indent=2, ensure_ascii=False)

    assert not iteraciones_fallidas, (
        f'{len(iteraciones_fallidas)}/{ITERACIONES} iteraciones violaron el criterio '
        f'de "exactamente 1 job" para {tenant_id}:\n' +
        '\n'.join(f'  iteracion {n}: {f}' for n, f in iteraciones_fallidas))
