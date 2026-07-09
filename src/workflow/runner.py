"""
Demo runner para probar el workflow engine sin Notion.

Este script crea un lead ficticio, le aplica el LEAD_WORKFLOW,
simula que pasa el tiempo, acepta el quote, y deja que el sistema
haga todo el resto automaticamente.

Uso:
  python -m src.workflow.runner
"""
import sys
import time
from datetime import datetime, timedelta

from .engine import WorkflowEngine
from .templates import LEAD_WORKFLOW, PRODUCTION_WORKFLOW
from .models import StepStatus, WorkflowStatus


def print_banner(msg):
    print("\n" + "=" * 70)
    print(f"  {msg}")
    print("=" * 70)


def print_step(step_id, name, delay_display, status):
    icon = {
        StepStatus.PENDING.value: 'o',
        StepStatus.READY.value: '~',
        StepStatus.RUNNING.value: '*',
        StepStatus.DONE.value: '[OK]',
        StepStatus.SKIPPED.value: '[--]',
        StepStatus.FAILED.value: '[XX]',
    }.get(status, '?')
    print(f"    {icon} {name:<35} ({delay_display:>12})  [{status}]")


def main():
    print_banner("WORKFLOW ENGINE DEMO - Kevin, esto es tu CRM Studio Ninja style")

    # Crear engine
    engine = WorkflowEngine()
    engine.register_template(LEAD_WORKFLOW())
    engine.register_template(PRODUCTION_WORKFLOW())

    print("\n[1] Templates cargados:")
    for tmpl in engine.list_templates():
        print(f"     - {tmpl.name} ({len(tmpl.steps)} steps)")

    # Crear un lead ficticio
    print_banner("[2] LEAD CREATED: Maria Lopez")
    print("    Simulando que Kevin capturo un lead desde Instagram...")
    lead_instance = engine.start_workflow(
        workflow=LEAD_WORKFLOW(),
        subject_type='lead',
        subject_id='lead_maria_001',
        subject_name='Maria Lopez',
        trigger_event='lead.created',
    )

    print(f"\n    Instance ID: {lead_instance.id}")
    print(f"    Subject: {lead_instance.subject_type} '{lead_instance.subject_name}'")
    print(f"    Workflow: {lead_instance.workflow_id}")
    print(f"\n    Lead Workflow Progress:")
    template = engine.get_template(lead_instance.workflow_id)
    for step in template.steps:
        state = lead_instance.step_states.get(step.id, StepStatus.PENDING).value
        print_step(step.id, step.name, step.delay_display, state)

    # Avanzar el reloj 3 horas
    print_banner("[3] Avanzamos el reloj +3 horas")
    future = datetime.now() + timedelta(hours=3)
    due = engine.get_due_steps(future)
    print(f"    Steps que ya deberian ejecutarse: {len(due)}")
    for instance, step in due:
        print(f"     - {instance.subject_name} -> {step.name} ({step.delay_display})")
    print(f"\n    Ejecutandolos...")
    for instance, step in due:
        engine.execute_step(instance.id, step.id)
    print(f"\n    Despues de la ejecucion:")
    template = engine.get_template(lead_instance.workflow_id)
    for step in template.steps:
        state = lead_instance.step_states.get(step.id, StepStatus.PENDING).value
        print_step(step.id, step.name, step.delay_display, state)

    # Avanzar hasta 7 dias (se ejecuta el 3er step)
    print_banner("[4] Avanzamos el reloj +7 dias (mas all)")
    future = datetime.now() + timedelta(days=8)
    due = engine.get_due_steps(future)
    print(f"    Steps pendientes: {len(due)}")
    for instance, step in due:
        engine.execute_step(instance.id, step.id)
    print(f"\n    Estado actual:")
    for step in template.steps:
        state = lead_instance.step_states.get(step.id, StepStatus.PENDING).value
        print_step(step.id, step.name, step.delay_display, state)

    # === EL MAGICO: QUOTE ACCEPTED ===
    print_banner("[5] *** QUOTE ACCEPTED *** (El momento magico: Lead -> Job)")
    print("    Maria acepto el paquete premium!")
    print("    -> Esto dispara AUTOMATICAMENTE el PRODUCTION_WORKFLOW")

    # Buscar el step 'job_accepted' que esta PENDING
    # Pero para simplificar, lo marcamos como DONE y disparamos PRODUCTION
    # (en realidad seria cuando el cliente firma el quote en el sistema)

    # El engine lo hace automaticamente al ejecutar un step LINK_JOB
    # Asi que forzamos la ejecucion del primer step de PRODUCTION simulando que
    # el LINK_JOB ya paso

    # Forma realista: crear una nueva instancia de PRODUCTION
    job_instance = engine.start_workflow(
        workflow=PRODUCTION_WORKFLOW(),
        subject_type='job',
        subject_id='job_maria_001',
        subject_name='Boda Maria Lopez & Carlos',
        trigger_event='quote.accepted',
    )

    print(f"\n    Job Instance ID: {job_instance.id}")
    print(f"\n    Production Workflow recien iniciado (todos los steps PENDING):")
    template = engine.get_template(job_instance.workflow_id)
    for step in template.steps:
        state = job_instance.step_states.get(step.id, StepStatus.PENDING).value
        print_step(step.id, step.name, step.delay_display, state)

    # Avanzar mucho tiempo (todo se ejecuta)
    print_banner("[6] Avanzamos el reloj +200 dias (todo el wedding journey)")
    future = datetime.now() + timedelta(days=200)
    due = engine.get_due_steps(future)
    print(f"    Total steps que se ejecutan: {len(due)}")
    for instance, step in due:
        engine.execute_step(instance.id, step.id)

    # Mostrar estado final
    print_banner("[7] ESTADO FINAL")
    print("\n    LEAD WORKFLOW (Maria Lopez - Instagram lead):")
    template = engine.get_template(lead_instance.workflow_id)
    for step in template.steps:
        state = lead_instance.step_states.get(step.id, StepStatus.PENDING).value
        result = lead_instance.step_results.get(step.id, '')
        print_step(step.id, step.name, step.delay_display, state)
        if result:
            print(f"        => {result[:100]}")

    print(f"\n    Progress: {lead_instance.progress()}")

    print("\n    PRODUCTION WORKFLOW (Boda Maria & Carlos):")
    template = engine.get_template(job_instance.workflow_id)
    for step in template.steps:
        state = job_instance.step_states.get(step.id, StepStatus.PENDING).value
        result = job_instance.step_results.get(step.id, '')
        print_step(step.id, step.name, step.delay_display, state)
        if result:
            print(f"        => {result[:100]}")

    print(f"\n    Progress: {job_instance.progress()}")
    print(f"    Status: {job_instance.status.value}")

    # Stats globales
    print_banner("[8] STATS GLOBALES DEL ENGINE")
    stats = engine.stats()
    print(json.dumps(stats, indent=2))

    # History
    print_banner("[9] ULTIMOS 10 EVENTOS DEL HISTORIAL")
    for entry in engine.get_history(limit=10):
        print(f"    [{entry['timestamp'][:19]}] {entry['event']:<20} {entry['message'][:60]}")

    print_banner("DEMO COMPLETADO")
    print("""
    Esto es lo que Studio Ninja hace automaticamente:

    1. Crear un LEAD -> dispara LEAD_WORKFLOW
       -> Email de bienvenida (3h)
       -> Email de paquetes (3h)
       -> Seguimiento cliente (7d)
       -> Levanta muertos (30d)
       -> Seguimiento final (90d)

    2. Cliente ACEPTA QUOTE -> dispara PRODUCTION_WORKFLOW automaticamente
       -> Crea JOB desde el lead
       -> Email reserva confirmada (1d)
       -> Contrato para firma (3d)
       -> Pago de reserva (7d)
       -> Cuestionario cliente (60d antes boda)
       -> Boda (90d)
       -> Pedir Google review (150d)
       -> Job complete (180d)

    El motor evalua cada hora/dia que pasos toca ejecutar y los dispara.
    """)


if __name__ == '__main__':
    import json
    main()