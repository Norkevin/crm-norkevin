"""
Workflow Engine - el corazon del sistema.

Responsabilidades:
  1. Aplicar un Workflow template a un subject (lead/job/cliente) -> crea WorkflowInstance
  2. Recibir eventos (lead.created, quote.accepted, etc.)
  3. Evaluar que steps estan READY para ejecutar (delay cumplido)
  4. Ejecutar las acciones de cada step
  5. Trackear el progreso

Uso:
  engine = WorkflowEngine()

  # 1. Crear un lead dispara el LEAD_WORKFLOW
  instance = engine.start_workflow(
      workflow=LEAD_WORKFLOW(),
      subject_type='lead',
      subject_id='abc123',
      subject_name='Maria Lopez',
      trigger_event='lead.created',
  )

  # 2. Cuando se acepta el quote, dispara PRODUCTION_WORKFLOW
  production = engine.start_workflow(
      workflow=PRODUCTION_WORKFLOW(),
      subject_type='job',
      subject_id='job456',
      subject_name='Boda Maria Lopez',
      trigger_event='quote.accepted',
  )

  # 3. Revisar que hacer HOY
  due_steps = engine.get_due_steps(datetime.now())
  for instance_id, step in due_steps:
      engine.execute_step(instance_id, step.id)
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
import uuid
import json

from .models import (
    Workflow, WorkflowInstance, Step, Trigger, Action,
    TriggerType, ActionType, StepStatus, WorkflowStatus,
)


class WorkflowEngine:
    """Motor que ejecuta los workflows automaticamente."""

    def __init__(self):
        # Almacenamiento en memoria (se puede mover a Notion/DB despues)
        self.instances: Dict[str, WorkflowInstance] = {}
        self.history: List[Dict[str, Any]] = []  # log de todas las acciones
        # Templates disponibles por id
        self.templates: Dict[str, Workflow] = {}

    # ============================================================
    # TEMPLATES
    # ============================================================
    def register_template(self, workflow: Workflow):
        """Registra un template para que pueda ser aplicado."""
        self.templates[workflow.id] = workflow

    def get_template(self, template_id: str) -> Optional[Workflow]:
        return self.templates.get(template_id)

    def list_templates(self) -> List[Workflow]:
        return list(self.templates.values())

    # ============================================================
    # WORKFLOW INSTANCES
    # ============================================================
    def start_workflow(
        self,
        workflow: Workflow,
        subject_type: str,
        subject_id: str,
        subject_name: str = '',
        trigger_event: str = '',
        trigger_at: Optional[datetime] = None,
        auto_execute_first: bool = True,
    ) -> WorkflowInstance:
        """Aplica un workflow a un subject (lead, job, cliente)."""
        if trigger_at is None:
            trigger_at = datetime.now()

        instance_id = f"wi_{uuid.uuid4().hex[:8]}"
        instance = WorkflowInstance(
            id=instance_id,
            workflow_id=workflow.id,
            subject_type=subject_type,
            subject_id=subject_id,
            subject_name=subject_name,
            trigger_event=trigger_event,
            trigger_at=trigger_at,
        )

        # Inicializar el estado de cada step como PENDING
        for step in workflow.steps:
            instance.step_states[step.id] = StepStatus.PENDING

        # Primer step = el inmediatamente despues del trigger (delay 0)
        first_step = self._find_first_ready_step(workflow, instance, trigger_at)
        if first_step:
            instance.current_step_id = first_step.id

        self.instances[instance_id] = instance

        self._log(instance, 'workflow.started', f'Workflow {workflow.name} aplicado a {subject_name}')

        # Auto-ejecutar el primer step si tiene delay 0
        if auto_execute_first and first_step and first_step.trigger.offset_minutes == 0:
            self.execute_step(instance_id, first_step.id)

        return instance

    def get_instance(self, instance_id: str) -> Optional[WorkflowInstance]:
        return self.instances.get(instance_id)

    def list_instances(
        self,
        subject_type: Optional[str] = None,
        subject_id: Optional[str] = None,
        status: Optional[WorkflowStatus] = None,
    ) -> List[WorkflowInstance]:
        """Lista instancias con filtros opcionales."""
        results = list(self.instances.values())
        if subject_type:
            results = [i for i in results if i.subject_type == subject_type]
        if subject_id:
            results = [i for i in results if i.subject_id == subject_id]
        if status:
            results = [i for i in results if i.status == status]
        return results

    # ============================================================
    # SCHEDULING
    # ============================================================
    def _find_first_ready_step(
        self,
        workflow: Workflow,
        instance: WorkflowInstance,
        trigger_at: datetime,
    ) -> Optional[Step]:
        """Encuentra el primer step que debe ejecutarse (delay minimo)."""
        ready = [s for s in workflow.steps if s.trigger.offset_minutes == 0]
        return ready[0] if ready else workflow.steps[0] if workflow.steps else None

    def _step_scheduled_time(
        self,
        step: Step,
        trigger_at: datetime,
    ) -> datetime:
        """Calcula la fecha/hora en que el step debe ejecutarse."""
        return trigger_at + timedelta(minutes=step.trigger.offset_minutes)

    def get_due_steps(self, now: Optional[datetime] = None) -> List[Tuple[WorkflowInstance, Step]]:
        """Retorna (instance, step) para cada step cuyo delay ya se cumplio y esta pendiente."""
        if now is None:
            now = datetime.now()

        due = []
        for instance in self.instances.values():
            if instance.status != WorkflowStatus.ACTIVE:
                continue
            template = self.templates.get(instance.workflow_id)
            if not template:
                continue

            for step in template.steps:
                state = instance.step_states.get(step.id, StepStatus.PENDING)
                if state != StepStatus.PENDING:
                    continue

                # Calcular tiempo programado
                scheduled = self._step_scheduled_time(step, instance.trigger_at)

                # Listo si scheduled <= now
                if scheduled <= now:
                    # Marcar como READY
                    instance.step_states[step.id] = StepStatus.READY
                    due.append((instance, step))

        return due

    def get_next_pending_step(self, instance: WorkflowInstance) -> Optional[Step]:
        """Retorna el siguiente step pendiente de una instancia."""
        template = self.templates.get(instance.workflow_id)
        if not template:
            return None
        for step in template.steps:
            state = instance.step_states.get(step.id, StepStatus.PENDING)
            if state in (StepStatus.PENDING, StepStatus.READY):
                return step
        return None

    # ============================================================
    # EXECUTION
    # ============================================================
    def execute_step(self, instance_id: str, step_id: str) -> bool:
        """Ejecuta un step especifico de una instancia."""
        instance = self.instances.get(instance_id)
        if not instance:
            return False

        template = self.templates.get(instance.workflow_id)
        if not template:
            return False

        step = next((s for s in template.steps if s.id == step_id), None)
        if not step:
            return False

        # Marcar como RUNNING
        instance.step_states[step_id] = StepStatus.RUNNING

        try:
            result = self._execute_action(step.action, instance)
            instance.step_states[step_id] = StepStatus.DONE
            instance.step_results[step_id] = result
            step.executed_at = datetime.now()
            step.result = result
            self._log(instance, 'step.done', f'{step.name}: {result}')

            # Si era LINK_JOB (quote.accepted), disparar PRODUCTION_WORKFLOW automaticamente
            if step.action.type == ActionType.LINK_JOB:
                self._trigger_production_after_accept(instance)

            # Avanzar al siguiente step
            next_step = self.get_next_pending_step(instance)
            instance.current_step_id = next_step.id if next_step else None

            # Si no hay mas steps, completar el workflow
            if not next_step:
                instance.status = WorkflowStatus.COMPLETED
                self._log(instance, 'workflow.completed', f'Workflow {template.name} completado')

            return True
        except Exception as e:
            instance.step_states[step_id] = StepStatus.FAILED
            instance.step_results[step_id] = f'Error: {str(e)}'
            self._log(instance, 'step.failed', f'{step.name}: {str(e)}')
            return False

    def _execute_action(self, action: Action, instance: WorkflowInstance) -> str:
        """Ejecuta la accion de un step. Aqui se integraria con email/Notion/etc."""
        # En esta primera version, solo LOGGEAMOS que accion se ejecutaria
        # La integracion real con email/Notion se hace en actions.py
        timestamp = datetime.now().isoformat()

        if action.type == ActionType.SEND_EMAIL:
            return f"EMAIL sent: '{action.params.get('asunto', 'No subject')}' at {timestamp}"

        elif action.type == ActionType.SEND_WHATSAPP:
            return f"WHATSAPP sent at {timestamp}"

        elif action.type == ActionType.CREATE_TASK:
            return f"TASK created: {action.params} at {timestamp}"

        elif action.type == ActionType.CHANGE_STATUS:
            new_status = action.params.get('new_status', 'Updated')
            return f"STATUS changed to '{new_status}' at {timestamp}"

        elif action.type == ActionType.NOTIFY_OWNER:
            return f"OWNER notified: {action.params.get('mensaje', '')} at {timestamp}"

        elif action.type == ActionType.LINK_JOB:
            # ESTE ES EL PASO MAGICO: convierte lead -> job
            return f"JOB created from lead {instance.subject_id} at {timestamp}"

        elif action.type == ActionType.ARCHIVE:
            return f"ARCHIVED at {timestamp}"

        else:  # NOOP
            return f"Marker placed at {timestamp}"

    def _trigger_production_after_accept(self, instance: WorkflowInstance):
        """Cuando un lead acepta el quote, dispara el PRODUCTION_WORKFLOW automaticamente."""
        from .templates import PRODUCTION_WORKFLOW

        # Registrar el template de produccion si no existe
        if 'production_workflow_v1' not in self.templates:
            self.register_template(PRODUCTION_WORKFLOW())

        # Crear nueva instancia para el JOB
        production_workflow = self.get_template('production_workflow_v1')
        new_instance = self.start_workflow(
            workflow=production_workflow,
            subject_type='job',
            subject_id=instance.subject_id,  # mismo id que el lead (se mapea en Notion)
            subject_name=f"Job de {instance.subject_name}",
            trigger_event='quote.accepted',
        )
        self._log(instance, 'workflow.cascaded', f'PRODUCTION_WORKFLOW disparado automaticamente: {new_instance.id}')

    # ============================================================
    # HISTORY
    # ============================================================
    def _log(self, instance: WorkflowInstance, event: str, message: str):
        entry = {
            'timestamp': datetime.now().isoformat(),
            'instance_id': instance.id,
            'subject': f"{instance.subject_type}:{instance.subject_name}",
            'event': event,
            'message': message,
        }
        self.history.append(entry)

    def get_history(self, instance_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Retorna el historial de eventos."""
        h = self.history
        if instance_id:
            h = [e for e in h if e['instance_id'] == instance_id]
        return h[-limit:]

    # ============================================================
    # STATS
    # ============================================================
    def stats(self) -> Dict[str, Any]:
        """Estadisticas globales."""
        total = len(self.instances)
        by_status = {}
        for inst in self.instances.values():
            by_status[inst.status.value] = by_status.get(inst.status.value, 0) + 1
        return {
            'total_instances': total,
            'by_status': by_status,
            'templates': list(self.templates.keys()),
            'total_history': len(self.history),
        }