"""Al aceptar una cotizacion se dispara el PRODUCTION_WORKFLOW del job nuevo.
Bug real que Kevin encontro: el step 'Cuestionario cliente' (due_date de tipo
'after_event', o sea "1 mes antes de la boda") aparecia marcado como
completado el mismo dia que se acepta la cotizacion, sin que nadie lo haya
disparado y sin que se mandara nada -- por dos bugs combinados en
Step.offset_minutes (devolvia 0 para cualquier modo que no fuera
'after_creation', incluyendo 'after_event') y en
WorkflowEngine.start_workflow (auto-ejecutaba el primer step con
offset_minutes == 0 aunque el comentario del propio archivo dice
'NO auto-ejecutar')."""
import uuid


def _make_lead(app_module, **overrides):
    lead_id = 'lead-' + uuid.uuid4().hex[:8]
    lead = {
        'id': lead_id,
        'nombre': 'Cliente Workflow Test',
        'email': 'workflow.test@example.com',
        'telefono': '555-0000',
        'status': 'Nuevo',
        'tenant_id': 'tenant-norkevin',
    }
    lead.update(overrides)
    app_module.upsert_lead(lead)
    return lead


def _make_quote(app_module, lead_id, **overrides):
    quote_id = 'quote-' + uuid.uuid4().hex[:8]
    quote = {
        'id': quote_id,
        'lead_id': lead_id,
        'paquete_nombre': 'Gold',
        'precio_total': 18000,
        'incluye': ['Cobertura 10h'],
        'status': 'Enviada',
        'plan_pago': 1,
    }
    quote.update(overrides)
    app_module.store.upsert('quotes', quote)
    return quote


def test_accepting_quote_does_not_auto_complete_after_event_steps(client, flask_app):
    import app as app_module

    lead = _make_lead(app_module)
    quote = _make_quote(app_module, lead['id'])

    resp = client.post(f"/quotes/{quote['id']}/accept", json={})
    assert resp.status_code == 200

    updated_lead = app_module.get_lead(lead['id'])
    job_id = updated_lead.get('converted_to_job')
    assert job_id, 'aceptar la cotizacion deberia haber creado un job'

    instances = app_module.workflow_engine.list_instances(subject_id=job_id, subject_type='job')
    assert instances, 'deberia haber una instancia de PRODUCTION_WORKFLOW para el job'
    instance = instances[0]

    assert instance.step_states.get('job_accepted') == app_module.StepStatus.DONE

    for step_id in ('reserva_confirmada', 'firma_contrato', 'cuestionario_cliente', 'envio_galeria', 'pedir_review'):
        state = instance.step_states.get(step_id)
        assert state != app_module.StepStatus.DONE, (
            f"{step_id} no deberia marcarse DONE solo por aceptar la cotizacion -- "
            f"nadie lo disparo de verdad (estado actual: {state})"
        )


def test_offset_minutes_is_never_zero_for_after_event_steps():
    from src.workflow.models import Step, DueDate, ActionType

    step = Step(
        id='cuestionario_cliente',
        name='Cuestionario cliente',
        action_type=ActionType.SEND_QUESTIONNAIRE,
        due_date=DueDate(mode='after_event', amount=1, unit='months', relative_to='before_boda'),
    )
    assert step.offset_minutes > 0
