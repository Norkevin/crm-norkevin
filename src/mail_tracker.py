"""
mail_tracker.py - Sistema de tracking de emails.

Estilo Studio Ninja:
  - Registra cada email enviado (subject, to, status, sent_at)
  - Tracking manual: marcar como 'opened' o 'clicked' (clickeando en el tracking link)
  - Mail log persistente en data/mail_log.json (via el JsonStore compartido,
    para que la campana de notificaciones y cualquier otro lector vean
    siempre el mismo estado que lo que este modulo acaba de escribir)
"""
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any
from enum import Enum
from .email_delivery import send_email
from .storage import store


class MailStatus(Enum):
    PENDING = 'pending'             # aun no enviado
    SENT = 'sent'                    # enviado, sin abrir
    OPENED = 'opened'                # cliente abrio
    CLICKED = 'clicked'              # cliente hizo click en link
    BOUNCED = 'bounced'              # reboto
    FAILED = 'failed'                # fallo
    BLOCKED = 'blocked'              # rechazado por no cuadrar de cuenta


def check_same_tenant(tenant_id, *, lead_id=None, job_id=None, template_id=None):
    """Verifica que todo lo que interviene en un correo sea de la MISMA cuenta.

    Nace del incidente en que el CRM mando correos firmados como Astral a
    clientes de Norkevin. La comprobacion vive aca, en el servidor y en el
    punto por el que pasa todo correo, justamente para que siga protegiendo
    aunque en el futuro un bug del frontend arme mal la peticion.

    Devuelve None si todo cuadra, o el motivo del bloqueo.
    """
    if not tenant_id:
        return 'sin cuenta identificada'

    for etiqueta, tabla, valor in (
        ('lead', 'leads', lead_id),
        ('job', 'jobs', job_id),
        ('plantilla', 'email_templates', template_id),
    ):
        if not valor:
            continue
        dueno = store.owner_tenant_of(tabla, valor)
        # dueno None = registro inexistente o sin cuenta: no se bloquea por
        # eso (lo reporta el inventario de huerfanos), pero si pertenece a
        # OTRA cuenta se corta.
        if dueno and dueno != tenant_id:
            return f'{etiqueta} {valor} pertenece a {dueno}, no a {tenant_id}'
    return None


class MailTracker:
    """Tracker de emails. Persiste via el JsonStore compartido (data/mail_log.json)
    en vez de mantener su propia copia en memoria, para que cualquier otro
    lector (la campana de notificaciones, el listado de mail log, etc.) vea
    siempre el mismo estado que lo que este modulo acaba de escribir."""

    @property
    def log(self):
        return store.list('mail_log')

    def log_email(self, to_email, subject, body='', template_id=None,
                  lead_id=None, job_id=None, attachments=None, tenant_id=None):
        """Entrega y registra un email.

        tenant_id explicito: mail_log es tenant-scoped, y store.upsert()
        solo auto-estampa el tenant_id de la SESION activa -- pero el hilo
        de recordatorios en segundo plano (check_and_send_payment_reminders,
        _auto_fire_due_job_steps) no tiene sesion, corre para las 3 cuentas
        a la vez. Sin pasar el tenant_id del job/payment que esta procesando
        en ese momento, el correo quedaria en mail_log sin cuenta asignada
        (invisible para todos)."""
        # Ultima verificacion antes de que salga cualquier cosa: que la
        # cuenta que envia sea la misma del lead/job/plantilla. Va aca porque
        # TODO correo de la app pasa por este metodo -- ponerlo en cada punto
        # de llamada seria olvidarse de uno tarde o temprano.
        # La mayoria de los envios normales no pasan tenant_id (lo daba solo
        # el hilo de fondo); en esos casos la cuenta es la de la sesion
        # activa. Sin este fallback la validacion cortaria TODO envio
        # legitimo, no solo los cruzados.
        tenant_id = tenant_id or store.current_tenant_id()
        motivo = check_same_tenant(tenant_id, lead_id=lead_id, job_id=job_id,
                                   template_id=template_id)
        if motivo:
            entry = {
                'id': 'mail-' + uuid.uuid4().hex[:8],
                'to': to_email,
                'subject': subject,
                'body': body or '',
                'body_preview': body[:200] if body else '',
                'template_id': template_id,
                'lead_id': lead_id,
                'job_id': job_id,
                'attachments': attachments or [],
                'status': MailStatus.BLOCKED.value,
                'blocked_reason': motivo,
                'sent_at': datetime.now().isoformat(),
                'opened_at': None,
                'clicked_at': None,
                'bounced_at': None,
                'delivery_provider': 'blocked',
                'delivery_mode': None,
                'delivery_message_id': None,
                'delivery_error': f'EMAIL BLOCKED: cross-company data mismatch ({motivo})',
            }
            if tenant_id:
                entry['tenant_id'] = tenant_id
            # Queda registrado aunque no se envie: sin rastro de los intentos
            # bloqueados no hay forma de investigar despues.
            try:
                store.upsert('mail_log', entry)
            except Exception:
                pass
            return entry

        delivery = send_email(
            to_email,
            subject,
            body or '',
            attachments=attachments or [],
            metadata={'lead_id': lead_id, 'job_id': job_id, 'template_id': template_id, 'tenant_id': tenant_id},
        )
        entry = {
            'id': 'mail-' + uuid.uuid4().hex[:8],
            'to': to_email,
            'subject': subject,
            'body': body or '',
            'body_preview': body[:200] if body else '',
            'template_id': template_id,
            'lead_id': lead_id,
            'job_id': job_id,
            'attachments': attachments or [],
            'status': MailStatus.SENT.value if delivery.ok else MailStatus.FAILED.value,
            'sent_at': datetime.now().isoformat(),
            'opened_at': None,
            'clicked_at': None,
            'bounced_at': None,
            'delivery_provider': delivery.provider,
            'delivery_mode': delivery.mode,
            'delivery_message_id': delivery.message_id,
            'delivery_error': delivery.error,
        }
        if tenant_id:
            entry['tenant_id'] = tenant_id
        store.upsert('mail_log', entry)
        return entry

    def mark_opened(self, mail_id):
        """Marca un email como abierto."""
        entry = store.get('mail_log', mail_id)
        if not entry:
            return None
        if entry['status'] in (MailStatus.SENT.value,):
            entry['status'] = MailStatus.OPENED.value
            entry['opened_at'] = datetime.now().isoformat()
            store.upsert('mail_log', entry)
        return entry

    def mark_clicked(self, mail_id):
        """Marca un email como clickeado."""
        entry = store.get('mail_log', mail_id)
        if not entry:
            return None
        entry['status'] = MailStatus.CLICKED.value
        if not entry.get('clicked_at'):
            entry['clicked_at'] = datetime.now().isoformat()
        store.upsert('mail_log', entry)
        return entry

    def list_for_lead(self, lead_id):
        """Lista todos los emails de un lead."""
        return [e for e in self.log if e.get('lead_id') == lead_id]

    def list_for_job(self, job_id):
        """Lista todos los emails de un job."""
        return [e for e in self.log if e.get('job_id') == job_id]

    def list_recent(self, limit=50):
        """Lista los ultimos emails."""
        return sorted(self.log, key=lambda e: e.get('sent_at', ''), reverse=True)[:limit]

    def stats(self):
        """Estadisticas del mail log."""
        log = self.log
        total = len(log)
        by_status = {}
        for entry in log:
            s = entry.get('status', 'unknown')
            by_status[s] = by_status.get(s, 0) + 1
        return {'total': total, 'by_status': by_status}


# Singleton global
_tracker = None

def get_tracker():
    global _tracker
    if _tracker is None:
        _tracker = MailTracker()
    return _tracker
