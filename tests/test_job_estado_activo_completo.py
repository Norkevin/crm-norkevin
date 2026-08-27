"""Estados de job activo/completo segun FECHA y PAGO (uso diario, agosto 2026).

Kevin, punto 1 de la lista de uso diario: "estados correctos de jobs
activos/completos segun fecha/pago".

Habia dos problemas distintos:

  1. El dinero no contaba. Una boda que YA OCURRIO, con el workflow al
     100%, se pintaba "Completada" en verde aunque el cliente todavia
     debiera dinero. Un trabajo que falta cobrar no esta completo.

  2. El chip y el filtro no coincidian. El servidor decidia el chip en
     _job_estado_label() (workflow >= 100% -> "Completada"), pero el
     filtro de la interfaz volvia a deducir el estado por su cuenta en JS
     (`completed = rowStatus === 'Listo'`). Resultado: un job con el chip
     "Completada" no aparecia al filtrar "Completados", y ademas seguia
     contando como activo. Dos fuentes de verdad para lo mismo.

Ahora _job_estado_label() devuelve tambien un `estado_key` canonico, y la
interfaz filtra por ESE valor (data-estado-key / data-activo /
data-completado), sin volver a interpretarlo.

Se prueba con las DOS marcas: la logica no depende del tenant, y hay que
demostrar que se comporta igual en ambas.
"""
import pytest

ASTRAL = 'tenant-norkevin'
NORKEVIN = 'tenant-norkevin-photography'
AMBAS = [ASTRAL, NORKEVIN]


def _job(dias_restantes, *, status='Confirmado', workflow=0, tenant_id=ASTRAL):
    return {
        'id': f'job-test-{dias_restantes}-{workflow}',
        'tenant_id': tenant_id,
        'nombre': 'Boda Test',
        'status': status,
        'dias_restantes': dias_restantes,
        'workflow_progress': workflow,
    }


def _cuota(amount, status='Pendiente'):
    return {'id': 'pay-x', 'amount': amount, 'status': status}


# ============================================================
# El caso que motivo el cambio: ya paso, pero falta cobrar
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_boda_pasada_con_saldo_no_es_completada(tenant_id):
    """Aunque el workflow este al 100%: si deben dinero, no esta completo."""
    import app as app_module

    job = _job(-10, workflow=100, tenant_id=tenant_id)
    label, tone, key = app_module._job_estado_label(job, [_cuota(5000)])

    assert key == 'por_cobrar', f'estado_key={key} para una boda pasada con saldo'
    assert 'cobrar' in label.lower()
    assert '5,000' in label, f'el saldo debe verse en el chip: {label}'
    assert tone == 'red'
    assert key in app_module.ESTADOS_JOB_ACTIVOS, 'falta cobrar => sigue ACTIVO'
    assert key not in app_module.ESTADOS_JOB_COMPLETOS


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_boda_pasada_sin_saldo_y_workflow_completo_si_es_completada(tenant_id):
    import app as app_module

    job = _job(-10, workflow=100, tenant_id=tenant_id)
    label, tone, key = app_module._job_estado_label(job, [_cuota(0, 'Pagado')])

    assert key == 'completada'
    assert label == 'Completada'
    assert tone == 'green'
    assert key in app_module.ESTADOS_JOB_COMPLETOS
    assert key not in app_module.ESTADOS_JOB_ACTIVOS


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_boda_pasada_sin_saldo_pero_workflow_pendiente_queda_por_cerrar(tenant_id):
    import app as app_module

    job = _job(-3, workflow=40, tenant_id=tenant_id)
    _label, _tone, key = app_module._job_estado_label(job, [_cuota(0, 'Pagado')])

    assert key == 'por_cerrar'
    assert key in app_module.ESTADOS_JOB_ACTIVOS


# ============================================================
# La fecha sigue mandando sobre el workflow (lo que ya pedia Kevin)
# ============================================================

@pytest.mark.parametrize('tenant_id', AMBAS)
def test_evento_futuro_nunca_es_completado_aunque_el_workflow_este_al_100(tenant_id):
    """Regresion del criterio original: 'un evento futuro no deberia verse
    como completado si todavia no ocurrio'."""
    import app as app_module

    job = _job(30, workflow=100, tenant_id=tenant_id)
    label, _tone, key = app_module._job_estado_label(job, [])

    assert key == 'proxima'
    assert label == 'Proxima'
    assert key not in app_module.ESTADOS_JOB_COMPLETOS


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_evento_futuro_con_saldo_sigue_siendo_proxima_no_por_cobrar(tenant_id):
    """Deber dinero de una boda que TODAVIA NO OCURRIO es normal (el
    anticipo se paga a plazos). No debe alarmar como 'Por cobrar'."""
    import app as app_module

    job = _job(45, tenant_id=tenant_id)
    _label, _tone, key = app_module._job_estado_label(job, [_cuota(9000)])
    assert key == 'proxima'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_evento_hoy(tenant_id):
    import app as app_module
    _label, _tone, key = app_module._job_estado_label(_job(0, tenant_id=tenant_id), [])
    assert key == 'hoy'
    assert key in app_module.ESTADOS_JOB_ACTIVOS


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_archivado_y_cancelado_no_son_activos_ni_completados(tenant_id):
    import app as app_module

    for status, esperado in (('Archivado', 'archivado'), ('Cancelado', 'cancelado')):
        job = _job(-5, status=status, workflow=100, tenant_id=tenant_id)
        _label, _tone, key = app_module._job_estado_label(job, [_cuota(3000)])
        assert key == esperado
        assert key not in app_module.ESTADOS_JOB_ACTIVOS
        assert key not in app_module.ESTADOS_JOB_COMPLETOS


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_sin_fecha_es_activo_para_no_desaparecer(tenant_id):
    """Un job sin fecha no puede quedar invisible: sigue pidiendo atencion."""
    import app as app_module
    _label, _tone, key = app_module._job_estado_label(_job(None, tenant_id=tenant_id), [])
    assert key == 'sin_fecha'
    assert key in app_module.ESTADOS_JOB_ACTIVOS


# ============================================================
# Coherencia chip <-> filtro (la incoherencia original)
# ============================================================

def test_activos_y_completados_son_conjuntos_disjuntos():
    import app as app_module
    assert not (app_module.ESTADOS_JOB_ACTIVOS & app_module.ESTADOS_JOB_COMPLETOS)


def test_todo_estado_posible_esta_clasificado():
    """Ningun estado puede quedar fuera de activo/completo/terminal: si
    apareciera uno nuevo sin clasificar, un job podria no salir en ningun
    filtro -- exactamente el bug que se corrigio."""
    import app as app_module

    terminales = {'archivado', 'cancelado'}
    posibles = set()
    escenarios = [
        (_job(10), []),
        (_job(0), []),
        (_job(-1), [_cuota(100)]),
        (_job(-1), [_cuota(0, 'Pagado')]),
        (_job(-1, workflow=100), [_cuota(0, 'Pagado')]),
        (_job(None), []),
        (_job(-1, status='Archivado'), []),
        (_job(-1, status='Cancelado'), []),
    ]
    for job, pagos in escenarios:
        posibles.add(app_module._job_estado_label(job, pagos)[2])

    sin_clasificar = posibles - app_module.ESTADOS_JOB_ACTIVOS \
        - app_module.ESTADOS_JOB_COMPLETOS - terminales
    assert not sin_clasificar, f'estados sin clasificar: {sin_clasificar}'


@pytest.mark.parametrize('tenant_id', AMBAS)
def test_la_vista_de_jobs_expone_el_estado_canonico(auth_client, tenant_id):
    """/jobs debe mandar estado_key/es_activo/es_completado al HTML, que es
    lo que el filtro de la interfaz usa ahora."""
    import app as app_module
    from conftest import login_as_tenant

    login_as_tenant(auth_client, tenant_id,
                    email='astralweddingsgt@gmail.com' if tenant_id == ASTRAL
                    else 'norkevinfoto@gmail.com')

    cli = {'id': f'client-estado-{tenant_id}', 'tenant_id': tenant_id,
           'first_name': 'Estado', 'last_name': 'Test'}
    app_module.store.upsert('clients', cli)
    app_module.store.upsert('jobs', {
        'id': f'job-estado-{tenant_id}', 'tenant_id': tenant_id,
        'nombre': 'Boda Estado Test', 'client_id': cli['id'],
        'boda_date': '2020-01-01', 'status': 'Confirmado',
        'workflow_progress': 100,
    })
    app_module.store.upsert('payments', {
        'id': f'pay-estado-{tenant_id}', 'tenant_id': tenant_id,
        'job_id': f'job-estado-{tenant_id}', 'amount': 2500,
        'status': 'Pendiente',
    })

    resp = auth_client.get('/jobs')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'data-estado-key=' in html, 'la vista no expone el estado canonico'
    assert 'data-activo=' in html
    assert 'data-completado=' in html
    # La boda ya paso y falta cobrar: debe salir como por_cobrar, no completada
    assert 'data-estado-key="por_cobrar"' in html
