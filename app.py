"""
CRM Astral Weddings - Backend Flask
Arquitectura: Notion-first. SQLite solo para cache de sesión.
"""
import os
import re
import hmac
import time
import threading
import logging
from datetime import datetime, date, timedelta
from flask import (Flask, render_template, request, redirect, url_for, jsonify, flash, abort,
                   session, make_response, g, has_request_context)
from dotenv import load_dotenv

load_dotenv()

import notion_sync as ns
from collections import defaultdict

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from src.workflow import WorkflowEngine, LEAD_WORKFLOW, PRODUCTION_WORKFLOW
from src.workflow.models import StepStatus, WorkflowStatus, TriggerType
from src.storage import store, log_security_event, TenantMismatchError
from src import public_links, public_tokens, quote_numbering
from src.tenant_brand_map import display_name_for_tenant as _brand_display_name_for_tenant

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MONTH_NAMES_ES = {
    1: 'enero',
    2: 'febrero',
    3: 'marzo',
    4: 'abril',
    5: 'mayo',
    6: 'junio',
    7: 'julio',
    8: 'agosto',
    9: 'septiembre',
    10: 'octubre',
    11: 'noviembre',
    12: 'diciembre',
}


def _parse_iso_day(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def _format_date_es(value):
    day = value if isinstance(value, date) else _parse_iso_day(value)
    if not day:
        return ''
    return f"{day.day} {MONTH_NAMES_ES.get(day.month, '')} {day.year}"



def _log_storage_safety_status():
    status = store.status()
    if os.environ.get('RENDER') and not status['is_render_persistent_path']:
        logger.warning(
            'Render esta usando %s para datos. Configura CRM_DATA_DIR=/var/data '
            'y monta el disk persistente en /var/data antes de cargar datos reales.',
            status['data_dir'],
        )
    else:
        logger.info('CRM data dir activo: %s', status['data_dir'])


_log_storage_safety_status()


def _bootstrap_seed_table(table):
    """Si una tabla de configuracion esta vacia (deploy nuevo, ej. Render),
    la llena con los defaults en data/seeds/<table>.default.json -- esos SI
    viajan con el codigo (a diferencia de data/*.json real, que nunca se
    sube). Sin esto los steps de workflow que 'auto-mandan email' mandan
    correos en blanco (plantillas inexistentes) y el editor de cotizaciones
    arranca sin ningun paquete para elegir."""
    if store.list(table):
        return
    seed_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'seeds', f'{table}.default.json')
    if not os.path.exists(seed_path):
        return
    try:
        import json as _json_seed
        with open(seed_path, 'r', encoding='utf-8') as f:
            defaults = _json_seed.load(f)
        for record in defaults:
            store.upsert(table, record)
        logger.info(f'Sembrados {len(defaults)} registros por defecto en {table}')
    except Exception as exc:
        logger.warning(f'No se pudieron sembrar los defaults de {table}: {exc}')


def _bootstrap_default_email_templates():
    """Compat: los tests y el arranque llaman esta funcion por nombre."""
    _bootstrap_seed_table('email_templates')


_bootstrap_default_email_templates()
_bootstrap_seed_table('packages')

app = Flask(__name__)

# Filtro Jinja compartido: cualquier plantilla puede escribir
# `{{ fecha|fecha_es }}` y obtener "28 noviembre 2026" en vez de la fecha
# ISO con la que se guarda. Registrarlo como filtro -- y no depender de que
# cada vista precalcule un campo *_display -- es lo que garantiza que no se
# vuelva a colar un "2026-11-28" en un documento que ve el cliente: si
# alguien agrega una fecha nueva y olvida el filtro, se nota enseguida, y
# si la usa, funciona sin tocar Python.
app.jinja_env.filters['fecha_es'] = lambda v: _format_date_es(v) or (v or '')

app.secret_key = os.environ.get('FLASK_SECRET', 'norkevin-crm-dev-secret-change-me')
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True

# Aislamiento multi-tenant: JsonStore filtra automaticamente por
# Cuenta activa de la peticion en curso. Dos fuentes, en este orden:
#
#   1. session['tenant_id'] -- alguien logueado en el CRM.
#   2. g.public_tenant_id  -- una ruta publica (portal del cliente, aceptar
#      cotizacion, firmar contrato) que llega SIN sesion. La cuenta se deduce
#      del propio registro del enlace en _resolve_public_tenant(), antes de
#      tocar ningun dato.
#
# Fuera de un request (hilos de fondo, scripts) esto lanza RuntimeError y
# storage.py lo atrapa devolviendo None. Antes None significaba "todas las
# cuentas"; ahora significa "ninguna" y la operacion se deniega. Ese cambio
# es la correccion del incidente en que un hilo sin sesion recorrio las
# bodas de los dos negocios juntos.
def _active_tenant_id():
    tid = session.get('tenant_id')
    if tid:
        return tid
    return getattr(g, 'public_tenant_id', None)


store.tenant_resolver = _active_tenant_id
store.request_context_probe = has_request_context
# Solo las rutas de /api/admin/* autenticadas marcan este flag (ver
# _require_login). Es lo que habilita scope='all_tenants' en
# store.list_privileged: cruzar empresas tiene que ser una excepcion
# deliberada, no algo que cualquier ruta consiga por descuido.
store.admin_context_probe = lambda: bool(getattr(g, 'is_admin_request', False))

from src import gmail_delivery as _gmail_delivery_module
_gmail_delivery_module.tenant_resolver = _active_tenant_id


@app.after_request
def add_dev_cache_headers(response):
    if response.content_type and response.content_type.startswith('text/html'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


@app.route('/api/storage/status')
def api_storage_status():
    counts = {}
    for table in ('clients', 'leads', 'jobs', 'quotes', 'payments', 'contracts'):
        counts[table] = len(store.list(table))
    return jsonify({
        'ok': True,
        'storage': store.status(),
        'counts': counts,
        'render': bool(os.environ.get('RENDER')),
    })

# ============================================================
# WORKFLOW ENGINE (singleton global)
# ============================================================
workflow_engine = WorkflowEngine(persistence_store=store)
workflow_engine.register_template(LEAD_WORKFLOW())
workflow_engine.register_template(PRODUCTION_WORKFLOW())


def _workflow_from_dict(d):
    """Reconstruye un Workflow desde el formato de Workflow.to_dict()."""
    from src.workflow.models import Workflow, Step, DueDate, ActionType as _AT, TriggerType as _TT
    steps = []
    for s in d.get('steps', []):
        dd = s.get('due_date') or {}
        steps.append(Step(
            id=s['id'],
            name=s['name'],
            description=s.get('description', ''),
            action_type=_AT(s.get('action_type', 'noop')),
            email_template_id=s.get('email_template_id') or None,
            due_date=DueDate(
                mode=dd.get('mode', 'manual'),
                amount=int(dd.get('amount', 0) or 0),
                unit=dd.get('unit', 'days'),
                relative_to=dd.get('relative_to', 'lead_created'),
            ),
        ))
    return Workflow(
        id=d['id'],
        name=d.get('name', d['id']),
        description=d.get('description', ''),
        trigger=_TT(d.get('trigger_type', 'lead.created')),
        steps=steps,
        is_template=True,
    )


def _persist_workflow_template(workflow):
    """Guarda el template editado en data/workflow_templates.json.
    NOTA (multi-tenant): el WorkflowEngine registra templates por un id
    fijo compartido (p.ej. 'production_workflow_v1'), no por tenant --
    volver esto realmente independiente por cuenta requeriria que el motor
    mismo indexe sus templates por (tenant_id, workflow_id), no solo
    cambiar donde se guarda el archivo. Por ahora la automatizacion base
    (que steps existen, que plantilla usa cada uno) es compartida entre
    las 3 cuentas; lo que SI esta aislado por cuenta es el AVANCE de cada
    job/lead dentro de esos steps (workflow_instances, ligado al job que
    ya paso por el filtro de tenant al buscarlo)."""
    saved = store.get_dict('workflow_templates')
    saved[workflow.id] = workflow.to_dict()
    store.save_dict('workflow_templates', saved)


# Overlay: templates editados por el usuario pisan los hardcodeados al boot.
for _tid, _tdata in store.get_dict('workflow_templates').items():
    try:
        workflow_engine.register_template(_workflow_from_dict(_tdata))
    except Exception as _exc:
        logger.warning(f'No se pudo cargar workflow template guardado {_tid}: {_exc}')


# ============================================================
# TRIGGERS AUTOMATICOS
# ============================================================
def trigger_workflow_for_lead(lead_id, lead_name, tenant_id=None):
    """Dispara LEAD_WORKFLOW cuando se crea un lead.

    tenant_id (27-ago-2026): quien llama deberia pasar el tenant_id del
    propio lead (ya conocido en el call site). Si no se pasa, cae a la
    sesion activa -- pensado para no romper algun caller que todavia no
    se actualizo, no como el camino preferido."""
    return workflow_engine.start_workflow(
        workflow=LEAD_WORKFLOW(),
        subject_type='lead',
        subject_id=lead_id,
        subject_name=lead_name,
        trigger_event='lead.created',
        tenant_id=tenant_id or get_current_tenant_id(),
    )


def trigger_workflow_for_quote_accepted(lead_id, lead_name, job_id=None, tenant_id=None):
    """Dispara PRODUCTION_WORKFLOW cuando un lead acepta el quote.

    tenant_id (27-ago-2026): idem trigger_workflow_for_lead -- preferi
    pasar el tenant_id del job/lead ya conocido en el call site."""
    job_id = job_id or ('job-' + lead_id)
    return workflow_engine.start_workflow(
        workflow=PRODUCTION_WORKFLOW(),
        subject_type='job',
        subject_id=job_id,
        subject_name=lead_name,
        trigger_event='quote.accepted',
        tenant_id=tenant_id or get_current_tenant_id(),
    )

# ============================================================
# MULTI-TENANCY: 3 cuentas completamente independientes (Astral Weddings /
# Norkevin Photography / Ramiro Cruz Photo). El tenant_id de la cuenta
# logueada se fija UNA VEZ en session['tenant_id'] durante el login con
# Google (ver auth_google_login_callback) y de ahi en mas es la unica
# fuente de verdad -- ya no se puede "cambiar de cuenta" con un query
# param, un ID o una peticion manual (Kevin fue explicito sobre esto).
# El filtrado real por tenant_id vive en JsonStore (src/storage.py); estos
# helpers son compatibilidad hacia atras para el codigo que ya los llama.
# ============================================================
def get_current_tenant_id():
    """Tenant_id de la cuenta logueada en esta sesion, o None si no hay
    sesion (login, rutas publicas, o el hilo de recordatorios en segundo
    plano que corre fuera de cualquier request)."""
    try:
        return session.get('tenant_id')
    except RuntimeError:
        # Fuera de un contexto de request (hilo en segundo plano/scripts).
        return None

def filter_by_tenant(records, tenant_id=None):
    """Filtra una lista de records por tenant_id. store.list(...) ya viene
    filtrado si hay una cuenta activa, asi que esto es principalmente para
    los pocos call sites que reciben la lista ya construida."""
    if tenant_id is None:
        tenant_id = get_current_tenant_id()
    if not tenant_id:
        return records
    return [r for r in records if r.get('tenant_id') == tenant_id]


def _tenant_for_login_email(email):
    """tenants.json es la unica fuente de verdad de 'quien puede entrar y a
    que cuenta' -- reemplaza a ALLOWED_LOGIN_EMAILS (que solo decia SI podia
    entrar, no A CUAL cuenta, y podia desincronizarse). 'tenants' no es una
    tabla tenant-scoped, asi que store.list() siempre devuelve las 3 aunque
    todavia no haya sesion (estamos resolviendo justamente cual es)."""
    email = (email or '').strip().lower()
    if not email:
        return None
    for tenant in store.list('tenants'):
        if (tenant.get('login_email') or '').strip().lower() == email:
            return tenant
    return None


def _tenant_by_slug(slug):
    slug = (slug or '').strip().lower()
    if not slug:
        return None
    for tenant in store.list('tenants'):
        if (tenant.get('slug') or '').strip().lower() == slug:
            return tenant
    return None

def list_leads(tenant_id=None):
    if tenant_id is None:
        tenant_id = get_current_tenant_id()
    return filter_by_tenant(store.list('leads'), tenant_id)

def list_jobs(tenant_id=None):
    if tenant_id is None:
        tenant_id = get_current_tenant_id()
    return filter_by_tenant(store.list('jobs'), tenant_id)

def list_clients(tenant_id=None):
    if tenant_id is None:
        tenant_id = get_current_tenant_id()
    return filter_by_tenant(store.list('clients'), tenant_id)

def list_payments(tenant_id=None):
    if tenant_id is None:
        tenant_id = get_current_tenant_id()
    return filter_by_tenant(store.list('payments'), tenant_id)

def list_quotes(tenant_id=None):
    if tenant_id is None:
        tenant_id = get_current_tenant_id()
    return filter_by_tenant(store.list('quotes'), tenant_id)


def _visible_billable_payments(tenant_id=None):
    """Pagos que deben verse en Payments/Dashboard.

    Las cotizaciones solo cuentan cuando ya fueron aceptadas y por tanto
    generaron factura. Pagos internos de equipo quedan fuera.
    """
    quotes = {q.get('id'): q for q in store.list('quotes')}
    visible = []
    for payment in list_payments(tenant_id):
        if payment.get('tipo') == 'team_payment':
            continue
        quote_id = payment.get('quote_id')
        if quote_id in quotes and quotes.get(quote_id, {}).get('status') != 'Aceptada':
            continue
        visible.append(payment)
    return visible

# Helpers individuales (sin filtro tenant)
def get_lead(lead_id):
    return store.get('leads', lead_id)

def get_client(client_id):
    return store.get('clients', client_id)

def get_job(job_id):
    return store.get('jobs', job_id)


def get_job_client_ids(job):
    """Kevin: 'un Job puede tener uno, dos o mas clientes relacionados'.
    El modelo YA tenia soporte para esto -- client_id (principal) +
    secondary_client_id + planner_client_id -- solo que el import de
    Studio Ninja nunca lo usaba (siempre dejaba secondary/planner vacios).
    Esta funcion es la unica fuente de verdad para "todos los clientes de
    este job" reutilizando esos 3 campos reales en vez de inventar una
    estructura paralela."""
    # ACTUALIZADO (agosto 2026): esta funcion leia SOLO los 3 campos
    # legacy, asi que seguia topada en 3 clientes aunque el job tuviera
    # mas en job_clients. Ahora delega en la fuente canonica, que lee la
    # relacion N y cae a los campos viejos solo si el job no tiene
    # relaciones nuevas.
    return [r['client_id'] for r in _job_client_relations(job)]


def get_job_clients(job):
    """Los registros de Client completos (no solo ids) para este job,
    principal primero. Filtra los que ya no existen en vez de reventar."""
    return [c for c in (get_client(cid) for cid in get_job_client_ids(job)) if c]

def upsert_lead(lead):
    return store.upsert('leads', lead)

def upsert_job(job):
    return store.upsert('jobs', job)


def _norm_email(value):
    return (value or '').strip().lower()


def _norm_phone(value):
    return re.sub(r'\D+', '', value or '')


def _split_name(full_name):
    parts = (full_name or '').strip().split(' ', 1)
    return (parts[0] if parts else 'Cliente', parts[1] if len(parts) > 1 else '')


def _client_name(client=None, lead=None, job=None):
    if client:
        full = f"{client.get('first_name', '')} {client.get('last_name', '')}".strip()
        if full:
            return full
    if lead and lead.get('nombre'):
        return lead.get('nombre')
    if job and job.get('nombre'):
        return job.get('nombre')
    return 'Cliente'


def _email_for(client=None, lead=None):
    return (client or {}).get('email') or (lead or {}).get('email') or ''


def _job_all_recipient_emails(job, primary_client=None, lead=None):
    """Kevin: 'que se pueda agregar hasta 3 clientes... asi le mandaria los
    correos a los 3 y no se le pasa a nadie' -- junta el email del cliente
    principal, el secundario y el wedding planner (si estan vinculados) en
    una sola lista para el header To:, sin duplicados."""
    emails = []
    primary_email = _email_for(client=primary_client, lead=lead)
    if primary_email:
        emails.append(primary_email)
    for role in ('secondary_client_id', 'planner_client_id'):
        cid = job.get(role)
        if not cid:
            continue
        extra_client = get_client(cid)
        extra_email = (extra_client or {}).get('email')
        if extra_email and extra_email not in emails:
            emails.append(extra_email)
    return emails


def _mail_delivery_warning(entry):
    """Kevin recibia toasts de 'enviado' cuando en realidad Gmail estaba
    desconectado y el correo solo se guardaba en data/mail_outbox.json
    (local_outbox), sin llegar nunca al cliente. send_email() cae ahi en
    silencio -- este helper convierte ese caso en un aviso explicito que el
    frontend puede mostrar en vez de un exito falso.

    STAGE 2 (agosto 2026): ahora tambien recibe registros de pending_emails
    (lo que devuelve MailTracker.queue_email()), no solo de mail_log -- los
    14 puntos de envio de produccion pasaron de log_email() (entrega
    inmediata) a queue_email() (espera aprobacion humana en /emails), asi
    que el caso mas comun en la practica ya no es 'se entrego' sino 'quedo
    en la cola'. Sin este aviso, la pantalla seguiria diciendo 'enviado'
    para algo que en realidad nadie reviso todavia."""
    if not entry:
        return None
    estado = entry.get('status')
    if estado == 'pending':
        return ('El correo quedo en la cola de aprobacion (STAGE 2): no sale '
                'hasta que alguien lo apruebe en /emails.')
    if estado == 'blocked':
        motivo = entry.get('blocked_reason') or entry.get('delivery_error') or 'motivo no especificado'
        return f'El correo quedo BLOQUEADO y no se puso en la cola: {motivo}. Revisalo en /emails.'
    if estado == 'failed':
        return f"El correo NO se pudo entregar: {entry.get('delivery_error') or entry.get('error') or 'error desconocido'}."
    if entry.get('delivery_provider') == 'local_outbox':
        return 'El correo se registro pero NO se entrego de verdad porque Gmail no esta conectado. Conecta Gmail en Configuracion y vuelve a enviarlo.'
    return None


def _lead_mail_status_chip(mail_entry):
    """Traduce el resultado de queue_email() al chip que ve Kevin en el
    lead (dashboard.html, l.mail_status). Antes de STAGE 2 esto se pisaba
    con 'ENVIADO' sin condicion -- ahora refleja lo que de verdad paso:
    'EN COLA' mientras espera aprobacion en /emails, 'ENVIADO' solo si el
    idempotency_key ya encontro un envio real anterior, 'BLOQUEADO' solo si
    una regla de seguridad de verdad impidio encolarlo.

    Revision de STAGE 2 (agosto 2026): el else final devolvia 'BLOQUEADO'
    para CUALQUIER estado que no fuera 'sent'/'pending' -- incluyendo
    'sending', 'failed', 'discarded' o un mail_entry vacio/None, ninguno de
    los cuales es una decision de seguridad. Eso le mostraria a Kevin un
    chip de bloqueo por algo que nunca fue bloqueado. Cada estado real de
    MailStatus (src/mail_tracker.py) tiene ahora su propio chip; solo lo
    desconocido cae en 'EN COLA', que es el neutral honesto (revisar
    /emails para saber que paso de verdad) en vez de alarmar de mas."""
    estado = (mail_entry or {}).get('status')
    if estado in ('sent', 'ENVIADO'):
        return 'ENVIADO'
    if estado in ('pending', 'PENDIENTE', 'sending', 'ENVIANDO'):
        return 'EN COLA'
    if estado in ('blocked', 'BLOQUEADO'):
        return 'BLOQUEADO'
    if estado in ('failed', 'FALLO'):
        return 'FALLO'
    if estado in ('discarded', 'CANCELADO'):
        return 'DESCARTADO'
    return 'EN COLA'


def _idempotency_minute_bucket():
    """Ventana de 1 minuto para las claves de idempotencia de los correos
    disparados manualmente (boton de un admin): suficiente para no encolar
    dos veces un doble-click o un doble-submit del formulario, sin bloquear
    un reenvio deliberado unos minutos despues -- a diferencia de un paso de
    workflow o un recordatorio automatico, un reenvio manual intencional es
    un caso legitimo que no hay que confundir con un duplicado accidental."""
    return datetime.now().strftime('%Y%m%d%H%M')


def _get_email_template(template_id):
    if not template_id:
        return None
    return next((tpl for tpl in store.list('email_templates') if tpl.get('id') == template_id), None)


def _inject_link(body, url, placeholders, fallback_label):
    """Garantiza que un correo lleve SIEMPRE su link (cuestionario, contrato,
    etc): reemplaza el primer placeholder que encuentre, y si el usuario
    edito el mensaje y borro el placeholder, lo agrega al final igual --
    nunca debe salir un correo sin el link que lo justifica."""
    for ph in placeholders:
        if ph in body:
            return body.replace(ph, url)
    return f"{body}\n\n{fallback_label}:\n{url}"


def _render_message_template(text, *, client=None, lead=None, job=None):
    text = text or ''
    name = _client_name(client=client, lead=lead, job=job)
    company_name = _brand_display_name_for_tenant(
        (job or {}).get('tenant_id') or (lead or {}).get('tenant_id') or (client or {}).get('tenant_id')
    )
    boda_date = (
        (job or {}).get('boda_date')
        or (lead or {}).get('fecha_tentativa')
        or (lead or {}).get('fecha_evento')
        or ''
    )
    location = (job or {}).get('location') or (lead or {}).get('locacion') or (lead or {}).get('ubicacion') or ''
    replacements = {
        '{{nombre}}': name,
        '{{ nombre }}': name,
        '{{fecha_boda}}': boda_date,
        '{{ fecha_boda }}': boda_date,
        '{{job_date}}': boda_date,
        '{{ job_date }}': boda_date,
        '{{locacion}}': location,
        '{{ locacion }}': location,
        '%client_name%': name,
        '%job_date%': boda_date,
        '%company_name%': company_name,
    }
    for key, value in replacements.items():
        text = text.replace(key, str(value or ''))
    return text


def _complete_job_workflow_step(job, step_id, result_message=None):
    if not step_id:
        return {'completed': False}

    tmpl = PRODUCTION_WORKFLOW()
    step = next((s for s in tmpl.steps if s.id == step_id), None)
    if not step:
        return {'completed': False, 'warning': 'Step no encontrado'}

    instances = _workflow_instances_seguras(subject_type='job', subject_id=job.get('id'))
    if not instances:
        instance = workflow_engine.start_workflow(
            workflow=PRODUCTION_WORKFLOW(),
            subject_type='job',
            subject_id=job.get('id'),
            subject_name=job.get('nombre', 'Job'),
            trigger_event='job.created',
            auto_execute_first=False,
            tenant_id=job.get('tenant_id'),
        )
        instances = [instance]

    instance = instances[0]
    if step_id in instance.step_states and instance.step_states[step_id] == StepStatus.DONE:
        return {'completed': False, 'already_done': True, 'step': step.name}

    instance.step_states[step_id] = StepStatus.DONE
    instance.step_results[step_id] = result_message or f"ACTION completed manually: {step.name}"

    pagos_equipo = []
    action_value = step.action_type.value if hasattr(step.action_type, 'value') else str(step.action_type)
    if action_value == 'change_status':
        new_status = 'Listo'
        job['status'] = new_status
        if new_status == 'Listo':
            pagos_equipo = generate_team_payments_for_job(job)
            workflow_engine._log(instance, 'team.payments_generated',
                                 f'Se generaron {len(pagos_equipo)} pagos para el equipo')

    ordered_ids = [s.id for s in tmpl.steps]
    total_steps = len(ordered_ids)
    done_steps = sum(1 for sid in ordered_ids if instance.step_states.get(sid) == StepStatus.DONE)
    next_step = next((s for s in tmpl.steps if instance.step_states.get(s.id) != StepStatus.DONE), None)
    instance.current_step_id = next_step.id if next_step else None
    if not next_step:
        instance.status = WorkflowStatus.COMPLETED

    job['workflow_progress'] = round(done_steps * 100 / total_steps) if total_steps else 0
    job['next_task'] = next_step.name if next_step else 'Trabajo completado'
    job['updated_at'] = datetime.now().isoformat()
    upsert_job(job)

    workflow_engine._log(instance, 'step.manual', f'{step.name}: completado manualmente')
    workflow_engine._save_to_storage()
    return {
        'completed': True,
        'step': step.name,
        'action': action_value,
        'next_task': job['next_task'],
        'workflow_progress': job['workflow_progress'],
        'pagos_equipo_generados': len(pagos_equipo),
    }


def _complete_lead_workflow_step(lead, step_id, result_message=None, *, send_email=True,
                                  subject_override=None, body_override=None):
    if not step_id:
        return {'completed': False}

    if step_id == 'job_accepted':
        result = _convert_lead_to_job(lead, quote=None, status='Confirmado', create_payments=False)
        return {
            'completed': True,
            'step': 'Trabajo aceptado',
            'converted': True,
            'job_id': result['job']['id'],
            'client_id': result['client']['id'],
            'already_converted': not result['job_created'],
            'sent': False,
        }

    tmpl = LEAD_WORKFLOW()
    step = next((s for s in tmpl.steps if s.id == step_id), None)
    if not step:
        return {'completed': False, 'warning': 'Step no encontrado'}

    instances = _workflow_instances_seguras(subject_type='lead', subject_id=lead.get('id'))
    if not instances:
        return {'completed': False, 'warning': 'No hay workflow activo para este lead'}

    instance = instances[0]
    if step_id in instance.step_states and instance.step_states[step_id] == StepStatus.DONE:
        return {'completed': False, 'already_done': True, 'step': step.name}

    mail_entry = None
    if send_email:
        to_email = lead.get('email') or ''
        if not to_email:
            return {'completed': False, 'warning': 'Este lead no tiene email'}
        template = _get_email_template(step.email_template_id)
        subject = _render_message_template(
            subject_override or (template or {}).get('asunto') or step.name, lead=lead)
        body = _render_message_template(
            body_override or (template or {}).get('cuerpo') or '', lead=lead)
        from src.mail_tracker import get_tracker
        # STAGE 2 (agosto 2026): cola de aprobacion en vez de entrega
        # inmediata. Clave estable: la logica de arriba (linea 605-606) ya
        # impide re-disparar un step DONE, asi que esto es un respaldo, no
        # la guarda principal.
        mail_entry = get_tracker().queue_email(
            to_email=to_email,
            subject=subject,
            body=body,
            template_id=step.email_template_id,
            lead_id=lead.get('id'),
            client_id=lead.get('client_id') or None,
            source=f'workflow:lead-step:{step_id}',
            idempotency_key=f"leadstep:{lead.get('id')}:{step_id}",
        )
        lead['mail_status'] = _lead_mail_status_chip(mail_entry)

    instance.step_states[step_id] = StepStatus.DONE
    instance.step_results[step_id] = result_message or (
        f"EMAIL sent manually: {step.name}" if send_email else f"TASK completed manually: {step.name}"
    )

    steps, _, _ = compute_workflow_steps_for_lead(lead)
    next_pending = next((s for s in steps if s.get('id') != step_id and s.get('status') != 'done'), None)
    lead['next_task'] = next_pending.get('name') if next_pending else 'Trabajo aceptado'
    upsert_lead(lead)

    action_label = 'enviado manualmente' if send_email else 'completado manualmente'
    workflow_engine._log(instance, 'step.manual', f'{step.name}: {action_label}')
    workflow_engine._save_to_storage()
    return {
        'completed': True,
        'step': step.name,
        'mail_id': mail_entry.get('id') if mail_entry else None,
        'sent': bool(mail_entry),
    }


def _same_tenant_or_legacy(record, tenant_id):
    record_tenant = record.get('tenant_id')
    return not tenant_id or not record_tenant or record_tenant == tenant_id


def _find_client_for_lead(lead):
    tenant_id = lead.get('tenant_id') or get_current_tenant_id()
    email = _norm_email(lead.get('email'))
    phone = _norm_phone(lead.get('telefono') or lead.get('phone'))

    direct_id = lead.get('client_id')
    if direct_id:
        direct = get_client(direct_id)
        if direct:
            return direct

    clients = store.list('clients')
    if email:
        for client in clients:
            if _same_tenant_or_legacy(client, tenant_id) and _norm_email(client.get('email')) == email:
                return client

    if phone:
        for client in clients:
            if _same_tenant_or_legacy(client, tenant_id) and _norm_phone(client.get('phone')) == phone:
                return client

    return None


def _ensure_client_for_lead(lead):
    import uuid
    tenant_id = lead.get('tenant_id') or get_current_tenant_id()
    existing = _find_client_for_lead(lead)
    first_name, last_name = _split_name(lead.get('nombre'))
    today = datetime.now().isoformat()[:10]

    if existing:
        # Kevin: lleno el formulario como 'Angel Lemus' pero el cliente que
        # quedo vinculado seguia mostrando el nombre de un cliente viejo
        # (coincidio por email/telefono con un registro existente) -- antes
        # esto SOLO llenaba campos vacios, nunca corregia un nombre ya
        # presente, asi que un match por email/telefono dejaba el nombre
        # viejo pegado sin sentido. El lead mas reciente es la fuente mas
        # confiable de quien es esta persona ahora mismo, asi que sincroniza
        # nombre/telefono/email/direccion en vez de solo rellenar blancos.
        changed = False
        for key, value in {
            'first_name': first_name,
            'last_name': last_name,
            'phone': lead.get('telefono', ''),
            'email': lead.get('email', ''),
            'tenant_id': tenant_id,
            'estado': 'Activo',
        }.items():
            if value and existing.get(key) != value:
                existing[key] = value
                changed = True
        if changed:
            store.upsert('clients', existing)
        return existing, False

    client = {
        'id': 'client-' + uuid.uuid4().hex[:8],
        'first_name': first_name,
        'last_name': last_name,
        'company': '',
        'phone': lead.get('telefono', ''),
        'email': lead.get('email', ''),
        # address deliberadamente vacio: NO se hereda del venue del lead.
        # Ver la nota en _ensure_client_for_lead sobre el bucle
        # venue -> address -> venue que metia emails/telefonos en location.
        'address': '',
        'source': lead.get('fuente', 'Lead'),
        'tenant_id': tenant_id,
        'created': today,
        'estado': 'Activo',
    }
    store.upsert('clients', client)
    return client, True


def _find_job_for_lead(lead):
    for key in ('lead_id_job', 'job_id', 'converted_to_job', 'converted_job_id'):
        job_id = lead.get(key)
        if job_id:
            job = get_job(job_id)
            if job:
                return job

    tenant_id = lead.get('tenant_id')
    jobs = [
        j for j in store.list('jobs')
        if j.get('lead_id') == lead.get('id') and _same_tenant_or_legacy(j, tenant_id)
    ]
    if not jobs:
        return None
    jobs.sort(key=lambda j: (j.get('created') or '', j.get('id') or ''))
    return jobs[-1]


def _converted_job_for_lead(lead, jobs_cache=None):
    if not lead:
        return None

    for key in ('lead_id_job', 'job_id', 'converted_to_job', 'converted_job_id'):
        job_id = lead.get(key)
        if job_id:
            job = get_job(job_id)
            if job:
                return job

    status = str(lead.get('status') or lead.get('estado') or '').strip().lower()
    if status in {'convertido', 'converted', 'aceptado', 'accepted'}:
        return _find_job_for_lead(lead)

    accepted_statuses = {'confirmado', 'confirmed', 'listo', 'completed', 'archivado', 'archived'}
    tenant_id = lead.get('tenant_id')
    # jobs_cache: la lista de jobs ya leida por quien llama. Sin esto, la
    # pantalla de leads releia la tabla ENTERA de jobs una vez por lead
    # (leads_list -> compute_workflow_steps_for_lead -> _lead_is_converted
    # -> aca). Con 200 leads eran 200 lecturas de disco por pagina.
    _todos = store.list('jobs') if jobs_cache is None else jobs_cache
    jobs = [
        j for j in _todos
        if j.get('lead_id') == lead.get('id') and _same_tenant_or_legacy(j, tenant_id)
    ]
    accepted_jobs = [
        j for j in jobs
        if str(j.get('status') or '').strip().lower() in accepted_statuses or j.get('accepted_quote_id')
    ]
    if not accepted_jobs:
        return None
    accepted_jobs.sort(key=lambda j: (j.get('created') or '', j.get('id') or ''))
    return accepted_jobs[-1]


def _lead_is_converted(lead, jobs_cache=None):
    return bool(_converted_job_for_lead(lead, jobs_cache))


def _lead_is_open(lead):
    status = str(lead.get('status') or lead.get('estado') or '').strip().lower()
    if status in {'convertido', 'converted', 'perdido', 'lost', 'archivado', 'archived'}:
        return False
    return not _lead_is_converted(lead)


def _open_leads(tenant_id=None):
    return [lead for lead in list_leads(tenant_id) if _lead_is_open(lead)]


def _job_canonical_score(job, lead=None):
    status = str(job.get('status') or '').strip().lower()
    explicit_ids = {
        (lead or {}).get('lead_id_job'),
        (lead or {}).get('job_id'),
        (lead or {}).get('converted_to_job'),
        (lead or {}).get('converted_job_id'),
    }
    score = 0
    if job.get('id') in explicit_ids:
        score += 100
    if status in {'confirmado', 'confirmed', 'listo', 'completed'}:
        score += 50
    if job.get('accepted_quote_id'):
        score += 40
    if status in {'cotizando', 'quote'}:
        score += 10
    if status in {'archivado', 'archived'}:
        score -= 10
    return (score, str(job.get('created') or ''), str(job.get('id') or ''))


def _canonical_jobs(jobs=None):
    jobs = list(jobs) if jobs is not None else list_jobs()
    leads_by_id = {lead.get('id'): lead for lead in list_leads()}
    by_lead = {}
    without_lead = []
    for job in jobs:
        lead_id = job.get('lead_id')
        if not lead_id:
            without_lead.append(job)
            continue
        lead = leads_by_id.get(lead_id)
        current = by_lead.get(lead_id)
        if not current or _job_canonical_score(job, lead) > _job_canonical_score(current, lead):
            by_lead[lead_id] = job
    return without_lead + list(by_lead.values())


def _canonical_clients(clients=None):
    clients = list(clients) if clients is not None else list_clients()
    # "Esta persona esta metida en una boda real" es el criterio que decide
    # cual de dos fichas duplicadas (mismo email, del import de Studio
    # Ninja) es la buena. Antes solo contaba el rol `principal`, asi que si
    # la ficha real estaba enlazada como `pareja` perdia contra el duplicado
    # huerfano y la lista mostraba la ficha equivocada.
    canonical_job_client_ids = set(_jobs_por_cliente().keys())
    by_key = {}
    for client in clients:
        key = _norm_email(client.get('email')) or _norm_phone(client.get('phone')) or client.get('id')
        current = by_key.get(key)
        score = (
            1 if client.get('id') in canonical_job_client_ids else 0,
            str(client.get('created') or ''),
            str(client.get('id') or ''),
        )
        current_score = (
            1 if (current or {}).get('id') in canonical_job_client_ids else 0,
            str((current or {}).get('created') or ''),
            str((current or {}).get('id') or ''),
        )
        if not current or score > current_score:
            by_key[key] = client
    return list(by_key.values())


def _ensure_job_for_lead(lead, client_id, quote=None, status='Confirmado'):
    import uuid
    tenant_id = lead.get('tenant_id') or get_current_tenant_id()
    today = datetime.now().isoformat()[:10]
    existing = _find_job_for_lead(lead)

    if existing:
        changed = False
        for key, value in {
            'client_id': client_id,
            'lead_id': lead.get('id'),
            'tenant_id': tenant_id,
        }.items():
            if value and not existing.get(key):
                existing[key] = value
                changed = True
        if quote and not existing.get('accepted_quote_id'):
            existing['accepted_quote_id'] = quote.get('id')
            changed = True
        if status and existing.get('status') != status:
            existing['status'] = status
            changed = True
        if status == 'Confirmado':
            current_progress = int(existing.get('workflow_progress') or 0)
            if current_progress < 12:
                existing['workflow_progress'] = 12
                changed = True
        if changed:
            upsert_job(existing)
        return existing, False

    nombre_completo = lead.get('nombre', 'Cliente')
    price_total = float((quote or {}).get('precio_total') or 15000)
    plan_pago = int((quote or {}).get('plan_pago') or 1)
    cuota_monto = round(price_total / plan_pago, 2) if plan_pago else price_total
    job = {
        'id': 'boda-' + uuid.uuid4().hex[:8],
        'nombre': f'Boda {nombre_completo}',
        'boda_date': lead.get('fecha_tentativa') or today,
        'status': status,
        'workflow_progress': 12 if status == 'Confirmado' else 0,
        # Antes hardcodeado a 'ASTRAL WEDDINGS' sin importar el tenant real
        # -- causaba que un job de CUALQUIER empresa quedara marcado como
        # Astral. Ahora se resuelve por la identidad canonica del tenant_id
        # (src/tenant_brand_map.py), nunca por un string fijo.
        'empresa': _brand_display_name_for_tenant(tenant_id),
        'type': lead.get('tipo_evento', 'Boda'),
        'location': lead.get('locacion', ''),
        'package': (quote or {}).get('paquete_nombre', 'Basico'),
        'client_id': client_id,
        'lead_id': lead.get('id'),
        'accepted_quote_id': (quote or {}).get('id'),
        'price_total': price_total,
        'price_paid': 0,
        'plan_pago': plan_pago,
        'cuota_monto': cuota_monto,
        'tenant_id': tenant_id,
        'created': today,
    }
    upsert_job(job)
    return job, True


def _add_one_month(dt):
    """Suma un mes a una fecha sin depender de python-dateutil."""
    month = dt.month + 1
    year = dt.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    import calendar as _cal
    day = min(dt.day, _cal.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _ensure_payments_for_quote(quote, client_id, job_id, tenant_id=None):
    import uuid
    if not quote:
        return [], False

    tenant_id = tenant_id or get_current_tenant_id()

    # Guardia 1: ya existe un CALENDARIO ACTIVO para esta identidad logica
    # (tenant + job + cotizacion). Aceptar dos veces la misma cotizacion no
    # puede producir dos calendarios -- es la sobrefacturacion que ya
    # ocurrio una vez (ver quarantine Camila/Daniel).
    schedule_activo = _active_schedule_for(tenant_id, job_id, quote.get('id'))
    if schedule_activo:
        cuotas_previas = [p for p in store.list('payments')
                          if p.get('id') in (schedule_activo.get('payment_ids') or [])]
        return [p.get('invoice_id') for p in cuotas_previas if p.get('invoice_id')], False

    # Guardia 2 (defensa en profundidad): cuotas sueltas de antes de que
    # existieran los schedules.
    existing = [
        p for p in store.list('payments')
        if p.get('job_id') == job_id
        and p.get('tipo') != 'team_payment'
        and p.get('quote_id') == quote.get('id')
    ]
    if existing:
        # Se registra el calendario legacy para que quede visible con
        # estado explicito, sin tocar ni reasignar los pagos.
        _crear_schedule(
            tenant_id, job_id, quote.get('id'),
            total_plan=sum(_row_original_amount(p) for p in existing),
            cuotas=len(existing),
            suma_cuotas=sum(_row_original_amount(p) for p in existing),
            price_total=(get_job(job_id) or {}).get('price_total'),
            payment_ids=[p.get('id') for p in existing],
        )
        return [p.get('invoice_id') for p in existing if p.get('invoice_id')], False

    plan_pago = max(int(quote.get('plan_pago') or 1), 1)
    total = float(quote.get('precio_total') or 0)
    base = round(total / plan_pago, 2)
    invoice_ids = []
    payment_ids = []
    sum_generada = 0.0

    # Calendario de pagos inteligente: 1era cuota el dia de aceptacion,
    # ultima cuota 1 mes despues de la boda. Para 3 cuotas, la segunda va a
    # mitad exacta entre aceptacion y boda; para 4/5, las cuotas intermedias
    # se reparten de forma equidistante hasta la fecha final.
    due_dates = None
    job_for_dates = get_job(job_id) if job_id else None
    boda_date_str = job_for_dates.get('boda_date') if job_for_dates else None
    if boda_date_str:
        try:
            boda_date = datetime.strptime(boda_date_str, '%Y-%m-%d')
            today_dt = datetime.now()
            last_due = _add_one_month(boda_date)
            if last_due > today_dt:
                if plan_pago == 1:
                    due_dates = [today_dt.strftime('%Y-%m-%d')]
                elif plan_pago == 3 and boda_date > today_dt:
                    middle = today_dt + timedelta(seconds=(boda_date - today_dt).total_seconds() / 2)
                    due_dates = [
                        today_dt.strftime('%Y-%m-%d'),
                        middle.strftime('%Y-%m-%d'),
                        last_due.strftime('%Y-%m-%d'),
                    ]
                else:
                    span = (last_due - today_dt).total_seconds()
                    due_dates = [
                        (today_dt + timedelta(seconds=span * i / (plan_pago - 1))).strftime('%Y-%m-%d')
                        for i in range(plan_pago)
                    ]
        except ValueError:
            due_dates = None

    for i in range(1, plan_pago + 1):
        invoice_id = 'INV-' + uuid.uuid4().hex[:6].upper()
        amount = base if i < plan_pago else round(total - base * (plan_pago - 1), 2)
        sum_generada += amount
        due_date = due_dates[i - 1] if due_dates else (datetime.now() + timedelta(days=30 * (i - 1))).strftime('%Y-%m-%d')
        invoice = {
            'id': 'pay-' + uuid.uuid4().hex[:8],
            'invoice_id': invoice_id,
            'client_id': client_id,
            'job_id': job_id,
            'quote_id': quote.get('id'),
            'concepto': f'Cotizacion {quote.get("paquete_nombre", "")} - Pago {i} de {plan_pago}',
            'amount': amount,
            'original_amount': amount,
            'due_date': due_date,
            'status': 'Pendiente',
            'cuota': f'{i}/{plan_pago}',
            'tenant_id': tenant_id,
        }
        store.upsert('payments', invoice)
        invoice_ids.append(invoice_id)
        payment_ids.append(invoice['id'])

    # Se registra el calendario con estado explicito. Para un schedule
    # generado por el CRM a partir de una cotizacion valida, la suma DEBE
    # cuadrar exacto -- si no cuadra queda anotado en `avisos` y en el log
    # de seguridad, pero no se corrige solo.
    _crear_schedule(
        tenant_id, job_id, quote.get('id'),
        total_plan=total,
        cuotas=plan_pago,
        suma_cuotas=sum_generada,
        price_total=(job_for_dates or {}).get('price_total'),
        payment_ids=payment_ids,
    )

    return invoice_ids, True


def _accept_quote_for_existing_job(quote):
    job = get_job(quote.get('job_id', ''))
    if not job and quote.get('lead_id'):
        lead = get_lead(quote.get('lead_id', ''))
        if lead:
            result = _convert_lead_to_job(lead, quote=quote, status='Confirmado', create_payments=True)
            return result
    if not job:
        return {'job': None, 'client': None, 'invoice_ids': [], 'invoices_created': False}

    client = get_client(quote.get('client_id') or job.get('client_id', ''))
    if not client and job.get('lead_id'):
        lead = get_lead(job.get('lead_id'))
        if lead:
            client, _ = _ensure_client_for_lead(lead)
    if not client:
        return {'job': job, 'client': None, 'invoice_ids': [], 'invoices_created': False}

    quote['status'] = 'Aceptada'
    quote['aceptada_en'] = quote.get('aceptada_en') or date.today().isoformat()
    quote['client_id'] = client['id']
    quote['job_id'] = job['id']
    store.upsert('quotes', quote)

    job['accepted_quote_id'] = quote.get('id')
    job['package'] = quote.get('paquete_nombre') or job.get('package') or ''
    job['price_total'] = float(quote.get('precio_total') or job.get('price_total') or 0)
    job['plan_pago'] = int(quote.get('plan_pago') or job.get('plan_pago') or 1)
    job['cuota_monto'] = float(quote.get('cuota_monto') or (job['price_total'] / max(job['plan_pago'], 1)))
    if job.get('status') in ('Cotizando', 'Nuevo', ''):
        job['status'] = 'Confirmado'
    upsert_job(job)

    invoice_ids, invoices_created = _ensure_payments_for_quote(
        quote,
        client['id'],
        job['id'],
        quote.get('tenant_id') or job.get('tenant_id') or get_current_tenant_id(),
    )
    return {
        'job': job,
        'client': client,
        'invoice_ids': invoice_ids,
        'invoices_created': invoices_created,
    }


def _ensure_production_workflow_for_job(lead, job):
    existing = _workflow_instances_seguras(subject_type='job', subject_id=job['id'])
    if existing:
        return existing[0].id, False
    instance = trigger_workflow_for_quote_accepted(
        lead.get('id'), job.get('nombre') or lead.get('nombre', 'Job'), job['id'],
        tenant_id=job.get('tenant_id') or lead.get('tenant_id'),
    )
    return instance.id, True


def _complete_original_lead_workflow(lead, job):
    instance = _workflow_instance_for('lead', lead.get('id', ''))
    if not instance:
        return

    for step in LEAD_WORKFLOW().steps:
        instance.step_states[step.id] = StepStatus.DONE
        instance.step_results.setdefault(step.id, 'Closed because lead was converted into a job')
    instance.status = WorkflowStatus.COMPLETED
    instance.current_step_id = None
    workflow_engine._log(instance, 'workflow.completed', f'Lead converted into job {job.get("id", "")}')
    workflow_engine._save_to_storage()


def _activate_job_workflow_start(job):
    instance = _workflow_instance_for('job', job.get('id', ''))
    if not instance:
        return

    instance.step_states['job_accepted'] = StepStatus.DONE
    instance.step_results['job_accepted'] = 'Lead converted into job'
    next_step = next(
        (step for step in PRODUCTION_WORKFLOW().steps
         if step.id != 'job_accepted' and instance.step_states.get(step.id) != StepStatus.DONE),
        None,
    )
    instance.current_step_id = next_step.id if next_step else None
    workflow_engine._log(instance, 'step.done', 'Trabajo aceptado: lead convertido en job')
    workflow_engine._save_to_storage()


def _convert_lead_to_job(lead, quote=None, status='Confirmado', create_payments=True):
    """Conversion lead -> job, EXACTAMENTE UNA VEZ por lead.

    Envoltorio de exclusion mutua sobre `_convert_lead_to_job_unlocked()`.
    La identidad de la conversion (tenant_id + lead_id) es PRIMARY KEY en
    SQLite (ver src/conversion_registry.py), asi que de N llamadas
    simultaneas solo una entra a crear; las demas esperan y devuelven el
    MISMO job.

    Antes de esto, el unico guardia era `_find_job_for_lead()` dentro de
    `_ensure_job_for_lead()`: un "leer y luego decidir" que cierra el caso
    secuencial (doble click) pero deja abierta la ventana entre la lectura
    y la escritura. Con 5 peticiones simultaneas dos podian pasar el check
    antes de que ninguna hubiera guardado -- que es literalmente como se
    produjo el incidente de Camila Rios (4 jobs, 4 workflows para un mismo
    lead).

    El perdedor NO reejecuta la creacion: vuelve a entrar por el camino
    idempotente cuando el ganador YA publico su job_id, asi que todos los
    `_ensure_*` encuentran lo existente y no duplican ningun efecto
    colateral (workflow, calendario de pagos, cuestionario)."""
    from src import conversion_registry

    tenant_id = lead.get('tenant_id') or get_current_tenant_id()
    lead_id = lead.get('id')

    if not lead_id:
        # Sin lead_id no hay identidad de conversion que registrar.
        return _convert_lead_to_job_unlocked(
            lead, quote=quote, status=status, create_payments=create_payments)

    rol, job_id_ganador = conversion_registry.claim(tenant_id, lead_id)

    if rol == 'loser':
        # El ganador ya termino y publico su job. Reentrar por el camino
        # idempotente: todo existe, no se crea nada nuevo.
        logger.info('Conversion concurrente para lead %s: se devuelve el job '
                    'ganador %s sin crear uno nuevo.', lead_id, job_id_ganador)
        return _convert_lead_to_job_unlocked(
            lead, quote=quote, status=status, create_payments=create_payments)

    if rol == 'timeout':
        # El ganador reclamo y nunca publico (murio). Se sigue por el
        # camino normal, que conserva su guardia de aplicacion.
        log_security_event('CONVERSION_CLAIM_TIMEOUT', tabla='jobs',
                           registro=lead_id, cuenta_activa=tenant_id)
        return _convert_lead_to_job_unlocked(
            lead, quote=quote, status=status, create_payments=create_payments)

    # rol == 'winner'
    try:
        result = _convert_lead_to_job_unlocked(
            lead, quote=quote, status=status, create_payments=create_payments)
    except Exception:
        # Sin esto, una conversion fallida dejaria la clave reclamada para
        # siempre y ningun reintento futuro podria volver a intentarla.
        conversion_registry.release(tenant_id, lead_id)
        raise

    conversion_registry.finalize(tenant_id, lead_id, result['job']['id'])
    return result


def _convert_lead_to_job_unlocked(lead, quote=None, status='Confirmado', create_payments=True):
    """Cuerpo real de la conversion. NO llamar directo desde una ruta:
    usar `_convert_lead_to_job()`, que agrega la exclusion mutua."""
    client, client_created = _ensure_client_for_lead(lead)
    job, job_created = _ensure_job_for_lead(lead, client['id'], quote=quote, status=status)
    if job.get('client_id') and job.get('client_id') != client['id']:
        job_client = get_client(job['client_id'])
        if job_client:
            client = job_client
            client_created = False
    invoice_ids, invoices_created = _ensure_payments_for_quote(
        quote, client['id'], job['id'], lead.get('tenant_id') or get_current_tenant_id()
    ) if create_payments else ([], False)

    if quote:
        quote['status'] = 'Aceptada'
        quote['aceptada_en'] = quote.get('aceptada_en') or datetime.now().isoformat()[:10]
        quote['job_id'] = job['id']
        quote['client_id'] = client['id']
        store.upsert('quotes', quote)

        for other in store.list('quotes'):
            if other.get('lead_id') == lead.get('id') and other.get('id') != quote.get('id') and other.get('status') not in ('Aceptada', 'Superada'):
                other['status'] = 'Superada'
                other['superseded_by_quote_id'] = quote.get('id')
                store.upsert('quotes', other)

    lead['status'] = 'Convertido'
    lead['mail_status'] = 'ABIERTO'
    lead['next_task'] = 'Boda el ' + (job.get('boda_date') or '')
    lead['lead_id_job'] = job['id']
    lead['job_id'] = job['id']
    lead['converted_to_job'] = job['id']
    lead['converted_at'] = lead.get('converted_at') or datetime.now().isoformat()[:10]
    lead['client_id'] = client['id']
    upsert_lead(lead)

    workflow_instance_id, workflow_created = _ensure_production_workflow_for_job(lead, job)
    _complete_original_lead_workflow(lead, job)
    _activate_job_workflow_start(job)
    if job_created:
        # Kevin: 'al crear el job creo el cuestionario deberia estar creado'
        # -- se crea de una vez en Draft (sin mandar nada todavia); el envio
        # real lo dispara _auto_fire_due_job_steps() cuando llegue la fecha
        # del step "Cuestionario cliente" del workflow.
        try:
            _create_job_questionnaire(job, send_email=False)
        except Exception as e:
            logger.error(f'No se pudo pre-crear el cuestionario del job {job.get("id")}: {e}')
    return {
        'client': client,
        'job': job,
        'invoice_ids': invoice_ids,
        'workflow_instance_id': workflow_instance_id,
        'client_created': client_created,
        'job_created': job_created,
        'invoices_created': invoices_created,
        'workflow_created': workflow_created,
    }


def _client_detail_view_model(client):
    full_name = (f"{client.get('first_name', '')} {client.get('last_name', '')}").strip()
    return {
        'id': client.get('id'),
        'Nombre': client.get('nombre') or full_name or 'Cliente',
        'Email': client.get('email'),
        'Teléfono': client.get('phone'),
        'Teléfono secundario': client.get('phone_secondary'),
        'Estado': client.get('estado') or 'Activo',
        'Fuente': client.get('source') or client.get('fuente'),
        'Tags': client.get('tags') or [],
        'Fecha primer contacto': client.get('created'),
        'Último acceso': client.get('last_access'),
        'Dirección facturación': client.get('address'),
        'Notas': client.get('notes') or client.get('notas'),
        'Portal URL': client.get('portal_url') or f"/portal/{client.get('id')}",
        'Galería URL': client.get('galeria_url'),
        'Galería contraseña cliente': client.get('galeria_cliente_pwd'),
        'Galería contraseña invitado': client.get('galeria_invitado_pwd'),
        'Token de acceso': client.get('token_acceso'),
        'Carpeta Drive': client.get('carpeta_drive'),
    }


def _job_detail_view_model(job):
    return {
        'id': job.get('id'),
        'BODA': job.get('nombre'),
        'Fecha del evento': job.get('boda_date'),
        'Estado': job.get('status'),
    }

# Mantener compatibilidad con codigo viejo (sin tenant)
def list_all_leads():
    return store.list('leads')

def list_all_jobs():
    return store.list('jobs')

def list_all_clients():
    return store.list('clients')

def list_all_payments():
    return store.list('payments')

# ============================================================


# ============================================================
# CONTEXT PROCESSOR: tenant actual para todos los templates
# ============================================================
import json as _json

def _build_recent_notifications(tenant_id):
    """Leads y correos recientes para la campana de notificaciones. Se usa
    tanto en el render inicial de la pagina como en /api/notifications/recent
    (que el JS del bell consulta cada rato) para que quede reflejado un lead
    nuevo sin tener que recargar la pagina entera."""
    recent_notifications = []
    try:
        latest_leads = sorted(
            _open_leads(tenant_id),
            key=lambda lead: str(lead.get('created') or lead.get('updated') or ''),
            reverse=True
        )[:3]
        for lead in latest_leads:
            name = lead.get('nombre') or 'Nuevo lead'
            recent_notifications.append({
                'id': f"lead-{lead.get('id')}",
                'type': 'lead',
                'title': f'New Lead from your FORMULARIO DE CONTACTO {_brand_display_name_for_tenant(tenant_id).upper()}: {name}',
                'date': lead.get('created') or datetime.now().strftime('%d %b %Y'),
                'time': lead.get('created_time') or '',
                'age': lead.get('age') or '',
                'url': f"/leads/{lead.get('id')}",
            })

        mail_candidates = []
        for m in store.list('mail_log'):
            lead = get_lead(m.get('lead_id', '')) if m.get('lead_id') else None
            job = get_job(m.get('job_id', '')) if m.get('job_id') else None
            if not lead and not job:
                continue  # el lead/job fue borrado -- no mostrar un link muerto
            if not _same_tenant_or_legacy(lead or job, tenant_id):
                continue
            mail_candidates.append(m)
        latest_mail = sorted(
            mail_candidates,
            key=lambda mail: str(mail.get('sent_at') or mail.get('opened_at') or ''),
            reverse=True
        )[:2]
        for mail in latest_mail:
            if mail.get('lead_id'):
                mail_url = f"/leads/{mail.get('lead_id')}"
            elif mail.get('job_id'):
                mail_url = f"/jobs/{mail.get('job_id')}"
            else:
                mail_url = ''
            recent_notifications.append({
                'id': f"mail-{mail.get('id')}",
                'type': 'mail',
                'title': f"New Email activity: {mail.get('subject') or 'Email'}",
                'date': (mail.get('sent_at') or '')[:10] or datetime.now().strftime('%d %b %Y'),
                'time': '',
                'age': '',
                'url': mail_url,
            })
    except Exception:
        recent_notifications = []
    return recent_notifications[:5]


@app.context_processor
def inject_tenant():
    """Inyecta el tenant actual (el de la sesion logueada, no un query
    param) en todos los templates. Nunca cae al 'primer tenant de la
    lista' como placeholder -- eso filtraria el nombre/color de OTRA
    cuenta a una sesion sin tenant resuelto (login, paginas publicas)."""
    tenant_id = get_current_tenant_id()
    tenants = store.list('tenants')
    current = next((t for t in tenants if t['id'] == tenant_id), None) or {
        'id': None, 'name': 'Flow CRM', 'color': '#2F7D73', 'logo_letter': 'F',
    }
    recent_notifications = _build_recent_notifications(tenant_id) if tenant_id else []

    from src import gmail_delivery
    try:
        gmail_connected = gmail_delivery.is_connected()
    except Exception:
        gmail_connected = False

    return {
        'current_tenant': current,
        # Nunca se expone la lista completa de tenants a una sesion -- solo
        # el propio. Nada la usa hoy (el selector es un <span> decorativo),
        # pero mejor no dejar en el contexto de Jinja los nombres de las
        # otras 2 cuentas.
        'all_tenants': [current] if current.get('id') else [],
        'recent_notifications': recent_notifications,
        'unread_notifications_count': min(len(recent_notifications), 59),
        'gmail_connected': gmail_connected,
    }


@app.route('/api/notifications/recent')
def api_notifications_recent():
    """Lo consulta el JS de la campana de notificaciones cada cierto tiempo
    y al abrirla, para que un lead nuevo se vea reflejado sin recargar."""
    tenant_id = get_current_tenant_id()
    notifications = _build_recent_notifications(tenant_id) if tenant_id else []
    return jsonify({'ok': True, 'notifications': notifications, 'count': min(len(notifications), 59)})

# HELPERS - Data access via JSON store (NO Notion)
# ============================================================

def list_calendar():
    return store.list('calendar')

def get_settings(tenant_id=None):
    """tenant_id explicito para las rutas publicas (formulario de contacto)
    que no tienen sesion -- sin eso, get_tenant_dict caeria al archivo
    compartido en vez de la config de la marca correcta."""
    return store.get_tenant_dict('settings', tenant_id=tenant_id)


def _package_config_view(package):
    return {
        'id': package.get('id'),
        'Name': package.get('name') or package.get('Name'),
        # Un paquete sin marca NO puede caer en Astral por default: si lo
        # crea Norkevin Photography, quedaria etiquetado con la otra
        # empresa. Se resuelve por el tenant activo, como todo lo demas.
        'Marca': (package.get('marca') or package.get('Marca')
                  or _brand_display_name_for_tenant(get_current_tenant_id())),
        'Precio Q': package.get('price') or package.get('Precio Q') or 0,
        'Activo': package.get('active', package.get('Activo', True)),
        'Notas': package.get('description') or package.get('Notas') or '',
    }


def _default_config_items(kind):
    # Los items semilla se etiquetan con la marca de la cuenta que los pide.
    # Antes decian 'ASTRAL WEDDINGS' fijo: al abrir Configuracion por
    # primera vez con Norkevin Photography, sus cuentas bancarias y sus
    # reglas de pago a equipo aparecian con el nombre de la otra empresa.
    _marca = _brand_display_name_for_tenant(get_current_tenant_id())
    if kind == 'cuentas':
        return [
            {'id': 'cuenta-transferencia', 'Name': 'Transferencia bancaria', 'Marca': _marca, 'Notas': 'Cuenta principal para anticipos y pagos finales', 'Activo': True},
        ]
    if kind == 'reglas':
        return [
            {'id': 'regla-foto-principal', 'Name': 'Fotografo principal', 'Marca': _marca, 'Porcentaje': 20, 'Notas': 'Referencia inicial para liquidacion de equipo', 'Activo': True},
            {'id': 'regla-asistente', 'Name': 'Asistente', 'Marca': _marca, 'Porcentaje': 10, 'Notas': 'Referencia inicial para apoyo de evento', 'Activo': True},
        ]
    if kind == 'fuentes':
        names = ['Instagram', 'Facebook', 'WhatsApp', 'Recomendacion', 'Google', 'Wedding Planner', 'Web']
        for lead in store.list('leads'):
            if lead.get('fuente') and lead['fuente'] not in names:
                names.append(lead['fuente'])
        return [{'id': 'fuente-' + re.sub(r'[^a-z0-9]+', '-', n.lower()).strip('-'), 'Name': n, 'Marca': 'Global', 'Activo': True} for n in names]
    if kind == 'datos':
        company = get_settings().get('company', {})
        return [
            {'id': 'dato-nombre-estudio', 'Name': 'Nombre del estudio', 'Notas': company.get('name', 'ASTRAL WEDDINGS Guatemala'), 'Activo': True},
            {'id': 'dato-email-estudio', 'Name': 'Email principal', 'Notas': company.get('email', ''), 'Activo': True},
            {'id': 'dato-telefono-estudio', 'Name': 'Telefono principal', 'Notas': company.get('phone', ''), 'Activo': True},
        ]
    return []


def _config_items(kind, tenant_id=None):
    settings = get_settings(tenant_id=tenant_id)
    saved = (settings.get('config') or {}).get(kind) or []
    by_id = {item.get('id'): dict(item) for item in _default_config_items(kind)}
    for item in saved:
        if item.get('id') in by_id:
            by_id[item['id']].update(item)
        elif item.get('id'):
            by_id[item['id']] = dict(item)
    return list(by_id.values())


def _save_config_items(kind, items):
    settings = get_settings()
    settings.setdefault('config', {})[kind] = items
    store.save_tenant_dict('settings', settings)


def _upsert_config_item(kind, item_id, data):
    import uuid
    items = _config_items(kind)
    if not item_id:
        item_id = f"{kind[:-1] if kind.endswith('s') else kind}-{uuid.uuid4().hex[:8]}"
        item = {'id': item_id, 'Name': data.get('Name') or 'Nuevo item', 'Activo': data.get('Activo', True)}
        items.append(item)
    else:
        item = next((x for x in items if x.get('id') == item_id), None)
        if not item:
            item = {'id': item_id, 'Name': data.get('Name') or item_id, 'Activo': True}
            items.append(item)
    item.update({k: v for k, v in data.items() if v is not None})
    _save_config_items(kind, items)
    return item


SOURCE_COLORS = ['#7d83f2', '#20a7dc', '#c65a09', '#10b981', '#f2c94c', '#94a3b8', '#8b5cf6', '#ef4444']


def _configured_lead_sources(include_inactive=False, tenant_id=None):
    sources = []
    for idx, item in enumerate(_config_items('fuentes', tenant_id=tenant_id)):
        name = (item.get('Name') or item.get('name') or '').strip()
        if not name:
            continue
        active = item.get('Activo', True) is not False
        if not include_inactive and not active:
            continue
        sources.append({
            'id': item.get('id') or ('fuente-' + re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')),
            'name': name,
            'label': name,
            'active': active,
            'color': item.get('Color') or SOURCE_COLORS[idx % len(SOURCE_COLORS)],
        })
    return sources


def _workflow_state_value(value):
    if value is None:
        return None
    if hasattr(value, 'value'):
        return value.value
    text = str(value)
    if '.' in text:
        text = text.rsplit('.', 1)[-1]
    return text.lower()


def _workflow_instance_for(subject_type, subject_id, job_ids_cache=None, lead_ids_cache=None, tenant_id=None):
    instances = _workflow_instances_seguras(subject_type=subject_type, subject_id=subject_id,
                                             job_ids_cache=job_ids_cache, lead_ids_cache=lead_ids_cache,
                                             tenant_id=tenant_id)
    return instances[0] if instances else None


def compute_workflow_steps_for_lead(lead, jobs_cache=None, job_ids_cache=None, lead_ids_cache=None, tenant_id=None):
    from datetime import datetime, timedelta
    tmpl = LEAD_WORKFLOW()
    try:
        trigger_at = datetime.fromisoformat(lead['created'].replace('Z', '+00:00').split('T')[0] + 'T00:00:00')
    except Exception:
        trigger_at = datetime.now()
    now = datetime.now()
    instance = _workflow_instance_for('lead', lead.get('id', ''), job_ids_cache=job_ids_cache, lead_ids_cache=lead_ids_cache, tenant_id=tenant_id)
    state_map = getattr(instance, 'step_states', {}) if instance else {}
    result_map = getattr(instance, 'step_results', {}) if instance else {}
    force_done = _lead_is_converted(lead, jobs_cache)
    steps = []
    for step in tmpl.steps:
        scheduled = trigger_at + timedelta(minutes=step.offset_minutes)
        stored_status = _workflow_state_value(state_map.get(step.id))
        if force_done:
            status = 'done'
            executed_at = lead.get('converted_at') or trigger_at.isoformat()
        elif stored_status:
            status = stored_status
            executed_at = trigger_at.isoformat() if status == 'done' else None
        elif scheduled <= now:
            status = 'done'
            executed_at = scheduled.isoformat()
        else:
            status = 'pending'
            executed_at = None
        steps.append({
            'id': step.id,
            'name': step.name,
            'description': step.description,
            'email_template_id': step.email_template_id,
            'action_type': step.action_type.value if hasattr(step.action_type, 'value') else str(step.action_type),
            'scheduled': scheduled.isoformat(),
            'executed_at': executed_at,
            'status': status,
            'result': result_map.get(step.id),
        })
    done = sum(1 for s in steps if s['status'] == 'done')
    progress = round(done * 100 / len(steps)) if steps else 0
    return steps, progress, tmpl.name


def _step_scheduled_for_job(step, trigger_at, boda_date):
    """step.offset_minutes (en models.py) es una aproximacion cruda para
    steps 'after_event' -- no conoce la fecha real de la boda, asi que
    cuenta el amount/unit desde la creacion del job en vez de desde boda_date.
    Con una boda real (normalmente meses/un anio despues del job), eso hace
    que p.ej. 'Cuestionario cliente: 1 mes antes de la boda' se calcule casi
    de inmediato en vez de 1 mes antes de la boda de verdad. Si tenemos
    boda_date, calculamos el offset desde ahi en su lugar."""
    from datetime import timedelta
    dd = step.due_date
    if dd.mode == 'after_event' and boda_date:
        mult_days = {
            'minutes': 1 / (60 * 24), 'hours': 1 / 24, 'days': 1,
            'weeks': 7, 'months': 30,
        }.get(dd.unit, 1)
        delta = timedelta(days=dd.amount * mult_days)
        if dd.relative_to == 'before_boda':
            return boda_date - delta
        return boda_date + delta
    return trigger_at + timedelta(minutes=step.offset_minutes)


def compute_workflow_steps_for_job(job, job_ids_cache=None, lead_ids_cache=None, tenant_id=None):
    from datetime import datetime, timedelta
    tmpl = PRODUCTION_WORKFLOW()
    try:
        trigger_at = datetime.fromisoformat(job['created'].replace('Z', '+00:00').split('T')[0] + 'T00:00:00')
    except Exception:
        trigger_at = datetime.now()
    boda_date = None
    if job.get('boda_date'):
        try:
            boda_date = datetime.strptime(job['boda_date'], '%Y-%m-%d')
        except ValueError:
            boda_date = None
    instance = _workflow_instance_for('job', job.get('id', ''), job_ids_cache=job_ids_cache, lead_ids_cache=lead_ids_cache, tenant_id=tenant_id)
    state_map = getattr(instance, 'step_states', {}) if instance else {}
    result_map = getattr(instance, 'step_results', {}) if instance else {}
    steps = []
    for step in tmpl.steps:
        scheduled = _step_scheduled_for_job(step, trigger_at, boda_date)
        stored_status = _workflow_state_value(state_map.get(step.id))
        status = stored_status or 'pending'
        executed_at = trigger_at.isoformat() if status == 'done' else None
        steps.append({
            'id': step.id,
            'name': step.name,
            'description': step.description,
            'email_template_id': step.email_template_id,
            'action_type': step.action_type.value if hasattr(step.action_type, 'value') else str(step.action_type),
            'scheduled': scheduled.isoformat(),
            'executed_at': executed_at,
            'status': status,
            'result': result_map.get(step.id),
        })
    # 'skipped' cuenta para el progreso igual que 'done' -- un step saltado
    # a proposito (menu de 3 puntos: "por si no quieres mandar contrato")
    # ya no aparece como pendiente en jobs_list() (next_task pasa a
    # 'Completado'), pero si solo se contara 'done' aca la barra quedaria
    # contradictoriamente vacia/baja mientras el texto dice terminado.
    done = sum(1 for s in steps if s['status'] in ('done', 'skipped'))
    progress = round(done * 100 / len(steps)) if steps else 0
    return steps, progress, tmpl.name


def days_until(date_str):
    from datetime import datetime, date
    try:
        d = datetime.strptime(date_str, '%Y-%m-%d').date()
        return (d - date.today()).days
    except Exception:
        return None

# ============================================================
# HELPERS
# ============================================================

def q_money(v) -> str:
    amount = coerce_amount(v, 0.0)
    return f"Q{amount:,.0f}".replace(',', ',')


def coerce_amount(value, default=0.0) -> float:
    """Convierte montos reales del CRM a float sin romper vistas.

    En produccion hay datos que a veces vienen como:
    - numeros puros
    - strings con prefijo de moneda: `Q74,982.15`
    - strings vacios o campos parciales

    El dashboard y varias vistas no deben caerse por eso.
    """
    if value is None:
        return float(default)
    if isinstance(value, (int, float)):
        return float(value)

    raw = str(value).strip()
    if not raw:
        return float(default)

    negative = raw.startswith('(') and raw.endswith(')')
    cleaned = re.sub(r'[^0-9,.\-]', '', raw)
    cleaned = cleaned.replace(',', '')
    if cleaned in ('', '-', '.', '-.'):
        return float(default)

    try:
        parsed = float(cleaned)
    except Exception:
        return float(default)
    return -abs(parsed) if negative else parsed


def parse_date(s) -> str:
    """YYYY-MM-DD → 'Sábado 21 de noviembre 2026'"""
    if not s: return ''
    try:
        d = date.fromisoformat(s)
        dias = ['Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo']
        meses = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre']
        return f"{dias[d.weekday()]} {d.day} de {meses[d.month-1]} {d.year}"
    except:
        return s



def fmt_dt(s) -> str:
    if not s: return ''
    try:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        return d.strftime('%Y-%m-%d %H:%M')
    except:
        return s


JOB_WORKFLOW_STEPS = [
    ('Lead', 'lead'),
    ('Cotizando', 'quote'),
    ('Confirmado', 'booked'),
    ('Produccion', 'shoot'),
    ('Post produccion', 'post'),
    ('Listo', 'done'),
]


def job_stage_index(status: str) -> int:
    st = (status or '').lower()
    if 'listo' in st:
        return 5
    if 'post' in st:
        return 4
    if 'produccion' in st or 'producción' in st or 'progreso' in st:
        return 3
    if 'confirmado' in st:
        return 2
    if 'cotizando' in st or 'propuesta' in st:
        return 1
    if 'lead' in st:
        return 0
    return 0


def enrich_job_ops(job, cotizaciones=None):
    """Studio Ninja-style operational summary for list/detail screens."""
    cotizaciones = cotizaciones or []
    total = float(job.get('Total facturado al cliente (Q)') or 0)
    paid = float(job.get('Total pagado por cliente (Q)') or 0)
    if cotizaciones:
        total = max(total, sum(float(c.get('Monto total (Q)') or 0) for c in cotizaciones))
        paid = max(paid, sum(float(c.get('Pagado (Q)') or c.get('Anticipo (Q)') or 0) for c in cotizaciones))

    quote_count = len([c for c in cotizaciones if c.get('Estado') != 'Pagada'])
    invoice_count = len([c for c in cotizaciones if c.get('Estado') == 'Pagada'])
    accepted_quote = any((c.get('Estado') or '') in ('Aceptada', 'Pagada') for c in cotizaciones)
    sent_quote = any((c.get('Estado') or '') in ('Enviada', 'Vista por cliente') for c in cotizaciones)
    status = job.get('Estado') or ''
    stage_idx = job_stage_index(status)
    event_days = days_until(job.get('Fecha del evento'))
    balance = max(0, total - paid)
    has_client = bool(job.get('Cliente') or job.get('cliente'))
    has_team = any(job.get(k) and job.get(k) != 'NO APLICA' for k in ('Primera Camara', 'Segunda Camara', 'Videografo 1', 'Videografo 2'))

    next_task = 'Revisar proyecto'
    next_task_tone = 'neutral'
    if status == 'Listo':
        next_task = 'Proyecto completado'
        next_task_tone = 'done'
    elif not has_client:
        next_task = 'Vincular cliente'
        next_task_tone = 'urgent'
    elif stage_idx <= 1 and quote_count == 0 and not accepted_quote:
        next_task = 'Crear cotizacion'
        next_task_tone = 'urgent'
    elif sent_quote and not accepted_quote:
        next_task = 'Dar seguimiento a cotizacion'
        next_task_tone = 'warning'
    elif accepted_quote and balance > 0:
        next_task = 'Cobrar saldo pendiente'
        next_task_tone = 'warning'
    elif event_days <= 14 and event_days >= 0 and not has_team:
        next_task = 'Asignar equipo'
        next_task_tone = 'urgent'
    elif event_days <= 7 and event_days >= 0 and not job.get('Confirmado'):
        next_task = 'Confirmar produccion'
        next_task_tone = 'warning'
    elif event_days < 0 and status != 'Listo':
        next_task = 'Cerrar post produccion'
        next_task_tone = 'warning'
    elif event_days >= 0:
        next_task = 'Preparar shoot'
        next_task_tone = 'neutral'

    progress = int(((stage_idx + 1) / len(JOB_WORKFLOW_STEPS)) * 100)
    return {
        'stage_idx': stage_idx,
        'progress': max(0, min(100, progress)),
        'next_task': next_task,
        'next_task_tone': next_task_tone,
        'quote_count': quote_count,
        'invoice_count': invoice_count,
        'balance': balance,
        'total': total,
        'paid': paid,
    }


# ============================================================
# INTEGRACION DE SOLO LECTURA CON LAS GALERIAS
# ============================================================
@app.route('/api/integrations/gallery/jobs')
def api_gallery_job_search():
    """Busca jobs Astral y devuelve solo datos necesarios para destinatarios.

    La credencial es servidor-a-servidor. Nunca se expone al navegador y este
    endpoint no permite crear, editar ni enviar nada desde FlowingCRM.
    """
    configured_token = (os.environ.get('GALLERY_INTEGRATION_TOKEN') or '').strip()
    supplied = request.headers.get('Authorization', '')
    supplied_token = supplied[7:].strip() if supplied.startswith('Bearer ') else ''
    if not configured_token or not supplied_token or not hmac.compare_digest(configured_token, supplied_token):
        return jsonify({'ok': False, 'error': 'Unauthorized'}), 401

    query = (request.args.get('q') or '').strip().lower()
    browse_all = request.args.get('all') == '1'
    if not browse_all and len(query) < 2:
        return jsonify({'ok': False, 'error': 'Escribe al menos 2 caracteres'}), 400
    try:
        offset = max(0, int(request.args.get('offset') or 0))
        limit = max(1, min(100, int(request.args.get('limit') or 50)))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'Paginación inválida'}), 400

    # La galeria es de ASTRAL WEDDINGS. El id se ve raro porque es heredado:
    # 'tenant-norkevin' ES la cuenta de Astral (ver SEGURIDAD_AISLAMIENTO.md).
    # Se toma de una variable de entorno para poder apuntarla sin editar
    # codigo, con el valor actual como default para no cambiar el contrato de
    # la integracion existente.
    astral_tenant_id = os.environ.get('GALLERY_TENANT_ID', 'tenant-norkevin')
    jobs = store.list_privileged('jobs', tenant_id=astral_tenant_id,
                                 reason='integracion de galeria (token servidor-a-servidor)')
    clients = {
        c.get('id'): c for c in store.list_privileged(
            'clients', tenant_id=astral_tenant_id,
            reason='integracion de galeria (token servidor-a-servidor)')
    }
    leads = {
        lead.get('id'): lead for lead in store.list_privileged(
            'leads', tenant_id=astral_tenant_id,
            reason='integracion de galeria (token servidor-a-servidor)')
    }

    results = []
    for job in jobs:
        contacts = []
        seen = set()
        for role, client_id in (
            ('Cliente principal', job.get('client_id')),
            ('Cliente adicional', job.get('secondary_client_id')),
            ('Wedding planner', job.get('planner_client_id')),
        ):
            client = clients.get(client_id) or {}
            email = _norm_email(client.get('email'))
            if not email or email in seen:
                continue
            seen.add(email)
            contacts.append({
                'name': _client_name(client=client),
                'email': email,
                'role': role,
            })

        lead = leads.get(job.get('lead_id')) or {}
        lead_email = _norm_email(lead.get('email'))
        if lead_email and lead_email not in seen:
            seen.add(lead_email)
            contacts.append({
                'name': _client_name(lead=lead),
                'email': lead_email,
                'role': 'Contacto del lead',
            })

        searchable = ' '.join([
            str(job.get('nombre') or ''),
            str(job.get('boda_date') or ''),
            str(job.get('id') or ''),
            ' '.join(c['name'] for c in contacts),
            ' '.join(c['email'] for c in contacts),
        ]).lower()
        if not browse_all and query not in searchable:
            continue
        results.append({
            'id': job.get('id'),
            'name': job.get('nombre') or 'Boda sin nombre',
            'eventDate': job.get('boda_date') or '',
            'status': job.get('status') or '',
            'contacts': contacts,
        })

    results.sort(key=lambda item: (item.get('eventDate') or '', item.get('name') or ''), reverse=True)
    total = len(results)
    page = results[offset:offset + limit]
    return jsonify({
        'ok': True,
        # Esta integracion es servidor-a-servidor con token: NO hay sesion,
        # asi que get_current_tenant_id() daria None y la marca saldria como
        # "(empresa sin identificar)". La cuenta a la que sirve ya esta
        # resuelta arriba (astral_tenant_id, con GALLERY_TENANT_ID como
        # override), y esa es la fuente correcta.
        'source': _brand_display_name_for_tenant(astral_tenant_id),
        'jobs': page,
        'total': total,
        'offset': offset,
        'hasMore': offset + len(page) < total,
    })


# ============================================================
# LOGIN CON GOOGLE (portada) -- protege todo el CRM salvo las paginas
# publicas que los CLIENTES necesitan sin iniciar sesion (portal, ver/firmar
# cotizacion y contrato, cuestionario, descargar PDFs, formularios de
# contacto). Todo lo demas exige haber iniciado sesion con una cuenta de
# Google cuyo email coincida con el login_email de alguno de los 3 tenants
# en data/tenants.json (_tenant_for_login_email) -- esa cuenta de Google
# entra SOLO a su propia cuenta del CRM, nunca a las otras.
# ============================================================
import re as _re_auth

PUBLIC_EXACT_PATHS = {
    '/login', '/logout', '/dev/login', '/contacto', '/api/leads/nuevo', '/captacion', '/api/captacion',
    '/api/integrations/gallery/jobs',
    '/manifest.webmanifest', '/service-worker.js', '/offline.html',
}
PUBLIC_PREFIXES = ('/portal/', '/static/', '/auth/google/login/')
PUBLIC_PATTERNS = [
    _re_auth.compile(r'^/quotes/[^/]+$'),
    _re_auth.compile(r'^/quotes/[^/]+/accept$'),
    _re_auth.compile(r'^/quotes/[^/]+/decline$'),
    _re_auth.compile(r'^/quotes/[^/]+/pdf$'),
    # Public Quote Experience (28-ago-2026): /q/<token> es el enlace nuevo
    # que se manda al cliente (token seguro, ver src/public_tokens.py).
    # /quotes/<id> NO se quita de esta lista: sigue siendo el alias interno
    # que ya circula en PDFs y correos enviados antes de este cambio.
    _re_auth.compile(r'^/q/[^/]+$'),
    _re_auth.compile(r'^/q/[^/]+/accept$'),
    _re_auth.compile(r'^/q/[^/]+/decline$'),
    _re_auth.compile(r'^/q/[^/]+/pdf$'),
    _re_auth.compile(r'^/contracts/[^/]+$'),
    _re_auth.compile(r'^/contracts/[^/]+/pdf$'),
    _re_auth.compile(r'^/api/contracts/[^/]+/sign$'),
    _re_auth.compile(r'^/questionnaires/[^/]+$'),
    _re_auth.compile(r'^/api/questionnaires/[^/]+/submit$'),
    _re_auth.compile(r'^/invoices/[^/]+/pdf$'),
    # Factura web publica (29-ago-2026): /i/<token> es el enlace nuevo que
    # recibe el cliente. Antes solo existia el PDF. /invoices/<id> (sin
    # /pdf) NO se agrega: esa sigue siendo la vista interna con acciones de
    # administracion, y el cliente nunca debe aterrizar ahi.
    _re_auth.compile(r'^/i/[^/]+$'),
    _re_auth.compile(r'^/i/[^/]+/pdf$'),
    _re_auth.compile(r'^/files/[^/]+/download$'),
    _re_auth.compile(r'^/contacto/[^/]+$'),
    _re_auth.compile(r'^/captacion/[^/]+$'),
]


def _is_public_path(path):
    if path in PUBLIC_EXACT_PATHS:
        return True
    if path.startswith(PUBLIC_PREFIXES):
        return True
    return any(p.match(path) for p in PUBLIC_PATTERNS)


# Que tabla identifica la cuenta en cada tipo de enlace publico. El id va
# siempre en el mismo lugar de la ruta: /portal/<client_id>, /quotes/<id>...
_PUBLIC_TENANT_LOOKUP = (
    ('/portal/', 'clients', 'id'),
    ('/quotes/', 'quotes', 'id'),
    ('/contracts/', 'contracts', 'id'),
    ('/api/contracts/', 'contracts', 'id'),
    ('/questionnaires/', 'questionnaires', 'id'),
    ('/api/questionnaires/', 'questionnaires', 'id'),
    # El PDF publico de una factura se pide por invoice_id, no por el id del
    # registro de pago.
    ('/invoices/', 'payments', 'invoice_id'),
    ('/files/', 'files', 'id'),
)


# Ids que NO son tokens seguros: se construyeron a partir del nombre de la
# boda al importar de Studio Ninja (contract-sn-boda-rebeca-y-jos), o sea que
# alguien que sepa como se llamaba la boda puede reconstruir el enlace.
_ID_LEGACY = re.compile(r'^[a-z]+-sn-')


def _registrar_uso_legacy(tipo, record_id, tenant_id):
    """Etapa 2 de la migracion de enlaces: dejar constancia de que alguien
    entro por un enlace VIEJO.

    Sin este dato la etapa 3 seria adivinar: no hay forma de saber que
    enlaces antiguos siguen circulando de verdad (en correos ya enviados, en
    WhatsApp, guardados por el cliente) y cuales murieron solos.

    Del enlace se guarda una HUELLA, nunca el id completo: Kevin fue
    explicito en que un enlace publico es una credencial, y una credencial
    completa en un log es una credencial filtrada.
    """
    log_security_event(
        'LEGACY_PUBLIC_LINK_USED',
        tipo=tipo,
        recurso=public_tokens.huella(record_id),
        cuenta=tenant_id or 'desconocida',
        cuando=datetime.now().isoformat(),
    )


def _resolve_public_tenant(path):
    """Deduce a que cuenta pertenece un enlace publico.

    Las rutas publicas (portal del cliente, aceptar cotizacion, firmar
    contrato) llegan sin sesion. Con el aislamiento cerrado, sin cuenta
    activa no verian nada, asi que hay que fijar la cuenta ANTES de leer
    datos -- y sacarla del propio registro del enlace, no de un parametro
    que el visitante pueda cambiar.

    Se resuelve una sola vez por peticion en @app.before_request en vez de
    ruta por ruta: una sola puerta es mucho mas facil de auditar que veinte.
    """
    # Los formularios publicos no llevan id de registro: identifican la
    # cuenta con su slug (/contacto/norkevin-photography). Sin slug caen en
    # Astral Weddings, que es el enlace ya embebido en su sitio.
    for prefix in ('/contacto', '/captacion'):
        if path == prefix or path.startswith(prefix + '/'):
            slug = path[len(prefix):].strip('/').split('/', 1)[0]
            tenant = _tenant_by_slug(slug) if slug else _tenant_by_slug('astral-weddings')
            return (tenant or {}).get('id')

    # Public Quote Experience (28-ago-2026): el segmento en /q/<token> no es
    # un id, es un secreto (src/public_tokens.py) -- la cuenta se busca por
    # el HASH del token, nunca por igualdad directa contra un id, asi que
    # no encaja en el loop generico de abajo (que compara por igualdad).
    if path.startswith('/q/'):
        token = path[len('/q/'):].split('/', 1)[0]
        if not token:
            return None
        return store.owner_tenant_of_public_token('quotes', token)

    # Factura publica (29-ago-2026): mismo caso que /q/, pero el hash vive
    # en las filas de 'payments' -- una factura es el conjunto de cuotas que
    # comparten invoice_id, no un registro propio.
    if path.startswith('/i/'):
        token = path[len('/i/'):].split('/', 1)[0]
        if not token:
            return None
        return store.owner_tenant_of_public_token('payments', token)

    for prefix, table, field in _PUBLIC_TENANT_LOOKUP:
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix):]
        record_id = rest.split('/', 1)[0]
        if not record_id:
            return None
        tenant_id = store.owner_tenant_of(table, record_id, field=field)
        # Se registra DESPUES de resolver la empresa, para poder decir de
        # quien era el enlace. Solo registra, no cambia nada: los enlaces
        # viejos siguen funcionando exactamente igual.
        if tenant_id and _ID_LEGACY.match(str(record_id)):
            _registrar_uso_legacy(table, record_id, tenant_id)
        return tenant_id
    return None


# Estas 12 rutas de /api/admin (ver _ADMIN_CAPABILITIES) cruzan las dos
# empresas y algunas mutan datos (workflow-cleanup, migrate-to-multi-tenant,
# reconcile-studio-ninja-jobs, fix-secondary-clients, import-astral-leads),
# asi que este token es una llave real, no un detalle cosmetico.
#
# 27-ago-2026: vivia hardcodeado aca mismo como string literal. El repo de
# GitHub es publico (github.com/Norkevin/crm-norkevin) y la app ya esta
# desplegada en internet (Render) -- ese valor quedaba leible por cualquiera
# y usable YA contra datos reales de las dos empresas, sin login. Se movio a
# variable de entorno con fail-closed real: sin ADMIN_ONE_TIME_TOKEN seteado
# en el entorno, NINGUN valor de `token` (ni uno vacio) matchea nunca -- ver
# el chequeo en _require_login(). El valor viejo queda en el historial de
# git para siempre (es publico igual), pero como el codigo ya no confia en
# ningun valor hardcodeado, queda inerte.
_ADMIN_ONE_TIME_TOKEN = os.environ.get('ADMIN_ONE_TIME_TOKEN', '')


def _dev_login_enabled():
    """Login de desarrollo para poder abrir la app en el navegador local sin
    pasar por Google OAuth (necesario para revisar el responsive).

    Apagado salvo que se pidan LAS DOS cosas a la vez: la variable
    DEV_LOGIN=1 y que la peticion venga de la propia maquina. En Render no
    existe esa variable, asi que alla la ruta responde 404 siempre.
    """
    if os.environ.get('DEV_LOGIN') != '1':
        return False
    return request.remote_addr in ('127.0.0.1', '::1', 'localhost')


@app.route('/dev/login')
def dev_login():
    if not _dev_login_enabled():
        abort(404)
    tenant_id = request.args.get('tenant') or 'tenant-norkevin'
    tenant = next((t for t in store.list('tenants') if t.get('id') == tenant_id), None)
    if not tenant:
        abort(404)
    session['logged_in'] = True
    session['user_email'] = tenant.get('login_email') or 'dev@localhost'
    session['user_name'] = 'Dev Local'
    session['tenant_id'] = tenant['id']
    session.permanent = True
    logger.warning(f'DEV LOGIN local usado para {tenant["id"]} -- solo desarrollo')
    return redirect(request.args.get('next') or '/dashboard')


# ---------------------------------------------------------------------------
# Capacidades administrativas
# ---------------------------------------------------------------------------
#
# Kevin (punto 9): "evita que todo dependa solamente de que una URL comience
# con /api/admin/".
#
# Cada ruta administrativa declara DOS cosas: que hace, y a que nivel opera.
# El nivel es la distincion del punto 8, que es la que no puede volver a
# mezclarse:
#
#   NIVEL_GLOBAL  -- cruza las dos empresas. Token de admin, nunca sesion.
#                    Sin token responde 404, aunque haya sesion valida.
#   NIVEL_EMPRESA -- opera SOLO sobre la empresa de la sesion, como cualquier
#                    otra pantalla. Se llama desde Settings y no ve nada de la
#                    otra empresa. Es "administrativa" en el sentido de poco
#                    frecuente y peligrosa, no de cruzar negocios.
#
# Deliberadamente NO es un sistema de permisos: no hay roles, ni herencia, ni
# base de datos. Es una lista con un test que la mantiene honesta:
#
#   1. una ruta /api/admin/ nueva que nadie declaro hace fallar un test, asi
#      que no puede quedar desprotegida por olvido;
#   2. una ruta declarada NIVEL_EMPRESA que use scope='all_tenants' tambien
#      hace fallar un test -- la etiqueta no puede mentir;
#   3. el log dice que capacidad se uso, no solo que URL se llamo.

NIVEL_GLOBAL = 'global'
NIVEL_EMPRESA = 'empresa'

CAP_TENANT_AUDIT = 'tenant_audit'          # leer/contar entre empresas, sin escribir
CAP_INCIDENT_REPORT = 'incident_report'    # reconstruir el incidente desde mail_log
CAP_WORKFLOW_CLEANUP = 'workflow_cleanup'  # tocar workflows (con dry-run)
CAP_MIGRATION = 'migration'                # reescribir datos: lo mas peligroso
CAP_DATA_IMPORT = 'data_import'            # crear registros desde un export
CAP_DATA_RESET = 'data_reset'              # vaciar los datos de UNA empresa

_ADMIN_CAPABILITIES = {
    # --- cruzan las dos empresas: solo con token
    '/api/admin/tenant-inventory': (CAP_TENANT_AUDIT, NIVEL_GLOBAL),
    '/api/admin/orphan-audit': (CAP_TENANT_AUDIT, NIVEL_GLOBAL),
    '/api/admin/public-links-audit': (CAP_TENANT_AUDIT, NIVEL_GLOBAL),
    '/api/admin/list-studio-ninja-clients': (CAP_TENANT_AUDIT, NIVEL_GLOBAL),
    '/api/admin/debug-production-workflow': (CAP_TENANT_AUDIT, NIVEL_GLOBAL),
    '/api/admin/incident-report': (CAP_INCIDENT_REPORT, NIVEL_GLOBAL),
    '/api/admin/workflow-cleanup': (CAP_WORKFLOW_CLEANUP, NIVEL_GLOBAL),
    '/api/admin/cleanup-duplicate-questionnaires': (CAP_WORKFLOW_CLEANUP, NIVEL_GLOBAL),
    '/api/admin/migrate-to-multi-tenant': (CAP_MIGRATION, NIVEL_GLOBAL),
    '/api/admin/reconcile-studio-ninja-jobs': (CAP_MIGRATION, NIVEL_GLOBAL),
    '/api/admin/fix-secondary-clients': (CAP_MIGRATION, NIVEL_GLOBAL),
    '/api/admin/import-astral-leads': (CAP_DATA_IMPORT, NIVEL_GLOBAL),

    # --- solo la empresa de la sesion. Se llaman desde Settings, y el
    # aislamiento del store ya las limita a esa empresa: subirlas a token
    # romperia una pantalla que Kevin usa, sin ganar aislamiento.
    '/api/admin/reset-test-data': (CAP_DATA_RESET, NIVEL_EMPRESA),
    '/api/admin/import-studio-ninja': (CAP_DATA_IMPORT, NIVEL_EMPRESA),
}

# Solo las globales van detras del token. Se derivan del mapa para que no
# puedan desincronizarse.
_ADMIN_PATHS = tuple(sorted(ruta for ruta, (_, nivel) in _ADMIN_CAPABILITIES.items()
                            if nivel == NIVEL_GLOBAL))


@app.before_request
def _set_public_tenant():
    """Fija la cuenta de la peticion cuando viene de un enlace publico.

    Corre ANTES de _require_login (orden de registro) para que la ruta ya
    tenga cuenta cuando empiece a leer datos.
    """
    g.public_tenant_id = None
    if session.get('tenant_id'):
        return None
    if not _is_public_path(request.path):
        return None
    try:
        g.public_tenant_id = _resolve_public_tenant(request.path)
    except Exception as e:
        logger.error(f'No se pudo resolver la cuenta de {request.path}: {e}')
    return None


@app.before_request
def _require_login():
    if _is_public_path(request.path):
        return None
    if request.path in _ADMIN_PATHS:
        # `not _ADMIN_ONE_TIME_TOKEN` primero y a proposito: si la variable
        # de entorno no esta seteada, _ADMIN_ONE_TIME_TOKEN es '' y un
        # ?token= vacio (o ausente, que tambien resuelve a None) NUNCA debe
        # poder "matchear" un secreto vacio. Sin esto, un entorno sin la
        # variable configurada quedaria con estas 12 rutas abiertas.
        if not _ADMIN_ONE_TIME_TOKEN or request.args.get('token') != _ADMIN_ONE_TIME_TOKEN:
            # Estar logueado NO alcanza: estas rutas cruzan las dos empresas,
            # asi que sin el token no existen. 404 y no 403 a proposito, para
            # no confirmarle a nadie que la ruta esta ahi.
            log_security_event('RUTA_ADMIN_SIN_TOKEN', ruta=request.path,
                               capacidad=_ADMIN_CAPABILITIES[request.path][0])
            return jsonify({'ok': False, 'error': 'Not found'}), 404
        # Recien aca la peticion queda autorizada a mirar todas las empresas.
        g.is_admin_request = True
        g.admin_capability = _ADMIN_CAPABILITIES[request.path][0]
        log_security_event('CAPACIDAD_ADMIN_USADA', ruta=request.path,
                           capacidad=g.admin_capability)
        return None
    if session.get('logged_in'):
        tenant_id = (session.get('tenant_id') or '').strip()
        user_email = (session.get('user_email') or '').strip().lower()
        tenant = next((t for t in store.list('tenants') if t.get('id') == tenant_id), None) if tenant_id else None
        tenant_login_email = ((tenant or {}).get('login_email') or '').strip().lower()

        # Si el navegador conserva una cookie vieja (antes de multi-tenant,
        # de otra cuenta, o con datos incompletos), dejamos de intentar
        # renderizar pantallas con una sesion corrupta y la "autocuramos"
        # enviando al login otra vez en lugar de responder 500.
        if tenant and user_email and (not tenant_login_email or tenant_login_email == user_email):
            return None

        # Mismo bootstrap de un solo uso que auth_google_login_callback: si
        # ningun tenant tiene login_email configurado todavia (deploy fresco
        # o tenants.json recien migrado), no hay con que validar la sesion
        # que el propio login bootstrap acaba de crear -- confiamos en ella
        # en vez de invalidarla en el siguiente request.
        if tenant_id and user_email:
            all_tenants = store.list('tenants')
            none_migrated_yet = not any(t.get('login_email') for t in all_tenants)
            if none_migrated_yet:
                return None

        session.clear()
        if request.path.startswith('/api/'):
            return jsonify({'ok': False, 'error': 'Sesion invalida o expirada, inicia sesion de nuevo'}), 401
        return redirect(url_for('login_page', next=request.path, error='sesion_expirada'))
    if request.path.startswith('/api/'):
        return jsonify({'ok': False, 'error': 'Sesion expirada, inicia sesion de nuevo'}), 401
    return redirect(url_for('login_page', next=request.path))


def _login_redirect_uri():
    host = request.host
    scheme = 'http' if host.startswith('127.0.0.1') or host.startswith('localhost') else 'https'
    return f'{scheme}://{host}' + url_for('auth_google_login_callback')


@app.route('/manifest.webmanifest')
def pwa_manifest():
    """Servido en la raiz (no en /static/) por convencion, aunque el
    archivo fisico vive en static/ -- el scope no depende de esto (lo
    define el service worker), pero mantiene la URL consistente con
    /service-worker.js."""
    from flask import send_from_directory
    return send_from_directory('static', 'manifest.webmanifest', mimetype='application/manifest+json')


@app.route('/service-worker.js')
def pwa_service_worker():
    """Debe servirse desde la raiz del sitio (no /static/) para que su
    scope por defecto cubra TODO el sitio, no solo /static/*."""
    from flask import send_from_directory
    repo_root = os.path.dirname(os.path.abspath(__file__))
    response = send_from_directory(repo_root, 'service-worker.js', mimetype='application/javascript')
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache'
    return response


@app.route('/offline.html')
def pwa_offline():
    return render_template('offline.html')


@app.route('/login')
def login_page():
    from src import google_login
    response = make_response(render_template(
        'login.html',
        google_configured=google_login.is_configured(),
        next_path=request.args.get('next', '/dashboard')
    ))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'

    # Safari/Chrome pueden conservar una mezcla fea de cookie vieja,
    # service worker y almacenamiento local cuando el usuario vuelve desde
    # una sesion corrupta. Si el backend ya decidio que esa sesion expiro,
    # aprovechamos la pantalla de login para pedirle al navegador que limpie
    # el estado del sitio y vuelva a arrancar "en limpio".
    if request.args.get('error') == 'sesion_expirada':
        response.headers['Clear-Site-Data'] = '"cache", "cookies", "storage"'

    return response


@app.route('/auth/google/login/start')
def auth_google_login_start():
    from src import google_login
    import secrets as _secrets

    if not google_login.is_configured():
        return redirect(url_for('login_page', error='not_configured'))

    redirect_uri = _login_redirect_uri()
    state = _secrets.token_urlsafe(16)
    session['login_state'] = state
    session['login_next'] = request.args.get('next', '/dashboard')
    return redirect(google_login.build_login_url(redirect_uri, state))


@app.route('/auth/google/login/callback')
def auth_google_login_callback():
    from src import google_login

    error = request.args.get('error')
    if error:
        return redirect(url_for('login_page', error=error))

    code = request.args.get('code')
    state = request.args.get('state')
    if not code or not state or state != session.get('login_state'):
        return redirect(url_for('login_page', error='state_invalido'))

    redirect_uri = _login_redirect_uri()
    try:
        email, name, picture = google_login.exchange_code_for_email(code, redirect_uri)
    except Exception as exc:
        return redirect(url_for('login_page', error=str(exc)))

    tenant = _tenant_for_login_email(email)
    if not tenant:
        # Bootstrap de un solo uso: produccion todavia puede tener el
        # tenants.json viejo (sin login_email) la primera vez que este
        # codigo corre -- sin este fallback, Kevin quedaria bloqueado de
        # su propio CRM antes de poder ejecutar la migracion a multi-tenant
        # desde Settings. Se cae al viejo ALLOWED_LOGIN_EMAILS + el primer
        # tenant de la lista, PERO SOLO si ningun tenant tiene login_email
        # configurado todavia -- en cuanto la migracion corre una vez, esta
        # rama deja de poder activarse nunca mas.
        all_tenants = store.list('tenants')
        # bool(all_tenants) descartado a proposito: si tenants.json nunca
        # existio en el disco de Render (nada lo necesitaba antes de esto),
        # store.list() devuelve [] -- eso TAMBIEN cuenta como "todavia no
        # migrado", no como "no reconocido". Antes este chequeo exigia
        # all_tenants no vacio y dejaba a Kevin bloqueado de su propia
        # cuenta en un deploy fresco.
        none_migrated_yet = not any(t.get('login_email') for t in all_tenants)
        allowed_env = {e.strip().lower() for e in os.environ.get('ALLOWED_LOGIN_EMAILS', '').split(',') if e.strip()}
        if none_migrated_yet and email.lower() in allowed_env:
            tenant = all_tenants[0] if all_tenants else {
                'id': 'tenant-norkevin', 'name': 'ASTRAL WEDDINGS', 'active': True,
            }
    if not tenant or tenant.get('active') is False:
        return redirect(url_for('login_page', error='cuenta_no_autorizada'))

    session['logged_in'] = True
    session['user_email'] = email
    session['user_name'] = name
    session['user_picture'] = picture
    session['tenant_id'] = tenant['id']
    session.permanent = True
    next_path = session.pop('login_next', '/dashboard')
    return redirect(next_path if next_path.startswith('/') else '/dashboard')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))


# ============================================================
# PÁGINAS PRINCIPALES
# ============================================================

@app.route('/')
def index():
    return redirect('/dashboard')
    """Calendar principal con bodas del mes en curso + próximas."""
    import calendar as _cal
    from datetime import date

    jobs = _canonical_jobs()
    events = list_calendar()
    hoy = date.today()

    mes_param = request.args.get('month', '')
    if mes_param and re.match(r'\d{4}-\d{2}', mes_param):
        year, month = map(int, mes_param.split('-'))
    else:
        year, month = hoy.year, hoy.month

    cal = _cal.Calendar(firstweekday=6)  # domingo
    weeks = []
    for week in cal.monthdayscalendar(year, month):
        cells = []
        for day_num in week:
            in_month = day_num != 0
            day = day_num if day_num else 1
            iso_date = f"{year}-{month:02d}-{day:02d}" if in_month else None
            day_events = [e for e in events if e.get('date', '').startswith(iso_date or 'XXXX')] if iso_date else []
            cells.append({
                'day': day if in_month else '',
                'in_month': in_month,
                'today': iso_date == hoy.isoformat() if iso_date else False,
                'events': day_events,
            })
        weeks.append(cells)

    # Prev / next
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    return render_template('index.html',
                          weeks=weeks,
                          year=year,
                          month=month,
                          month_name=['', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'][month],
                          prev_year=prev_year, prev_month=prev_month,
                          next_year=next_year, next_month=next_month,
                          day_names=['Dom', 'Lun', 'Mar', 'Mie', 'Jue', 'Vie', 'Sab'])


def _compute_custom_range_payload(start_day, end_day):
    """Igual que los rangos preseteados (7/30/mtd/ytd) de dashboard(), pero
    para un rango de fechas arbitrario que el usuario elige con el
    calendario -- Kevin: 'quiero ver cuanto he ganado de fecha a fecha'.
    Bucket diario (como '7'/'30'), tope de 366 dias para no generar series
    gigantes por error de seleccion."""
    from datetime import date as _date, timedelta as _timedelta

    if end_day < start_day:
        start_day, end_day = end_day, start_day
    max_days = 366
    total_days = (end_day - start_day).days + 1
    if total_days > max_days:
        end_day = start_day + _timedelta(days=max_days - 1)
        total_days = max_days

    days = [start_day + _timedelta(days=i) for i in range(total_days)]
    if total_days > 62:
        labels = [d.strftime('%d %b') if d.day in (1, 15) else '' for d in days]
    else:
        labels = [d.strftime('%d %b') for d in days]
    date_label = f"{start_day.strftime('%d %b %Y')} - {end_day.strftime('%d %b %Y')}"
    base_keys = [d.isoformat() for d in days]
    keys_index = {key: idx for idx, key in enumerate(base_keys)}

    all_leads = _open_leads()
    all_jobs = _canonical_jobs()
    all_payments = _visible_billable_payments()
    job_type_labels = sorted({
        (j.get('type') or j.get('tipo_evento') or 'BODAS') for j in all_jobs
    } | {
        (l.get('tipo_evento') or 'BODAS') for l in all_leads
    } | {'All Job Types'})

    def _parse_day(value):
        if not value:
            return None
        try:
            return _date.fromisoformat(str(value)[:10])
        except Exception:
            return None

    job_by_id = {j.get('id'): j for j in all_jobs}
    lead_by_id = {l.get('id'): l for l in all_leads}

    job_types_payload = {}
    for job_type in job_type_labels:
        lead_series = [0] * total_days
        session_series = [0] * total_days
        payment_series = [0] * total_days
        revenue_series = [0] * total_days

        for lead in all_leads:
            lead_type = lead.get('tipo_evento') or 'BODAS'
            if job_type != 'All Job Types' and lead_type != job_type:
                continue
            d = _parse_day(lead.get('created'))
            idx = keys_index.get(d.isoformat()) if d else None
            if idx is not None:
                lead_series[idx] += 1

        for job in all_jobs:
            current_type = job.get('type') or job.get('tipo_evento') or 'BODAS'
            if job_type != 'All Job Types' and current_type != job_type:
                continue
            d = _parse_day(job.get('boda_date') or job.get('created'))
            idx = keys_index.get(d.isoformat()) if d else None
            if idx is not None:
                session_series[idx] += 1
                revenue_series[idx] += coerce_amount(job.get('price_total') or job.get('Total facturado al cliente (Q)'))

        for payment in all_payments:
            job = job_by_id.get(payment.get('job_id')) or {}
            lead = lead_by_id.get(job.get('lead_id')) or {}
            current_type = job.get('type') or job.get('tipo_evento') or lead.get('tipo_evento') or 'BODAS'
            if job_type != 'All Job Types' and current_type != job_type:
                continue
            d = _parse_day(payment.get('paid_date') or payment.get('fecha_pago') or payment.get('sent_at') or payment.get('due_date'))
            idx = keys_index.get(d.isoformat()) if d else None
            if idx is not None and payment.get('status') == 'Pagado':
                payment_series[idx] += coerce_amount(payment.get('amount'))

        job_types_payload[job_type] = {
            'leads': lead_series,
            'sessions': session_series,
            'payments': payment_series,
            'revenue': revenue_series,
            'totals': {
                'leads': sum(lead_series),
                'sessions': sum(session_series),
                'payments': sum(payment_series),
                'revenue': sum(revenue_series),
            }
        }

    return {'labels': labels, 'dateLabel': date_label, 'jobTypes': job_types_payload}


@app.route('/api/dashboard/custom-range')
def api_dashboard_custom_range():
    """Endpoint para el selector de calendario del dashboard -- devuelve la
    misma forma de datos que los rangos preseteados pero para las fechas
    exactas que el usuario elija."""
    start_str = (request.args.get('start') or '').strip()
    end_str = (request.args.get('end') or '').strip()
    try:
        start_day = date.fromisoformat(start_str)
        end_day = date.fromisoformat(end_str)
    except Exception:
        return jsonify({'ok': False, 'error': 'Fechas invalidas'}), 400
    payload = _compute_custom_range_payload(start_day, end_day)
    payload['ok'] = True
    return jsonify(payload)


@app.route('/dashboard')
def dashboard():
    """Dashboard con KPIs + graficas de ingresos.

    SIN "estos leads necesitan tu atencion" - Kevin lo elimino.
    CON graficas vectoriales (line chart + pie chart).
    """
    from datetime import date, timedelta
    import math

    today = date.today()
    today_str = today.isoformat()

    # Kevin: la lista de "proximas sesiones" quedaba vacia con datos reales
    # porque estaba limitada a los proximos 60 dias -- sus bodas reales
    # agendadas suelen estar meses (o mas de un año) por delante. Mostramos
    # cualquier boda futura, ordenada por la mas cercana primero (el corte a
    # 5 abajo en el template ya evita que la lista crezca sin limite).
    upcoming_jobs = []
    for j in _canonical_jobs():
        if str(j.get('status') or '').strip().lower() in ('archivado', 'cancelado', 'cancelada'):
            continue
        boda = j.get('boda_date', '')
        try:
            bd = date.fromisoformat(boda)
            if (bd - today).days >= 0:
                j['dias_restantes'] = (bd - today).days
                upcoming_jobs.append(j)
        except Exception:
            pass
    upcoming_jobs.sort(key=lambda j: (j.get('dias_restantes') if j.get('dias_restantes') is not None else 999))

    # Tabla "Trabajos recientes" del dashboard. Usa exactamente el mismo
    # helper que /jobs (_job_clients_display): antes armaba el nombre a
    # mano desde `client_id`, o sea SOLO el cliente principal. La misma
    # boda mostraba una persona en el dashboard y dos o tres en la lista
    # de trabajos -- y la novia no aparecia en la pantalla de inicio.
    _dash_clients_by_id = {c['id']: c for c in _canonical_clients()}
    _dash_rel = _relaciones_por_job(upcoming_jobs[:5])
    _dash_job_ids_cache = {jj.get('id') for jj in list_jobs()}
    _dash_lead_ids_cache = {ll.get('id') for ll in list_leads()}
    _month_abbrs_es = ['ENE', 'FEB', 'MAR', 'ABR', 'MAY', 'JUN', 'JUL', 'AGO', 'SEP', 'OCT', 'NOV', 'DIC']
    for j in upcoming_jobs[:5]:
        j['client_name'] = _job_clients_display(
            j, _dash_clients_by_id, _dash_rel.get(j.get('id'), []))
        try:
            _, prog, _ = compute_workflow_steps_for_job(
                j, job_ids_cache=_dash_job_ids_cache, lead_ids_cache=_dash_lead_ids_cache)
            j['workflow_progress'] = prog
        except Exception:
            j['workflow_progress'] = 0
        try:
            _bd = date.fromisoformat(j.get('boda_date'))
            j['event_day'] = _bd.day
            j['event_month_abbr'] = _month_abbrs_es[_bd.month - 1]
        except Exception:
            j['event_day'] = None
            j['event_month_abbr'] = None

    # Recent leads (ultimos 5)
    recent_leads = sorted(_open_leads(), key=lambda l: l.get('created', ''), reverse=True)[:5]

    # Workflow events -- solo los de esta cuenta. Sin el filtro, el
    # dashboard de Astral mostraba movimientos de bodas de Norkevin.
    _mis_instancias = {i.id for i in _workflow_instances_del_tenant()}
    workflow_events = [h for h in workflow_engine.get_history(limit=2000)
                       if h.get('instance_id') in _mis_instancias][-10:]

    total_upcoming = sum(coerce_amount(j.get('price_total') or j.get('Total facturado al cliente (Q)')) for j in upcoming_jobs)

    # === GRAFICA 1: Ingresos por mes (ultimos 6 meses) ===
    monthly_income = []
    for i in range(5, -1, -1):
        d = today - timedelta(days=30 * i)
        month_key = d.strftime('%Y-%m')
        month_label = d.strftime('%b')
        monthly_income.append({'key': month_key, 'label': month_label, 'amount': 0})

    for p in list_payments():
        if p.get('status') == 'Pagado':
            paid = p.get('paid_date') or p.get('sent_at') or ''
            month_key = paid[:7] if paid else ''
            for m in monthly_income:
                if m['key'] == month_key:
                    m['amount'] += coerce_amount(p.get('amount'))
                    break

    # === GRAFICA 2: Pie chart ===
    all_payments = _visible_billable_payments()
    total_paid = sum(coerce_amount(p.get('amount')) for p in all_payments if p.get('status') == 'Pagado')
    total_pending = sum(coerce_amount(p.get('amount')) for p in all_payments if p.get('status') == 'Pendiente')
    total_late = sum(coerce_amount(p.get('amount')) for p in all_payments if p.get('status') == 'Late')

    total_amount = total_paid + total_pending + total_late
    paid_pct = (total_paid / total_amount) if total_amount > 0 else 0
    pending_pct = (total_pending / total_amount) if total_amount > 0 else 0
    late_pct = (total_late / total_amount) if total_amount > 0 else 0

    def arc_path(start_pct, end_pct):
        cx, cy = 100, 100
        r = 80
        if end_pct - start_pct >= 1:
            return f'M {cx-r} {cy} A {r} {r} 0 1 1 {cx+r} {cy} A {r} {r} 0 1 1 {cx-r} {cy} Z'
        start_angle = start_pct * 360 - 90
        end_angle = end_pct * 360 - 90
        s_rad = math.radians(start_angle)
        e_rad = math.radians(end_angle)
        x1 = cx + r * math.cos(s_rad)
        y1 = cy + r * math.sin(s_rad)
        x2 = cx + r * math.cos(e_rad)
        y2 = cy + r * math.sin(e_rad)
        large_arc = 1 if (end_pct - start_pct) > 0.5 else 0
        return f'M {cx} {cy} L {x1:.1f} {y1:.1f} A {r} {r} 0 {large_arc} 1 {x2:.1f} {y2:.1f} Z'

    pie_segments = []
    if paid_pct > 0:
        pie_segments.append({'color': '#059669', 'label': 'Pagado', 'amount': total_paid, 'pct': paid_pct, 'path': arc_path(0, paid_pct)})
    if pending_pct > 0:
        pie_segments.append({'color': '#D97706', 'label': 'Pendiente', 'amount': total_pending, 'pct': pending_pct, 'path': arc_path(paid_pct, paid_pct + pending_pct)})
    if late_pct > 0:
        pie_segments.append({'color': '#DC2626', 'label': 'Atrasado', 'amount': total_late, 'pct': late_pct, 'path': arc_path(paid_pct + pending_pct, 1.0)})

    configured_sources = _configured_lead_sources(include_inactive=True)
    source_meta = {source['name']: source for source in configured_sources}
    lead_source_counts = defaultdict(int)
    lead_source_jobs = defaultdict(int)
    for source in configured_sources:
        lead_source_counts[source['name']] += 0
        lead_source_jobs[source['name']] += 0
    source_leads = _open_leads()
    for lead in source_leads:
        source = lead.get('fuente') or 'Sin fuente'
        lead_source_counts[source] += 1
    leads_by_id = {lead.get('id'): lead for lead in list_leads()}
    for job in _canonical_jobs():
        lead = leads_by_id.get(job.get('lead_id'))
        source = (lead or {}).get('fuente') or job.get('lead_source') or 'Sin fuente'
        lead_source_jobs[source] += 1

    source_total = sum(lead_source_counts.values()) or 1
    lead_source_stats = []
    start = 0
    all_source_names = sorted(set(lead_source_counts.keys()) | set(lead_source_jobs.keys()),
                              key=lambda name: lead_source_counts.get(name, 0),
                              reverse=True)
    visible_source_names = [
        name for name in all_source_names
        if lead_source_counts.get(name, 0) or lead_source_jobs.get(name, 0) or source_meta.get(name, {}).get('active', False)
    ]
    for idx, source in enumerate(visible_source_names):
        count = lead_source_counts.get(source, 0)
        jobs_for_source = lead_source_jobs.get(source, 0)
        pct = count / source_total
        end = start + pct
        meta = source_meta.get(source) or {}
        lead_source_stats.append({
            'label': source,
            'leads': count,
            'jobs': jobs_for_source,
            'pct': pct,
            'status': 'Active' if meta.get('active', True) else 'Inactive',
            'color': meta.get('color') or SOURCE_COLORS[idx % len(SOURCE_COLORS)],
            'path': arc_path(start, end),
        })
        start = end

    max_source_leads = max((s['leads'] for s in lead_source_stats), default=0) or 1
    for s in lead_source_stats:
        s['bar_pct'] = round(s['leads'] / max_source_leads * 100, 1)

    def _parse_iso_day(value):
        if not value:
            return None
        try:
            return date.fromisoformat(str(value)[:10])
        except Exception:
            return None

    def _range_points(range_key):
        if range_key == '7':
            start_day = today - timedelta(days=6)
            days = [start_day + timedelta(days=i) for i in range(7)]
            return days, [d.strftime('%d %b') for d in days], f"{start_day.strftime('%d %b %Y')} - {today.strftime('%d %b %Y')}"
        if range_key == '30':
            start_day = today - timedelta(days=29)
            days = [start_day + timedelta(days=i) for i in range(30)]
            return days, [d.strftime('%d %b') for d in days], f"{start_day.strftime('%d %b %Y')} - {today.strftime('%d %b %Y')}"
        if range_key == 'ytd':
            months = [date(today.year, m, 1) for m in range(1, 13)]
            return months, [d.strftime('%b') for d in months], f"01 Jan {today.year} - {today.strftime('%d %b %Y')}"
        start_day = today.replace(day=1)
        days = [start_day + timedelta(days=i) for i in range((today - start_day).days + 1)]
        return days, [d.strftime('%d %b') for d in days], f"{start_day.strftime('%d %b %Y')} - {today.strftime('%d %b %Y')}"

    def _bucket_key(day, range_key):
        if not day:
            return ''
        return day.strftime('%Y-%m') if range_key == 'ytd' else day.isoformat()

    all_dashboard_leads = _open_leads()
    all_dashboard_jobs = _canonical_jobs()
    all_dashboard_payments = _visible_billable_payments()

    job_type_labels = sorted({
        (j.get('type') or j.get('tipo_evento') or 'BODAS')
        for j in all_dashboard_jobs
    } | {
        (l.get('tipo_evento') or 'BODAS')
        for l in all_dashboard_leads
    } | {'All Job Types'})

    dashboard_data = {}
    for range_key in ('7', '30', 'mtd', 'ytd'):
        points, labels, date_label = _range_points(range_key)
        base_keys = [_bucket_key(point, range_key) for point in points]
        range_payload = {
            'labels': labels,
            'dateLabel': date_label,
            'jobTypes': {},
        }
        for job_type in job_type_labels:
            lead_series = [0 for _ in base_keys]
            session_series = [0 for _ in base_keys]
            payment_series = [0 for _ in base_keys]
            revenue_series = [0 for _ in base_keys]
            keys_index = {key: idx for idx, key in enumerate(base_keys)}

            for lead in all_dashboard_leads:
                lead_type = lead.get('tipo_evento') or 'BODAS'
                if job_type != 'All Job Types' and lead_type != job_type:
                    continue
                key = _bucket_key(_parse_iso_day(lead.get('created')), range_key)
                if key in keys_index:
                    lead_series[keys_index[key]] += 1

            for job in all_dashboard_jobs:
                current_type = job.get('type') or job.get('tipo_evento') or 'BODAS'
                if job_type != 'All Job Types' and current_type != job_type:
                    continue
                key = _bucket_key(_parse_iso_day(job.get('boda_date') or job.get('created')), range_key)
                if key in keys_index:
                    session_series[keys_index[key]] += 1
                    revenue_series[keys_index[key]] += coerce_amount(job.get('price_total') or job.get('Total facturado al cliente (Q)'))

            job_by_id = {job.get('id'): job for job in all_dashboard_jobs}
            lead_by_id = {lead.get('id'): lead for lead in all_dashboard_leads}
            for payment in all_dashboard_payments:
                job = job_by_id.get(payment.get('job_id')) or {}
                lead = lead_by_id.get(job.get('lead_id')) or {}
                current_type = job.get('type') or job.get('tipo_evento') or lead.get('tipo_evento') or 'BODAS'
                if job_type != 'All Job Types' and current_type != job_type:
                    continue
                key = _bucket_key(_parse_iso_day(payment.get('paid_date') or payment.get('fecha_pago') or payment.get('sent_at') or payment.get('due_date')), range_key)
                if key in keys_index:
                    amount = coerce_amount(payment.get('amount'))
                    if payment.get('status') == 'Pagado':
                        payment_series[keys_index[key]] += amount

            range_payload['jobTypes'][job_type] = {
                'leads': lead_series,
                'sessions': session_series,
                'payments': payment_series,
                'revenue': revenue_series,
                'totals': {
                    'leads': sum(lead_series),
                    'sessions': sum(session_series),
                    'payments': sum(payment_series),
                    'revenue': sum(revenue_series),
                }
            }
        dashboard_data[range_key] = range_payload

    # === Revenue Comparison: años reales superpuestos (estilo Studio Ninja) ===
    # Esta grafica es de proyeccion: suma pagos cobrados + pagos agendados
    # pendientes por su fecha de cobro. La pantalla Payments conserva el estado
    # real de cada pago sin mezclarlo.
    revenue_by_year = defaultdict(lambda: [0.0] * 12)
    paid_by_year = defaultdict(lambda: [0.0] * 12)
    projected_by_year = defaultdict(lambda: [0.0] * 12)
    for p in all_dashboard_payments:
        amount = coerce_amount(p.get('amount'))
        if p.get('status') == 'Pagado':
            paid_day = _parse_iso_day(p.get('paid_date') or p.get('fecha_pago') or p.get('sent_at') or p.get('due_date'))
            if paid_day:
                revenue_by_year[paid_day.year][paid_day.month - 1] += amount
                paid_by_year[paid_day.year][paid_day.month - 1] += amount
        else:
            due_day = _parse_iso_day(p.get('due_date'))
            if due_day:
                revenue_by_year[due_day.year][due_day.month - 1] += amount
                projected_by_year[due_day.year][due_day.month - 1] += amount

    year_palette = ['#2563EB', '#7C3AED', '#F59E0B', '#DC2626', '#059669', '#0891B2']
    sorted_years = sorted(set(revenue_by_year.keys()) | set(paid_by_year.keys()) | set(projected_by_year.keys()))
    revenue_comparison_series = []
    for idx, yr in enumerate(sorted_years):
        values = revenue_by_year[yr]
        paid_values = paid_by_year[yr]
        projected = projected_by_year[yr]
        color = year_palette[idx % len(year_palette)]
        revenue_comparison_series.append({
            'year': yr,
            'color': color,
            'values': values,
            'paid': paid_values,
            'projected': projected,
            'total': sum(values),
            'total_paid': sum(paid_values),
            'total_projected': sum(projected),
        })

    total_unpaid = total_pending + total_late

    return render_template('dashboard.html',
                           today=today,
                           current_year=today.year,
                           upcoming_jobs=upcoming_jobs,
                           recent_leads=recent_leads,
                           workflow_events=workflow_events,
                           total_upcoming=total_upcoming,
                           total_income=total_paid,
                           total_pending=total_unpaid,
                           total_unpaid=total_unpaid,
                           monthly_income=monthly_income,
                           pie_segments=pie_segments,
                           lead_source_stats=lead_source_stats,
                           dashboard_data=dashboard_data,
                           job_type_labels=job_type_labels,
                           total_paid=total_paid,
                           total_late=total_late,
                           revenue_comparison_series=revenue_comparison_series)


def _format_pretty_date(value):
    """'2027-05-08' -> 'Sat, 08 May 2027' (formato Studio Ninja)."""
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').strftime('%a, %d %b %Y')
    except Exception:
        return value


@app.route('/leads')
def leads_list():
    """Leads Overview estilo Studio Ninja - usa data JSON local."""
    from src.mail_tracker import get_tracker

    leads = _open_leads()
    booked_dates = {
        j.get('boda_date'): j.get('nombre')
        for j in _canonical_jobs()
        if j.get('boda_date') and j.get('status') not in ('Archivado',)
    }
    # Fechas donde hay mas de un lead abierto interesado (estilo Studio Ninja:
    # naranja = "another lead is at the same time"), para ayudar a decidir si
    # aceptar o no una boda cuando hay competencia por la misma fecha.
    open_leads_by_date = defaultdict(list)
    for l in leads:
        fecha_l = l.get('fecha_tentativa')
        if fecha_l and l.get('status') not in ('Convertido', 'Perdido'):
            open_leads_by_date[fecha_l].append(l.get('id'))
    tracker = get_tracker()

    # Una sola lectura de jobs para toda la pantalla (ver jobs_cache en
    # _converted_job_for_lead). Los sets de ids de abajo son para
    # compute_workflow_steps_for_lead: sin pasarlos, el heuristico legacy de
    # _instancia_es_de_la_cuenta() releeria list_jobs()/list_leads() del
    # disco una vez por lead abierto (N+1 -- ver dashboard()/jobs_list()).
    _jobs_cache = list_jobs()
    _leads_job_ids_cache = {j.get('id') for j in _jobs_cache}
    _leads_lead_ids_cache = {l.get('id') for l in leads}

    for lead in leads:
        if lead.get('status') not in ('Convertido', 'Perdido'):
            try:
                steps, progress, _ = compute_workflow_steps_for_lead(
                    lead, _jobs_cache,
                    job_ids_cache=_leads_job_ids_cache, lead_ids_cache=_leads_lead_ids_cache)
                pending = next((s for s in steps if s.get('status') != 'done'), None)
                lead['workflow_progress'] = progress
                lead['next_task'] = pending.get('name') if pending else (lead.get('next_task') or 'Trabajo aceptado')
            except Exception:
                lead['workflow_progress'] = lead.get('workflow_progress') or 0

        # Fechas estilo SN + indicador de disponibilidad (rojo = ya hay boda ese dia)
        if lead.get('created'):
            try:
                lead['created_display'] = datetime.strptime(str(lead['created'])[:10], '%Y-%m-%d').strftime('%d %b %Y')
            except Exception:
                lead['created_display'] = lead['created']
        fecha = lead.get('fecha_tentativa')
        lead['boda_date_display'] = _format_pretty_date(fecha) if fecha else None
        conflict_job = booked_dates.get(fecha) if fecha else None
        lead['date_conflict'] = conflict_job
        other_leads_same_date = [i for i in open_leads_by_date.get(fecha, []) if i != lead.get('id')] if fecha else []
        lead['other_lead_conflict'] = bool(fecha) and not conflict_job and bool(other_leads_same_date)
        lead['date_available'] = bool(fecha) and not conflict_job and not other_leads_same_date

        # Ultimo correo real del lead (subject + chip con fecha, como SN)
        mails = tracker.list_for_lead(lead.get('id'))
        if mails:
            last = max(mails, key=lambda m: m.get('sent_at') or '')
            lead['last_mail_subject'] = last.get('subject') or ''
            if last.get('status') in ('opened', 'clicked'):
                when = (last.get('opened_at') or last.get('sent_at') or '')[:10]
                lead['last_mail_chip'] = ('cyan', f'EMAIL OPENED ON {_format_pretty_date(when)[5:].upper()}' if when else 'EMAIL OPENED')
            elif last.get('status') == 'sent':
                when = (last.get('sent_at') or '')[:10]
                lead['last_mail_chip'] = ('yellow', f'EMAIL SENT ON {_format_pretty_date(when)[5:].upper()}' if when else 'EMAIL SENT')
            else:
                lead['last_mail_chip'] = ('gray', (last.get('status') or 'NO EMAIL').upper())

    leads.sort(key=lambda l: l.get('created') or '', reverse=True)
    email_templates = [tpl for tpl in store.list('email_templates') if tpl.get('activo', True)]
    return render_template('leads.html', leads=leads, email_templates=email_templates,
                          lead_sources=_configured_lead_sources())


@app.route('/api/leads/export.csv')
def api_leads_export_csv():
    """Exporta leads a CSV."""
    from flask import Response
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Lead Created', 'Lead Name', 'Email', 'Phone', 'Type', 'Boda Date', 'Mail Status', 'Next Task', 'Status', 'Source'])
    for lead in sorted(_open_leads(), key=lambda l: l.get('created', ''), reverse=True):
        writer.writerow([
            lead.get('created', ''),
            lead.get('nombre', ''),
            lead.get('email', ''),
            lead.get('telefono', ''),
            lead.get('tipo_evento', ''),
            lead.get('fecha_tentativa', ''),
            lead.get('mail_status', ''),
            lead.get('next_task', ''),
            lead.get('status', ''),
            lead.get('fuente', ''),
        ])

    return Response(output.getvalue(), mimetype='text/csv', headers={
        'Content-Disposition': 'attachment; filename=norkevin-leads.csv'
    })


@app.route('/api/leads/export.xls')
def api_leads_export_xls():
    """Exporta las fuentes de leads a XLS (tabla HTML con MIME de Excel)."""
    from flask import Response

    lead_source_counts = defaultdict(int)
    lead_source_jobs = defaultdict(int)
    for lead in _open_leads():
        lead_source_counts[lead.get('fuente') or 'Sin fuente'] += 1
    leads_by_id = {lead.get('id'): lead for lead in list_leads()}
    for job in _canonical_jobs():
        lead = leads_by_id.get(job.get('lead_id'))
        lead_source_jobs[(lead or {}).get('fuente') or job.get('lead_source') or 'Sin fuente'] += 1

    rows = ''.join(
        f'<tr><td>{source}</td><td>{count}</td><td>{lead_source_jobs.get(source, 0)}</td></tr>'
        for source, count in sorted(lead_source_counts.items(), key=lambda kv: kv[1], reverse=True)
    )
    html = (
        '<html><head><meta charset="utf-8"></head><body>'
        '<table border="1"><tr><th>Lead Source</th><th>Leads</th><th>Jobs</th></tr>'
        f'{rows}</table></body></html>'
    )
    return Response(html, mimetype='application/vnd.ms-excel', headers={
        'Content-Disposition': 'attachment; filename=norkevin-lead-sources.xls'
    })


@app.route('/api/clients/import', methods=['POST'])
def api_clients_import():
    """Importa clientes desde un CSV (columnas flexibles ES/EN)."""
    import csv
    import io
    import uuid

    data = request.get_json(silent=True) or {}
    csv_text = data.get('csv') or ''
    if not csv_text.strip():
        return jsonify({'ok': False, 'error': 'CSV vacio'}), 400

    header_map = {
        'first_name': 'first_name', 'firstname': 'first_name', 'nombre': 'first_name', 'name': 'first_name',
        'last_name': 'last_name', 'lastname': 'last_name', 'apellido': 'last_name',
        'email': 'email', 'correo': 'email', 'e-mail': 'email',
        'phone': 'phone', 'telefono': 'phone', 'tel': 'phone', 'celular': 'phone',
        'address': 'address', 'direccion': 'address', 'ciudad': 'address', 'city': 'address',
        'company': 'company', 'empresa': 'company',
    }

    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return jsonify({'ok': False, 'error': 'CSV sin encabezados'}), 400

    existing_emails = {_norm_email(c.get('email')) for c in store.list('clients') if c.get('email')}
    today = datetime.now().isoformat()[:10]
    imported, skipped = 0, 0

    for row in reader:
        mapped = {}
        for key, value in row.items():
            field = header_map.get((key or '').strip().lower())
            if field and value:
                mapped[field] = value.strip()
        if not mapped.get('first_name') and not mapped.get('email'):
            skipped += 1
            continue
        if mapped.get('email') and _norm_email(mapped['email']) in existing_emails:
            skipped += 1
            continue
        client = {
            'id': 'client-' + uuid.uuid4().hex[:8],
            'first_name': mapped.get('first_name') or (mapped.get('email') or '').split('@')[0],
            'last_name': mapped.get('last_name', ''),
            'company': mapped.get('company', ''),
            'phone': mapped.get('phone', ''),
            'email': mapped.get('email', ''),
            'address': mapped.get('address', ''),
            'created': today,
            'estado': 'Activo',
            'tenant_id': get_current_tenant_id(),
        }
        store.upsert('clients', client)
        if client['email']:
            existing_emails.add(_norm_email(client['email']))
        imported += 1

    return jsonify({'ok': True, 'imported': imported, 'skipped': skipped,
                    'message': f'{imported} clientes importados' + (f', {skipped} omitidos (duplicados o sin datos)' if skipped else '')})


@app.route('/leads/<lead_id>')
def lead_detail(lead_id):
    """Lead Detail con workflow vertical + Mail Log + Quote Wizard."""
    from src.mail_tracker import get_tracker
    from src.workflow import LEAD_WORKFLOW, PRODUCTION_WORKFLOW

    lead = get_lead(lead_id)
    if not lead:
        abort(404)

    converted_job = _converted_job_for_lead(lead)
    if converted_job:
        return redirect(url_for('job_detail', job_id=converted_job['id']))

    # Combinar LEAD + PRODUCTION workflows para mostrar todo en el sidebar
    lead_tmpl = LEAD_WORKFLOW()
    prod_tmpl = PRODUCTION_WORKFLOW()

    # Workflow lead
    lead_steps, lead_progress, _ = compute_workflow_steps_for_lead(lead)
    # Workflow production (si esta convertido)
    job_vinculado = None
    client = None
    prod_steps = []
    for j in _canonical_jobs():
        if j.get('lead_id') == lead_id:
            job_vinculado = j
            client = get_client(j.get('client_id', ''))
            prod_steps, _, _ = compute_workflow_steps_for_job(j)
            break
    if not job_vinculado:
        for step in prod_tmpl.steps:
            prod_steps.append({
                'id': step.id,
                'name': step.name,
                'description': step.description,
                'email_template_id': step.email_template_id,
                'action_type': step.action_type.value if hasattr(step.action_type, 'value') else str(step.action_type),
                'scheduled': None,
                'executed_at': None,
                'status': 'pending',
                'result': None,
                'locked': step.id != 'job_accepted',
            })

    # Combinar steps (los primeros 4 son lead, el resto production)
    workflow_steps = lead_steps + prod_steps
    workflow_progress = lead_progress
    workflow_name = f'BODAS {_brand_display_name_for_tenant(lead.get("tenant_id")).upper()}'

    # Mail Log (tracking real)
    tracker = get_tracker()
    mail_log = tracker.list_for_lead(lead_id)
    if job_vinculado:
        mail_log += tracker.list_for_job(job_vinculado['id'])
    mail_log.sort(key=lambda m: m.get('sent_at', ''), reverse=True)

    # Quotes y Payments
    quotes_list = [q for q in store.list('quotes') if q.get('lead_id') == lead_id]
    jobs_del_lead = [j['id'] for j in list_jobs() if j.get('lead_id') == lead_id]
    payments_del_lead = [p for p in list_payments() if p.get('job_id') in jobs_del_lead]
    contracts_del_lead = [
        c for c in store.list('contracts')
        if c.get('lead_id') == lead_id or c.get('job_id') in jobs_del_lead
    ]
    questionnaires_del_lead = [
        q for q in store.list('questionnaires')
        if q.get('lead_id') == lead_id or q.get('job_id') in jobs_del_lead
    ]
    files_del_lead = [
        f for f in store.list('files')
        if f.get('lead_id') == lead_id or f.get('job_id') in jobs_del_lead
    ]

    quotes_invoices = {
        'invoices': f'{len(payments_del_lead)} invoices' if payments_del_lead else 'No invoices yet',
        'quotes': f'{len([q for q in quotes_list if q.get("status") != "Aceptada"])} pendientes, {len([q for q in quotes_list if q.get("status") == "Aceptada"])} aceptadas' if quotes_list else 'Quotes will appear here',
        'contracts': 'Sin contratos',
    }

    # Packages para el quote wizard
    packages = _load_packages()
    email_templates = [tpl for tpl in store.list('email_templates') if tpl.get('activo', True)]

    return render_template('lead_detail.html',
                          lead=lead,
                          workflow_steps=workflow_steps,
                          workflow_progress=workflow_progress,
                          workflow_name=workflow_name,
                          client=client,
                          quotes_invoices=quotes_invoices,
                          quotes=quotes_list,
                          payments=payments_del_lead,
                          contracts=contracts_del_lead,
                          questionnaires=questionnaires_del_lead,
                          files=files_del_lead,
                          job_vinculado=job_vinculado,
                          mail_log=mail_log,
                          email_templates=email_templates,
                          packages=packages)


def _load_packages():
    """Carga el catalogo de paquetes via el JsonStore compartido (respeta
    CRM_DATA_DIR, a diferencia de la version vieja con ruta fija)."""
    return store.list('packages')


@app.route('/clients')
def clients_list():
    """Clients Overview estilo Studio Ninja."""
    clients = _canonical_clients()
    leads = _open_leads()
    jobs = _canonical_jobs()
    payments = list_payments()
    # Un solo indice para toda la tabla: sin esto habria que recorrer los
    # jobs (y releer job_clients) una vez por cliente.
    jobs_de = _jobs_por_cliente(jobs)
    for client in clients:
        client_id = client.get('id')
        email = _norm_email(client.get('email'))
        client['leads_count'] = sum(
            1 for lead in leads
            if lead.get('client_id') == client_id or (email and _norm_email(lead.get('email')) == email)
        )
        client['jobs_count'] = len(jobs_de.get(client_id, []))
        client['balance_due'] = sum(
            float(payment.get('amount') or 0)
            for payment in payments
            if payment.get('client_id') == client_id and payment.get('status') != 'Pagado'
        )
        try:
            client['created_display'] = datetime.strptime(str(client.get('created'))[:10], '%Y-%m-%d').strftime('%d %b %Y')
        except Exception:
            client['created_display'] = client.get('created')
    # c.get('created', '') solo usa el default si la KEY no existe -- un
    # cliente con 'created': None explicito (bodas sin fecha del import de
    # Studio Ninja) hace que sort() intente comparar str con None y tumbe
    # toda la pagina (confirmado en produccion: TypeError '<' not
    # supported between instances of 'str' and 'NoneType'). 'or ''' cubre
    # los dos casos: key ausente Y key presente pero None/vacia.
    clients.sort(key=lambda c: c.get('created') or '', reverse=True)
    return render_template('clients.html', clients=clients)


@app.route('/api/clients/export.csv')
def api_clients_export_csv():
    """Exporta clientes al formato de Studio Ninja."""
    from flask import Response
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date Created', 'First Name', 'Last Name', 'Company', 'Phone', 'Email', 'Address', 'Source', 'Status'])
    for client in sorted(list_clients(), key=lambda c: c.get('created', ''), reverse=True):
        writer.writerow([
            client.get('created', ''),
            client.get('first_name', ''),
            client.get('last_name', ''),
            client.get('company', ''),
            client.get('phone', ''),
            client.get('email', ''),
            client.get('address', ''),
            client.get('source', ''),
            client.get('estado', ''),
        ])

    return Response(output.getvalue(), mimetype='text/csv', headers={
        'Content-Disposition': 'attachment; filename=norkevin-clients.csv'
    })


@app.route('/equipo')
def equipo_list():
    """Equipo - miembros del staff."""
    team = store.list('team')
    team.sort(key=lambda m: m.get('created') or '', reverse=True)
    return render_template('equipo.html', team=team)


# ============================================================
# LEAD ACTIONS: trigger workflow step + create quote
# ============================================================

@app.route('/api/leads/<lead_id>/trigger-step', methods=['POST'])
def api_lead_trigger_step(lead_id):
    """Dispara manualmente un step del workflow (enviar email ahora)."""
    lead = get_lead(lead_id)
    if not lead:
        return jsonify({'ok': False, 'error': 'Lead no encontrado'}), 404

    data = request.get_json() or {}
    step_id = data.get('step_id', '')
    send_email = data.get('send_email', True)

    result = _complete_lead_workflow_step(
        lead,
        step_id,
        send_email=send_email,
        result_message=data.get('result_message'),
        subject_override=data.get('subject'),
        body_override=data.get('body'),
    )
    if result.get('warning'):
        return jsonify({'ok': False, 'error': result['warning']}), 400
    if result.get('already_done'):
        return jsonify({'ok': False, 'error': 'Step ya ejecutado'}), 400

    return jsonify({
        'ok': True,
        'step': result.get('step'),
        'email': lead.get('email', ''),
        'mail_id': result.get('mail_id'),
        'job_id': result.get('job_id'),
        'client_id': result.get('client_id'),
        'converted': result.get('converted', False),
        'message': (
            'Job created from lead.'
            if result.get('converted') else
            f'Email enviado a {lead.get("email", "")}. Registrado en Mail Log.'
            if send_email else 'Task completed'
        )
    })


@app.route('/api/leads/<lead_id>/quote', methods=['POST'])
def api_lead_create_quote(lead_id):
    """Crea una cotizacion para el lead."""
    lead = get_lead(lead_id)
    if not lead:
        return jsonify({'ok': False, 'error': 'Lead no encontrado'}), 404

    data = request.get_json() or {}
    paquete_nombre = data.get('paquete_nombre', '').strip()
    precio_total = data.get('precio_total', 0)
    plan_pago = int(data.get('plan_pago', 1))
    notas = data.get('notas', '')
    incluye = data.get('incluye', '')
    status = data.get('status') or 'Enviada'

    if not paquete_nombre:
        return jsonify({'ok': False, 'error': 'Nombre del paquete requerido'}), 400

    import uuid
    from datetime import datetime as _dt

    quote_id = 'quote-' + uuid.uuid4().hex[:8]
    cuota_monto = round(float(precio_total) / plan_pago, 2)

    quote = {
        'id': quote_id,
        'lead_id': lead_id,
        'paquete_nombre': paquete_nombre,
        'precio_total': float(precio_total),
        'plan_pago': plan_pago,
        'cuota_monto': cuota_monto,
        'notas': notas,
        'incluye': incluye if isinstance(incluye, list) else (incluye.split('\n') if incluye else []),
        'status': status,
        'created': _dt.now().isoformat()[:10],
        'aceptada_en': None,
        'tenant_id': lead.get('tenant_id') or get_current_tenant_id(),
    }
    _assign_quote_number(quote)
    store.upsert('quotes', quote)

    if status != 'Borrador':
        lead['status'] = 'Cotizando'
        lead['next_task'] = f'Cotizacion enviada ({paquete_nombre})'
        upsert_lead(lead)

    return jsonify({
        'ok': True,
        'quote_id': quote_id,
        'quote': quote,
        'accept_link': f'/api/leads/{lead_id}/accept-quote'
    })


# ============================================================
# API: Mail Tracking (enviar email + tracking)
# ============================================================
@app.route('/api/leads/<lead_id>/send-email', methods=['POST'])
def api_lead_send_email(lead_id):
    """Envia un email al lead y lo registra en mail_log."""
    from src.mail_tracker import get_tracker

    lead = get_lead(lead_id)
    if not lead:
        return jsonify({'ok': False, 'error': 'Lead no encontrado'}), 404

    data = request.get_json() or {}
    template = _get_email_template(data.get('template_id'))
    subject = data.get('subject') or (template or {}).get('asunto') or \
        f'Mensaje de {_brand_display_name_for_tenant(lead.get("tenant_id"))}'
    body = data.get('body') or (template or {}).get('cuerpo') or ''
    subject = _render_message_template(subject, lead=lead)
    body = _render_message_template(body, lead=lead)
    if not lead.get('email'):
        return jsonify({'ok': False, 'error': 'Este lead no tiene email'}), 400

    # STAGE 2 (agosto 2026): cola de aprobacion en vez de entrega inmediata.
    # Clave por minuto: es un mensaje libre escrito a mano, un reenvio
    # deliberado unos minutos despues es legitimo y no debe bloquearse.
    tracker = get_tracker()
    entry = tracker.queue_email(
        to_email=lead.get('email', ''),
        subject=subject,
        body=body,
        template_id=data.get('template_id'),
        lead_id=lead_id,
        client_id=lead.get('client_id') or None,
        source='manual:lead-send-email',
        idempotency_key=f'leadmail:{lead_id}:{_idempotency_minute_bucket()}',
    )
    lead['mail_status'] = _lead_mail_status_chip(entry)
    upsert_lead(lead)
    if data.get('complete_step') and data.get('step_id'):
        _complete_lead_workflow_step(
            lead,
            data.get('step_id'),
            result_message=f"EMAIL sent from modal: {subject}",
            send_email=False,
        )

    return jsonify({
        'ok': True,
        'mail_id': entry['id'],
        'to': lead.get('email'),
        'subject': subject,
        'delivery_status': entry.get('status'),
        'blocked_reason': entry.get('blocked_reason'),
        'mail_warning': _mail_delivery_warning(entry),
    })


@app.route('/api/leads/<lead_id>/questionnaires', methods=['POST'])
def api_lead_create_questionnaire(lead_id):
    """Crea el mismo cuestionario real que se usa desde Jobs."""
    import uuid
    lead = get_lead(lead_id)
    if not lead:
        return jsonify({'ok': False, 'error': 'Lead no encontrado'}), 404
    data = request.get_json() or {}
    job = get_job(lead.get('lead_id_job', '')) if lead.get('lead_id_job') else None
    client = get_client(lead.get('client_id', '')) if lead.get('client_id') else None
    # Kevin: "porque hay 9 cuestionarios por boda? no tiene sentido" -- este
    # endpoint creaba uno nuevo en CADA llamada (reenviar, recordatorio,
    # etc.) sin revisar si ya habia uno pendiente para el mismo lead, a
    # diferencia del flujo desde Job que si reutiliza su borrador. Ahora
    # reutiliza el cuestionario existente para este lead mientras no este
    # ya Respondido, igual que hace _create_job_questionnaire.
    questionnaire = next(
        (q for q in store.list('questionnaires')
         if q.get('lead_id') == lead_id and q.get('status') != 'Respondido'),
        None,
    )
    if questionnaire is None:
        questionnaire = {
            'id': 'questionnaire-' + uuid.uuid4().hex[:8],
            'lead_id': lead_id,
            'client_id': lead.get('client_id', ''),
            'job_id': lead.get('lead_id_job', ''),
            'created': datetime.now().isoformat()[:10],
            'tenant_id': lead.get('tenant_id') or get_current_tenant_id(),
        }
    questionnaire['name'] = data.get('name') or questionnaire.get('name') or 'Cuestionario de Bodas Generico'
    questionnaire['template_name'] = 'Cuestionario de Bodas Generico'
    questionnaire['questions'] = data.get('questions') or questionnaire.get('questions') or QUESTIONNAIRE_QUESTIONS
    questionnaire['status'] = data.get('status') or ('Sent' if data.get('send_email', True) else 'Draft')
    store.upsert('questionnaires', questionnaire)

    questionnaire_path = f"/questionnaires/{questionnaire['id']}"
    questionnaire_url = request.url_root.rstrip('/') + questionnaire_path
    mail_id = None
    mail_warning = None
    if data.get('send_email', True):
        from src.mail_tracker import get_tracker
        to_email = _email_for(client=client, lead=lead)
        if to_email:
            subject = _render_message_template(
                data.get('subject') or 'Cuestionario para tu boda',
                client=client,
                lead=lead,
                job=job,
            )
            body = _render_message_template(
                data.get('body') or 'Hola %client_name%,\n\nTe comparto el cuestionario para preparar todos los detalles de tu boda:\n\n[LINK AL CUESTIONARIO]\n\nSaludos,\nKevin',
                client=client,
                lead=lead,
                job=job,
            )
            body = _inject_link(body, questionnaire_url,
                                placeholders=['[LINK AL CUESTIONARIO]',
                                              'Please view the questionnaire online by clicking here'],
                                fallback_label='Completa el cuestionario aqui')
            # STAGE 2 (agosto 2026): cola de aprobacion en vez de entrega
            # inmediata. Clave por dia: un reenvio deliberado manana debe
            # poder salir, pero dos clicks seguidos hoy no deben duplicar.
            entry = get_tracker().queue_email(
                to_email=to_email,
                subject=subject,
                body=body,
                template_id=data.get('template_id') or 'tpl-cuestionario-prod',
                lead_id=lead_id,
                job_id=lead.get('lead_id_job'),
                client_id=lead.get('client_id') or None,
                attachments=[questionnaire['name']],
                source='manual:questionnaire-lead',
                idempotency_key=f"questionnaire:{questionnaire['id']}:notify:{date.today().isoformat()}",
            )
            mail_id = entry['id']
            mail_warning = _mail_delivery_warning(entry)
        else:
            mail_warning = 'Este lead no tiene email registrado -- el cuestionario se creo pero no se mando nada.'

    return jsonify({
        'ok': True,
        'questionnaire': questionnaire,
        'questionnaire_path': questionnaire_path,
        'questionnaire_url': questionnaire_url,
        'mail_id': mail_id,
        'mail_warning': mail_warning,
    })


@app.route('/api/leads/<lead_id>/files', methods=['POST'])
def api_lead_create_file_record(lead_id):
    """Registra un archivo local asociado a un lead."""
    import uuid
    lead = get_lead(lead_id)
    if not lead:
        return jsonify({'ok': False, 'error': 'Lead no encontrado'}), 404
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'name requerido'}), 400
    file_record = {
        'id': 'file-' + uuid.uuid4().hex[:8],
        'lead_id': lead_id,
        'client_id': lead.get('client_id', ''),
        'job_id': lead.get('lead_id_job', ''),
        'name': name,
        'size': data.get('size') or '0 MB',
        'status': data.get('status') or 'Attached',
        'created': datetime.now().isoformat()[:10],
        'tenant_id': lead.get('tenant_id') or get_current_tenant_id(),
    }
    store.upsert('files', file_record)
    return jsonify({'ok': True, 'file': file_record})


@app.route('/api/mail/<mail_id>/opened', methods=['POST'])
def api_mail_mark_opened(mail_id):
    """Marca un email como abierto (tracking)."""
    from src.mail_tracker import get_tracker

    tracker = get_tracker()
    entry = tracker.mark_opened(mail_id)
    if not entry:
        return jsonify({'ok': False, 'error': 'Email no encontrado'}), 404
    return jsonify({'ok': True, 'mail_id': mail_id, 'status': 'opened'})


@app.route('/api/mail/<mail_id>/clicked', methods=['POST'])
def api_mail_mark_clicked(mail_id):
    """Marca un email como clickeado."""
    from src.mail_tracker import get_tracker

    tracker = get_tracker()
    entry = tracker.mark_clicked(mail_id)
    if not entry:
        return jsonify({'ok': False, 'error': 'Email no encontrado'}), 404
    return jsonify({'ok': True, 'mail_id': mail_id, 'status': 'clicked'})


@app.route('/api/mail/recent')
def api_mail_recent():
    """Lista los ultimos emails enviados."""
    from src.mail_tracker import get_tracker
    limit = request.args.get('limit', 50, type=int)
    tracker = get_tracker()
    return jsonify({'emails': tracker.list_recent(limit), 'stats': tracker.stats()})


@app.route('/api/leads/<lead_id>/accept-quote', methods=['POST'])
def api_lead_accept_quote(lead_id):
    """Acepta una cotizacion. Convierte lead -> job, genera invoices."""
    lead = get_lead(lead_id)
    if not lead:
        return jsonify({'ok': False, 'error': 'Lead no encontrado'}), 404

    quotes_for_lead = [q for q in store.list('quotes') if q.get('lead_id') == lead_id]
    accepted_quotes = [q for q in quotes_for_lead if q.get('status') == 'Aceptada' and q.get('job_id')]
    pending_quotes = [q for q in quotes_for_lead if q.get('status') != 'Aceptada']

    already_converted = bool(lead.get('lead_id_job') or _find_job_for_lead(lead) or accepted_quotes)
    if already_converted:
        quote = max(accepted_quotes, key=lambda q: (q.get('aceptada_en') or '', q.get('created') or '', q.get('id') or '')) if accepted_quotes else None
        result = _convert_lead_to_job(lead, quote=quote, status='Confirmado', create_payments=bool(quote))
        return jsonify({
            'ok': True,
            'already_converted': True,
            'lead_id': lead_id,
            'client_id': result['client']['id'],
            'job_id': result['job']['id'],
            'invoice_ids': result['invoice_ids'],
            'workflow_instance_id': result['workflow_instance_id'],
            'message': 'Este lead ya estaba convertido. Se devolvio el cliente/job existente sin duplicar.'
        })

    if not pending_quotes:
        return jsonify({'ok': False, 'error': 'No hay cotizacion pendiente'}), 400

    quote = max(pending_quotes, key=lambda q: (q.get('created') or '', q.get('id') or ''))
    result = _convert_lead_to_job(lead, quote=quote, status='Confirmado', create_payments=True)

    return jsonify({
        'ok': True,
        'already_converted': False,
        'lead_id': lead_id,
        'client_id': result['client']['id'],
        'job_id': result['job']['id'],
        'invoice_ids': result['invoice_ids'],
        'workflow_instance_id': result['workflow_instance_id'],
        'client_created': result['client_created'],
        'job_created': result['job_created'],
        'invoices_created': result['invoices_created'],
        'workflow_created': result['workflow_created'],
        'message': 'Cotizacion aceptada. Lead convertido a job sin duplicar cliente/job/invoices.'
    })


def _job_saldo_pendiente(job_payments):
    """Saldo que el cliente todavia debe. 'amount' de una fila pendiente YA
    es su saldo actual (se ajusta con cada abono), por eso no se le resta
    lo pagado otra vez."""
    return sum(float(p.get('amount') or 0)
               for p in (job_payments or []) if p.get('status') != 'Pagado')


def _job_estado_label(job, job_payments=None):
    """Estado real del Job: fecha del evento PRIMERO, dinero despues, y el
    workflow solo como ultimo desempate.

    Kevin: "no quiero que todos los Jobs aparezcan 100% Completado
    simplemente porque el workflow este tecnicamente marcado asi... un
    evento futuro no deberia verse como completado si todavia no ocurrio".

    El % del workflow mide cuantos pasos administrativos estan hechos (y en
    los jobs importados de Studio Ninja se marcaron como saltados de golpe,
    justamente para no re-enviar correos). Eso no dice nada sobre si la boda
    ya paso, ni sobre si el cliente ya pago.

    AGREGADO (uso diario, agosto 2026): una boda que YA OCURRIO pero que
    todavia tiene saldo pendiente NO es un trabajo completado -- es un
    trabajo que falta cobrar. Antes se pintaba "Completada" en verde solo
    porque el workflow estaba al 100%, y un job con dinero pendiente
    desaparecia visualmente entre los cerrados. Ahora ese caso sale como
    "Por cobrar" y cuenta como ACTIVO.

    Devuelve (label, tone, estado_key). `estado_key` es la forma canonica
    y estable del estado, para que la interfaz filtre por ELLA en vez de
    volver a deducir el estado por su cuenta (era justo la incoherencia:
    el chip decia "Completada" y el filtro "Completados" no lo encontraba).
    """
    estado = (job.get('status') or '').strip()
    if estado == 'Archivado':
        return 'Archivado', 'muted', 'archivado'
    if estado == 'Cancelado':
        return 'Cancelado', 'red', 'cancelado'

    dias = job.get('dias_restantes')
    if dias is None:
        return 'Sin fecha', 'muted', 'sin_fecha'
    if dias > 0:
        return 'Proxima', 'violet', 'proxima'
    if dias == 0:
        return 'Hoy', 'orange', 'hoy'

    # Ya paso. Antes de hablar de "completado", el dinero.
    saldo = _job_saldo_pendiente(job_payments)
    if saldo > 0:
        return f'Por cobrar Q{saldo:,.0f}', 'red', 'por_cobrar'

    if (job.get('workflow_progress') or 0) >= 100 or estado == 'Listo':
        return 'Completada', 'green', 'completada'
    return 'Por cerrar', 'orange', 'por_cerrar'


# Estados en los que el job todavia PIDE ALGO: aparecer o no en "Activos"
# se decide por esta lista, no por el string libre de job['status'].
ESTADOS_JOB_ACTIVOS = {'proxima', 'hoy', 'por_cobrar', 'por_cerrar', 'sin_fecha'}
ESTADOS_JOB_COMPLETOS = {'completada'}


# Roles de cliente que un job puede tener hoy. El orden importa: es el
# orden en que se muestran y en que se arma el To: de un correo.
ROLES_CLIENTE_JOB = (
    ('client_id', 'Principal'),
    ('secondary_client_id', 'Cliente adicional'),
    ('planner_client_id', 'Wedding planner'),
)


ETIQUETA_ROL = {
    'principal': 'Principal',
    'pareja': 'Pareja',
    'wedding_planner': 'Wedding planner',
    'contacto': 'Contacto',
    'otro': 'Otro',
}

# El frontend existente manda 'secondary'/'planner'. Se aceptan como
# alias para no tener que tocar el JS de job_detail.html, pero por dentro
# todo se guarda con el rol canonico.
ALIAS_ROL_LEGACY = {
    'secondary': 'pareja',
    'planner': 'wedding_planner',
    'principal': 'principal',
    'primary': 'principal',
}

ROL_PRINCIPAL = 'principal'
ROL_PAREJA = 'pareja'
ROL_PLANNER = 'wedding_planner'
ROL_CONTACTO = 'contacto'
ROL_OTRO = 'otro'
ROLES_JOB_CLIENT = (ROL_PRINCIPAL, ROL_PAREJA, ROL_PLANNER, ROL_CONTACTO, ROL_OTRO)

# Los roles que reciben documentos cliente-facing por defecto. El wedding
# planner NO esta aca a proposito: Kevin fue explicito -- "no mandar
# accidentalmente un contrato a un wedding planner". Ser MIEMBRO del job y
# ser DESTINATARIO de una accion concreta son dos cosas distintas.
ROLES_DESTINATARIOS_DOCUMENTOS = (ROL_PRINCIPAL, ROL_PAREJA)

# Mapeo de los 3 campos viejos al rol canonico. Se sigue LEYENDO para no
# romper jobs existentes, pero nada nuevo escribe aca.
LEGACY_CLIENT_FIELDS = (
    ('client_id', ROL_PRINCIPAL),
    ('secondary_client_id', ROL_PAREJA),
    ('planner_client_id', ROL_PLANNER),
)


def _job_client_relations(job):
    """Relaciones job<->cliente en forma canonica, vengan de donde vengan.

    ANTES: el job tenia 3 campos fijos (client_id, secondary_client_id,
    planner_client_id). Un cuarto cliente no cabia, y la unica salida
    hubiera sido seguir agregando client_4_id, client_5_id...

    AHORA: la tabla `job_clients` guarda 0..N relaciones, cada una con su
    rol. Los 3 campos viejos se siguen leyendo como ADAPTER (jobs creados
    antes de este cambio siguen mostrando a su gente), pero solo si el job
    todavia no tiene relaciones nuevas -- si las tiene, mandan ellas y los
    campos viejos se ignoran para no duplicar a la misma persona.

    Devuelve [{'client_id','role','orden'}...] ordenado. NO toca el store
    ni migra nada: es solo lectura.
    """
    job_id = (job or {}).get('id')
    if not job_id:
        return []

    relaciones = [r for r in store.list('job_clients') if r.get('job_id') == job_id]
    if relaciones:
        relaciones.sort(key=lambda r: (r.get('orden', 0), r.get('created_at') or ''))
        return [{'client_id': r.get('client_id'), 'role': r.get('role') or ROL_OTRO,
                 'orden': r.get('orden', 0)} for r in relaciones if r.get('client_id')]

    # Adapter legacy: 3 campos viejos -> roles canonicos.
    salida = []
    vistos = set()
    for idx, (campo, rol) in enumerate(LEGACY_CLIENT_FIELDS):
        cid = (job or {}).get(campo)
        if cid and cid not in vistos:
            vistos.add(cid)
            salida.append({'client_id': cid, 'role': rol, 'orden': idx})
    return salida


def _relaciones_por_job(jobs):
    """job_id -> relaciones canonicas, leyendo `job_clients` UNA sola vez.

    Misma semantica que _job_client_relations() pero para un conjunto de
    jobs: si el job tiene relaciones nuevas mandan ellas; si no, se leen
    los 3 campos legacy. Se deduplica conservando el orden.

    Existe para que las pantallas que recorren varios jobs no llamen a
    _job_client_relations() una vez por fila -- eso relee el archivo
    entero por cada boda. Con 20 bodas no se nota; con 300 la ficha del
    cliente se arrastra. Es la misma regla en un solo lugar, no una
    segunda implementacion.
    """
    por_job = {}
    for rel in store.list('job_clients'):
        jid, cid = rel.get('job_id'), rel.get('client_id')
        if jid and cid:
            por_job.setdefault(jid, []).append(rel)

    salida = {}
    for job in jobs:
        rels = por_job.get(job.get('id'))
        if rels:
            rels.sort(key=lambda r: (r.get('orden', 0), r.get('created_at') or ''))
            lista = [{'client_id': r.get('client_id'),
                      'role': r.get('role') or ROL_OTRO,
                      'orden': r.get('orden', 0)} for r in rels]
        else:
            lista = [{'client_id': job.get(campo), 'role': rol, 'orden': i}
                     for i, (campo, rol) in enumerate(LEGACY_CLIENT_FIELDS)
                     if job.get(campo)]
        vistos, dedup = set(), []
        for r in lista:
            if r['client_id'] not in vistos:
                vistos.add(r['client_id'])
                dedup.append(r)
        salida[job.get('id')] = dedup
    return salida


def _jobs_por_cliente(jobs=None):
    """Indice cliente -> jobs en los que participa, en CUALQUIER rol.

    Bug de uso diario que esto corrige: `/clients` y `/clients/<id>` solo
    miraban `job.client_id`. La otra mitad de la pareja entra al job como
    rol `pareja` (o, en jobs viejos, por `secondary_client_id`), nunca como
    `client_id`. Resultado: abrias la ficha de la novia y su propia boda no
    aparecia -- decia que no tenia ningun job. Lo mismo con el wedding
    planner.

    Se lee `job_clients` UNA sola vez y se arma el indice de una pasada, en
    vez de llamar a _job_client_relations() por cada job de cada cliente
    (eso era una lectura del store por job, N x M).

    Misma semantica que _job_client_relations(): si el job tiene relaciones
    nuevas mandan ellas y los campos viejos se ignoran; si no las tiene, se
    leen los 3 campos legacy. Es solo lectura: no migra ni escribe nada.
    """
    jobs = _canonical_jobs() if jobs is None else jobs
    relaciones = _relaciones_por_job(jobs)

    indice = {}
    for job in jobs:
        for rel in relaciones.get(job.get('id'), []):
            indice.setdefault(rel['client_id'], []).append(job)
    return indice


def _set_job_clients(job, relaciones, *, tenant_id=None):
    """Define TODAS las relaciones de un job de una sola vez (0..N).

    `relaciones`: lista de (client_id, role) o dicts {'client_id','role'}.

    Reglas:
      - Identidad por tenant_id + client_id. NUNCA por nombre ni email.
      - Un client_id de OTRA empresa se rechaza con TenantMismatchError:
        aunque alguien arme el request a mano, un job de Astral no puede
        quedar asociado a un cliente de Norkevin.
      - La misma persona repetida en dos roles se guarda UNA vez, con el
        primer rol indicado.
      - Es idempotente: llamarla dos veces con lo mismo deja lo mismo.

    Devuelve la lista canonica resultante.
    """
    import uuid
    job_id = (job or {}).get('id')
    if not job_id:
        raise ValueError('_set_job_clients necesita un job con id')
    tenant_id = tenant_id or job.get('tenant_id') or get_current_tenant_id()

    normalizadas = []
    vistos = set()
    for item in relaciones or []:
        if isinstance(item, dict):
            cid, rol = item.get('client_id'), item.get('role') or ROL_OTRO
        else:
            cid, rol = item[0], (item[1] if len(item) > 1 else ROL_OTRO)
        if not cid or cid in vistos:
            continue
        if rol not in ROLES_JOB_CLIENT:
            rol = ROL_OTRO

        cliente = store.get('clients', cid)
        if not cliente:
            # Cliente inexistente (o de otra empresa, que el store scoped
            # ya oculta): no se asocia. No se inventa.
            log_security_event('JOB_CLIENT_NO_ENCONTRADO', tabla='job_clients',
                               registro=cid, cuenta_activa=tenant_id)
            continue
        dueno = cliente.get('tenant_id')
        if dueno and dueno != tenant_id:
            log_security_event('CROSS_TENANT_JOB_CLIENT_BLOCKED', tabla='job_clients',
                               registro=cid, cuenta_activa=tenant_id,
                               cuenta_del_registro=dueno)
            raise TenantMismatchError(
                'No se puede asociar a este job un cliente de otra empresa.')
        vistos.add(cid)
        normalizadas.append((cid, rol))

    existentes = [r for r in store.list('job_clients') if r.get('job_id') == job_id]
    deseados = {cid for cid, _r in normalizadas}
    for r in existentes:
        if r.get('client_id') not in deseados:
            store.delete('job_clients', r.get('id'))

    por_client = {r.get('client_id'): r for r in existentes}
    ahora = datetime.now().isoformat()
    for orden, (cid, rol) in enumerate(normalizadas):
        previo = por_client.get(cid)
        registro = {
            'id': previo.get('id') if previo else f'jc-{uuid.uuid4().hex[:10]}',
            'tenant_id': tenant_id,
            'job_id': job_id,
            'client_id': cid,
            'role': rol,
            'orden': orden,
            'created_at': (previo or {}).get('created_at') or ahora,
        }
        store.upsert('job_clients', registro)

    return _job_client_relations(job)


def _sincronizar_campos_legacy(job):
    """Refleja las 3 primeras relaciones canonicas en los campos viejos.

    NO es la fuente de verdad -- job_clients lo es. Existe solo para que
    cualquier vista o integracion que todavia lea job['client_id'] o
    job['secondary_client_id'] no quede desfasada tras una edicion. Del
    cuarto cliente en adelante no hay campo legacy donde reflejarlo, y esta
    bien: por eso existe el modelo nuevo."""
    relaciones = _job_client_relations(job)
    por_rol = {}
    for r in relaciones:
        por_rol.setdefault(r['role'], []).append(r['client_id'])

    principal = (por_rol.get(ROL_PRINCIPAL) or [None])[0]
    pareja = (por_rol.get(ROL_PAREJA) or [None])[0]
    planner = (por_rol.get(ROL_PLANNER) or [None])[0]

    # Si nadie quedo como principal pero hay relaciones, la primera manda:
    # un job sin client_id romperia vistas que todavia lo asumen.
    if not principal and relaciones:
        principal = relaciones[0]['client_id']

    job['client_id'] = principal
    job['secondary_client_id'] = pareja
    job['planner_client_id'] = planner
    return job


def _job_recipient_clients(job, clients_by_id=None):
    """Clientes que SI deben recibir un documento cliente-facing.

    Distinto de _job_clients(): ese devuelve a todos los miembros del job.
    Este filtra por rol, para no mandarle un contrato al wedding planner
    solo porque esta asociado al job."""
    return [c for c in _job_clients(job, clients_by_id)
            if c.get('role') in ROLES_DESTINATARIOS_DOCUMENTOS]


def _job_clients(job, clients_by_id=None, relaciones=None):
    """TODOS los clientes de un job, en orden, con su rol.

    Un job de boda casi nunca tiene un solo cliente: estan los dos novios
    y a veces el wedding planner. El modelo ya lo soportaba
    (client_id / secondary_client_id / planner_client_id) y el import de
    Studio Ninja ya mapeaba 2 y 3 clientes, pero el resto del CRM seguia
    preguntando por `job['client_id']` como si fuera el unico -- en la
    lista de jobs se veia un solo nombre y los demas desaparecian.

    Devuelve [{'id','rol','cliente','nombre'}...] saltando los vacios y
    SIN repetir: si por un import quedo el mismo client_id en dos roles,
    aparece una sola vez (con el primer rol), en vez de duplicar a la
    persona en la interfaz y en el To: de los correos.

    clients_by_id: dict opcional para no ir al store por cada job cuando
    se esta armando una lista larga.

    relaciones: si ya se calcularon en lote (con _relaciones_por_job), se
    pasan aca. Sin esto, una lista de 300 bodas leia `job_clients` 300
    veces -- una vez por fila. El parametro es opcional para no romper a
    quien llama con un job suelto.
    """
    resultado = []
    for relacion in (relaciones if relaciones is not None
                     else _job_client_relations(job)):
        cid = relacion['client_id']
        cliente = (clients_by_id or {}).get(cid) if clients_by_id is not None else get_client(cid)
        if not cliente:
            # Cliente borrado: se omite en vez de romper la vista entera.
            continue
        nombre = (f"{cliente.get('first_name', '')} "
                  f"{cliente.get('last_name', '')}").strip() or cliente.get('nombre') or 'Cliente'
        resultado.append({
            'id': cid,
            'role': relacion['role'],
            'rol': ETIQUETA_ROL.get(relacion['role'], relacion['role']),
            'cliente': cliente,
            'nombre': nombre,
        })
    return resultado


def _job_clients_display(job, clients_by_id=None, relaciones=None):
    """Nombres de todos los clientes del job para mostrar en una lista."""
    clientes = _job_clients(job, clients_by_id, relaciones)
    if not clientes:
        return 'Sin cliente'
    return ' + '.join(c['nombre'] for c in clientes)


SCHEDULE_ACTIVE = 'active'
SCHEDULE_SUPERSEDED = 'superseded'
SCHEDULE_COMPLETED = 'completed'
SCHEDULE_CANCELLED = 'cancelled'
SCHEDULE_LEGACY = 'legacy_quarantined'
ESTADOS_SCHEDULE = (SCHEDULE_ACTIVE, SCHEDULE_SUPERSEDED, SCHEDULE_COMPLETED,
                    SCHEDULE_CANCELLED, SCHEDULE_LEGACY)


def _schedule_origin_key(tenant_id, job_id, origen):
    """Identidad logica de un calendario de pagos.

    'origen' es normalmente el quote_id aceptado. Dos generaciones del
    MISMO compromiso comercial comparten esta clave, y por eso no pueden
    convivir dos activas."""
    return f'{tenant_id}::{job_id}::{origen or "sin_origen"}'


def _active_schedule_for(tenant_id, job_id, origen):
    clave = _schedule_origin_key(tenant_id, job_id, origen)
    for s in store.list('payment_schedules'):
        if s.get('origin_key') == clave and s.get('status') == SCHEDULE_ACTIVE:
            return s
    return None


def _validar_schedule(total_plan, cuotas, suma_cuotas, price_total):
    """Discrepancias del plan. NO corrige nada: devuelve la lista.

    Kevin: "si hay discrepancia NO la corrijas silenciosamente". Un
    descuento real y un error de captura se ven igual desde aca, asi que
    la decision es humana. Lo que si se exige es que un schedule generado
    por el CRM a partir de una cotizacion valida cuadre exacto."""
    avisos = []
    if cuotas <= 0:
        avisos.append('el plan no tiene cuotas')
    if abs(round(suma_cuotas - total_plan, 2)) > 0.01:
        avisos.append(
            f'las cuotas suman {suma_cuotas:,.2f} pero el plan dice {total_plan:,.2f}')
    if price_total and abs(round(float(price_total) - total_plan, 2)) > 0.01:
        avisos.append(
            f'el plan ({total_plan:,.2f}) no coincide con job.price_total ({float(price_total):,.2f})')
    return avisos


def _crear_schedule(tenant_id, job_id, origen, *, total_plan, cuotas,
                    suma_cuotas, price_total=None, payment_ids=None):
    """Registra un calendario ACTIVO, si no hay otro para la misma
    identidad logica. Idempotente: si ya existe uno activo, lo devuelve
    sin crear otro ni tocar los pagos existentes."""
    import uuid
    existente = _active_schedule_for(tenant_id, job_id, origen)
    if existente:
        return existente, False

    avisos = _validar_schedule(total_plan, cuotas, suma_cuotas, price_total)
    schedule = {
        'id': f'sched-{uuid.uuid4().hex[:10]}',
        'tenant_id': tenant_id,
        'job_id': job_id,
        'origin': origen,
        'origin_key': _schedule_origin_key(tenant_id, job_id, origen),
        'status': SCHEDULE_ACTIVE,
        'total_plan': round(float(total_plan or 0), 2),
        'cuotas': cuotas,
        'suma_cuotas': round(float(suma_cuotas or 0), 2),
        'payment_ids': list(payment_ids or []),
        'avisos': avisos,
        'created_at': datetime.now().isoformat(),
        'superseded_by': None,
    }
    store.upsert('payment_schedules', schedule)
    if avisos:
        log_security_event('PAYMENT_SCHEDULE_CON_DISCREPANCIA',
                           tabla='payment_schedules', registro=schedule['id'],
                           cuenta_activa=tenant_id)
    return schedule, True


def supersede_schedule(schedule_id, nuevo_schedule_id=None, *, motivo=''):
    """Marca un calendario como reemplazado. NUNCA lo borra.

    Kevin: "si se cambia de cotizacion/plan NO borres el anterior... los
    payments ya realizados deben conservar su historia. No reasignes
    dinero automaticamente entre cotizaciones."

    Por eso esto solo cambia el estado del schedule viejo y deja el
    puntero al nuevo. Los pagos siguen colgando de su schedule original:
    lo que ya se cobro contra una cotizacion sigue perteneciendo a esa
    cotizacion."""
    schedule = store.get('payment_schedules', schedule_id)
    if not schedule:
        return None
    schedule['status'] = SCHEDULE_SUPERSEDED
    schedule['superseded_by'] = nuevo_schedule_id
    schedule['superseded_at'] = datetime.now().isoformat()
    if motivo:
        schedule['superseded_motivo'] = motivo
    store.upsert('payment_schedules', schedule)
    log_security_event('PAYMENT_SCHEDULE_SUPERSEDED', tabla='payment_schedules',
                       registro=schedule_id, cuenta_activa=schedule.get('tenant_id'))
    return schedule


def _job_schedules(job_id):
    return [s for s in store.list('payment_schedules') if s.get('job_id') == job_id]


def _job_payment_summary(job, job_payments):
    """UNA sola fuente de verdad para la plata de un job.

    Antes cada vista calculaba lo suyo: /jobs sumaba `balance_due` por su
    cuenta, job_detail volvia a sumar total/pagado con otra formula, y el
    chip de estado usaba una tercera. Con tres formulas para el mismo
    numero, tarde o temprano no coinciden.

    Reglas:
      - `amount` de una fila PENDIENTE ya es su saldo actual (se ajusta con
        cada abono), asi que no se le vuelve a restar lo pagado.
      - el total del job es lo cotizado (price_total) si existe; si no, la
        suma de las cuotas -- un job sin cotizacion formal igual tiene que
        mostrar un total coherente.
    """
    pagos = list(job_payments or [])
    pagado = sum(_row_paid_amount(p) for p in pagos)
    pendiente = _job_saldo_pendiente(pagos)

    total_cuotas = sum(_row_original_amount(p) for p in pagos)
    try:
        total_cotizado = float((job or {}).get('price_total') or 0)
    except (TypeError, ValueError):
        total_cotizado = 0.0
    total = total_cotizado or total_cuotas

    vencidos = [p for p in pagos if p.get('status') == 'Late']
    proximas = sorted(
        (p for p in pagos if p.get('status') != 'Pagado' and p.get('due_date')),
        key=lambda p: p.get('due_date') or '')
    proximo = proximas[0] if proximas else None

    return {
        'total': total,
        'pagado': pagado,
        'pendiente': pendiente,
        'cuotas': len(pagos),
        'cuotas_pagadas': sum(1 for p in pagos if p.get('status') == 'Pagado'),
        'vencidas': len(vencidos),
        'proximo_pago_fecha': (proximo or {}).get('due_date'),
        'proximo_pago_monto': float((proximo or {}).get('amount') or 0) if proximo else 0.0,
        'esta_pagado': bool(pagos) and pendiente <= 0,
        # Discrepancia entre lo cotizado y lo que suman las cuotas: no se
        # corrige sola (puede ser un descuento legitimo), se expone.
        'descuadre_cotizado_vs_cuotas': (
            round(total_cotizado - total_cuotas, 2)
            if total_cotizado and total_cuotas else 0.0),
    }


def _job_orden_relevancia(job):
    """Orden por defecto: lo que pide atencion primero.

    Kevin: "por defecto quiero los jobs mas nuevos/relevantes primero, no
    los mas viejos".

    El orden anterior era `sorted(key=dias_restantes or 999)` y tenia dos
    bugs:
      1. Los dias de un evento pasado son NEGATIVOS, asi que ordenar
         ascendente ponia la boda MAS VIEJA en primer lugar -- justo lo
         contrario de lo util.
      2. `or 999`: para la boda de HOY, dias_restantes es 0, que es falsy,
         asi que se convertia en 999 y la boda de hoy caia al FINAL de la
         lista.

    Ahora: primero los eventos futuros (el mas proximo arriba), despues
    los ya ocurridos (el mas reciente arriba), y al final los que no
    tienen fecha. Empate resuelto por fecha de creacion descendente, para
    que el orden sea ESTABLE entre servidor e interfaz.
    """
    dias = job.get('dias_restantes')
    creado = str(job.get('created') or '')
    if dias is None:
        return (2, 0, creado, str(job.get('id') or ''))
    if dias >= 0:
        return (0, dias, creado, str(job.get('id') or ''))
    return (1, -dias, creado, str(job.get('id') or ''))


def _job_pago_label(job, job_payments):
    """Estado financiero, separado del estado del Job (Kevin lo pidio
    explicitamente: "mantener el estado financiero separado")."""
    if not job_payments:
        return '', ''
    saldo = _job_saldo_pendiente(job_payments)
    vencidos = [p for p in job_payments if p.get('status') == 'Late']
    if vencidos:
        return 'Pago atrasado', 'red'
    if saldo <= 0:
        return 'Pagado', 'green'
    return f'Saldo Q{saldo:,.0f}', 'muted'


@app.route('/jobs')
def jobs_list():
    """Jobs Overview con barra de progreso workflow (estilo Studio Ninja)."""
    from datetime import datetime
    jobs = _canonical_jobs()
    clients = {c['id']: c for c in _canonical_clients()}
    payments_by_job = defaultdict(list)
    for p in list_payments():
        payments_by_job[p.get('job_id')].append(p)

    # Kevin: los mismos indicadores de conflicto de fecha de /leads, vistos
    # desde el lado de los Jobs -- rojo = otra boda real ya agendada ese
    # mismo dia (doble booking de verdad), naranja = un lead todavia abierto
    # esta preguntando por esa misma fecha (util para saber si conviene
    # ofrecerle otro dia antes de que avance mas).
    other_jobs_by_date = defaultdict(list)
    for j in jobs:
        if j.get('boda_date') and j.get('status') not in ('Archivado',):
            other_jobs_by_date[j['boda_date']].append(j.get('id'))
    open_leads_by_date = defaultdict(list)
    for l in _open_leads():
        if l.get('fecha_tentativa'):
            open_leads_by_date[l['fecha_tentativa']].append(l.get('id'))

    # Una sola lectura de `job_clients` para toda la lista. Antes cada fila
    # llamaba a _job_clients_display(), que por dentro releia la tabla
    # entera: con 300 bodas eran 300 lecturas de disco por pagina.
    _rel_por_job = _relaciones_por_job(jobs)

    # Idem para compute_workflow_steps_for_job: sin esto, el heuristico
    # legacy de _instancia_es_de_la_cuenta() releeria list_jobs()/list_leads()
    # una vez por fila para las instancias sin tenant_id (ver dashboard()).
    _jobs_job_ids_cache = {j.get('id') for j in list_jobs()}
    _jobs_lead_ids_cache = {l.get('id') for l in list_leads()}

    for j in jobs:
        try:
            d = datetime.strptime(j['boda_date'], '%Y-%m-%d').date()
            j['dias_restantes'] = (d - datetime.now().date()).days
            j['boda_date_display'] = d.strftime('%a, %d %b %Y')
        except Exception:
            j['dias_restantes'] = None
            j['boda_date_display'] = None
        fecha = j.get('boda_date')
        other_jobs_same_date = [i for i in other_jobs_by_date.get(fecha, []) if i != j.get('id')] if fecha else []
        j['date_conflict'] = bool(other_jobs_same_date)
        j['lead_interest_conflict'] = bool(fecha) and not j['date_conflict'] and bool(open_leads_by_date.get(fecha))
        try:
            steps, prog, _ = compute_workflow_steps_for_job(
                j, job_ids_cache=_jobs_job_ids_cache, lead_ids_cache=_jobs_lead_ids_cache)
            pending = [s for s in steps if s['status'] == 'pending']
            j['next_task'] = pending[0]['name'] if pending else 'Completado'
            j['workflow_progress'] = prog
        except Exception:
            j['next_task'] = '—'
        # Todos los clientes del job, no solo el principal: una boda tiene
        # dos novios (y a veces wedding planner) y antes solo se veia uno.
        _rel_j = _rel_por_job.get(j.get('id'), [])
        j['clientes'] = _job_clients(j, clients, _rel_j)
        j['client_name'] = _job_clients_display(j, clients, _rel_j)
        j['clientes_count'] = len(j['clientes'])
        job_payments = payments_by_job.get(j.get('id'), [])
        resumen = _job_payment_summary(j, job_payments)
        j['pagos'] = resumen
        j['payments_count'] = resumen['cuotas']
        j['balance_due'] = resumen['pendiente']
        j['estado_label'], j['estado_tone'], j['estado_key'] = _job_estado_label(j, job_payments)
        j['es_activo'] = j['estado_key'] in ESTADOS_JOB_ACTIVOS
        j['es_completado'] = j['estado_key'] in ESTADOS_JOB_COMPLETOS
        j['pago_label'], j['pago_tone'] = _job_pago_label(j, job_payments)
    jobs.sort(key=_job_orden_relevancia)
    all_clients = sorted(clients.values(), key=lambda c: (c.get('first_name') or '').lower())
    return render_template('jobs.html', jobs=jobs, all_clients=all_clients)


@app.route('/jobs/<job_id>')
def job_detail(job_id):
    """Job Detail con Production Workflow vertical."""
    job = get_job(job_id)
    if not job:
        abort(404)
    workflow_steps, workflow_progress, workflow_name = compute_workflow_steps_for_job(job)
    lead = get_lead(job.get('lead_id', '')) if job.get('lead_id') else None
    if lead:
        lead_steps, lead_progress, lead_workflow_name = compute_workflow_steps_for_lead(lead)
    else:
        lead_steps, lead_progress, lead_workflow_name = [], 0, 'Lead'
    client = get_client(job.get('client_id', ''))
    secondary_client = get_client(job.get('secondary_client_id')) if job.get('secondary_client_id') else None
    planner_client = get_client(job.get('planner_client_id')) if job.get('planner_client_id') else None
    all_recipient_emails = ', '.join(_job_all_recipient_emails(job, primary_client=client, lead=lead))
    payments = [p for p in list_payments() if p.get('job_id') == job_id]
    for p in payments:
        p['due_date_display_es'] = _format_date_es(p.get('due_date')) or p.get('due_date') or '-'
        p['paid_date_display_es'] = _format_date_es(p.get('paid_date') or p.get('fecha_pago'))
    quotes = [
        q for q in store.list('quotes')
        if q.get('job_id') == job_id or (job.get('lead_id') and q.get('lead_id') == job.get('lead_id'))
    ]
    quotes_by_id = {q.get('id'): q for q in quotes}
    invoice_groups_map = {}
    for p in sorted(payments, key=lambda row: (row.get('quote_id') or row.get('invoice_id') or '', row.get('due_date') or '', row.get('cuota') or 0)):
        group_key = p.get('quote_id') or p.get('invoice_group_id') or p.get('invoice_id') or p.get('id')
        quote = quotes_by_id.get(p.get('quote_id')) or {}
        group = invoice_groups_map.setdefault(group_key, {
            'id': group_key,
            'invoice_id': p.get('invoice_id') or p.get('id'),
            'title': quote.get('paquete_nombre') or quote.get('title') or p.get('concepto') or p.get('invoice_id') or 'Invoice',
            'quote': quote,
            'payments': [],
            'total': 0.0,
            'paid': 0.0,
            'balance': 0.0,
            'next_due': '',
            'status': 'Pagado',
        })
        group['payments'].append(p)
        group['total'] += _row_original_amount(p)
        group['paid'] += _row_paid_amount(p)
        if p.get('status') != 'Pagado':
            group['status'] = p.get('status') or 'Unpaid'
            if not group['next_due'] or (p.get('due_date') or '') < group['next_due']:
                group['next_due'] = p.get('due_date') or ''
    invoice_groups = []
    for group in invoice_groups_map.values():
        group['balance'] = max(group['total'] - group['paid'], 0)
        group['next_due_display_es'] = _format_date_es(group.get('next_due')) or group.get('next_due') or '-'
        invoice_groups.append(group)
    contracts = [c for c in store.list('contracts') if c.get('job_id') == job_id]
    questionnaires = [
        q for q in store.list('questionnaires')
        if q.get('job_id') == job_id or (job.get('lead_id') and q.get('lead_id') == job.get('lead_id'))
    ]
    files = [
        f for f in store.list('files')
        if f.get('job_id') == job_id or (job.get('lead_id') and f.get('lead_id') == job.get('lead_id'))
    ]
    email_templates = [tpl for tpl in store.list('email_templates') if tpl.get('activo', True)]
    email_template_names = {tpl.get('id'): tpl.get('name') for tpl in email_templates}
    mail_log = [
        m for m in store.list('mail_log')
        if m.get('job_id') == job_id or (job.get('lead_id') and m.get('lead_id') == job.get('lead_id'))
    ]
    mail_log.sort(key=lambda m: m.get('sent_at') or '', reverse=True)
    pending_steps = [s for s in workflow_steps if s['status'] == 'pending']
    job['production_tasks'] = ', '.join(s['name'] for s in pending_steps[:3]) if pending_steps else 'Sin tareas pendientes'
    job['invoices'] = f"{len(invoice_groups)} invoices" if invoice_groups else 'Sin invoices'
    # FUENTE UNICA de la plata del job. El detalle calculaba lo suyo con
    # una formula propia, la lista otra y el chip de estado una tercera --
    # tres numeros para lo mismo, que tarde o temprano no coinciden.
    # Ahora los tres salen de _job_payment_summary().
    pagos_resumen = _job_payment_summary(job, payments)
    total_paid = pagos_resumen['pagado']
    balance_due = pagos_resumen['pendiente']

    # Estado del job: la MISMA autoridad que usa la lista (estado_key).
    job['estado_label'], job['estado_tone'], job['estado_key'] = _job_estado_label(job, payments)
    job['es_activo'] = job['estado_key'] in ESTADOS_JOB_ACTIVOS
    job['es_completado'] = job['estado_key'] in ESTADOS_JOB_COMPLETOS

    # Calendario de pagos vigente (los superseded NO cuentan como activos).
    schedules_job = _job_schedules(job_id)
    schedule_activo = next(
        (s for s in schedules_job if s.get('status') == SCHEDULE_ACTIVE), None)
    # Modelo N-clientes para la vista: la plantilla recorre esta lista en
    # vez de los 3 campos fijos, y ofrece el selector de rol.
    job_clientes = _job_clients(job)
    roles_disponibles = [(r, ETIQUETA_ROL.get(r, r)) for r in ROLES_JOB_CLIENT]

    return render_template('job_detail.html',
                          job_clientes=job_clientes,
                          roles_disponibles=roles_disponibles,
                          pagos_resumen=pagos_resumen,
                          schedule_activo=schedule_activo,
                          schedules_job=schedules_job,
                          job=job,
                          lead=lead,
                          lead_steps=lead_steps,
                          lead_progress=lead_progress,
                          lead_workflow_name=lead_workflow_name,
                          workflow_steps=workflow_steps,
                          workflow_progress=workflow_progress,
                          workflow_name=workflow_name,
                          client=client,
                          secondary_client=secondary_client,
                          planner_client=planner_client,
                          all_recipient_emails=all_recipient_emails,
                          all_clients=list_clients(),
                          payments=payments,
                          invoice_groups=invoice_groups,
                          quotes=quotes,
                          contracts=contracts,
                          questionnaires=questionnaires,
                          files=files,
                          email_templates=email_templates,
                          email_template_names=email_template_names,
                          mail_log=mail_log,
                          total_paid=total_paid,
                          balance_due=balance_due)


@app.route('/jobs/<job_id>/quote/<quote_type>/new')
def quote_builder(job_id, quote_type):
    """Pantalla completa para crear cotizaciones desde un job."""
    job = get_job(job_id)
    if not job:
        abort(404)

    client = get_client(job.get('client_id', ''))
    lead = get_lead(job.get('lead_id', '')) if job.get('lead_id') else None
    raw_packages = _load_packages()
    packages = []
    for package in raw_packages:
        includes = package.get('includes') or package.get('incluye') or []
        if isinstance(includes, str):
            includes = [line.strip() for line in includes.splitlines() if line.strip()]
        packages.append({
            'id': package.get('id') or re.sub(r'[^a-z0-9]+', '-', (package.get('name') or package.get('Name') or 'package').lower()).strip('-'),
            'name': package.get('name') or package.get('Name') or 'Package',
            'category': package.get('category') or package.get('Categoria') or 'Package',
            'description': package.get('description') or package.get('Notas') or '',
            'price': float(package.get('price') or package.get('Precio Q') or 0),
            'includes': includes,
        })

    normalized = 'pick_and_choose' if quote_type in ('pick-and-choose', 'pick_and_choose', 'pick') else 'fixed'
    return render_template(
        'quote_builder.html',
        job=job,
        client=client,
        lead=lead,
        packages=packages,
        quote_kind=normalized,
        quote_kind_label='Pick & Choose Quote' if normalized == 'pick_and_choose' else 'Fixed Quote',
    )


def generate_team_payments_for_job(job):
    """Cuando un Job esta LISTO, genera pagos automaticos para cada miembro del equipo
    que participo en la boda, basado en sus tarifas."""
    import uuid
    from datetime import datetime as _dt

    team = store.list('team')
    if not team:
        return []

    pagos_generados = []
    job_name = job.get('nombre', '')
    package = job.get('package', 'Basico')

    for member in team:
        # La tarifa depende del package
        if 'Premium' in package or 'premium' in package.lower():
            tarifa = member.get('tarifa_boda', 0)
        else:
            tarifa = member.get('tarifa_evento', 0)

        if tarifa <= 0:
            continue

        pay_id = 'pay-team-' + uuid.uuid4().hex[:8]
        pay = {
            'id': pay_id,
            'invoice_id': f'TEAM-{uuid.uuid4().hex[:6].upper()}',
            'team_id': member['id'],
            'job_id': job.get('id', ''),
            'concepto': f'{member["rol"]} - {job_name}',
            'amount': tarifa,
            'due_date': _dt.now().isoformat()[:10],
            'status': 'Pendiente',
            'tipo': 'team_payment',
        }
        store.upsert('payments', pay)
        pagos_generados.append(pay_id)

    return pagos_generados


@app.route('/api/jobs/<job_id>/trigger-step', methods=['POST'])
def api_job_trigger_step(job_id):
    """Dispara manualmente un step del production workflow."""
    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404

    data = request.get_json() or {}
    step_id = data.get('step_id', '')
    result = _complete_job_workflow_step(job, step_id)
    if result.get('warning') == 'Step no encontrado':
        return jsonify({'ok': False, 'error': result['warning']}), 404
    if result.get('warning'):
        return jsonify({'ok': False, 'error': result['warning']}), 400
    if result.get('already_done'):
        return jsonify({'ok': False, 'error': 'Step ya ejecutado'}), 400

    return jsonify({
        'ok': True,
        'step': result.get('step'),
        'action': result.get('action'),
        'next_task': result.get('next_task'),
        'workflow_progress': result.get('workflow_progress'),
        'pagos_equipo_generados': result.get('pagos_equipo_generados', 0),
        'message': f'Step "{result.get("step")}" completado' +
                   (f'. {result.get("pagos_equipo_generados")} pagos al equipo generados.' if result.get('pagos_equipo_generados') else '')
    })


@app.route('/invoices')
def invoices_list():
    """Invoices Overview: una fila por factura (agrupa cuotas de un mismo
    quote/invoice_id), igual que la agrupacion que ya existe en job_detail
    pero a traves de todos los jobs en vez de uno solo."""
    payments_all = _visible_billable_payments()
    clients = {c['id']: c for c in list_clients()}
    jobs = {j['id']: j for j in list_jobs()}
    quotes_by_id = {q['id']: q for q in list_quotes()}

    invoice_groups_map = {}
    for p in sorted(payments_all, key=lambda row: (row.get('quote_id') or row.get('invoice_id') or '', row.get('due_date') or '', row.get('cuota') or 0)):
        group_key = p.get('quote_id') or p.get('invoice_id') or p.get('id')
        quote = quotes_by_id.get(p.get('quote_id')) or {}
        job = jobs.get(p.get('job_id'))
        client = clients.get(p.get('client_id'))
        group = invoice_groups_map.setdefault(group_key, {
            'invoice_id': p.get('invoice_id') or p.get('id'),
            'title': quote.get('paquete_nombre') or p.get('concepto') or p.get('invoice_id') or 'Invoice',
            'client_name': f"{client['first_name']} {client['last_name']}" if client else 'Sin cliente',
            'job_name': job.get('nombre') if job else 'Sin job',
            'total': 0.0,
            'paid': 0.0,
            'next_due': '',
            'status': 'Pagado',
        })
        group['total'] += coerce_amount(p.get('amount') or p.get('original_amount'))
        group['paid'] += coerce_amount(p.get('amount')) if p.get('status') == 'Pagado' else 0.0
        if p.get('status') != 'Pagado':
            group['status'] = p.get('status') or 'Pendiente'
            if not group['next_due'] or (p.get('due_date') or '') < group['next_due']:
                group['next_due'] = p.get('due_date') or ''

    invoices = []
    for group in invoice_groups_map.values():
        group['balance'] = max(group['total'] - group['paid'], 0)
        group['next_due_display'] = _format_date_es(group.get('next_due')) or group.get('next_due') or '-'
        invoices.append(group)
    invoices.sort(key=lambda g: (0 if g['status'] != 'Pagado' else 1, g.get('next_due') or ''))

    total_billed = sum(g['total'] for g in invoices)
    total_paid = sum(g['paid'] for g in invoices)
    total_balance = sum(g['balance'] for g in invoices)

    return render_template('invoices.html', invoices=invoices,
                          total_billed=total_billed, total_paid=total_paid, total_balance=total_balance)


@app.route('/payments')
def payments_list():
    """Payments Overview estilo Studio Ninja con totales y days_ago."""
    from datetime import datetime, date

    payments_all = _visible_billable_payments()
    clients = {c['id']: c for c in list_clients()}
    jobs = {j['id']: j for j in list_jobs()}

    for p in payments_all:
        c = clients.get(p.get('client_id', ''))
        p['client_name'] = f"{c['first_name']} {c['last_name']}" if c else '—'
        j = jobs.get(p.get('job_id', ''))
        p['job_name'] = j['nombre'] if j else '—'

        # Calcular days_ago
        try:
            d = datetime.strptime(p.get('due_date', ''), '%Y-%m-%d').date()
            days = (date.today() - d).days
            p['days_ago'] = days if days > 0 else None
            p['days_until'] = abs(days) if days < 0 else None
            p['due_date_display'] = _format_date_es(d)
            if days > 0 and p.get('status') == 'Pendiente':
                p['status'] = 'Late'
        except Exception:
            p['days_ago'] = None
            p['days_until'] = None
            p['due_date_display'] = None

    # Sort: Late primero, luego por due_date asc
    payments_all.sort(key=lambda p: (
        0 if p.get('status') == 'Late' else
        1 if p.get('status') == 'Pendiente' else
        2,
        p.get('due_date', '')
    ))

    # Totales
    total_due = sum(coerce_amount(p.get('amount')) for p in payments_all if p.get('status') != 'Pagado')
    total_expected = sum(coerce_amount(p.get('amount')) for p in payments_all if p.get('status') == 'Pendiente' and p.get('days_until'))
    total_unpaid = sum(coerce_amount(p.get('amount')) for p in payments_all if p.get('status') == 'Pendiente')
    total_late = sum(coerce_amount(p.get('amount')) for p in payments_all if p.get('status') == 'Late')
    total_paid = sum(coerce_amount(p.get('amount')) for p in payments_all if p.get('status') == 'Pagado')

    return render_template('payments.html',
                          payments=payments_all,
                          total_due=total_due,
                          total_unpaid=total_unpaid,
                          total_expected=total_expected,
                          total_late=total_late,
                          total_paid=total_paid)


@app.route('/api/payments')
def api_payments_list():
    """Lista pagos locales para diagnostico/UI."""
    visible = _visible_billable_payments()
    return jsonify({'ok': True, 'payments': visible, 'count': len(visible)})


def _row_saldo_vivo(row):
    """Cuanto le queda debiendo el cliente en ESTA cuota, ahora mismo.

    Es 'amount', no 'original_amount - paid_amount'. La diferencia es real y
    ya mordio una vez: _apply_payment_sequentially() reparte un sobrepago
    entre las cuotas siguientes bajando su 'amount' (y marcandolas 'Pagado'
    cuando llega a cero) SIN tocar 'paid_amount' -- porque ese credito no es
    dinero recibido en esa cuota, es saldo trasladado. Calcular el saldo
    restando paid_amount daria una cuota "vencida por Q5,000" que en
    realidad ya esta saldada, en un documento que ve el cliente.

    'original_amount' se sigue usando para el subtotal (el precio del
    contrato, que no cambia); 'amount' para lo que falta cobrar; y
    _row_paid_amount para lo efectivamente recibido en la cuota.
    """
    if (row.get('status') or '') in ('Pagado', 'Cancelado'):
        # Cancelado no se le cobra a nadie: no debe nada.
        return 0.0
    return round(max(float(row.get('amount') or 0), 0), 2)


def _invoice_estado(total, pagado, pendiente, schedule, *, cancelada=False):
    """Estado de la factura como documento (no de una cuota suelta).

    Sistema de documentos (29-ago-2026). Kevin pidio estados claros:
    BORRADOR / PENDIENTE / PARCIALMENTE PAGADA / PAGADA / VENCIDA /
    CANCELADA. No hay un campo 'estado de factura' en el modelo -- una
    factura es el conjunto de cuotas que comparten invoice_id -- asi que se
    DEDUCE de esas cuotas, que son la fuente de verdad de siempre. No se
    escribe nada: esto solo mira.

    El orden importa: pagada gana sobre vencida (una factura saldada no
    puede estar vencida), y vencida gana sobre parcial (si hay algo
    vencido, es lo que el cliente necesita ver primero).

    Devuelve (etiqueta, tono, detalle) -- el tono es el sufijo del badge
    compartido, nunca el unico portador del significado.
    """
    hoy = date.today()
    if cancelada:
        return 'Cancelada', 'neutral', 'Esta factura fue cancelada.'
    if total <= 0:
        return 'Borrador', 'neutral', 'Todavia no tiene montos definidos.'
    if pendiente <= 0.005:
        return 'Pagada', 'success', 'No queda saldo pendiente.'

    vencidas = []
    for fila in schedule:
        # Una cuota cancelada no se le cobra a nadie: no puede estar
        # vencida ni sumar al aviso.
        if (fila.get('status') or '') == 'Cancelado':
            continue
        if _row_saldo_vivo(fila) <= 0.005:
            continue
        try:
            if date.fromisoformat(str(fila.get('due_date') or '')) < hoy:
                vencidas.append(fila)
        except (ValueError, TypeError):
            # due_date vacio o mal formado: no se puede afirmar que este
            # vencido, asi que no se afirma.
            continue
    if vencidas:
        monto = sum(_row_saldo_vivo(f) for f in vencidas)
        cuantas = len(vencidas)
        return ('Vencida', 'danger',
                f'{cuantas} pago{"s" if cuantas != 1 else ""} vencido'
                f'{"s" if cuantas != 1 else ""} por Q{monto:,.2f}.')
    if pagado > 0.005:
        return 'Parcialmente pagada', 'warning', 'Ya se recibio una parte del total.'
    return 'Pendiente', 'info', 'Todavia no se ha recibido ningun pago.'


def _invoice_document(invoice_id, *, tenant_id=None):
    """Arma TODO lo que necesita un documento de factura (web o PDF) a
    partir del modelo real: payments agrupados por invoice_id, su quote, su
    job y su cliente.

    Un solo lugar produce estos datos para que la factura web, la interna y
    el PDF no puedan mostrar cifras distintas del mismo documento. No
    calcula dinero nuevo: reutiliza _row_original_amount/_row_paid_amount,
    los mismos helpers que ya usan /invoices y el portal.

    Devuelve None si no existe (el llamador decide el 404).
    """
    payments_all = _visible_billable_payments(tenant_id)
    selected = next((p for p in payments_all
                     if p.get('invoice_id') == invoice_id or p.get('id') == invoice_id), None)
    if not selected:
        return None

    quote = store.get('quotes', selected.get('quote_id', '')) if selected.get('quote_id') else None
    if quote:
        schedule = [p for p in payments_all
                    if p.get('quote_id') == quote.get('id') and p.get('job_id') == selected.get('job_id')]
    else:
        # Sin cotizacion, la factura es el conjunto de filas que comparten
        # invoice_id (asi se agrupan las cuotas desde el importador y desde
        # la creacion manual de facturas).
        mismo = [p for p in payments_all if p.get('invoice_id') and p.get('invoice_id') == selected.get('invoice_id')]
        schedule = mismo or [selected]
    schedule.sort(key=lambda p: (p.get('due_date') or '', str(p.get('cuota') or ''), p.get('id') or ''))

    job = get_job(selected.get('job_id', '')) if selected.get('job_id') else None
    client = get_client(selected.get('client_id', '')) if selected.get('client_id') else None
    lead = get_lead(job.get('lead_id', '')) if (job and job.get('lead_id')) else None

    # Las tres cifras del documento tienen que cuadrar entre si SIEMPRE
    # (total = pagado + pendiente), porque es lo primero que el cliente
    # suma con la vista. Por eso:
    #   total     = precio del contrato (original_amount, no cambia nunca)
    #   pendiente = lo que de verdad falta cobrar (saldo vivo por cuota)
    #   pagado    = total - pendiente, o sea todo lo ya cubierto
    # 'pagado' NO es la suma de paid_amount a proposito: cuando un sobrepago
    # se traslada como credito a la cuota siguiente, esa cuota queda saldada
    # sin que su paid_amount suba. Sumar paid_amount mostraria una factura
    # saldada como "parcialmente pagada, faltan Q5,000".
    # Una cuota cancelada no se factura: queda fuera del total (si no, el
    # cliente veria un total mas alto del que se le va a cobrar) pero sigue
    # apareciendo en el historial, marcada como cancelada, para que no
    # parezca que un pago "desaparecio".
    cobrables = [p for p in schedule if (p.get('status') or '') != 'Cancelado']
    total = round(sum(_row_original_amount(p) for p in cobrables), 2)
    pendiente = round(min(round(sum(_row_saldo_vivo(p) for p in cobrables), 2), total), 2)
    pagado = round(max(total - pendiente, 0), 2)

    cancelada = bool(schedule) and all((p.get('status') or '') == 'Cancelado' for p in schedule)
    etiqueta, tono, detalle = _invoice_estado(total, pagado, pendiente, schedule, cancelada=cancelada)

    # --- filas para el historial/calendario que renderiza el componente ---
    hoy = date.today()
    filas = []
    proximo = None
    # La moneda sale del tema de la cuenta, no escrita a mano: una cuenta
    # que no facture en quetzales mostraria el simbolo equivocado.
    simbolo_moneda = (_document_theme(
        (job or {}).get('tenant_id') or (selected or {}).get('tenant_id')
    ) or {}).get('currency_symbol') or 'Q'
    for fila in schedule:
        importe = _row_original_amount(fila)
        cobrado = _row_paid_amount(fila)
        saldo = _row_saldo_vivo(fila)
        vence = fila.get('due_date') or ''
        cancelada_fila = (fila.get('status') or '') == 'Cancelado'
        pagada_completa = (not cancelada_fila) and saldo <= 0.005
        try:
            vencida = (not pagada_completa) and (not cancelada_fila) and date.fromisoformat(str(vence)) < hoy
        except (ValueError, TypeError):
            vencida = False

        if cancelada_fila:
            estado = 'scheduled'
        elif pagada_completa:
            estado = 'paid'
        elif vencida:
            estado = 'due'
        elif proximo is None and vence:
            # Sin fecha no se puede llamar "proximo pago" a nada.
            estado = 'next'
            proximo = fila
        else:
            estado = 'scheduled'

        # El timeline separa "posicion en el plan" (Pago 2/5) de "estado"
        # (Pagado / Vencido / Proximo pago), en vez de mezclarlos en una
        # sola cadena: asi el componente puede darle a cada uno su propio
        # peso visual.
        # OJO con el nombre de esta variable: `etiqueta` (sin sufijo) es el
        # estado del DOCUMENTO, ya calculado arriba y devuelto como
        # 'estado_label'. Llamar igual a la de cada cuota la pisaba en cada
        # vuelta del bucle, y la factura terminaba mostrando en su badge la
        # etiqueta de la ultima fila ("PAGADO", "PROGRAMADO", "CANCELADO")
        # en vez de su propio estado. Por eso va con sufijo _fila.
        posicion = f"Pago {fila.get('cuota')}" if fila.get('cuota') else 'Pago'
        if cancelada_fila:
            cuando = _format_date_es(vence) or vence or 'Sin fecha'
            etiqueta_fila = 'Cancelado'
        elif pagada_completa:
            cuando = _format_date_es(fila.get('paid_date') or fila.get('fecha_pago') or vence) or vence or 'Sin fecha'
            etiqueta_fila = 'Pagado'
        else:
            cuando = _format_date_es(vence) or vence or 'Sin fecha'
            etiqueta_fila = 'Vencido' if vencida else ('Próximo pago' if estado == 'next' else 'Programado')
            if cobrado > 0.005:
                etiqueta_fila += f' (abonado {simbolo_moneda}{cobrado:,.2f})'
        filas.append({
            'estado': estado, 'cuando': cuando,
            'posicion': posicion, 'etiqueta': etiqueta_fila,
            # 'nota' se conserva por compatibilidad con consumidores previos
            # de esta estructura, en minusculas y sin acentos como estaba.
            'nota': f'{posicion} · {etiqueta_fila.lower()}',
            'monto': importe if pagada_completa else saldo,
        })

    # El desglose de la factura sale del SNAPSHOT COMERCIAL de la cotizacion
    # aceptada, no de la plantilla ni del job. Ver _snapshot_comercial: una
    # factura representa un acuerdo, no lo reconstruye.
    snapshot = _snapshot_comercial(quote)
    concepto = (snapshot or {}).get('nombre') or selected.get('concepto') or 'Servicios'
    incluye = (snapshot or {}).get('incluye') or []

    return {
        'invoice_id': selected.get('invoice_id') or selected.get('id'),
        'selected': selected,
        'schedule': schedule,
        'quote': quote, 'job': job, 'client': client, 'lead': lead,
        'total': total, 'pagado': pagado, 'pendiente': pendiente,
        'estado_label': etiqueta, 'estado_tono': tono, 'estado_detalle': detalle,
        'filas_pago': filas,
        # El monto del proximo pago sale del saldo vivo, igual que el resto
        # del documento: restar paid_amount del original ignora el credito
        # trasladado por un sobrepago y le pediria al cliente mas de lo que
        # debe.
        'proximo': ({'cuando': _format_date_es(proximo.get('due_date')) or proximo.get('due_date'),
                     'monto': _row_saldo_vivo(proximo)}
                    if proximo else None),
        'concepto': concepto,
        'incluye': incluye or [],
        # Desglose resuelto por el mismo camino que la cotizacion: si el
        # paquete tiene servicios estructurados salen agrupados y con icono
        # por tipo; si es una cotizacion vieja, clasificacion legacy
        # conservadora. La factura y la cotizacion muestran lo mismo porque
        # lo resuelven con la misma funcion, no con dos plantillas parecidas.
        # Los grupos salen del MISMO snapshot que consume la cotizacion.
        # Es lo que garantiza que "Que incluye" e "Incluye" no puedan
        # divergir: no son dos lecturas parecidas, es la misma.
        'grupos': _quote_grupos_display(snapshot or {}),
        # Trazabilidad interna (no se muestra al cliente): de donde salio
        # este desglose. Sirve para el reporte de reconciliacion.
        'fuente_conceptos': (snapshot or {}).get('fuente') or 'sin_cotizacion',
        'source_quote_id': (quote or {}).get('id') or '',
        'emitida': _format_date_es(selected.get('created') or selected.get('issued_date')),
        # AUDITORIA 3-sep-2026 (Kevin: "de donde sale 'Vence'"). El dato es
        # el due_date de la cuota REPRESENTATIVA -- la primera del plan --
        # no un vencimiento del documento completo, que no existe en el
        # modelo. Con una sola cuota las dos cosas coinciden y "Vence" es
        # correcto. Con un plan de varias, mostrar "Vence: 22 julio 2025"
        # arriba de un calendario cuyo ultimo pago es de noviembre de 2026
        # se lee como una contradiccion. No se cambia el dato ni la logica:
        # se cambia la ETIQUETA para que describa lo que el dato realmente
        # es. El vencimiento de cada cuota sigue estando en el calendario.
        'vence': _format_date_es(selected.get('due_date')),
        'vence_label': ('Vence' if len([f for f in filas if f.get('estado') != 'cancelled']) <= 1
                        else 'Primer vencimiento'),
        'cliente_nombre': _client_name(client=client, lead=lead, job=job),
        'job_nombre': (job or {}).get('nombre') or '',
        'boda_fecha': _format_date_es((job or {}).get('boda_date')),
        'notas': selected.get('notas') or selected.get('nota') or '',
    }


@app.route('/invoices/<invoice_id>/documento')
def invoice_document_preview(invoice_id):
    """El MISMO documento de factura que ve el cliente, pero desde adentro
    (con sesion). Sirve para revisar como quedo antes de mandarlo.

    No emite token a proposito: emitir rota el enlace y dejaria muerto el
    que el cliente ya tenga. Por eso esta es una ruta interna aparte y no
    /i/<token> -- mirar una factura nunca deberia invalidar el enlace de
    nadie. La ruta NO esta en PUBLIC_PATTERNS: exige sesion como cualquier
    pantalla de administracion, y _visible_billable_payments() ya filtra por
    la cuenta activa, asi que no puede mostrar la factura de otra empresa."""
    doc = _invoice_document(invoice_id)
    if not doc:
        abort(404)
    tenant_id = ((doc['job'] or {}).get('tenant_id')
                 or (doc['selected'] or {}).get('tenant_id')
                 or get_current_tenant_id())
    terms = _load_terms_templates(tenant_id) if tenant_id else []
    return render_template(
        'invoice_document.html',
        doc=doc, theme=_document_theme(tenant_id), publico=False,
        terms_blocks=(terms[0].get('blocks') if terms else []) or [],
        pdf_url=f'/invoices/{invoice_id}/pdf',
        pay_url=None,
    )


@app.route('/invoices/<invoice_id>')
def invoice_view(invoice_id):
    """Vista interna de factura con calendario de pago."""
    payments_all = _visible_billable_payments()
    selected = next((p for p in payments_all if p.get('invoice_id') == invoice_id or p.get('id') == invoice_id), None)
    if not selected:
        abort(404)

    quote = store.get('quotes', selected.get('quote_id', '')) if selected.get('quote_id') else None
    if quote:
        schedule = [
            p for p in payments_all
            if p.get('quote_id') == quote.get('id') and p.get('job_id') == selected.get('job_id')
        ]
    else:
        schedule = [selected]

    schedule.sort(key=lambda p: (p.get('due_date') or '', p.get('cuota') or 0, p.get('invoice_id') or ''))
    job = get_job(selected.get('job_id', ''))
    client = get_client(selected.get('client_id', ''))
    lead = get_lead(job.get('lead_id', '')) if job and job.get('lead_id') else None
    total = sum(_row_original_amount(p) for p in schedule)
    paid = sum(_row_paid_amount(p) for p in schedule)
    balance = max(total - paid, 0)

    for row in schedule:
        row['is_selected'] = row.get('id') == selected.get('id')
        row['due_date_display_es'] = _format_date_es(row.get('due_date')) or row.get('due_date') or '-'
        row['paid_date_display_es'] = _format_date_es(row.get('paid_date') or row.get('fecha_pago'))
        row['last_action_display'] = row.get('last_action') or (f"Pagado el {row['paid_date_display_es']}" if row.get('paid_date_display_es') else '-')
        try:
            due = datetime.strptime(row.get('due_date', ''), '%Y-%m-%d').date()
            days = (due - date.today()).days
            row['relative_due'] = 'hoy' if days == 0 else (f'en {days} dias' if days > 0 else f'hace {abs(days)} dias')
        except Exception:
            row['relative_due'] = ''

    selected['due_date_display'] = _format_date_es(selected.get('due_date')) or None
    invoice_context = {
        'client_name': (
            f"{client.get('first_name', '')} {client.get('last_name', '')}".strip()
            if client else (job.get('client_name') if job else '')
        ),
        'wedding_date': _format_date_es(job.get('boda_date') if job else '') or (job.get('boda_date') if job else ''),
        'event_time': job.get('time') if job else '',
        'location': (job.get('location') if job else '') or (lead.get('location') if lead else '') or 'Sin ubicacion',
        'services': (
            quote.get('paquete_nombre') if quote else ''
        ) or selected.get('concepto') or 'Servicios de boda',
        'job_name': job.get('nombre') if job else '',
    }

    return render_template(
        'invoice_view.html',
        invoice=selected,
        schedule=schedule,
        quote=quote,
        job=job,
        lead=lead,
        client=client,
        invoice_context=invoice_context,
        total=total,
        paid=paid,
        balance=balance,
        company_email=get_settings().get('company', {}).get('email'),
    )


def _google_redirect_uri():
    """Redirect URI para el OAuth callback.

    Cloudflare Quick Tunnel termina TLS afuera y le manda a Flask trafico
    plano por HTTP, asi que request.host_url siempre dice 'http://' aunque el
    navegador este en 'https://'. Google exige HTTPS salvo en localhost, asi
    que forzamos https para cualquier host que no sea local.
    """
    host = request.host
    scheme = 'http' if host.startswith('127.0.0.1') or host.startswith('localhost') else 'https'
    return f'{scheme}://{host}' + url_for('auth_google_callback')


@app.route('/auth/google/start')
def auth_google_start():
    """Arranca el flujo OAuth para conectar Gmail (Settings > Email Settings)."""
    from src import gmail_delivery
    import secrets

    if not gmail_delivery.is_configured():
        return redirect(url_for('settings', google_status='not_configured'))

    redirect_uri = _google_redirect_uri()
    state = secrets.token_urlsafe(16)
    session_store = store.get_dict('google_oauth_state')
    session_store['state'] = state
    store.save_dict('google_oauth_state', session_store)
    return redirect(gmail_delivery.build_authorization_url(redirect_uri, state))


@app.route('/auth/google/callback')
def auth_google_callback():
    """Recibe el codigo de Google, lo cambia por tokens y los guarda."""
    from src import gmail_delivery

    error = request.args.get('error')
    if error:
        return redirect(url_for('settings', google_status='error', google_msg=error))

    code = request.args.get('code')
    state = request.args.get('state')
    expected_state = store.get_dict('google_oauth_state').get('state')
    if not code or not state or state != expected_state:
        return redirect(url_for('settings', google_status='error', google_msg='state invalido'))

    redirect_uri = _google_redirect_uri()
    try:
        token = gmail_delivery.exchange_code_for_token(code, redirect_uri)
        return redirect(url_for('settings', google_status='connected', google_email=token.get('email', '')))
    except Exception as exc:
        return redirect(url_for('settings', google_status='error', google_msg=str(exc)))


@app.route('/api/settings/google/disconnect', methods=['POST'])
def api_settings_google_disconnect():
    from src import gmail_delivery
    gmail_delivery.disconnect()
    return jsonify({'ok': True})


# ============================================================
# RECURRENTE por cuenta -- cada tenant conecta y administra su propia API
# ============================================================
@app.route('/api/settings/recurrente/status')
def api_settings_recurrente_status():
    from src import recurrente
    return jsonify({'ok': True, **recurrente.connection_status()})


@app.route('/api/settings/recurrente/connect', methods=['POST'])
def api_settings_recurrente_connect():
    """Conecta o actualiza las credenciales de Recurrente de la cuenta
    activa. Las llaves nunca vuelven en la respuesta -- solo un booleano
    y los ultimos 4 caracteres para que Kevin reconozca cual quedo puesta."""
    from src import recurrente
    data = request.get_json(silent=True) or {}
    secret_key = (data.get('secret_key') or '').strip()
    secret_key_test = (data.get('secret_key_test') or '').strip()
    mode = (data.get('mode') or 'live').strip()
    if not secret_key and not secret_key_test:
        return jsonify({'ok': False, 'error': 'Pega al menos una llave (live o de prueba)'}), 400
    recurrente.save_credentials(secret_key=secret_key, secret_key_test=secret_key_test, mode=mode)
    logger.info(f"Recurrente conectado para {get_current_tenant_id()} por {session.get('user_email')}")
    return jsonify({'ok': True, **recurrente.connection_status()})


@app.route('/api/settings/recurrente/test', methods=['POST'])
def api_settings_recurrente_test():
    from src import recurrente
    result = recurrente.test_connection()
    status = recurrente.connection_status()
    return jsonify({'ok': result.get('ok', False), 'error': result.get('error'), **status})


@app.route('/api/settings/recurrente/disconnect', methods=['POST'])
def api_settings_recurrente_disconnect():
    from src import recurrente
    recurrente.disconnect()
    logger.info(f"Recurrente desconectado para {get_current_tenant_id()} por {session.get('user_email')}")
    return jsonify({'ok': True})


@app.route('/settings')
def settings():
    """Settings generales del estudio."""
    from datetime import datetime
    s = get_settings()
    leads = _open_leads()
    jobs = _canonical_jobs()
    today = datetime.now().date()
    inicio_mes = today.replace(day=1)

    host = request.host_url.rstrip('/')
    current_tenant_id = get_current_tenant_id()
    current_tenant = next((t for t in store.list('tenants') if t.get('id') == current_tenant_id), None)
    tenant_slug = (current_tenant or {}).get('slug')
    # Kevin: 'el link del formulario es el mismo en las 3 cuentas' -- sin el
    # slug, las 3 cuentas mostraban /captacion a secas, que siempre cae en
    # Astral Weddings (el default de compatibilidad de la ruta sin slug).
    # Cualquier lead que llegara por el link copiado desde Settings de
    # Norkevin Photography o Ramiro Cruz Photo se le habria asignado a
    # Astral Weddings por error.
    captacion_url = host + '/captacion/' + tenant_slug if tenant_slug else host + '/captacion'

    stats = {
        'leads_mes': sum(1 for l in leads if l.get('created', '') >= inicio_mes.isoformat()),
        'bodas_activas': sum(1 for j in jobs if j.get('status') not in ('Listo', 'Archivado')),
        'total_instances': len([i for i in _workflow_instances_del_tenant() if i.status.value == 'active']),
    }

    from src import gmail_delivery, recurrente
    redirect_uri = _google_redirect_uri()

    return render_template('settings.html',
                          company=s.get('company', {}),
                          templates=workflow_engine.list_templates(),
                          tables=[
                              {'name': 'Leads', 'count': len(leads)},
                              {'name': 'Clients', 'count': len(_canonical_clients())},
                              {'name': 'Jobs', 'count': len(jobs)},
                              {'name': 'Payments', 'count': len(list_payments())},
                              {'name': 'Equipo', 'count': len(store.list('team'))},
                          ],
                          captacion_url=captacion_url,
                          stats=stats,
                          email_templates_count=len(store.list('email_templates')),
                          packages_count=len(store.list('packages')),
                          email_delivery_mode=os.environ.get('EMAIL_DELIVERY_MODE', 'test'),
                          gmail_configured=gmail_delivery.is_configured(),
                          gmail_connected=gmail_delivery.is_connected(),
                          gmail_email=gmail_delivery.connected_email(),
                          gmail_redirect_uri=redirect_uri,
                          recurrente_configured=recurrente.is_configured(),
                          recurrente_test_mode=recurrente.is_test_mode(),
                          recurrente_status=recurrente.connection_status(),
                          google_status=request.args.get('google_status'),
                          google_msg=request.args.get('google_msg'),
                          google_email_param=request.args.get('google_email'))


@app.route('/api/admin/reset-test-data', methods=['POST'])
def api_admin_reset_test_data():
    """Kevin: 'borra todos los datos para seguir haciendo pruebas, prefiero
    que este vacio'. Vacia leads/clientes/jobs/cotizaciones/pagos/contratos/
    cuestionarios/archivos/mail/calendario para volver a un CRM vacio.
    NO toca configuracion (plantillas de correo, paquetes, equipo, fuentes,
    workflow templates guardados, conexion de Gmail/Recurrente, tenants) --
    eso costo tiempo configurarlo y no es "dato de prueba".

    HARDENING (estabilizacion, agosto 2026, bloque de cierre de brechas):
    esta ruta es capaz de vaciar 11 tablas completas de UNA cuenta con una
    sola llamada autenticada. La version anterior solo pedia
    confirm=='BORRAR' -- facil de reproducir por error (un script de
    pruebas, un curl copiado sin pensar) y sin nada que la desactive en
    produccion. Ahora:
      1. Deshabilitada por defecto salvo ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS=1
         explicito en el entorno (pensado para NO estar presente en Render/
         produccion real).
      2. Confirmacion especifica por cuenta: confirm debe ser
         'BORRAR-<tenant_id>' de la cuenta activa, no un string generico
         que funcione igual para cualquier cuenta.
      3. Backup VERIFICADO (store.backup_now(), no solo el backup pasivo
         de _save) de cada tabla ANTES de tocar nada. Si cualquiera falla,
         se aborta sin haber vaciado ninguna tabla.
      4. Audit event con actor, tenant, timestamp, IP, tablas afectadas y
         resultado -- tambien cuando se bloquea o se aborta, no solo
         cuando se ejecuta."""
    tenant_id = get_current_tenant_id()
    actor = session.get('user_email')
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)

    if os.environ.get('ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS', '0') not in ('1', 'true', 'True'):
        log_security_event('RESET_TEST_DATA_BLOQUEADO_POR_FLAG', actor=actor,
                           tenant_id=tenant_id, ip=ip)
        return jsonify({
            'ok': False,
            'error': ('Esta operacion esta deshabilitada. Requiere '
                      'ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS=1 en el entorno del '
                      'servidor (deliberadamente ausente en produccion).'),
        }), 403

    data = request.get_json(silent=True) or {}
    expected_confirm = f'BORRAR-{tenant_id}'
    if not tenant_id or data.get('confirm') != expected_confirm:
        log_security_event('RESET_TEST_DATA_CONFIRMACION_INVALIDA', actor=actor,
                           tenant_id=tenant_id, ip=ip)
        return jsonify({
            'ok': False,
            'error': f"Confirmacion requerida: envia {{'confirm': '{expected_confirm}'}}.",
        }), 400

    tables_to_wipe = [
        'leads', 'clients', 'jobs', 'quotes', 'payments', 'contracts',
        'questionnaires', 'files', 'mail_log', 'mail_outbox', 'calendar',
        # pending_emails (STAGE 2, agosto 2026): antes de la cola de
        # aprobacion esta tabla practicamente no se usaba en operacion real,
        # asi que faltar aca no se notaba. Ahora que los 13 puntos de envio
        # de produccion encolan ahi, un reset de datos de prueba que no la
        # vacie dejaria correos de prueba pendientes de aprobar mezclados
        # con datos reales -- o, peor, un pendiente que sobrevive al reset
        # referenciando un lead/job ya borrado (check_same_tenant no
        # bloquea por un registro inexistente, asi que ese pendiente
        # todavia se podria aprobar y enviar de verdad despues).
        'pending_emails',
    ]

    # Snapshot de jobs/leads de ESTA cuenta antes de vaciar nada. Hace
    # falta para poder identificar mas abajo que workflow_instances son
    # de esta cuenta (instancias legacy sin tenant_id, ver
    # _instancia_es_de_la_cuenta) -- para cuando se llega a ese paso, la
    # tabla 'jobs'/'leads' de esta cuenta ya esta vacia, asi que no se
    # puede recalcular despues.
    _reset_job_ids_cache = {j.get('id') for j in list_jobs()}
    _reset_lead_ids_cache = {l.get('id') for l in list_leads()}

    # Paso 1: backup VERIFICADO de cada tabla antes de tocar nada. Si
    # cualquiera falla, abortar -- 0 tablas vaciadas.
    backup_paths = {}
    try:
        for table in tables_to_wipe:
            backup_paths[table] = store.backup_now(table)
    except Exception as exc:
        log_security_event('RESET_TEST_DATA_ABORTADO_BACKUP_FALLO', actor=actor,
                           tenant_id=tenant_id, ip=ip, tabla_fallida=table, error=str(exc))
        return jsonify({
            'ok': False,
            'error': f'Backup de "{table}" no se pudo verificar -- se abortó sin borrar nada. {exc}',
            'tables_wiped': 0,
        }), 500

    # Paso 2: recien con TODOS los backups verificados, vaciar.
    wiped = {}
    workflow_instances_wiped = 0
    try:
        for table in tables_to_wipe:
            wiped[table] = len(store.list(table))
            store.clear(table)

        # OJO: workflow_engine.instances/history son un diccionario y una
        # lista GLOBALES de todo el proceso -- guardan las instancias de
        # las 3 cuentas juntas (workflow_instances.json no tiene sufijo de
        # cuenta) y no pasan por store.clear() ni por su aislamiento por
        # tenant_id. Un 'workflow_engine.instances = {}' liso y llano aca
        # borraba el progreso de TODAS las cuentas -- incluida Ramiro, que
        # ni siquiera se esta reseteando -- de un plumazo, cada vez que se
        # vaciaba UNA sola cuenta. Se reusa _instancia_es_de_la_cuenta (el
        # mismo helper que ya cierra esta clase de fuga en las vistas y en
        # workflow-cleanup) con el snapshot tomado ANTES del wipe de
        # arriba, para que las instancias legacy sin tenant_id tambien se
        # identifiquen bien aunque sus jobs/leads ya no existan mas.
        ids_a_borrar = {
            iid for iid, inst in workflow_engine.instances.items()
            if _instancia_es_de_la_cuenta(
                inst, job_ids_cache=_reset_job_ids_cache,
                lead_ids_cache=_reset_lead_ids_cache, tenant_id=tenant_id)
        }
        for iid in ids_a_borrar:
            del workflow_engine.instances[iid]
        workflow_engine.history = [
            e for e in workflow_engine.history
            if e.get('instance_id') not in ids_a_borrar
        ]
        workflow_instances_wiped = len(ids_a_borrar)
        workflow_engine._save_to_storage()
    except Exception as exc:
        # Interrupcion a mitad de camino: no es atomico entre tablas (cada
        # store.clear() es su propio archivo), asi que se deja constancia
        # explicita de exactamente donde se detuvo en vez de fingir que
        # termino limpio.
        log_security_event('RESET_TEST_DATA_INTERRUMPIDO', actor=actor,
                           tenant_id=tenant_id, ip=ip, tablas_vaciadas=list(wiped.keys()),
                           error=str(exc))
        return jsonify({
            'ok': False,
            'error': f'Interrumpido despues de vaciar {list(wiped.keys())}: {exc}',
            'tables_wiped_before_error': wiped,
            'backup_paths': backup_paths,
        }), 500

    log_security_event('RESET_TEST_DATA_EJECUTADO', actor=actor, tenant_id=tenant_id,
                       ip=ip, tablas=wiped, backups=backup_paths,
                       workflow_instances_wiped=workflow_instances_wiped)
    logger.info(f"Datos de prueba reiniciados por {actor} (tenant={tenant_id}): {wiped}, "
                f"workflow_instances_wiped={workflow_instances_wiped}")
    return jsonify({'ok': True, 'wiped': wiped, 'workflow_instances_wiped': workflow_instances_wiped})


@app.route('/api/admin/import-studio-ninja', methods=['POST'])
def api_admin_import_studio_ninja():
    """Kevin: 'llenemos el CRM con toda esta info... tal cual esta' -- importa
    jobs reales de su export de Studio Ninja (clientes, cotizaciones, facturas
    con su historial de pagos y contratos). El JSON con los datos (transcrito a
    mano leyendo cada factura/contrato, no con un parser automatico, para no
    arriesgar los montos reales) lo sube Kevin en el momento desde Settings --
    NUNCA se guarda en el repo, porque tiene nombres/emails/telefonos/montos
    reales de clientes y este repo es publico en GitHub.
    Escribe directo via store.upsert -- NO pasa por _convert_lead_to_job ni
    dispara el workflow engine, asi que no se mandan correos automaticos a
    estos clientes reales por datos historicos.
    Idempotente: cada job usa un id deterministico (boda-sn-<slug>), asi que
    correrlo de nuevo saltea los jobs que ya existen en vez de duplicarlos.

    Cada entry['workflow_status'] (opcional) es como Kevin le dice al CRM
    en que momento del workflow esta esa boda de verdad, para que el
    historial se vea correcto en vez de todo marcado igual:
      {'questionnaire_completed': true/false,
       'gallery_delivered': true/false,
       'review_left': true/false}
    entry['contract']['signed'] ya cubre el paso de firma de contrato.
    Lo que no se marca como completado queda SKIPPED (nunca 'pending'),
    asi que nada de esto puede disparar un correo automatico -- la
    diferencia entre DONE/SKIPPED es solo que se vea bien en el job."""
    data = request.get_json(silent=True) or {}
    if data.get('confirm') != 'IMPORTAR':
        return jsonify({'ok': False, 'error': 'Confirmacion requerida'}), 400

    payload = data.get('payload')
    if not isinstance(payload, dict) or not isinstance(payload.get('jobs'), list):
        return jsonify({'ok': False, 'error': 'Payload invalido: se espera {"jobs": [...]}'}), 400

    tenant_id = get_current_tenant_id()
    created = []
    skipped = []

    for entry in payload.get('jobs', []):
        slug = entry['slug']
        job_id = f'boda-sn-{slug}'
        if get_job(job_id):
            skipped.append(entry['job_name'])
            continue

        # Un job sin fecha detectada en el nombre de la carpeta ("[no
        # date] - ...") llega con entry['created'] = None -- guardar eso
        # tal cual en clients/leads/jobs/quotes hace que cualquier
        # .sort(key=lambda x: x.get('created')) reviente comparando str
        # con None (confirmado en produccion, tumbo /clients). Se
        # normaliza ACA, una sola vez, en vez de en cada upsert.
        if not entry.get('created'):
            entry['created'] = date.today().isoformat()

        lead_id = f'lead-sn-{slug}'
        # Kevin: "hay muchos Jobs de Studio Ninja que tienen 2 o mas
        # clientes asociados... no reemplaces un cliente por otro ni
        # dupliques Jobs". entry['clients'] (lista) es el formato nuevo;
        # entry['client'] (uno solo) se sigue aceptando para no romper
        # payloads viejos -- se normaliza a lista aca mismo.
        clients_in = entry.get('clients') or ([entry['client']] if entry.get('client') else [])
        client_ids = []
        for idx, c in enumerate(clients_in):
            cid = f'client-sn-{slug}' if idx == 0 else f'client-sn-{slug}-{idx + 1}'
            store.upsert('clients', {
                'id': cid,
                'first_name': c.get('first_name') or '',
                'last_name': c.get('last_name') or '',
                'email': c.get('email') or '',
                'phone': c.get('phone') or '',
                # NO se copia entry['location'] a address: el venue del
                # evento no es la direccion de facturacion del cliente.
                # Es la misma contaminacion que se corrigio en
                # _ensure_client_for_lead y api_lead_create; esta tercera
                # puerta habia quedado abierta.
                'estado': 'Activo',
                'tenant_id': tenant_id,
                'created': entry['created'],
            })
            client_ids.append(cid)
        client_id = client_ids[0] if client_ids else None
        primary_client = clients_in[0] if clients_in else {}
        full_name = f"{primary_client.get('first_name') or ''} {primary_client.get('last_name') or ''}".strip()

        store.upsert('leads', {
            'id': lead_id,
            'nombre': full_name,
            'email': primary_client.get('email') or '',
            'telefono': primary_client.get('phone') or '',
            'status': 'Convertido',
            'fuente': entry.get('lead_source') or '',
            'tipo_evento': 'Boda',
            'fecha_tentativa': entry['boda_date'],
            'locacion': entry.get('location') or '',
            'client_id': client_id,
            'created': entry['created'],
            'tenant_id': tenant_id,
        })

        price_total = round(sum(q['total'] for q in entry['quotes']), 2)
        # Kevin: "practicamente todos los Jobs aparecen como Completos y al
        # mismo tiempo como Activos, lo cual no tiene sentido" -- la causa
        # real era que ESTE endpoint escribia siempre 'Confirmado' sin
        # importar si la boda ya paso hace anos, asi que el filtro de
        # Jobs (que trata todo lo que no sea 'Listo'/'Archivado' como
        # Activo) los mostraba activos para siempre. No es solo la fecha:
        # si el workflow_status del payload dice explicitamente que el job
        # NO esta completo (job_complete=False), eso manda aunque la fecha
        # ya haya pasado (ej. boda cancelada/en disputa que Kevin marco).
        ws_for_status = entry.get('workflow_status') or {}
        job_complete_flag = ws_for_status.get('job_complete')
        boda_date_str = entry.get('boda_date')
        is_past_event = False
        if boda_date_str:
            try:
                is_past_event = date.fromisoformat(boda_date_str) < date.today()
            except ValueError:
                is_past_event = False
        if job_complete_flag is False:
            computed_status = 'Confirmado'
        elif job_complete_flag is True or is_past_event:
            computed_status = 'Listo'
        else:
            computed_status = 'Confirmado'

        job_dict = {
            'id': job_id,
            'nombre': entry['job_name'],
            'boda_date': entry['boda_date'],
            'location': entry.get('location') or '',
            'client_id': client_id,
            'lead_id': lead_id,
            'status': computed_status,
            'empresa': _brand_display_name_for_tenant(tenant_id),
            'price_total': price_total,
            'tenant_id': tenant_id,
            'created': entry['created'],
        }
        # El 2do (y 3er) cliente del job usan los campos que job_detail.html
        # YA sabe mostrar (avatar, editar, quitar) -- 'secondary_client_id'
        # para la otra mitad de la pareja, 'planner_client_id' si hubiera
        # un tercero (wedding planner). No se inventa una relacion nueva.
        if len(client_ids) > 1:
            job_dict['secondary_client_id'] = client_ids[1]
        if len(client_ids) > 2:
            job_dict['planner_client_id'] = client_ids[2]
        store.upsert('jobs', job_dict)

        # Modelo canonico N-clientes: ya no se pierde el 4to en adelante.
        # Los campos legacy de arriba se siguen escribiendo para que nada
        # que todavia los lea se rompa, pero la fuente de verdad es esta.
        if client_ids:
            _roles_import = [ROL_PRINCIPAL, ROL_PAREJA, ROL_PLANNER]
            _set_job_clients(job_dict, [
                (cid, _roles_import[i] if i < len(_roles_import) else ROL_CONTACTO)
                for i, cid in enumerate(client_ids)
            ], tenant_id=tenant_id)

        # Salvaguarda: se marca SKIPPED (nunca 'pending') apenas se crea el
        # job, ANTES de procesar cotizaciones/pagos/contrato -- eso puede
        # reventar con datos mal formados del ZIP (Kevin: "ten mucho
        # cuidado en no enviar correos donde no haya que enviarlos"). Si
        # algo falla a mitad de este job, ya queda protegido en vez de
        # quedar a medio marcar y vulnerable a _auto_fire_due_job_steps().
        # Mas abajo se recalcula a DONE lo que de verdad paso.
        _safety_instance = _get_or_create_job_workflow_instance(get_job(job_id))
        for _safety_step in PRODUCTION_WORKFLOW().steps:
            _safety_instance.step_states[_safety_step.id] = StepStatus.SKIPPED
        workflow_engine._save_to_storage()

        accepted_quote_id = None
        for qi, q in enumerate(entry['quotes']):
            quote_id = f'quote-sn-{slug}-{qi + 1}'
            store.upsert('quotes', {
                'id': quote_id,
                'job_id': job_id,
                'client_id': client_id,
                'lead_id': lead_id,
                'status': 'Aceptada',
                'paquete_nombre': q['package_name'],
                'precio_total': q['total'],
                'incluye': q.get('incluye') or [],
                'created': entry['created'],
                'tenant_id': tenant_id,
            })
            if accepted_quote_id is None:
                accepted_quote_id = quote_id

            invoice_id = 'INV-SN-' + slug.upper().replace('-', '')[:8] + f'-{qi + 1}'
            num_cuotas = len(q['cuotas'])
            for ci, cuota in enumerate(q['cuotas']):
                original_amount = round(cuota['amount'], 2)
                row = {
                    'id': f'pay-sn-{slug}-{qi + 1}-{ci + 1}',
                    'invoice_id': invoice_id,
                    'client_id': client_id,
                    'job_id': job_id,
                    'quote_id': quote_id,
                    'concepto': q['package_name'],
                    'original_amount': original_amount,
                    'due_date': cuota['due_date'],
                    'cuota': f'{ci + 1}/{num_cuotas}',
                    'tenant_id': tenant_id,
                }
                if cuota['status'] == 'Pagado':
                    paid_date = cuota.get('paid_date') or cuota['due_date']
                    row.update({
                        'amount': original_amount,
                        'paid_amount': original_amount,
                        'status': 'Pagado',
                        'paid_date': paid_date,
                        'fecha_pago': paid_date,
                    })
                else:
                    row.update({'amount': original_amount, 'paid_amount': 0, 'status': 'Pendiente'})
                store.upsert('payments', row)

        job = get_job(job_id)
        job['accepted_quote_id'] = accepted_quote_id
        store.upsert('jobs', job)

        contract = entry.get('contract')
        if contract:
            store.upsert('contracts', {
                'id': f'contract-sn-{slug}',
                'job_id': job_id,
                'client_id': client_id,
                'lead_id': lead_id,
                'tipo': 'boda',
                'status': 'Firmado' if contract.get('signed') else 'Enviado',
                'signed': bool(contract.get('signed')),
                'photographer_signed': bool(contract.get('photographer_signed')),
                'created': contract.get('signed_date') or entry['created'],
                'tenant_id': tenant_id,
            })

        # Estos son jobs HISTORICOS (bodas ya realizadas/canceladas de Studio
        # Ninja). Sin marcarlos de alguna forma que no sea 'pending',
        # _auto_fire_due_job_steps() (corre cada 6h en produccion) ve sus
        # steps de contrato/cuestionario como "vencidos hace meses" y les
        # manda correos reales a clientes reales apenas arranca.
        #
        # Kevin: "como hago para que sepas en que momento del workflow
        # estan las bodas" -- antes esto marcaba TODO como SKIPPED sin
        # importar que paso de verdad, asi que aunque el cliente ya hubiera
        # respondido el cuestionario o recibido su galeria, el job se veia
        # como si nada de eso hubiera pasado. Ahora entry['workflow_status']
        # (opcional) deja decir que paso realmente, y cada paso se marca
        # DONE (si de verdad se hizo) o SKIPPED (si no) -- las dos dejan el
        # step fuera de get_due_steps() por igual, la diferencia es solo que
        # el historial se ve correcto en vez de todo gris.
        ws = entry.get('workflow_status') or {}
        step_done = {
            'job_accepted': True,
            'reserva_confirmada': True,
            'firma_contrato': bool(contract and contract.get('signed')),
            'cuestionario_cliente': bool(ws.get('questionnaire_completed')),
            'envio_galeria': bool(ws.get('gallery_delivered')),
            'pedir_review': bool(ws.get('review_left')),
            'job_complete': bool(ws.get('job_complete', True)),
        }
        instance = _get_or_create_job_workflow_instance(job)
        for step in PRODUCTION_WORKFLOW().steps:
            instance.step_states[step.id] = (
                StepStatus.DONE if step_done.get(step.id) else StepStatus.SKIPPED
            )
        workflow_engine._save_to_storage()

        created.append(entry['job_name'])

    logger.info(f"Import Studio Ninja por {session.get('user_email')}: {len(created)} creados, {len(skipped)} salteados")
    return jsonify({'ok': True, 'created': created, 'skipped': skipped})


# Los 3 tenants reales -- Astral Weddings reutiliza el id 'tenant-norkevin'
# a proposito (es el que YA tienen todos los registros existentes, asi la
# migracion no tiene que reasignar nada de esa cuenta, solo rellenar lo que
# nunca tuvo tenant_id o quedo en el stub viejo 'tenant-astral').
_MULTI_TENANT_REAL_TENANTS = [
    {'id': 'tenant-norkevin', 'slug': 'astral-weddings', 'name': 'ASTRAL WEDDINGS',
     'logo_letter': 'A', 'color': '#2F7D73', 'active': True, 'currency': 'GTQ',
     'language': 'es', 'login_email': 'astralweddingsgt@gmail.com'},
    {'id': 'tenant-norkevin-photography', 'slug': 'norkevin-photography', 'name': 'Norkevin Photography',
     'logo_letter': 'N', 'color': '#0284C7', 'active': True, 'currency': 'GTQ',
     'language': 'es', 'login_email': 'norkevinfoto@gmail.com'},
    {'id': 'tenant-ramiro-cruz', 'slug': 'ramiro-cruz-photo', 'name': 'Ramiro Cruz Photo',
     'logo_letter': 'R', 'color': '#7C3AED', 'active': True, 'currency': 'GTQ',
     'language': 'es', 'login_email': 'ramirocruz10x@gmail.com'},
]
_MULTI_TENANT_KNOWN_OLD_IDS = {None, '', 'tenant-norkevin', 'tenant-astral'}
# Union con los 3 ids reales: si la migracion ya corrio una vez, los
# registros de Norkevin Photography/Ramiro Cruz ya traen su propio
# tenant_id real (no uno de los stubs viejos) -- sin esto, volver a llamar
# el endpoint (p.ej. para ver el dry-run actual) abortaria pensando que sus
# propios datos ya migrados son "desconocidos".
_MULTI_TENANT_KNOWN_IDS = _MULTI_TENANT_KNOWN_OLD_IDS | {t['id'] for t in _MULTI_TENANT_REAL_TENANTS}


@app.route('/api/admin/migrate-to-multi-tenant', methods=['POST'])
def api_admin_migrate_to_multi_tenant():
    """Convierte el CRM de una sola cuenta implicita a 3 cuentas
    completamente independientes (Astral Weddings / Norkevin Photography /
    Ramiro Cruz Photo). Kevin: 'antes de hacer la migracion crea un
    respaldo completo... si algun registro no tiene una cuenta claramente
    identificada, no lo asignes al azar, dejalo marcado para revision'.

    Todo lo que existe hoy le pertenece 100% a Astral Weddings (es el unico
    negocio que uso este CRM hasta ahora) -- por eso el id 'tenant-norkevin'
    se reutiliza sin cambios para esa cuenta, y el backfill solo toca
    registros sin tenant_id o con el id del viejo stub 'tenant-astral'
    (nunca tuvo datos reales). Si aparece CUALQUIER otro tenant_id
    desconocido, esto aborta sin escribir nada.

    dry_run=true (default) solo devuelve el reporte de conteos, no escribe
    nada -- hay que llamarlo de nuevo con dry_run=false para ejecutar de
    verdad."""
    from src.storage import TENANT_SCOPED_TABLES
    import shutil
    from datetime import datetime as _dt

    data = request.get_json(silent=True) or {}
    if data.get('confirm') != 'MIGRAR':
        return jsonify({'ok': False, 'error': 'Confirmacion requerida'}), 400
    dry_run = data.get('dry_run', True) not in (False, 'false', 0, '0')

    report = {}
    unexpected = {}
    for table in sorted(TENANT_SCOPED_TABLES):
        counts = {}
        for r in store.list_privileged(table, scope='all_tenants',
                                          reason='migracion a multi-cuenta (admin)'):
            tid = r.get('tenant_id') or '(sin tenant_id)'
            counts[tid] = counts.get(tid, 0) + 1
        report[table] = counts
        for tid in counts:
            real_tid = None if tid == '(sin tenant_id)' else tid
            if real_tid not in _MULTI_TENANT_KNOWN_IDS:
                unexpected.setdefault(table, []).append(tid)

    if unexpected:
        return jsonify({
            'ok': False,
            'error': 'Hay tenant_id que no reconozco -- no se toca nada hasta revisarlos a mano.',
            'unexpected': unexpected,
            'report': report,
        }), 400

    if dry_run:
        return jsonify({
            'ok': True, 'dry_run': True, 'report': report,
            'would_create_tenants': [t['id'] for t in _MULTI_TENANT_REAL_TENANTS],
        })

    # 1. Respaldo completo (ademas del backup automatico que ya hace
    #    JsonStore en cada _save individual).
    backup_dir = os.path.join(store.data_dir, 'backups', f"pre-multi-tenant-{_dt.now().strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(backup_dir, exist_ok=True)
    for fname in os.listdir(store.data_dir):
        src = os.path.join(store.data_dir, fname)
        if os.path.isfile(src) and fname.endswith('.json'):
            shutil.copy2(src, os.path.join(backup_dir, fname))

    # 2. tenants.json -> los 3 reales (reemplaza los 2 stub viejos).
    store._save('tenants', _MULTI_TENANT_REAL_TENANTS)

    # 3. Backfill: sin tenant_id o con el stub viejo -> Astral Weddings.
    migrated = {}
    for table in sorted(TENANT_SCOPED_TABLES):
        records = store.list_privileged(table, scope='all_tenants',
                                          reason='migracion a multi-cuenta (admin)')
        changed = 0
        for r in records:
            if not r.get('tenant_id') or r.get('tenant_id') == 'tenant-astral':
                r['tenant_id'] = 'tenant-norkevin'
                changed += 1
        if changed:
            store._save(table, records)
        migrated[table] = changed

    # 4. Clonar plantillas/paquetes de Astral Weddings para que las otras 2
    #    cuentas nuevas arranquen con algo funcional (no vacio) en vez de
    #    heredar por accidente los de otra marca.
    cloned = {}
    for table in ('email_templates', 'packages'):
        base_records = store.list_privileged(
            table, tenant_id='tenant-norkevin',
            reason='migracion a multi-cuenta (admin)')
        count = 0
        for new_tenant in _MULTI_TENANT_REAL_TENANTS[1:]:
            for rec in base_records:
                clone = dict(rec)
                clone['id'] = f"{rec['id']}-{new_tenant['id']}"
                clone['tenant_id'] = new_tenant['id']
                store.upsert(table, clone)
                count += 1
        cloned[table] = count

    # 5. Los archivos tipo-dict que SI se separan por cuenta (settings,
    #    token de Gmail, estado de OAuth) tambien son 100% de Astral
    #    Weddings -- se copian a su version con sufijo de tenant en vez de
    #    perderse. workflow_instances/workflow_history quedan compartidos
    #    a proposito (ver nota en _persist_workflow_template).
    for name in ('settings', 'google_oauth_state', 'google_token'):
        old_path = os.path.join(store.data_dir, f'{name}.json')
        new_path = os.path.join(store.data_dir, f'{name}_tenant-norkevin.json')
        if os.path.exists(old_path) and not os.path.exists(new_path):
            shutil.copy2(old_path, new_path)
        # El archivo global de credenciales se RETIRA despues de copiarlo.
        # Antes solo se copiaba, y ese google_token.json quedaba vivo: fue
        # exactamente la credencial que uso el hilo sin cuenta para mandar
        # correos de Astral a clientes de Norkevin. Se renombra en vez de
        # borrarse para no destruir nada de forma irreversible.
        if name == 'google_token' and os.path.exists(old_path):
            os.replace(old_path, old_path + '.retirado')

    # 6. Si Recurrente estaba configurado a la vieja usanza (una sola llave
    #    global por variable de entorno), se migra a las credenciales
    #    cifradas de Astral Weddings para no perder la conexion existente.
    old_recurrente_key = os.environ.get('RECURRENTE_SECRET_KEY', '')
    old_recurrente_key_test = os.environ.get('RECURRENTE_SECRET_KEY_TEST', '')
    if old_recurrente_key or old_recurrente_key_test:
        from src import recurrente as _recurrente_module
        old_mode = 'test' if os.environ.get('RECURRENTE_MODE', 'live').strip().lower() == 'test' else 'live'
        _recurrente_module.save_credentials(
            secret_key=old_recurrente_key, secret_key_test=old_recurrente_key_test,
            mode=old_mode, tenant_id='tenant-norkevin',
        )

    logger.info(
        f"Migracion multi-tenant ejecutada por {session.get('user_email')}: "
        f"migrated={migrated} cloned={cloned} backup={backup_dir}"
    )
    return jsonify({
        'ok': True, 'dry_run': False, 'backup_dir': backup_dir,
        'tenants': [t['id'] for t in _MULTI_TENANT_REAL_TENANTS],
        'migrated': migrated, 'cloned': cloned,
    })


@app.route('/settings/email-templates')
def settings_email_templates():
    return render_template('settings_email_templates.html', templates=store.list('email_templates'))


@app.route('/settings/lead-sources')
def settings_lead_sources():
    return render_template('settings_lead_sources.html', lead_sources=_configured_lead_sources(include_inactive=True))


@app.route('/api/settings/lead-sources', methods=['POST'])
def api_settings_lead_source_save():
    import uuid
    data = request.get_json() or {}
    name = (data.get('name') or data.get('Name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'Nombre requerido'}), 400
    raw_active = data.get('active', data.get('Activo', True))
    active = str(raw_active).lower() not in ('false', '0', 'no', 'off') if isinstance(raw_active, str) else bool(raw_active)
    item_id = data.get('id') or ('fuente-' + uuid.uuid4().hex[:8])
    item = _upsert_config_item('fuentes', item_id, {
        'Name': name,
        'Marca': 'Global',
        'Activo': active,
        'Color': data.get('color') or data.get('Color'),
    })
    return jsonify({'ok': True, 'source': item})


@app.route('/api/settings/lead-sources/<source_id>', methods=['DELETE'])
def api_settings_lead_source_delete(source_id):
    item = _upsert_config_item('fuentes', source_id, {'Activo': False})
    return jsonify({'ok': True, 'source': item})


@app.route('/api/settings/email-templates', methods=['POST'])
def api_settings_email_template_save():
    import uuid
    data = request.get_json() or {}
    template_id = data.get('id') or ('tpl-' + uuid.uuid4().hex[:8])
    template = {
        'id': template_id,
        'name': data.get('name', '').strip(),
        'asunto': data.get('asunto', '').strip(),
        'cuerpo': data.get('cuerpo', ''),
        'adjuntos': data.get('adjuntos', []),
        'activo': bool(data.get('activo', True)),
        'created': store.get('email_templates', template_id).get('created') if store.get('email_templates', template_id) else datetime.now().isoformat()[:10],
    }
    if not template['name']:
        return jsonify({'ok': False, 'error': 'Nombre requerido'}), 400
    store.upsert('email_templates', template)
    return jsonify({'ok': True, 'template': template})


@app.route('/api/settings/email-templates/<template_id>', methods=['DELETE'])
def api_settings_email_template_delete(template_id):
    if not store.get('email_templates', template_id):
        return jsonify({'ok': False, 'error': 'No encontrado'}), 404
    store.delete('email_templates', template_id)
    return jsonify({'ok': True})


@app.route('/settings/packages')
def settings_packages():
    return render_template('settings_packages.html', packages=store.list('packages'))


@app.route('/settings/quotes')
def settings_quotes():
    """BLOQUE F: donde se administran tema/portafolio/condiciones/plantillas
    de la Public Quote Experience -- las librerias que BLOQUE D (el editor
    de cotizaciones) deja ELEGIR pero no crear. Pagina interna con sesion:
    todo tenant-scoped a la cuenta activa, mismo patron que /settings/packages."""
    tenant_id = get_current_tenant_id()
    return render_template(
        'settings_quotes.html',
        quote_theme=_quote_theme_for_tenant(tenant_id),
        portfolio_items=_load_portfolio(tenant_id, only_active=False),
        terms_templates=_load_terms_templates(tenant_id),
        quote_templates=_load_quote_templates(tenant_id),
    )


@app.route('/api/settings/quote-theme', methods=['POST'])
def api_settings_quote_theme_update():
    """Guarda quote_theme dentro de settings.<tenant>.json, mismo lugar que
    _quote_theme_for_tenant (BLOQUE B) ya sabe leer -- no hace falta tocar
    esa funcion para que esto tenga efecto. Solo lo cosmetico (colores,
    texto del boton, whatsapp); nombre/email/telefono siguen viniendo de
    tenant_brand_map/resolve_pdf_brand, sin duplicarlos aca."""
    s = get_settings()
    data = request.get_json() or {}

    if 'quote_theme' not in s:
        s['quote_theme'] = {}
    campos = ('bg_dark', 'cream', 'bone', 'ink', 'ink_soft', 'line', 'accent',
              'cta_text', 'whatsapp', 'logo_url', 'footer_text',
              'currency_symbol', 'currency_label', 'featured_video_url')
    for campo in campos:
        if campo in data:
            valor = (data.get(campo) or '').strip()
            if valor:
                s['quote_theme'][campo] = valor
            else:
                s['quote_theme'].pop(campo, None)

    store.save_tenant_dict('settings', s)
    return jsonify({'ok': True, 'quote_theme': s['quote_theme']})


@app.route('/api/settings/packages', methods=['POST'])
def api_settings_package_save():
    import uuid
    data = request.get_json() or {}
    package_id = data.get('id') or ('pkg-' + uuid.uuid4().hex[:8])
    try:
        price = float(data.get('price') or 0)
        duration = int(data.get('duration_hours') or 0)
        num_photos = int(data.get('num_photos') or 0)
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'Precio, horas y fotos deben ser numeros'}), 400
    includes = data.get('includes')
    if isinstance(includes, str):
        includes = [line.strip() for line in includes.split('\n') if line.strip()]
    package = {
        'id': package_id,
        'name': data.get('name', '').strip(),
        'category': data.get('category', '').strip() or 'General',
        'description': data.get('description', '').strip(),
        'duration_hours': duration,
        'num_photos': num_photos,
        'price': price,
        'includes': includes or [],
    }
    if not package['name']:
        return jsonify({'ok': False, 'error': 'Nombre requerido'}), 400
    store.upsert('packages', package)
    return jsonify({'ok': True, 'package': package})


@app.route('/api/settings/packages/<package_id>', methods=['DELETE'])
def api_settings_package_delete(package_id):
    if not store.get('packages', package_id):
        return jsonify({'ok': False, 'error': 'No encontrado'}), 404
    store.delete('packages', package_id)
    return jsonify({'ok': True})


@app.route('/captacion')
@app.route('/captacion/<tenant_slug>')
def captacion_form(tenant_slug=None):
    """Formulario publico de captacion (2do formulario publico, mismo
    patron que /contacto: cada marca tiene su propia URL con slug)."""
    if tenant_slug:
        tenant = _tenant_by_slug(tenant_slug)
        if not tenant:
            abort(404)
    else:
        tenant = _tenant_by_slug('astral-weddings')
    tenant = tenant or {}
    company = get_settings(tenant_id=tenant.get('id')).get('company', {})
    contact_email = company.get('email') or tenant.get('login_email') or 'info@astralweddings.com'
    contact_phone = company.get('phone') or '+502 2222 3333'
    return render_template(
        'captacion.html',
        lead_sources=_configured_lead_sources(tenant_id=tenant.get('id')),
        tenant_slug=tenant.get('slug', 'astral-weddings'),
        tenant=tenant,
        contact_email=contact_email,
        contact_phone=contact_phone,
    )


@app.route('/api/captacion', methods=['POST'])
def api_captacion_submit():
    """Recibe el formulario publico y crea un lead. Sin sesion -- el
    tenant_id sale del tenant_slug validado, no de get_current_tenant_id()."""
    import uuid
    from datetime import datetime as _dt

    data = request.get_json() or request.form.to_dict() or {}

    if not data.get('nombre'):
        return jsonify({'ok': False, 'error': 'nombre requerido'}), 400

    tenant = _tenant_by_slug(data.get('tenant_slug')) or _tenant_by_slug('astral-weddings')
    if not tenant:
        return jsonify({'ok': False, 'error': 'Cuenta no reconocida'}), 400
    # El slug ya quedo validado contra tenants.json: recien ahora se fija la
    # cuenta de la peticion, para que el store deje escribir el lead.
    g.public_tenant_id = tenant['id']

    lead_id = 'lead-' + uuid.uuid4().hex[:8]
    lead = {
        'id': lead_id,
        'nombre': data.get('nombre', ''),
        'email': data.get('email', ''),
        'telefono': data.get('telefono', ''),
        'status': 'Nuevo',
        'fuente': data.get('fuente', 'Web'),
        'tipo_evento': data.get('tipo_evento', 'Boda'),
        'fecha_tentativa': data.get('fecha_tentativa'),
        'locacion': data.get('locacion', ''),
        'presupuesto': data.get('presupuesto', ''),
        'created': _dt.now().isoformat()[:10],
        'is_new': True,
        'next_task': 'Pendiente de contacto',
        'mail_status': 'ENVIADO',
        'tenant_id': tenant['id'],
    }
    upsert_lead(lead)
    client, _client_created = _ensure_client_for_lead(lead)
    lead['client_id'] = client['id']
    upsert_lead(lead)

    try:
        instance = trigger_workflow_for_lead(lead_id, lead['nombre'], tenant_id=lead.get('tenant_id'))
        workflow_id = instance.id
    except Exception:
        workflow_id = None

    _notify_new_lead(lead, 'Formulario de captacion')
    return jsonify({'ok': True, 'lead_id': lead_id, 'workflow_id': workflow_id,
                    'message': 'Gracias! Te contactaremos pronto.'})




@app.route('/clients/<client_id>')
def client_detail(client_id):
    local_client = get_client(client_id)
    if local_client:
        cliente = _client_detail_view_model(local_client)
        client_email = _norm_email(local_client.get('email'))
        all_client_leads = [
            lead for lead in list_leads()
            if lead.get('client_id') == client_id or (client_email and _norm_email(lead.get('email')) == client_email)
        ]
        leads_vinculados = [lead for lead in all_client_leads if _lead_is_open(lead)]
        # Cualquier rol, no solo `principal`: la novia entra como `pareja` y
        # antes su propia boda no salia en su ficha.
        jobs_raw = _jobs_por_cliente().get(client_id, [])
        jobs_vinculados = [_job_detail_view_model(j) for j in jobs_raw]

        # La ficha del cliente tenia menos informacion que la del job: solo
        # nombre, fecha y una barra de avance del workflow. Faltaba lo que
        # se pregunta de verdad cuando alguien llama por telefono: que rol
        # tiene esta persona en la boda, en que estado esta el evento,
        # cuanto falta por cobrar y quien mas esta en ese job.
        #
        # Todo sale de las MISMAS fuentes canonicas que usa la lista de
        # jobs y la ficha del job. No hay un segundo calculo paralelo: el
        # estado viene de _job_estado_label y la plata de
        # _job_payment_summary.
        pagos_por_job = {}
        for pago in list_payments():
            pagos_por_job.setdefault(pago.get('job_id'), []).append(pago)
        clientes_por_id = {c.get('id'): c for c in list_clients()}

        relaciones_por_job = _relaciones_por_job(jobs_raw)

        jobs_resumen = []
        for j in jobs_raw:
            relaciones = relaciones_por_job.get(j.get('id'), [])
            mi_rol = next((r.get('role') for r in relaciones
                           if r.get('client_id') == client_id), None)
            pagos_j = pagos_por_job.get(j.get('id'), [])
            etiqueta, tono, estado_key = _job_estado_label(j, pagos_j)
            # Con quien mas comparte esta boda. Se omite a los que ya no
            # existen en vez de romper la pagina.
            acompanantes = []
            for rel in relaciones:
                otro_id = rel.get('client_id')
                if otro_id == client_id:
                    continue
                otro = clientes_por_id.get(otro_id)
                if not otro:
                    continue
                acompanantes.append({
                    'id': otro_id,
                    'nombre': (f"{otro.get('first_name') or ''} "
                               f"{otro.get('last_name') or ''}").strip() or 'Sin nombre',
                    'rol': ETIQUETA_ROL.get(rel.get('role'), rel.get('role')),
                })
            jobs_resumen.append({
                'job': j,
                'rol': mi_rol,
                'rol_etiqueta': ETIQUETA_ROL.get(mi_rol, mi_rol) if mi_rol else None,
                'recibe_documentos': mi_rol in ROLES_DESTINATARIOS_DOCUMENTOS,
                'estado_label': etiqueta,
                'estado_tone': tono,
                'estado_key': estado_key,
                'pagos': _job_payment_summary(j, pagos_j),
                'acompanantes': acompanantes,
            })
        # Las bodas por venir primero: es lo que se mira al abrir la ficha.
        jobs_resumen.sort(key=lambda r: _job_orden_relevancia(r['job']))

        # Encabezado de la ficha: cuantas bodas activas, cuanto falta por
        # cobrar entre todas, y cuando es el proximo pago.
        activos = [r for r in jobs_resumen if r['estado_key'] in ESTADOS_JOB_ACTIVOS]
        proximos = sorted(
            (r['pagos']['proximo_pago_fecha'] for r in jobs_resumen
             if r['pagos'].get('proximo_pago_fecha')))
        resumen_cliente = {
            'jobs_total': len(jobs_resumen),
            'jobs_activos': len(activos),
            'pendiente': sum(r['pagos'].get('pendiente') or 0 for r in jobs_resumen),
            'vencidas': sum(r['pagos'].get('vencidas') or 0 for r in jobs_resumen),
            'proximo_pago': proximos[0] if proximos else None,
        }
        job_ids = {j.get('id') for j in jobs_raw}
        lead_ids = {l.get('id') for l in all_client_leads}
        payments_vinculados = [
            p for p in list_payments()
            if p.get('client_id') == client_id or p.get('job_id') in job_ids
        ]
        quotes_vinculadas = [
            q for q in store.list('quotes')
            if q.get('client_id') == client_id or q.get('job_id') in job_ids or q.get('lead_id') in lead_ids
        ]
        contracts_vinculados = [
            c for c in store.list('contracts')
            if c.get('client_id') == client_id or c.get('job_id') in job_ids or c.get('lead_id') in lead_ids
        ]
        total_due = sum(float(p.get('amount') or 0) for p in payments_vinculados if p.get('status') != 'Pagado')

        from src.mail_tracker import get_tracker
        emails_vinculados = [
            e for e in get_tracker().log
            if e.get('lead_id') in lead_ids or e.get('job_id') in job_ids
            or (client_email and _norm_email(e.get('to') or '') == client_email)
        ]
        emails_vinculados.sort(key=lambda e: e.get('sent_at') or '', reverse=True)

        return render_template('client_detail.html',
                               cliente=cliente,
                               jobs=jobs_vinculados,
                               jobs_raw=jobs_raw,
                               jobs_resumen=jobs_resumen,
                               resumen_cliente=resumen_cliente,
                               leads=leads_vinculados,
                               payments=payments_vinculados,
                               quotes=quotes_vinculadas,
                               contracts=contracts_vinculados,
                               emails=emails_vinculados,
                               total_due=total_due,
                               parse_date=parse_date, days_until=days_until, q_money=q_money,
                               fmt_dt=fmt_dt)

    try:
        page = ns.get_page(client_id)
        cliente = ns._normalize_props(page.get('properties', {}))
        cliente['id'] = client_id
    except Exception as e:
        logger.error(f'Error cargando cliente {client_id}: {e}')
        abort(404)

    # Jobs vinculados
    jobs_ids = cliente.get('Jobs') or []
    jobs_vinculados = []
    for jid in jobs_ids:
        try:
            jp = ns.get_page(jid)
            j = ns._normalize_props(jp.get('properties', {}))
            j['id'] = jid
            jobs_vinculados.append(j)
        except:
            pass

    return render_template('client_detail.html',
                           cliente=cliente,
                           jobs=jobs_vinculados,
                           jobs_raw=[],
                           leads=[],
                           payments=[],
                           quotes=[],
                           contracts=[],
                           emails=[],
                           total_due=0,
                           parse_date=parse_date, days_until=days_until, q_money=q_money,
                           fmt_dt=fmt_dt)


# ============================================================
# LEADS
# ============================================================

@app.route('/leads-demo')
def leads_demo():
    """Endpoint demo del Kanban con datos FAKE para validar el template sin Notion."""
    import random
    from datetime import datetime, date, timedelta

    # Datos fake
    nombres = [
        'Maria Lopez', 'Carlos Mendez', 'Ana Ramirez', 'Luis Garcia',
        'Sofia Castillo', 'Diego Morales', 'Valentina Cruz', 'Andres Vega',
        'Camila Reyes', 'Sebastian Diaz', 'Isabella Torres', 'Mateo Romero',
        'Luciana Flores', 'Joaquin Vargas'
    ]
    fuentes = ['Instagram', 'Facebook', 'WhatsApp', 'Recomendacion', 'Google', 'Web']
    tipos = ['Boda', 'Evento corporativo', 'Quinceaneros', 'Civil', 'Otro']
    telefonos = ['+502 5555 1234', '+502 4444 5678', '+502 3333 9012', '+502 2222 3456']
    emails = ['maria@gmail.com', 'carlos@hotmail.com', 'ana@yahoo.com', 'luis@outlook.com', 'sofia@gmail.com']
    ubicaciones = ['Antigua Guatemala', 'Atitlan', 'Ciudad de Guatemala', 'Huehuetenango', 'Quetzaltenango']
    estados = ['Nuevo', 'Contactado', 'Cotizando', 'Propuesta Enviada', 'Negociando', 'Convertido', 'Perdido']

    leads = []
    now = datetime.now()
    for i, nombre in enumerate(nombres):
        estado = random.choice(estados)
        tiene_fecha_evento = random.random() > 0.3
        fecha_evento = (now + timedelta(days=random.randint(20, 200))).strftime('%Y-%m-%d') if tiene_fecha_evento else None
        fuente = random.choice(fuentes)
        tipo = random.choice(tipos)

        leads.append({
            'id': f'lead-{i:03d}',
            'Nombre': nombre,
            'Email': random.choice(emails),
            'Teléfono': random.choice(telefonos),
            'Estado': estado,
            'Fuente': fuente,
            'Tipo de evento': tipo,
            'Fecha tentativa del evento': fecha_evento,
            'is_new': random.random() > 0.7,
            'created_time': (now - timedelta(days=random.randint(0, 30))).isoformat()
        })

    # Conteos
    counts = {}
    for l in leads:
        st = l['Estado']
        counts[st] = counts.get(st, 0) + 1

    fuentes_set = sorted(set(l['Fuente'] for l in leads))
    tipos_set = sorted(set(l['Tipo de evento'] for l in leads))

    email_templates = [tpl for tpl in store.list('email_templates') if tpl.get('activo', True)]
    return render_template(
        'leads.html',
        leads=leads,
        search='',
        counts=counts,
        fuentes=fuentes_set,
        tipos_evento=tipos_set,
        fuente_filtro='',
        tipo_filtro='',
        email_templates=email_templates
    )










@app.route('/calendar')
def calendar_view():
    """Calendar con eventos del mes."""
    from datetime import datetime
    import calendar as _cal

    # Solo tomamos eventos manuales (type='event') de calendar.json: los de
    # tipo lead/job son entradas antiguas duplicadas, ya que abajo se generan
    # frescos desde los datos reales del lead/job (con su url correcta).
    events = [dict(e) for e in list_calendar() if e.get('type') == 'event']
    for lead in _open_leads():
        if lead.get('fecha_tentativa'):
            events.append({
                'id': 'lead-' + lead.get('id', ''),
                'date': lead.get('fecha_tentativa'),
                'type': 'lead',
                'title': lead.get('nombre', 'Lead'),
                'lead_id': lead.get('id'),
                'url': f"/leads/{lead.get('id')}",
            })
    for job in _canonical_jobs():
        if job.get('boda_date'):
            events.append({
                'id': 'job-' + job.get('id', ''),
                'date': job.get('boda_date'),
                'type': 'job',
                'title': job.get('nombre', 'Job'),
                'job_id': job.get('id'),
                'url': f"/jobs/{job.get('id')}",
            })
    today = datetime.now().date()

    mes_param = request.args.get('month', '')
    if mes_param and re.match(r'\d{4}-\d{2}', mes_param):
        year, month = map(int, mes_param.split('-'))
    elif request.args.get('year') and request.args.get('month'):
        year = request.args.get('year', type=int) or today.year
        month = request.args.get('month', type=int) or today.month
    else:
        year, month = today.year, today.month

    cal = _cal.Calendar(firstweekday=6)
    calendar_grid = []
    for week in cal.monthdayscalendar(year, month):
        cells = []
        for day_num in week:
            in_month = day_num != 0
            day = day_num if day_num else 1
            iso_date = f"{year}-{month:02d}-{day:02d}" if in_month else None
            day_events = [e for e in events if e.get('date', '') == iso_date] if iso_date else []
            cells.append({
                'day': day if in_month else '',
                'in_month': in_month,
                'today': iso_date == today.isoformat() if iso_date else False,
                'events': day_events,
                'iso_date': iso_date,
            })
        calendar_grid.append(cells)

    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    month_names = ['', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
    month_names_short = ['', 'Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']

    today_iso = today.isoformat()
    # "Proximos eventos" son trabajo confirmado (jobs) o eventos manuales --
    # los leads siguen viendose en la grilla del calendario, pero no aca,
    # porque todavia no representan un trabajo confirmado.
    upcoming_events = sorted(
        (e for e in events if e.get('date') and e['date'] >= today_iso and e.get('type') != 'lead'),
        key=lambda e: e['date'],
    )[:3]
    for e in upcoming_events:
        try:
            d = datetime.strptime(e['date'], '%Y-%m-%d').date()
            e['date_label'] = f"{d.day:02d} {month_names_short[d.month]} {d.year}"
            e['days_away'] = (d - today).days
        except ValueError:
            e['date_label'] = e['date']
            e['days_away'] = None

    return render_template('calendar.html',
                          calendar_grid=calendar_grid,
                          year=year, month=month,
                          month_name=month_names[month],
                          prev_year=prev_year, prev_month=prev_month,
                          next_year=next_year, next_month=next_month,
                          day_names=['Dom', 'Lun', 'Mar', 'Mie', 'Jue', 'Vie', 'Sab'],
                          upcoming_events=upcoming_events)






@app.route('/api/leads/<lead_id>/cotizar', methods=['POST'])
def crear_cotizacion_desde_lead(lead_id):
    """Crea una cotización para un lead usando un paquete del DB CONFIG."""
    data = request.get_json() or {}
    paquete_nombre = data.get('paquete')
    cliente_email = data.get('email') or ''
    if not paquete_nombre:
        return jsonify({'ok': False, 'error': 'Paquete requerido'}), 400

    paquete = ns.get_paquete_by_nombre(paquete_nombre)
    if not paquete:
        return jsonify({'ok': False, 'error': 'Paquete no existe'}), 400

    precio = paquete.get('Precio Q') or 0

    # Obtener nombre del lead
    try:
        page = ns.get_page(lead_id)
        lead_props = ns._normalize_props(page.get('properties', {}))
        nombre = lead_props.get('Nombre') or f'Cotización para {cliente_email}'
        lead_email = lead_props.get('Email') or cliente_email
    except Exception:
        nombre = f'Cotización {paquete_nombre}'
        lead_email = cliente_email

    cotiz_props = {
        'Cotización': {'title': [{'type': 'text', 'text': {'content': f'{nombre} · {paquete_nombre}'}}]},
        'Paquete': {'select': {'name': paquete_nombre}},
        'Monto total (Q)': {'number': precio},
        'Anticipo (Q)': {'number': round(precio * 0.5, 2)},
        'Estado': {'status': {'name': 'Aceptada'}},
        'Cantidad de cuotas': {'select': {'name': '2 (50% + 50%)'}},
        'Fecha de envío': {'date': {'start': date.today().isoformat()}},
        'Fecha aceptación': {'date': {'start': date.today().isoformat()}},
    }

    try:
        cotiz = ns.client().pages.create(parent={'data_source_id': ns.DS['COTIZ']}, properties=cotiz_props)
        return jsonify({'ok': True, 'id': cotiz['id'], 'nombre': cotiz_props['Cotización']['title'][0]['text']['content']})
    except Exception as e:
        logger.error(f'Error creando cotización: {e}')
        return jsonify({'ok': False, 'error': str(e)}), 500


# ============================================================
# COBROS A CLIENTES (COTIZACIONES)
# ============================================================



@app.route('/pagos-equipo')
def pagos_equipo_list():
    """Vista de pagos al equipo (cuentas por pagar).
    Espejo del modulo de Cobros pero para dinero que SALE."""
    from datetime import datetime
    estado_filtro = request.args.get('estado', '')
    persona_filtro = request.args.get('persona', '')

    # Filtrar solo los pagos del equipo (tipo == 'team_payment')
    pagos = [p for p in list_payments() if p.get('tipo') == 'team_payment']

    if estado_filtro:
        pagos = [p for p in pagos if p.get('status') == estado_filtro]
    if persona_filtro:
        pagos = [p for p in pagos if p.get('team_id') == persona_filtro]

    # Enriquecer con nombre del miembro
    team_by_id = {m['id']: m for m in store.list('team')}
    for p in pagos:
        member = team_by_id.get(p.get('team_id', ''))
        p['team_name'] = f"{member['first_name']} {member['last_name']}" if member else 'Desconocido'

    # Calcular KPIs
    pendientes = [p for p in pagos if p.get('status') == 'Pendiente']
    late = [p for p in pagos if p.get('status') == 'Late']
    pagados = [p for p in pagos if p.get('status') == 'Pagado']

    total_pendiente = sum(p.get('amount', 0) for p in pendientes + late)
    total_pagado = sum(p.get('amount', 0) for p in pagados)
    pagado_mes = sum(
        p.get('amount', 0)
        for p in pagados
        if p.get('paid_date', '').startswith(datetime.now().strftime('%Y-%m'))
    )

    # Sort: Late primero, luego Pendiente, luego Pagado
    pagos.sort(key=lambda p: (
        0 if p.get('status') == 'Late' else
        1 if p.get('status') == 'Pendiente' else
        2,
        p.get('due_date', '')
    ))

    team = store.list('team')

    return render_template('pagos_equipo.html',
                           payments=pagos,
                           team=team,
                           total_pendiente=total_pendiente,
                           count_pendiente=len(pendientes) + len(late),
                           total_pagado=total_pagado,
                           pagado_mes=pagado_mes,
                           count_pagado_mes=sum(1 for p in pagados if p.get('paid_date', '').startswith(datetime.now().strftime('%Y-%m'))))


def _row_original_amount(row):
    """El monto FIJO original de esta cuota (nunca cambia) -- se usa para
    calcular el Subtotal de la factura, que siempre debe sumar el precio
    del contrato sin importar como se hayan repartido los pagos despues.
    Filas viejas (de antes de este campo) no lo tienen guardado; se asume
    que su 'amount' actual todavia es el original (nunca fueron tocadas)."""
    if row.get('original_amount') is not None:
        return round(float(row['original_amount']), 2)
    return round(float(row.get('amount') or 0), 2)


def _row_paid_amount(row):
    """Cuanto dinero se recibio DIRECTAMENTE en esta cuota (no cuenta el
    credito que le hayan pasado otras cuotas por sobrepago -- eso reduce lo
    que debe sin ser 'un pago' en si). Sumado en todas las filas de una
    factura da el total realmente cobrado. Filas viejas sin el campo lo
    infieren de su status."""
    if 'paid_amount' in row:
        return round(float(row.get('paid_amount') or 0), 2)
    return round(float(row.get('amount') or 0), 2) if row.get('status') == 'Pagado' else 0.0


def _apply_payment_sequentially(job_id, amount_received, paid_date):
    """Aplica un pago a la cuota pendiente mas proxima por vencer.

    Kevin (version final, confirmada): si el cliente paga MAS de lo que
    esa cuota pedia, la cuota se marca Pagada por el monto REAL recibido
    (aunque sea mayor a lo que pedia originalmente), y el sobrante se
    reparte EN PARTES IGUALES entre TODAS las demas cuotas pendientes,
    reduciendo lo que cada una debe. Si paga MENOS, es un abono parcial
    normal (sin sobrante que repartir).

    'original_amount' es el monto fijo del contrato para esa cuota y NUNCA
    se toca -- se usa solo para el Subtotal de la factura (que siempre
    debe sumar el total del contrato). 'amount' SI cambia: para una cuota
    Pagada muestra lo realmente recibido; para una cuota pendiente muestra
    el saldo actual (despues de abonos directos o credito de otras
    cuotas). Devuelve la lista de filas que se tocaron."""
    from datetime import datetime as _dt

    amount_received = round(float(amount_received or 0), 2)
    if amount_received <= 0:
        return []

    pending = sorted(
        [p for p in store.list('payments') if p.get('job_id') == job_id and p.get('status') != 'Pagado'
         and p.get('tipo') != 'team_payment'],
        key=lambda p: p.get('due_date') or ''
    )
    if not pending:
        return []

    target = pending[0]
    others = pending[1:]

    # El saldo real de la cuota es su 'amount' ACTUAL -- ya refleja tanto
    # abonos directos anteriores como credito recibido de otras cuotas que
    # se sobrepagaron. Recalcularlo desde original_amount - paid_amount
    # ignoraria ese credito (bug encontrado al probar dos pagos seguidos).
    already_paid_on_target = _row_paid_amount(target)
    target_balance = round(float(target.get('amount') or 0), 2)

    touched = []

    if amount_received < target_balance - 0.01:
        # No alcanza a cubrir esta cuota todavia -- abono parcial, sin
        # sobrante que repartir en las demas.
        target['paid_amount'] = round(already_paid_on_target + amount_received, 2)
        target['amount'] = round(target_balance - amount_received, 2)
        target['paid_date'] = paid_date
        target['fecha_pago'] = paid_date
        target['last_action'] = f'Abono parcial de Q{amount_received:,.2f} el {paid_date} (saldo Q{target["amount"]:,.2f})'
        store.upsert('payments', target)
        return [target]

    # Cubre (o sobra) esta cuota -- se marca Pagada por el monto REAL
    # recibido en total (puede ser mayor a su monto original).
    total_received_on_target = round(already_paid_on_target + amount_received, 2)
    target['paid_amount'] = total_received_on_target
    target['amount'] = total_received_on_target
    target['status'] = 'Pagado'
    target['paid_date'] = paid_date
    target['fecha_pago'] = paid_date
    target['paid_at'] = _dt.now().isoformat()
    target['last_action'] = f'Paid on {paid_date} (distribucion automatica)'
    store.upsert('payments', target)
    touched.append(target)

    surplus = round(amount_received - target_balance, 2)
    if surplus > 0 and others:
        share = round(surplus / len(others), 2)
        distributed = 0.0
        for i, row in enumerate(others):
            row_amount = round(float(row.get('amount') or 0), 2)
            this_share = share
            if i == len(others) - 1:
                this_share = round(surplus - distributed, 2)
            applied = min(this_share, row_amount)
            distributed = round(distributed + applied, 2)
            if applied <= 0:
                continue
            row['amount'] = round(row_amount - applied, 2)
            row['last_action'] = f'Credito de Q{applied:,.2f} aplicado el {paid_date} (sobrepago en otra cuota)'
            if row['amount'] <= 0.01:
                row['amount'] = 0.0
                row['status'] = 'Pagado'
                row['paid_date'] = paid_date
                row['fecha_pago'] = paid_date
                row['last_action'] = f'Saldada por credito el {paid_date}'
            store.upsert('payments', row)
            touched.append(row)

    return touched


@app.route('/api/jobs/<job_id>/record-payment', methods=['POST'])
def api_job_record_payment(job_id):
    """Kevin: 'no quiero tener que modificar manualmente los pagos'. Recibe
    UN monto para el job entero (no una cuota especifica) y lo reparte
    automaticamente entre las cuotas pendientes en orden."""
    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404

    data = request.json or request.form or {}
    try:
        amount = float(data.get('amount') or 0)
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'monto invalido'}), 400
    if amount <= 0:
        return jsonify({'ok': False, 'error': 'El monto debe ser mayor a 0'}), 400

    paid_date = data.get('fecha_pago') or data.get('paid_date') or date.today().isoformat()
    touched = _apply_payment_sequentially(job_id, amount, paid_date)
    if not touched:
        return jsonify({'ok': False, 'error': 'No hay cuotas pendientes para este job'}), 400

    paid_total = sum(
        _row_paid_amount(p) for p in store.list('payments')
        if p.get('job_id') == job_id and p.get('tipo') != 'team_payment'
    )
    job['price_paid'] = paid_total
    upsert_job(job)

    return jsonify({
        'ok': True,
        'amount_applied': amount,
        'rows_touched': [
            {'id': r['id'], 'status': r['status'], 'amount': r['amount'], 'paid_amount': r.get('paid_amount', 0)}
            for r in touched
        ],
        'message': f'Q{amount:,.2f} distribuido automaticamente entre las cuotas pendientes',
    })


@app.route('/api/payments/<pay_id>/pay', methods=['POST'])
def api_payment_mark_paid(pay_id):
    """Marca un pago como PAGADO (tanto para clientes como para equipo)."""
    from datetime import datetime as _dt

    data = request.json or request.form
    fecha_pago = data.get('fecha_pago') or data.get('paid_date') or date.today().isoformat()
    all_payments = store.list('payments')
    pay = next((p for p in all_payments if p.get('id') == pay_id or p.get('invoice_id') == pay_id), None)
    if not pay:
        return jsonify({'ok': False, 'error': 'Pago no encontrado'}), 404

    if pay.get('status') == 'Pagado':
        return jsonify({'ok': False, 'error': 'Ya estaba pagado'}), 400

    if data.get('amount') or data.get('monto'):
        try:
            pay['amount'] = float(data.get('amount') or data.get('monto') or pay.get('amount') or 0)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'monto invalido'}), 400

    pay['status'] = 'Pagado'
    pay['paid_date'] = fecha_pago
    pay['fecha_pago'] = fecha_pago
    pay['paid_at'] = _dt.now().isoformat()
    pay['last_action'] = f'Paid on {fecha_pago}'
    store.upsert('payments', pay)
    job = get_job(pay.get('job_id', ''))
    if job:
        paid_total = sum(
            float(p.get('amount') or 0)
            for p in store.list('payments')
            if p.get('job_id') == job.get('id') and p.get('status') == 'Pagado'
        )
        job['price_paid'] = paid_total
        upsert_job(job)

    return jsonify({
        'ok': True,
        'pay_id': pay_id,
        'concepto': pay.get('concepto', ''),
        'amount': pay.get('amount', 0),
        'message': f'Pago marcado como PAGADO'
    })


# ============================================================
# PARTNERS (FOTÓGRAFOS / VIDEOGRAFOS)
# ============================================================

@app.route('/partners')
def partners_list():
    estado_filtro = request.args.get('estado', '')
    partners = []
    for member in store.list('team'):
        partners.append({
            'id': member.get('id'),
            'Nombre': (f"{member.get('first_name', '')} {member.get('last_name', '')}").strip(),
            'Estado': member.get('estado', 'Activo'),
            'Numero de celular': member.get('phone'),
            'Email': member.get('email'),
            'Tarifa Foto 8h (Q)': member.get('tarifa_boda') if 'foto' in (member.get('rol', '').lower()) else None,
            'Tarifa Video 8h (Q)': member.get('tarifa_boda') if 'video' in (member.get('rol', '').lower()) else None,
            'Tarifa Wedding Content 8h (Q)': member.get('tarifa_evento'),
            'Skills': [member.get('rol')] if member.get('rol') else [],
        })
    if estado_filtro:
        partners = [p for p in partners if p.get('Estado') == estado_filtro]

    return render_template('partners.html',
                           partners=partners,
                           estado_filtro=estado_filtro,
                           estado_options=['Activo','Pausa temporal','Inactivo','Nuevo'],
                           q_money=q_money, parse_date=parse_date)


# ============================================================
# API - JOBS
# ============================================================

@app.route('/api/jobs/new', methods=['POST'])
def api_job_new():
    """Crea un job directo. No se puede saltar el paso de cliente: siempre
    hay que seleccionar un cliente EXISTENTE (no texto libre) -- pero ya no
    exige pasar primero por un lead. Si el job viene de convertir un lead
    real, usa _convert_lead_to_job en su lugar.

    IDEMPOTENCIA (post-incidente Camila Rios, 10-11 jul 2026): esta ruta
    creaba un job NUEVO en cada llamada sin verificar si el lead_id que
    llega ya tenia uno. /api/leads/<id>/accept-quote SI tenia esa guardia
    (_find_job_for_lead); esta puerta no, y por eso un mismo lead termino
    con 4 jobs y 4 workflow_instances distintos (boda-69f508a1,
    boda-1d62d5e2, boda-35bd38a1, boda-e8b7e2a7 -- solo el ultimo sobrevive
    en jobs.json hoy). El flujo de Pick & Choose llega a esta ruta, no a
    accept-quote, asi que el parche de idempotencia de julio no la cubria.

    NOTA: esto es un guardia a nivel aplicacion sobre almacenamiento JSON.
    No reemplaza el constraint a nivel de base de datos que exige el punto
    3 de la fase de estabilizacion (ver migrations/idempotency_patch_v5.2.sql
    y src/lead_conversion.py) -- ese es el que realmente cierra la ventana
    de carrera entre 2 requests simultaneos. Este guardia sola reduce el
    caso comun (llamadas secuenciales, doble click) pero dos requests
    concurrentes contra JsonStore todavia pueden intercalarse entre el
    _find_job_for_lead() y el upsert_job(); la garantia dura vive en SQL,
    no aca."""
    import uuid
    data = request.json or request.form
    nombre = (data.get('nombre') or data.get('name') or '').strip()
    if not nombre:
        return jsonify({'ok': False, 'error': 'nombre requerido'}), 400

    client_id = (data.get('client_id') or '').strip()
    if not client_id:
        return jsonify({'ok': False, 'error': 'Selecciona un cliente para el job'}), 400
    client = get_client(client_id)
    if not client:
        return jsonify({'ok': False, 'error': 'Ese cliente no existe'}), 404

    lead_id = (data.get('lead_id') or '').strip()

    lead = get_lead(lead_id) if lead_id else None
    if lead_id and not lead:
        return jsonify({'ok': False, 'error': 'Ese lead no existe'}), 404

    try:
        price_total = float(data.get('price_total') or data.get('monto') or 0)
    except (TypeError, ValueError):
        price_total = 0

    if lead:
        # CONSOLIDACION (prioridad 8, cierre de brechas -- agosto 2026):
        # esta rama ya NO arma su propio dict de job a mano. El bug de
        # Camila Rios (4 jobs / 4 workflow_instances distintos, ver nota de
        # julio arriba) paso justo porque esta ruta tenia su propia copia
        # parcial de la logica de conversion (creaba el job pero se
        # olvidaba de crear workflow/payment schedule/questionnaire, y su
        # guardia de idempotencia vivia por separado de la de accept-quote).
        # Ahora /api/jobs/new y /api/leads/<id>/accept-quote llaman a la
        # MISMA funcion (_convert_lead_to_job), que ya trae su propia
        # guardia de idempotencia (_find_job_for_lead dentro de
        # _ensure_job_for_lead) -- una sola fuente de verdad para
        # tenant/lead/quote/client/job/workflow/payment schedule/
        # cuestionario. Repetir esta llamada con el mismo lead_id nunca
        # duplica nada: _ensure_job_for_lead devuelve el job existente,
        # _ensure_production_workflow_for_job devuelve la workflow_instance
        # existente, y _ensure_payments_for_quote devuelve [] sin crear
        # filas nuevas si ya existen para ese quote_id.
        #
        # No hay una cotizacion formal en el flujo "Pick & Choose" (el
        # usuario arma el job a mano con nombre/precio/paquete sueltos), asi
        # que se pasa quote=None y create_payments=False -- el calendario de
        # pagos automatico requiere un quote con plan_pago; si no hay quote,
        # no se inventan cuotas. El cliente ya viene resuelto por client_id
        # explicito (no por _ensure_client_for_lead), asi que si el job es
        # nuevo se le pisan los campos que SI vinieron en este formulario
        # (nombre/precio/paquete/ubicacion/fecha) despues de que la funcion
        # canonica lo cree con sus defaults.
        already_existed = bool(_find_job_for_lead(lead))
        result = _convert_lead_to_job(lead, quote=None, status=data.get('status') or 'Cotizando', create_payments=False)
        job = result['job']

        if already_existed:
            log_security_event(
                'DUPLICATE_JOB_CREATION_BLOCKED',
                tabla='jobs', registro=lead_id,
                cuenta_activa=job.get('tenant_id'),
            )
            return jsonify({
                'ok': True,
                'already_converted': True,
                'job_created': False,
                'job_id': job['id'],
                'job': job,
                'message': ('Este lead ya tiene un job existente '
                            f"({job['id']}). Se devolvio el "
                            'existente en vez de crear uno duplicado.'),
            }), 200

        if result['job_created']:
            # Job recien creado por la funcion canonica -- ahora si se
            # aplican los campos que llegaron sueltos en este formulario
            # (client_id ya se resolvio via el cliente EXISTENTE que exige
            # esta ruta, no via _ensure_client_for_lead).
            job['nombre'] = nombre or job.get('nombre')
            job['boda_date'] = data.get('boda_date') or data.get('fecha_evento') or job.get('boda_date')
            job['type'] = data.get('type') or job.get('type')
            job['location'] = data.get('location') or data.get('lugar_evento') or job.get('location')
            job['package'] = data.get('package') or job.get('package')
            job['client_id'] = client['id']
            if price_total:
                job['price_total'] = price_total
            upsert_job(job)

        return jsonify({
            'ok': True, 'job_id': job['id'], 'job': job,
            'job_created': result['job_created'],
            'workflow_created': result['workflow_created'],
        })

    # Sin lead_id: job manual puro (no hay conversion que consolidar, no
    # hay workflow/quote/payment-schedule automaticos que crear).
    job_id = 'boda-' + uuid.uuid4().hex[:8]
    tenant_id = client.get('tenant_id') or get_current_tenant_id()
    job = {
        'id': job_id,
        'nombre': nombre,
        'boda_date': data.get('boda_date') or data.get('fecha_evento') or None,
        'status': data.get('status') or 'Cotizando',
        'workflow_progress': 0,
        # Antes hardcodeado a 'ASTRAL WEDDINGS' sin importar el tenant real.
        # Ver src/tenant_brand_map.py -- unica fuente de verdad de marca.
        'empresa': _brand_display_name_for_tenant(tenant_id),
        'type': data.get('type') or 'Boda',
        'location': data.get('location') or data.get('lugar_evento') or '',
        'package': data.get('package') or '',
        'client_id': client['id'],
        'lead_id': '',
        'price_total': price_total,
        'price_paid': 0,
        'created': date.today().isoformat(),
        'tenant_id': tenant_id,
    }
    upsert_job(job)

    return jsonify({'ok': True, 'job_id': job_id, 'job': job, 'job_created': True})


@app.route('/api/jobs/export.csv')
def api_jobs_export_csv():
    import csv
    import io
    from flask import Response
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Boda Date', 'Job Name', 'Job Type', 'Status', 'Workflow Progress', 'Location', 'Package', 'Total', 'Paid'])
    for job in _canonical_jobs():
        writer.writerow([
            job.get('boda_date') or '',
            job.get('nombre') or '',
            job.get('type') or '',
            job.get('status') or '',
            job.get('workflow_progress') or '',
            job.get('location') or '',
            job.get('package') or '',
            job.get('price_total') or 0,
            job.get('price_paid') or 0,
        ])
    return Response(output.getvalue(), mimetype='text/csv', headers={
        'Content-Disposition': 'attachment; filename=jobs-norkevin.csv'
    })


@app.route('/api/jobs/<job_id>/status', methods=['POST'])
def api_job_status(job_id):
    data = request.json or request.form
    nuevo_status = data.get('status')
    if not nuevo_status:
        return jsonify({'ok': False, 'error': 'status requerido'}), 400
    job = get_job(job_id)
    if job:
        job['status'] = nuevo_status
        job['updated_at'] = datetime.now().isoformat()
        upsert_job(job)
        return jsonify({'ok': True, 'job_id': job_id, 'status': nuevo_status})
    res = ns.update_job(job_id, status=nuevo_status)
    return jsonify(res)


@app.route('/api/jobs/<job_id>/delete-payments', methods=['POST'])
def api_job_delete_payments(job_id):
    """Kevin: 'un cliente me cancelo su boda, solo me habia pagado una
    parte, quiero una opcion para poder eliminar los pagos, escribiendo la
    palabra BORRAR'. Borra TODAS las cuotas (pagadas y pendientes) de este
    job -- no toca el cliente, el job ni las cotizaciones/contratos, y cada
    tabla ya se respalda automaticamente antes de escribir (JsonStore)."""
    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404

    data = request.get_json(silent=True) or {}
    if data.get('confirm') != 'BORRAR':
        return jsonify({'ok': False, 'error': 'Confirmacion requerida'}), 400

    # store.delete() opera sobre la tabla completa (todas las cuentas) y
    # solo borra si el registro es de la cuenta activa -- a diferencia de
    # armar 'remaining' filtrando store.list() (que ya viene acotado a
    # esta cuenta) y volver a guardarlo entero con _save(), que borraria
    # sin querer los pagos de las OTRAS 2 cuentas del archivo.
    to_delete = [p['id'] for p in store.list('payments') if p.get('job_id') == job_id]
    for pid in to_delete:
        store.delete('payments', pid)
    deleted_count = len(to_delete)

    logger.info(f"Pagos eliminados del job {job_id} por {session.get('user_email')}: {deleted_count}")
    return jsonify({'ok': True, 'deleted': deleted_count})


@app.route('/api/jobs/<job_id>/notes', methods=['POST'])
def api_job_notes(job_id):
    data = request.json or request.form
    notas = data.get('notas', '')
    job = get_job(job_id)
    if job:
        job['notas'] = notas
        job['updated_at'] = datetime.now().isoformat()
        upsert_job(job)
        return jsonify({'ok': True, 'job_id': job_id})
    res = ns.update_job(job_id, notas=notas)
    return jsonify(res)


def _send_job_template_email(job, *, template_id=None, subject=None, body=None, attachments=None,
                              step_id=None, auto_fire=False):
    """Compone y pone en cola (STAGE 2, agosto 2026) un correo a partir de
    una plantilla para un job. Extraido de la ruta para que el modal manual
    y el disparador automatico por fecha (_auto_fire_due_job_steps)
    compartan la misma logica.

    step_id/auto_fire deciden la clave de idempotencia: el disparador
    automatico pasa auto_fire=True + el step['id'] real, para una clave
    ESTABLE que no vuelva a encolar el mismo aviso en cada pasada de 6h
    mientras el pendiente anterior siga sin revisar. El modal manual no
    pasa step_id (no siempre esta ligado a un step) y usa una clave por
    minuto, que solo protege contra doble-click."""
    from src.mail_tracker import get_tracker

    lead = get_lead(job.get('lead_id', '')) if job.get('lead_id') else None
    client = get_client(job.get('client_id', '')) if job.get('client_id') else None
    to_email = ', '.join(_job_all_recipient_emails(job, primary_client=client, lead=lead))
    if not to_email:
        return {'error': 'Este job no tiene email de cliente'}

    template = _get_email_template(template_id)
    rendered_subject = subject or (template or {}).get('asunto') or \
        f'Mensaje de {_brand_display_name_for_tenant(job.get("tenant_id"))}'
    rendered_body = body or (template or {}).get('cuerpo') or ''
    rendered_subject = _render_message_template(rendered_subject, client=client, lead=lead, job=job)
    rendered_body = _render_message_template(rendered_body, client=client, lead=lead, job=job)

    idempotency_key = (
        f"jobstep:{job.get('id')}:{step_id}" if (auto_fire and step_id)
        else f"jobtemplate:{job.get('id')}:{_idempotency_minute_bucket()}"
    )
    entry = get_tracker().queue_email(
        to_email=to_email,
        subject=rendered_subject,
        body=rendered_body,
        template_id=template_id,
        lead_id=job.get('lead_id'),
        job_id=job.get('id'),
        client_id=job.get('client_id') or None,
        attachments=attachments or [],
        tenant_id=job.get('tenant_id'),
        source='auto:job-template' if auto_fire else 'manual:job-template',
        idempotency_key=idempotency_key,
    )
    return {
        'mail_id': entry['id'],
        'to': to_email,
        'subject': rendered_subject,
        'delivery_status': entry.get('status'),
        'blocked_reason': entry.get('blocked_reason'),
        'mail_warning': _mail_delivery_warning(entry),
    }


@app.route('/api/jobs/<job_id>/send-email', methods=['POST'])
def api_job_send_email(job_id):
    """Registra un email enviado desde el job y opcionalmente completa un workflow step."""
    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404

    data = request.get_json() or {}
    result = _send_job_template_email(
        job,
        template_id=data.get('template_id'),
        subject=data.get('subject'),
        body=data.get('body'),
        attachments=data.get('attachments'),
    )
    if result.get('error'):
        return jsonify({'ok': False, 'error': result['error']}), 400

    workflow = _complete_job_workflow_step(
        job,
        data.get('step_id'),
        result_message=f"Email enviado: {result['subject']}"
    )
    return jsonify({
        'ok': True,
        'workflow': workflow,
        **result,
    })


QUESTIONNAIRE_QUESTIONS = [
    {'group': 'Datos de la novia', 'columns': 2, 'fields': [
        {'id': 'nombre_novia', 'label': 'Nombre de la novia *', 'type': 'text', 'required': True},
        {'id': 'instagram_novia', 'label': 'Usuario de Instagram', 'type': 'text'},
        {'id': 'telefono_novia', 'label': 'Numero de contacto novia', 'type': 'text'},
        {'id': 'email_novia', 'label': 'Correo Electronico novia', 'type': 'text'},
        {'id': 'familia_novia_ausente', 'label': 'Alguien del nucleo familiar de la novia no estara en la boda?', 'type': 'textarea', 'full': True},
    ]},
    {'group': 'Datos del novio', 'columns': 2, 'fields': [
        {'id': 'nombre_novio', 'label': 'Nombre del novio *', 'type': 'text', 'required': True},
        {'id': 'instagram_novio', 'label': 'Usuario de Instagram', 'type': 'text'},
        {'id': 'telefono_novio', 'label': 'Numero de contacto novio', 'type': 'text'},
        {'id': 'email_novio', 'label': 'Correo Electronico novio', 'type': 'text'},
        {'id': 'familia_novio_ausente', 'label': 'Alguien del nucleo familiar del novio no estara en la boda?', 'type': 'textarea', 'full': True},
    ]},
    {'group': 'Ubicaciones del evento', 'columns': 1, 'fields': [
        {'id': 'lugar_arreglo_novia', 'label': 'Cual es la direccion donde la novia se estara preparando? (si aplica)', 'type': 'text'},
        {'id': 'lugar_arreglo_novio', 'label': 'Cual es la direccion donde el novio se estara preparando? (si aplica)', 'type': 'text'},
        {'id': 'ubicacion_ceremonia_boda', 'label': 'Cual es la direccion exacta de la ceremonia? (si aplica)', 'type': 'text'},
        {'id': 'ubicacion_recepcion', 'label': 'Cual es la direccion exacta de la recepcion? (si aplica)', 'type': 'text'},
    ]},
    {'group': 'Momentos y logistica', 'columns': 2, 'fields': [
        {'id': 'tendra_vals', 'label': 'Tendras vals?', 'type': 'radio', 'options': ['Yes', 'No']},
        {'id': 'fotos_mesa', 'label': 'Tendras fotos de mesa en mesa?', 'type': 'radio', 'options': ['Yes', 'No']},
        {'id': 'lanzamiento_ramo', 'label': 'Habra lanzamiento del ramo?', 'type': 'radio', 'options': ['Yes', 'No']},
        {'id': 'lanzamiento_liga', 'label': 'Habra lanzamiento de liga?', 'type': 'radio', 'options': ['Yes', 'No']},
        {'id': 'hora_inicio_cobertura', 'label': 'A que hora te gustaria iniciar la cobertura? Recuerda que las horas de cobertura son continuas', 'type': 'text', 'full': True},
        {'id': 'num_invitados', 'label': 'Cuantos invitados aproximadamente habra el dia de tu boda?', 'type': 'text', 'full': True},
        {'id': 'punto_especial', 'label': 'Hay algun punto especial en la boda del que deba estar al tanto?', 'type': 'textarea', 'full': True},
    ]},
]


@app.route('/api/admin/debug-production-workflow')
def api_debug_production_workflow():
    """Kevin: 'todas las bodas tienen 9 cuestionarios!!!!!' -- eso apunta a
    la PLANTILLA (PRODUCTION_WORKFLOW), no a reenvios sueltos: si el
    workflow de Production tiene el paso 'Cuestionario cliente' repetido 9
    veces, cada job nuevo dispara 9 envios reales. Este endpoint muestra
    los steps EXACTOS que se estan usando ahora mismo (el override
    guardado por el Workflow Editor si existe, o el default) para
    confirmar antes de tocar nada."""
    tmpl = PRODUCTION_WORKFLOW()
    has_override = bool(store.get_dict('workflow_templates').get('production_workflow_v1'))
    steps = [
        {'id': s.id, 'name': s.name,
         'action_type': s.action_type.value if hasattr(s.action_type, 'value') else str(s.action_type)}
        for s in tmpl.steps
    ]
    questionnaire_steps = [s for s in steps if s['action_type'] == 'send_questionnaire']
    return jsonify({
        'ok': True,
        'usando_override_guardado': has_override,
        'total_steps': len(steps),
        'steps': steps,
        'pasos_de_cuestionario': len(questionnaire_steps),
    })


@app.route('/api/admin/cleanup-duplicate-questionnaires')
def api_cleanup_duplicate_questionnaires():
    """Kevin: 'porque hay 9 cuestionarios por boda? no tiene sentido' --
    limpieza de una sola vez para los duplicados que se acumularon antes
    del fix en api_lead_create_questionnaire / api_job_create_questionnaire
    (creaban uno nuevo en cada reenvio en vez de reutilizar el existente).
    Visita esta URL una vez logueado (o con ?token=... via curl, ver
    _ADMIN_ONE_TIME_TOKEN) para limpiar los duplicados: agrupa por job_id
    (o lead_id si no hay job), conserva el respondido si existe, si no el
    mas reciente, y borra el resto. Segura de correr mas de una vez (no
    hace nada si ya no hay duplicados).

    Via token no hay sesion/tenant_id (por eso existe el token en primer
    lugar), asi que en ese caso limpia TODOS los tenants en vez de solo el
    de la sesion -- no hay forma de acotarlo a "el tenant actual" cuando
    no hay sesion."""
    tenant_id = get_current_tenant_id()
    all_qs = [q for q in store.list('questionnaires') if tenant_id is None or q.get('tenant_id') == tenant_id]
    groups = {}
    for q in all_qs:
        key = q.get('job_id') or q.get('lead_id') or q.get('client_id') or q.get('id')
        groups.setdefault(key, []).append(q)

    kept = []
    removed = []
    for key, group in groups.items():
        if len(group) <= 1:
            kept.extend(group)
            continue
        answered = [q for q in group if q.get('status') == 'Respondido']
        winner = max(answered, key=lambda q: q.get('answered_at') or q.get('created') or '') if answered \
            else max(group, key=lambda q: q.get('created') or '')
        kept.append(winner)
        for q in group:
            if q is not winner:
                removed.append(q)
                store.delete('questionnaires', q['id'])

    return jsonify({
        'ok': True,
        'grupos_revisados': len(groups),
        'cuestionarios_eliminados': len(removed),
        'cuestionarios_conservados': len(kept),
        'eliminados_ids': [q['id'] for q in removed],
    })


@app.route('/api/admin/reconcile-studio-ninja-jobs', methods=['POST'])
def api_reconcile_studio_ninja_jobs():
    """Kevin: rediseno del import de Studio Ninja (multi-cliente, status
    real, location sin datos ajenos) despues de que 131 bodas de Norkevin
    Photography ya se habian importado con esos 3 bugs. api_admin_import_
    studio_ninja ya esta arreglado para las importaciones FUTURAS, pero
    salta los jobs que ya existen (es idempotente a proposito) -- asi que
    no corrige lo que ya esta mal en produccion. Este endpoint es esa
    correccion de una sola vez: recibe el MISMO payload {"jobs": [...]}
    corregido y, SOLO para jobs boda-sn-* que YA existen, actualiza
    location/status/segundo-cliente -- nunca crea un job nuevo (eso lo
    sigue haciendo el import normal), nunca toca quotes/payments/contracts
    (Kevin no reporto esos como incorrectos, y podria haber pagos
    registrados a mano desde el import que no hay que perder).

    Salvaguardas para no perder trabajo manual de Kevin desde el import:
    - status: solo se recalcula si el status actual sigue siendo el
      'Confirmado' hardcodeado del bug viejo. Si Kevin ya lo cambio a mano
      (Archivado, o Confirmado a proposito), no se toca.
    - location: se sobreescribe siempre con la version corregida (el bug
      reportado es justamente que el dato viejo esta mal armado, no que
      falte revisarlo caso por caso).
    - segundo cliente: solo se agrega si el job TODAVIA no tiene
      secondary_client_id (no pisa una relacion que Kevin ya haya
      ajustado a mano en el job)."""
    data = request.get_json(silent=True) or {}
    if data.get('confirm') != 'RECONCILIAR':
        return jsonify({'ok': False, 'error': 'Confirmacion requerida'}), 400
    payload = data.get('payload')
    if not isinstance(payload, dict) or not isinstance(payload.get('jobs'), list):
        return jsonify({'ok': False, 'error': 'Payload invalido: se espera {"jobs": [...]}'}), 400

    updated = []
    skipped_not_found = []
    unchanged = []

    for entry in payload.get('jobs', []):
        slug = entry.get('slug')
        if not slug:
            continue
        job_id = f'boda-sn-{slug}'
        job = get_job(job_id)
        if not job:
            skipped_not_found.append(job_id)
            continue

        tenant_id = job.get('tenant_id')
        changes = {}

        new_location = entry.get('location') or ''
        if new_location and job.get('location') != new_location:
            changes['location'] = {'antes': job.get('location'), 'despues': new_location}
            job['location'] = new_location

        if job.get('status') == 'Confirmado':
            ws = entry.get('workflow_status') or {}
            job_complete_flag = ws.get('job_complete')
            is_past_event = False
            boda_date_str = entry.get('boda_date') or job.get('boda_date')
            if boda_date_str:
                try:
                    is_past_event = date.fromisoformat(boda_date_str) < date.today()
                except ValueError:
                    is_past_event = False
            new_status = 'Listo' if (job_complete_flag is True or (job_complete_flag is not False and is_past_event)) else 'Confirmado'
            if new_status != job['status']:
                changes['status'] = {'antes': job['status'], 'despues': new_status}
                job['status'] = new_status

        clients_in = entry.get('clients') or ([entry['client']] if entry.get('client') else [])
        if len(clients_in) > 1 and not job.get('secondary_client_id'):
            c2 = clients_in[1]
            cid2 = f'client-sn-{slug}-2'
            store.upsert('clients', {
                'id': cid2,
                'first_name': c2.get('first_name') or '',
                'last_name': c2.get('last_name') or '',
                'email': c2.get('email') or '',
                'phone': c2.get('phone') or '',
                # El venue del evento NO es la direccion de facturacion
                # del cliente (misma contaminacion cerrada en las otras
                # rutas de creacion de clientes).
                'estado': 'Activo',
                'tenant_id': tenant_id,
                # Nunca None: un job sin fecha detectada (folder "[no date]")
                # deja entry['created'] en None, y un cliente con
                # 'created': None tumba clients.html al ordenar (confirmado
                # en produccion). date.today() como ultimo recurso.
                'created': job.get('created') or entry.get('created') or date.today().isoformat(),
            })
            job['secondary_client_id'] = cid2
            changes['secondary_client_id'] = {'antes': None, 'despues': cid2}

        if changes:
            store.upsert('jobs', job)
            updated.append({'job_id': job_id, 'changes': changes})
        else:
            unchanged.append(job_id)

    logger.info(f"Reconciliacion de jobs Studio Ninja: {len(updated)} actualizados, {len(unchanged)} sin cambios, {len(skipped_not_found)} no encontrados")
    return jsonify({
        'ok': True,
        'actualizados': len(updated),
        'sin_cambios': len(unchanged),
        'no_encontrados': len(skipped_not_found),
        'detalle': updated,
    })


@app.route('/api/admin/import-astral-leads', methods=['POST'])
def api_admin_import_astral_leads():
    """Carga los contactos del formulario de ASTRAL como Leads.

    Kevin: 'no quiero enviar correos de cosas que ya se enviaron antes en
    Studio Ninja'. Esto es seguro por construccion: escribe registros de lead
    directo al storage y nada mas. Un lead sin job no tiene pagos ni steps de
    workflow, que son las DOS unicas cosas que el hilo de recordatorios en
    segundo plano revisa para mandar correo (check_and_send_payment_reminders
    recorre 'payments' y _auto_fire_due_job_steps recorre 'jobs'). Ningun
    correo sale de aca.

    Idempotente: el id se deriva del email, y un lead que ya existe NO se
    sobreescribe -- asi se puede reintentar un lote sin duplicar ni pisar
    ediciones que Kevin haya hecho a mano."""
    data = request.get_json(silent=True) or {}
    if data.get('confirm') != 'IMPORTAR':
        return jsonify({'ok': False, 'error': 'Confirmacion requerida'}), 400

    tenant_id = data.get('tenant_id')
    if not tenant_id:
        return jsonify({'ok': False, 'error': 'tenant_id requerido'}), 400

    # Los ids se derivan del email, asi que un choque puede venir de
    # CUALQUIER empresa: para no pisar un lead ajeno hay que mirar todas.
    todos_los_leads = store.list_privileged(
        'leads', scope='all_tenants', reason='import de leads: evitar pisar ids ajenos')
    existing = {l.get('id') for l in todos_los_leads}
    existing_emails = {
        (l.get('email') or '').strip().lower()
        for l in todos_los_leads
        if l.get('tenant_id') == tenant_id and l.get('email')
    }

    creados, omitidos = [], []
    for item in data.get('leads') or []:
        lead_id = item.get('id')
        email = (item.get('email') or '').strip().lower()
        if not lead_id or not email:
            omitidos.append({'id': lead_id, 'motivo': 'sin id o sin email'})
            continue
        if lead_id in existing or email in existing_emails:
            omitidos.append({'id': lead_id, 'motivo': 'ya existe'})
            continue
        store.upsert('leads', {
            'id': lead_id,
            'nombre': item.get('nombre') or '',
            'email': item.get('email') or '',
            'telefono': item.get('telefono') or '',
            'status': item.get('status') or 'Nuevo',
            'fuente': item.get('fuente') or '',
            'tipo_evento': item.get('tipo_evento') or '',
            'fecha_tentativa': item.get('fecha_tentativa') or '',
            'locacion': item.get('locacion') or '',
            'notas': item.get('notas') or '',
            # Nunca None: un lead con 'created': None tumba el sort de
            # /leads igual que paso con /clients.
            'created': item.get('created') or date.today().isoformat(),
            'tenant_id': tenant_id,
        })
        existing.add(lead_id)
        existing_emails.add(email)
        creados.append(lead_id)

    logger.info(f"Leads de Astral: {len(creados)} creados, {len(omitidos)} omitidos (sin enviar ningun correo)")
    return jsonify({'ok': True, 'creados': creados, 'omitidos': omitidos})


# ============================================================
# EMAILS PENDIENTES DE APROBACION
# Despues del incidente ningun correo sale solo. Estas rutas son la unica
# forma de que un correo generado por el CRM llegue a alguien.
# ============================================================

def _pending_email_view(p):
    """Vista compacta de un pendiente, resolviendo nombres para la lista."""
    cliente = get_client(p.get('client_id')) if p.get('client_id') else None
    job = get_job(p.get('job_id')) if p.get('job_id') else None
    tenant = next((t for t in store.list('tenants')
                   if t.get('id') == p.get('tenant_id')), None) or {}
    return {
        'id': p.get('id'),
        'empresa': tenant.get('name') or p.get('tenant_id'),
        'tenant_id': p.get('tenant_id'),
        'para': p.get('to'),
        'cliente': (f"{cliente.get('first_name','')} {cliente.get('last_name','')}".strip()
                    if cliente else None),
        'job': job.get('nombre') if job else None,
        'job_id': p.get('job_id'),
        'asunto': p.get('subject'),
        'origen': p.get('source') or 'desconocido',
        'creado': p.get('created_at'),
        'estado': p.get('status'),
        'motivo_bloqueo': p.get('blocked_reason'),
        # No bloquea, pero hay que verlo antes de aprobar (direccion que no
        # es la del cliente, o que existe tambien en la otra empresa).
        'aviso_identidad': p.get('aviso_identidad'),
        'adjuntos': len(p.get('attachments') or []),
        # Secuencia completa de estados, no solo el ultimo.
        'historial': p.get('historial') or [],
        'reintentable': p.get('status') == 'failed',
    }


def _actor_actual():
    """Quien esta aprobando. Va al historial del pendiente: despues del
    incidente, 'lo mando el sistema' no es una respuesta aceptable."""
    return (session.get('user_email') or '').strip() or 'sesion sin correo'


@app.route('/emails')
def pending_emails_page():
    """Bandeja de correos por aprobar, enviados y bloqueados."""
    pendientes = sorted(store.list('pending_emails'),
                        key=lambda p: p.get('created_at') or '', reverse=True)
    vistas = [_pending_email_view(p) for p in pendientes]
    return render_template(
        'pending_emails.html',
        emails=vistas,
        conteos={
            'pendiente': sum(1 for v in vistas if v['estado'] == 'pending'),
            'enviando': sum(1 for v in vistas if v['estado'] == 'sending'),
            'enviado': sum(1 for v in vistas if v['estado'] == 'sent'),
            # Bloqueado (seguridad) y Fallo (tecnico) van separados: mezclarlos
            # haria ver un timeout de Gmail como un intento de cruce.
            'bloqueado': sum(1 for v in vistas if v['estado'] == 'blocked'),
            'fallido': sum(1 for v in vistas if v['estado'] == 'failed'),
            'cancelado': sum(1 for v in vistas if v['estado'] == 'discarded'),
        },
    )


@app.route('/api/pending-emails/<pending_id>')
def api_pending_email_detail(pending_id):
    """Detalle para la pantalla de revision, con el resultado de validar
    empresa, cliente, job y cuenta de Gmail.

    Estas validaciones son SOLO informativas: al presionar Enviar el backend
    las vuelve a correr. Mostrarlas aca sirve para revisar antes, no para
    autorizar.
    """
    from src import gmail_delivery
    from src.mail_tracker import check_recipient_identity, check_same_tenant

    p = store.get('pending_emails', pending_id)
    if not p:
        abort(404)

    actual = get_current_tenant_id()
    tenant = next((t for t in store.list('tenants') if t.get('id') == actual), None) or {}
    cliente = get_client(p.get('client_id')) if p.get('client_id') else None
    job = get_job(p.get('job_id')) if p.get('job_id') else None
    motivo = check_same_tenant(actual, lead_id=p.get('lead_id'),
                               job_id=p.get('job_id'), template_id=p.get('template_id'))
    aviso = None
    if not motivo:
        motivo, aviso = check_recipient_identity(actual, p.get('to'), p.get('client_id'))
    gmail_ok = gmail_delivery.is_connected(tenant_id=actual)

    return jsonify({
        'ok': True,
        'email': _pending_email_view(p),
        'cuerpo': p.get('body') or '',
        'desde': gmail_delivery.connected_email(tenant_id=actual) or None,
        'empresa': tenant.get('name') or actual,
        'cliente_email': (cliente or {}).get('email'),
        'validaciones': {
            'empresa': bool(actual) and p.get('tenant_id') == actual,
            # "cliente" ya no es solo que exista: tiene que ser un cliente de
            # ESTA empresa. La direccion no identifica a nadie.
            'cliente': cliente is not None or not p.get('client_id'),
            'job': job is not None or not p.get('job_id'),
            'gmail': gmail_ok,
            'sin_cruce': motivo is None,
        },
        'motivo': motivo,
        # No bloquea, pero es justo lo que hay que mirar dos veces: direccion
        # que no es la del cliente, o que existe tambien en la otra empresa.
        'aviso_identidad': aviso,
        'enviable': (p.get('status') == 'pending' and motivo is None and gmail_ok
                     and p.get('tenant_id') == actual),
    })


@app.route('/api/pending-emails/<pending_id>/send', methods=['POST'])
def api_pending_email_send(pending_id):
    """Aprueba y envia. Revalida TODO del lado del servidor."""
    from src.mail_tracker import MailTracker

    resultado = MailTracker().approve_and_send(pending_id, actor=_actor_actual())
    if not resultado.get('ok'):
        # Se devuelve tambien el pendiente (si lo hay) para que la pantalla
        # muestre el estado nuevo -- bloqueado o fallido -- sin recargar.
        respuesta = {'ok': False, 'error': resultado.get('error')}
        if resultado.get('pendiente'):
            respuesta['email'] = _pending_email_view(resultado['pendiente'])
        return jsonify(respuesta), 400
    return jsonify({'ok': True, 'email': _pending_email_view(resultado['pendiente'])})


@app.route('/api/pending-emails/<pending_id>/retry', methods=['POST'])
def api_pending_email_retry(pending_id):
    """Reintenta un correo que fallo. MANUAL, nunca automatico.

    Kevin: "nada de fallo -> enviar automaticamente otra vez". Un reintento
    automatico es como un fallo de red se convierte en tres copias del mismo
    correo -- que es literalmente lo que paso en el incidente.

    Solo aplica a FALLO (problema tecnico). Un BLOQUEADO no se reintenta: la
    razon del bloqueo sigue ahi y forzarlo seria saltarse la validacion.
    """
    from src.mail_tracker import MailTracker

    # sender_tenant_id explicito: no depender de que store.current_tenant_id()
    # resuelva la sesion por su cuenta (ver nota en MailTracker.retry_failed).
    resultado = MailTracker().retry_failed(
        pending_id, actor=_actor_actual(), sender_tenant_id=get_current_tenant_id())
    if not resultado.get('ok'):
        respuesta = {'ok': False, 'error': resultado.get('error')}
        if resultado.get('pendiente'):
            respuesta['email'] = _pending_email_view(resultado['pendiente'])
        return jsonify(respuesta), 400
    return jsonify({'ok': True, 'email': _pending_email_view(resultado['pendiente'])})


@app.route('/api/pending-emails/<pending_id>/discard', methods=['POST'])
def api_pending_email_discard(pending_id):
    from src.mail_tracker import MailTracker

    resultado = MailTracker().discard_pending(pending_id, actor=_actor_actual())
    if not resultado.get('ok'):
        return jsonify({'ok': False, 'error': resultado.get('error')}), 404
    return jsonify({'ok': True, 'email': _pending_email_view(resultado['pendiente'])})


@app.route('/api/admin/incident-report')
def api_admin_incident_report():
    """Reconstruye el alcance del incidente desde mail_log. SOLO LECTURA.

    Kevin: "no quiero tener que reconstruir esto manualmente desde logs
    crudos". Con una sola llamada queda claro cuantos correos salieron, a
    quien, de que empresa era cada destinatario y cuales eran cobros.

    No modifica ni borra nada del log: esos datos son la evidencia.

    Parametros opcionales: ?desde=YYYY-MM-DD&hasta=YYYY-MM-DD
    """
    desde = (request.args.get('desde') or '').strip()
    hasta = (request.args.get('hasta') or '').strip()

    # Palabras que delatan un correo de dinero, que son los mas delicados.
    COBRO = ('pago', 'cobro', 'factura', 'recordatorio', 'saldo', 'invoice')

    # Duenos reales de cada correo destino, para saber a que empresa
    # pertenecia realmente quien lo recibio (independiente de quien envio).
    #
    # Se guarda un CONJUNTO de empresas por direccion, no la primera que
    # aparece: la misma direccion puede existir como cliente en las dos
    # empresas y son dos personas distintas. Quedarse con la primera
    # convertiria una ambiguedad real en una certeza inventada -- y este
    # reporte es evidencia del incidente, no puede adivinar.
    duenos_por_email = {}
    for tabla in ('clients', 'leads'):
        for r in store.list_privileged(tabla, scope='all_tenants',
                                            reason='reporte del incidente (admin)'):
            correo = (r.get('email') or '').strip().lower()
            if correo and r.get('tenant_id'):
                duenos_por_email.setdefault(correo, set()).add(r['tenant_id'])

    def _identidad(destino, remitente):
        """A quien pertenecia de verdad quien recibio el correo.

        Devuelve (empresa_o_None, marca). La marca es la conclusion honesta:
        no todo caso se puede resolver.
        """
        duenos = duenos_por_email.get(destino) or set()
        if not duenos:
            # No esta como cliente ni lead de nadie: no se puede saber.
            return None, 'AMBIGUOUS_RECIPIENT_IDENTITY'
        if len(duenos) > 1:
            # Existe en las dos empresas. mail_log guarda la direccion, no el
            # client_id, asi que desde aca es imposible saber a cual de las
            # dos personas se le escribio. REQUIERE REVISION manual.
            return None, 'AMBIGUOUS_RECIPIENT_IDENTITY'
        unico = next(iter(duenos))
        if remitente and unico != remitente:
            return unico, 'CROSS_TENANT'
        return unico, 'OK'

    nombres = {t.get('id'): t.get('name') for t in store.list('tenants')}

    entradas = []
    for m in store.list_privileged('mail_log', scope='all_tenants',
                                    reason='reporte del incidente (admin)'):
        cuando = m.get('sent_at') or ''
        if desde and cuando[:10] < desde:
            continue
        if hasta and cuando[:10] > hasta:
            continue
        destino = (m.get('to') or '').strip().lower()
        asunto = m.get('subject') or ''
        pertenece_a, marca = _identidad(destino, m.get('tenant_id'))
        entradas.append({
            'fecha': cuando,
            'para': m.get('to'),
            'asunto': asunto,
            'estado': m.get('status'),
            'motivo_bloqueo': m.get('blocked_reason'),
            'job_id': m.get('job_id'),
            'lead_id': m.get('lead_id'),
            'template_id': m.get('template_id'),
            'enviado_por_empresa': m.get('tenant_id'),
            'destinatario_pertenece_a': pertenece_a,
            'identidad': marca,
            'empresas_con_ese_correo': sorted(duenos_por_email.get(destino) or []),
            'es_cobro': any(p in asunto.lower() for p in COBRO),
            'proveedor': m.get('delivery_provider'),
        })

    enviados = [e for e in entradas if e['estado'] == 'sent']
    cruzados = [e for e in enviados if e['identidad'] == 'CROSS_TENANT']
    # Los que NO se pueden clasificar con certeza van aparte, nunca mezclados
    # con los confirmados: un cruce dudoso contado como cruce confirmado
    # infla el incidente, y contado como correcto lo esconde.
    ambiguos = [e for e in enviados if e['identidad'] == 'AMBIGUOUS_RECIPIENT_IDENTITY']

    def _por_empresa(lista, campo):
        salida = {}
        for e in lista:
            k = e.get(campo)
            salida[nombres.get(k, k or '(sin empresa)')] = salida.get(
                nombres.get(k, k or '(sin empresa)'), 0) + 1
        return salida

    return jsonify({
        'ok': True,
        'rango': {'desde': desde or None, 'hasta': hasta or None},
        'totales': {
            'intentos': len(entradas),
            'enviados': len(enviados),
            'bloqueados': sum(1 for e in entradas if e['estado'] == 'blocked'),
            'fallidos': sum(1 for e in entradas if e['estado'] == 'failed'),
            'destinatarios_unicos': len({e['para'] for e in enviados if e['para']}),
            'cobros_enviados': sum(1 for e in enviados if e['es_cobro']),
            'enviados_a_otra_empresa': len(cruzados),
            'destinatario_ambiguo': len(ambiguos),
        },
        'enviados_por_empresa_remitente': _por_empresa(enviados, 'enviado_por_empresa'),
        'destinatarios_por_empresa_real': _por_empresa(enviados, 'destinatario_pertenece_a'),
        'cruzados': cruzados,
        'ambiguos': ambiguos,
        'detalle': sorted(entradas, key=lambda e: e['fecha'] or '', reverse=True),
        'nota': ('Solo lectura. "cruzados" son los correos que salieron desde '
                 'una empresa hacia un destinatario que pertenece a otra: '
                 'exactamente lo que hay que revisar del incidente. '
                 '"ambiguos" (AMBIGUOUS_RECIPIENT_IDENTITY) son los que NO se '
                 'pueden clasificar: la direccion existe en las dos empresas, '
                 'o en ninguna. mail_log guarda la direccion y no el '
                 'client_id, asi que esos REQUIEREN REVISION manual y no se '
                 'cuentan como cruzados ni como correctos.'),
    })


@app.route('/api/admin/public-links-audit')
def api_admin_public_links_audit():
    """Clasifica los enlaces publicos existentes. SOLO LECTURA. DRY-RUN.

    No genera, no rota, no desactiva y no toca ningun enlace. Kevin: "por
    ahora esto debe servir unicamente para clasificacion y dry-run".

    Los enlaces /quotes /contracts /questionnaires /portal son bearer: el
    cliente los abre sin sesion, el enlace ES la credencial. Su seguridad
    depende enteramente de que el id no se pueda adivinar -- y los importados
    de Studio Ninja se construyeron con el nombre de la boda
    (contract-sn-boda-rebeca-y-jos), o sea reconstruibles por cualquiera que
    supiera como se llamaba la boda.

    Se cruzan DOS ejes, porque responden preguntas distintas:

      forma del id  -> que tan adivinable es      (el riesgo)
      actividad     -> si rotarlo romperia algo   (el costo)

    Lo que hay que atender primero es la interseccion: predecible Y activo.

    Parametro opcional: ?tenant_id=... para mirar una sola empresa.
    """
    solo_tenant = (request.args.get('tenant_id') or '').strip() or None

    # uuid4 recortado a 8 hex: lo que genera la app hoy.
    ALEATORIO = re.compile(r'^[a-z]+-[0-9a-f]{8}$')

    def forma_del_id(record):
        rid = str(record.get('id') or '')
        if record.get('public_token_hash'):
            return 'ALREADY_MIGRATED'
        if not rid:
            return 'INVALID'
        if _ID_LEGACY.match(rid):
            return 'PREDICTABLE_LEGACY'
        if ALEATORIO.match(rid):
            return 'SECURE_UUID'
        return 'MISSING_TOKEN'

    def _todos(tabla):
        registros = store.list_privileged(
            tabla, scope='all_tenants',
            reason='auditoria de enlaces publicos (admin)')
        if solo_tenant:
            registros = [r for r in registros if r.get('tenant_id') == solo_tenant]
        return registros

    # Se leen una vez y se indexan: clasificar necesita mirar el job y los
    # pagos de cada recurso, y recorrer las tablas por recurso seria
    # cuadratico sobre miles de registros.
    jobs = {j['id']: j for j in _todos('jobs') if j.get('id')}
    pagos_por_job, pagos_por_cliente = {}, {}
    for pago in _todos('payments'):
        pagos_por_job.setdefault(pago.get('job_id'), []).append(pago)
        pagos_por_cliente.setdefault(pago.get('client_id'), []).append(pago)

    def contexto_de(record, es_portal=False):
        if es_portal:
            # El portal no cuelga de un job sino de una persona, y da acceso
            # a todo lo suyo: basta con que UNO de sus jobs siga vivo.
            suyos = [j for j in jobs.values()
                     if j.get('client_id') == record.get('id')]
            job = next((j for j in suyos if j.get('job_complete') is not True),
                       suyos[0] if suyos else None)
            return public_links.Contexto(
                job=job,
                pagos=pagos_por_cliente.get(record.get('id'), []),
                tareas_pendientes=_tareas_pendientes(job),
                cliente=record)
        job_id = record.get('job_id')
        job = jobs.get(job_id) if job_id else None
        return public_links.Contexto(
            job=job,
            pagos=pagos_por_job.get(job_id, []) if job_id else [],
            tareas_pendientes=_tareas_pendientes(job))

    TABLAS = (('quotes', 'quote'), ('contracts', 'contract'),
              ('questionnaires', 'questionnaire'), ('clients', 'portal'))

    por_forma, por_actividad, cruce = {}, {}, {}
    atender = []   # predecible Y activo: lo que de verdad importa
    revisar = []   # no se pudo determinar
    for tabla, tipo in TABLAS:
        forma_conteo, actividad_conteo = {}, {}
        for r in _todos(tabla):
            forma = forma_del_id(r)
            veredicto = public_links.clasificar(
                tipo, r, contexto_de(r, tipo == 'portal'))
            estado = veredicto['estado']

            forma_conteo[forma] = forma_conteo.get(forma, 0) + 1
            actividad_conteo[estado] = actividad_conteo.get(estado, 0) + 1
            clave = forma + '/' + estado
            cruce[clave] = cruce.get(clave, 0) + 1

            if forma != 'PREDICTABLE_LEGACY':
                continue
            if estado == public_links.ACTIVO and len(atender) < 50:
                atender.append({
                    'tipo': tipo,
                    # Huella, nunca el id completo: es una credencial.
                    'recurso': public_tokens.huella(r.get('id')),
                    'tenant_id': r.get('tenant_id'),
                    'por_que_sigue_activo': veredicto['razones'],
                    'accion_propuesta': 'ETAPA_1: emitir token nuevo, mantener alias',
                })
            elif estado == public_links.REVISAR and len(revisar) < 50:
                revisar.append({
                    'tipo': tipo,
                    'recurso': public_tokens.huella(r.get('id')),
                    'tenant_id': r.get('tenant_id'),
                    'que_falta_saber': veredicto['dudas'],
                })
        por_forma[tipo] = forma_conteo
        por_actividad[tipo] = actividad_conteo

    return jsonify({
        'ok': True,
        'dry_run': True,
        'empresa': solo_tenant,
        'por_forma_del_id': por_forma,
        'por_actividad': por_actividad,
        'cruce_forma_actividad': cruce,
        'atender_primero': atender,
        'requieren_revision': revisar,
        'configuracion': {
            'dias_actividad_reciente': public_links.DIAS_ACTIVIDAD_RECIENTE,
            'dias_alias_legacy': public_links.DIAS_ALIAS_LEGACY,
            'nota': ('dias_alias_legacy=0 significa SIN LIMITE: ningun enlace '
                     'viejo se desactiva por el paso del tiempo mientras no se '
                     'fije el periodo. Se cambia con LEGACY_LINK_ALIAS_DAYS.'),
        },
        'leyenda_forma': {
            'SECURE_UUID': 'id aleatorio (uuid4), no se puede adivinar',
            'PREDICTABLE_LEGACY': 'derivado del nombre de la boda: reconstruible',
            'MISSING_TOKEN': 'no sigue ningun formato conocido, revisar a mano',
            'ALREADY_MIGRATED': 'ya tiene public_token_hash separado del id',
            'INVALID': 'sin id',
        },
        'leyenda_actividad': {
            'ACTIVO': 'cumple al menos una condicion de uso: rotarlo romperia algo',
            'INACTIVO': 'se pudo evaluar todo lo que aplica y nada indica uso',
            'REVIEW_REQUIRED': 'NO se pudo determinar con seguridad',
        },
        'nota': ('SOLO LECTURA y DRY-RUN. No se genero, roto ni desactivo '
                 'ningun enlace. "atender_primero" es la interseccion que '
                 'importa: id adivinable Y todavia en uso. Los ids salen como '
                 'huella (ab12****89) y nunca completos, porque un enlace '
                 'publico es una credencial.'),
    })


def _tareas_pendientes(job):
    """Cuantos pasos del workflow del job siguen sin cerrarse.

    Devuelve None si no se pudo saber -- que NO es lo mismo que 0, y esa
    diferencia es justo la que decide entre INACTIVO y REVIEW_REQUIRED.
    """
    if not job:
        return None
    try:
        instancia = store.get_tenant_dict('workflow_instances').get(job.get('id'))
    except Exception:
        return None
    pasos = (instancia or {}).get('steps') or []
    if not pasos:
        return None
    return sum(1 for p in pasos
               if str(p.get('status') or '').lower() in ('pending', 'in_progress', ''))


@app.route('/api/admin/orphan-audit')
def api_admin_orphan_audit():
    """Inventario de registros sin empresa asignada. SOLO LECTURA.

    Con el aislamiento cerrado un registro sin tenant_id queda invisible y
    su enlace publico responde 404, asi que hay que verlos antes de
    desplegar.

    Para cada huerfano se intenta deducir la empresa siguiendo relaciones
    reales (el job del pago, el cliente del job...). Kevin fue explicito:
    nada se asigna solo. Cada caso sale clasificado y el ambiguo queda para
    que lo decida el.
    """
    # De que registro cuelga cada tabla para poder deducir su empresa.
    RELACIONES = {
        'payments': [('job_id', 'jobs'), ('client_id', 'clients')],
        'quotes': [('job_id', 'jobs'), ('lead_id', 'leads'), ('client_id', 'clients')],
        'contracts': [('job_id', 'jobs'), ('client_id', 'clients')],
        'questionnaires': [('job_id', 'jobs'), ('lead_id', 'leads')],
        'jobs': [('client_id', 'clients'), ('lead_id', 'leads')],
        'clients': [('lead_id', 'leads')],
        'calendar': [('job_id', 'jobs')],
        'files': [('job_id', 'jobs'), ('client_id', 'clients')],
        'mail_log': [('job_id', 'jobs'), ('lead_id', 'leads')],
    }

    from src.storage import TENANT_SCOPED_TABLES
    tenants = [t.get('id') for t in store.list('tenants')]

    tabla_resumen = []
    huerfanos = []
    for tabla in sorted(TENANT_SCOPED_TABLES):
        registros = store.list_privileged(tabla, scope='all_tenants',
                                        reason='auditoria de huerfanos (admin)')
        por_cuenta = {tid: 0 for tid in tenants}
        sin_cuenta = 0
        desconocidas = 0
        for r in registros:
            tid = r.get('tenant_id')
            if not tid:
                sin_cuenta += 1
            elif tid in por_cuenta:
                por_cuenta[tid] += 1
            else:
                desconocidas += 1

        fila = {'tabla': tabla, 'total': len(registros),
                'sin_tenant': sin_cuenta, 'tenant_desconocido': desconocidas}
        fila.update({tid: por_cuenta[tid] for tid in tenants})
        tabla_resumen.append(fila)

        for r in registros:
            if r.get('tenant_id'):
                continue
            pistas = []
            candidatos = set()
            for campo, tabla_rel in RELACIONES.get(tabla, []):
                valor = r.get(campo)
                if not valor:
                    continue
                dueno = store.owner_tenant_of(tabla_rel, valor)
                if dueno:
                    candidatos.add(dueno)
                    pistas.append(f'{campo}={valor} -> {tabla_rel} de {dueno}')

            if len(candidatos) == 1:
                confianza = 'HIGH_CONFIDENCE'
            elif len(candidatos) > 1:
                # Relaciones que apuntan a empresas distintas: justo el tipo
                # de caso que no se puede resolver adivinando.
                confianza = 'REVIEW_REQUIRED'
            else:
                confianza = 'UNRESOLVED'

            huerfanos.append({
                'tabla': tabla,
                'id': r.get('id'),
                'posible_tenant': list(candidatos)[0] if len(candidatos) == 1 else None,
                'candidatos': sorted(candidatos),
                'confianza': confianza,
                'pistas': pistas or ['sin relaciones que permitan deducirla'],
            })

    return jsonify({
        'ok': True,
        'resumen_por_tabla': tabla_resumen,
        'huerfanos': huerfanos,
        'totales': {
            'huerfanos': len(huerfanos),
            'HIGH_CONFIDENCE': sum(1 for h in huerfanos if h['confianza'] == 'HIGH_CONFIDENCE'),
            'REVIEW_REQUIRED': sum(1 for h in huerfanos if h['confianza'] == 'REVIEW_REQUIRED'),
            'UNRESOLVED': sum(1 for h in huerfanos if h['confianza'] == 'UNRESOLVED'),
        },
        'nota': ('Solo lectura: no se asigno ninguna empresa. Los '
                 'HIGH_CONFIDENCE se pueden proponer para asignacion; los '
                 'demas los tiene que decidir Kevin.'),
    })


@app.route('/api/admin/workflow-cleanup', methods=['POST'])
def api_admin_workflow_cleanup():
    """Marca como completadas las tareas de workflow de los jobs activos,
    dejando 'Trabajo completado' PENDIENTE.

    Kevin: "quiero ser yo quien marque manualmente Job Complete cuando
    considere que realmente terminamos esa boda".

    Dos cosas que NO hace, a proposito:

    - No ejecuta la accion del paso. Marcar un 'Auto send email' como
      completado es un cambio de ESTADO, no una reproduccion del workflow:
      no genera ni manda ningun correo. Esto importa mucho despues del
      incidente.
    - No toca nada financiero. Pagos, facturas, cuotas, montos, saldos y
      fechas quedan exactamente igual.

    Arranca en dry_run: sin 'confirm' solo informa que pasaria.
    """
    data = request.get_json(silent=True) or {}
    ejecutar = data.get('confirm') == 'LIMPIAR_WORKFLOWS'

    # Huella financiera ANTES, para poder demostrar que no se movio nada.
    def _huella_financiera():
        huella = {}
        for p in store.list_privileged('payments', scope='all_tenants',
                                            reason='huella financiera antes/despues (admin)'):
            huella[p.get('id')] = (p.get('amount'), p.get('status'),
                                   p.get('due_date'), p.get('paid_date'))
        return huella

    antes = _huella_financiera()

    resumen = {}
    cambios = []
    for tenant in store.list('tenants'):
        tid = tenant.get('id')
        jobs = store.list_privileged('jobs', tenant_id=tid,
                                    reason='limpieza de workflows por empresa (admin)')
        activos = [j for j in jobs if j.get('status') not in ('Archivado', 'Cancelado')]
        por_cambiar = 0
        tareas = 0
        job_complete_pendientes = 0

        for job in activos:
            try:
                # tenant_id=tid explicito: sin esto, la instancia se busca
                # contra la sesion activa del admin, no contra la cuenta que
                # este loop esta recorriendo -- ver docstring de
                # _instancia_es_de_la_cuenta.
                steps, _, _ = compute_workflow_steps_for_job(job, tenant_id=tid)
            except Exception:
                continue
            pendientes = [s for s in steps if s['status'] == 'pending']
            # 'Trabajo completado' es el unico que NO se toca.
            a_marcar = [s for s in pendientes if 'completado' not in (s['name'] or '').lower()]
            queda = [s for s in pendientes if 'completado' in (s['name'] or '').lower()]
            if queda:
                job_complete_pendientes += 1
            if a_marcar:
                por_cambiar += 1
                tareas += len(a_marcar)
                cambios.append({'tenant_id': tid, 'job_id': job.get('id'),
                                'job': job.get('nombre'),
                                'tareas': [s['name'] for s in a_marcar]})

        resumen[tenant.get('name') or tid] = {
            'jobs_encontrados': len(jobs),
            'jobs_activos': len(activos),
            'jobs_que_cambiarian': por_cambiar,
            'tareas_a_marcar_completadas': tareas,
            'job_complete_que_siguen_pendientes': job_complete_pendientes,
            'pagos_afectados': 0,
            'facturas_afectadas': 0,
            'emails_generados': 0,
            'emails_enviados': 0,
        }

    aplicados = []
    if ejecutar:
        for cambio in cambios:
            job = next((j for j in store.list_privileged(
                'jobs', tenant_id=cambio['tenant_id'],
                reason='limpieza de workflows por empresa (admin)')
                if j.get('id') == cambio['job_id']), None)
            if not job:
                continue
            # tenant_id=cambio['tenant_id'] explicito por la misma razon que
            # arriba: 'cambios' mezcla filas de varias cuentas.
            instancia = _workflow_instance_for('job', job['id'], tenant_id=cambio['tenant_id'])
            if not instancia:
                continue
            tmpl = PRODUCTION_WORKFLOW()
            for step in tmpl.steps:
                if 'completado' in (step.name or '').lower():
                    continue
                if instancia.step_states.get(step.id) != StepStatus.DONE:
                    # SKIPPED, no DONE: deja claro en el historial que se
                    # cerro por esta limpieza y no porque se ejecutara.
                    instancia.step_states[step.id] = StepStatus.SKIPPED
            workflow_engine._save_to_storage()
            aplicados.append(job['id'])

    despues = _huella_financiera()
    finanzas_intactas = antes == despues

    return jsonify({
        'ok': True,
        'modo': 'ejecutado' if ejecutar else 'dry_run',
        'resumen': resumen,
        'detalle': cambios[:50],
        'total_cambios': len(cambios),
        'jobs_modificados': aplicados,
        'finanzas_intactas': finanzas_intactas,
        'nota': ('Marcar tareas NO ejecuta su accion: no se genero ni envio '
                 'ningun correo. "Trabajo completado" queda pendiente.'),
    })


@app.route('/api/admin/tenant-inventory')
def api_admin_tenant_inventory():
    """Solo lectura. Kevin trajo el export de la cuenta de ASTRAL (no la de
    Norkevin) y pidio 'llenar todo'. Antes de importar hay que saber que hay
    ya en cada tenant: importar encima de lo que ya existe duplicaria bodas
    reales, y meterlo en el tenant equivocado mezclaria dos negocios."""
    # Lectura cruzada explicita: este reporte existe justamente para comparar
    # las dos empresas. store.list() aca devolveria [] (la peticion admin no
    # tiene cuenta activa) y el inventario saldria vacio sin decir por que.
    def _todas(tabla):
        return store.list_privileged(tabla, scope='all_tenants',
                                     reason='inventario por empresa (admin)')

    tenants = store.list('tenants')
    jobs = _todas('jobs')
    clients = _todas('clients')
    leads = _todas('leads')
    quotes = _todas('quotes')
    payments = _todas('payments')
    contracts = _todas('contracts')

    def by_tenant(records):
        out = {}
        for r in records:
            # Los huerfanos se cuentan aparte con una etiqueta y no con None:
            # asi se ven en el reporte en vez de desaparecer.
            clave = r.get('tenant_id') or '(sin cuenta)'
            out[clave] = out.get(clave, 0) + 1
        return out

    # Detalle de los jobs pedidos: sirve para saber si ya traen cotizacion,
    # pagos y contrato, o si solo existe el cabezal del job.
    wanted = set(request.args.get('job_ids', '').split(',')) - {''}
    detalle = []
    for job in jobs:
        if wanted and job.get('id') not in wanted:
            continue
        if not wanted and not str(job.get('id', '')).startswith('boda-sn-'):
            continue
        jid = job.get('id')
        detalle.append({
            'id': jid,
            'nombre': job.get('nombre'),
            'boda_date': job.get('boda_date'),
            'tenant_id': job.get('tenant_id'),
            'status': job.get('status'),
            'location': job.get('location'),
            'lead_id': job.get('lead_id'),
            'quotes': sum(1 for q in quotes if q.get('job_id') == jid),
            'payments': sum(1 for p in payments if p.get('job_id') == jid),
            'contracts': sum(1 for c in contracts if c.get('job_id') == jid),
        })

    # Registros sin cuenta. Con el aislamiento cerrado quedan invisibles
    # (antes se veian desde cualquier negocio, que es justo el problema), asi
    # que hay que saber si existen ANTES de desplegar: un enlace publico a
    # una cotizacion huerfana responderia 404.
    from src.storage import TENANT_SCOPED_TABLES
    huerfanos = {}
    for tabla in sorted(TENANT_SCOPED_TABLES):
        sin_cuenta = [r.get('id') for r in store.list_privileged(
            tabla, scope='all_tenants', reason='inventario por empresa (admin)') if not r.get('tenant_id')]
        if sin_cuenta:
            huerfanos[tabla] = {'total': len(sin_cuenta), 'ejemplos': sin_cuenta[:10]}

    # Credenciales de Gmail en disco. Kevin: "quiero terminar esta correccion
    # sin ningun credential fallback historico escondido". Se listan TODAS
    # las que existen, incluida la global vieja, para poder verla y decidir.
    credenciales = []
    try:
        for archivo in sorted(os.listdir(store.data_dir)):
            if not archivo.startswith('google_token'):
                continue
            ruta = os.path.join(store.data_dir, archivo)
            info = {'archivo': archivo, 'de_cuenta': None, 'email': None,
                    'tiene_refresh_token': False, 'modificado': None,
                    'retirado': archivo.endswith('.retirado')}
            if archivo.startswith('google_token_'):
                info['de_cuenta'] = archivo[len('google_token_'):].replace('.json', '')
            else:
                # Sin sufijo de cuenta = la global vieja, la que uso el hilo
                # sin sesion durante el incidente.
                info['de_cuenta'] = 'GLOBAL (sin cuenta)'
            try:
                info['modificado'] = datetime.fromtimestamp(os.path.getmtime(ruta)).isoformat()
                import json as _json_cred
                with open(ruta, encoding='utf-8') as fh:
                    tok = _json_cred.load(fh)
                info['email'] = tok.get('email')
                info['tiene_refresh_token'] = bool(tok.get('refresh_token'))
            except Exception as e:
                info['error'] = str(e)
            credenciales.append(info)
    except Exception as e:
        credenciales.append({'error': str(e)})

    return jsonify({
        'ok': True,
        'credenciales_gmail': credenciales,
        'huerfanos_sin_tenant': huerfanos,
        'tenants': [{'id': t.get('id'), 'name': t.get('name'),
                     'login_email': t.get('login_email'), 'active': t.get('active')}
                    for t in tenants],
        'totales': {
            'jobs': by_tenant(jobs), 'clients': by_tenant(clients),
            'leads': by_tenant(leads), 'quotes': by_tenant(quotes),
            'payments': by_tenant(payments), 'contracts': by_tenant(contracts),
        },
        'jobs': detalle,
    })


@app.route('/api/admin/list-studio-ninja-clients')
def api_list_studio_ninja_clients():
    """Solo lectura -- Kevin: 'arregla todos los clientes, no solo los de
    segundo cliente, revisa todos'. Para auditar los 131 jobs importados
    contra el export real de Contactos de Studio Ninja hace falta ver que
    quedo guardado de verdad en cada uno (cliente principal + secundario
    si tiene), no solo confiar en lo que el import creyo que leyo."""
    jobs = [j for j in store.list('jobs') if str(j.get('id', '')).startswith('boda-sn-')]
    out = []
    for job in jobs:
        primary = get_client(job.get('client_id')) if job.get('client_id') else None
        secondary = get_client(job.get('secondary_client_id')) if job.get('secondary_client_id') else None
        out.append({
            'job_id': job['id'],
            'job_name': job.get('nombre'),
            'boda_date': job.get('boda_date'),
            'primary': ({
                'id': primary.get('id'), 'first_name': primary.get('first_name'),
                'last_name': primary.get('last_name'), 'email': primary.get('email'),
                'phone': primary.get('phone'),
            } if primary else None),
            'secondary': ({
                'id': secondary.get('id'), 'first_name': secondary.get('first_name'),
                'last_name': secondary.get('last_name'), 'email': secondary.get('email'),
                'phone': secondary.get('phone'),
            } if secondary else None),
        })
    return jsonify({'ok': True, 'total': len(out), 'jobs': out})


@app.route('/api/admin/fix-secondary-clients', methods=['POST'])
def api_fix_secondary_clients():
    """Kevin: el segundo cliente de una boda se adivinaba del titulo del
    job (ej. 'Boda X y Y'), pero cruzarlo contra el export real de
    Contactos de Studio Ninja mostro que la mayoria de esos "matches" por
    nombre eran ambiguos entre 1240 contactos (mismo nombre, apellido
    distinto) -- Kevin: "quitalos por ahora" en vez de dejar un contacto
    incorrecto. Este endpoint hace las 2 cosas de una sola vez:
      - 'confirm': el nombre SI se pudo verificar 1 a 1 contra Studio
        Ninja (nombre y apellido coinciden) -- se corrige el registro de
        cliente con el email/telefono real.
      - 'remove': el nombre no se pudo confirmar con certeza -- se quita
        el vinculo de segundo cliente del job y se borra el registro de
        cliente placeholder (mejor sin el dato que con uno equivocado)."""
    data = request.get_json(silent=True) or {}
    if data.get('confirm') != 'CONFIRMAR':
        return jsonify({'ok': False, 'error': 'Confirmacion requerida'}), 400

    confirmed = []
    for item in data.get('confirm_clients') or []:
        job = get_job(f"boda-sn-{item['slug']}")
        if not job:
            continue
        # Kevin: "arregla todos los clientes, no solo los de segundo
        # cliente" -- role deja apuntar al principal (client_id) o al
        # secundario (secondary_client_id), default 'secondary' para no
        # romper llamadas anteriores que no mandaban role.
        role = item.get('role', 'secondary')
        field = {'primary': 'client_id', 'planner': 'planner_client_id'}.get(
            role, 'secondary_client_id')
        c = get_client(job[field]) if job.get(field) else None
        if c is None:
            # Todavia no existe ese rol en el job: se crea. Solo llega aca con
            # contactos verificados por email contra el export de Studio Ninja.
            if role == 'primary':
                continue  # el principal siempre existe; crearlo seria un bug
            suffix = '-2' if role == 'secondary' else '-planner'
            c = {
                'id': f"client-sn-{item['slug']}{suffix}",
                # Sin heredar el venue como address -- ver nota en
                # _ensure_client_for_lead.
                'estado': 'Activo',
                'tenant_id': job.get('tenant_id'),
                'created': job.get('created') or date.today().isoformat(),
            }
            job[field] = c['id']
            store.upsert('jobs', job)
        c['first_name'] = item['first_name']
        c['last_name'] = item['last_name']
        c['email'] = item.get('email') or ''
        c['phone'] = item.get('phone') or ''
        store.upsert('clients', c)
        confirmed.append({'job_id': job['id'], 'role': role, 'client_id': c['id']})

    # Kevin: "arregla los nombres del evento porque estan incompletos o raros".
    # El zip de Studio Ninja corrompio los acentos del titulo ('Jos_' por
    # 'Jose'), y muchos titulos arrastran ruido del formulario web.
    renamed = []
    for item in data.get('job_names') or []:
        job = get_job(f"boda-sn-{item['slug']}")
        if not job or not item.get('nombre'):
            continue
        if job.get('nombre') == item['nombre']:
            continue
        renamed.append({'job_id': job['id'], 'antes': job.get('nombre'), 'despues': item['nombre']})
        job['nombre'] = item['nombre']
        store.upsert('jobs', job)

    # Kevin: "Location debe contener unicamente informacion de ubicacion...
    # debe revisarse la fuente de datos, no simplemente esconder esa
    # informacion en mobile". El parser pegaba columnas vecinas del PDF, asi
    # que a 10 jobs se les colo el horario, la fecha y hasta el telefono y
    # correo del propio estudio.
    ubicaciones = []
    for item in data.get('job_locations') or []:
        job = get_job(f"boda-sn-{item['slug']}")
        if not job:
            continue
        nueva = (item.get('location') or '').strip()
        if (job.get('location') or '') == nueva:
            continue
        ubicaciones.append({'job_id': job['id'], 'antes': job.get('location'), 'despues': nueva})
        job['location'] = nueva
        store.upsert('jobs', job)

    removed = []
    for slug in data.get('remove_slugs') or []:
        job = get_job(f"boda-sn-{slug}")
        if not job or not job.get('secondary_client_id'):
            continue
        cid = job['secondary_client_id']
        # store.upsert hace un MERGE superficial (dict.update), no un
        # reemplazo completo -- job.pop() + upsert no borra el campo en el
        # storage porque la key simplemente no aparece en el dict que se
        # manda, y .update() nunca toca keys ausentes. Hay que poner el
        # valor en None explicitamente para que de verdad se desvincule.
        job['secondary_client_id'] = None
        store.upsert('jobs', job)
        store.delete('clients', cid)
        removed.append(job['id'])

    logger.info(f"Clientes: {len(confirmed)} corregidos con datos reales, {len(removed)} quitados por no poder confirmarse, {len(renamed)} eventos renombrados")
    return jsonify({'ok': True, 'confirmados': confirmed, 'quitados': removed,
                    'renombrados': renamed, 'ubicaciones': ubicaciones})


@app.route('/questionnaires/<questionnaire_id>')
def questionnaire_view(questionnaire_id):
    """Vista web del cuestionario (cliente): formulario para completar los detalles de la boda."""
    q = store.get('questionnaires', questionnaire_id)
    if not q:
        abort(404)
    job = get_job(q.get('job_id', '')) if q.get('job_id') else None
    client = get_client(q.get('client_id', '')) if q.get('client_id') else None
    brand = resolve_pdf_brand((job.get('tenant_id') if job else None) or q.get('tenant_id'))
    return render_template(
        'questionnaire_view.html',
        questionnaire=q,
        job=job,
        client=client,
        groups=q.get('questions') or QUESTIONNAIRE_QUESTIONS,
        answers=q.get('answers') or {},
        brand=brand,
    )


@app.route('/api/questionnaires/<questionnaire_id>/submit', methods=['POST'])
def api_questionnaire_submit(questionnaire_id):
    """Guarda las respuestas del cuestionario enviadas por el cliente."""
    q = store.get('questionnaires', questionnaire_id)
    if not q:
        return jsonify({'ok': False, 'error': 'Cuestionario no encontrado'}), 404
    data = request.get_json() or {}
    answers = data.get('answers') or {}
    q['answers'] = answers
    q['status'] = 'Respondido'
    q['answered_at'] = datetime.now().isoformat()
    store.upsert('questionnaires', q)
    return jsonify({'ok': True, 'questionnaire': q})


def _create_job_questionnaire(job, *, name=None, subject=None, body=None, questions=None,
                               status=None, template_id=None, send_email=True, host_url=None,
                               reuse_draft=False, questionnaire_id=None, auto_fire=False):
    """Crea (o reutiliza) el cuestionario de un job y opcionalmente lo manda.
    Extraido de la ruta para que tanto el modal manual (api_job_create_questionnaire)
    como el disparador automatico por fecha (_auto_fire_due_job_steps) compartan
    exactamente la misma logica de armado y entrega de correo.

    reuse_draft=True reutiliza el cuestionario en Draft que ya se pre-crea
    al convertir el job (ver _convert_lead_to_job) en vez de crear uno
    nuevo -- evita duplicados cuando el disparador automatico reintenta
    cada 6 horas mientras el correo no se termine de entregar de verdad.

    questionnaire_id apunta a un cuestionario EXACTO ya preparado (ver
    /api/jobs/<job_id>/questionnaires/prepare) -- el modal 'Send
    Questionnaire' lo usa para que el link real que Kevin ve en el preview
    sea EXACTAMENTE el mismo registro que se termina enviando, en vez de
    crear un cuestionario nuevo con otro id al momento de enviar.

    auto_fire=True (STAGE 2, agosto 2026): distingue al disparador
    automatico del modal manual para la clave de idempotencia del correo --
    el automatico necesita una clave ESTABLE (nunca debe reenviarse solo
    porque el scheduler paso de nuevo 6h despues mientras el pendiente
    anterior sigue sin revisar), el manual necesita una clave por dia (un
    reenvio deliberado manana es legitimo, dos clicks seguidos hoy no)."""
    import uuid
    lead = get_lead(job.get('lead_id', '')) if job.get('lead_id') else None
    client = get_client(job.get('client_id', '')) if job.get('client_id') else None
    host = (host_url or os.environ.get('APP_BASE_URL') or 'http://localhost:5000').rstrip('/')

    questionnaire = None
    if questionnaire_id:
        questionnaire = store.get('questionnaires', questionnaire_id)
        if questionnaire and questionnaire.get('job_id') != job.get('id'):
            questionnaire = None
    if questionnaire is None and reuse_draft:
        # No solo 'Draft': un reenvio (Sent -> Sent otra vez) tampoco debe
        # crear un registro nuevo mientras el cliente no lo haya
        # respondido -- ver api_lead_create_questionnaire para el mismo
        # fix del lado de leads.
        questionnaire = next(
            (q for q in store.list('questionnaires')
             if q.get('job_id') == job.get('id') and q.get('status') != 'Respondido'),
            None,
        )
    if questionnaire is None:
        questionnaire = {
            'id': 'questionnaire-' + uuid.uuid4().hex[:8],
            'lead_id': job.get('lead_id', ''),
            'client_id': job.get('client_id', ''),
            'job_id': job.get('id'),
            'name': name or 'Cuestionario de Bodas Generico',
            'template_name': 'Cuestionario de Bodas Generico',
            'questions': questions or QUESTIONNAIRE_QUESTIONS,
            'created': datetime.now().isoformat()[:10],
            'tenant_id': job.get('tenant_id') or get_current_tenant_id(),
        }
    else:
        if name:
            questionnaire['name'] = name
        if questions:
            questionnaire['questions'] = questions
    questionnaire['status'] = status or ('Sent' if send_email else 'Draft')
    store.upsert('questionnaires', questionnaire)

    questionnaire_path = f"/questionnaires/{questionnaire['id']}"
    questionnaire_url = host + questionnaire_path
    mail_id = None
    mail_warning = None
    if send_email:
        from src.mail_tracker import get_tracker
        to_email = _email_for(client=client, lead=lead)
        if to_email:
            rendered_subject = _render_message_template(
                subject or 'Cuestionario para tu boda',
                client=client, lead=lead, job=job,
            )
            rendered_body = _render_message_template(
                body or 'Hola %client_name%,\n\nTe comparto el cuestionario para preparar todos los detalles de tu boda:\n\n[LINK AL CUESTIONARIO]\n\nSaludos,\nKevin',
                client=client, lead=lead, job=job,
            )
            rendered_body = _inject_link(rendered_body, questionnaire_url,
                                placeholders=['[LINK AL CUESTIONARIO]',
                                              'Please view the questionnaire online by clicking here'],
                                fallback_label='Completa el cuestionario aqui')
            # STAGE 2 (agosto 2026): cola de aprobacion en vez de entrega
            # inmediata. Ver docstring de auto_fire mas arriba para la
            # eleccion de clave.
            idempotency_key = (
                f"jobquestionnaire:{questionnaire['id']}:notify"
                if auto_fire else
                f"jobquestionnaire:{questionnaire['id']}:notify:{date.today().isoformat()}"
            )
            entry = get_tracker().queue_email(
                to_email=to_email,
                subject=rendered_subject,
                body=rendered_body,
                template_id=template_id or 'tpl-cuestionario-prod',
                lead_id=job.get('lead_id'),
                job_id=job.get('id'),
                client_id=job.get('client_id') or None,
                tenant_id=job.get('tenant_id'),
                source='auto:job-questionnaire' if auto_fire else 'manual:job-questionnaire',
                idempotency_key=idempotency_key,
                attachments=[questionnaire['name']],
            )
            mail_id = entry['id']
            mail_warning = _mail_delivery_warning(entry)
        else:
            mail_warning = 'Este cliente no tiene email registrado -- el cuestionario se creo pero no se mando nada.'

    return {
        'questionnaire': questionnaire,
        'questionnaire_path': questionnaire_path,
        'questionnaire_url': questionnaire_url,
        'mail_id': mail_id,
        'mail_warning': mail_warning,
    }


@app.route('/api/jobs/<job_id>/questionnaires/prepare', methods=['POST'])
def api_job_prepare_questionnaire(job_id):
    """Kevin: 'quiero el link automatico puesto del cuestionario' -- igual
    que un contrato (que se crea ANTES de abrir el modal para poder mostrar
    su link real desde el primer momento), esto crea/reutiliza el
    cuestionario Draft del job SIN mandar nada, solo para que el modal
    'Send Questionnaire' tenga un id/link real que mostrar en el preview
    en vez del placeholder [LINK AL CUESTIONARIO] sin resolver."""
    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404

    result = _create_job_questionnaire(
        job, send_email=False, reuse_draft=True, host_url=request.url_root,
    )
    return jsonify({'ok': True, **result})


@app.route('/api/jobs/<job_id>/questionnaires', methods=['POST'])
def api_job_create_questionnaire(job_id):
    """Crea un cuestionario asociado al job y opcionalmente registra el email de envio."""
    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404

    data = request.get_json() or {}
    result = _create_job_questionnaire(
        job,
        name=data.get('name'),
        subject=data.get('subject'),
        body=data.get('body'),
        questions=data.get('questions'),
        status=data.get('status'),
        template_id=data.get('template_id'),
        send_email=data.get('send_email', True),
        host_url=request.url_root,
        questionnaire_id=data.get('questionnaire_id'),
        reuse_draft=True,
    )

    workflow = _complete_job_workflow_step(
        job,
        data.get('step_id'),
        result_message=f"Cuestionario creado: {result['questionnaire']['name']}"
    )
    return jsonify({
        'ok': True,
        'workflow': workflow,
        **result,
    })


UPLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'uploads')


@app.route('/api/jobs/<job_id>/files', methods=['POST'])
def api_job_create_file_record(job_id):
    """Sube un archivo real asociado al job (multipart) o registra metadata (JSON legacy)."""
    import uuid
    from werkzeug.utils import secure_filename

    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404

    file_id = 'file-' + uuid.uuid4().hex[:8]
    upload = request.files.get('file')

    if upload and upload.filename:
        safe_name = secure_filename(upload.filename) or 'archivo'
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        stored_name = f'{file_id}__{safe_name}'
        upload.save(os.path.join(UPLOADS_DIR, stored_name))
        size_mb = os.path.getsize(os.path.join(UPLOADS_DIR, stored_name)) / (1024 * 1024)
        name = upload.filename
        size = f'{size_mb:.2f} MB'
        stored = stored_name
    else:
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'ok': False, 'error': 'Selecciona un archivo'}), 400
        size = data.get('size') or '0.00 MB'
        stored = None

    file_record = {
        'id': file_id,
        'lead_id': job.get('lead_id', ''),
        'client_id': job.get('client_id', ''),
        'job_id': job_id,
        'name': name,
        'size': size,
        'status': 'Uploaded',
        'stored': stored,
        'created': datetime.now().isoformat()[:10],
        'tenant_id': job.get('tenant_id') or get_current_tenant_id(),
    }
    store.upsert('files', file_record)
    return jsonify({'ok': True, 'file': file_record})


@app.route('/files/<file_id>/download')
def file_download(file_id):
    """Descarga el archivo fisico subido al job."""
    from flask import send_file

    rec = store.get('files', file_id)
    if not rec:
        abort(404)
    stored = rec.get('stored')
    if not stored:
        return jsonify({'ok': False, 'error': 'Este registro no tiene archivo adjunto (solo metadata). Sube el archivo de nuevo.'}), 404
    path = os.path.join(UPLOADS_DIR, stored)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=rec.get('name') or stored)


@app.route('/api/jobs/<job_id>/history')
def api_job_history(job_id):
    """Historial real del workflow del job (para el modal History Log)."""
    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404
    # 27-ago-2026: antes escaneaba TODAS las instancias globales (de las 3
    # cuentas) por subject_id, sin ningun filtro de tenant -- un job
    # importado de Studio Ninja con id colisionado (mismo slug de pareja en
    # dos cuentas) exponia el historial completo -- nombres reales incluidos
    # -- de la otra cuenta en este modal.
    instancias_seguras = _workflow_instances_seguras(subject_type='job', subject_id=job_id)
    if job.get('lead_id'):
        instancias_seguras = instancias_seguras + _workflow_instances_seguras(
            subject_type='lead', subject_id=job['lead_id'])
    instance_ids = {i.id for i in instancias_seguras}
    history = [h for h in workflow_engine.history if h.get('instance_id') in instance_ids]
    return jsonify({'ok': True, 'history': history[-100:]})


@app.route('/api/jobs/<job_id>/workflow-task', methods=['POST'])
def api_job_workflow_task(job_id):
    """Kevin: 'poder agregar un shoot extra desde jobs, porque muchas veces se
    anotan bodas civiles, save the dates, trash the dress, welcome party' --
    'Extra Event'/'Appointment' antes se creaban al instante sin fecha propia
    (heredaban la fecha de la boda principal) y nunca aparecian en el
    calendario (se guardaban con type='job', pero /calendar solo muestra
    type='event' -- las de tipo job las regenera fresco desde boda_date de
    cada job, asi que una fecha de shoot extra quedaba huerfana y se perdia).
    Ahora se piden fecha/hora/ubicacion reales y el evento de calendario
    siempre se guarda como type='event' para que sí aparezca."""
    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404

    data = request.get_json() or {}
    task_type = data.get('type') or 'to-do'
    name = (data.get('name') or '').strip()
    default_names = {
        'to-do': 'New to-do',
        'automation': 'Automation',
        'extra-event': 'Evento extra',
        'appointment': 'Appointment',
    }
    if not name:
        name = default_names.get(task_type, 'Workflow task')

    needs_schedule = task_type in ('extra-event', 'appointment')
    start_date = (data.get('start_date') or '').strip()
    if needs_schedule and not start_date:
        return jsonify({'ok': False, 'error': 'La fecha es requerida'}), 400
    end_date = (data.get('end_date') or '').strip() or start_date
    start_time = (data.get('start_time') or '').strip()
    end_time = (data.get('end_time') or '').strip()
    location = (data.get('location') or '').strip()
    show_in_portal = bool(data.get('show_in_portal'))

    import uuid
    task = {
        'id': 'task-' + uuid.uuid4().hex[:8],
        'type': task_type,
        'name': name,
        'status': 'pending',
        'created': datetime.now().isoformat()[:10],
    }
    if needs_schedule:
        task.update({
            'start_date': start_date,
            'end_date': end_date,
            'start_time': start_time,
            'end_time': end_time,
            'location': location,
            'show_in_portal': show_in_portal,
        })

    manual_tasks = job.get('manual_workflow_tasks') or []
    manual_tasks.append(task)
    job['manual_workflow_tasks'] = manual_tasks
    job['next_task'] = name
    job['updated_at'] = datetime.now().isoformat()
    upsert_job(job)

    calendar_event = None
    if needs_schedule:
        event_id = 'evt-' + uuid.uuid4().hex[:8]
        calendar_event = {
            'id': event_id,
            'date': start_date,
            'end_date': end_date,
            'start_time': start_time,
            'end_time': end_time,
            'location': location,
            'type': 'event',
            'title': f"{name} - {job.get('nombre', 'Job')}",
            'job_id': job_id,
            'lead_id': job.get('lead_id'),
            'notes': data.get('notes') or f"Creado desde workflow: {task_type}",
            'created': datetime.now().isoformat()[:10],
        }
        store.upsert('calendar', calendar_event)
        task['calendar_event_id'] = event_id
        # task ya se guardo en manual_tasks arriba por referencia, pero
        # upsert_job(job) todavia no corrio -- lo dejamos correr una sola vez.
        upsert_job(job)

    return jsonify({'ok': True, 'task': task, 'calendar_event': calendar_event})


@app.route('/api/jobs/<job_id>/workflow-task/<task_id>/complete', methods=['POST'])
def api_job_workflow_task_complete(job_id, task_id):
    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404

    found = None
    for task in job.get('manual_workflow_tasks') or []:
        if task.get('id') == task_id:
            task['status'] = 'done'
            task['completed'] = datetime.now().isoformat()[:10]
            found = task
            break
    if not found:
        return jsonify({'ok': False, 'error': 'Tarea no encontrada'}), 404
    upsert_job(job)
    return jsonify({'ok': True, 'task': found})


@app.route('/api/jobs/<job_id>/workflow-task/<task_id>/update', methods=['POST'])
def api_job_workflow_task_update(job_id, task_id):
    """Edita un shoot extra / appointment ya creado (nombre, fecha, hora,
    ubicacion, visibilidad en el portal) y mantiene su evento de calendario
    sincronizado con los mismos datos."""
    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404

    manual_tasks = job.get('manual_workflow_tasks') or []
    task = next((t for t in manual_tasks if t.get('id') == task_id), None)
    if not task:
        return jsonify({'ok': False, 'error': 'Tarea no encontrada'}), 404

    data = request.get_json() or {}
    name = (data.get('name') or '').strip() or task.get('name')
    needs_schedule = task.get('type') in ('extra-event', 'appointment')
    start_date = (data.get('start_date') or '').strip()
    if needs_schedule and not start_date:
        return jsonify({'ok': False, 'error': 'La fecha es requerida'}), 400
    end_date = (data.get('end_date') or '').strip() or start_date
    start_time = (data.get('start_time') or '').strip()
    end_time = (data.get('end_time') or '').strip()
    location = (data.get('location') or '').strip()
    show_in_portal = bool(data.get('show_in_portal'))

    task['name'] = name
    if needs_schedule:
        task.update({
            'start_date': start_date, 'end_date': end_date,
            'start_time': start_time, 'end_time': end_time,
            'location': location, 'show_in_portal': show_in_portal,
        })
        event_id = task.get('calendar_event_id')
        if event_id and store.get('calendar', event_id):
            event = store.get('calendar', event_id)
            event.update({
                'date': start_date, 'end_date': end_date,
                'start_time': start_time, 'end_time': end_time,
                'location': location,
                'title': f"{name} - {job.get('nombre', 'Job')}",
            })
            store.upsert('calendar', event)
        elif not event_id:
            event_id = 'evt-' + uuid.uuid4().hex[:8]
            store.upsert('calendar', {
                'id': event_id, 'date': start_date, 'end_date': end_date,
                'start_time': start_time, 'end_time': end_time, 'location': location,
                'type': 'event', 'title': f"{name} - {job.get('nombre', 'Job')}",
                'job_id': job_id, 'lead_id': job.get('lead_id'),
                'created': datetime.now().isoformat()[:10],
            })
            task['calendar_event_id'] = event_id

    job['manual_workflow_tasks'] = manual_tasks
    upsert_job(job)
    return jsonify({'ok': True, 'task': task})


@app.route('/api/jobs/<job_id>/workflow-task/<task_id>/delete', methods=['POST'])
def api_job_workflow_task_delete(job_id, task_id):
    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404

    manual_tasks = job.get('manual_workflow_tasks') or []
    task = next((t for t in manual_tasks if t.get('id') == task_id), None)
    if not task:
        return jsonify({'ok': False, 'error': 'Tarea no encontrada'}), 404

    if task.get('calendar_event_id'):
        store.delete('calendar', task['calendar_event_id'])

    job['manual_workflow_tasks'] = [t for t in manual_tasks if t.get('id') != task_id]
    upsert_job(job)
    return jsonify({'ok': True})


@app.route('/api/jobs/<job_id>/workflow-task/<task_id>/toggle-portal', methods=['POST'])
def api_job_workflow_task_toggle_portal(job_id, task_id):
    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404

    manual_tasks = job.get('manual_workflow_tasks') or []
    task = next((t for t in manual_tasks if t.get('id') == task_id), None)
    if not task:
        return jsonify({'ok': False, 'error': 'Tarea no encontrada'}), 404

    task['show_in_portal'] = not task.get('show_in_portal')
    job['manual_workflow_tasks'] = manual_tasks
    upsert_job(job)
    return jsonify({'ok': True, 'show_in_portal': task['show_in_portal']})


def _get_or_create_job_workflow_instance(job):
    instances = _workflow_instances_seguras(subject_type='job', subject_id=job.get('id'))
    if instances:
        return instances[0]
    return workflow_engine.start_workflow(
        workflow=PRODUCTION_WORKFLOW(),
        subject_type='job',
        subject_id=job.get('id'),
        subject_name=job.get('nombre', 'Job'),
        trigger_event='job.created',
        auto_execute_first=False,
        tenant_id=job.get('tenant_id'),
    )


@app.route('/api/jobs/<job_id>/steps/<step_id>/skip', methods=['POST'])
def api_job_step_skip(job_id, step_id):
    """Kevin: 'una opcion por cada paso... es util por si no quieres mandar
    contrato por ejemplo'. Marca el step como SKIPPED solo para este job --
    get_due_steps() del engine ya ignora cualquier estado que no sea PENDING,
    asi que un step saltado nunca se dispara solo."""
    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404

    tmpl = PRODUCTION_WORKFLOW()
    step = next((s for s in tmpl.steps if s.id == step_id), None)
    if not step:
        return jsonify({'ok': False, 'error': 'Step no encontrado'}), 404

    instance = _get_or_create_job_workflow_instance(job)
    if instance.step_states.get(step_id) == StepStatus.DONE:
        return jsonify({'ok': False, 'error': 'Este step ya se completo, no se puede saltar'}), 400

    instance.step_states[step_id] = StepStatus.SKIPPED
    workflow_engine._log(instance, 'step.skipped', f'{step.name}: saltado manualmente')
    workflow_engine._save_to_storage()
    return jsonify({'ok': True, 'step': step.name})


@app.route('/api/jobs/<job_id>/steps/<step_id>/unskip', methods=['POST'])
def api_job_step_unskip(job_id, step_id):
    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404

    tmpl = PRODUCTION_WORKFLOW()
    step = next((s for s in tmpl.steps if s.id == step_id), None)
    if not step:
        return jsonify({'ok': False, 'error': 'Step no encontrado'}), 404

    instance = _get_or_create_job_workflow_instance(job)
    instance.step_states[step_id] = StepStatus.PENDING
    workflow_engine._log(instance, 'step.unskipped', f'{step.name}: vuelve a estar activo')
    workflow_engine._save_to_storage()
    return jsonify({'ok': True, 'step': step.name})


@app.route('/api/jobs/<job_id>/notes_produccion', methods=['POST'])
def api_job_notes_prod(job_id):
    data = request.json or request.form
    notas = data.get('notas', '')
    job = get_job(job_id)
    if job:
        job['notas_produccion'] = notas
        job['updated_at'] = datetime.now().isoformat()
        upsert_job(job)
        return jsonify({'ok': True, 'job_id': job_id})
    res = ns.update_job(job_id, notas_produccion=notas)
    return jsonify(res)


@app.route('/api/jobs/<job_id>/team', methods=['POST'])
def api_job_team(job_id):
    data = request.json or request.form
    fields = {}
    for k in ('primera_camara','segunda_camara','videografo_1','videografo_2','asistencia'):
        if k in data:
            v = data[k]
            if v == '' or v == 'None':
                v = 'NO APLICA'
            fields[k] = v
    if not fields:
        return jsonify({'ok': False, 'error': 'Sin cambios'}), 400
    job = get_job(job_id)
    if job:
        job.update(fields)
        job['updated_at'] = datetime.now().isoformat()
        upsert_job(job)
        return jsonify({'ok': True, 'job_id': job_id, 'updated': fields})
    res = ns.update_job(job_id, **fields)
    return jsonify(res)


@app.route('/api/jobs/<job_id>/confirm', methods=['POST'])
def api_job_confirm(job_id):
    data = request.json or request.form
    fields = {}
    for k in ('confirmado','confirmado_video','confirmado_video_2','confirmado_1'):
        if k in data:
            fields[k] = str(data[k]).lower() in ('1','true','on','yes')
    if not fields:
        return jsonify({'ok': False, 'error': 'Sin cambios'}), 400
    job = get_job(job_id)
    if job:
        job.update(fields)
        job['updated_at'] = datetime.now().isoformat()
        upsert_job(job)
        return jsonify({'ok': True, 'job_id': job_id, 'updated': fields})
    res = ns.update_job(job_id, **fields)
    return jsonify(res)


@app.route('/api/jobs/<job_id>/update', methods=['POST'])
def api_job_update(job_id):
    data = request.json or request.form
    job = get_job(job_id)
    if job:
        local_mapping = {
            'nombre': 'nombre',
            'name': 'nombre',
            'boda_date': 'boda_date',
            'fecha_evento': 'boda_date',
            'location': 'location',
            'lugar_evento': 'location',
            'package': 'package',
            'type': 'type',
            'status': 'status',
            'notas': 'notas',
            'notas_produccion': 'notas_produccion',
            'smart_file_url': 'smart_file_url',
        }
        numeric_mapping = {
            'price_total': 'price_total',
            'total_facturado': 'price_total',
            'price_paid': 'price_paid',
            'total_pagado': 'price_paid',
        }
        changed = {}
        for source, target in local_mapping.items():
            if source in data:
                value = data.get(source)
                if value == '':
                    value = None
                job[target] = value
                changed[target] = value
        for source, target in numeric_mapping.items():
            if source in data:
                value = data.get(source)
                if value in ('', None):
                    value = 0
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    return jsonify({'ok': False, 'error': f'{source} invalido'}), 400
                job[target] = value
                changed[target] = value
        if not changed:
            return jsonify({'ok': False, 'error': 'Sin cambios'}), 400
        job['updated_at'] = datetime.now().isoformat()
        upsert_job(job)
        return jsonify({'ok': True, 'job_id': job_id, 'updated': changed})

    fields = {}
    mapping = {
        'fecha_evento': 'fecha_evento',
        'lugar_evento': 'lugar_evento',
        'fecha_anticipo': 'fecha_anticipo',
        'fecha_contrato': 'fecha_contrato',
        'fecha_entrega_estimada': 'fecha_entrega_estimada',
        'total_pagado': 'total_pagado',
        'total_facturado': 'total_facturado',
        'smart_file_url': 'smart_file_url',
    }
    for k, target in mapping.items():
        if k in data:
            v = data[k]
            if v == '': v = None
            fields[target] = v
    if not fields:
        return jsonify({'ok': False, 'error': 'Sin cambios'}), 400
    res = ns.update_job(job_id, **fields)
    return jsonify(res)


JOB_CLIENT_ROLES = {'secondary': 'secondary_client_id', 'planner': 'planner_client_id'}


@app.route('/api/jobs/<job_id>/link-client', methods=['POST'])
def api_job_link_client(job_id):
    """Kevin: 'que se pueda agregar hasta 3 clientes... el principal, el
    secundario y el tercero seria la wedding planner, asi le mandaria los
    correos a los 3 y no se le pasa a nadie'. Vincula un cliente existente
    (por id) o crea uno nuevo en el momento (nombre/email/telefono) como
    contacto secundario o wedding planner de este job."""
    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404

    data = request.get_json() or {}
    role = data.get('role')
    # La validacion del rol la hace ALIAS_ROL_LEGACY/ROLES_JOB_CLIENT mas
    # abajo, que acepta tanto los alias viejos ('secondary'/'planner') como
    # los roles canonicos. La guardia anterior usaba JOB_CLIENT_ROLES, que
    # solo conocia esos dos alias, y rechazaba con 400 cualquier rol nuevo
    # ('principal', 'contacto', 'otro') ANTES de llegar al codigo canonico.

    client_id = data.get('client_id')
    if client_id:
        client = get_client(client_id)
        if not client:
            return jsonify({'ok': False, 'error': 'Cliente no encontrado'}), 404
    else:
        first_name = (data.get('first_name') or '').strip()
        if not first_name:
            return jsonify({'ok': False, 'error': 'Nombre requerido'}), 400
        import uuid as _uuid
        client_id = 'client-' + _uuid.uuid4().hex[:8]
        client = {
            'id': client_id,
            'first_name': first_name,
            'last_name': (data.get('last_name') or '').strip(),
            'email': (data.get('email') or '').strip(),
            'phone': (data.get('phone') or '').strip(),
            'estado': 'Activo',
            'tenant_id': get_current_tenant_id(),
            'created': datetime.now().isoformat()[:10],
        }
        store.upsert('clients', client)

    # Escribe en el modelo canonico job_clients (0..N), no en los 3 campos
    # fijos. `role` acepta tanto los alias viejos ('secondary'/'planner')
    # como los roles canonicos ('pareja', 'wedding_planner', 'contacto',
    # 'otro', 'principal'), asi el frontend existente sigue funcionando.
    rol_canonico = ALIAS_ROL_LEGACY.get(role, role)
    if rol_canonico not in ROLES_JOB_CLIENT:
        return jsonify({'ok': False, 'error': f'Rol invalido: {role}'}), 400

    relaciones = _job_client_relations(job)
    ya = next((r for r in relaciones if r['client_id'] == client_id), None)
    if ya:
        ya['role'] = rol_canonico  # cambiar el rol de alguien ya asociado
    else:
        relaciones.append({'client_id': client_id, 'role': rol_canonico})

    try:
        _set_job_clients(job, relaciones, tenant_id=job.get('tenant_id'))
    except TenantMismatchError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 403

    # Compatibilidad de LECTURA: se mantienen los campos viejos alineados
    # para que cualquier vista que todavia los lea no quede desfasada. La
    # fuente de verdad es job_clients.
    _sincronizar_campos_legacy(job)
    job['updated_at'] = datetime.now().isoformat()
    upsert_job(job)
    return jsonify({'ok': True, 'client': client,
                    'clientes': [{'id': c['id'], 'role': c['role'], 'nombre': c['nombre']}
                                 for c in _job_clients(job)]})


@app.route('/api/jobs/<job_id>/unlink-client', methods=['POST'])
def api_job_unlink_client(job_id):
    """No borra el cliente, solo lo desvincula de este job (rol secundario
    o wedding planner)."""
    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404

    data = request.get_json() or {}
    role = data.get('role')
    client_id = data.get('client_id')

    # Se puede quitar por client_id (preciso, y lo unico que sirve cuando
    # hay varios contactos con el mismo rol) o por rol (compatibilidad).
    relaciones = _job_client_relations(job)
    if client_id:
        quedan = [r for r in relaciones if r['client_id'] != client_id]
    else:
        rol_canonico = ALIAS_ROL_LEGACY.get(role, role)
        if rol_canonico not in ROLES_JOB_CLIENT:
            return jsonify({'ok': False, 'error': 'Indica client_id o un rol valido'}), 400
        quedan = [r for r in relaciones if r['role'] != rol_canonico]

    if len(quedan) == len(relaciones):
        return jsonify({'ok': False, 'error': 'Esa relacion no existe en este job'}), 404

    # SOLO se elimina la relacion. El cliente sigue existiendo con toda su
    # historia -- quitarlo de un job no puede borrar a la persona.
    _set_job_clients(job, quedan, tenant_id=job.get('tenant_id'))
    _sincronizar_campos_legacy(job)
    job['updated_at'] = datetime.now().isoformat()
    upsert_job(job)
    return jsonify({'ok': True,
                    'clientes': [{'id': c['id'], 'role': c['role'], 'nombre': c['nombre']}
                                 for c in _job_clients(job)]})


# ============================================================
# API - CREAR COTIZACIÓN / INVOICE desde JOB
# ============================================================

@app.route('/api/jobs/<job_id>/quotes', methods=['POST'])
def api_job_create_quote(job_id):
    """Crea una cotización en Notion COTIZ DB vinculada a este job."""
    import json
    data = request.json or request.form
    raw_items = data.get('items') or []
    if isinstance(raw_items, str):
        try:
            raw_items = json.loads(raw_items)
        except json.JSONDecodeError:
            raw_items = []
    if not isinstance(raw_items, list):
        raw_items = []
    raw_includes = data.get('incluye') or []
    if isinstance(raw_includes, str):
        raw_includes = [line.strip() for line in raw_includes.splitlines() if line.strip()]
    if not isinstance(raw_includes, list):
        raw_includes = []

    item_total = 0
    item_names = []
    item_includes = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_names.append(item.get('name') or item.get('paquete') or 'Package')
        try:
            item_total += float(item.get('price') or item.get('monto') or 0)
        except (TypeError, ValueError):
            pass
        includes = item.get('includes') or []
        if isinstance(includes, list):
            item_includes.extend(str(x) for x in includes if x)

    paquete = data.get('paquete') or (' + '.join(item_names) if item_names else '')
    monto = data.get('monto') or (str(item_total) if item_total else '')
    cuotas = data.get('cuotas', '2 (50% + 50%)')
    tipo_cotizacion = data.get('tipo_cotizacion') or data.get('quote_type') or 'fixed'

    if not paquete or not monto:
        return jsonify({'ok': False, 'error': 'paquete y monto requeridos'}), 400

    try:
        monto_f = float(monto)
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'monto inválido'}), 400

    job_local = get_job(job_id)
    if job_local:
        import uuid
        cuotas_num = 2
        if str(cuotas).startswith('1 '):
            cuotas_num = 1
        elif str(cuotas).startswith('3 '):
            cuotas_num = 3
        elif str(cuotas).startswith('4 '):
            cuotas_num = 4
        quote_id = 'quote-' + uuid.uuid4().hex[:8]
        quote = {
            'id': quote_id,
            'lead_id': job_local.get('lead_id') or '',
            'job_id': job_id,
            'paquete_nombre': paquete,
            'precio_total': monto_f,
            'plan_pago': cuotas_num,
            'tipo_cotizacion': tipo_cotizacion,
            'cuota_monto': round(monto_f / cuotas_num, 2) if cuotas_num else monto_f,
            'notas': data.get('notas') or data.get('introduction') or '',
            'items': raw_items,
            'incluye': item_includes or raw_includes,
            'status': data.get('status') or 'Pendiente',
            'created': date.today().isoformat(),
            'sent_at': date.today().isoformat(),
            'tenant_id': job_local.get('tenant_id') or get_current_tenant_id(),
        }
        _assign_quote_number(quote)
        store.upsert('quotes', quote)
        return jsonify({
            'ok': True,
            'id': quote_id,
            'quote_url': f'/quotes/{quote_id}',
            'message': 'Quote creado localmente',
        })

    # Anticipo según cuotas
    anticipo_pct = {'1 (total al reservar)': 1.0, '2 (50% + 50%)': 0.5, '3 (33% + 33% + 34%)': 0.33, '4 (25% c/u)': 0.25}.get(cuotas, 0.5)
    anticipo = int(monto_f * anticipo_pct)

    # Calcular token y links
    import secrets
    token = secrets.token_urlsafe(20).replace('-', 'a').replace('_', 'b')[:20]
    smart_url = f'https://norkevinphoto.com/portal/?t={token}&c={job_id[:8]}'
    recurrente_url = f'https://app.recurrente.com/checkout/demo-{secrets.token_urlsafe(12).replace("-","").replace("_","")[:14]}'

    # Obtener job + cliente
    try:
        job = ns.get_job_full(job_id)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Job no encontrado: {e}'}), 404

    cliente_id = (job.get('cliente') or {}).get('id')
    if not cliente_id:
        return jsonify({'ok': False, 'error': 'Job sin cliente asignado'}), 400

    title = (job.get('BODA') or 'Cotización')[:50] + ' — ' + paquete

    from datetime import date as _date
    props = {
        'Cotización': {'title': [{'type': 'text', 'text': {'content': title}}]},
        'Estado': {'status': {'name': 'Enviada'}},
        'Paquete': {'select': {'name': paquete}},
        'Cliente': {'relation': [{'id': cliente_id}]},
        'Job': {'relation': [{'id': job_id}]},
        'Monto total (Q)': {'number': monto_f},
        'Anticipo (Q)': {'number': float(anticipo)},
        'Cantidad de cuotas': {'select': {'name': cuotas}},
        'Link Smart File': {'url': smart_url},
        'Link Recurrente anticipo': {'url': recurrente_url},
        'Fecha de envío': {'date': {'start': _date.today().isoformat()}},
    }

    try:
        r = ns.client().pages.create(parent={'data_source_id': ns.DS['COTIZ']}, properties=props)
        return jsonify({'ok': True, 'id': r['id'], 'smart_url': smart_url, 'recurrente_url': recurrente_url})
    except Exception as e:
        logger.error(f'Error creando cotización: {e}')
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/jobs/<job_id>/invoices', methods=['POST'])
def api_job_create_invoice(job_id):
    """Crea un invoice en Notion COTIZ DB (estado Enviada, monto custom)."""
    data = request.json or request.form
    concepto = data.get('concepto') or 'Invoice'
    monto = data.get('monto')

    if not monto:
        return jsonify({'ok': False, 'error': 'monto requerido'}), 400

    try:
        monto_f = float(monto)
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'monto inválido'}), 400

    job_local = get_job(job_id)
    if job_local:
        import uuid
        invoice_id = 'INV-' + uuid.uuid4().hex[:6].upper()
        pay_id = 'pay-' + uuid.uuid4().hex[:8]
        payment = {
            'id': pay_id,
            'invoice_id': invoice_id,
            'client_id': job_local.get('client_id') or '',
            'job_id': job_id,
            'concepto': concepto,
            'amount': monto_f,
            'due_date': data.get('due_date') or date.today().isoformat(),
            'status': 'Pendiente',
            'sent_at': date.today().isoformat(),
            'tenant_id': job_local.get('tenant_id') or get_current_tenant_id(),
        }
        store.upsert('payments', payment)
        return jsonify({
            'ok': True,
            'id': pay_id,
            'invoice_id': invoice_id,
            'pdf_url': f'/invoices/{invoice_id}/pdf',
            'message': 'Invoice creado localmente',
        })

    import secrets
    token = secrets.token_urlsafe(20).replace('-', 'a').replace('_', 'b')[:20]
    recurrente_url = f'https://app.recurrente.com/checkout/inv-{secrets.token_urlsafe(12).replace("-","").replace("_","")[:14]}'

    try:
        job = ns.get_job_full(job_id)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Job no encontrado: {e}'}), 404

    cliente_id = (job.get('cliente') or {}).get('id')
    if not cliente_id:
        return jsonify({'ok': False, 'error': 'Job sin cliente asignado'}), 400

    from datetime import date as _date
    title = (job.get('BODA') or 'Invoice')[:50] + ' — ' + concepto
    props = {
        'Cotización': {'title': [{'type': 'text', 'text': {'content': title}}]},
        'Estado': {'status': {'name': 'Enviada'}},
        'Cliente': {'relation': [{'id': cliente_id}]},
        'Job': {'relation': [{'id': job_id}]},
        'Monto total (Q)': {'number': monto_f},
        'Anticipo (Q)': {'number': monto_f},
        'Cantidad de cuotas': {'select': {'name': '1 (total al reservar)'}},
        'Link Recurrente anticipo': {'url': recurrente_url},
        'Fecha de envío': {'date': {'start': _date.today().isoformat()}},
        'Notas': {'rich_text': [{'type': 'text', 'text': {'content': concepto}}]},
    }
    try:
        r = ns.client().pages.create(parent={'data_source_id': ns.DS['COTIZ']}, properties=props)
        return jsonify({'ok': True, 'id': r['id'], 'recurrente_url': recurrente_url})
    except Exception as e:
        logger.error(f'Error creando invoice: {e}')
        return jsonify({'ok': False, 'error': str(e)}), 500


# ============================================================
# API - PAYMENTS
# ============================================================

# NOTA: la ruta POST /api/payments/<id>/pay vive en api_payment_mark_paid (mas arriba).
# Aqui existia un duplicado muerto (api_pago_pay) que Flask nunca despachaba; eliminado.


def _client_facing_invoice_url(host, client, invoice_id, *, emitir_token=False, tenant_id=None):
    """URL que le mandamos al CLIENTE para ver/pagar su factura.

    /invoices/<id> es la vista interna (admin): tiene boton para editar fecha
    de vencimiento y generar links de pago con nuestra API key de Recurrente.
    El cliente nunca debe aterrizar ahi.

    Sistema de documentos (29-ago-2026): ahora existe la factura web publica
    en /i/<token> -- un documento real, con la misma piel que la cotizacion,
    en vez de un PDF suelto o el portal completo.

    emitir_token=True SOLO cuando se esta componiendo el correo que le lleva
    el enlace al cliente. Emitir rota el token (el anterior deja de servir,
    porque en la base solo vive el hash y no se puede recuperar el viejo),
    asi que hacerlo desde un redirect post-pago romperia el enlace que el
    cliente ya tiene en su bandeja. Por eso esta apagado por default y los
    llamadores que solo necesitan "a donde mando al cliente ahora" siguen
    cayendo al portal, igual que antes de este cambio."""
    if emitir_token:
        token = _emitir_token_de_factura(invoice_id, tenant_id, rotar=True)
        if token:
            return host + f'/i/{token}'
    if client and client.get('id'):
        return host + f"/portal/{client['id']}#invoices"
    return host + f"/invoices/{invoice_id}"


def _invoice_send_email_text(pay, client, job, lead, host, *, emitir_token=False):
    """Texto del correo de factura.

    emitir_token: solo el ENVIO real lo pone en True. La vista previa no
    puede emitir -- emitir rota el token y dejaria muerto el enlace que el
    cliente ya tiene en su bandeja solo por abrir la previsualizacion. Sin
    emitir, la previa muestra el marcador [[INVOICE_LINK]], que api_pago_send
    reemplaza por la URL real recien emitida (mismo patron ya probado con
    [[QUOTE_LINK]] en api_quote_send)."""
    invoice_id = pay.get('invoice_id') or pay['id']
    invoice_url = _client_facing_invoice_url(
        host, client, invoice_id, emitir_token=emitir_token,
        tenant_id=(job or {}).get('tenant_id') or pay.get('tenant_id')) if emitir_token else '[[INVOICE_LINK]]'
    name = _client_name(client=client, lead=lead, job=job)
    amount = float(pay.get('amount') or 0)
    # Antes hardcodeado a 'ASTRAL WEDDINGS' sin importar el tenant del job --
    # mismo patron del incidente del 16 de agosto, aca en la firma de la
    # factura. Resuelto via la marca canonica del tenant dueno del pago/job.
    empresa = _brand_display_name_for_tenant((job or {}).get('tenant_id') or pay.get('tenant_id'))

    subject = f'Factura {invoice_id} - {empresa}'
    body = (
        f"Hola {name},\n\n"
        f"Tu factura por Q{amount:,.2f} ({pay.get('concepto') or 'Pago'}) esta lista.\n"
        f"Puedes verla y pagarla en este enlace:\n{invoice_url}\n\n"
        f"Saludos,\n{empresa}"
    )
    return subject, body, invoice_url


@app.route('/api/payments/<pago_id>/send-preview')
def api_pago_send_preview(pago_id):
    """Vista previa (sin enviar nada) del correo de factura/invoice para
    este pago -- Kevin pidio ver que se va a mandar antes de mandarlo,
    igual que ya existe para los recordatorios de pago."""
    pay = store.get('payments', pago_id) or next((p for p in store.list('payments') if p.get('invoice_id') == pago_id), None)
    if not pay:
        return jsonify({'ok': False, 'error': 'Pago no encontrado'}), 404

    job = get_job(pay.get('job_id', '')) if pay.get('job_id') else None
    client = get_client(pay.get('client_id', '')) if pay.get('client_id') else None
    lead = get_lead(job.get('lead_id', '')) if (job and job.get('lead_id')) else None
    to_email = _email_for(client=client, lead=lead)
    host = request.host_url.rstrip('/')
    subject, body, _ = _invoice_send_email_text(pay, client, job, lead, host)

    return jsonify({'ok': True, 'to_email': to_email or '', 'subject': subject, 'body': body})


@app.route('/api/payments/<pago_id>/send', methods=['POST'])
def api_pago_send(pago_id):
    """Envia la factura/invoice por email al cliente, no solo marca sent_at."""
    from src.mail_tracker import get_tracker

    pay = store.get('payments', pago_id) or next((p for p in store.list('payments') if p.get('invoice_id') == pago_id), None)
    if not pay:
        return jsonify({'ok': False, 'error': 'Pago no encontrado'}), 404

    job = get_job(pay.get('job_id', '')) if pay.get('job_id') else None
    client = get_client(pay.get('client_id', '')) if pay.get('client_id') else None
    lead = get_lead(job.get('lead_id', '')) if (job and job.get('lead_id')) else None
    host = request.host_url.rstrip('/')
    # Aca SI se emite el token: este es el momento en que el enlace sale
    # hacia el cliente. Ver _invoice_send_email_text.
    default_subject, default_body, invoice_url = _invoice_send_email_text(
        pay, client, job, lead, host, emitir_token=True)

    # Si Kevin edito el "Para/Asunto/Mensaje" en la vista previa, respetamos
    # eso tal cual -- si no mando nada, generamos el correo por defecto.
    data = request.get_json(silent=True) or {}
    to_email = (data.get('to_email') or '').strip() or _email_for(client=client, lead=lead)
    if not to_email:
        return jsonify({'ok': False, 'error': 'Este pago no tiene email de cliente'}), 400
    subject = (data.get('subject') or '').strip() or default_subject
    body = (data.get('body') or '').strip() or default_body
    # El cuerpo editado en la previa trae el marcador; se cambia por el
    # enlace real. Sin esto el cliente recibiria literalmente
    # "[[INVOICE_LINK]]" en vez de su factura.
    body = body.replace('[[INVOICE_LINK]]', invoice_url)

    # STAGE 2 (agosto 2026): cola de aprobacion en vez de entrega inmediata.
    mail = get_tracker().queue_email(
        to_email=to_email,
        subject=subject,
        body=body,
        lead_id=job.get('lead_id') if job else None,
        job_id=pay.get('job_id'),
        client_id=pay.get('client_id') or None,
        tenant_id=pay.get('tenant_id') or (job or {}).get('tenant_id'),
        source='manual:invoice-send',
        idempotency_key=f"pago:{pay['id']}:invoice:{_idempotency_minute_bucket()}",
    )

    pay['sent_at'] = datetime.now().isoformat()
    pay['last_action'] = 'sent'
    store.upsert('payments', pay)

    return jsonify({
        'ok': True,
        'payment_id': pay['id'],
        'sent_at': pay['sent_at'],
        'mail_id': mail.get('id'),
        'delivery_status': mail.get('status'),
        'email': to_email,
        'invoice_url': invoice_url,
        'mail_warning': _mail_delivery_warning(mail),
        'message': (f'Factura puesta en cola de aprobacion para {to_email} (revisa /emails)'
                    if mail.get('status') == 'pending'
                    else f'Factura NO se pudo poner en cola: {mail.get("blocked_reason") or "motivo no especificado"}'),
    })


@app.route('/api/payments/<pago_id>/payment-link', methods=['POST'])
def api_pago_create_payment_link(pago_id):
    """Genera un link de pago real con Recurrente para este pago/cuota."""
    from src import recurrente

    pay = store.get('payments', pago_id) or next((p for p in store.list('payments') if p.get('invoice_id') == pago_id), None)
    if not pay:
        return jsonify({'ok': False, 'error': 'Pago no encontrado'}), 404

    if not recurrente.is_configured(tenant_id=pay.get('tenant_id')):
        return jsonify({'ok': False, 'error': 'Recurrente no esta conectado para esta cuenta. Conectalo en Settings.'}), 400

    # 'amount' de una cuota pendiente YA es su saldo actual (se ajusta con
    # cada abono directo o credito recibido) -- cobrar eso directamente.
    amount = round(float(pay.get('amount') or 0), 2)
    if amount <= 0:
        return jsonify({'ok': False, 'error': 'El pago no tiene un monto valido'}), 400

    host = request.host_url.rstrip('/')
    invoice_id = pay.get('invoice_id') or pay['id']
    concepto = pay.get('concepto') or f'Pago {invoice_id}'
    client = get_client(pay.get('client_id', '')) if pay.get('client_id') else None
    redirect_url = _client_facing_invoice_url(host, client, invoice_id)

    result = recurrente.create_checkout(
        name=concepto,
        amount_in_cents=round(amount * 100),
        currency='GTQ',
        success_url=redirect_url,
        cancel_url=redirect_url,
        tenant_id=pay.get('tenant_id'),
    )
    if not result.get('ok'):
        return jsonify({'ok': False, 'error': result.get('error')}), 502

    pay['payment_link_url'] = result.get('checkout_url')
    pay['payment_link_id'] = result.get('id')
    pay['payment_link_created_at'] = datetime.now().isoformat()
    store.upsert('payments', pay)

    return jsonify({
        'ok': True,
        'payment_id': pay['id'],
        'payment_link_url': pay['payment_link_url'],
        'message': 'Link de pago generado',
    })


REMINDER_WINDOW_DAYS_AHEAD = 7   # avisar hasta 7 dias antes del vencimiento
REMINDER_WINDOW_DAYS_OVERDUE = 30  # seguir avisando hasta 30 dias despues de vencido
REMINDER_MIN_GAP_DAYS = 5   # no volver a avisar antes de que pasen estos dias


def _payment_reminder_email_text(pay, client, job, payment_link):
    """Construye subject+body del recordatorio de pago. Compartido entre el
    envio automatico (check_and_send_payment_reminders) y la vista previa /
    envio manual desde 'Generar link de pago', para que sea EXACTAMENTE el
    mismo correo en ambos casos."""
    # tenant_id explicito: esta funcion la llama tambien el scheduler
    # automatico (check_and_send_payment_reminders), que no tiene sesion
    # Flask -- sin esto, get_settings()/_brand_display_name_for_tenant()
    # caerian al fallback global (ver comentario de get_settings()).
    job_tenant_id = (job or {}).get('tenant_id') or pay.get('tenant_id')
    settings_dict = get_settings(tenant_id=job_tenant_id)
    empresa = _brand_display_name_for_tenant(job_tenant_id)
    bank_info = (settings_dict.get('company') or {}).get('bank_info') or ''
    name = _client_name(client=client, lead=None, job=job)
    amount = round(float(pay.get('amount') or 0), 2)
    due_date_str = pay.get('due_date') or ''
    when_text = 'sin fecha de vencimiento definida'
    if due_date_str:
        try:
            due = datetime.strptime(due_date_str, '%Y-%m-%d').date()
            days_until = (due - date.today()).days
            if days_until == 0:
                when_text = 'vence hoy'
            elif days_until > 0:
                when_text = f'vence en {days_until} dia(s)'
            else:
                when_text = f'vencio hace {-days_until} dia(s)'
        except ValueError:
            pass
    subject = f'Recordatorio de pago - {when_text} - {empresa}'

    raw_options = []
    if bank_info.strip():
        raw_options.append(f"Transferencia bancaria:\n{bank_info.strip()}")
    else:
        raw_options.append("Transferencia bancaria: contactame para coordinar los datos.")
    if payment_link:
        raw_options.append(f"Pago en linea con tarjeta:\n{payment_link}")
    options = [f"{i}. {opt}" for i, opt in enumerate(raw_options, start=1)]

    body = (
        f"Hola {name},\n\n"
        f"Te escribo para recordarte tu proximo pago con {empresa}.\n\n"
        f"Monto: Q{amount:,.2f}\n"
        f"Vence: {due_date_str or 'Por definir'} ({when_text})\n\n"
        f"Opciones de pago:\n\n" + "\n\n".join(options) + "\n\n"
        f"Cualquier duda, avisame.\n\nSaludos,\n{empresa}"
    )
    return subject, body


@app.route('/api/payments/<pago_id>/reminder-preview')
def api_payment_reminder_preview(pago_id):
    """Vista previa (sin enviar nada) del correo de recordatorio para este
    pago -- se usa justo despues de 'Generar link de pago' para que Kevin
    vea exactamente que se le mandaria al cliente antes de mandarlo."""
    pay = store.get('payments', pago_id) or next((p for p in store.list('payments') if p.get('invoice_id') == pago_id), None)
    if not pay:
        return jsonify({'ok': False, 'error': 'Pago no encontrado'}), 404

    job = get_job(pay.get('job_id', '')) if pay.get('job_id') else None
    client = get_client(pay.get('client_id', '')) if pay.get('client_id') else None
    to_email = _email_for(client=client, lead=None)
    subject, body = _payment_reminder_email_text(pay, client, job, pay.get('payment_link_url'))

    return jsonify({'ok': True, 'to_email': to_email or '', 'subject': subject, 'body': body})


@app.route('/api/payments/<pago_id>/send-reminder', methods=['POST'])
def api_payment_send_reminder(pago_id):
    """Manda el recordatorio de pago para ESTE pago ahora mismo, sin esperar
    al scheduler automatico -- lo dispara Kevin desde la vista previa."""
    from src.mail_tracker import get_tracker

    pay = store.get('payments', pago_id) or next((p for p in store.list('payments') if p.get('invoice_id') == pago_id), None)
    if not pay:
        return jsonify({'ok': False, 'error': 'Pago no encontrado'}), 404

    job = get_job(pay.get('job_id', '')) if pay.get('job_id') else None
    client = get_client(pay.get('client_id', '')) if pay.get('client_id') else None

    # Si Kevin edito el "Para/Asunto/Mensaje" en la vista previa, respetamos
    # eso tal cual -- si no mando nada (o vacio), generamos el correo por defecto.
    data = request.get_json(silent=True) or {}
    to_email = (data.get('to_email') or '').strip() or _email_for(client=client, lead=None)
    if not to_email:
        return jsonify({'ok': False, 'error': 'Este pago no tiene email de cliente'}), 400

    default_subject, default_body = _payment_reminder_email_text(pay, client, job, pay.get('payment_link_url'))
    subject = (data.get('subject') or '').strip() or default_subject
    body = (data.get('body') or '').strip() or default_body

    # STAGE 2 (agosto 2026): misma familia de clave que el recordatorio
    # automatico (check_and_send_payment_reminders) -- si el scheduler ya
    # encolo el recordatorio de hoy para este pago, este boton manual no
    # debe crear un segundo pendiente identico, solo mostrar el que ya
    # existe.
    mail = get_tracker().queue_email(
        to_email=to_email,
        subject=subject,
        body=body,
        lead_id=job.get('lead_id') if job else None,
        job_id=pay.get('job_id'),
        client_id=pay.get('client_id') or None,
        tenant_id=pay.get('tenant_id') or (job or {}).get('tenant_id'),
        source='manual:payment-reminder',
        idempotency_key=f"pago:{pay['id']}:reminder:{date.today().isoformat()}",
    )
    pay['reminder_sent_at'] = datetime.now().isoformat()
    pay['last_action'] = 'sent'
    store.upsert('payments', pay)

    return jsonify({
        'ok': True,
        'mail_id': mail.get('id'),
        'delivery_status': mail.get('status'),
        'mail_warning': _mail_delivery_warning(mail),
        'message': (f'Recordatorio puesto en cola de aprobacion para {to_email} (revisa /emails)'
                    if mail.get('status') == 'pending'
                    else f'Recordatorio NO se pudo poner en cola: {mail.get("blocked_reason") or "motivo no especificado"}'),
    })


def check_and_send_payment_reminders(host_url=None):
    """Revisa todos los pagos pendientes/atrasados y manda un recordatorio por
    email (con opciones de transferencia, efectivo y link de Recurrente) a los
    que estan por vencer o ya vencieron, sin repetir el aviso muy seguido."""
    from src.mail_tracker import get_tracker
    from src import recurrente

    host = (host_url or os.environ.get('APP_BASE_URL') or 'http://localhost:5000').rstrip('/')
    today = date.today()

    sent = []
    for pay in store.list('payments'):
        if pay.get('status') not in ('Pendiente', 'Late'):
            continue
        if pay.get('tipo') == 'team_payment':
            continue
        due_date_str = pay.get('due_date')
        if not due_date_str:
            continue
        try:
            due = datetime.strptime(due_date_str, '%Y-%m-%d').date()
        except ValueError:
            continue

        days_until = (due - today).days
        if days_until > REMINDER_WINDOW_DAYS_AHEAD or days_until < -REMINDER_WINDOW_DAYS_OVERDUE:
            continue

        last_sent = pay.get('reminder_sent_at')
        if last_sent:
            try:
                last_sent_date = datetime.fromisoformat(last_sent).date()
                if (today - last_sent_date).days < REMINDER_MIN_GAP_DAYS:
                    continue
            except ValueError:
                pass

        job = get_job(pay.get('job_id', '')) if pay.get('job_id') else None
        client = get_client(pay.get('client_id', '')) if pay.get('client_id') else None
        to_email = _email_for(client=client, lead=None)
        if not to_email:
            continue

        amount = round(float(pay.get('amount') or 0), 2)
        invoice_id = pay.get('invoice_id') or pay['id']

        payment_link = pay.get('payment_link_url')
        if not payment_link and recurrente.is_configured(tenant_id=pay.get('tenant_id')) and amount > 0:
            redirect_url = _client_facing_invoice_url(host, client, invoice_id)
            result = recurrente.create_checkout(
                name=pay.get('concepto') or invoice_id,
                amount_in_cents=round(amount * 100),
                currency='GTQ',
                success_url=redirect_url,
                cancel_url=redirect_url,
                tenant_id=pay.get('tenant_id'),
            )
            if result.get('ok'):
                payment_link = result.get('checkout_url')
                pay['payment_link_url'] = payment_link
                pay['payment_link_id'] = result.get('id')

        subject, body = _payment_reminder_email_text(pay, client, job, payment_link)

        # STAGE 2 (agosto 2026): cola de aprobacion en vez de entrega
        # inmediata. Clave estable por pago+dia: comparte familia con el
        # boton manual "enviar recordatorio ahora" para no duplicar el
        # mismo aviso si ambos caminos se disparan el mismo dia.
        get_tracker().queue_email(
            to_email=to_email,
            subject=subject,
            body=body,
            lead_id=job.get('lead_id') if job else None,
            job_id=pay.get('job_id'),
            client_id=pay.get('client_id') or None,
            tenant_id=pay.get('tenant_id'),
            source='auto:payment-reminder',
            idempotency_key=f"pago:{pay['id']}:reminder:{today.isoformat()}",
        )
        pay['reminder_sent_at'] = datetime.now().isoformat()
        store.upsert('payments', pay)
        sent.append(pay['id'])

    return sent


@app.route('/api/payments/check-reminders', methods=['POST'])
def api_payments_check_reminders():
    """Dispara manualmente la revision de recordatorios de pago (la misma
    logica corre sola cada dia en segundo plano)."""
    sent = check_and_send_payment_reminders(host_url=request.host_url)
    return jsonify({'ok': True, 'sent': sent, 'count': len(sent)})


@app.route('/api/payments/<pago_id>/status', methods=['POST'])
def api_pago_status(pago_id):
    data = request.json or request.form
    nuevo = data.get('estado_pago') or data.get('status')
    if not nuevo:
        return jsonify({'ok': False, 'error': 'estado_pago requerido'}), 400
    pay = store.get('payments', pago_id) or next((p for p in store.list('payments') if p.get('invoice_id') == pago_id), None)
    if pay:
        pay['status'] = nuevo
        pay['updated_at'] = datetime.now().isoformat()
        store.upsert('payments', pay)
        return jsonify({'ok': True, 'payment_id': pay['id'], 'status': nuevo})
    res = ns.update_pago(pago_id, estado_pago=nuevo)
    return jsonify(res)


@app.route('/api/payments/<pago_id>/update', methods=['POST'])
def api_pago_update(pago_id):
    data = request.json or request.form
    pay = store.get('payments', pago_id) or next((p for p in store.list('payments') if p.get('invoice_id') == pago_id), None)
    if pay:
        local_fields = {}
        if 'monto_acordado' in data or 'amount' in data:
            try:
                local_fields['amount'] = float(data.get('monto_acordado') or data.get('amount') or 0)
            except (TypeError, ValueError):
                return jsonify({'ok': False, 'error': 'monto invalido'}), 400
        if 'fecha_pago' in data:
            local_fields['fecha_pago'] = data.get('fecha_pago') or None
        if 'due_date' in data:
            local_fields['due_date'] = data.get('due_date') or None
        if 'comprobante_url' in data:
            local_fields['comprobante_url'] = data.get('comprobante_url')
        if 'evento' in data:
            local_fields['concepto'] = data.get('evento')
        if 'status' in data or 'estado_pago' in data:
            local_fields['status'] = data.get('status') or data.get('estado_pago')
        if not local_fields:
            return jsonify({'ok': False, 'error': 'Sin cambios'}), 400
        pay.update(local_fields)
        pay['updated_at'] = datetime.now().isoformat()
        store.upsert('payments', pay)
        return jsonify({'ok': True, 'payment_id': pay['id'], 'updated': local_fields})

    fields = {}
    if 'monto_acordado' in data:
        try: fields['monto_acordado'] = float(data['monto_acordado'])
        except: pass
    if 'fecha_pago' in data:
        fields['fecha_pago'] = data['fecha_pago'] or None
    if 'comprobante_url' in data:
        fields['comprobante_url'] = data['comprobante_url']
    if 'evento' in data:
        fields['evento'] = data['evento']
    if not fields:
        return jsonify({'ok': False, 'error': 'Sin cambios'}), 400
    res = ns.update_pago(pago_id, **fields)
    return jsonify(res)


@app.route('/api/leads/<lead_id>/accept_shoot', methods=['POST'])
def api_lead_accept_shoot(lead_id):
    """
    Workflow Leads → Jobs: cuando un lead acepta shoot.
    Crea automáticamente un Cliente + Job en Notion, marca el lead como Convertido.
    """
    try:
        page = ns.client().pages.retrieve(page_id=lead_id)
        lead = ns._normalize_props(page.get('properties', {}))
        lead['id'] = lead_id

        if lead.get('Estado') == 'Convertido':
            return jsonify({'ok': False, 'error': 'Lead ya está convertido'}), 400

        # 1. Crear Cliente
        nombre_cliente = (lead.get('Nombre') or 'Sin nombre').replace('[DEMO]', '').strip()
        props_cliente = {
            'Nombre': {'title': [{'type': 'text', 'text': {'content': nombre_cliente}}]},
            'Estado': {'status': {'name': 'Activo'}},
        }
        if lead.get('Teléfono'):
            props_cliente['Teléfono'] = {'phone_number': lead['Teléfono']}
        if lead.get('Email'):
            props_cliente['Email'] = {'email': lead['Email']}
        if lead.get('Fuente'):
            props_cliente['Fuente'] = {'select': {'name': lead['Fuente']}}
        if lead.get('Fecha tentativa del evento'):
            props_cliente['Fecha primer contacto'] = {'date': {'start': lead['Fecha tentativa del evento']}}
        if lead.get('Notas'):
            props_cliente['Notas'] = {'rich_text': [{'type': 'text', 'text': {'content': 'Cliente creado automáticamente desde lead.\n\n' + lead['Notas']}}]}

        r_cliente = ns.client().pages.create(parent={'data_source_id': ns.DS['CLIENTES']}, properties=props_cliente)
        cliente_id = r_cliente['id']

        # 2. Crear Job
        nombre_boda = (lead.get('Nombre') or 'Boda').replace('[DEMO]', '').strip()
        props_job = {
            'BODA': {'title': [{'type': 'text', 'text': {'content': nombre_boda}}]},
            'Estado': {'status': {'name': 'Cotizando'}},
            # Push a Notion (legado): la empresa tambien sale del tenant
            # activo, no de un string fijo.
            'EMPRESA': {'select': {'name': _brand_display_name_for_tenant(get_current_tenant_id())}},
            'Tipo de evento': {'select': {'name': lead.get('Tipo de evento') or 'Boda'}},
            'Cliente': {'relation': [{'id': cliente_id}]},
        }
        if lead.get('Fecha tentativa del evento'):
            props_job['Fecha del evento'] = {'date': {'start': lead['Fecha tentativa del evento']}}
        if lead.get('Locación tentativa'):
            props_job['Lugar de evento'] = {'rich_text': [{'type': 'text', 'text': {'content': lead['Locación tentativa']}}]}

        presupuesto = lead.get('Presupuesto estimado') or ''
        if 'Mas de' in presupuesto: monto = 35500
        elif 'Q20000' in presupuesto: monto = 23500
        elif 'Q10000' in presupuesto: monto = 15500
        elif 'Q5000' in presupuesto: monto = 8500
        else: monto = 20000
        props_job['Total facturado al cliente (Q)'] = {'number': float(monto)}

        nota = 'Job creado automáticamente al aceptar shoot desde lead.\n\n'
        nota += 'Lead original:\n' + (lead.get('Notas') or '')
        props_job['NOTAS'] = {'rich_text': [{'type': 'text', 'text': {'content': nota[:1900]}}]}

        r_job = ns.client().pages.create(parent={'data_source_id': ns.DS['JOBS_BODAS']}, properties=props_job)
        job_id = r_job['id']

        # 3. Marcar Lead como Convertido + vincular cliente
        ns.client().pages.update(page_id=lead_id, properties={
            'Estado': {'status': {'name': 'Convertido'}},
            'Cliente generado': {'relation': [{'id': cliente_id}]},
        })

        return jsonify({'ok': True, 'cliente_id': cliente_id, 'job_id': job_id, 'lead_id': lead_id})
    except Exception as e:
        logger.error(f'Error en accept_shoot: {e}')
        return jsonify({'ok': False, 'error': str(e)}), 500


# ============================================================
# API - LEADS
# ============================================================

@app.route('/api/leads/<lead_id>/status', methods=['POST'])
def api_lead_status(lead_id):
    data = request.json or request.form
    nuevo = data.get('estado')
    if not nuevo:
        return jsonify({'ok': False, 'error': 'estado requerido'}), 400
    local_lead = get_lead(lead_id)
    if local_lead:
        local_lead['status'] = nuevo
        local_lead['updated'] = datetime.now().isoformat()[:10]
        upsert_lead(local_lead)
        return jsonify({'ok': True, 'lead_id': lead_id, 'status': nuevo})
    res = ns.update_lead(lead_id, estado=nuevo)
    return jsonify(res)


@app.route('/api/leads/<lead_id>/delete', methods=['POST'])
def api_lead_delete(lead_id):
    local_lead = get_lead(lead_id)
    if not local_lead:
        return jsonify({'ok': False, 'error': 'Lead no encontrado'}), 404
    store.delete('leads', lead_id)
    return jsonify({'ok': True, 'lead_id': lead_id})


@app.route('/api/leads/<lead_id>/update', methods=['POST'])
def api_lead_update(lead_id):
    data = request.json or request.form
    fields = {}
    for k in ('notas','historial','presupuesto','proximo_followup','email','telefono',
              'fecha_evento','tipo_evento','ubicacion','fuente','tags'):
        if k in data:
            v = data[k]
            if v == '': v = None
            fields[k] = v
    if not fields:
        return jsonify({'ok': False, 'error': 'Sin cambios'}), 400

    local_lead = get_lead(lead_id)
    if local_lead:
        mapping = {
            'notas': 'notas',
            'historial': 'historial',
            'presupuesto': 'presupuesto',
            'proximo_followup': 'proximo_followup',
            'email': 'email',
            'telefono': 'telefono',
            'fecha_evento': 'fecha_tentativa',
            'tipo_evento': 'tipo_evento',
            'ubicacion': 'locacion',
            'fuente': 'fuente',
            'tags': 'tags',
        }
        for source, target in mapping.items():
            if source in fields:
                local_lead[target] = fields[source]
        if local_lead.get('fecha_tentativa'):
            local_lead['next_task'] = local_lead.get('next_task') or 'Seguimiento Cliente'
        local_lead['updated'] = datetime.now().isoformat()[:10]
        upsert_lead(local_lead)
        return jsonify({'ok': True, 'lead': local_lead})

    res = ns.update_lead(lead_id, **fields)
    return jsonify(res)


# ============================================================
# API - CLIENTS
# ============================================================

@app.route('/api/clients/<client_id>/update', methods=['POST'])
def api_client_update(client_id):
    data = request.json or request.form
    fields = {}
    for k in ('nombre','telefono','telefono_secundario','email','portal_url','galeria_url',
              'galeria_cliente_pwd','galeria_invitado_pwd','token_acceso','tags','estado',
              'fuente','notas','direccion_fact','carpeta_drive'):
        if k in data:
            v = data[k]
            if v == '': v = None
            fields[k] = v
    if not fields:
        return jsonify({'ok': False, 'error': 'Sin cambios'}), 400

    local_client = get_client(client_id)
    if local_client:
        if 'nombre' in fields:
            first_name, last_name = _split_name(fields['nombre'] or '')
            local_client['first_name'] = first_name
            local_client['last_name'] = last_name
        mapping = {
            'telefono': 'phone',
            'telefono_secundario': 'phone_secondary',
            'email': 'email',
            'portal_url': 'portal_url',
            'galeria_url': 'galeria_url',
            'galeria_cliente_pwd': 'galeria_cliente_pwd',
            'galeria_invitado_pwd': 'galeria_invitado_pwd',
            'token_acceso': 'token_acceso',
            'tags': 'tags',
            'estado': 'estado',
            'fuente': 'source',
            'notas': 'notes',
            'direccion_fact': 'address',
            'carpeta_drive': 'carpeta_drive',
        }
        for source, target in mapping.items():
            if source in fields:
                local_client[target] = fields[source]
        local_client['updated'] = datetime.now().isoformat()[:10]
        store.upsert('clients', local_client)
        return jsonify({'ok': True, 'client': local_client})

    res = ns.update_client(client_id, **fields)
    return jsonify(res)


# ============================================================
# SETTINGS
# ============================================================



def _notify_new_lead(lead, source_label):
    """Le manda un correo al dueno de la cuenta (el email de la empresa en
    Settings DE ESE TENANT) cada vez que entra un lead nuevo desde un
    formulario publico -- para que se entere aunque no tenga el CRM
    abierto. tenant_id explicito en get_settings/log_email porque esto
    corre desde una ruta publica (sin sesion, el lead ya trae su propio
    tenant_id resuelto por el slug del formulario)."""
    from src.mail_tracker import get_tracker

    tenant_id = lead.get('tenant_id')
    tenant = next((t for t in store.list('tenants') if t.get('id') == tenant_id), {})
    company = (get_settings(tenant_id=tenant_id).get('company', {}) or {})
    to_email = company.get('email') or tenant.get('login_email') or 'norkevinfoto@gmail.com'
    nombre = lead.get('nombre') or 'Sin nombre'
    subject = f"Nuevo lead: {nombre} - {tenant.get('name') or 'Flow CRM'}"
    body_lines = [
        f'Te escribio un nuevo lead desde {source_label}.',
        '',
        f'Nombre: {nombre}',
        f'Email: {lead.get("email") or "-"}',
        f'Telefono: {lead.get("telefono") or "-"}',
        f'Tipo de evento: {lead.get("tipo_evento") or "-"}',
        f'Fecha tentativa: {lead.get("fecha_tentativa") or "-"}',
        f'Ubicacion: {lead.get("locacion") or "-"}',
        f'Fuente: {lead.get("fuente") or "-"}',
    ]
    if lead.get('notes'):
        body_lines += ['', 'Notas:', lead['notes']]
    body_lines += ['', f'Ver lead: /leads/{lead.get("id")}']
    try:
        # STAGE 2 (agosto 2026): tambien pasa por la cola, igual que el
        # resto -- es una notificacion interna (al dueno de la cuenta, no a
        # un cliente), pero "cada correo se mira antes de salir" es la
        # regla pareja que pidio Kevin despues del incidente. Clave estable
        # por lead: un aviso de "lead nuevo" solo tiene sentido una vez.
        get_tracker().queue_email(
            to_email=to_email, subject=subject, body='\n'.join(body_lines),
            lead_id=lead.get('id'), tenant_id=tenant_id,
            source='auto:new-lead-notify',
            idempotency_key=f"leadnotify:{lead.get('id')}",
        )
    except Exception as exc:
        logger.error(f'No se pudo notificar el lead nuevo por correo: {exc}')


@app.route('/contacto')
@app.route('/contacto/<tenant_slug>')
def formulario_lead(tenant_slug=None):
    """Formulario publico para captar leads -- cada marca tiene su propia
    URL (/contacto/<slug>, p.ej. /contacto/norkevin-photography) para que
    el lead quede asignado a la cuenta correcta sin depender de un query
    param manipulable. /contacto sin slug (el link ya embebido en el sitio
    de Astral Weddings hoy) sigue funcionando y cae en esa cuenta."""
    if tenant_slug:
        tenant = _tenant_by_slug(tenant_slug)
        if not tenant:
            abort(404)
    else:
        tenant = _tenant_by_slug('astral-weddings')
    tenant = tenant or {}
    company = get_settings(tenant_id=tenant.get('id')).get('company', {})
    contact_email = company.get('email') or tenant.get('login_email') or 'info@astralweddings.com'
    return render_template(
        'formulario.html',
        lead_sources=_configured_lead_sources(tenant_id=tenant.get('id')),
        tenant_slug=tenant.get('slug', 'astral-weddings'),
        tenant=tenant,
        contact_email=contact_email,
    )


@app.route('/api/leads/nuevo', methods=['POST'])
def crear_lead_publico():
    """Crea un nuevo Lead desde el formulario público. Sin sesion (es un
    endpoint publico), asi que el tenant_id NO sale de get_current_tenant_id()
    -- sale del tenant_slug que /contacto/<slug> incrusto como campo oculto
    del form, validado contra tenants.json (nunca del cliente inventando
    un tenant_id directo)."""
    data = request.get_json() or {}

    tenant = _tenant_by_slug(data.get('tenant_slug')) or _tenant_by_slug('astral-weddings')
    if not tenant:
        return jsonify({'ok': False, 'error': 'Cuenta no reconocida'}), 400
    # El slug ya quedo validado contra tenants.json: recien ahora se fija la
    # cuenta de la peticion, para que el store deje escribir el lead.
    g.public_tenant_id = tenant['id']

    # Validación mínima
    nombre = (data.get('nombre') or '').strip()
    apellido = (data.get('apellido') or '').strip()
    email = (data.get('email') or '').strip()
    pais = (data.get('pais') or '').strip()
    fecha = (data.get('fecha_boda') or '').strip()

    if not nombre or not apellido or not email or not pais or not fecha:
        return jsonify({'ok': False, 'error': 'Faltan campos obligatorios'}), 400

    # Construir notas con toda la info adicional
    mensaje = (data.get('mensaje') or '').strip()
    celular = (data.get('celular') or '').strip()
    ubicacion = (data.get('ubicacion') or '').strip()
    fuente = (data.get('fuente') or '').strip()

    notas_parts = []
    if mensaje:
        notas_parts.append(f"📝 {mensaje}")
    if celular:
        notas_parts.append(f"📱 {celular}")
    if ubicacion:
        notas_parts.append(f"📍 Ubicación: {ubicacion}")
    if pais:
        notas_parts.append(f"🌎 País: {pais}")
    if fuente:
        notas_parts.append(f"🔗 Fuente: {fuente}")
    notas_texto = '\n'.join(notas_parts)

    import uuid
    lead_id = 'lead-' + uuid.uuid4().hex[:8]
    lead = {
        'id': lead_id,
        'nombre': f"{nombre} {apellido}".strip(),
        'email': email,
        'telefono': celular,
        'status': 'Nuevo',
        'fuente': fuente or 'Web',
        'tipo_evento': data.get('tipo_evento', 'Boda'),
        'fecha_tentativa': fecha,
        'locacion': f"{ubicacion + ', ' if ubicacion else ''}{pais}",
        'presupuesto': data.get('presupuesto', ''),
        'notes': notas_texto,
        'created': datetime.now().isoformat()[:10],
        'is_new': True,
        'next_task': 'Pendiente de contacto',
        'mail_status': 'ENVIADO',
        'tenant_id': tenant['id'],
    }
    upsert_lead(lead)
    client, _client_created = _ensure_client_for_lead(lead)
    lead['client_id'] = client['id']
    upsert_lead(lead)
    try:
        instance = trigger_workflow_for_lead(lead_id, lead['nombre'], tenant_id=lead.get('tenant_id'))
        workflow_id = instance.id
    except Exception:
        workflow_id = None
    logger.info(f"Lead publico creado localmente para {tenant['id']}: {lead['nombre']} ({email}) -> {lead_id}")
    _notify_new_lead(lead, 'Formulario de contacto')
    return jsonify({'ok': True, 'id': lead_id, 'lead_id': lead_id, 'workflow_id': workflow_id})

    # Propiedades Notion
    properties = {
        'Nombre': {'title': [{'type': 'text', 'text': {'content': f"{nombre} {apellido}"}}]},
        'Email': {'email': email if email else None},
        'Fecha tentativa del evento': {'date': {'start': fecha}},
        'Locación tentativa': {'rich_text': [{'type': 'text', 'text': {'content': f"{ubicacion + ', ' if ubicacion else ''}{pais}"[:1900]}}]},
        'Estado': {'status': {'name': 'Nuevo'}},
    }
    if celular:
        properties['Teléfono'] = {'phone_number': celular}
    if fuente:
        properties['Fuente'] = {'select': {'name': fuente}}
    if notas_texto:
        properties['Notas'] = {'rich_text': [{'type': 'text', 'text': {'content': notas_texto[:1900]}}]}

    try:
        page = ns.client().pages.create(parent={'data_source_id': ns.DS['LEADS']}, properties=properties)
        logger.info(f"Lead público creado: {nombre} {apellido} ({email}) → {page['id']}")
        return jsonify({'ok': True, 'id': page['id']})
    except Exception as e:
        logger.error(f"Error creando lead público: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


# ============================================================
# BÚSQUEDA GLOBAL (Cmd+K)
# ============================================================

@app.route('/configuracion')
def configuracion_index():
    return render_template('configuracion.html')


@app.route('/api/config/paquetes', methods=['GET'])
def api_config_paquetes_list():
    return jsonify({'paquetes': [_package_config_view(p) for p in store.list('packages')]})


@app.route('/api/config/paquetes', methods=['POST'])
def api_config_paquetes_create():
    data = request.get_json() or {}
    nombre = data.get('Name')
    if not nombre:
        return jsonify({'ok': False, 'error': 'Nombre requerido'}), 400
    import uuid
    package = {
        'id': 'pkg-' + uuid.uuid4().hex[:8],
        'name': nombre,
        'category': data.get('Tipo') or 'Paquete',
        'description': data.get('Notas', ''),
        'duration_hours': data.get('duration_hours'),
        'num_photos': data.get('num_photos', 0),
        'price': float(data.get('precio_q') or 0),
        'includes': [],
        'marca': data.get('Marca') or _brand_display_name_for_tenant(get_current_tenant_id()),
        'active': bool(data.get('Activo', True)),
    }
    store.upsert('packages', package)
    return jsonify({'ok': True, 'item': _package_config_view(package)})


@app.route('/api/config/paquetes/<item_id>', methods=['PATCH'])
def api_config_paquetes_update(item_id):
    data = request.get_json() or {}
    package = store.get('packages', item_id)
    if not package:
        return jsonify({'ok': False, 'error': 'Paquete no encontrado'}), 404
    if 'Name' in data:
        package['name'] = data['Name']
    if 'precio_q' in data and data['precio_q'] is not None:
        package['price'] = float(data['precio_q'])
    if 'Activo' in data:
        package['active'] = bool(data['Activo'])
    if 'Notas' in data:
        package['description'] = data['Notas'] or ''
    store.upsert('packages', package)
    return jsonify({'ok': True, 'item': _package_config_view(package)})


@app.route('/api/config/cuentas', methods=['GET'])
def api_config_cuentas_list():
    return jsonify({'cuentas': _config_items('cuentas')})


@app.route('/api/config/cuentas', methods=['POST'])
def api_config_cuentas_create():
    data = request.get_json() or {}
    if not data.get('Name'):
        return jsonify({'ok': False, 'error': 'Nombre requerido'}), 400
    item = _upsert_config_item('cuentas', None, {
        'Name': data['Name'],
        'Marca': data.get('Marca') or _brand_display_name_for_tenant(get_current_tenant_id()),
        'Notas': data.get('Notas', ''),
        'Activo': data.get('Activo', True),
    })
    return jsonify({'ok': True, 'item': item})


@app.route('/api/config/cuentas/<item_id>', methods=['PATCH'])
def api_config_cuentas_update(item_id):
    data = request.get_json() or {}
    fields = {}
    if 'Name' in data:
        fields['Name'] = data['Name']
    if 'Notas' in data:
        fields['Notas'] = data['Notas'] or ''
    item = _upsert_config_item('cuentas', item_id, fields)
    return jsonify({'ok': True, 'item': item})


@app.route('/api/config/reglas', methods=['GET'])
def api_config_reglas_list():
    return jsonify({'reglas': _config_items('reglas')})


@app.route('/api/config/reglas/<item_id>', methods=['PATCH'])
def api_config_reglas_update(item_id):
    data = request.get_json() or {}
    fields = {}
    if 'Name' in data:
        fields['Name'] = data['Name']
    if 'porcentaje' in data and data['porcentaje'] is not None:
        fields['Porcentaje'] = float(data['porcentaje'])
    if 'Notas' in data:
        fields['Notas'] = data['Notas'] or ''
    item = _upsert_config_item('reglas', item_id, fields)
    return jsonify({'ok': True, 'item': item})


@app.route('/api/config/fuentes', methods=['GET'])
def api_config_fuentes_list():
    return jsonify({'fuentes': _config_items('fuentes')})


@app.route('/api/config/fuentes/<item_id>/activo', methods=['PATCH'])
def api_config_fuentes_toggle(item_id):
    data = request.get_json() or {}
    item = _upsert_config_item('fuentes', item_id, {'Activo': bool(data.get('Activo', True))})
    return jsonify({'ok': True, 'item': item})


@app.route('/api/config/datos', methods=['GET'])
def api_config_datos_list():
    return jsonify({'datos': _config_items('datos')})


@app.route('/api/config/datos', methods=['POST'])
def api_config_datos_create():
    data = request.get_json() or {}
    if not data.get('Name'):
        return jsonify({'ok': False, 'error': 'Nombre requerido'}), 400
    item = _upsert_config_item('datos', None, {
        'Name': data['Name'],
        'Notas': data.get('Notas', ''),
        'Activo': True,
    })
    return jsonify({'ok': True, 'item': item})


@app.route('/api/config/datos/<item_id>', methods=['PATCH'])
def api_config_datos_update(item_id):
    data = request.get_json() or {}
    fields = {}
    if 'Name' in data:
        fields['Name'] = data['Name']
    if 'Notas' in data:
        fields['Notas'] = data['Notas'] or ''
    item = _upsert_config_item('datos', item_id, fields)
    return jsonify({'ok': True, 'item': item})


# ============================================================
# WORKFLOW EDITOR + API
# ============================================================

@app.route('/workflow-editor')
def workflow_editor():
    """Pantalla estilo Studio Ninja para editar workflow templates."""
    selected_id = request.args.get('id', 'lead_workflow_v1')
    templates = workflow_engine.list_templates()
    selected = workflow_engine.get_template(selected_id)
    if not selected:
        selected = templates[0] if templates else LEAD_WORKFLOW()
    email_templates = store.list('email_templates')
    # Se lee en vivo de email_templates.json (misma fuente que usan los
    # modales de Send Email/Send Contract en Jobs y Leads) para que el
    # preview de aca siempre este sincronizado -- no se guarda ninguna
    # copia del texto en el workflow template, solo el email_template_id.
    email_template_map = {tpl.get('id'): tpl for tpl in email_templates}
    return render_template('workflow_editor.html',
                          templates=templates,
                          selected=selected,
                          selected_id=selected.id,
                          email_templates=email_templates,
                          email_template_map=email_template_map)


@app.route('/api/workflow/templates')
def api_workflow_templates():
    return jsonify({'templates': [t.to_dict() for t in workflow_engine.list_templates()]})


@app.route('/api/workflow/template/<template_id>')
def api_workflow_template_get(template_id):
    tmpl = workflow_engine.get_template(template_id)
    if not tmpl:
        return jsonify({'ok': False, 'error': 'Template no encontrado'}), 404
    return jsonify({'ok': True, 'template': tmpl.to_dict()})


@app.route('/api/workflow/template/<template_id>', methods=['PUT'])
def api_workflow_template_update(template_id):
    """Actualiza un template (reemplaza steps) y lo persiste a disco."""
    data = request.get_json() or {}
    data['id'] = template_id
    try:
        new_workflow = _workflow_from_dict(data)
        workflow_engine.register_template(new_workflow)
        _persist_workflow_template(new_workflow)
        return jsonify({'ok': True, 'template': new_workflow.to_dict()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.route('/api/workflow/template', methods=['POST'])
def api_workflow_template_create():
    """Crea un nuevo workflow template vacio y lo persiste."""
    import re as _re
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'Nombre requerido'}), 400

    slug = _re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_') or 'workflow'
    template_id = slug
    suffix = 1
    while workflow_engine.get_template(template_id):
        suffix += 1
        template_id = f'{slug}_{suffix}'

    try:
        new_workflow = _workflow_from_dict({
            'id': template_id,
            'name': name,
            'description': data.get('description', ''),
            'trigger_type': data.get('trigger_type', 'lead.created'),
            'steps': [],
        })
        workflow_engine.register_template(new_workflow)
        _persist_workflow_template(new_workflow)
        return jsonify({'ok': True, 'template_id': template_id, 'template': new_workflow.to_dict()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.route('/api/workflow/test/<template_id>', methods=['POST'])
def api_workflow_test(template_id):
    """Crea un lead ficticio y le aplica el workflow."""
    tmpl = workflow_engine.get_template(template_id)
    if not tmpl:
        return jsonify({'ok': False, 'error': 'Template no encontrado'}), 404

    # Crear instancia ficticia
    fake_name = f"Test Lead {datetime.now().strftime('%H:%M:%S')}"
    instance = workflow_engine.start_workflow(
        workflow=tmpl,
        subject_type='lead',
        subject_id=f"test_{int(datetime.now().timestamp())}",
        subject_name=fake_name,
        trigger_event='test.created',
        tenant_id=get_current_tenant_id(),
    )
    return jsonify({'ok': True, 'instance_id': instance.id, 'subject': fake_name})


def _instancia_es_de_la_cuenta(inst, job_ids_cache=None, lead_ids_cache=None, tenant_id=None):
    """True si la instancia es de la cuenta activa (o de `tenant_id`, para
    el unico caso que recorre varias cuentas en una sola llamada: el admin
    tool /api/admin/workflow-cleanup, que ya usa store.list_privileged(
    tenant_id=tid, ...) para leer los jobs de cada cuenta por turno -- sin
    este parametro, la comparacion de la capa 1 usaria get_current_tenant_id()
    -la sesion activa del admin, no la cuenta que se esta recorriendo- y
    podria: (a) no encontrar nunca instancias etiquetadas de otras cuentas
    (falla cerrado, solo se vuelve inefectivo), o (b) peor, en una colision
    real de subject_id, devolver la instancia de la cuenta EQUIVOCADA. Todo
    call site normal (request-scoped, una sola cuenta activa) deja esto en
    None y el comportamiento no cambia.

    El WorkflowEngine guarda TODAS las instancias de las 3 cuentas en un
    solo diccionario en memoria y en un `workflow_instances.json` global
    (sin sufijo de cuenta). /api/workflow/instances listaba el diccionario
    entero sin pasar por ningun job, asi que Astral veia los nombres de las
    bodas de Norkevin -- ese fue el bug original que esta funcion cerro.

    Dos capas (27-ago-2026):
      1. Si la instancia tiene `tenant_id` (todas las creadas desde que se
         agrego ese campo -- ver WorkflowInstance en src/workflow/models.py),
         es una comparacion directa e inequivoca contra `tenant_id` (o la
         cuenta activa si no se paso ninguna). No hace falta mirar
         jobs/leads para nada.
      2. Si no lo tiene (instancias guardadas ANTES de ese campo -- no se
         migran retroactivamente), se cae al heuristico original: el dueno
         se resuelve por los datos, no por un campo. Si el job/lead no
         aparece en las listas ya filtradas por cuenta, la instancia no es
         de esta cuenta.

    La capa 1 tambien cierra un agujero que la capa 2 sola no podia cerrar:
    los jobs importados de Studio Ninja usan un id deterministico por
    nombre de pareja ('boda-sn-<slug>'), asi que dos cuentas pueden terminar
    con el MISMO subject_id. En ese caso el heuristico de la capa 2 le dice
    "si" a CUALQUIER instancia con ese subject_id, sin importar de que
    cuenta sea -- exactamente el escenario que motivo agregar `tenant_id`.

    job_ids_cache/lead_ids_cache (batch, ver test_rendimiento_vistas.py):
    quien ya los calculo (por ejemplo una vista que llama esto una vez por
    fila) los pasa para no releer jobs/leads en cada llamada -- solo se
    usan para la capa 2 (instancias legacy sin tenant_id).

    Efecto lateral buscado (capa 2, sin cambios): las instancias cuyo
    subject ya no existe (las 143 filas huerfanas de los datos demo) dejan
    de aparecer. Una instancia que apunta a un job borrado no es accionable
    por nadie.
    """
    if inst.tenant_id:
        return inst.tenant_id == (tenant_id or get_current_tenant_id())
    if job_ids_cache is None:
        job_ids_cache = {j.get('id') for j in list_jobs()}
    if lead_ids_cache is None:
        lead_ids_cache = {l.get('id') for l in list_leads()}
    if inst.subject_type == 'job':
        return inst.subject_id in job_ids_cache
    if inst.subject_type == 'lead':
        return inst.subject_id in lead_ids_cache
    return False


def _workflow_instances_del_tenant():
    """Instancias de workflow de la cuenta activa, en una sola pasada."""
    job_ids_cache = {j.get('id') for j in list_jobs()}
    lead_ids_cache = {l.get('id') for l in list_leads()}
    return [i for i in workflow_engine.list_instances()
            if _instancia_es_de_la_cuenta(i, job_ids_cache, lead_ids_cache)]


def _workflow_instances_seguras(subject_type=None, subject_id=None, job_ids_cache=None, lead_ids_cache=None, tenant_id=None):
    """Envoltorio seguro sobre el motor de workflows: aplica
    _instancia_es_de_la_cuenta() para que ningun call site pueda quedarse
    con la instancia de OTRA cuenta ante una colision de subject_id (ver
    docstring de esa funcion). Usar esto en vez de ir directo al motor en
    cualquier codigo nuevo que busque "la instancia de este job/lead".

    job_ids_cache/lead_ids_cache: idem _instancia_es_de_la_cuenta -- pasarlos
    cuando quien llama ya los tiene (una vista que recorre muchos jobs/leads),
    para no releer la tabla completa una vez por fila.

    tenant_id: idem _instancia_es_de_la_cuenta -- solo hace falta cuando
    quien llama no esta en el contexto de una sola cuenta activa (el admin
    tool que recorre varias cuentas por turno)."""
    candidatas = workflow_engine.list_instances(subject_type=subject_type, subject_id=subject_id)
    return [i for i in candidatas if _instancia_es_de_la_cuenta(i, job_ids_cache, lead_ids_cache, tenant_id)]


@app.route('/api/workflow/instances')
def api_workflow_instances():
    instancias = _workflow_instances_del_tenant()
    por_estado = {}
    for inst in instancias:
        clave = inst.status.value
        por_estado[clave] = por_estado.get(clave, 0) + 1
    return jsonify({
        'instances': [i.to_dict() for i in instancias],
        # Las stats se calculan aca sobre las instancias de ESTA cuenta.
        # workflow_engine.stats() cuenta las de todas las marcas juntas y
        # ademas nunca incrementa su contador por estado (siempre da 0).
        'stats': {
            'total_instances': len(instancias),
            'by_status': por_estado,
            'templates': list(workflow_engine.templates.keys()),
        },
    })


@app.route('/api/workflow/instances/<instance_id>')
def api_workflow_instance_detail(instance_id):
    inst = workflow_engine.get_instance(instance_id)
    if not inst or not _instancia_es_de_la_cuenta(inst):
        # Mismo 404 para "no existe" y "no es tuya": responder distinto
        # confirmaria a una marca que la otra tiene esa instancia.
        return jsonify({'ok': False, 'error': 'No encontrado'}), 404
    return jsonify({'ok': True, 'instance': inst.to_dict()})


@app.route('/api/workflow/history')
def api_workflow_history():
    mios = {i.id for i in _workflow_instances_del_tenant()}
    historial = [h for h in workflow_engine.get_history(limit=2000)
                 if h.get('instance_id') in mios]
    return jsonify({'history': historial[-100:]})


# Trigger automatico: cuando se crea un lead, dispara LEAD_WORKFLOW
@app.route('/api/workflow/trigger/lead_created', methods=['POST'])
def api_workflow_trigger_lead_created():
    data = request.get_json() or {}
    instance = workflow_engine.start_workflow(
        workflow=LEAD_WORKFLOW(),
        subject_type='lead',
        subject_id=data.get('lead_id', ''),
        subject_name=data.get('nombre', 'Lead'),
        trigger_event='lead.created',
        tenant_id=get_current_tenant_id(),
    )
    return jsonify({'ok': True, 'instance_id': instance.id})


# ============================================================
# LEAD CRUD (con auto-workflow)
# ============================================================
@app.route('/api/leads/new', methods=['POST'])
def api_lead_create():
    """Crea un lead nuevo y dispara LEAD_WORKFLOW automaticamente."""
    data = request.get_json() or {}
    client = get_client(data.get('client_id', '')) if data.get('client_id') else None
    if client and not data.get('nombre'):
        data['nombre'] = (f"{client.get('first_name', '')} {client.get('last_name', '')}").strip()
    if client:
        data.setdefault('email', client.get('email', ''))
        data.setdefault('telefono', client.get('phone', ''))
        # NO se rellena 'locacion' con la direccion de facturacion del
        # cliente: son campos distintos (venue del evento vs direccion de
        # cobro) y este setdefault era la segunda mitad del bucle de
        # contaminacion. Si no se sabe el lugar del evento, se deja vacio.
    if not data.get('nombre'):
        return jsonify({'ok': False, 'error': 'nombre requerido'}), 400

    import uuid
    from datetime import datetime as _dt

    lead_id = 'lead-' + uuid.uuid4().hex[:8]
    lead = {
        'id': lead_id,
        'nombre': data['nombre'],
        'email': data.get('email', ''),
        'telefono': data.get('telefono', ''),
        'status': 'Nuevo',
        'fuente': data.get('fuente', 'Instagram'),
        'tipo_evento': data.get('tipo_evento', 'Boda'),
        'fecha_tentativa': data.get('fecha_tentativa'),
        'locacion': data.get('locacion', ''),
        'presupuesto': data.get('presupuesto', ''),
        'created': _dt.now().isoformat()[:10],
        'is_new': True,
        'next_task': 'Pendiente de contacto',
        'mail_status': 'ENVIADO',
        'client_id': data.get('client_id') or '',
        'tenant_id': (client or {}).get('tenant_id') or get_current_tenant_id(),
    }
    upsert_lead(lead)
    if not lead['client_id']:
        linked_client, _client_created = _ensure_client_for_lead(lead)
        lead['client_id'] = linked_client['id']
        upsert_lead(lead)

    # AUTO-DISPARAR workflow
    instance = trigger_workflow_for_lead(lead_id, data['nombre'], tenant_id=lead.get('tenant_id'))

    return jsonify({'ok': True, 'lead': lead, 'workflow_instance_id': instance.id})


@app.route('/api/leads/<lead_id>/accept', methods=['POST'])
def api_lead_accept(lead_id):
    """Acepta el quote de un lead. Lo convierte a CLIENTE + JOB.
    Esto es la cascada magica: dispara PRODUCTION_WORKFLOW."""
    lead = get_lead(lead_id)
    if not lead:
        return jsonify({'ok': False, 'error': 'Lead no encontrado'}), 404

    result = _convert_lead_to_job(lead, quote=None, status='Confirmado', create_payments=False)

    return jsonify({
        'ok': True,
        'already_converted': not result['job_created'],
        'lead_id': lead_id,
        'client_id': result['client']['id'],
        'job_id': result['job']['id'],
        'workflow_instance_id': result['workflow_instance_id'],
        'client_created': result['client_created'],
        'job_created': result['job_created'],
    })


# Trigger automatico: cuando se acepta quote, dispara PRODUCTION_WORKFLOW
@app.route('/api/workflow/trigger/quote_accepted', methods=['POST'])
def api_workflow_trigger_quote_accepted():
    data = request.get_json() or {}
    instance = workflow_engine.start_workflow(
        workflow=PRODUCTION_WORKFLOW(),
        subject_type='job',
        subject_id=data.get('job_id', ''),
        subject_name=data.get('nombre', 'Job'),
        trigger_event='quote.accepted',
        tenant_id=get_current_tenant_id(),
    )
    return jsonify({'ok': True, 'instance_id': instance.id})


# Cron: ejecutar steps vencidos
@app.route('/api/workflow/run-due', methods=['POST'])
def api_workflow_run_due():
    due = workflow_engine.get_due_steps()
    executed = 0
    for instance, step in due:
        if workflow_engine.execute_step(instance.id, step.id):
            executed += 1
    return jsonify({'ok': True, 'executed': executed, 'due_count': len(due)})


# ============================================================
# API: Calendar Events (CRUD real)
# ============================================================
@app.route('/api/calendar/events', methods=['POST'])
def api_calendar_create_event():
    """Crea un evento en el calendario."""
    import uuid
    from datetime import datetime as _dt

    data = request.get_json() or {}
    title = data.get('title', '').strip()
    date_str = data.get('date', '')
    event_type = data.get('type', 'event')  # 'job' | 'wedding' | 'lead' | 'event'

    if not title or not date_str:
        return jsonify({'ok': False, 'error': 'titulo y fecha requeridos'}), 400

    event = {
        'id': 'evt-' + uuid.uuid4().hex[:8],
        'date': date_str,
        'type': event_type,
        'title': title,
        'job_id': data.get('job_id'),
        'lead_id': data.get('lead_id'),
        'notes': data.get('notes', ''),
        'created': _dt.now().isoformat()[:10],
    }
    store.upsert('calendar', event)
    return jsonify({'ok': True, 'event': event})


@app.route('/api/calendar/events/<event_id>', methods=['DELETE'])
def api_calendar_delete_event(event_id):
    """Elimina un evento del calendario."""
    store.delete('calendar', event_id)
    return jsonify({'ok': True})


@app.route('/api/calendar/export.ics')
def api_calendar_export_ics():
    """Exporta todos los eventos a formato iCal (.ics)."""
    from flask import Response

    events = list_calendar()
    jobs = {j['id']: j for j in list_jobs()}

    ics_lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//ASTRAL WEDDINGS CRM//Calendar//ES',
        'CALSCALE:GREGORIAN',
    ]

    for evt in events:
        # Convertir date (YYYY-MM-DD) a formato iCal (YYYYMMDD)
        date_compact = evt.get('date', '').replace('-', '')
        title = evt.get('title', 'Sin titulo').replace(',', '\\,')

        # Buscar info adicional del job
        desc_lines = []
        job_id = evt.get('job_id')
        if job_id and job_id in jobs:
            job = jobs[job_id]
            desc_lines.append(f"Job: {job.get('nombre', '')}")
            if job.get('location'):
                desc_lines.append(f"Lugar: {job['location']}")
            if job.get('price_total'):
                desc_lines.append(f"Total: Q{job['price_total']:,.0f}")
        if evt.get('notes'):
            desc_lines.append(f"Notas: {evt['notes']}")
        desc = '\\n'.join(desc_lines)

        ics_lines.extend([
            'BEGIN:VEVENT',
            f'DTSTART;VALUE=DATE:{date_compact}',
            f'DTEND;VALUE=DATE:{date_compact}',
            f'SUMMARY:{title}',
            f'DESCRIPTION:{desc}',
            f'UID:{evt.get("id", "")}@norkevin-crm',
            'END:VEVENT',
        ])

    ics_lines.append('END:VCALENDAR')
    ics_content = '\\r\\n'.join(ics_lines)

    return Response(ics_content, mimetype='text/calendar', headers={
        'Content-Disposition': 'attachment; filename=norkevin-calendar.ics'
    })


# ============================================================
# API: Payments - Export CSV y acciones reales
# ============================================================
@app.route('/api/payments/export.csv')
def api_payments_export_csv():
    """Exporta los pagos a CSV (descargable)."""
    from flask import Response
    import csv
    import io

    payments_all = _visible_billable_payments()
    clients = {c['id']: c for c in list_clients()}
    jobs = {j['id']: j for j in list_jobs()}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Invoice ID', 'Status', 'Due Date', 'Client', 'Job', 'Concepto', 'Amount (GTQ)', 'Cuota'])

    for p in payments_all:
        c = clients.get(p.get('client_id', ''))
        client_name = f"{c['first_name']} {c['last_name']}" if c else ''
        j = jobs.get(p.get('job_id', ''))
        job_name = j['nombre'] if j else ''

        writer.writerow([
            p.get('invoice_id', ''),
            p.get('status', ''),
            p.get('due_date', ''),
            client_name,
            job_name,
            p.get('concepto', ''),
            p.get('amount', 0),
            p.get('cuota', ''),
        ])

    csv_content = output.getvalue()
    return Response(csv_content, mimetype='text/csv', headers={
        'Content-Disposition': 'attachment; filename=norkevin-payments.csv'
    })


# ============================================================
# API: Settings - Actualizar configuracion
# ============================================================
@app.route('/api/settings/company', methods=['POST'])
def api_settings_company_update():
    """Actualiza los datos de la empresa."""
    s = get_settings()
    data = request.get_json() or {}

    if 'company' not in s:
        s['company'] = {}

    if 'name' in data: s['company']['name'] = data['name']
    if 'currency' in data: s['company']['currency'] = data['currency']
    if 'timezone' in data: s['company']['timezone'] = data['timezone']
    if 'email' in data: s['company']['email'] = data['email']
    if 'phone' in data: s['company']['phone'] = data['phone']
    if 'bank_info' in data: s['company']['bank_info'] = data['bank_info']

    store.save_tenant_dict('settings', s)
    return jsonify({'ok': True, 'company': s['company']})


# ============================================================
# API: Quotes - Generar PDF y enviar al cliente
# ============================================================
def _normalize_quote_options(quote):
    """Devuelve la lista de opciones de paquete de una cotizacion. Si la
    cotizacion ya tiene 'options' (cotizacion nueva, multi-opcion) las usa
    tal cual; si no (cotizacion vieja, un solo paquete) sintetiza UNA opcion
    a partir de los campos planos de siempre, para que nada viejo se rompa."""
    options = quote.get('options')
    if options:
        return options
    return [{
        'id': 'legacy',
        'name': quote.get('paquete_nombre') or 'Paquete',
        'precio_total': quote.get('precio_total') or 0,
        'items': quote.get('items') or [],
        'incluye': quote.get('incluye') or [],
        'notas': quote.get('notas') or '',
    }]


def _snapshot_comercial(quote):
    """Fuente UNICA del desglose comercial de una cotizacion.

    La regla del sistema, y el motivo de que esta funcion exista:

        QUOTE TEMPLATE           plantilla reutilizable (packages.json)
        QUOTE DRAFT              propuesta editable
        SNAPSHOT ACEPTADO        el acuerdo comercial, congelado
        JOB                      ejecucion operativa
        INVOICE                  representacion financiera de ese acuerdo

    Una factura NO reconstruye el paquete: lee el acuerdo. Si el fotografo
    cambia la plantilla Silver de 8 a 12 horas, la factura de un cliente
    que acepto 8 sigue diciendo 8. La plantilla es reutilizable; una
    cotizacion aceptada es historica.

    Prioridad de fuentes, de mas fiel a menos:
      1. snapshot_aceptado  -- congelado al aceptar (cotizaciones nuevas)
      2. campos planos del quote materializados al aceptar (servicios /
         groups / incluye) -- cotizaciones aceptadas antes del snapshot
      3. la opcion seleccionada, si todavia no se materializo
      4. la primera opcion, solo si no hay ninguna seleccion
    NUNCA la plantilla actual: eso es lo que producia la divergencia.

    Devuelve None si no hay cotizacion.
    """
    if not quote:
        return None

    snap = quote.get('snapshot_aceptado')
    if isinstance(snap, dict) and (snap.get('servicios') or snap.get('groups') or snap.get('incluye')):
        return {
            'nombre': snap.get('name') or quote.get('paquete_nombre') or 'Servicios',
            'servicios': snap.get('servicios') or [],
            'groups': snap.get('groups') or [],
            'incluye': snap.get('incluye') or [],
            'total': snap.get('total'),
            'extras': snap.get('extras') or [],
            'fuente': 'snapshot_aceptado',
        }

    # Aceptada antes de que existiera el snapshot: los campos planos ya
    # fueron materializados por quote_accept y son historicos igual.
    if quote.get('paquete_nombre'):
        return {
            'nombre': quote.get('paquete_nombre'),
            'servicios': quote.get('servicios') or [],
            'groups': quote.get('groups') or [],
            'incluye': quote.get('incluye') or [],
            'total': quote.get('precio_total'),
            'extras': quote.get('selected_extras') or [],
            'fuente': 'campos_materializados',
        }

    # Todavia no aceptada: la opcion elegida, o la primera propuesta.
    opciones = _normalize_quote_options(quote)
    elegida = next((o for o in opciones if o.get('id') == quote.get('selected_option_id')), None)
    fuente = 'opcion_seleccionada' if elegida else 'primera_opcion'
    elegida = elegida or (opciones[0] if opciones else {})
    return {
        'nombre': elegida.get('name') or 'Servicios',
        'servicios': elegida.get('servicios') or [],
        'groups': elegida.get('groups') or [],
        'incluye': elegida.get('incluye') or [],
        'total': elegida.get('precio_total'),
        'extras': quote.get('selected_extras') or [],
        'fuente': fuente,
    }


def _quote_grupos_display(opcion):
    """Grupos listos para pintar: [{titulo, servicios:[{texto, icono}]}].

    Tres caminos, en orden de calidad del dato:

      1. servicios[] estructurados -> agrupacion, iconos y pluralizacion
         resueltos por tipo. Es el camino de las cotizaciones nuevas.
      2. groups[] escritos a mano (modelo intermedio) -> se respetan los
         titulos del fotografo y el icono se deduce del texto.
      3. incluye[] plano (cotizaciones viejas) -> un solo bloque sin titulo.

    En 2 y 3 el icono sale de clasificar_legacy(), que solo reconoce
    patrones inequivocos ("2 fotografos", "Galeria online") y ante la duda
    devuelve el neutro. Nunca se adivina de mas: un icono generico es un
    detalle estetico, uno equivocado le dice al cliente algo que no es.
    """
    if not isinstance(opcion, dict):
        return []

    servicios = opcion.get('servicios')
    if servicios:
        return qsvc.agrupar(servicios)

    grupos = opcion.get('groups') or []
    if grupos:
        return [{
            'clave': '', 'titulo': (g.get('title') or '').strip(),
            'servicios': [{'texto': str(x), 'icono': qsvc.icono_para_texto_legacy(str(x))}
                          for x in (g.get('items') or []) if str(x).strip()],
        } for g in grupos if isinstance(g, dict) and (g.get('items') or [])]

    incluye = opcion.get('incluye') or []
    if incluye:
        # Los items legacy pasan por la capa de normalizacion ANTES de
        # pintarse: vuelve a unir fragmentos que evidentemente eran una
        # sola frase ("8 horas de cobertura + 1 hora" / "extra"), baja las
        # aclaraciones entre parentesis a sub-informacion, y convierte en
        # titulo lo que se guardo como "Eventos:". El dato almacenado no se
        # toca -- esto es presentacion.
        normalizados = qsvc.normalizar_items_legacy(incluye)
        grupos, actual = [], None
        for item in normalizados:
            if item['es_encabezado']:
                actual = {'clave': '', 'titulo': item['texto'], 'servicios': []}
                grupos.append(actual)
                continue
            if actual is None:
                actual = {'clave': '', 'titulo': '', 'servicios': []}
                grupos.append(actual)
            actual['servicios'].append({
                'texto': item['texto'],
                'nota': item['nota'],
                'icono': qsvc.icono_para_texto_legacy(item['texto']),
            })
        return [g for g in grupos if g['servicios']]
    return []


def _resolve_quote_package(quote):
    """Nombre + descripcion (incluye) del paquete de una cotizacion, ya sea
    que este ya aceptada (campos planos materializados) o siga pendiente
    (multi-opcion): en ese caso usa la opcion elegida o la primera propuesta.
    Se usa en el PDF, el portal del cliente y cualquier lugar que necesite
    mostrarle al cliente que incluye lo que esta pagando."""
    name = quote.get('paquete_nombre')
    incluye = quote.get('incluye')
    if name:
        return name, incluye or []
    options = _normalize_quote_options(quote)
    selected = next((o for o in options if o.get('id') == quote.get('selected_option_id')), options[0])
    return selected.get('name'), selected.get('incluye') or []


def _quote_plan_choices(quote):
    """Cuotas ofrecidas al cliente. Si la cotizacion define plan_pago_opciones
    las respeta, si no ofrece 1-4 por defecto."""
    choices = quote.get('plan_pago_opciones')
    if choices:
        return sorted(set(int(c) for c in choices if int(c) > 0))
    return [1, 2, 3, 4]


# ============================================================
# PUBLIC QUOTE EXPERIENCE -- BLOQUE B (28-ago-2026)
# ============================================================
# Extensiones de modelo para la nueva experiencia publica de cotizaciones.
# Regla de todo este bloque: aditivo. Ningun campo nuevo reemplaza uno
# existente, y toda cotizacion vieja (sin estos campos) sigue funcionando
# exactamente igual via .get(clave, default). NO se toca accept_quote(),
# _convert_lead_to_job(), conversion_registry, _ensure_payments_for_quote,
# _crear_schedule/_active_schedule_for, ni tenant_brand_map/resolve_pdf_brand.

def _assign_quote_number(quote, tenant_id=None):
    """Asigna quote['number'] (NORK-2026-0041) una sola vez, via el contador
    atomico por cuenta+anio (JsonStore.next_sequence_number). Idempotente:
    si el quote ya tiene numero, no hace nada -- evita "quemar" un numero
    de la secuencia en un resave.

    tenant_id explicito porque el dict `quote` que arma cada endpoint de
    creacion no siempre tiene 'tenant_id' todavia en el momento en que esto
    se llama (varios lo agregan recien dentro de store.upsert()) -- ver
    _upsert_locked en storage.py. Si no se puede resolver ninguna cuenta ni
    ningun prefijo de marca (tenant_brand_map.py, sin tocar), el quote
    queda sin numero: mejor visible y corregible a mano que numerado con el
    prefijo de la empresa equivocada."""
    if quote.get('number'):
        return quote
    tenant_id = tenant_id or quote.get('tenant_id') or get_current_tenant_id()
    prefix = quote_numbering.prefix_for_tenant(tenant_id)
    if not tenant_id or not prefix:
        return quote
    year = date.today().year
    seq = store.next_sequence_number('quotes', tenant_id=tenant_id, year=year)
    quote['number'] = quote_numbering.format_quote_number(prefix, year, seq)
    return quote


def _flatten_option_groups(groups):
    """Aplana grupos estructurados ({'title', 'items': [...]}) a una lista
    plana de strings ('Titulo: item'), para seguir poblando quote['incluye']
    / option['incluye'] -- el campo que YA leen el PDF (pdf_generator.py) y
    las vistas viejas. Asi una opcion armada con grupos nuevos sigue
    mostrandose bien en todo lo que todavia no sabe de 'groups', sin tener
    que tocar ese codigo."""
    flat = []
    for g in (groups or []):
        title = (g.get('title') or '').strip()
        for item in (g.get('items') or []):
            item = str(item).strip()
            if not item:
                continue
            flat.append(f'{title}: {item}' if title else item)
    return flat


def _load_portfolio(tenant_id=None, *, only_active=True):
    """Portfolio de la cuenta activa (o tenant_id explicito, para el caso de
    una ruta publica que ya resolvio la cuenta del enlace pero no tiene
    sesion). list_privileged ya filtra por tenant_id internamente -- no
    hace falta filtrar de nuevo."""
    items = store.list('portfolio_items') if tenant_id is None else store.list_privileged(
        'portfolio_items', tenant_id=tenant_id, reason='resolver portfolio de la cuenta')
    if only_active:
        items = [i for i in items if i.get('active', True)]
    return sorted(items, key=lambda i: (i.get('order') if i.get('order') is not None else 999))


def _load_terms_templates(tenant_id=None):
    items = store.list('quote_terms_templates') if tenant_id is None else store.list_privileged(
        'quote_terms_templates', tenant_id=tenant_id, reason='resolver condiciones de la cuenta')
    return sorted(items, key=lambda i: (i.get('order') if i.get('order') is not None else 999))


def _load_quote_templates(tenant_id=None):
    items = store.list('quote_templates') if tenant_id is None else store.list_privileged(
        'quote_templates', tenant_id=tenant_id, reason='resolver templates de la cuenta')
    return sorted([i for i in items if i.get('active', True)],
                  key=lambda i: (i.get('order') if i.get('order') is not None else 999))


def _video_embed_url(url):
    """Convierte un link de YouTube/Vimeo pegado a mano a su URL de embed.

    Rediseño editorial (29-ago-2026): Kevin pidio un 'video destacado' en el
    portafolio de la cotizacion publica. No existia ningun campo de video en
    el modelo -- se agrega theme.featured_video_url (una URL comun, la que
    se copia de la barra de direcciones) y esta funcion la traduce a lo que
    un <iframe> puede reproducir. Si no reconoce el formato, devuelve None
    a proposito: mejor no mostrar nada que mostrar un iframe roto."""
    if not url:
        return None
    url = url.strip()
    m = re.search(r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([\w-]{6,})', url)
    if m:
        return f'https://www.youtube.com/embed/{m.group(1)}'
    m = re.search(r'vimeo\.com/(?:video/)?(\d+)', url)
    if m:
        return f'https://player.vimeo.com/video/{m.group(1)}'
    return None


def _quote_theme_for_tenant(tenant_id):
    """Theme visual (colores/logo/footer/CTA) de la Public Quote Experience
    para una cuenta. Envuelve resolve_pdf_brand (sin tocarlo ni duplicar su
    logica) y agrega SOLO lo cosmetico, leido de settings_<tenant>.json,
    bloque 'quote_theme'. Mismo fail-hard que el resto: sin marca resuelta,
    placeholder neutro -- nunca la marca de otra cuenta."""
    brand = resolve_pdf_brand(tenant_id)
    theme_saved = {}
    if tenant_id:
        try:
            theme_saved = (store.get_tenant_dict('settings', tenant_id=tenant_id) or {}).get('quote_theme') or {}
        except Exception:
            theme_saved = {}
    defaults = {
        # ------------------------------------------------------------------
        # TOKENS DEL SISTEMA DE DOCUMENTOS DE FLOW CRM (29-ago-2026)
        # ------------------------------------------------------------------
        # Kevin: "quiero que Flow CRM tenga un lenguaje visual consistente...
        # la cotizacion que ya tenemos, pero diseñada por Flow CRM". Estos
        # valores son el MISMO ADN que templates/base.html (las variables
        # --sn-* del CRM), para que dashboard, cotizacion y factura se vean
        # obviamente del mismo producto sin ser la misma pantalla.
        #
        # Los defaults viejos (paleta editorial hueso/crema/dorado) quedaron
        # reemplazados aca, pero NO se pierden donde importa: una cotizacion
        # ya enviada guarda su theme_snapshot y _document_theme() hace merge
        # sobre estos defaults, asi que sigue viendose como cuando se mando
        # y ademas hereda los tokens nuevos que su snapshot no conocia.
        #
        # Todo esto es por cuenta: una marca futura puede cambiar su primary
        # sin tocar una sola plantilla.
        # ------------------------------------------------------------------
        # Estos valores NO son una paleta inventada para los documentos: son
        # los tokens --sn-* de templates/base.html, copiados uno a uno. El
        # criterio de Kevin es que al pasar del CRM a la cotizacion no se
        # note el salto, y para eso el morado, los grises, las lineas y los
        # radios tienen que ser literalmente los mismos, no parecidos.
        #   --sn-green         -> primary          #7357F6
        #   --sn-green-dark    -> primary_dark     #6447EE
        #   --sn-mint          -> primary_soft     #F0EDFF
        #   --sn-mint-soft     -> primary_tint     #F7F5FF
        #   --sn-canvas        -> background       #F7F8FC
        #   --sn-surface-2     -> surface_2        #F4F5F9
        #   --sn-ink           -> text_primary     #111827
        #   --sn-muted         -> text_secondary   #667085
        #   --sn-soft          -> muted            #98A2B3
        #   --sn-line          -> border           #E7EAF0
        #   --sn-line-dark     -> border_strong    #D7DCE5
        #   --sn-success       -> success          #2FB66D
        #   --sn-yellow        -> warning          #F59E0B
        #   --sn-red           -> danger           #EF5B5B
        #   --sn-radius-*      -> radius_sm/md/lg  8 / 12 / 16
        #   --sn-shadow-resting-> shadow_card
        # Si algun dia cambian en base.html, hay que cambiarlos aca tambien.
        # ------------------------------------------------------------------
        'primary': '#7357F6',
        'primary_dark': '#6447EE',
        'primary_soft': '#F0EDFF',
        'primary_tint': '#F7F5FF',
        'background': '#F7F8FC',
        'surface': '#FFFFFF',
        'surface_2': '#F4F5F9',
        'text_primary': '#111827',
        'text_secondary': '#667085',
        'muted': '#98A2B3',
        'border': '#E7EAF0',
        'border_strong': '#D7DCE5',
        'success': '#2FB66D', 'success_soft': '#EAF8F0',
        'warning': '#F59E0B', 'warning_soft': '#FFF5DF',
        'danger': '#EF5B5B', 'danger_soft': '#FEEEEE',
        # Variantes de TEXTO. El propio CRM ya hace esta distincion: usa
        # --sn-success (#2FB66D) para superficies y marcadores, pero un
        # verde mas oscuro (#17864F en .badge-success) cuando el color es
        # texto. Aca hace mas falta todavia, porque el cliente lee estos
        # montos en su telefono y puede imprimirlos: #2FB66D sobre blanco
        # da 2.6:1, por debajo del minimo de WCAG incluso para texto
        # grande. Estos llegan a 4.9:1 sobre blanco y 4.5:1 sobre su propio
        # fondo suave, asi que sirven tambien dentro de los badges.
        'success_text': '#158048',
        'danger_text': '#C93636',
        'radius_sm': '8px', 'radius_md': '12px', 'radius_lg': '16px',
        'shadow_card': '0 1px 2px rgba(16, 24, 40, .03), 0 3px 10px rgba(16, 24, 40, .04)',
        'shadow_raised': '0 8px 24px rgba(16, 24, 40, .045)',
        # ------------------------------------------------------------------
        # Compatibilidad: los nombres viejos siguen existiendo porque los
        # theme_snapshot ya guardados los traen y porque Settings >
        # Cotizaciones los deja editar. Ahora apuntan al mismo ADN.
        'bg_dark': '#111827', 'cream': '#FFFFFF', 'bone': '#F7F8FC',
        'ink': '#111827', 'ink_soft': '#667085', 'line': '#E7EAF0',
        'accent': '#7357F6', 'logo_url': '', 'footer_text': '',
        'cta_text': 'ACEPTAR COTIZACIÓN', 'whatsapp': '',
        # Rediseño editorial (29-ago-2026): moneda y tipografia tambien
        # salen del theme en vez de estar escritas a mano en quote_view.html,
        # para que una cuenta futura pueda tener su propia combinacion sin
        # duplicar la plantilla. serif_font/sans_font son el valor CSS
        # font-family completo (con fallbacks); google_fonts_href es la URL
        # completa del <link> a cargar -- si una cuenta algun dia necesita
        # otra tipografia, alcanza con cambiar estos tres campos aca.
        'currency_symbol': 'Q', 'currency_label': 'Quetzales (GTQ)', 'featured_video_url': '',
        # Inter en TODO el sistema documental, sin excepciones -- tampoco
        # en el wordmark de la marca. Kevin: "si no es un logo grafico
        # real, tambien debe usar Inter; no quiero conservar serif solo
        # porque se ve elegante".
        #
        # SIN COMILLAS a proposito. Ver _css_font_stack(): una comilla en
        # este valor se convierte en &#39; al emitirse dentro del <style> y
        # rompe la variable entera. "Segoe UI" sin comillas es CSS valido.
        'sans_font': 'Inter, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif',
        'google_fonts_href': (
            'https://fonts.googleapis.com/css2?'
            'family=Inter:wght@400;450;500;600;700&display=swap'
        ),
    }
    theme = {**defaults, **theme_saved}
    theme['display_name'] = brand['display_name']
    theme['tagline'] = brand.get('tagline', '')
    theme['email'] = brand.get('email', '')
    theme['phone'] = brand.get('phone', '')
    # Precalculado aca (no en el template: Jinja no puede llamar re.search)
    # para que quote_view.html solo necesite un 'if' -- y para que quede
    # congelado dentro de theme_snapshot igual que el resto del tema.
    theme['featured_video_embed'] = _video_embed_url(theme.get('featured_video_url'))
    return theme


def _css_font_stack(valor, respaldo='Inter, system-ui, -apple-system, sans-serif'):
    """Devuelve una font-family segura para emitir DENTRO de un <style>.

    BUG ENCONTRADO (3-sep-2026). El documento se veia entero en Times pese
    a declarar Inter. La causa no era el CSS: era el escapado. Jinja
    autoescapa las comillas simples a &#39;, y dentro de un <style> las
    entidades HTML NO se decodifican -- ese contenido no es markup. Asi que
    el navegador leia

        --font-sans: Inter, ..., &#39;Segoe UI&#39;, sans-serif;

    que es un valor invalido. La variable no resolvia, `font-family:
    var(--font-sans)` se ignoraba, y todo el documento caia a la fuente por
    defecto del navegador, que es una serif. Ironicamente Inter SI se
    descargaba: el <link> de Google Fonts esta fuera del <style>, donde las
    entidades si se decodifican. Se bajaba la fuente y nunca se aplicaba.

    La solucion es no necesitar comillas: en CSS un nombre de familia con
    espacios es valido sin comillas mientras cada palabra sea un
    identificador ("Segoe UI" -> Segoe UI). Aca se quitan las comillas y se
    valida el resto con una whitelist, porque este valor es EDITABLE por el
    tenant desde Settings > Cotizaciones: sin la whitelist seria una via de
    inyeccion de CSS. Si el valor no pasa, se usa el respaldo.
    """
    texto = (valor or '').replace('"', '').replace("'", '').strip()
    if not texto:
        return respaldo
    # Letras, numeros, espacios, comas, guiones y puntos. Nada de ; { } ( )
    # ni caracteres que permitan cerrar la declaracion y escribir CSS nuevo.
    if not re.match(r'^[A-Za-z0-9 ,._-]+$', texto):
        return respaldo
    partes = [p.strip() for p in texto.split(',') if p.strip()]
    return ', '.join(partes) if partes else respaldo


def _document_theme(tenant_id, snapshot=None):
    """Tokens visuales de CUALQUIER documento de Flow CRM (cotizacion,
    factura, y lo que venga) para una cuenta.

    Sistema de documentos (29-ago-2026). Un solo lugar resuelve el tema,
    para que factura y cotizacion no puedan divergir: si manana cambia el
    primary de una marca, cambian los dos documentos a la vez.

    `snapshot` es el tema congelado dentro del registro (quote.theme_snapshot).
    Se hace merge SOBRE los defaults actuales, nunca se usa crudo: un
    snapshot guardado antes de que existieran estos tokens no los tiene, y
    usarlo tal cual dejaria variables CSS sin definir (colores rotos en una
    cotizacion vieja). Con el merge, el snapshot sigue mandando en lo que si
    guardo -- que es justo la garantia que Kevin pidio: "una cotizacion
    enviada ayer NO debe cambiar silenciosamente" -- y hereda lo demas.
    """
    base = _quote_theme_for_tenant(tenant_id)
    if not snapshot:
        return base
    tema = {**base, **{k: v for k, v in snapshot.items() if v not in (None, '')}}
    # La marca NUNCA sale del snapshot: se re-resuelve siempre desde el
    # tenant. Un snapshot es una foto de lo cosmetico, no una credencial de
    # identidad -- si alguna vez se guardo mal, no puede convertirse en la
    # marca equivocada mostrada a un cliente (el incidente de agosto).
    for campo in ('display_name', 'tagline', 'email', 'phone'):
        tema[campo] = base.get(campo, '')
    # Las font-family se sanean SIEMPRE, tanto las del default como las que
    # pudiera traer un snapshot o la config del tenant. Ver _css_font_stack.
    tema['sans_font'] = _css_font_stack(tema.get('sans_font'))
    return tema


def _snapshot_public_quote_extras(quote, tenant_id):
    """Congela portfolio/condiciones/theme dentro del quote en el momento de
    enviarlo (mismo instante en que ya se marca sent_at/status='Enviada').
    Kevin: 'si modifico la plantilla mañana, una cotizacion enviada ayer NO
    debe cambiar silenciosamente'. Si el quote ya tenia un snapshot (reenvio),
    no lo pisa -- el snapshot se toma UNA vez, la primera vez que se envia."""
    if quote.get('portfolio_snapshot') is None:
        portfolio_ids = quote.get('portfolio_ids')
        portfolio = _load_portfolio(tenant_id)
        if portfolio_ids:
            portfolio = [p for p in portfolio if p.get('id') in portfolio_ids]
        quote['portfolio_snapshot'] = portfolio
    if quote.get('terms_snapshot') is None:
        terms_template_id = quote.get('terms_template_id')
        templates = _load_terms_templates(tenant_id)
        chosen = next((t for t in templates if t.get('id') == terms_template_id), None) \
            if terms_template_id else (templates[0] if templates else None)
        quote['terms_snapshot'] = (chosen or {}).get('blocks', [])
    if quote.get('theme_snapshot') is None:
        quote['theme_snapshot'] = _quote_theme_for_tenant(tenant_id)
    return quote


@app.route('/quotes')
def quotes_list():
    """Quotes Overview: todas las cotizaciones del tenant, sin importar
    si ya se convirtieron en job o siguen ligadas a un lead."""
    quotes = list_quotes()
    clients = {c['id']: c for c in list_clients()}
    jobs = {j['id']: j for j in list_jobs()}
    leads = {l['id']: l for l in list_leads()}

    for q in quotes:
        client = clients.get(q.get('client_id'))
        q['client_name'] = f"{client['first_name']} {client['last_name']}" if client else 'Sin cliente'
        job = jobs.get(q.get('job_id'))
        lead = leads.get(q.get('lead_id'))
        q['ref_name'] = (job or {}).get('nombre') or (lead or {}).get('nombre') or '—'

    quotes.sort(key=lambda q: q.get('created') or '', reverse=True)

    total_sent = sum(1 for q in quotes if q.get('status') in ('Enviada', 'Aceptada'))
    total_accepted = sum(1 for q in quotes if q.get('status') == 'Aceptada')
    total_value = sum(coerce_amount(q.get('precio_total')) for q in quotes)

    return render_template('quotes.html', quotes=quotes,
                          total_sent=total_sent, total_accepted=total_accepted, total_value=total_value)


@app.route('/quotes/<quote_id>/diagnostico')
def quote_diagnostico(quote_id):
    """DIAGNOSTICO interno: que conceptos ve la cotizacion y que ve la
    factura, para la MISMA cotizacion, con los datos reales.

    Existe porque "la cotizacion muestra menos que la factura" no se puede
    depurar sin ver los datos: en local reproduce igual en los dos lados.
    Esto imprime las dos listas, la fuente de la que salio cada una y los
    campos crudos del quote, para poder senalar exactamente donde se
    pierde la informacion.

    Es una pagina interna (requiere sesion, como el resto de /quotes) y no
    muestra nada al cliente. Solo LEE.
    """
    quote = store.get('quotes', quote_id)
    if not quote:
        abort(404)

    snap = _snapshot_comercial(quote)
    grupos_cot = _quote_grupos_display(snap or {})
    conceptos_cot = [s['texto'] for g in grupos_cot for s in g['servicios']]

    # Las facturas que nacieron de esta cotizacion
    facturas = []
    for pago in _visible_billable_payments():
        if pago.get('quote_id') != quote_id:
            continue
        inv = pago.get('invoice_id') or pago.get('id')
        if any(f['invoice_id'] == inv for f in facturas):
            continue
        doc = _invoice_document(inv)
        if not doc:
            continue
        facturas.append({
            'invoice_id': inv,
            'fuente': doc.get('fuente_conceptos'),
            'concepto': doc.get('concepto'),
            'total': doc.get('total'),
            'conceptos': [s['texto'] for g in (doc.get('grupos') or [])
                          for s in g['servicios']],
        })

    lineas = []
    A = lineas.append
    A(f'DIAGNOSTICO DE CONCEPTOS -- cotizacion {quote_id}')
    A('=' * 72)
    A('')
    A(f"estado           : {quote.get('status')}")
    A(f"paquete_nombre   : {quote.get('paquete_nombre')!r}")
    A(f"precio_total     : {quote.get('precio_total')}")
    A(f"tenant_id        : {quote.get('tenant_id')!r}")
    A('')
    A('CAMPOS CRUDOS DEL QUOTE (de aca sale todo):')
    A(f"  snapshot_aceptado : {'SI' if quote.get('snapshot_aceptado') else 'no'}")
    A(f"  servicios (raiz)  : {len(quote.get('servicios') or [])} items")
    A(f"  groups (raiz)     : {len(quote.get('groups') or [])} grupos")
    A(f"  incluye (raiz)    : {len(quote.get('incluye') or [])} lineas")
    A(f"  options           : {len(quote.get('options') or [])}")
    for i, o in enumerate(quote.get('options') or []):
        A(f"      opcion {i} id={o.get('id')!r} name={o.get('name')!r} "
          f"servicios={len(o.get('servicios') or [])} "
          f"groups={len(o.get('groups') or [])} "
          f"incluye={len(o.get('incluye') or [])}")
    A(f"  selected_option_id: {quote.get('selected_option_id')!r}")
    A(f"  selected_extras   : {len(quote.get('selected_extras') or [])}")
    A('')
    A(f"FUENTE QUE ELIGIO _snapshot_comercial: {(snap or {}).get('fuente')}")
    A('')
    A(f'LA COTIZACION MUESTRA ({len(conceptos_cot)} conceptos):')
    for t in conceptos_cot:
        A(f'    - {t}')
    if not conceptos_cot:
        A('    (ninguno -- por eso se ve vacia)')
    A('')
    if not facturas:
        A('No hay facturas asociadas a esta cotizacion.')
    for f in facturas:
        A(f"FACTURA {f['invoice_id']} MUESTRA ({len(f['conceptos'])} conceptos):")
        A(f"    fuente   : {f['fuente']}")
        A(f"    concepto : {f['concepto']!r}")
        A(f"    total    : {f['total']}")
        for t in f['conceptos']:
            A(f'    - {t}')
        iguales = f['conceptos'] == conceptos_cot
        A('')
        A(f"    ¿COINCIDE CON LA COTIZACION? {'SI' if iguales else 'NO'}")
        if not iguales:
            solo_f = [t for t in f['conceptos'] if t not in conceptos_cot]
            solo_c = [t for t in conceptos_cot if t not in f['conceptos']]
            for t in solo_f:
                A(f'      solo en la FACTURA    : {t}')
            for t in solo_c:
                A(f'      solo en la COTIZACION : {t}')
        A('')
    from flask import Response
    return Response('\n'.join(lineas), mimetype='text/plain; charset=utf-8')


@app.route('/quotes/<quote_id>')
def quote_view(quote_id):
    """Vista de una cotizacion (que el cliente ve)."""
    quotes = store.list('quotes')
    quote = next((q for q in quotes if q.get('id') == quote_id), None)
    if not quote:
        abort(404)

    lead = get_lead(quote.get('lead_id', ''))
    if not lead and quote.get('job_id'):
        job = get_job(quote.get('job_id'))
        client = get_client(job.get('client_id', '')) if job else None
        lead = {
            'id': quote.get('lead_id') or quote.get('job_id'),
            'nombre': job.get('nombre') if job else 'Cliente',
            'email': client.get('email') if client else '',
            'telefono': client.get('phone') if client else '',
            'fecha_tentativa': job.get('boda_date') if job else '',
            'locacion': job.get('location') if job else '',
        }
    if not lead:
        abort(404)

    payment_schedule = []
    if quote.get('status') == 'Aceptada':
        payment_schedule = sorted(
            [p for p in store.list('payments') if p.get('quote_id') == quote_id],
            key=lambda p: p.get('due_date') or ''
        )
        # El due_date se guarda en ISO (2026-11-28) porque asi se ordena y
        # se compara. Pero un documento que ve el cliente no puede mostrar
        # una fecha de base de datos: se agrega el valor ya formateado y la
        # plantilla usa ese. El campo original queda intacto -- lo usan el
        # motor de pagos y el ordenamiento de arriba.
        for _p in payment_schedule:
            _p['due_date_display'] = _format_date_es(_p.get('due_date')) or _p.get('due_date') or ''

    # La cotizacion no siempre tiene su propio tenant_id en registros viejos;
    # el job (cuando existe) es la fuente mas confiable, igual que en
    # contract_view. Sin ninguno de los dos, resolve_pdf_brand devuelve el
    # placeholder neutro -- nunca "Astral Weddings" por default.
    _job_para_marca = get_job(quote.get('job_id', '')) if quote.get('job_id') else None
    _tenant_para_marca = (
        (_job_para_marca.get('tenant_id') if _job_para_marca else None)
        or quote.get('tenant_id') or lead.get('tenant_id')
    )
    brand = resolve_pdf_brand(_tenant_para_marca)

    # Public Quote Experience (28-ago-2026): theme/portfolio/condiciones
    # vienen del snapshot congelado al enviar si existe (quote ya enviado/
    # aceptado); para un Borrador que un admin esta previsualizando todavia
    # no hay snapshot, asi que se resuelve en vivo -- asi el live preview
    # (BLOQUE D) siempre refleja el catalogo actual hasta que se envia.
    # _document_theme y no el snapshot crudo: un quote enviado ANTES del
    # sistema de documentos guardo un snapshot sin los tokens nuevos, y
    # usarlo tal cual dejaba media hoja de estilos con variables vacias
    # (boton de aceptar blanco sobre blanco). El merge conserva lo que el
    # snapshot si guardo -- la garantia de "no cambia sola" sigue intacta.
    theme = _document_theme(_tenant_para_marca, quote.get('theme_snapshot'))
    portfolio = quote.get('portfolio_snapshot')
    if portfolio is None:
        portfolio = _load_portfolio(_tenant_para_marca)
    terms_blocks = quote.get('terms_snapshot')
    if terms_blocks is None:
        _templates_disponibles = _load_terms_templates(_tenant_para_marca)
        terms_blocks = (_templates_disponibles[0].get('blocks') if _templates_disponibles else []) or []

    # Base de las URLs publicas (accept/decline/pdf) segun por donde entro
    # el visitante: /q/<token>/... si vino por el link nuevo, /quotes/<id>/...
    # si vino por el alias interno viejo. Se deduce de request.path (no hace
    # falta el token en claro aca, ya se uso para resolver `quote`).
    if request.path.startswith('/q/'):
        public_base = '/q/' + request.path.split('/')[2]
    else:
        public_base = f'/quotes/{quote_id}'

    # Cada opcion llega a la plantilla con sus grupos ya resueltos (texto +
    # nombre de icono). La plantilla no clasifica ni adivina nada: pinta.
    # Es la separacion que pidio Kevin -- los datos deciden que dice el
    # documento, el renderer decide como se ve.
    opciones = _normalize_quote_options(quote)
    for _o in opciones:
        if isinstance(_o, dict):
            _o['grupos_display'] = _quote_grupos_display(_o)
    # La cotizacion ya aceptada muestra los campos planos materializados,
    # asi que su desglose se resuelve aparte, por el mismo camino.
    # Mismo snapshot que consume la factura. Si estos dos divergen, es que
    # alguien dejo de usar _snapshot_comercial en algun lado.
    grupos_aceptada = _quote_grupos_display(_snapshot_comercial(quote) or {})

    return render_template(
        'quote_view.html',
        quote=quote,
        lead=lead,
        grupos_aceptada=grupos_aceptada,
        options=opciones,
        plan_choices=_quote_plan_choices(quote),
        payment_schedule=payment_schedule,
        brand=brand,
        theme=theme,
        portfolio=portfolio,
        terms_blocks=terms_blocks,
        public_base=public_base,
        # Formateada aca porque Jinja no puede llamar _format_date_es. Puede
        # venir vacia: las cotizaciones importadas de Studio Ninja entran ya
        # aceptadas pero sin fecha de aceptacion, y el template cierra la
        # frase sin ella en vez de dejar "aceptada el ." colgando.
        estado_fecha=_format_date_es(quote.get('aceptada_en') or quote.get('rechazada_en')),
    )


@app.route('/quotes/<quote_id>/edit')
def quote_edit(quote_id):
    """Vista de administrador: armar hasta 3 opciones de paquete antes de
    enviar la cotizacion al cliente. Una vez enviada, esta pagina redirige a
    la vista publica (ya no se puede seguir editando)."""
    quote = store.get('quotes', quote_id)
    if not quote:
        abort(404)
    if quote.get('status') and quote.get('status') != 'Borrador':
        return redirect(url_for('quote_view', quote_id=quote_id))

    lead = get_lead(quote.get('lead_id', '')) if quote.get('lead_id') else None
    job = get_job(quote.get('job_id', '')) if quote.get('job_id') else None
    client = get_client(quote.get('client_id') or (job.get('client_id') if job else '')) if (quote.get('client_id') or (job and job.get('client_id'))) else None
    display_name = _client_name(client=client, lead=lead, job=job)
    display_email = _email_for(client=client, lead=lead)
    # Pagina interna con sesion: el tenant es el de la sesion, no hay que
    # deducirlo de los datos del quote/lead/job.
    brand = resolve_pdf_brand(get_current_tenant_id())

    return render_template(
        'quote_edit.html',
        quote=quote,
        options=quote.get('options') or [],
        display_name=display_name,
        display_email=display_email,
        plan_pago_opciones=quote.get('plan_pago_opciones') or [1, 2, 3, 4],
        saved_packages=_load_packages(),
        brand=brand,
        # BLOQUE D: el builder deja ELEGIR de estas librerias (armarlas es
        # Settings > Cotizaciones, BLOQUE F); pueden venir vacias todavia.
        portfolio_items=_load_portfolio(get_current_tenant_id(), only_active=False),
        terms_templates=_load_terms_templates(get_current_tenant_id()),
        extras_catalog=quote.get('extras_catalog') or [],
        selected_portfolio_ids=quote.get('portfolio_ids') or [],
        selected_terms_template_id=quote.get('terms_template_id') or '',
        # QUOTE BUILDER (3-sep-2026): el catalogo de servicios que el
        # fotografo puede agregar. Son DATOS -- tipo, etiqueta, si pide
        # cantidad y con que unidad -- agrupados por categoria. El icono, la
        # pluralizacion, el orden y la agrupacion en el documento los
        # resuelve el sistema; aca no se elige ninguno de los cuatro.
        catalogo_servicios=qsvc.catalogo_para_selector(),
    )


@app.route('/api/quotes/draft', methods=['POST'])
def api_quote_create_draft():
    """Crea una cotizacion vacia en estado Borrador (multi-opcion) y devuelve
    su id para redirigir al editor."""
    import uuid
    data = request.get_json() or {}
    lead_id = data.get('lead_id')
    job_id = data.get('job_id')
    if not lead_id and not job_id:
        return jsonify({'ok': False, 'error': 'lead_id o job_id requerido'}), 400

    lead = get_lead(lead_id) if lead_id else None
    job = get_job(job_id) if job_id else None
    tenant_id = (lead or job or {}).get('tenant_id') or get_current_tenant_id()

    quote_id = 'quote-' + uuid.uuid4().hex[:8]
    quote = {
        'id': quote_id,
        'lead_id': lead_id or (job.get('lead_id') if job else ''),
        'job_id': job_id or '',
        'client_id': (job.get('client_id') if job else '') or (lead.get('client_id') if lead else ''),
        'status': 'Borrador',
        'options': [],
        'created': date.today().isoformat(),
        'tenant_id': tenant_id,
    }
    _assign_quote_number(quote, tenant_id)
    store.upsert('quotes', quote)
    return jsonify({'ok': True, 'quote_id': quote_id, 'edit_url': f'/quotes/{quote_id}/edit'})


@app.route('/api/quotes/<quote_id>/options', methods=['POST'])
def api_quote_option_save(quote_id):
    """Agrega o actualiza una opcion de paquete (maximo 3) en una cotizacion
    en estado Borrador.

    Extendido (BLOQUE B, Public Quote Experience, 28-ago-2026) con campos
    opcionales para la vista publica premium: subtitle, description,
    precio_anterior, descuento, horas, label, order, photos, groups
    (secciones de incluidos, ej. 'Boda principal · Fotografia'). Todos
    opcionales y con default seguro -- una opcion vieja (o guardada sin
    estos campos) sigue funcionando identico. 'incluye' (plano) se sigue
    poblando siempre, ahora tambien a partir de 'groups' si se manda, para
    no romper el PDF ni ninguna vista que todavia solo sepa leer esa
    clave."""
    import uuid
    quote = store.get('quotes', quote_id)
    if not quote:
        return jsonify({'ok': False, 'error': 'Cotizacion no encontrada'}), 404
    if quote.get('status') and quote.get('status') != 'Borrador':
        return jsonify({'ok': False, 'error': 'Esta cotizacion ya fue enviada, no se puede editar'}), 400

    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'Nombre del paquete requerido'}), 400
    try:
        precio_total = float(data.get('precio_total') or 0)
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'Precio invalido'}), 400
    if precio_total <= 0:
        return jsonify({'ok': False, 'error': 'El precio debe ser mayor a 0'}), 400

    precio_anterior = data.get('precio_anterior')
    try:
        precio_anterior = float(precio_anterior) if precio_anterior not in (None, '') else None
    except (TypeError, ValueError):
        precio_anterior = None

    descuento = data.get('descuento')
    try:
        descuento = float(descuento) if descuento not in (None, '') else None
    except (TypeError, ValueError):
        descuento = None

    horas = data.get('horas')
    try:
        horas = float(horas) if horas not in (None, '') else None
    except (TypeError, ValueError):
        horas = None

    # SERVICIOS ESTRUCTURADOS (3-sep-2026). La fuente de verdad nueva: cada
    # inclusion es un dato ({'tipo': 'fotografos', 'cantidad': 2}), no un
    # string. De ahi salen el icono, la pluralizacion, la agrupacion y el
    # orden, sin que nadie tenga que elegirlos. `groups` e `incluye` se
    # DERIVAN de aca, asi que el PDF y cualquier vista vieja siguen leyendo
    # lo mismo de siempre y no se enteran del cambio.
    servicios = qsvc.normalizar_servicios(data.get('servicios'))

    groups = data.get('groups')
    if not isinstance(groups, list):
        groups = []
    groups = [{
        'title': (g.get('title') or '').strip(),
        'items': [str(x).strip() for x in (g.get('items') or []) if str(x).strip()],
    } for g in groups if isinstance(g, dict)]
    if servicios:
        # Los servicios mandan sobre los grupos escritos a mano: si vienen
        # ambos, `groups` se regenera para que no puedan contradecirse.
        groups = qsvc.derivar_groups(servicios)

    incluye = data.get('incluye')
    if isinstance(incluye, str):
        incluye = [line.strip() for line in incluye.split('\n') if line.strip()]
    incluye = incluye or []
    if servicios:
        incluye = qsvc.derivar_incluye(servicios)
    elif groups:
        # Groups es la fuente estructurada nueva; si se manda, 'incluye'
        # (plano) se deriva de ahi para que PDF/vistas viejas sigan
        # mostrando lo mismo sin tener que saber que existen los grupos.
        incluye = _flatten_option_groups(groups)

    photos = data.get('photos')
    if not isinstance(photos, list):
        photos = []
    photos = [str(p).strip() for p in photos if str(p).strip()]

    products = data.get('products')
    if not isinstance(products, list):
        products = []

    options = quote.get('options') or []
    option_id = data.get('id')
    option = {
        'id': option_id or ('opt-' + uuid.uuid4().hex[:6]),
        'name': name,
        'subtitle': (data.get('subtitle') or '').strip(),
        'description': (data.get('description') or '').strip(),
        'precio_total': precio_total,
        'precio_anterior': precio_anterior,
        'descuento': descuento,
        'horas': horas,
        'label': (data.get('label') or '').strip(),
        'order': data.get('order') if isinstance(data.get('order'), int) else len(options),
        'incluye': incluye,
        'groups': groups,
        'servicios': servicios,
        'products': products,
        'photos': photos,
        'notas': data.get('notas') or '',
    }
    existing_idx = next((i for i, o in enumerate(options) if o.get('id') == option_id), None) if option_id else None
    if existing_idx is not None:
        options[existing_idx] = option
    else:
        if len(options) >= 3:
            return jsonify({'ok': False, 'error': 'Maximo 3 opciones de paquete por cotizacion'}), 400
        options.append(option)

    quote['options'] = options
    store.upsert('quotes', quote)
    return jsonify({'ok': True, 'options': options})


@app.route('/api/quotes/<quote_id>/options/<option_id>', methods=['DELETE'])
def api_quote_option_delete(quote_id, option_id):
    quote = store.get('quotes', quote_id)
    if not quote:
        return jsonify({'ok': False, 'error': 'Cotizacion no encontrada'}), 404
    if quote.get('status') and quote.get('status') != 'Borrador':
        return jsonify({'ok': False, 'error': 'Esta cotizacion ya fue enviada, no se puede editar'}), 400

    options = [o for o in (quote.get('options') or []) if o.get('id') != option_id]
    quote['options'] = options
    store.upsert('quotes', quote)
    return jsonify({'ok': True, 'options': options})


@app.route('/api/quotes/<quote_id>/payment-options', methods=['POST'])
def api_quote_payment_options(quote_id):
    """Guarda en cuantas cuotas se le puede ofrecer pagar al cliente (lo que
    ve como botones en /quotes/<id> al aceptar)."""
    quote = store.get('quotes', quote_id)
    if not quote:
        return jsonify({'ok': False, 'error': 'Cotizacion no encontrada'}), 404
    if quote.get('status') and quote.get('status') != 'Borrador':
        return jsonify({'ok': False, 'error': 'Esta cotizacion ya fue enviada, no se puede editar'}), 400

    data = request.json or request.form or {}
    raw = data.get('plan_pago_opciones') or []
    try:
        opciones = sorted(set(int(n) for n in raw if int(n) > 0))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'Valores invalidos'}), 400
    if not opciones:
        return jsonify({'ok': False, 'error': 'Selecciona al menos una opcion de pago'}), 400

    quote['plan_pago_opciones'] = opciones
    store.upsert('quotes', quote)
    return jsonify({'ok': True, 'plan_pago_opciones': opciones})


@app.route('/api/quotes/<quote_id>/extras', methods=['POST'])
def api_quote_extras_save(quote_id):
    """Guarda el catalogo de extras ofrecidos en esta cotizacion (BLOQUE B,
    Public Quote Experience). Solo el catalogo -- que el cliente elige y
    cuanto suma al total se resuelve y valida en BLOQUE E, siempre en
    backend, nunca confiando en lo que mande el navegador (mismo principio
    que ya usa quote_accept con cuota_monto)."""
    quote = store.get('quotes', quote_id)
    if not quote:
        return jsonify({'ok': False, 'error': 'Cotizacion no encontrada'}), 404
    if quote.get('status') and quote.get('status') != 'Borrador':
        return jsonify({'ok': False, 'error': 'Esta cotizacion ya fue enviada, no se puede editar'}), 400

    data = request.get_json() or {}
    raw = data.get('extras')
    if not isinstance(raw, list):
        return jsonify({'ok': False, 'error': 'Se espera {"extras": [...]}'}), 400

    import math
    import uuid
    catalog = []
    ids_usados = set()
    for e in raw:
        if not isinstance(e, dict):
            continue
        name = (e.get('name') or '').strip()
        if not name:
            continue
        try:
            price = float(e.get('price') or 0)
        except (TypeError, ValueError):
            price = 0.0
        # price < 0 no atrapa inf/-inf/nan (las comparaciones con nan
        # siempre dan False) -- si no es un numero finito y normal, se
        # descarta al mismo default seguro que un precio invalido.
        if not math.isfinite(price) or price < 0:
            price = 0.0
        item_id = e.get('id') or ('extra-' + uuid.uuid4().hex[:6])
        if item_id in ids_usados:
            # Un id repetido en el mismo guardado sumaria dos veces al
            # aceptar (quote_accept suma por cada match de id) -- se
            # descarta el duplicado en vez de dejarlo persistir.
            continue
        ids_usados.add(item_id)
        catalog.append({
            'id': item_id,
            'name': name,
            'description': (e.get('description') or '').strip(),
            'price': price,
        })

    quote['extras_catalog'] = catalog
    store.upsert('quotes', quote)
    return jsonify({'ok': True, 'extras_catalog': catalog})


@app.route('/api/quotes/<quote_id>/presentation', methods=['POST'])
def api_quote_presentation_save(quote_id):
    """Guarda que portfolio_items y que quote_terms_template usa ESTA
    cotizacion (BLOQUE D, Quote Builder). No crea ni edita los items de la
    libreria -- eso es Settings > Cotizaciones (BLOQUE F); esto solo
    selecciona cuales de los que ya existen se muestran aca. Mismo momento
    en que se congelan (quote_view/_snapshot_public_quote_extras ya sabe
    leer portfolio_ids/terms_template_id, ver BLOQUE B) -- por eso no hace
    falta tocar esas funciones para que esta seleccion tenga efecto."""
    quote = store.get('quotes', quote_id)
    if not quote:
        return jsonify({'ok': False, 'error': 'Cotizacion no encontrada'}), 404
    if quote.get('status') and quote.get('status') != 'Borrador':
        return jsonify({'ok': False, 'error': 'Esta cotizacion ya fue enviada, no se puede editar'}), 400

    data = request.get_json() or {}
    portfolio_ids = data.get('portfolio_ids')
    if not isinstance(portfolio_ids, list):
        portfolio_ids = []
    portfolio_ids = [str(x) for x in portfolio_ids if str(x).strip()]

    # Solo se guardan ids que de verdad existen en el portfolio de esta
    # cuenta -- si el navegador manda basura (o un id de otra cuenta) se
    # descarta en vez de guardarse tal cual.
    valid_ids = {p['id'] for p in _load_portfolio(get_current_tenant_id(), only_active=False)}
    portfolio_ids = [pid for pid in portfolio_ids if pid in valid_ids]

    terms_template_id = (data.get('terms_template_id') or '').strip()
    if terms_template_id:
        valid_terms_ids = {t['id'] for t in _load_terms_templates(get_current_tenant_id())}
        if terms_template_id not in valid_terms_ids:
            terms_template_id = ''

    quote['portfolio_ids'] = portfolio_ids
    quote['terms_template_id'] = terms_template_id
    store.upsert('quotes', quote)
    return jsonify({'ok': True, 'portfolio_ids': portfolio_ids, 'terms_template_id': terms_template_id})


# ============================================================
# PUBLIC QUOTE EXPERIENCE -- catalogos por cuenta (portfolio, condiciones,
# templates). CRUD simple, tenant-scoped por la sesion activa como
# cualquier otra tabla (store.upsert/list/delete ya aislan). Sin token de
# admin: son datos de configuracion de la propia cuenta logueada, mismo
# nivel de confianza que api_quote_option_save. La UI que los administra
# (Settings > Quotes) es BLOQUE D/F -- esto deja el contrato de datos listo.
# ============================================================

@app.route('/api/portfolio', methods=['GET'])
def api_portfolio_list():
    return jsonify({'ok': True, 'items': _load_portfolio(only_active=False)})


@app.route('/api/portfolio', methods=['POST'])
def api_portfolio_save():
    import uuid
    data = request.get_json() or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'ok': False, 'error': 'Titulo requerido'}), 400
    item_id = data.get('id') or ('pf-' + uuid.uuid4().hex[:8])
    existing = store.get('portfolio_items', item_id) if data.get('id') else None
    item = {
        'id': item_id,
        'title': title,
        'couple_names': (data.get('couple_names') or '').strip(),
        'location': (data.get('location') or '').strip(),
        'image_url': (data.get('image_url') or '').strip(),
        'external_url': (data.get('external_url') or '').strip(),
        'password': (data.get('password') or '').strip(),
        'blurb': (data.get('blurb') or '').strip(),
        'order': data.get('order') if isinstance(data.get('order'), int) else (existing or {}).get('order', 0),
        'active': data.get('active') if isinstance(data.get('active'), bool) else True,
    }
    store.upsert('portfolio_items', item)
    return jsonify({'ok': True, 'item': item})


@app.route('/api/portfolio/<item_id>', methods=['DELETE'])
def api_portfolio_delete(item_id):
    ok = store.delete('portfolio_items', item_id)
    return jsonify({'ok': ok})


@app.route('/api/quote-terms-templates', methods=['GET'])
def api_quote_terms_list():
    return jsonify({'ok': True, 'items': _load_terms_templates()})


@app.route('/api/quote-terms-templates', methods=['POST'])
def api_quote_terms_save():
    """Guarda un template de condiciones: {title, blocks: [{title, body}]}.
    'blocks' es la lista de secciones (Cobertura, Entrega, Hora adicional,
    Disponibilidad...) que Kevin pidio poder reutilizar y editar libremente."""
    import uuid
    data = request.get_json() or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'ok': False, 'error': 'Titulo requerido'}), 400
    blocks = data.get('blocks')
    if not isinstance(blocks, list):
        blocks = []
    blocks = [{
        'title': (b.get('title') or '').strip(),
        'body': (b.get('body') or '').strip(),
    } for b in blocks if isinstance(b, dict) and (b.get('title') or b.get('body'))]

    item_id = data.get('id') or ('terms-' + uuid.uuid4().hex[:8])
    existing = store.get('quote_terms_templates', item_id) if data.get('id') else None
    item = {
        'id': item_id,
        'title': title,
        'blocks': blocks,
        'order': data.get('order') if isinstance(data.get('order'), int) else (existing or {}).get('order', 0),
        'active': data.get('active') if isinstance(data.get('active'), bool) else True,
    }
    store.upsert('quote_terms_templates', item)
    return jsonify({'ok': True, 'item': item})


@app.route('/api/quote-terms-templates/<item_id>', methods=['DELETE'])
def api_quote_terms_delete(item_id):
    ok = store.delete('quote_terms_templates', item_id)
    return jsonify({'ok': ok})


@app.route('/api/quote-templates', methods=['GET'])
def api_quote_templates_list():
    return jsonify({'ok': True, 'items': _load_quote_templates()})


@app.route('/api/quote-templates', methods=['POST'])
def api_quote_templates_save():
    """Template de cotizacion (Boda, Boda foto+video, Civil, XV anios...):
    pre-arma opciones + plan de pago + condiciones default. Al usarlo desde
    el builder (BLOQUE D) se copian como snapshot editable dentro del quote
    nuevo -- modificar el template despues no altera cotizaciones ya
    creadas con el (mismo patron que _snapshot_public_quote_extras)."""
    import uuid
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'Nombre requerido'}), 400
    options = data.get('options')
    if not isinstance(options, list):
        options = []

    item_id = data.get('id') or ('qt-' + uuid.uuid4().hex[:8])
    existing = store.get('quote_templates', item_id) if data.get('id') else None
    item = {
        'id': item_id,
        'name': name,
        'description': (data.get('description') or '').strip(),
        'options': options,
        'plan_pago_opciones': data.get('plan_pago_opciones') or [1, 2, 3, 4],
        'terms_template_id': data.get('terms_template_id') or '',
        'order': data.get('order') if isinstance(data.get('order'), int) else (existing or {}).get('order', 0),
        'active': data.get('active') if isinstance(data.get('active'), bool) else True,
    }
    store.upsert('quote_templates', item)
    return jsonify({'ok': True, 'item': item})


@app.route('/api/quote-templates/<item_id>', methods=['DELETE'])
def api_quote_templates_delete(item_id):
    ok = store.delete('quote_templates', item_id)
    return jsonify({'ok': ok})


@app.route('/api/quotes/draft-from-template', methods=['POST'])
def api_quote_create_from_template():
    """Crea un Borrador multi-opcion a partir de un quote_template: copia
    options/plan_pago_opciones/terms_template_id como snapshot editable
    (dict independiente, no una referencia) para que editar el template
    despues no cambie este quote ya creado."""
    import copy
    import uuid
    data = request.get_json() or {}
    lead_id = data.get('lead_id')
    job_id = data.get('job_id')
    template_id = data.get('template_id')
    if not lead_id and not job_id:
        return jsonify({'ok': False, 'error': 'lead_id o job_id requerido'}), 400
    if not template_id:
        return jsonify({'ok': False, 'error': 'template_id requerido'}), 400

    template = store.get('quote_templates', template_id)
    if not template:
        return jsonify({'ok': False, 'error': 'Template no encontrado'}), 404

    lead = get_lead(lead_id) if lead_id else None
    job = get_job(job_id) if job_id else None
    tenant_id = (lead or job or {}).get('tenant_id') or get_current_tenant_id()

    quote_id = 'quote-' + uuid.uuid4().hex[:8]
    quote = {
        'id': quote_id,
        'lead_id': lead_id or (job.get('lead_id') if job else ''),
        'job_id': job_id or '',
        'client_id': (job.get('client_id') if job else '') or (lead.get('client_id') if lead else ''),
        'status': 'Borrador',
        'options': copy.deepcopy(template.get('options') or []),
        'plan_pago_opciones': list(template.get('plan_pago_opciones') or [1, 2, 3, 4]),
        'terms_template_id': template.get('terms_template_id') or '',
        'quote_template_id': template_id,
        'created': date.today().isoformat(),
        'tenant_id': tenant_id,
    }
    _assign_quote_number(quote, tenant_id)
    store.upsert('quotes', quote)
    return jsonify({'ok': True, 'quote_id': quote_id, 'edit_url': f'/quotes/{quote_id}/edit'})


@app.route('/quotes/<quote_id>/accept', methods=['POST'])
def quote_accept(quote_id):
    """Vista publica: el cliente acepta la cotizacion. Si la cotizacion tiene
    varias opciones de paquete, el cliente eligio una (option_id) y una
    cantidad de cuotas (plan_pago); las "materializamos" en los campos
    planos de siempre para no tener que tocar la logica de creacion de
    job/pagos, que sigue funcionando igual que antes."""
    quotes = store.list('quotes')
    quote = next((q for q in quotes if q.get('id') == quote_id), None)
    if not quote:
        abort(404)
    brand = resolve_pdf_brand(quote.get('tenant_id'))
    # Mismo tema/URL-base que quote_view (BLOQUE C) para que la confirmacion
    # se vea igual de premium que la cotizacion que el cliente acaba de
    # aceptar, y para que "Ver cotizacion" seguido apunte a /q/<token> si
    # entro por ahi. Puramente de presentacion: no toca ninguna rama de
    # decision de aca abajo (idempotencia, conversion, pagos).
    # Mismo merge que quote_view (ver comentario alla): el snapshot viejo no
    # trae los tokens del sistema de documentos.
    theme = _document_theme(quote.get('tenant_id'), quote.get('theme_snapshot'))
    if request.path.startswith('/q/'):
        public_base = '/q/' + request.path.split('/')[2]
    else:
        public_base = f'/quotes/{quote_id}'

    if quote.get('status') != 'Aceptada':
        data = request.get_json(silent=True) or request.form or {}
        option_id = data.get('option_id')
        try:
            selected_plan = int(data.get('plan_pago')) if data.get('plan_pago') else None
        except (TypeError, ValueError):
            selected_plan = None

        options = _normalize_quote_options(quote)
        chosen = next((o for o in options if o.get('id') == option_id), None)
        if not chosen and len(options) == 1:
            chosen = options[0]
        if not chosen:
            return redirect(url_for('quote_view', quote_id=quote_id) + '?error=select_option')
        if not selected_plan:
            selected_plan = int(quote.get('plan_pago') or 1)

        # Hardening (revision BLOQUE G, no cambia ninguna decision de
        # negocio): plan_pago viene de un form PUBLICO sin login. Antes de
        # esto, un plan_pago negativo o gigante (ej. 5000000) llegaba tal
        # cual hasta _ensure_payments_for_quote, que SI limita el minimo
        # (max(..., 1)) pero no el maximo -- un valor absurdo intentaria
        # generar esa cantidad de filas de pago en un solo request, sin
        # autenticacion. Se valida contra las cuotas que el admin
        # realmente ofrecio (quote.plan_pago_opciones, armadas en el
        # builder); si no hay lista (cotizacion vieja) o el valor no esta
        # en ella, se descarta en silencio y se usa el default de siempre
        # -- mismo criterio no-error-solo-ignora que ya usa extra_ids.
        opciones_de_plan_validas = quote.get('plan_pago_opciones') or []
        if opciones_de_plan_validas:
            if selected_plan not in opciones_de_plan_validas:
                selected_plan = int(quote.get('plan_pago') or opciones_de_plan_validas[0])
        elif not (1 <= selected_plan <= 24):
            selected_plan = int(quote.get('plan_pago') or 1)

        # BLOQUE E: agregados opcionales que el cliente marco (BLOQUE C ya
        # los manda como extra_ids, coma-separado, en el mismo form que
        # option_id/plan_pago). El precio SIEMPRE sale del catalogo server
        # side (quote.extras_catalog, congelado desde BLOQUE B) -- lo unico
        # que se confia del cliente es CUALES eligio, nunca cuanto cuestan;
        # cualquier id que no exista en el catalogo de esta cotizacion se
        # ignora en silencio en vez de aceptarse tal cual.
        raw_extra_ids = data.get('extra_ids') or ''
        if isinstance(raw_extra_ids, str):
            requested_extra_ids = [x.strip() for x in raw_extra_ids.split(',') if x.strip()]
        elif isinstance(raw_extra_ids, list):
            requested_extra_ids = [str(x).strip() for x in raw_extra_ids if str(x).strip()]
        else:
            requested_extra_ids = []
        # dict.fromkeys en vez de un set: descarta ids repetidos (un
        # checkbox marcado dos veces en el mismo POST no debe sumar dos
        # veces) preservando el orden en que se recibieron.
        requested_extra_ids = list(dict.fromkeys(requested_extra_ids))
        extras_catalog = quote.get('extras_catalog') or []
        # dict por id, no una lista filtrada: api_quote_extras_save ya
        # rechaza ids repetidos al guardar, pero un catalogo viejo
        # (guardado antes de esa validacion) no se vuelve a limpiar solo
        # -- de esta forma un id duplicado no puede sumarse dos veces aca
        # tampoco, sin importar cuantas veces aparezca en la lista.
        extras_por_id = {e.get('id'): e for e in extras_catalog if e.get('id')}
        selected_extras = [extras_por_id[eid] for eid in requested_extra_ids if eid in extras_por_id]
        extras_total = sum(float(e.get('price') or 0) for e in selected_extras)

        base_price = float(chosen.get('precio_total') or 0)
        quote['selected_option_id'] = chosen.get('id')
        quote['paquete_nombre'] = chosen.get('name')
        quote['paquete_precio_base'] = base_price
        quote['selected_extras'] = selected_extras
        quote['extras_total'] = extras_total
        quote['precio_total'] = base_price + extras_total
        quote['incluye'] = chosen.get('incluye')
        quote['items'] = chosen.get('items', [])
        # SNAPSHOT COMERCIAL (3-sep-2026). Antes aca solo se congelaban
        # `incluye` (strings) e `items`, pero NO los `servicios`
        # estructurados ni los `groups` de la opcion elegida. Como la
        # cotizacion y la factura leen esos campos desde la RAIZ del quote,
        # y la raiz podia tener los grupos de otra opcion (o ninguno), los
        # dos documentos podian mostrar desgloses distintos del mismo
        # acuerdo. Ahora se congela lo que el cliente acepto, completo:
        # lo que se cotizo = lo que se acepto = lo que se factura.
        quote['servicios'] = chosen.get('servicios') or []
        quote['groups'] = chosen.get('groups') or []
        quote['snapshot_aceptado'] = {
            'option_id': chosen.get('id'),
            'name': chosen.get('name'),
            'subtitle': chosen.get('subtitle') or '',
            'description': chosen.get('description') or '',
            'servicios': chosen.get('servicios') or [],
            'groups': chosen.get('groups') or [],
            'incluye': chosen.get('incluye') or [],
            'precio_base': base_price,
            'extras': selected_extras,
            'extras_total': extras_total,
            'total': base_price + extras_total,
            'plan_pago': selected_plan,
            'aceptado_en': date.today().isoformat(),
        }
        quote['selected_plan_pago'] = selected_plan
        quote['plan_pago'] = selected_plan
        quote['cuota_monto'] = round(float(quote.get('precio_total') or 0) / selected_plan, 2)
        store.upsert('quotes', quote)

    if quote.get('status') == 'Aceptada':
        if quote.get('job_id'):
            _accept_quote_for_existing_job(quote)
        quote = store.get('quotes', quote_id) or quote
        return render_template('quote_accepted.html', quote=quote, already=True, brand=brand,
                                theme=theme, public_base=public_base,
                                portal_url=(f"/portal/{quote['client_id']}" if quote.get('client_id') else None))

    if quote.get('job_id'):
        _accept_quote_for_existing_job(quote)
        quote = store.get('quotes', quote_id) or quote
        return render_template('quote_accepted.html', quote=quote, already=False, brand=brand,
                                theme=theme, public_base=public_base,
                                portal_url=(f"/portal/{quote['client_id']}" if quote.get('client_id') else None))

    if not quote.get('lead_id'):
        quote['status'] = 'Aceptada'
        quote['aceptada_en'] = date.today().isoformat()
        store.upsert('quotes', quote)
        return render_template('quote_accepted.html', quote=quote, already=False, brand=brand,
                                theme=theme, public_base=public_base, portal_url=None)

    lead = get_lead(quote.get('lead_id', ''))
    if not lead:
        abort(404)

    _convert_lead_to_job(lead, quote=quote, status='Confirmado', create_payments=True)
    quote = store.get('quotes', quote_id) or quote
    return render_template('quote_accepted.html', quote=quote, already=False, brand=brand,
                            theme=theme, public_base=public_base,
                            portal_url=(f"/portal/{quote['client_id']}" if quote.get('client_id') else None))


@app.route('/quotes/<quote_id>/decline', methods=['POST'])
def quote_decline(quote_id):
    """Vista publica: el cliente rechaza la cotizacion."""
    quotes = store.list('quotes')
    quote = next((q for q in quotes if q.get('id') == quote_id), None)
    if not quote:
        abort(404)

    if quote.get('status') != 'Aceptada':
        quote['status'] = 'Rechazada'
        quote['rechazada_en'] = date.today().isoformat()
        store.upsert('quotes', quote)

    # Si el cliente entro por el link nuevo (/q/<token>), que el rechazo lo
    # deje ahi -- no en el alias interno /quotes/<id>. Solo cambia el
    # destino del redirect, no la logica de arriba.
    if request.path.startswith('/q/'):
        return redirect('/q/' + request.path.split('/')[2])
    return redirect(url_for('quote_view', quote_id=quote_id))


@app.route('/api/quotes/<quote_id>/send', methods=['POST'])
def api_quote_send(quote_id):
    """Envia la cotizacion por email al cliente."""
    from src.mail_tracker import get_tracker

    quotes = store.list('quotes')
    quote = next((q for q in quotes if q.get('id') == quote_id), None)
    if not quote:
        return jsonify({'ok': False, 'error': 'Cotizacion no encontrada'}), 404

    if quote.get('status') == 'Borrador' and not quote.get('options'):
        return jsonify({'ok': False, 'error': 'Agrega al menos 1 opcion de paquete antes de enviar'}), 400

    lead = get_lead(quote.get('lead_id', ''))
    job = get_job(quote.get('job_id', '')) if quote.get('job_id') else None
    client = get_client(quote.get('client_id') or (job or {}).get('client_id', '')) if (quote.get('client_id') or (job or {}).get('client_id')) else None
    if not lead:
        lead = {
            'id': quote.get('lead_id') or quote.get('job_id') or quote_id,
            'nombre': _client_name(client=client, job=job),
            'email': _email_for(client=client),
        }

    to_email = _email_for(client=client, lead=lead)
    if not to_email:
        return jsonify({'ok': False, 'error': 'Esta cotizacion no tiene email de cliente'}), 400

    tenant_id_for_send = quote.get('tenant_id') or (job or {}).get('tenant_id')

    # Public Quote Experience (28-ago-2026): el link que se manda ahora usa
    # un token seguro (src/public_tokens.py) en vez del id interno -- se
    # emite uno NUEVO en cada envio (rotando el anterior, si habia), porque
    # el token en claro solo existe en el momento de emitirlo y no se puede
    # recuperar despues para reusarlo en un reenvio. /quotes/<id> se deja
    # intacto como alias interno: enlaces ya enviados antes de este cambio
    # siguen funcionando exactamente igual.
    host = request.host_url.rstrip('/')
    token_claro, quote = public_tokens.emitir_para(quote)
    quote_url = host + f'/q/{token_claro}'

    # Congela portfolio/condiciones/theme en el mismo instante en que la
    # cotizacion pasa a ser "la version que ve el cliente" -- editar el
    # catalogo/template/branding despues no debe cambiarla silenciosamente.
    _snapshot_public_quote_extras(quote, tenant_id_for_send)

    data = request.json or request.form or {}
    empresa = _brand_display_name_for_tenant(tenant_id_for_send)
    subject = (data.get('subject') or '').strip() or f'Cotizacion {quote.get("number") or quote_id} - {empresa}'
    body = (data.get('body') or '').strip() or (
        f"Hola {lead.get('nombre') or 'Cliente'},\n\n"
        "Tu cotizacion esta lista. Puedes verla y aceptarla en este enlace:\n"
        f"{quote_url}\n\n"
        f"Saludos,\n{empresa}"
    )
    # quote_edit.html (BLOQUE D) no puede mostrar el link real en la vista
    # previa del mensaje -- el token recien se emite arriba, en este mismo
    # request. Manda el mensaje con un marcador [[QUOTE_LINK]] en su lugar;
    # se reemplaza aca por la URL real de ESTE envio, tanto si el admin
    # dejo la plantilla tal cual como si la edito y el marcador sigue en
    # el texto. Antes de este reemplazo, un envio sin editar el mensaje
    # salia con el link interno viejo (/quotes/<id>) en vez de /q/<token>.
    body = body.replace('[[QUOTE_LINK]]', quote_url)
    # STAGE 2 (agosto 2026): ya no se entrega de inmediato -- se pone en la
    # cola de aprobacion (mail_tracker.queue_email) y espera a que alguien
    # la revise y apruebe en /emails. El status de la cotizacion se sigue
    # marcando 'Enviada' aca abajo (Kevin ya tomo la accion de mandarla), la
    # verdad de si el correo salio de verdad vive en /emails.
    mail = get_tracker().queue_email(
        to_email=to_email,
        subject=subject,
        body=body,
        lead_id=lead.get('id'),
        job_id=quote.get('job_id'),
        client_id=(client or {}).get('id'),
        attachments=[],
        tenant_id=tenant_id_for_send,
        source='manual:quote-send',
        idempotency_key=f'quote:{quote_id}:send:{_idempotency_minute_bucket()}',
    )

    # Marcar como enviada
    quote['sent_at'] = datetime.now().isoformat()[:10]
    quote['status'] = 'Enviada'
    store.upsert('quotes', quote)

    return jsonify({
        'ok': True,
        'quote_id': quote_id,
        'mail_id': mail.get('id'),
        'delivery_status': mail.get('status'),
        'mail_warning': _mail_delivery_warning(mail),
        'email': to_email,
        'quote_url': quote_url,
        'message': (f'Cotizacion puesta en cola de aprobacion para {to_email} (revisa /emails)'
                    if mail.get('status') == 'pending'
                    else f'Cotizacion NO se pudo poner en cola: {mail.get("blocked_reason") or "motivo no especificado"}')
    })


# ============================================================
# PUBLIC QUOTE EXPERIENCE -- /q/<token> (28-ago-2026)
# ============================================================
# El token se resuelve UNA vez aca (via public_tokens.buscar_por_token,
# sobre store.list('quotes'), que para este momento de la peticion YA esta
# aislado a la cuenta duena del token -- _resolve_public_tenant() lo fijo
# en @app.before_request antes de que este codigo corra). Con el quote_id
# resuelto, cada ruta llama DIRECTO a la funcion de siempre (quote_view,
# quote_accept, quote_decline, quote_pdf) -- no se reimplementa nada de
# accept_quote ni de las vistas: esto solo cambia COMO se llega al mismo
# codigo, nunca que hace ese codigo.
def _resolve_quote_by_token(token):
    return public_tokens.buscar_por_token(store.list('quotes'), token)


@app.route('/q/<token>')
def public_quote_view(token):
    quote = _resolve_quote_by_token(token)
    if not quote:
        abort(404)
    return quote_view(quote['id'])


@app.route('/q/<token>/accept', methods=['POST'])
def public_quote_accept(token):
    quote = _resolve_quote_by_token(token)
    if not quote:
        abort(404)
    return quote_accept(quote['id'])


@app.route('/q/<token>/decline', methods=['POST'])
def public_quote_decline(token):
    quote = _resolve_quote_by_token(token)
    if not quote:
        abort(404)
    return quote_decline(quote['id'])


@app.route('/q/<token>/pdf')
def public_quote_pdf(token):
    quote = _resolve_quote_by_token(token)
    if not quote:
        abort(404)
    return quote_pdf(quote['id'])


# ============================================================
# FACTURA PUBLICA -- /i/<token> (29-ago-2026)
# ============================================================
# Mismo mecanismo de enlace seguro que /q/<token> (src/public_tokens.py):
# el token viaja en la URL, en la base solo vive su hash. Se emite una vez,
# al mandar la factura, y se guarda en TODAS las filas de pago de esa
# factura -- una "factura" en este CRM es el conjunto de payments que
# comparten invoice_id, no un registro propio, asi que el token tiene que
# vivir donde vive la factura.
#
# Antes de esto el cliente solo podia recibir un PDF o el portal completo.
# Kevin: "no quiero que la experiencia principal sea un archivo PDF".

def _emitir_token_de_factura(invoice_id, tenant_id=None, *, rotar=False):
    """Emite (o reemplaza) el enlace publico de una factura.

    Devuelve el token en claro, que solo existe en este momento: en la base
    queda unicamente su hash, igual que /q/<token>. Se guarda en TODAS las
    filas de la factura porque una factura ES ese conjunto de filas.

    OJO con rotar: emitir un token nuevo MATA el anterior (en la base solo
    hay hashes, el viejo no se puede recuperar). Por eso rotar es explicito
    y solo lo pide el ENVIO -- mirar la factura o previsualizar el correo
    nunca rota, que era el riesgo real: dejar muerto de un vistazo el
    enlace que el cliente ya tiene.

    Al reenviar SI se rota, igual que hace api_quote_send con la cotizacion
    (public_tokens.emitir_para en cada envio): la regla del producto es "el
    ultimo correo enviado es el que tiene el enlace bueno". Consecuencia a
    tener presente con la cola de aprobacion de STAGE 2: si se encolan dos
    envios de la misma factura y se aprueba el mas viejo, ese llevara un
    enlace ya rotado. Lo mismo aplica hoy a las cotizaciones; queda
    anotado como deuda conocida, no es algo que introduzca esta ruta.
    """
    filas = [p for p in _visible_billable_payments(tenant_id)
             if (p.get('invoice_id') or p.get('id')) == invoice_id]
    if not filas:
        return None
    if not rotar and any(p.get('public_token_hash') for p in filas):
        return None
    token = public_tokens.generar_token()
    hash_token = public_tokens.hash_token(token)
    for fila in filas:
        fila['public_token_hash'] = hash_token
        store.upsert('payments', fila)
    return token


def _resolve_invoice_by_token(token):
    fila = public_tokens.buscar_por_token(store.list('payments'), token)
    if not fila:
        return None
    return fila.get('invoice_id') or fila.get('id')


@app.route('/i/<token>')
def public_invoice_view(token):
    """Factura web que ve el CLIENTE. Solo lectura: ninguna accion interna
    (editar vencimiento, generar links de cobro) existe en esta pagina."""
    invoice_id = _resolve_invoice_by_token(token)
    if not invoice_id:
        abort(404)
    doc = _invoice_document(invoice_id)
    if not doc:
        abort(404)

    tenant_id = ((doc['job'] or {}).get('tenant_id')
                 or (doc['selected'] or {}).get('tenant_id'))
    theme = _document_theme(tenant_id)
    terms = _load_terms_templates(tenant_id) if tenant_id else []
    # El PDF solo se ofrece si de verdad se puede generar: invoice_pdf()
    # exige invoice_id + job + client y si no, aborta con 404. Mostrar el
    # boton igual seria darle al cliente un enlace roto.
    puede_pdf = bool(doc['selected'].get('invoice_id') and doc['job'] and doc['client'])
    return render_template(
        'invoice_document.html',
        doc=doc, theme=theme, publico=True,
        terms_blocks=(terms[0].get('blocks') if terms else []) or [],
        pdf_url=(f'/i/{token}/pdf' if puede_pdf else None),
        pay_url=(doc['selected'] or {}).get('payment_link_url') or None,
    )


@app.route('/i/<token>/pdf')
def public_invoice_pdf(token):
    invoice_id = _resolve_invoice_by_token(token)
    if not invoice_id:
        abort(404)
    return invoice_pdf(invoice_id)


# ============================================================
# PDF GENERATION (reportlab)
# ============================================================
import sys
sys.path.insert(0, os.path.dirname(__file__))
from src.pdf_generator import generate_quote_pdf, generate_contract_pdf, generate_invoice_pdf, contract_terms, resolve_pdf_brand
from src.pdf_invoice import render_invoice_pdf
from src.pdf_contract import render_contract_pdf
import src.quote_services as qsvc


@app.route('/quotes/<quote_id>/pdf')
def quote_pdf(quote_id):
    """Descarga el PDF de la cotizacion."""
    from flask import Response
    quotes = store.list('quotes')
    quote = next((q for q in quotes if q.get('id') == quote_id), None)
    if not quote:
        abort(404)

    lead = get_lead(quote.get('lead_id', ''))
    if not lead:
        # Cotizaciones de jobs ya confirmados no siempre tienen un lead real
        # asociado (el lead_id puede ser un id historico sin registro) --
        # armamos los datos del cliente desde el job/client igual que
        # quote_view() para no romper la descarga del PDF.
        job = get_job(quote.get('job_id', '')) if quote.get('job_id') else None
        client = get_client(quote.get('client_id') or (job or {}).get('client_id', '')) if (quote.get('client_id') or (job or {}).get('client_id')) else None
        if not job and not client:
            abort(404)
        lead = {
            'id': quote.get('lead_id') or quote.get('job_id') or quote_id,
            'nombre': job.get('nombre') if job else _client_name(client=client),
            'email': client.get('email') if client else '',
            'telefono': client.get('phone') if client else '',
            'fecha_tentativa': job.get('boda_date') if job else '',
            'locacion': job.get('location') if job else '',
        }

    # Cotizaciones nuevas (multi-opcion) no tienen paquete_nombre/incluye en
    # los campos planos hasta que el cliente acepta una opcion -- para que el
    # PDF siempre muestre que incluye el paquete, usamos la opcion elegida
    # (si ya acepto) o la primera propuesta (si todavia esta pendiente).
    quote_for_pdf = quote
    if not quote.get('incluye') and not quote.get('paquete_nombre'):
        options = _normalize_quote_options(quote)
        selected = next((o for o in options if o.get('id') == quote.get('selected_option_id')), options[0])
        quote_for_pdf = dict(quote)
        quote_for_pdf['paquete_nombre'] = selected.get('name')
        quote_for_pdf['precio_total'] = selected.get('precio_total')
        quote_for_pdf['incluye'] = selected.get('incluye')
        quote_for_pdf['notas'] = quote.get('notas') or selected.get('notas')

    # tenant_id de la cotizacion misma primero -- esta ruta es publica (el
    # cliente la abre desde el link del correo, sin sesion), asi que no se
    # puede depender de session['tenant_id'] como en las rutas internas.
    pdf_tenant_id = quote.get('tenant_id') or (locals().get('job') or {}).get('tenant_id')
    brand = resolve_pdf_brand(pdf_tenant_id)
    pdf_bytes = generate_quote_pdf(quote_for_pdf, lead, brand=brand)
    return Response(pdf_bytes, mimetype='application/pdf', headers={
        'Content-Disposition': f'inline; filename="cotizacion-{quote_id}.pdf"'
    })


@app.route('/contracts/<contract_id>')
def contract_view(contract_id):
    """Vista web del contrato (cliente): terminos, estado de firma y firma digital."""
    contract = get_contract(contract_id)
    if not contract:
        abort(404)
    job = get_job(contract.get('job_id', ''))
    client = get_client(contract.get('client_id', ''))
    if not job or not client:
        abort(404)
    # contract.tenant_id no es confiable (los contratos legados no siempre
    # lo tienen -- ver STABILIZATION_EXECUTION_REPORT.md); job.tenant_id
    # es la fuente primaria.
    brand = resolve_pdf_brand(job.get('tenant_id') or contract.get('tenant_id'))
    # El contrato usa el MISMO sistema de documentos que la cotizacion y la
    # factura, asi que necesita el theme resuelto igual que ellas. `brand`
    # se conserva por si alguna vista vieja todavia lo espera.
    return render_template(
        'contract_view.html',
        contract=contract,
        job=job,
        client=client,
        terms=contract_terms(job, brand=brand),
        brand=brand,
        theme=_document_theme(job.get('tenant_id') or contract.get('tenant_id')),
    )


@app.route('/contracts/<contract_id>/pdf')
def contract_pdf(contract_id):
    """Descarga el PDF del contrato."""
    from flask import Response
    contract = get_contract(contract_id)
    if not contract:
        abort(404)
    job = get_job(contract.get('job_id', ''))
    client = get_client(contract.get('client_id', ''))
    if not job or not client:
        abort(404)

    tenant_id = job.get('tenant_id') or contract.get('tenant_id')
    brand = resolve_pdf_brand(tenant_id)
    theme = _document_theme(tenant_id)
    # Mismo sistema que el contrato web y que la factura PDF. Se le pasa el
    # cliente ya resuelto para que el renderer no tenga que saber como se
    # arma un nombre en el CRM.
    pdf_bytes = render_contract_pdf(
        contract, job,
        {'nombre': f"{client.get('first_name','')} {client.get('last_name','')}".strip(),
         'contacto': [x for x in (client.get('email'), client.get('phone')) if x]},
        brand,
        terms=contract_terms(job, brand=brand),
        simbolo=theme.get('currency_symbol') or 'Q',
        fecha_evento=_format_date_es(job.get('boda_date')),
        firmas_es={
            'estudio': _format_date_es((contract.get('photographer_signed_at') or '')[:10]),
            'cliente': _format_date_es((contract.get('signed_at') or '')[:10]),
        },
    )
    return Response(pdf_bytes, mimetype='application/pdf', headers={
        'Content-Disposition': f'inline; filename="contrato-{contract_id}.pdf"'
    })


@app.route('/invoices/<invoice_id>/pdf')
def invoice_pdf(invoice_id):
    """Descarga el PDF de la factura. Si el job/cotizacion tiene varias cuotas,
    se genera UNA sola factura con el desglose de todos los pagos adentro."""
    from flask import Response
    payments_all = _visible_billable_payments()
    pay = next((p for p in payments_all if p.get('invoice_id') == invoice_id), None)
    if not pay:
        abort(404)
    job = get_job(pay.get('job_id', ''))
    client = get_client(pay.get('client_id', ''))
    if not job or not client:
        abort(404)

    quote = store.get('quotes', pay.get('quote_id', '')) if pay.get('quote_id') else None
    package_name = None
    package_incluye = None
    if quote:
        schedule = [
            p for p in payments_all
            if p.get('quote_id') == quote.get('id') and p.get('job_id') == pay.get('job_id')
        ]
        schedule.sort(key=lambda p: (p.get('due_date') or '', p.get('cuota') or 0, p.get('invoice_id') or ''))
        package_name, package_incluye = _resolve_quote_package(quote)
    else:
        schedule = [pay]
        package_name = job.get('package')

    tenant_id = job.get('tenant_id') or pay.get('tenant_id')
    brand = resolve_pdf_brand(tenant_id)
    # El PDF se dibuja desde el MISMO dict que consume invoice_document.html.
    # Antes tenia su propio armado de datos y su propio diseño, y por eso la
    # web y el PDF se separaron: cada mejora de una dejaba a la otra atras.
    # Ahora comparten la fuente de datos (_invoice_document) y los tokens
    # (src.pdf_document_system, espejo de _document_tokens.html), asi que no
    # pueden mostrar cifras ni conceptos distintos.
    doc = _invoice_document(invoice_id, tenant_id=tenant_id)
    if not doc:
        abort(404)
    theme = _document_theme(tenant_id)
    pdf_bytes = render_invoice_pdf(
        doc, brand,
        simbolo=theme.get('currency_symbol') or 'Q',
        generado_el=_format_date_es(datetime.now().strftime('%Y-%m-%d')),
    )
    return Response(pdf_bytes, mimetype='application/pdf', headers={
        'Content-Disposition': f'inline; filename="factura-{invoice_id}.pdf"'
    })


# ============================================================
# CONTRACT CREATION + MANAGEMENT
# ============================================================

@app.route('/api/contracts/new', methods=['POST'])
def api_contract_new():
    """Crea el contrato de un job -- o devuelve el que ya existe. Kevin: 'quiero
    que solo haya un contrato por job, se generaron 3 de la nada' -- el boton +
    y el trigger del workflow step llamaban a este endpoint cada vez, creando
    un registro nuevo (y un link nuevo) en cada click/disparo. Ahora es
    idempotente: un job siempre resuelve al mismo contrato."""
    import uuid
    from datetime import datetime as _dt

    data = request.get_json() or {}
    job_id = data.get('job_id', '')
    if not job_id:
        return jsonify({'ok': False, 'error': 'job_id requerido'}), 400

    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404

    client = get_client(job.get('client_id', ''))
    if not client:
        return jsonify({'ok': False, 'error': 'Cliente no encontrado'}), 404

    existing = next((c for c in store.list('contracts') if c.get('job_id') == job_id), None)
    if existing:
        return jsonify({
            'ok': True,
            'contract_id': existing['id'],
            'pdf_url': f"/contracts/{existing['id']}/pdf",
        })

    contract_id = 'contract-' + uuid.uuid4().hex[:8]
    contract = {
        'id': contract_id,
        'job_id': job_id,
        'client_id': job['client_id'],
        'lead_id': job.get('lead_id'),
        'tipo': 'boda',
        'status': 'Borrador',
        'signed': False,
        'created': _dt.now().isoformat()[:10],
    }
    store.upsert('contracts', contract)

    return jsonify({
        'ok': True,
        'contract_id': contract_id,
        'pdf_url': f'/contracts/{contract_id}/pdf',
    })


@app.route('/api/contracts/<contract_id>', methods=['DELETE'])
def api_contract_delete(contract_id):
    """Elimina un contrato -- para limpiar duplicados que se hayan generado
    antes de que /api/contracts/new fuera idempotente."""
    contract = get_contract(contract_id)
    if not contract:
        return jsonify({'ok': False, 'error': 'Contrato no encontrado'}), 404
    if contract.get('signed') or contract.get('photographer_signed'):
        return jsonify({'ok': False, 'error': 'No se puede eliminar un contrato ya firmado'}), 400
    store.delete('contracts', contract_id)
    return jsonify({'ok': True})


def get_contract(contract_id):
    contracts = store.list('contracts')
    return next((c for c in contracts if c.get('id') == contract_id), None)


# ============================================================
# CLIENT PORTAL (vista publica del cliente)
# ============================================================

@app.route('/portal/<client_id>')
def client_portal(client_id):
    """Vista publica del portal del cliente: todo en un solo lugar (cotizaciones,
    contratos, facturas, cuestionarios), sea que el cliente todavia sea un lead
    o ya tenga un job confirmado."""
    client = get_client(client_id)
    if not client:
        abort(404)

    # Buscar jobs del cliente, en cualquier rol que reciba documentos
    # (principal o pareja -- ROLES_DESTINATARIOS_DOCUMENTOS). Antes esto
    # miraba solo job.client_id (el principal): la pareja abria SU PROPIO
    # link de portal y lo veia vacio, sin su cotizacion ni su contrato --
    # el mismo bug que ya se habia cerrado en /clients/<id>, colado aca
    # porque esta puerta nunca paso por el mismo helper canonico. El
    # wedding planner y otros roles de contacto siguen sin ver nada en su
    # portal: no es un descuido, es la regla de "el planner nunca recibe
    # contratos" aplicada tambien aca, no solo en el envio de correos.
    _jobs_todos = _canonical_jobs()
    _relaciones = _relaciones_por_job(_jobs_todos)
    job_ids = {
        job.get('id') for job in _jobs_todos
        for rel in _relaciones.get(job.get('id'), [])
        if rel['client_id'] == client_id and rel['role'] in ROLES_DESTINATARIOS_DOCUMENTOS
    }
    jobs = [j for j in _jobs_todos if j.get('id') in job_ids]

    # Un cliente puede seguir siendo lead (sin job todavia): buscamos sus
    # leads tambien para que sus cotizaciones/cuestionarios se vean igual.
    client_leads = [
        l for l in list_leads()
        if l.get('client_id') == client_id or l.get('id') == client.get('lead_id')
    ]
    lead_ids = {l.get('id') for l in client_leads}

    # Cotizaciones vinculadas a cualquiera de sus jobs o leads.
    quotes = []
    seen_quotes = set()
    for q in store.list('quotes'):
        if q.get('id') in seen_quotes:
            continue
        linked = (
            (q.get('job_id') and q.get('job_id') in job_ids)
            or (q.get('lead_id') and q.get('lead_id') in lead_ids)
            or q.get('client_id') == client_id
        )
        if linked:
            quotes.append(q)
            seen_quotes.add(q.get('id'))
    quotes.sort(key=lambda q: q.get('created') or '', reverse=True)
    for q in quotes:
        q['paquete_nombre'], q['incluye'] = _resolve_quote_package(q)
        if not q.get('precio_total'):
            options = _normalize_quote_options(q)
            selected = next((o for o in options if o.get('id') == q.get('selected_option_id')), options[0])
            q['precio_total'] = selected.get('precio_total') or 0

    # Pagos/facturas: se agrupan por cotizacion (o job) para que el cliente
    # vea UNA sola factura por job, con el desglose de cuotas internamente
    # en vez de una factura separada por cada pago.
    # Los pagos se crean siempre con el client_id del principal (ver
    # _ensure_payments_for_quote): sin el `or job_id in job_ids`, la pareja
    # nunca veia sus propias cuotas aca aunque ya se le reconociera el job
    # arriba.
    payments = [p for p in list_payments()
                if p.get('client_id') == client_id or p.get('job_id') in job_ids]
    payments.sort(key=lambda p: p.get('due_date') or '')

    # El boton "Pagar ahora" solo aparece si el pago ya tiene un
    # payment_link_url -- pero las cuotas recien generadas por la
    # calendarizacion automatica (_ensure_payments_for_quote) nunca pasan
    # por el flujo de recordatorio ni por "Generar link de pago" del admin,
    # asi que el cliente entraba a su portal y no tenia como pagar. Genera
    # el link on-demand aqui, igual que ya se hacia para los recordatorios.
    from src import recurrente
    if recurrente.is_configured(tenant_id=client.get('tenant_id')):
        host = request.host_url.rstrip('/')
        for p in payments:
            if p.get('status') == 'Pagado' or p.get('payment_link_url'):
                continue
            amount = round(float(p.get('amount') or 0), 2)
            if amount <= 0:
                continue
            invoice_id = p.get('invoice_id') or p['id']
            redirect_url = _client_facing_invoice_url(host, client, invoice_id)
            result = recurrente.create_checkout(
                name=p.get('concepto') or invoice_id,
                amount_in_cents=round(amount * 100),
                currency='GTQ',
                success_url=redirect_url,
                cancel_url=redirect_url,
                tenant_id=client.get('tenant_id'),
            )
            if result.get('ok'):
                p['payment_link_url'] = result.get('checkout_url')
                p['payment_link_id'] = result.get('id')
                p['payment_link_created_at'] = datetime.now().isoformat()
                store.upsert('payments', p)

    invoice_groups = []
    seen_group_keys = set()
    for p in payments:
        group_key = p.get('quote_id') or p.get('job_id') or p.get('invoice_id')
        if group_key in seen_group_keys:
            continue
        seen_group_keys.add(group_key)
        rows = [
            r for r in payments
            if (r.get('quote_id') or r.get('job_id') or r.get('invoice_id')) == group_key
        ]
        rows.sort(key=lambda r: (r.get('due_date') or '', r.get('cuota') or 0))
        for r in rows:
            # 'amount' de una cuota pendiente YA es su saldo actual.
            r['balance'] = 0 if r.get('status') == 'Pagado' else round(float(r.get('amount') or 0), 2)
        total = sum(_row_original_amount(r) for r in rows)
        paid = sum(_row_paid_amount(r) for r in rows)
        if paid >= total and total > 0:
            group_status = 'Pagado'
        elif any(r.get('status') == 'Late' for r in rows):
            group_status = 'Late'
        elif paid > 0:
            group_status = 'Parcial'
        else:
            group_status = 'Pendiente'
        group_quote = store.get('quotes', rows[0].get('quote_id', '')) if rows[0].get('quote_id') else None
        package_name, package_incluye = _resolve_quote_package(group_quote) if group_quote else (None, [])
        invoice_groups.append({
            'invoice_id': rows[0].get('invoice_id'),
            'concepto': rows[0].get('concepto') or '',
            'package_name': package_name,
            'package_incluye': package_incluye,
            'rows': rows,
            'total': total,
            'paid': paid,
            'balance': max(total - paid, 0),
            'status': group_status,
        })

    # Contratos. Un solo contrato por job, creado con el client_id del
    # principal (ver api_contract_new): el `or job_id in job_ids` es lo que
    # deja que la pareja tambien lo vea y lo firme desde su propio portal.
    contracts = [c for c in store.list('contracts')
                 if c.get('client_id') == client_id or c.get('job_id') in job_ids]

    # Cuestionarios (creados desde el job, ver /api/jobs/<id>/questionnaires)
    questionnaires = [
        q for q in store.list('questionnaires')
        if q.get('client_id') == client_id or q.get('job_id') in job_ids
    ]

    # Archivos/galeria subidos al job (ver /api/jobs/<id>/files)
    files = [
        f for f in store.list('files')
        if f.get('client_id') == client_id or f.get('job_id') in job_ids
    ]

    primary_job = jobs[0] if jobs else None
    days_until_wedding = None
    wedding_date_label = None
    if primary_job and primary_job.get('boda_date'):
        try:
            d = datetime.strptime(primary_job['boda_date'], '%Y-%m-%d').date()
            days_until_wedding = (d - date.today()).days
            month_names_es = ['', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
            wedding_date_label = f"{d.day} de {month_names_es[d.month]}, {d.year}"
        except ValueError:
            pass

    total_all = sum(g['total'] for g in invoice_groups)
    paid_all = sum(g['paid'] for g in invoice_groups)
    payment_pct = round((paid_all / total_all) * 100) if total_all else 0

    next_due = None
    for g in invoice_groups:
        for r in g['rows']:
            if r.get('status') != 'Pagado' and r.get('due_date'):
                if not next_due or r['due_date'] < next_due.get('due_date', '9999'):
                    next_due = r
    steps_done = sum([
        bool(quotes and any(q.get('status') == 'Aceptada' for q in quotes)),
        bool(contracts and any(c.get('signed') for c in contracts)),
        bool(questionnaires and any(q.get('status') == 'Respondido' for q in questionnaires)),
        bool(files),
    ])

    brand = resolve_pdf_brand(client.get('tenant_id'))
    return render_template('client_portal.html',
                          client=client,
                          brand=brand,
                          jobs=jobs,
                          primary_job=primary_job,
                          days_until_wedding=days_until_wedding,
                          wedding_date_label=wedding_date_label,
                          quotes=quotes,
                          payments=payments,
                          invoice_groups=invoice_groups,
                          total_all=total_all,
                          paid_all=paid_all,
                          payment_pct=payment_pct,
                          next_due=next_due,
                          steps_done=steps_done,
                          contracts=contracts,
                          questionnaires=questionnaires,
                          files=files)


# ============================================================
# DIGITAL SIGNATURE (firma simple)
# ============================================================

@app.route('/api/contracts/<contract_id>/sign', methods=['POST'])
def api_contract_sign(contract_id):
    """Registra la firma digital del cliente."""
    import base64
    from datetime import datetime as _dt

    contract = get_contract(contract_id)
    if not contract:
        return jsonify({'ok': False, 'error': 'Contrato no encontrado'}), 404

    data = request.get_json() or {}
    signature_data = data.get('signature', '')  # base64 PNG

    if not signature_data:
        return jsonify({'ok': False, 'error': 'Firma requerida'}), 400

    # Guardar firma completa (antes se truncaba a 100 caracteres, lo que
    # rompia el data URI y la imagen nunca se veia).
    contract['signed'] = True
    contract['signed_at'] = _dt.now().isoformat()
    contract['signature_preview'] = signature_data
    contract['signature_type'] = data.get('signature_type') or 'draw'
    contract['signature_text'] = data.get('signature_text') or ''
    contract['status'] = 'Firmado' if contract.get('photographer_signed') else 'Firmado por cliente'
    store.upsert('contracts', contract)

    return jsonify({
        'ok': True,
        'contract_id': contract_id,
        'status': contract['status'],
        'signed_at': contract['signed_at']
    })


@app.route('/api/contracts/<contract_id>/sign-photographer', methods=['POST'])
def api_contract_sign_photographer(contract_id):
    """Registra la firma del fotografo (ASTRAL WEDDINGS) en el contrato."""
    from datetime import datetime as _dt

    contract = get_contract(contract_id)
    if not contract:
        return jsonify({'ok': False, 'error': 'Contrato no encontrado'}), 404

    data = request.get_json() or {}
    signature_data = data.get('signature', '')
    if not signature_data:
        return jsonify({'ok': False, 'error': 'Firma requerida'}), 400

    contract['photographer_signed'] = True
    contract['photographer_signed_at'] = _dt.now().isoformat()
    contract['photographer_signature_preview'] = signature_data
    contract['photographer_signature_type'] = data.get('signature_type') or 'draw'
    contract['photographer_signature_text'] = data.get('signature_text') or ''
    if contract.get('signed'):
        contract['status'] = 'Firmado'
    store.upsert('contracts', contract)

    return jsonify({
        'ok': True,
        'contract_id': contract_id,
        'status': contract.get('status'),
        'photographer_signed_at': contract['photographer_signed_at']
    })


@app.route('/api/contracts/<contract_id>/send', methods=['POST'])
def api_contract_send(contract_id):
    """Envia el contrato por email al cliente (con link de firma), no solo marca el status."""
    from src.mail_tracker import get_tracker

    contract = get_contract(contract_id)
    if not contract:
        return jsonify({'ok': False, 'error': 'Contrato no encontrado'}), 404

    job = get_job(contract.get('job_id', '')) if contract.get('job_id') else None
    client = get_client(contract.get('client_id', '')) if contract.get('client_id') else None
    lead = get_lead(contract.get('lead_id', '')) if contract.get('lead_id') else None
    to_email = _email_for(client=client, lead=lead)
    if not to_email:
        return jsonify({'ok': False, 'error': 'Este contrato no tiene email de cliente'}), 400

    host = request.host_url.rstrip('/')
    contract_url = host + f'/contracts/{contract_id}'
    name = _client_name(client=client, lead=lead, job=job)

    data = request.json or request.form or {}
    empresa = _brand_display_name_for_tenant((job or {}).get('tenant_id') or contract.get('tenant_id'))
    subject = (data.get('subject') or '').strip() or f'Tu contrato de servicios fotograficos - {empresa}'
    body = (data.get('body') or '').strip() or (
        f"Hola {name},\n\n"
        "Aqui esta el contrato de servicios. Puedes leerlo y firmarlo electronicamente desde este link:\n"
        f"{contract_url}\n\n"
        f"Si tienes preguntas legales, no dudes en consultarnos.\n\nSaludos,\n{empresa}"
    )
    # Igual que con cuestionarios: si Kevin elige una plantilla de Settings
    # que no trae el link del contrato, el correo saldria sin forma de
    # firmarlo -- _inject_link garantiza que el link siempre vaya, sea cual
    # sea la plantilla elegida.
    body = _inject_link(body, contract_url,
                        placeholders=['[LINK AL CONTRATO]', '[LINK DEL CONTRATO]'],
                        fallback_label='Firma tu contrato aqui')
    # STAGE 2 (agosto 2026): cola de aprobacion en vez de entrega inmediata.
    mail = get_tracker().queue_email(
        to_email=to_email,
        subject=subject,
        body=body,
        template_id=data.get('template_id'),
        lead_id=contract.get('lead_id'),
        job_id=contract.get('job_id'),
        client_id=contract.get('client_id'),
        tenant_id=(job or {}).get('tenant_id') or contract.get('tenant_id'),
        source='manual:contract-send',
        idempotency_key=f'contract:{contract_id}:send:{_idempotency_minute_bucket()}',
    )

    contract['status'] = 'Enviado'
    contract['sent_at'] = datetime.now().isoformat()
    store.upsert('contracts', contract)

    return jsonify({
        'ok': True,
        'contract_id': contract_id,
        'status': contract['status'],
        'mail_id': mail.get('id'),
        'delivery_status': mail.get('status'),
        'mail_warning': _mail_delivery_warning(mail),
        'email': to_email,
        'contract_url': contract_url,
        'message': (f'Contrato puesto en cola de aprobacion para {to_email} (revisa /emails)'
                    if mail.get('status') == 'pending'
                    else f'Contrato NO se pudo poner en cola: {mail.get("blocked_reason") or "motivo no especificado"}'),
    })



# ============================================================
# BUSQUEDA GLOBAL
# ============================================================
@app.route('/api/search')
def api_global_search():
    """Busca en leads, jobs, clients, payments, quotes (respeta tenant actual)."""
    from datetime import datetime

    query = (request.args.get('q') or '').strip().lower()
    if not query or len(query) < 2:
        return jsonify({'results': [], 'query': query, 'total': 0})

    results = []

    # Buscar en leads abiertos (tenant-aware). Si ya fue aceptado, vive en Jobs.
    for lead in _open_leads():
        searchable = f"{lead.get('nombre', '')} {lead.get('email', '')} {lead.get('telefono', '')} {lead.get('locacion', '')}".lower()
        if query in searchable:
            results.append({
                'type': 'lead',
                'id': lead.get('id'),
                'title': lead.get('nombre', ''),
                'subtitle': f"{lead.get('email', '')} * {lead.get('status', 'Nuevo')}",
                'url': f"/leads/{lead.get('id')}",
                'icon': 'user',
            })

    # Buscar en jobs (tenant-aware)
    for job in _canonical_jobs():
        searchable = f"{job.get('nombre', '')} {job.get('location', '')} {job.get('package', '')}".lower()
        if query in searchable:
            results.append({
                'type': 'job',
                'id': job.get('id'),
                'title': job.get('nombre', ''),
                'subtitle': f"{job.get('boda_date', '')} * {job.get('status', '')}",
                'url': f"/jobs/{job.get('id')}",
                'icon': 'briefcase',
            })

    # Buscar en clients (tenant-aware)
    for c in _canonical_clients():
        searchable = f"{c.get('first_name', '')} {c.get('last_name', '')} {c.get('email', '')} {c.get('phone', '')} {c.get('address', '')}".lower()
        if query in searchable:
            results.append({
                'type': 'client',
                'id': c.get('id'),
                'title': f"{c.get('first_name', '')} {c.get('last_name', '')}",
                'subtitle': f"{c.get('email', '')} * {c.get('phone', '')}",
                'url': f"/clients/{c.get('id')}",
                'icon': 'user-circle',
            })

    # Buscar en payments (tenant-aware)
    for p in list_payments():
        searchable = f"{p.get('invoice_id', '')} {p.get('concepto', '')}".lower()
        if query in searchable:
            results.append({
                'type': 'payment',
                'id': p.get('id'),
                'title': f"{p.get('invoice_id', '')} - Q{p.get('amount', 0):,.0f}",
                'subtitle': f"{p.get('status', '')} * {p.get('concepto', '')}",
                'url': f"/invoices/{p.get('invoice_id')}/pdf",
                'icon': 'currency',
            })

    # Buscar en quotes (tenant-aware via lead_id)
    leads_list = list_leads()
    lead_ids = {l['id'] for l in leads_list}
    for q_doc in store.list('quotes'):
        if q_doc.get('lead_id') in lead_ids:
            searchable = f"{q_doc.get('id', '')} {q_doc.get('paquete_nombre', '')}".lower()
            if query in searchable:
                results.append({
                    'type': 'quote',
                    'id': q_doc.get('id'),
                    'title': f"Quote {q_doc.get('id', '')} - {q_doc.get('paquete_nombre', '')}",
                    'subtitle': f"Q{q_doc.get('precio_total', 0):,.0f} * {q_doc.get('status', '')}",
                    'url': f"/quotes/{q_doc.get('id')}",
                    'icon': 'document',
                })

    return jsonify({
        'results': results[:20],
        'query': query,
        'total': len(results),
        'tenant': get_current_tenant_id()
    })



# ============================================================
# API: Crear cliente manualmente desde /clients
# ============================================================
@app.route('/api/clients/new', methods=['POST'])
def api_client_new():
    import uuid
    from datetime import datetime as _dt
    data = request.get_json() or {}

    first_name = (data.get('first_name') or '').strip()
    last_name = (data.get('last_name') or '').strip()
    email = (data.get('email') or '').strip()

    if not first_name or not last_name:
        return jsonify({'ok': False, 'error': 'Nombre y apellido requeridos'}), 400

    client_id = 'client-' + uuid.uuid4().hex[:8]
    client = {
        'id': client_id,
        'first_name': first_name,
        'last_name': last_name,
        'email': email,
        'phone': data.get('phone', ''),
        'address': data.get('address', ''),
        'company': data.get('company', ''),
        'source': data.get('source', 'Manual'),
        'estado': data.get('estado', 'Activo'),
        'tenant_id': get_current_tenant_id(),
        'created': _dt.now().isoformat()[:10],
    }
    store.upsert('clients', client)
    return jsonify({'ok': True, 'client_id': client_id, 'client': client})


# ============================================================
# API: Validar disponibilidad de fecha del Lead
# ============================================================
@app.route('/api/leads/<lead_id>/check-date', methods=['POST'])
def api_check_date(lead_id):
    """Verifica si la fecha tentativa del lead esta disponible."""
    from datetime import date as _date
    lead = get_lead(lead_id)
    if not lead:
        return jsonify({'ok': False, 'error': 'Lead no encontrado'}), 404

    fecha = lead.get('fecha_tentativa', '')
    if not fecha:
        return jsonify({'ok': False, 'error': 'Sin fecha tentativa. Pedile al cliente su fecha.'}), 400

    # Buscar si hay otra boda en esa fecha
    conflicts = []
    for j in _canonical_jobs():
        if j.get('boda_date') == fecha and j.get('lead_id') != lead_id:
            conflicts.append({
                'job_id': j['id'],
                'client': j.get('nombre', ''),
            })

    return jsonify({
        'ok': True,
        'fecha': fecha,
        'disponible': len(conflicts) == 0,
        'conflicts': conflicts,
        # La recomendacion no puede nombrar una marca fija: quien consulta
        # la fecha puede ser cualquiera de las dos empresas.
        'recomendacion': _brand_display_name_for_tenant(get_current_tenant_id()),
    })


# ============================================================
# API: Workflow con validacion (lead steps)
# ============================================================
@app.route('/api/workflow/step', methods=['POST'])
def api_workflow_step():
    """Dispara un step del workflow con logica inteligente."""
    from src.mail_tracker import get_tracker

    data = request.get_json() or {}
    lead_id = data.get('lead_id', '')
    step_id = data.get('step_id', '')

    lead = get_lead(lead_id)
    if not lead:
        return jsonify({'ok': False, 'error': 'Lead no encontrado'}), 404

    # Determinar el email template segun el workflow editable.
    from src.workflow import LEAD_WORKFLOW
    workflow_step = next((s for s in LEAD_WORKFLOW().steps if s.id == step_id), None)
    if not workflow_step:
        return jsonify({'ok': False, 'error': 'Step desconocido'}), 400

    template_id = workflow_step.email_template_id
    if step_id == 'validar_disponibilidad':
        # Verificar disponibilidad primero
        fecha = lead.get('fecha_tentativa', '')
        conflicts = []
        for j in _canonical_jobs():
            if j.get('boda_date') == fecha and j.get('lead_id') != lead_id:
                conflicts.append(j)
        if not conflicts:
            template_id = template_id or 'tpl-paquetes'
            return jsonify({
                'ok': True,
                'disponible': True,
                'fecha': fecha,
                'recomendacion': 'Enviar paquetes de Astral',
                'message': f'Fecha {fecha} esta LIBRE'
            })
        else:
            template_id = template_id or 'tpl-fecha-no-disponible'
            return jsonify({
                'ok': True,
                'disponible': False,
                'fecha': fecha,
                'conflicts': [{'job_id': c['id'], 'client': c.get('nombre', '')} for c in conflicts],
                'recomendacion': 'Enviar email de Astral Films',
                'message': f'Fecha {fecha} NO esta disponible. Recomendar Astral Films.'
            })
    if not template_id:
        return jsonify({'ok': False, 'error': 'Este step no tiene email template configurado'}), 400

    # Disparar workflow engine
    instances = _workflow_instances_seguras(subject_type='lead', subject_id=lead_id)
    if not instances:
        return jsonify({'ok': False, 'error': 'No hay workflow activo'}), 400
    instance = instances[0]

    # Marcar como done
    instance.step_states[step_id] = StepStatus.DONE
    instance.step_results[step_id] = f"EMAIL sent: {step_id}"

    # Registrar email
    tracker = get_tracker()
    templates_list = store.list('email_templates')
    tpl = next((t for t in templates_list if t.get('id') == template_id), None)
    subject = tpl.get('asunto', step_id) if tpl else step_id

    # STAGE 2 (agosto 2026): cola de aprobacion en vez de entrega inmediata.
    # Clave estable: el step ya se marco DONE arriba (linea 12120-12121),
    # asi que este es un respaldo contra un reintento del mismo request, no
    # la guarda principal.
    mail = tracker.queue_email(
        to_email=lead.get('email', ''),
        subject=subject,
        body=tpl.get('cuerpo', '') if tpl else '',
        template_id=template_id,
        lead_id=lead_id,
        client_id=lead.get('client_id') or None,
        source=f'workflow:lead-step:{step_id}',
        # Misma familia de clave que _complete_lead_workflow_step
        # (leadstep:) -- revision adversarial (agosto 2026): ambos
        # endpoints derivan asunto/cuerpo del mismo LEAD_WORKFLOW().steps
        # por step_id, o sea producen el mismo correo para el mismo step.
        # Con prefijos distintos, disparar el mismo step por las dos vias
        # crearia dos pendientes aprobables por separado -- aprobar ambos
        # seria un envio real duplicado al mismo cliente.
        idempotency_key=f'leadstep:{lead_id}:{step_id}',
    )

    workflow_engine._log(instance, 'step.manual', f'{step_id}: enviado')
    workflow_engine._save_to_storage()

    return jsonify({
        'ok': True,
        'step': step_id,
        'template': template_id,
        'mail_id': mail.get('id'),
        'email': lead.get('email', ''),
        'delivery_status': mail.get('status'),
        'mail_warning': _mail_delivery_warning(mail),
        'message': (f'Email "{subject}" puesto en cola de aprobacion para {lead.get("email", "")} (revisa /emails)'
                    if mail.get('status') == 'pending'
                    else f'Email NO se pudo poner en cola: {mail.get("blocked_reason") or "motivo no especificado"}'),
    })


# ============================================================
# API: Workflow de Production del Job
# ============================================================
@app.route('/api/jobs/<job_id>/production-step', methods=['POST'])
def api_job_production_step(job_id):
    """Dispara un step del production workflow del job."""
    from src.mail_tracker import get_tracker

    job = get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job no encontrado'}), 404

    data = request.get_json() or {}
    step_id = data.get('step_id', '')

    # Determinar el email template segun el step
    template_map = {
        'reserva_confirmada': 'tpl-reserva-prod',
        'firma_contrato': 'tpl-contrato-prod',
        'cuestionario_cliente': 'tpl-cuestionario-prod',
        'envio_galeria': 'tpl-galeria',
        'pedir_review': 'tpl-review',
    }
    template_id = template_map.get(step_id)

    # Buscar el workflow instance del job
    instances = _workflow_instances_seguras(subject_type='job', subject_id=job_id)
    if not instances:
        return jsonify({'ok': False, 'error': 'No hay workflow activo'}), 400
    instance = instances[0]

    # Marcar como done
    instance.step_states[step_id] = StepStatus.DONE
    instance.step_results[step_id] = f"PRODUCTION step: {step_id}"

    mail_id = None
    if template_id:
        templates_list = store.list('email_templates')
        tpl = next((t for t in templates_list if t.get('id') == template_id), None)
        subject = tpl.get('asunto', step_id) if tpl else step_id

        tracker = get_tracker()
        # Buscar el lead del job para obtener email
        lead = get_lead(job.get('lead_id', ''))
        to_email = lead.get('email', '') if lead else ''

        # STAGE 2 (agosto 2026): cola de aprobacion en vez de entrega
        # inmediata. Clave estable: el step ya se marco DONE arriba, este
        # es un respaldo contra un reintento del mismo request.
        mail = tracker.queue_email(
            to_email=to_email,
            subject=subject,
            body=tpl.get('cuerpo', '') if tpl else '',
            template_id=template_id,
            job_id=job_id,
            lead_id=job.get('lead_id', ''),
            client_id=job.get('client_id') or None,
            tenant_id=job.get('tenant_id'),
            source=f'workflow:job-production:{step_id}',
            # Misma familia de clave que el auto-fire de
            # _send_job_template_email (jobstep:) -- revision adversarial
            # (agosto 2026): confirmado que 'reserva_confirmada',
            # 'firma_contrato', 'cuestionario_cliente', 'envio_galeria' y
            # 'pedir_review' son EXACTAMENTE los mismos ids que produce
            # compute_workflow_steps_for_job() (ver src/workflow/templates.py),
            # asi que es el mismo step logico visto por dos rutas distintas.
            idempotency_key=f'jobstep:{job_id}:{step_id}',
        )
        mail_id = mail.get('id')

    workflow_engine._log(instance, 'step.manual', f'{step_id}: enviado')
    workflow_engine._save_to_storage()

    return jsonify({
        'ok': True,
        'step': step_id,
        'mail_id': mail_id,
        'message': f'Step {step_id} ejecutado'
    })




_AUTO_FIRE_JOB_ACTION_TYPES = ('send_email', 'send_questionnaire', 'send_gallery')


def _auto_fire_due_job_steps():
    """Kevin: 'al crear el job... que se envie cuando el workflow lo diga' --
    antes NADA disparaba un step de Job automaticamente por fecha; se
    quedaba pending para siempre hasta que alguien entrara a darle click
    manual. Revisa cada Job activo y dispara de verdad (correo real, no solo
    marcar el step 'done') los steps de envio cuya fecha ya llego."""
    fired = []
    for job in store.list('jobs'):
        if job.get('status') in ('Cancelado', 'Archivado'):
            continue
        try:
            # tenant_id=job.get('tenant_id') explicito: este loop corre sin
            # peticion web (hilo en segundo plano), asi que get_current_tenant_id()
            # -que usa la sesion activa- daria None aca. Sin esto, cualquier
            # instancia YA etiquetada con tenant_id (inst.tenant_id == None
            # nunca es igual) dejaria de encontrarse y su step jamas se
            # auto-dispararia. Ver docstring de _instancia_es_de_la_cuenta.
            steps, _, _ = compute_workflow_steps_for_job(job, tenant_id=job.get('tenant_id'))
        except Exception as e:
            logger.error(f'Error calculando steps del job {job.get("id")}: {e}')
            continue

        for step in steps:
            if step['status'] != 'pending':
                continue
            if step['action_type'] not in _AUTO_FIRE_JOB_ACTION_TYPES:
                continue
            scheduled = step.get('scheduled')
            if not scheduled:
                continue
            try:
                if datetime.fromisoformat(scheduled) > datetime.now():
                    continue
            except ValueError:
                continue

            try:
                if step['action_type'] == 'send_questionnaire':
                    result = _create_job_questionnaire(
                        job, template_id=step.get('email_template_id'), send_email=True,
                        reuse_draft=True, auto_fire=True,
                    )
                    ok = bool(result.get('mail_id')) and not result.get('mail_warning')
                    result_message = f"Cuestionario auto-enviado: {result['questionnaire']['name']}"
                else:
                    template = _get_email_template(step.get('email_template_id'))
                    result = _send_job_template_email(
                        job,
                        template_id=step.get('email_template_id'),
                        subject=(template or {}).get('asunto'),
                        body=(template or {}).get('cuerpo'),
                        step_id=step['id'],
                        auto_fire=True,
                    )
                    ok = bool(result.get('mail_id')) and not result.get('mail_warning') and not result.get('error')
                    result_message = f"Email auto-enviado: {step['name']}"

                if ok:
                    # Solo se marca 'done' cuando de verdad se entrego --
                    # si Gmail esta desconectado hoy, el step se queda
                    # pending y se reintenta en la siguiente pasada (6h)
                    # en vez de quedar marcado como completado en falso.
                    _complete_job_workflow_step(job, step['id'], result_message=result_message)
                    fired.append((job.get('id'), step['id']))
                else:
                    logger.warning(
                        f"Auto-fire del step {step['id']} en job {job.get('id')} no se entrego de verdad, "
                        f"se reintentara: {result.get('mail_warning') or result.get('error')}"
                    )
            except Exception as e:
                logger.error(f'Error auto-disparando step {step["id"]} del job {job.get("id")}: {e}')

    return fired


_reminder_thread_started = False


def _reminder_scheduler_loop():
    """Corre en segundo plano mientras la app este viva: revisa recordatorios
    de pago y steps de workflow vencidos cada 6 horas (la primera revision
    arranca a los 60s del boot)."""
    time.sleep(60)
    while True:
        try:
            sent = check_and_send_payment_reminders()
            if sent:
                logger.info(f'Recordatorios de pago enviados: {len(sent)} ({sent})')
        except Exception as e:
            logger.error(f'Error revisando recordatorios de pago: {e}')
        try:
            fired = _auto_fire_due_job_steps()
            if fired:
                logger.info(f'Steps de workflow auto-disparados: {len(fired)} ({fired})')
        except Exception as e:
            logger.error(f'Error auto-disparando steps de workflow: {e}')
        time.sleep(6 * 60 * 60)


def start_reminder_scheduler():
    """APAGADO POR DEFECTO tras un incidente real de envio masivo.

    Este hilo mandaba correos DE VERDAD sin que nadie los pidiera, y con los
    133 jobs importados de Studio Ninja (todos con fechas pasadas) eso se
    convirtio en cientos de correos a clientes reales. Peor: corre fuera de
    cualquier request, asi que store.list('jobs') NO filtra por tenant --
    mezclaba los clientes de Norkevin con la firma de Astral.

    Para volver a encenderlo hay que arreglar antes las dos cosas:
      1. que respete el tenant de cada job;
      2. que NUNCA dispare steps con fecha anterior al arranque (un job
         importado con fecha vieja no es un correo pendiente de enviar).
    Y aun asi deberia arrancar en modo simulacion primero.
    """
    global _reminder_thread_started
    if os.environ.get('ENABLE_REMINDER_SCHEDULER') != '1':
        logger.warning(
            'Scheduler de recordatorios APAGADO (ENABLE_REMINDER_SCHEDULER != 1). '
            'No se enviara ningun correo automatico.'
        )
        return
    if _reminder_thread_started:
        return
    _reminder_thread_started = True
    threading.Thread(target=_reminder_scheduler_loop, daemon=True).start()


start_reminder_scheduler()


# ============================================================
# PAGINAS DE ERROR
# ============================================================
# templates/404.html y templates/500.html existian desde hace tiempo pero
# NUNCA se mostraban: sin un errorhandler registrado, Flask sirve su
# propia pagina blanca ("Not Found" / "Internal Server Error"), sin menu,
# sin marca y sin forma de volver. Un enlace viejo dejaba a Kevin en un
# callejon sin salida en medio de una reunion con un cliente.


@app.errorhandler(404)
def pagina_no_encontrada(_e):
    # Las rutas /api/ las consume JavaScript: devolverles HTML haria que
    # el fetch explote al parsear en vez de mostrar un mensaje util.
    if request.path.startswith('/api/'):
        return jsonify({'ok': False, 'error': 'No encontrado'}), 404
    return render_template('404.html'), 404


@app.errorhandler(Exception)
def error_interno(e):
    # Las excepciones HTTP con codigo propio (403, 404, 405...) se dejan
    # pasar tal cual: convertirlas todas en 500 esconderia la causa real.
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e

    # En los tests la excepcion tiene que seguir subiendo. Un handler de
    # Exception se aplica ANTES que PROPAGATE_EXCEPTIONS de Flask, asi que
    # sin esto un bug real llegaria a pytest disfrazado de "500" en vez de
    # con su traza, y algun test podria hasta darlo por bueno.
    if app.config.get('TESTING') or app.config.get('PROPAGATE_EXCEPTIONS'):
        raise e

    # La traza va al log (logs/crm_runtime.log), no a la pantalla: al
    # usuario le sirve saber que fallo y como volver, no el stack.
    logger.exception(f'Error no controlado en {request.method} {request.path}')

    if request.path.startswith('/api/'):
        return jsonify({
            'ok': False,
            'error': 'Algo fallo del lado del servidor. Quedo registrado en el log del CRM.',
        }), 500

    try:
        return render_template(
            '500.html',
            error='Algo fallo del lado del servidor. El detalle quedo en el log del CRM.',
        ), 500
    except Exception:
        # Si la propia pagina de error falla (por ejemplo porque base.html
        # necesita contexto que no existe en esta peticion), se responde
        # texto plano antes que dejar al usuario sin nada.
        logger.exception('Fallo tambien la pagina de error 500')
        return 'Error interno del CRM. Revisa logs/crm_runtime.log', 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8765))
    # Opt-IN, no opt-OUT (mismo criterio que OUTBOUND_EMAIL_ENABLED,
    # ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS, etc.): el debugger interactivo de
    # Werkzeug no deberia quedar prendido solo porque alguien se olvido de
    # la variable. Esto solo corre al lanzar `python app.py` (abrir_crm.bat);
    # Render usa gunicorn via wsgi.py y nunca pasa por aca.
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    logger.info(f'CRM Astral Weddings arrancando en puerto {port} (debug={debug})')
    app.run(debug=debug, port=port, host='0.0.0.0')
