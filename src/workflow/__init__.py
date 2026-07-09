# Workflow Engine para Norkevin CRM
# Inspirado en Studio Ninja: workflows automaticos con triggers y acciones

from .models import Workflow, Step, Trigger, Action, StepStatus, WorkflowStatus
from .engine import WorkflowEngine
from .templates import LEAD_WORKFLOW, PRODUCTION_WORKFLOW, BODAS_NORKEVIN_TEMPLATE

__all__ = [
    'Workflow',
    'Step',
    'Trigger',
    'Action',
    'StepStatus',
    'WorkflowStatus',
    'WorkflowEngine',
    'LEAD_WORKFLOW',
    'PRODUCTION_WORKFLOW',
    'BODAS_NORKEVIN_TEMPLATE',
]