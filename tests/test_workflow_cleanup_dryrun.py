"""Limpieza de workflows: dry-run, 'Trabajo completado' manual y cero envios.

Kevin, despues del incidente: marcar tareas como completadas es un cambio de
ESTADO, no una reproduccion del workflow. Un paso 'Auto send email' marcado
como completo NO puede generar ni mandar nada, y la informacion financiera
tiene que quedar exactamente igual.
"""
import uuid

ASTRAL = 'tenant-norkevin'
TOKEN_QS = None


def _token(app_module):
    return app_module._ADMIN_ONE_TIME_TOKEN


def _leer(app_module, tabla, record_id):
    """Lee como lo haria Astral con su sesion abierta.

    El fixture `client` se usa como context manager, y Flask conserva el
    contexto de la peticion despues de cada llamada. Sin sesion en ese
    contexto el aislamiento bloquea la lectura -- que es justo lo que debe
    pasar. Por eso las verificaciones se hacen desde la cuenta.
    """
    ctx = app_module.app.test_request_context('/')
    ctx.push()
    try:
        from flask import session
        session['tenant_id'] = ASTRAL
        return app_module.store.get(tabla, record_id)
    finally:
        ctx.pop()


def _seed_job(app_module, **campos):
    job = {
        'id': 'job-cleanup-' + uuid.uuid4().hex[:8],
        'tenant_id': ASTRAL,
        'nombre': 'Boda de prueba limpieza',
        'status': 'Confirmado',
        'boda_date': '2026-01-15',
        'created': '2025-06-01',
    }
    job.update(campos)
    app_module.store.upsert('jobs', job)
    return job


def _seed_payment(app_module, job_id):
    pago = {
        'id': 'pay-cleanup-' + uuid.uuid4().hex[:8],
        'tenant_id': ASTRAL,
        'job_id': job_id,
        'amount': 5000,
        'status': 'Pendiente',
        'due_date': '2026-01-01',
        'invoice_id': 'INV-CLEAN-' + uuid.uuid4().hex[:5].upper(),
    }
    app_module.store.upsert('payments', pago)
    return pago


def test_dry_run_no_modifica_nada(client):
    import app as app_module

    job = _seed_job(app_module)
    pago = _seed_payment(app_module, job['id'])
    antes = _leer(app_module, 'payments', pago['id'])

    resp = client.post(f'/api/admin/workflow-cleanup?token={_token(app_module)}', json={})
    assert resp.status_code == 200
    data = resp.get_json()

    assert data['modo'] == 'dry_run'
    assert data['jobs_modificados'] == [], 'un dry-run no debe modificar jobs'
    assert data['finanzas_intactas'] is True
    assert _leer(app_module, 'payments', pago['id']) == antes


def test_el_dry_run_reporta_por_empresa_y_sin_correos(client):
    import app as app_module

    _seed_job(app_module)
    resp = client.post(f'/api/admin/workflow-cleanup?token={_token(app_module)}', json={})
    resumen = resp.get_json()['resumen']

    assert 'ASTRAL WEDDINGS' in resumen
    assert 'Norkevin Photography' in resumen
    for info in resumen.values():
        # Lo mas importante del reporte: esta limpieza no manda nada.
        assert info['emails_generados'] == 0
        assert info['emails_enviados'] == 0
        assert info['pagos_afectados'] == 0
        assert info['facturas_afectadas'] == 0


def test_la_limpieza_no_envia_ningun_correo(client, monkeypatch):
    """La garantia central: aunque el workflow tenga pasos de tipo
    'Auto send email', cerrarlos no debe entregar nada."""
    import app as app_module

    enviados = []
    monkeypatch.setattr('src.mail_tracker.send_email',
                        lambda *a, **k: enviados.append(a) or None)

    _seed_job(app_module)
    resp = client.post(f'/api/admin/workflow-cleanup?token={_token(app_module)}',
                       json={'confirm': 'LIMPIAR_WORKFLOWS'})

    assert resp.status_code == 200
    assert enviados == [], 'marcar tareas NO debe enviar correos'


def test_la_limpieza_no_toca_la_informacion_financiera(client):
    """Kevin: montos, saldos, cuotas, fechas y estados deben quedar igual."""
    import app as app_module

    job = _seed_job(app_module)
    pago = _seed_payment(app_module, job['id'])
    antes = dict(_leer(app_module, 'payments', pago['id']))

    resp = client.post(f'/api/admin/workflow-cleanup?token={_token(app_module)}',
                       json={'confirm': 'LIMPIAR_WORKFLOWS'})

    assert resp.get_json()['finanzas_intactas'] is True
    despues = _leer(app_module, 'payments', pago['id'])
    for campo in ('amount', 'status', 'due_date', 'invoice_id', 'job_id'):
        assert despues.get(campo) == antes.get(campo), f'cambio {campo}'


def test_trabajo_completado_nunca_se_marca_solo(client):
    """El paso que Kevin quiere controlar a mano no puede cerrarse aca."""
    import app as app_module

    job = _seed_job(app_module)
    client.post(f'/api/admin/workflow-cleanup?token={_token(app_module)}',
                json={'confirm': 'LIMPIAR_WORKFLOWS'})

    steps, _, _ = app_module.compute_workflow_steps_for_job(
        _leer(app_module, 'jobs', job['id']))
    final = [s for s in steps if 'completado' in (s['name'] or '').lower()]
    assert final, 'el workflow debe tener un paso final'
    for s in final:
        assert s['status'] != 'done', \
            '"Trabajo completado" debe quedar pendiente para que lo marque Kevin'


def test_el_job_no_queda_marcado_como_finalizado(client):
    """Cambiar tareas no debe cambiar el estado del Job."""
    import app as app_module

    job = _seed_job(app_module, status='Confirmado')
    client.post(f'/api/admin/workflow-cleanup?token={_token(app_module)}',
                json={'confirm': 'LIMPIAR_WORKFLOWS'})

    assert _leer(app_module, 'jobs', job['id'])['status'] == 'Confirmado'
