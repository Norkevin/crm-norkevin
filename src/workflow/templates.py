"""
Workflow templates - los workflows pre-configurados.

Para Kevin: arrancamos con 2 templates (lo que el pidio):
  1. LEAD_WORKFLOW - secuencia de emails automaticos despues de crear lead
  2. PRODUCTION_WORKFLOW - secuencia desde que se acepta el quote hasta la boda + post

El trigger clave: QUOTE_ACCEPTED pasa automaticamente al PRODUCTION_WORKFLOW
y crea el Job asociado.
"""
from .models import Workflow, Step, Trigger, Action, TriggerType, ActionType


def LEAD_WORKFLOW() -> Workflow:
    """
    Workflow que se aplica cuando se crea un LEAD.

    Pasos:
      1. Lead created (marcador inmediato)
      2. Envio de paquetes - 3 horas despues
      3. Seguimiento cliente - 7 dias despues
      4. Levanta muertos - 1 mes despues
      5. Seguimiento final - 3 meses despues
    """
    return Workflow(
        id='lead_workflow_v1',
        name='Lead Follow-up',
        description='Secuencia automatica de emails de seguimiento despues de crear un lead',
        trigger=Trigger(type=TriggerType.LEAD_CREATED),
        is_template=True,
        steps=[
            Step(
                id='lead_created',
                name='Lead created',
                description='Marcador: lead registrado en el CRM',
                trigger=Trigger(TriggerType.LEAD_CREATED, offset_minutes=0),
                action=Action(ActionType.NOOP, template='marcador'),
            ),
            Step(
                id='envio_paquetes',
                name='Envio de paquetes',
                description='Email automatico con nuestros paquetes de bodas',
                trigger=Trigger(TriggerType.LEAD_CREATED, offset_minutes=3 * 60),  # 3h
                action=Action(
                    ActionType.SEND_EMAIL,
                    template='envio_paquetes',
                    params={'asunto': 'Nuestros paquetes para tu boda', 'adjuntos': ['paquete_basico.pdf', 'paquete_premium.pdf']},
                ),
            ),
            Step(
                id='seguimiento_cliente',
                name='Seguimiento cliente',
                description='Email para ver si tienen dudas sobre los paquetes',
                trigger=Trigger(TriggerType.LEAD_CREATED, offset_minutes=7 * 24 * 60),  # 7d
                action=Action(
                    ActionType.SEND_EMAIL,
                    template='seguimiento',
                    params={'asunto': 'Tuviste chance de revisar los paquetes?'},
                ),
            ),
            Step(
                id='levanta_muertos',
                name='Levanta muertos',
                description='Email para re-enganchar leads que no respondieron',
                trigger=Trigger(TriggerType.LEAD_CREATED, offset_minutes=30 * 24 * 60),  # 30d
                action=Action(
                    ActionType.SEND_EMAIL,
                    template='levanta_muertos',
                    params={'asunto': 'Tu boda sigue siendo importante para nosotros'},
                ),
            ),
            Step(
                id='seguimiento_final',
                name='Seguimiento final',
                description='Ultimo intento - email de cierre',
                trigger=Trigger(TriggerType.LEAD_CREATED, offset_minutes=90 * 24 * 60),  # 90d
                action=Action(
                    ActionType.SEND_EMAIL,
                    template='seguimiento_final',
                    params={'asunto': 'Ultima oportunidad - que decidiste?'},
                ),
            ),
        ],
    )


def PRODUCTION_WORKFLOW() -> Workflow:
    """
    Workflow que se aplica cuando se ACEPTA el QUOTE (lead -> job).

    Trigger: QUOTE_ACCEPTED

    Pasos:
      1. Job accepted (marcador - dispara conversion lead -> job)
      2. Reserva confirmada - 1 dia despues
      3. Firma de contrato - 3 dias despues
      4. Pago de reserva (invoice.paid)
      5. Cuestionario cliente - 1 mes antes de la boda
      6. Boda (marcador el dia del evento)
      7. Google Comments - 2 meses despues de la boda
      8. Job complete
    """
    return Workflow(
        id='production_workflow_v1',
        name='Production',
        description='Workflow completo desde aceptar el quote hasta despues de la boda',
        trigger=Trigger(type=TriggerType.QUOTE_ACCEPTED),
        is_template=True,
        steps=[
            Step(
                id='job_accepted',
                name='Job accepted',
                description='El cliente acepto el quote - CONVERTIR LEAD A JOB',
                trigger=Trigger(TriggerType.QUOTE_ACCEPTED, offset_minutes=0),
                action=Action(
                    ActionType.LINK_JOB,
                    template='crear_job_desde_lead',
                    params={'empresa_default': 'NORKEVIN'},
                ),
            ),
            Step(
                id='reserva_confirmada',
                name='Reserva confirmada',
                description='Email de bienvenida + instrucciones de reserva',
                trigger=Trigger(TriggerType.QUOTE_ACCEPTED, offset_minutes=1 * 24 * 60),  # 1d
                action=Action(
                    ActionType.SEND_EMAIL,
                    template='reserva_confirmada',
                    params={'asunto': 'Reserva confirmada - Bienvenido!'},
                ),
            ),
            Step(
                id='firma_contrato',
                name='Firma de contrato',
                description='Enviar contrato para firma digital',
                trigger=Trigger(TriggerType.QUOTE_ACCEPTED, offset_minutes=3 * 24 * 60),  # 3d
                action=Action(
                    ActionType.SEND_EMAIL,
                    template='firma_contrato',
                    params={'asunto': 'Tu contrato de servicios fotograficos', 'adjunto': 'contrato_boda.pdf'},
                ),
            ),
            Step(
                id='pago_reserva',
                name='Pago de reserva (50%)',
                description='Recordatorio del pago de reserva - vincula a invoice',
                trigger=Trigger(TriggerType.QUOTE_ACCEPTED, offset_minutes=7 * 24 * 60),  # 7d
                action=Action(
                    ActionType.NOTIFY_OWNER,
                    template='owner_reminder',
                    params={'mensaje': 'Cliente aun no ha pagado la reserva'},
                ),
            ),
            Step(
                id='cuestionario_cliente',
                name='Cuestionario cliente',
                description='Cuestionario pre-boda: contactos, momentos clave, etc.',
                trigger=Trigger(TriggerType.QUOTE_ACCEPTED, offset_minutes=60 * 24 * 60),  # 60d antes (asumimos boda ~60d)
                action=Action(
                    ActionType.SEND_EMAIL,
                    template='cuestionario_pre_boda',
                    params={'asunto': 'Cuestionario para tu boda', 'link': '/cuestionario'},
                ),
            ),
            Step(
                id='boda',
                name='Boda',
                description='Marcador: dia del evento. Cambia status del job a En produccion',
                trigger=Trigger(TriggerType.QUOTE_ACCEPTED, offset_minutes=90 * 24 * 60),  # 90d (ajustar dinamicamente)
                action=Action(
                    ActionType.CHANGE_STATUS,
                    template='cambiar_a_produccion',
                    params={'new_status': 'En produccion'},
                ),
            ),
            Step(
                id='google_comments',
                name='Google Comments',
                description='Pedir review en Google despues de la boda',
                trigger=Trigger(TriggerType.QUOTE_ACCEPTED, offset_minutes=150 * 24 * 60),  # 60d post-boda
                action=Action(
                    ActionType.SEND_EMAIL,
                    template='pedir_review',
                    params={'asunto': 'Tu opinion nos importa - review Google?'},
                ),
            ),
            Step(
                id='job_complete',
                name='Job complete',
                description='Marcador final: archivo fotografico entregado, pagos completados',
                trigger=Trigger(TriggerType.QUOTE_ACCEPTED, offset_minutes=180 * 24 * 60),  # 90d post-boda
                action=Action(
                    ActionType.CHANGE_STATUS,
                    template='cambiar_a_listo',
                    params={'new_status': 'Listo'},
                ),
            ),
        ],
    )


def BODAS_NORKEVIN_TEMPLATE() -> dict:
    """Template nombre para aplicar por defecto (Studio Ninja style)."""
    return {
        'name': 'BODAS NORKEVIN',
        'lead_workflow': 'lead_workflow_v1',
        'production_workflow': 'production_workflow_v1',
        'is_default': True,
    }