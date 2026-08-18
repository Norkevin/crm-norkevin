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
from .storage import store, log_security_event


class MailStatus(Enum):
    PENDING = 'pending'             # aun no enviado
    SENT = 'sent'                    # enviado, sin abrir
    OPENED = 'opened'                # cliente abrio
    CLICKED = 'clicked'              # cliente hizo click en link
    BOUNCED = 'bounced'              # reboto
    FAILED = 'failed'                # fallo
    BLOCKED = 'blocked'              # rechazado por no cuadrar de cuenta


# Tipos de correo que SIEMPRE deben colgar de una boda concreta. Kevin: "no
# quiero que un template financiero pueda enviarse simplemente con un email
# escrito manualmente sin relacion con el cliente correcto".
TIPOS_QUE_EXIGEN_JOB = ('pago', 'factura', 'cobro', 'contrato', 'cuestionario',
                        'recordatorio')


def requires_job_relation(subject='', template_id=None, source=''):
    """True si este correo no deberia poder mandarse suelto.

    Se mira el asunto, la plantilla y el origen: un recordatorio de pago
    generado por un workflow tiene que apuntar a un job real, si no no hay
    forma de saber a que boda pertenece el cobro.
    """
    texto = ' '.join(str(x or '') for x in (subject, template_id, source)).lower()
    return any(p in texto for p in TIPOS_QUE_EXIGEN_JOB)


# Estados de un correo en la cola. La distincion clave que pidio Kevin:
#
#   BLOQUEADO = una regla de seguridad decidio que NO debia enviarse
#               (cruce de empresas, Gmail desconectado, adjunto ajeno).
#   FALLO     = estaba autorizado, pero el proveedor fallo (error de Gmail,
#               timeout, red).
#
# Mezclarlos haria que un problema de infraestructura se viera como un
# problema de seguridad, y al reves -- que es peor.
PENDIENTE = 'pending'
ENVIANDO = 'sending'
ENVIADO = 'sent'
BLOQUEADO = 'blocked'
FALLO = 'failed'
CANCELADO = 'discarded'


def check_attachments_same_tenant(tenant_id, attachments):
    """Ningun adjunto puede venir de otra empresa.

    Aunque el destinatario fuera el correcto, mandarle la factura o el
    contrato de otra empresa seria igual de grave que el incidente original.

    Los adjuntos pueden venir como dict con referencias ({'invoice_id':...},
    {'contract_id':...}, {'file_id':...}) o como texto suelto; solo se
    validan los que apuntan a un registro real.
    """
    for adj in attachments or []:
        if not isinstance(adj, dict):
            continue
        for campo, tabla, etiqueta in (
            ('invoice_id', 'payments', 'factura'),
            ('payment_id', 'payments', 'factura'),
            ('contract_id', 'contracts', 'contrato'),
            ('quote_id', 'quotes', 'cotizacion'),
            ('file_id', 'files', 'archivo'),
            ('questionnaire_id', 'questionnaires', 'cuestionario'),
        ):
            valor = adj.get(campo)
            if not valor:
                continue
            campo_busqueda = 'invoice_id' if campo == 'invoice_id' else 'id'
            dueno = store.owner_tenant_of(tabla, valor, field=campo_busqueda)
            if dueno is None:
                # Adjunto que no corresponde a ningun registro: no se puede
                # verificar de quien es, asi que no sale.
                log_security_event('ADJUNTO_SIN_DUENO', tabla=tabla,
                                   registro=valor, cuenta_activa=tenant_id)
                return f'el {etiqueta} adjunto no se pudo verificar'
            if dueno != tenant_id:
                log_security_event('CROSS_TENANT_ATTACHMENT_BLOCKED', tabla=tabla,
                                   registro=valor, cuenta_activa=tenant_id,
                                   cuenta_del_registro=dueno)
                return f'el {etiqueta} adjunto no pertenece a esta empresa'
    return None


def check_recipient_identity(tenant_id, to_email, client_id):
    """Quien recibe un correo es un CLIENTE de una empresa, no una direccion.

    Kevin, despues del incidente: "no confies en el email del destinatario".
    La misma direccion puede existir como cliente en Astral y en Norkevin y
    son dos personas distintas; la direccion no identifica a nadie.

    Devuelve (motivo_de_bloqueo, aviso). El bloqueo es duro; el aviso es
    informacion para la pantalla de revision, no corta el envio.
    """
    if not client_id:
        # Un correo sin cliente asociado no se puede verificar por identidad.
        # No se bloquea aca (el resto de reglas ya exige job para los tipos
        # sensibles), pero queda dicho que no hubo verificacion.
        return None, 'sin cliente asociado: no se pudo verificar la identidad'

    dueno = store.owner_tenant_of('clients', client_id)
    if dueno is None:
        log_security_event('CLIENTE_SIN_DUENO', registro=client_id,
                           cuenta_activa=tenant_id)
        return 'el cliente del correo no se pudo verificar', None
    if dueno != tenant_id:
        # ESTE es el caso del incidente: destinatario que pertenece al otro
        # negocio. El detalle de que empresa es va solo al log.
        log_security_event('CROSS_TENANT_RECIPIENT_BLOCKED', registro=client_id,
                           cuenta_activa=tenant_id, cuenta_del_registro=dueno)
        return 'el cliente del correo no pertenece a esta empresa', None

    # El cliente es de esta empresa. Falta ver si la direccion a la que se va
    # a escribir es de verdad la suya.
    cliente = store.get('clients', client_id) or {}
    suyas = {(cliente.get(c) or '').strip().lower()
             for c in ('email', 'secondary_email')} - {''}
    destino = (to_email or '').strip().lower()
    if destino and suyas and destino not in suyas:
        # No se bloquea: un cliente puede pedir que le escriban a otra
        # direccion, y cortar eso seria over-blocking. Pero queda registrado
        # y visible en la pantalla de revision.
        log_security_event('DESTINATARIO_DISTINTO_AL_DEL_CLIENTE',
                           registro=client_id, cuenta_activa=tenant_id)
        return None, 'la direccion no es la que tiene registrada el cliente'

    if destino and store.tenants_owning('clients', destino, field='email') - {tenant_id}:
        # La direccion tambien existe en la otra empresa. El envio es
        # correcto (manda el client_id, no el correo), pero es exactamente el
        # tipo de caso que hay que mirar dos veces antes de aprobar.
        log_security_event('DESTINATARIO_AMBIGUO', registro=client_id,
                           cuenta_activa=tenant_id)
        return None, ('esta direccion tambien existe como cliente en la otra '
                      'empresa: verifica que sea la persona correcta')
    return None, None


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
            # El detalle (que empresa es duena) va SOLO al log. Devolverlo al
            # usuario revelaria la existencia de recursos de la otra empresa.
            log_security_event(
                'CROSS_TENANT_EMAIL_BLOCKED', operacion='send_email', tabla=tabla,
                registro=valor, cuenta_activa=tenant_id, cuenta_del_registro=dueno,
            )
            return f'el {etiqueta} no pertenece a esta empresa'
    return None


def _anotar(pendiente, nuevo_estado, *, actor=None, motivo=None):
    """Cambia el estado dejando rastro, sin sobrescribir lo anterior.

    Kevin: "no quiero sobreescribir el historial... quiero poder reconstruir
    toda la secuencia". Cada paso se agrega a una lista, asi
    Pendiente -> Enviando -> Fallo queda entero y no solo el ultimo estado.
    """
    anterior = pendiente.get('status')
    pendiente['status'] = nuevo_estado
    if motivo:
        pendiente['blocked_reason' if nuevo_estado == BLOQUEADO else 'error'] = motivo
    pendiente.setdefault('historial', []).append({
        'de': anterior,
        'a': nuevo_estado,
        'cuando': datetime.now().isoformat(),
        'actor': actor or 'sistema',
        'motivo': motivo,
    })
    return pendiente


class MailTracker:
    """Tracker de emails. Persiste via el JsonStore compartido (data/mail_log.json)
    en vez de mantener su propia copia en memoria, para que cualquier otro
    lector (la campana de notificaciones, el listado de mail log, etc.) vea
    siempre el mismo estado que lo que este modulo acaba de escribir."""

    @property
    def log(self):
        return store.list('mail_log')

    # ---------------------------------------------------- cola de aprobacion

    def queue_email(self, to_email, subject, body='', template_id=None,
                    lead_id=None, job_id=None, client_id=None, attachments=None,
                    tenant_id=None, source=None):
        """Genera un correo y lo deja ESPERANDO aprobacion. No envia nada.

        Kevin, despues del incidente: "ningun email generado por el CRM debe
        poder salir sin una accion consciente de mi parte". Todo lo que antes
        se mandaba solo pasa por aca.

        Se guarda una copia completa (asunto, cuerpo ya renderizado,
        adjuntos, cuenta, destinatario, job, plantilla) a proposito: si
        manana cambia la plantilla, el pendiente debe seguir mostrando
        exactamente lo que se genero hoy, no algo distinto en silencio.
        """
        tenant_id = tenant_id or store.current_tenant_id()
        motivo = check_same_tenant(tenant_id, lead_id=lead_id, job_id=job_id,
                                   template_id=template_id)
        aviso = None
        if not motivo:
            motivo, aviso = check_recipient_identity(tenant_id, to_email, client_id)
        if not motivo and requires_job_relation(subject, template_id)                 and not job_id and not lead_id:
            motivo = 'un correo de este tipo debe estar ligado a una boda'
        # Un cobro o un contrato sin boda asociada no se puede verificar
        # contra nada: se bloquea antes de encolarlo.
        if not motivo and requires_job_relation(subject, template_id, source)                 and not job_id and not lead_id:
            motivo = 'un correo de este tipo debe estar ligado a una boda'
        entry = {
            'id': 'pend-' + uuid.uuid4().hex[:10],
            'tenant_id': tenant_id,
            'to': to_email,
            'client_id': client_id,
            'lead_id': lead_id,
            'job_id': job_id,
            'template_id': template_id,
            'subject': subject,
            'body': body or '',
            'attachments': attachments or [],
            'source': source or 'desconocido',
            'created_at': datetime.now().isoformat(),
            # Si ya al generarlo los datos no cuadran, se guarda igual pero
            # marcado: sirve de evidencia de que algo esta mal armado.
            'status': 'blocked' if motivo else 'pending',
            'blocked_reason': motivo,
            # Lo que no bloquea pero hay que mirar antes de aprobar.
            'aviso_identidad': aviso,
        }
        try:
            store.upsert('pending_emails', entry)
        except Exception:
            # Sin cuenta activa no se puede ni encolar; el intento igual se
            # devuelve para que el llamador lo reporte.
            entry['status'] = 'blocked'
            entry['blocked_reason'] = entry['blocked_reason'] or 'sin cuenta activa'
        return entry

    def approve_and_send(self, pending_id, sender_tenant_id=None, actor=None):
        """Envia un correo que estaba esperando aprobacion.

        Vuelve a validar TODO aca, no solo al crearlo: entre que se genero el
        pendiente y que se aprueba pueden pasar dias, y las relaciones pueden
        haber cambiado (un job reasignado, una plantilla movida). Confiar en
        la validacion vieja seria confiar en una foto vencida.
        """
        pendiente = store.get('pending_emails', pending_id)
        if not pendiente:
            return {'ok': False, 'error': 'No existe ese correo pendiente'}
        if pendiente.get('status') == 'sent':
            return {'ok': False, 'error': 'Ese correo ya fue enviado'}

        actual = sender_tenant_id or store.current_tenant_id()
        if not actual:
            return {'ok': False, 'error': 'Sin cuenta activa'}
        if pendiente.get('tenant_id') != actual:
            # Sin decir de que empresa es: eso revelaria su existencia.
            log_security_event('CROSS_TENANT_APPROVE_BLOCKED',
                               registro=pending_id, cuenta_activa=actual,
                               cuenta_del_registro=pendiente.get('tenant_id'))
            return {'ok': False, 'error': 'No encontrado'}

        motivo = check_same_tenant(actual,
                                   lead_id=pendiente.get('lead_id'),
                                   job_id=pendiente.get('job_id'),
                                   template_id=pendiente.get('template_id'))
        # Identidad del destinatario, tambien AL ENVIAR: entre que se genero
        # el pendiente y que se aprueba, el cliente pudo reasignarse a la
        # otra empresa sin que cambiara ni una letra de la direccion.
        if not motivo:
            motivo, aviso = check_recipient_identity(
                actual, pendiente.get('to'), pendiente.get('client_id'))
            pendiente['aviso_identidad'] = aviso
        # Los adjuntos se validan aparte y AL ENVIAR: un job pudo cambiar de
        # empresa despues de generarse el pendiente.
        if not motivo:
            motivo = check_attachments_same_tenant(actual, pendiente.get('attachments'))
        if motivo:
            _anotar(pendiente, BLOQUEADO, actor=actor, motivo=motivo)
            store.upsert('pending_emails', pendiente)
            # Se devuelve tambien el pendiente para que la pantalla pueda
            # mostrar el estado nuevo y su motivo sin volver a consultarlo.
            return {'ok': False, 'error': f'EMAIL BLOCKED: {motivo}',
                    'pendiente': pendiente}

        # Se marca ENVIANDO antes de intentar, para que la secuencia quede
        # completa en el historial aunque el envio se caiga a la mitad.
        _anotar(pendiente, ENVIANDO, actor=actor)
        store.upsert('pending_emails', pendiente)

        enviado = self.log_email(
            pendiente['to'], pendiente['subject'], pendiente.get('body') or '',
            template_id=pendiente.get('template_id'),
            lead_id=pendiente.get('lead_id'), job_id=pendiente.get('job_id'),
            attachments=pendiente.get('attachments') or [],
            tenant_id=actual,
        )
        ok = enviado.get('status') == MailStatus.SENT.value
        # Si el correo no salio, distinguir POR QUE: si mail_tracker lo
        # bloqueo es seguridad; si el proveedor fallo es infraestructura.
        if ok:
            estado_final = ENVIADO
        elif enviado.get('status') == MailStatus.BLOCKED.value:
            estado_final = BLOQUEADO
        else:
            estado_final = FALLO
        _anotar(pendiente, estado_final, actor=actor,
                motivo=enviado.get('blocked_reason') or enviado.get('delivery_error'))
        pendiente['sent_at'] = datetime.now().isoformat()
        pendiente['mail_id'] = enviado.get('id')
        store.upsert('pending_emails', pendiente)
        return {'ok': ok, 'pendiente': pendiente, 'mail': enviado}

    def retry_failed(self, pending_id, actor=None):
        """Reintenta un correo que fallo. MANUAL a proposito.

        Kevin: "nada de fallo -> enviar automaticamente otra vez". Un
        reintento automatico despues de un fallo es como se multiplican los
        envios cuando algo va mal.

        Solo aplica a FALLO (problema de infraestructura). Un BLOQUEADO no se
        reintenta: la razon por la que se bloqueo sigue ahi, y forzarlo seria
        saltarse la validacion.
        """
        pendiente = store.get('pending_emails', pending_id)
        if not pendiente:
            return {'ok': False, 'error': 'No encontrado'}
        if pendiente.get('status') != FALLO:
            return {'ok': False, 'pendiente': pendiente,
                    'error': 'Solo se puede reintentar un correo que fallo por '
                             'un problema tecnico'}
        # Vuelve a pendiente y pasa otra vez por TODAS las validaciones.
        _anotar(pendiente, PENDIENTE, actor=actor, motivo='reintento manual')
        store.upsert('pending_emails', pendiente)
        return self.approve_and_send(pending_id, actor=actor)

    def discard_pending(self, pending_id, actor=None):
        """Descarta un pendiente sin enviarlo. No se borra: queda como
        evidencia de que se genero y se decidio no mandarlo, y con quien lo
        decidio."""
        pendiente = store.get('pending_emails', pending_id)
        if not pendiente:
            return {'ok': False, 'error': 'No existe ese correo pendiente'}
        _anotar(pendiente, CANCELADO, actor=actor, motivo='descartado a mano')
        pendiente['discarded_at'] = datetime.now().isoformat()
        store.upsert('pending_emails', pendiente)
        return {'ok': True, 'pendiente': pendiente}

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
