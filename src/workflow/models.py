"""
Workflow models - data classes para el engine.

ARQUITECTURA (estilo Studio Ninja):
  - Cada workflow es una lista de TASKS
  - Cada TASK tiene 3 partes independientes:
    1. ACTION: que hace (send_email, send_contract, send_questionnaire, change_status, etc.)
    2. EMAIL TEMPLATE: que email se envia (referencia a email_templates.json)
    3. DUE DATE: cuando se dispara (auto, manual, o after X days/hours/months after Y trigger)
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any, List
import json


class StepStatus(Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class WorkflowStatus(Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class TriggerType(Enum):
    LEAD_CREATED = "lead.created"
    QUOTE_SENT = "quote.sent"
    QUOTE_ACCEPTED = "quote.accepted"
    CONTRACT_SENT = "contract.sent"
    CONTRACT_SIGNED = "contract.signed"
    INVOICE_PAID = "invoice.paid"
    JOB_CREATED = "job.created"
    BODA_DATE = "wedding.date"
    BODA_PASSED = "wedding.passed"
    SCHEDULED = "scheduled"


class ActionType(Enum):
    SEND_EMAIL = "send_email"
    SEND_CONTRACT = "send_contract"
    SEND_QUESTIONNAIRE = "send_questionnaire"
    SEND_INVOICE = "send_invoice"
    SEND_GALLERY = "send_gallery"
    SEND_WHATSAPP = "send_whatsapp"
    CREATE_TASK = "create_task"
    CHANGE_STATUS = "change_status"
    NOTIFY_OWNER = "notify_owner"
    LINK_JOB = "link_job"
    ARCHIVE = "archive"
    NOOP = "noop"


@dataclass
class DueDate:
    """Cuando se dispara el step (3 modos):
    - 'manual': tick manual del usuario
    - 'after_creation': X tiempo despues de crear el subject (lead o job)
    - 'after_event': X tiempo antes/despues del evento (boda)
    """
    mode: str = "manual"  # 'manual' | 'after_creation' | 'after_event'
    amount: int = 0
    unit: str = "days"  # 'minutes' | 'hours' | 'days' | 'weeks' | 'months'
    # 'after' (creation): 'lead_created', 'job_created', etc.
    # 'after_event' (boda): 'before_boda' o 'after_boda'
    relative_to: str = "lead_created"  # 'lead_created' | 'job_created' | 'before_boda' | 'after_boda'

    def to_dict(self) -> Dict[str, Any]:
        return {
            'mode': self.mode,
            'amount': self.amount,
            'unit': self.unit,
            'relative_to': self.relative_to,
        }


@dataclass
class Step:
    id: str
    name: str
    description: str = ""
    # 3 partes (estilo Studio Ninja)
    action_type: ActionType = ActionType.NOOP
    email_template_id: Optional[str] = None  # FK a email_templates.json
    due_date: DueDate = field(default_factory=DueDate)
    # Estado
    status: StepStatus = StepStatus.PENDING
    executed_at: Optional[datetime] = None
    result: Optional[str] = None

    @property
    def delay_display(self) -> str:
        if self.due_date.mode == 'manual':
            return 'Manual'
        elif self.due_date.mode == 'after_creation':
            return f"{self.due_date.amount} {self.due_date.unit} after {self.due_date.relative_to}"
        elif self.due_date.mode == 'after_event':
            when = 'before' if self.due_date.relative_to == 'before_boda' else 'after'
            return f"{self.due_date.amount} {self.due_date.unit} {when} boda"
        return 'Manual'

    @property
    def offset_minutes(self) -> int:
        """Para compatibilidad con el scheduler. Solo 'manual' devuelve 0 --
        'after_event' no tiene aqui la fecha real de la boda para calcular un
        offset exacto, pero NUNCA debe reportar 0 (eso lo confundiria con un
        step manual/inmediato y el engine lo ejecutaria solo al arrancar el
        workflow, sin que nadie lo haya disparado de verdad)."""
        mult = {
            'minutes': 1, 'hours': 60, 'days': 60*24,
            'weeks': 60*24*7, 'months': 60*24*30
        }.get(self.due_date.unit, 60*24)
        if self.due_date.mode == 'after_creation':
            return self.due_date.amount * mult
        if self.due_date.mode == 'after_event':
            return max(self.due_date.amount, 1) * mult
        return 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'action_type': self.action_type.value,
            'email_template_id': self.email_template_id,
            'due_date': self.due_date.to_dict(),
            'delay_display': self.delay_display,
            'status': self.status.value,
            'executed_at': self.executed_at.isoformat() if self.executed_at else None,
            'result': self.result,
        }


@dataclass
class Workflow:
    id: str
    name: str
    description: str = ""
    trigger: TriggerType = TriggerType.LEAD_CREATED
    steps: List[Step] = field(default_factory=list)
    is_template: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'trigger_type': self.trigger.value,
            'steps': [s.to_dict() for s in self.steps],
            'is_template': self.is_template,
        }


@dataclass
class WorkflowInstance:
    id: str
    workflow_id: str
    subject_type: str
    subject_id: str
    subject_name: str = ""
    trigger_event: str = ""
    trigger_at: datetime = field(default_factory=datetime.now)
    status: WorkflowStatus = WorkflowStatus.ACTIVE
    current_step_id: Optional[str] = None
    step_states: Dict[str, StepStatus] = field(default_factory=dict)
    step_results: Dict[str, str] = field(default_factory=dict)
    notes: str = ""

    def progress(self) -> Dict[str, Any]:
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