"""
Workflow models - data classes para el engine.

Studio Ninja concept: cada workflow es una secuencia de steps.
Cada step tiene un trigger (que lo activa) y una accion (que hace cuando se cumple el delay).

Ejemplo:
  Trigger: Lead created
    Step 1: Enviar email de bienvenida
      Delay: 0 (inmediato)
      Accion: SEND_EMAIL ('Bienvenida', 'Gracias por contactarnos')
    Step 2: Envio de paquetes
      Delay: 3 horas
      Accion: SEND_EMAIL ('Paquetes', 'Adjunto nuestros paquetes')
    Step 3: Seguimiento cliente
      Delay: 7 dias
      Accion: SEND_EMAIL ('Seguimiento', 'Como va la decision?')
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any, List
import json


class StepStatus(Enum):
    """Estado de cada step individual dentro de un workflow instance."""
    PENDING = "pending"          # todavia no se cumplio el delay
    READY = "ready"              # delay cumplido, listo para ejecutar
    RUNNING = "running"          # ejecutandose ahora
    DONE = "done"                # ejecutado OK
    SKIPPED = "skipped"          # saltado (ej: no cumple condicion)
    FAILED = "failed"            # fallo la ejecucion


class WorkflowStatus(Enum):
    """Estado global del workflow instance."""
    ACTIVE = "active"            # ejecutandose normalmente
    COMPLETED = "completed"      # todos los steps terminaron
    PAUSED = "paused"            # pausado por usuario
    CANCELLED = "cancelled"      # cancelado


class TriggerType(Enum):
    """Que evento inicia este step o workflow."""
    LEAD_CREATED = "lead.created"
    QUOTE_SENT = "quote.sent"
    QUOTE_ACCEPTED = "quote.accepted"        # <-- ESTE es el que pasa lead -> job
    CONTRACT_SENT = "contract.sent"
    CONTRACT_SIGNED = "contract.signed"
    INVOICE_PAID = "invoice.paid"
    JOB_CREATED = "job.created"
    BODA_DATE = "wedding.date"
    BODA_PASSED = "wedding.passed"
    SCHEDULED = "scheduled"                  # ejecutar en una fecha exacta


class ActionType(Enum):
    """Que accion ejecuta el step."""
    SEND_EMAIL = "send_email"
    SEND_WHATSAPP = "send_whatsapp"
    CREATE_TASK = "create_task"
    CHANGE_STATUS = "change_status"
    NOTIFY_OWNER = "notify_owner"
    LINK_JOB = "link_job"                   # asociar a un job existente
    ARCHIVE = "archive"
    NOOP = "noop"                            # marcador (sin accion)


@dataclass
class Trigger:
    """Cuando se dispara este step."""
    type: TriggerType
    offset_minutes: int = 0                  # delay desde el evento (3h, 7d, etc.)
    condition: Optional[Dict[str, Any]] = None  # filtro adicional (ej: lead.fuente == 'Instagram')


@dataclass
class Action:
    """Que hace cuando el step se ejecuta."""
    type: ActionType
    template: Optional[str] = None           # template del mensaje
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Step:
    """Un paso individual en el workflow."""
    id: str
    name: str
    description: str = ""
    trigger: Trigger = field(default_factory=lambda: Trigger(TriggerType.SCHEDULED))
    action: Action = field(default_factory=lambda: Action(ActionType.NOOP))
    status: StepStatus = StepStatus.PENDING
    executed_at: Optional[datetime] = None
    result: Optional[str] = None

    @property
    def delay_display(self) -> str:
        """Display humano del delay (ej: '3 horas', '7 dias')."""
        minutes = self.trigger.offset_minutes
        if minutes < 60:
            return f"{minutes} min"
        elif minutes < 1440:
            return f"{minutes // 60} hora{'s' if minutes > 60 else ''}"
        elif minutes < 43200:
            days = minutes // 1440
            return f"{days} dia{'s' if days > 1 else ''}"
        else:
            months = minutes // 43200
            return f"{months} mes{'es' if months > 1 else ''}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'trigger_type': self.trigger.type.value,
            'offset_minutes': self.trigger.offset_minutes,
            'delay_display': self.delay_display,
            'action_type': self.action.type.value,
            'action_template': self.action.template,
            'action_params': self.action.params,
            'status': self.status.value,
            'executed_at': self.executed_at.isoformat() if self.executed_at else None,
            'result': self.result,
        }


@dataclass
class Workflow:
    """
    Un workflow completo (template) con sus steps.

    Ejemplo: LEAD_WORKFLOW con 5 steps.
    """
    id: str
    name: str
    description: str = ""
    trigger: Trigger = field(default_factory=lambda: Trigger(TriggerType.LEAD_CREATED))
    steps: List[Step] = field(default_factory=list)
    is_template: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'trigger_type': self.trigger.type.value,
            'steps': [s.to_dict() for s in self.steps],
            'is_template': self.is_template,
        }


@dataclass
class WorkflowInstance:
    """
    Instancia activa de un workflow (un workflow aplicado a un lead o job especifico).

    Ejemplo: el LEAD_WORKFLOW aplicado al lead de 'Maria Lopez'.
    """
    id: str
    workflow_id: str                          # cual template esta siguiendo
    subject_type: str                          # 'lead' | 'job' | 'cliente'
    subject_id: str                            # id del lead/job en Notion
    subject_name: str = ""                     # para UI
    trigger_event: str = ""                    # que evento lo disparo
    trigger_at: datetime = field(default_factory=datetime.now)
    status: WorkflowStatus = WorkflowStatus.ACTIVE
    current_step_id: Optional[str] = None      # siguiente step a ejecutar
    step_states: Dict[str, StepStatus] = field(default_factory=dict)
    step_results: Dict[str, str] = field(default_factory=dict)
    notes: str = ""                            # notas adicionales

    def progress(self) -> Dict[str, Any]:
        """Calcula el progreso (X de Y steps completados)."""
        total = len(self.step_states)
        done = sum(1 for s in self.step_states.values() if s == StepStatus.DONE)
        return {'total': total, 'done': done, 'percent': round(done * 100 / total) if total else 0}

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'workflow_id': self.workflow_id,
            'subject_type': self.subject_type,
            'subject_id': self.subject_id,
            'subject_name': self.subject_name,
            'trigger_event': self.trigger_event,
            'trigger_at': self.trigger_at.isoformat(),
            'status': self.status.value,
            'current_step_id': self.current_step_id,
            'step_states': {k: v.value for k, v in self.step_states.items()},
            'step_results': self.step_results,
            'notes': self.notes,
            'progress': self.progress(),
        }