# Workflow Engine para Astral CRM
# Inspirado en Studio Ninja: workflows automaticos con triggers y acciones

from .models import Workflow, Step, DueDate, StepStatus, WorkflowStatus, ActionType, TriggerType
from .engine import WorkflowEngine
from .templates import LEAD_WORKFLOW, PRODUCTION_WORKFLOW, BODAS_NORKEVIN_TEMPLATE

__all__ = [
    'Workflow',
    'Step',
    'DueDate',
    'StepStatus',
    'WorkflowStatus',
    'ActionType',
    'TriggerType',
    'WorkflowEngine',
    'LEAD_WORKFLOW',
    'PRODUCTION_WORKFLOW',
    'BODAS_NORKEVIN_TEMPLATE',
]
