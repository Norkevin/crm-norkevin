"""
Workflow Engine - el corazon del sistema.

Responsabilidades:
  1. Aplicar un Workflow template a un subject (lead/job/cliente)
  2. Recibir eventos (lead.created, quote.accepted, etc.)
  3. Evaluar que steps estan READY para ejecutar (delay cumplido)
  4. Ejecutar las acciones de cada step
  5. Trackear el progreso

ARQUITECTURA:
  - Step tiene action_type + email_template_id + due_date
  - Email template es una REFERENCIA (FK) a email_templates.json
  - Due date tiene mode (manual / after_creation / after_event)
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
import uuid

from .models import (
    Workflow, WorkflowInstance, Step, DueDate,
    ActionType, StepStatus, WorkflowStatus,
)


class WorkflowEngine:
    """Motor que ejecuta los workflows automaticamente."""

    def __init__(self, persistence_store=None):
        self.instances: Dict[str, WorkflowInstance] = {}
        self.history: List[Dict[str, Any]] = []
        self.templates: Dict[str, Workflow] = {}
        self.store = persistence_store
        if self.store:
            self._load_from_storage()

    def register_template(self, workflow: Workflow):
        self.templates[workflow.id] = workflow

    def get_template(self, template_id: str) -> Optional[Workflow]:
        return self.templates.get(template_id)

    def list_templates(self) -> List[Workflow]:
        return list(self.templates.values())

    def start_workflow(
        self,
        workflow: Workflow,
        subject_type: str,
        subject_id: str,
        subject_name: str = '',
        trigger_event: str = '',
        trigger_at: Optional[datetime] = None,
        auto_execute_first: bool = False,
    ) -> WorkflowInstance:
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

        for step in workflow.steps:
            instance.step_states[step.id] = StepStatus.PENDING

        first_step = self._find_first_ready_step(workflow, instance, trigger_at)
        if first_step:
            instance.current_step_id = first_step.id

        self.instances[instance_id] = instance
        self._log(instance, 'workflow.started', f'Workflow {workflow.name} aplicado a {subject_name}')

        # NO auto-ejecutar. Los steps se disparan manualmente.
        if auto_execute_first and first_step and first_step.offset_minutes == 0 and first_step.due_date.mode != 'manual':
            self.execute_step(instance_id, first_step.id)

        self._save_to_storage()
        return instance

    def get_instance(self, instance_id: str) -> Optional[WorkflowInstance]:
        return self.instances.get(instance_id)

    def list_instances(
        self,
        subject_type: Optional[str] = None,
        subject_id: Optional[str] = None,
        status: Optional[WorkflowStatus] = None,
    ) -> List[WorkflowInstance]:
        results = list(self.instances.values())
        if subject_type:
            results = [i for i in results if i.subject_type == subject_type]
        if subject_id:
            results = [i for i in results if i.subject_id == subject_id]
        if status:
            results = [i for i in results if i.status == status]
        return results

    def _find_first_ready_step(
        self,
        workflow: Workflow,
        instance: WorkflowInstance,
        trigger_at: datetime,
    ) -> Optional[Step]:
        """Encuentra el primer step automatico (offset 0) o el primero de la lista."""
        auto = [s for s in workflow.steps if s.offset_minutes == 0 and s.due_date.mode != 'manual']
        if auto:
            return auto[0]
        return workflow.steps[0] if workflow.steps else None

    def _step_scheduled_time(self, step: Step, trigger_at: datetime) -> datetime:
        return trigger_at + timedelta(minutes=step.offset_minutes)

    def get_due_steps(self, now: Optional[datetime] = None):
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
                scheduled = self._step_scheduled_time(step, instance.trigger_at)
                if scheduled <= now and step.due_date.mode != 'manual':
                    instance.step_states[step.id] = StepStatus.READY
                    due.append((instance, step))
        return due

    def get_next_pending_step(self, instance):
        template = self.templates.get(instance.workflow_id)
        if not template:
            return None
        for step in template.steps:
            state = instance.step_states.get(step.id, StepStatus.PENDING)
            if state in (StepStatus.PENDING, StepStatus.READY):
                return step
        return None

    def execute_step(self, instance_id: str, step_id: str) -> bool:
        instance = self.instances.get(instance_id)
        if not instance:
            return False

        template = self.templates.get(instance.workflow_id)
        if not template:
            return False

        step = next((s for s in template.steps if s.id == step_id), None)
        if not step:
            return False

        instance.step_states[step_id] = StepStatus.RUNNING

        try:
            result = self._execute_action(step.action_type, instance, step)
            instance.step_states[step_id] = StepStatus.DONE
            instance.step_results[step_id] = result
            step.executed_at = datetime.now()
            step.result = result
            self._log(instance, 'step.done', f'{step.name}: {result}')

            if step.action_type == ActionType.LINK_JOB:
                self._trigger_production_after_accept(instance)

            next_step = self.get_next_pending_step(instance)
            instance.current_step_id = next_step.id if next_step else None

            if not next_step:
                instance.status = WorkflowStatus.COMPLETED
                self._log(instance, 'workflow.completed', f'Workflow {template.name} completado')

            self._save_to_storage()
            return True
        except Exception as e:
            instance.step_states[step_id] = StepStatus.FAILED
            instance.step_results[step_id] = f'Error: {str(e)}'
            self._log(instance, 'step.failed', f'{step.name}: {str(e)}')
            self._save_to_storage()
            return False

    def _execute_action(self, action_type, instance, step=None) -> str:
        """Ejecuta la accion de un step. Aqui se integraria con email/Notion/etc."""
        timestamp = datetime.now().isoformat()

        # Cargar email template si hay
        template_name = ''
        if step and step.email_template_id and self.store:
            templates = self.store.get_dict('email_templates')
            if not templates:
                templates_list = self.store.list('email_templates')
                tpl = next((t for t in templates_list if t.get('id') == step.email_template_id), None)
                if tpl:
                    template_name = tpl.get('name', step.email_template_id)
            else:
                # Si esta como dict (es un setting-like)
                tpl = templates.get(step.email_template_id) if isinstance(templates, dict) else None
                if tpl:
                    template_name = tpl.get('name', step.email_template_id)

        if action_type == ActionType.SEND_EMAIL:
            return f"EMAIL sent: '{template_name or step.email_template_id or 'No template'}' to lead {instance.subject_id} at {timestamp}"

        elif action_type == ActionType.SEND_CONTRACT:
            return f"CONTRACT sent: '{template_name}' at {timestamp}"

        elif action_type == ActionType.SEND_QUESTIONNAIRE:
            return f"QUESTIONNAIRE sent: '{template_name}' at {timestamp}"

        elif action_type == ActionType.SEND_INVOICE:
            return f"INVOICE sent at {timestamp}"

        elif action_type == ActionType.SEND_GALLERY:
            return f"GALLERY sent: '{template_name}' at {timestamp}"

        elif action_type == ActionType.SEND_WHATSAPP:
            return f"WHATSAPP sent at {timestamp}"

        elif action_type == ActionType.CREATE_TASK:
            return f"TASK created at {timestamp}"

        elif action_type == ActionType.CHANGE_STATUS:
            new_status = 'Updated'
            return f"STATUS changed to '{new_status}' at {timestamp}"

        elif action_type == ActionType.NOTIFY_OWNER:
            return f"OWNER notified at {timestamp}"

        elif action_type == ActionType.LINK_JOB:
            return f"JOB created from lead {instance.subject_id} at {timestamp}"

        elif action_type == ActionType.ARCHIVE:
            return f"ARCHIVED at {timestamp}"

        else:
            return f"Action completed at {timestamp}"

    def _trigger_production_after_accept(self, instance):
        if instance.workflow_id != 'lead_workflow_v1':
            return
        from .templates import PRODUCTION_WORKFLOW
        if 'production_workflow_v1' not in self.templates:
            self.register_template(PRODUCTION_WORKFLOW())
        production_workflow = self.get_template('production_workflow_v1')
        new_instance = self.start_workflow(
            workflow=production_workflow,
            subject_type='job',
            subject_id=instance.subject_id,
            subject_name=instance.subject_name,
            trigger_event='quote.accepted',
        )
        self._log(instance, 'workflow.cascaded', f'PRODUCTION_WORKFLOW disparado: {new_instance.id}')

    def _log(self, instance, event, message):
        entry = {
            'timestamp': datetime.now().isoformat(),
            'instance_id': instance.id,
            'subject': f"{instance.subject_type}:{instance.subject_name}",
            'event': event,
            'message': message,
        }
        self.history.append(entry)

    def get_history(self, instance_id=None, limit=50):
        h = self.history
        if instance_id:
            h = [e for e in h if e['instance_id'] == instance_id]
        return h[-limit:]

    def _save_to_storage(self):
        if not self.store:
            return
        instances_data = {iid: inst.to_dict() for iid, inst in self.instances.items()}
        self.store.save_dict('workflow_instances', instances_data)
        self.store.save_dict('workflow_history', {'history': self.history[-500:]})

    def _load_from_storage(self):
        if not self.store:
            return
        data = self.store.get_dict('workflow_instances')
        if not data:
            return
        for iid, idata in data.items():
            try:
                inst = WorkflowInstance(
                    id=idata['id'],
                    workflow_id=idata['workflow_id'],
                    subject_type=idata['subject_type'],
                    subject_id=idata['subject_id'],
                    subject_name=idata.get('subject_name', ''),
                    trigger_event=idata.get('trigger_event', ''),
                    trigger_at=datetime.fromisoformat(idata['trigger_at']),
                    status=WorkflowStatus(idata['status']),
                    current_step_id=idata.get('current_step_id'),
                    step_states={k: StepStatus(v) for k, v in idata.get('step_states', {}).items()},
                    step_results=idata.get('step_results', {}),
                    notes=idata.get('notes', ''),
                )
                self.instances[iid] = inst
            except Exception as e:
                print(f"Error cargando instance {iid}: {e}")

        hist_data = self.store.get_dict('workflow_history')
        if hist_data and 'history' in hist_data:
            self.history = hist_data['history']

    def stats(self):
        total = len(self.instances)
        by_status = {}
        for inst in self.instances.values():
            by_status[inst.status.value] = by_status.get(inst.status.value, 0)
        return {
            'total_instances': total,
            'by_status': by_status,
            'templates': list(self.templates.keys()),
            'total_history': len(self.history),
        }
