"""
Workflow templates - los workflows pre-configurados.

ARQUITECTURA NUEVA (estilo Studio Ninja):
  - Cada step tiene 3 partes: ACTION + EMAIL_TEMPLATE + DUE_DATE
  - EMAIL_TEMPLATE referencia un ID de email_templates.json
  - DUE_DATE configura cuando se dispara el step
"""
from .models import Workflow, Step, DueDate, ActionType, TriggerType


def LEAD_WORKFLOW() -> Workflow:
    """Workflow que se aplica cuando se crea un LEAD."""
    return Workflow(
        id='lead_workflow_v1',
        name='Lead Follow-up',
        description='Secuencia automatica de emails de seguimiento',
        trigger=TriggerType.LEAD_CREATED,
        is_template=True,
        steps=[
            Step(
                id='envio_paquetes',
                name='Envio de paquetes',
                description='Email automatico con nuestros paquetes de bodas',
                action_type=ActionType.SEND_EMAIL,
                email_template_id='tpl-paquetes',
                due_date=DueDate(mode='after_creation', amount=3, unit='hours', relative_to='lead_created'),
            ),
            Step(
                id='seguimiento_cliente',
                name='Seguimiento cliente',
                description='Email de seguimiento',
                action_type=ActionType.SEND_EMAIL,
                email_template_id='tpl-seguimiento',
                due_date=DueDate(mode='after_creation', amount=7, unit='days', relative_to='lead_created'),
            ),
            Step(
                id='levanta_muertos',
                name='Levanta muertos',
                description='Email para re-enganchar leads sin respuesta',
                action_type=ActionType.SEND_EMAIL,
                email_template_id='tpl-levanta-muertos',
                due_date=DueDate(mode='after_creation', amount=30, unit='days', relative_to='lead_created'),
            ),
            Step(
                id='seguimiento_final',
                name='Seguimiento final',
                description='Ultimo intento de cierre',
                action_type=ActionType.SEND_EMAIL,
                email_template_id='tpl-levanta-muertos',  # reusamos template
                due_date=DueDate(mode='after_creation', amount=90, unit='days', relative_to='lead_created'),
            ),
        ],
    )


def PRODUCTION_WORKFLOW() -> Workflow:
    """Workflow que se aplica cuando se ACEPTA el quote (lead -> job)."""
    return Workflow(
        id='production_workflow_v1',
        name='Production',
        description='Workflow completo desde aceptar el quote hasta despues de la boda',
        trigger=TriggerType.QUOTE_ACCEPTED,
        is_template=True,
        steps=[
            Step(
                id='job_accepted',
                name='Job accepted',
                description='Crea el job desde el lead',
                action_type=ActionType.LINK_JOB,
                email_template_id=None,
                due_date=DueDate(mode='manual', amount=0, unit='days'),
            ),
            Step(
                id='reserva_confirmada',
                name='Reserva confirmada',
                description='Email de bienvenida y reserva',
                action_type=ActionType.SEND_EMAIL,
                email_template_id='tpl-reserva',
                due_date=DueDate(mode='after_creation', amount=1, unit='days', relative_to='job_created'),
            ),
            Step(
                id='firma_contrato',
                name='Firma de contrato',
                description='Enviar contrato para firma',
                action_type=ActionType.SEND_CONTRACT,
                email_template_id='tpl-contrato',
                due_date=DueDate(mode='after_creation', amount=3, unit='days', relative_to='job_created'),
            ),
            Step(
                id='cuestionario_cliente',
                name='Cuestionario cliente',
                description='Cuestionario pre-boda',
                action_type=ActionType.SEND_QUESTIONNAIRE,
                email_template_id='tpl-cuestionario',
                due_date=DueDate(mode='after_event', amount=1, unit='months', relative_to='before_boda'),
            ),
            Step(
                id='envio_galeria',
                name='Envio de galeria',
                description='Enviar galeria de fotos al cliente',
                action_type=ActionType.SEND_GALLERY,
                email_template_id='tpl-galeria',
                due_date=DueDate(mode='after_event', amount=2, unit='months', relative_to='after_boda'),
            ),
            Step(
                id='pedir_review',
                name='Pedir review Google',
                description='Pedir review en Google',
                action_type=ActionType.SEND_EMAIL,
                email_template_id='tpl-review',
                due_date=DueDate(mode='after_event', amount=1, unit='months', relative_to='after_boda'),
            ),
            Step(
                id='job_complete',
                name='Job complete',
                description='Cambia el status del job a Listo',
                action_type=ActionType.CHANGE_STATUS,
                email_template_id=None,
                due_date=DueDate(mode='manual', amount=0, unit='days'),
            ),
        ],
    )


def BODAS_NORKEVIN_TEMPLATE() -> dict:
    return {
        'name': 'BODAS NORKEVIN',
        'lead_workflow': 'lead_workflow_v1',
        'production_workflow': 'production_workflow_v1',
        'is_default': True,
    }