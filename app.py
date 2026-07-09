"""
CRM Norkevin - Backend Flask
Arquitectura: Notion-first. SQLite solo para cache de sesión.
"""
import os
import re
import logging
from datetime import datetime, date, timedelta
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, abort
from dotenv import load_dotenv

import notion_sync as ns
from collections import defaultdict

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET', 'norkevin-crm-dev-secret-change-me')

# ============================================================
# HELPERS
# ============================================================

def q_money(v) -> str:
    if v is None: return 'Q0'
    return f"Q{v:,.0f}".replace(',', ',')


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


def days_until(date_str) -> int:
    if not date_str: return 9999
    try:
        d = date.fromisoformat(date_str)
        return (d - date.today()).days
    except:
        return 9999


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
# PÁGINAS PRINCIPALES
# ============================================================

@app.route('/')
def index():
    """Calendar principal con bodas del mes en curso + próximas."""
    import calendar as _cal

    jobs = ns.list_jobs_full()
    hoy = date.today()

    # Mes actual o ?month=YYYY-MM
    mes_param = request.args.get('month', '')
    if mes_param and re.match(r'\d{4}-\d{2}', mes_param):
        anio_actual = int(mes_param.split('-')[0])
        mes_actual = int(mes_param.split('-')[1])
    else:
        anio_actual = hoy.year
        mes_actual = hoy.month

    # Calcular primer día del mes (weekday)
    cal = _cal.Calendar(firstweekday=0)  # 0 = lunes
    weeks = cal.monthdayscalendar(anio_actual, mes_actual)
    nombre_mes = ['Enero','Febrero','Marzo','Abril','Mayo','Junio','Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre'][mes_actual - 1]

    # Bodas del mes (para el grid)
    eventos_mes_dia = defaultdict(list)
    for j in jobs:
        f = j.get('Fecha del evento') or ''
        try:
            y, m, d = f.split('-')
            if int(y) == anio_actual and int(m) == mes_actual:
                eventos_mes_dia[int(d)].append(j)
        except:
            pass

    # Bodas próximas (90 días)
    proximas = []
    for j in jobs:
        d = days_until(j.get('Fecha del evento'))
        if 0 <= d <= 90 and j.get('Estado') in ('Confirmado', 'En produccion', 'Cotizando', 'Lead'):
            proximas.append({**j, 'days_until': d})
    proximas.sort(key=lambda x: x['days_until'])

    # Nav para mes anterior/siguiente
    if mes_actual == 1:
        prev_month = f'{anio_actual - 1}-12'
        next_month = f'{anio_actual}-02'
    elif mes_actual == 12:
        prev_month = f'{anio_actual}-11'
        next_month = f'{anio_actual + 1}-01'
    else:
        prev_month = f'{anio_actual}-{mes_actual - 1:02d}'
        next_month = f'{anio_actual}-{mes_actual + 1:02d}'

    # Stats del mes
    jobs_mes = sum(len(v) for v in eventos_mes_dia.values())
    clientes = ns.list_clients_full()
    total_por_cobrar = sum(
        (j.get('Total facturado al cliente (Q)') or 0) - (j.get('Total pagado por cliente (Q)') or 0)
        for j in jobs if j.get('Estado') not in ('Listo',)
    )

    return render_template('calendar.html',
                           weeks=weeks,
                           eventos_mes_dia=eventos_mes_dia,
                           mes_actual=mes_actual,
                           anio_actual=anio_actual,
                           nombre_mes=nombre_mes,
                           prev_month=prev_month,
                           next_month=next_month,
                           hoy=hoy,
                           hoy_dia=hoy.day if (hoy.month == mes_actual and hoy.year == anio_actual) else None,
                           proximas=proximas[:8],
                           jobs_mes=jobs_mes,
                           clients_count=len(clientes),
                           jobs_activos=sum(1 for j in jobs if j.get('Estado') in ('Confirmado','En produccion')),
                           total_por_cobrar=total_por_cobrar,
                           parse_date=parse_date, days_until=days_until, q_money=q_money,
                           fmt_dt=fmt_dt)


@app.route('/dashboard')
def dashboard():
    """Métricas del negocio."""
    import json as _json
    from collections import Counter
    from datetime import date as _date

    jobs = ns.list_jobs_full()
    leads = ns.list_leads_full()
    pagos = ns.list_pagos_eq_full()
    clientes = ns.list_clients_full()

    hoy = _date.today()

    # KPIs principales
    proximas_30 = []
    for j in jobs:
        d = days_until(j.get('Fecha del evento'))
        if 0 <= d <= 30:
            j_copy = dict(j)
            j_copy['days_until'] = d
            proximas_30.append(j_copy)
    proximas_90 = [j for j in jobs if 0 <= days_until(j.get('Fecha del evento')) <= 90]

    # Revenue YTD (suma de total facturado de jobs este año)
    current_year = hoy.year
    revenue_ytd = sum(
        (j.get('Total facturado al cliente (Q)') or 0)
        for j in jobs
        if j.get('Fecha del evento', '').startswith(str(current_year))
    )

    # Total payments (cobrado + pendiente)
    total_payments_pagado = sum(p.get('Monto acordado') or 0 for p in pagos if p.get('Estado de pago') == 'Pagado')
    total_payments_pendiente = sum(p.get('Monto acordado') or 0 for p in pagos if p.get('Estado de pago') in ('Pendiente','Mitad pagado','En proceso'))
    total_payments = total_payments_pagado + total_payments_pendiente

    # Ingresos esperados (suma de total facturado de jobs próximos)
    ingresos_esperados = sum(j.get('Total facturado al cliente (Q)') or 0 for j in proximas_90)

    # Pipeline
    pipeline = {}
    for j in jobs:
        st = j.get('Estado') or 'Sin estado'
        pipeline[st] = pipeline.get(st, 0) + 1

    # Leads por estado
    leads_por_estado = {}
    for l in leads:
        st = l.get('Estado') or 'Nuevo'
        leads_por_estado[st] = leads_por_estado.get(st, 0) + 1

    # Lead Sources (para pie chart)
    fuentes_counter = Counter()
    for l in leads:
        f = l.get('Fuente') or 'Otro'
        fuentes_counter[f] += 1
    lead_sources = [{'label': f, 'count': c} for f, c in fuentes_counter.most_common()]

    # Revenue por año (mes Ene-Dic) - sumando Total facturado por mes/año
    revenue_by_year = {}
    for j in jobs:
        f = j.get('Fecha del evento') or ''
        fact = j.get('Total facturado al cliente (Q)') or 0
        if not f or not fact:
            continue
        try:
            y, m, _ = f.split('-')
            y = int(y); m = int(m)
            if y not in revenue_by_year:
                revenue_by_year[y] = [0]*12
            revenue_by_year[y][m-1] += fact
        except:
            pass

    # Equipo stats
    primera_camara_count = {}
    for j in jobs:
        if j.get('Estado') in ('Confirmado','En produccion','Listo'):
            p1 = j.get('Primera Camara')
            if p1:
                primera_camara_count[p1] = primera_camara_count.get(p1, 0) + 1

    # Pagos pendientes
    pagos_pendientes = [p for p in pagos if p.get('Estado de pago') in ('Pendiente','Mitad pagado','En proceso')]
    monto_pendiente = sum(p.get('Monto acordado') or 0 for p in pagos_pendientes)

    # === KPIs del Módulo 14 ===
    from datetime import datetime as _dt, timedelta as _td
    _ahora = _dt.now()
    _cots = ns.list_cotizaciones_full()

    # Leads nuevos (24h)
    kpi_leads_24h = 0
    for l in leads:
        try:
            ct = (l.get('created_time') or '')[:19]
            if ct and (_dt.fromisoformat(ct) > _ahora - _td(hours=24)):
                kpi_leads_24h += 1
        except Exception:
            pass

    # Clientes nuevos este mes
    kpi_clientes_nuevos_mes = 0
    for c in clientes:
        try:
            ct = str(c.get('created_time') or c.get('Fecha alta') or '')[:10]
            if ct and ct[:7] == f'{_ahora.year:04d}-{_ahora.month:02d}':
                kpi_clientes_nuevos_mes += 1
        except Exception:
            pass

    # Por cobrar (suma de Saldo de cotizaciones)
    kpi_por_cobrar = sum(c.get('Saldo (Q)') or 0 for c in _cots)

    # Bodas próximas 90 días + este mes
    kpi_proximas_90d = 0
    kpi_bodas_mes = 0
    for j in jobs:
        try:
            f = j.get('Fecha del evento') or ''
            if not f:
                continue
            d = days_until(f)
            if 0 <= d <= 90:
                kpi_proximas_90d += 1
            if f[:7] == f'{_ahora.year:04d}-{_ahora.month:02d}':
                kpi_bodas_mes += 1
        except Exception:
            pass

    # Cobrado este mes (sum Pagado de cotizaciones aceptadas este mes)
    kpi_cobrado_mes = 0
    for c in _cots:
        try:
            f = str(c.get('Fecha aceptación') or '')[:10]
            if f[:7] == f'{_ahora.year:04d}-{_ahora.month:02d}':
                kpi_cobrado_mes += c.get('Pagado (Q)') or 0
        except Exception:
            pass

    # A pagar partners (liquidaciones pendientes + parciales)
    kpi_a_pagar_partners = sum(
        p.get('Monto acordado') or 0 for p in pagos
        if p.get('Estado de pago') in ('Pendiente', 'Mitad pagado', 'En proceso')
    )

    # Tareas vencidas (jobs con fecha < hoy y estado no listo)
    kpi_tareas_vencidas = 0
    for j in jobs:
        try:
            d = days_until(j.get('Fecha del evento') or '')
            if d is not None and d < 0 and j.get('Estado') in ('Sin empezar', 'Cotizando', 'Lead'):
                kpi_tareas_vencidas += 1
        except Exception:
            pass

    # Tasa de conversión (mes)
    _leads_mes = [l for l in leads if (l.get('created_time') or '')[:7] == f'{_ahora.year:04d}-{_ahora.month:02d}']
    _leads_conv = [l for l in _leads_mes if (l.get('Estado') or '').upper() == 'CONVERTIDO']
    kpi_tasa_conversion = f'{int(len(_leads_conv) / max(len(_leads_mes), 1) * 100)}%'

    return render_template('dashboard.html',
                           jobs=jobs, leads=leads, pagos=pagos, clientes=clientes,
                           proximas_30=proximas_30, proximas_90=proximas_90,
                           ingresos_esperados=ingresos_esperados,
                           pipeline=pipeline, leads_por_estado=leads_por_estado,
                           primera_camara_count=primera_camara_count,
                           pagos_pendientes=pagos_pendientes,
                           monto_pendiente=monto_pendiente,
                           leads_total=len(leads),
                           kpi_leads_24h=kpi_leads_24h,
                           kpi_clientes_nuevos_mes=kpi_clientes_nuevos_mes,
                           kpi_por_cobrar=kpi_por_cobrar,
                           kpi_proximas_90d=kpi_proximas_90d,
                           kpi_bodas_mes=kpi_bodas_mes,
                           kpi_cobrado_mes=kpi_cobrado_mes,
                           kpi_a_pagar_partners=kpi_a_pagar_partners,
                           kpi_tareas_vencidas=kpi_tareas_vencidas,
                           kpi_tasa_conversion=kpi_tasa_conversion,
                           total_payments=total_payments_pagado,
                           total_payments_pending=total_payments_pendiente,
                           revenue_ytd=revenue_ytd,
                           year_current=current_year,
                           revenue_by_year_json=_json.dumps(revenue_by_year),
                           lead_sources_json=_json.dumps(lead_sources),
                           parse_date=parse_date, days_until=days_until, q_money=q_money)


# ============================================================
# JOBS (BODAS)
# ============================================================

@app.route('/jobs')
def jobs_list():
    estado_filtro = request.args.get('estado', '')
    empresa_filtro = request.args.get('empresa', '')
    search = request.args.get('q', '').lower()
    view = request.args.get('view', 'active')  # active | active_upcoming | completed | archived

    # Traer todos los jobs (incluyendo archivados)
    if view == 'archived':
        # Para archivados, usar search con filtro archived=true
        all_jobs = []
        cursor = None
        while True:
            kw = {'data_source_id': ns.DS['JOBS_BODAS'], 'page_size': 100, 'filter': {'property': 'Estado', 'status': {'does_not_equal': 'Listo'}}}
            if cursor:
                kw['start_cursor'] = cursor
            r = ns.client().data_sources.query(**kw)
            all_jobs.extend(r.get('results', []))
            if not r.get('has_more'):
                break
            cursor = r.get('next_cursor')
        # También incluye los archivados via search
        archived_results = []
        cursor = None
        while True:
            kw = {'query': '', 'filter': {'property': 'object', 'value': 'page'}, 'page_size': 100}
            if cursor:
                kw['start_cursor'] = cursor
            r = ns.client().search(**kw)
            for page in r.get('results', []):
                if page.get('archived') and page.get('parent', {}).get('data_source_id') == ns.DS['JOBS_BODAS']:
                    archived_results.append(page)
            if not r.get('has_more'):
                break
            cursor = r.get('next_cursor')
        jobs = [ns._normalize_props(p.get('properties', {})) for p in archived_results]
    else:
        jobs = ns.list_jobs_full()
        # Filtrar archivados (no aparecen en list_jobs_full)
        jobs = [j for j in jobs if not j.get('__archived__', False)]

    hoy = date.today()

    # Vista: filtrar por categoría
    if view == 'active':
        jobs = [j for j in jobs if j.get('Estado') != 'Listo']
    elif view == 'active_upcoming':
        jobs = [j for j in jobs if j.get('Estado') != 'Listo' and days_until(j.get('Fecha del evento')) >= 0]
    elif view == 'completed':
        jobs = [j for j in jobs if j.get('Estado') == 'Listo']

    # Filtros adicionales
    if estado_filtro:
        jobs = [j for j in jobs if j.get('Estado') == estado_filtro]
    if empresa_filtro:
        jobs = [j for j in jobs if j.get('EMPRESA') == empresa_filtro]
    if search:
        jobs = [j for j in jobs if search in (j.get('BODA') or '').lower()
                or search in (j.get('Lugar de evento') or '').lower()]

    # Ordenar por fecha del evento (ASC = más cercana primero)
    def sort_key(j):
        f = j.get('Fecha del evento') or ''
        return f if f else '9999-99-99'
    jobs = sorted(jobs, key=sort_key)

    # Stats (basado en todos, no filtrados)
    all_for_stats = ns.list_jobs_full()
    counts = {}
    for j in all_for_stats:
        st = j.get('Estado') or 'Sin estado'
        counts[st] = counts.get(st, 0) + 1

    # Stats por categoría para mostrar en dropdown
    view_counts = {
        'active': len([j for j in all_for_stats if j.get('Estado') != 'Listo']),
        'active_upcoming': len([j for j in all_for_stats if j.get('Estado') != 'Listo' and days_until(j.get('Fecha del evento')) >= 0]),
        'completed': len([j for j in all_for_stats if j.get('Estado') == 'Listo']),
    }

    # Origen: buscar si el cliente del job vino de un lead
    leads_all = ns.list_leads_full()
    leads_by_cliente = {}
    for l in leads_all:
        cg_ids = l.get('Cliente generado') or []
        if cg_ids:
            leads_by_cliente[cg_ids[0]] = l

    for j in jobs:
        cliente_ids = j.get('Cliente') or []
        if cliente_ids and cliente_ids[0] in leads_by_cliente:
            origin_lead = leads_by_cliente[cliente_ids[0]]
            j['origen_lead'] = {
                'id': origin_lead.get('id'),
                'nombre': origin_lead.get('Nombre'),
            }
        else:
            j['origen_lead'] = None

    # Cotizaciones/facturas por job para resumen operativo tipo Job Overview.
    cotiz_by_job = defaultdict(list)
    try:
        for c in ns.list_cotizaciones_full():
            for jid in (c.get('Job') or []):
                cotiz_by_job[jid].append(c)
    except Exception as e:
        logger.error(f'Error cargando cotizaciones para jobs overview: {e}')

    for j in jobs:
        j['ops'] = enrich_job_ops(j, cotiz_by_job.get(j.get('id'), []))

    return render_template('jobs.html',
                           jobs=jobs,
                           counts=counts,
                           view_counts=view_counts,
                           view=view,
                           estado_filtro=estado_filtro,
                           empresa_filtro=empresa_filtro,
                           search=search,
                           parse_date=parse_date, days_until=days_until, q_money=q_money,
                           status_options=ns.JOB_STATUS_OPTIONS)


@app.route('/jobs/<job_id>')
def job_detail(job_id):
    try:
        job = ns.get_job_full(job_id)
    except Exception as e:
        logger.error(f'Error cargando job {job_id}: {e}')
        abort(404)

    # Pagos del equipo vinculados a este evento (búsqueda por nombre de boda en PAGOS_EQ)
    nombre_boda = (job.get('BODA') or '').strip()
    pagos_rel = []
    if nombre_boda:
        for p in ns.list_pagos_eq_full():
            ev = (p.get('Evento específico') or '').lower()
            if nombre_boda.lower() in ev or ev in nombre_boda.lower():
                pagos_rel.append(p)

    # TIMELINE derivado (eventos del proyecto en orden cronológico)
    timeline = []

    # 1) Job creado
    if job.get('created_time'):
        timeline.append({
            'fecha': job['created_time'][:10],
            'titulo': 'Proyecto creado',
            'detalle': f"{job.get('BODA') or 'Proyecto'}",
            'icono': 'crear',
            'quien': 'Kevin',
        })

    # 2) Cliente vinculado
    cliente_ids = job.get('Cliente') or []
    if cliente_ids:
        try:
            cliente = ns.get_page(cliente_ids[0])
            cliente_created = cliente.get('created_time')
            if cliente_created:
                cliente_nombre = ''.join([t.get('plain_text', '') for t in cliente.get('properties', {}).get('Nombre', {}).get('title', [])])
                timeline.append({
                    'fecha': cliente_created[:10],
                    'titulo': 'Cliente vinculado',
                    'detalle': cliente_nombre or '—',
                    'icono': 'cliente',
                    'quien': 'Sistema',
                })
        except Exception:
            pass

    # 3) Lead origen
    leads_all = ns.list_leads_full()
    origin_lead_nombre = None
    if cliente_ids and cliente_ids[0]:
        for l in leads_all:
            cg = l.get('Cliente generado') or []
            if cg and cg[0] == cliente_ids[0]:
                origin_lead = l
                origin_lead_nombre = origin_lead.get('Nombre')
                if origin_lead.get('created_time'):
                    timeline.append({
                        'fecha': origin_lead['created_time'][:10],
                        'titulo': 'Lead originario',
                        'detalle': origin_lead_nombre or '—',
                        'icono': 'lead',
                        'quien': 'Sistema',
                    })
                break

    # 4) Pago recibido del cliente (FACTURA relacionada al job)
    try:
        cotizaciones = ns.list_cotizaciones_full()
        for c in cotizaciones:
            job_in_cotiz = c.get('Job') or []
            if job_in_cotiz and job_id in job_in_cotiz:
                # Esta cotizacion esta asociada al job
                fecha_envio = c.get('Fecha aceptación')
                if fecha_envio:
                    timeline.append({
                        'fecha': str(fecha_envio)[:10] if isinstance(fecha_envio, str) else str(fecha_envio),
                        'titulo': 'Cotización aceptada',
                        'detalle': f"{c.get('Cotización') or 'Cotización'} · Q{(c.get('Monto total (Q)') or 0):,.0f}".replace(',', ','),
                        'icono': 'cotizacion',
                        'quien': 'Cliente',
                    })
                break
    except Exception:
        pass

    # 5) Cobros realizados (recibidos)
    try:
        cotizaciones = ns.list_cotizaciones_full()
        for c in cotizaciones:
            job_in_cotiz = c.get('Job') or []
            if job_in_cotiz and job_id in job_in_cotiz:
                # Si tiene pagos al cliente reflejados en estado Pagada
                estado_cotiz = c.get('Estado') or ''
                if estado_cotiz == 'Pagada':
                    timeline.append({
                        'fecha': 'Pago total',
                        'titulo': 'Pago completo recibido',
                        'detalle': f"Q{(c.get('Monto total (Q)') or 0):,.0f}".replace(',', ','),
                        'icono': 'pago',
                        'quien': 'Cliente',
                    })
                elif (c.get('Anticipo (Q)') or 0) > 0:
                    timeline.append({
                        'fecha': 'Anticipo',
                        'titulo': 'Anticipo recibido',
                        'detalle': f"Q{(c.get('Anticipo (Q)') or 0):,.0f}".replace(',', ',') + ' (de Q' + f"{(c.get('Monto total (Q)') or 0):,.0f}".replace(',', ',') + ' total)',
                        'icono': 'pago',
                        'quien': 'Cliente',
                    })
                break
    except Exception:
        pass

    # 6) Liquidaciones al equipo hechas
    for p in pagos_rel:
        if p.get('Estado de pago') == 'Pagado':
            timeline.append({
                'fecha': str(p.get('Fecha de pago') or '')[:10],
                'titulo': f'Liquidado a {p.get("Persona") or "equipo"}',
                'detalle': f"Q{p.get('Monto acordado', 0):,.0f}".replace(',', ','),
                'icono': 'liquidado',
                'quien': 'Kevin',
            })

    # 7) Marcas del estado actual
    timeline.append({
        'fecha': 'Estado actual',
        'titulo': f"Estado: {job.get('Estado') or '—'}",
        'detalle': job.get('Notas') or 'Sin notas',
        'icono': 'actual',
        'quien': 'Sistema',
    })

    # Ordenar timeline por fecha descendente (mas reciente primero), pero el actual al final
    eventos_con_fecha = [e for e in timeline if e['fecha'] not in ('Estado actual', 'Pago total', 'Anticipo')]
    eventos_sin_fecha = [e for e in timeline if e['fecha'] in ('Estado actual', 'Pago total', 'Anticipo')]
    eventos_con_fecha.sort(key=lambda e: e['fecha'], reverse=True)
    timeline = eventos_con_fecha + eventos_sin_fecha

    # Workflow timeline state
    estado = job.get('Estado') or ''
    workflow_steps_order = ['Lead', 'Cotizando', 'Propuesta', 'Confirmado', 'En produccion', 'Listo']
    current_idx = -1
    for i, s in enumerate(workflow_steps_order):
        if s.lower() in estado.lower() or estado.lower() in s.lower():
            current_idx = i
            break
    # Mapear estado a step
    if 'Listo' in estado: current_idx = 5
    elif 'Post' in estado: current_idx = 5
    elif 'produccion' in estado: current_idx = 4
    elif 'Confirmado' in estado: current_idx = 3
    elif 'Propuesta' in estado: current_idx = 2
    elif 'Cotizando' in estado: current_idx = 1
    elif 'Lead' in estado: current_idx = 0
    elif 'Sin empezar' in estado: current_idx = -1  # aún no arranca

    workflow_done = {
        'lead': current_idx >= 0,
        'cotizando': current_idx >= 1,
        'propuesta': current_idx >= 2,
        'confirmado': current_idx >= 3,
        'produccion': current_idx >= 4,
        'listo': current_idx >= 5,
    }
    workflow_current = {
        'lead': current_idx == 0,
        'cotizando': current_idx == 1,
        'propuesta': current_idx == 2,
        'confirmado': current_idx == 3,
        'produccion': current_idx == 4,
        'listo': current_idx == 5,
    }
    workflow_progress = max(0, min(100, (current_idx + 1) * 100 // 6))

    # Workflow dates - usar fechas reales del job
    workflow_dates = {}
    if job.get('Fecha anticipo recibido'):
        workflow_dates['cotizando'] = parse_date(job['Fecha anticipo recibido'])[:10] if isinstance(job.get('Fecha anticipo recibido'), str) else ''
    if job.get('Fecha contrato firmado'):
        workflow_dates['confirmado'] = parse_date(job['Fecha contrato firmado'])[:10] if isinstance(job.get('Fecha contrato firmado'), str) else ''
    if job.get('Fecha del evento'):
        workflow_dates['produccion'] = parse_date(job['Fecha del evento'])[:10] if isinstance(job.get('Fecha del evento'), str) else ''

    # Quotes e Invoices - buscar en COTIZ DB
    quotes = []
    invoices = []
    cliente_id = (job.get('cliente') or {}).get('id')
    if cliente_id:
        try:
            cotiz_ds = ns.query('COTIZ', filter_obj={'property': 'Cliente', 'relation': {'contains': cliente_id}})
            for c in cotiz_ds:
                props = ns._normalize_props(c.get('properties', {}))
                props['id'] = c['id']
                if props.get('Estado') == 'Pagada':
                    invoices.append(props)
                else:
                    quotes.append(props)
        except Exception as e:
            logger.error(f'Error cargando cotizaciones: {e}')

    job_ops = enrich_job_ops(job, quotes + invoices)

    return render_template('job_detail.html',
                           job=job,
                           job_ops=job_ops,
                           pagos_rel=pagos_rel,
                           quotes=quotes,
                           invoices=invoices,
                           timeline=timeline,
                           origin_lead_nombre=origin_lead_nombre,
                           workflow_progress=workflow_progress,
                           workflow_done=workflow_done,
                           workflow_current=workflow_current,
                           workflow_dates=workflow_dates,
                           status_options=ns.JOB_STATUS_OPTIONS,
                           partner_foto=ns.PARTNER_FOTO,
                           partner_video=ns.PARTNER_VIDEO,
                           partner_nombres=ns.PARTNER_NAMES,
                           parse_date=parse_date, days_until=days_until, q_money=q_money,
                           fmt_dt=fmt_dt)


# ============================================================
# CLIENTES
# ============================================================

@app.route('/clients')
def clients_list():
    search = request.args.get('q', '').lower()
    estado = request.args.get('estado', '')

    clientes = ns.list_clients_full()
    jobs = ns.list_jobs_full()
    cotiz = ns.query('COTIZ')

    # Index jobs por cliente
    jobs_por_cliente = defaultdict(list)
    for j in jobs:
        for cid in (j.get('Cliente') or []):
            jobs_por_cliente[cid].append(j)

    # Index cotizaciones por cliente (para paquete/precio/pagado)
    cotiz_por_cliente = defaultdict(list)
    for c in cotiz:
        props = ns._normalize_props(c.get('properties', {}))
        for cid_cliente in (props.get('Cliente') or []):
            cotiz_por_cliente[cid_cliente].append(props)

    # Enriquecer cada cliente con su info derivada
    clientes_enriquecidos = []
    for c in clientes:
        cid = c['id']
        jobs_cliente = jobs_por_cliente.get(cid, [])
        cotiz_cliente = cotiz_por_cliente.get(cid, [])

        # Boda más reciente
        job_principal = None
        if jobs_cliente:
            job_principal = sorted(jobs_cliente, key=lambda x: x.get('Fecha del evento') or '9999', reverse=False)[0]

        # Paquete del job (extraer de Notas o del paquete cotización)
        paquete = None
        precio = 0
        pagado = 0
        if cotiz_cliente:
            # Tomar la cotización más reciente
            cotiz_sorted = sorted(cotiz_cliente, key=lambda x: x.get('Fecha de envío') or '', reverse=True)
            if cotiz_sorted:
                principal = cotiz_sorted[0]
                paquete = principal.get('Paquete')
                precio = principal.get('Monto total (Q)') or 0
                pagado = principal.get('Pagado (Q)') or 0

        # Fallback al job
        if not precio and job_principal:
            precio = job_principal.get('Total facturado al cliente (Q)') or 0
            pagado = job_principal.get('Total pagado por cliente (Q)') or 0

        # Boda fecha y lugar
        boda_fecha = job_principal.get('Fecha del evento') if job_principal else None
        boda_lugar = job_principal.get('Lugar de evento') if job_principal else None

        clientes_enriquecidos.append({
            'id': c['id'],
            'nombre': c.get('Nombre') or '',
            'email': c.get('Email') or '',
            'telefono': c.get('Teléfono') or '',
            'estado': c.get('Estado') or 'Activo',
            'tags': c.get('Tags') or [],
            'fuente': c.get('Fuente'),
            'carpeta_drive': c.get('Carpeta Drive'),
            'portal_url': c.get('Portal URL'),
            'galeria_url': c.get('Galería URL'),
            'token': c.get('Token de acceso'),
            'job_id': job_principal['id'] if job_principal else None,
            'job_nombre': job_principal.get('BODA') if job_principal else None,
            'boda_fecha': boda_fecha,
            'boda_lugar': boda_lugar,
            'paquete': paquete,
            'precio': precio,
            'pagado': pagado,
            'total_cotizaciones': len(cotiz_cliente),
            'total_jobs': len(jobs_cliente),
        })

    if search:
        clientes_enriquecidos = [c for c in clientes_enriquecidos if search in (c['nombre'] or '').lower() or search in (c['email'] or '').lower()]
    if estado:
        clientes_enriquecidos = [c for c in clientes_enriquecidos if c['estado'] == estado]

    return render_template('clients.html',
                           clients=clientes_enriquecidos,
                           search=search, estado=estado,
                           estado_options=ns.CLIENT_STATUS_OPTIONS,
                           parse_date=parse_date, q_money=q_money)


@app.route('/clients/<client_id>')
def client_detail(client_id):
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

    return render_template(
        'leads.html',
        leads=leads,
        search='',
        counts=counts,
        fuentes=fuentes_set,
        tipos_evento=tipos_set,
        fuente_filtro='',
        tipo_filtro=''
    )

@app.route('/leads')
def leads_list():
    """Pipeline Kanban de leads con drag-and-drop."""
    from datetime import datetime

    search = (request.args.get('q') or '').strip().lower()

    leads_raw = ns.list_leads_full()

    now = datetime.now()
    for l in leads_raw:
        ct_str = l.get('created_time') or ''
        try:
            ct = datetime.fromisoformat(ct_str.replace('Z', '+00:00')).replace(tzinfo=None)
            l['is_new'] = (now - ct).total_seconds() < 172800
        except Exception:
            l['is_new'] = False

    counts = {}
    for l in leads_raw:
        st = l.get('Estado') or 'Nuevo'
        counts[st] = counts.get(st, 0) + 1

    fuentes_set = sorted(set(l.get('Fuente') for l in leads_raw if l.get('Fuente')))
    tipos_set = sorted(set(l.get('Tipo de evento') for l in leads_raw if l.get('Tipo de evento')))

    return render_template(
        'leads.html',
        leads=leads_raw,
        search=search,
        counts=counts,
        fuentes=fuentes_set,
        tipos_evento=tipos_set,
        fuente_filtro='',
        tipo_filtro=''
    )
@app.route('/leads/<lead_id>')
def lead_detail(lead_id):
    try:
        page = ns.get_page(lead_id)
        lead = ns._normalize_props(page.get('properties', {}))
        lead['id'] = lead_id
    except Exception as e:
        logger.error(f'Error cargando lead {lead_id}: {e}')
        abort(404)

    # Cargar paquetes del DB CONFIG (Módulo 2)
    paquetes = ns.list_paquetes()

    # Cotizaciones previas del lead
    cotizaciones = ns.list_cotizaciones_full()
    cot_del_lead = [c for c in cotizaciones if (c.get('Email') or '').lower() == (lead.get('Email') or '').lower()]

    return render_template('lead_detail.html',
                           lead=lead,
                           paquetes=paquetes,
                           cotizaciones=cot_del_lead,
                           status_options=ns.LEAD_STATUS_OPTIONS,
                           parse_date=parse_date, days_until=days_until, fmt_dt=fmt_dt)


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

@app.route('/payments')
def payments_list():
    """Vista de cobros a clientes (dinero que entra)."""
    estado_filtro = request.args.get('estado', '')

    cotizaciones = ns.list_cotizaciones_full()
    if estado_filtro:
        cotizaciones = [c for c in cotizaciones if c.get('Estado') == estado_filtro]

    total_facturado = sum(c.get('Monto total (Q)') or 0 for c in cotizaciones)
    total_pagado = sum(c.get('Pagado (Q)') or 0 for c in cotizaciones)
    total_saldo = sum(c.get('Saldo (Q)') or 0 for c in cotizaciones)
    total_anticipo = sum(c.get('Anticipo (Q)') or 0 for c in cotizaciones)

    # Status options únicos
    status_options = sorted(set(c.get('Estado') for c in ns.list_cotizaciones_full() if c.get('Estado')))

    return render_template('payments.html',
                           cotizaciones=cotizaciones,
                           total_facturado=total_facturado,
                           total_pagado=total_pagado,
                           total_saldo=total_saldo,
                           total_anticipo=total_anticipo,
                           estado_filtro=estado_filtro,
                           status_options=status_options,
                           parse_date=parse_date, q_money=q_money)


# PAGOS AL EQUIPO (CXP)
# ============================================================

@app.route('/pagos-equipo')
def pagos_equipo_list():
    """Vista de pagos al equipo (dinero que sale)."""
    estado_filtro = request.args.get('estado', '')
    persona_filtro = request.args.get('persona', '')

    pagos = ns.list_pagos_eq_full()
    if estado_filtro:
        pagos = [p for p in pagos if p.get('Estado de pago') == estado_filtro]
    if persona_filtro:
        pagos = [p for p in pagos if p.get('Persona') == persona_filtro]

    pendientes = [p for p in pagos if p.get('Estado de pago') in ('Pendiente','Mitad pagado','En proceso')]
    pagados = [p for p in pagos if p.get('Estado de pago') == 'Pagado']

    total_pendiente = sum(p.get('Monto acordado') or 0 for p in pendientes)
    total_pagado = sum(p.get('Monto acordado') or 0 for p in pagados)

    personas = sorted(set(p.get('Persona') for p in ns.list_pagos_eq_full() if p.get('Persona')))

    return render_template('pagos_equipo.html',
                           pendientes=pendientes,
                           pagados=pagados,
                           total_pendiente=total_pendiente,
                           total_pagado=total_pagado,
                           estado_filtro=estado_filtro,
                           persona_filtro=persona_filtro,
                           personas=personas,
                           status_options=ns.PAGO_STATUS_OPTIONS,
                           parse_date=parse_date, q_money=q_money)


# ============================================================
# PARTNERS (FOTÓGRAFOS / VIDEOGRAFOS)
# ============================================================

@app.route('/partners')
def partners_list():
    estado_filtro = request.args.get('estado', '')
    partners = ns.list_partners_full()
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

@app.route('/api/jobs/<job_id>/status', methods=['POST'])
def api_job_status(job_id):
    data = request.json or request.form
    nuevo_status = data.get('status')
    if not nuevo_status:
        return jsonify({'ok': False, 'error': 'status requerido'}), 400
    res = ns.update_job(job_id, status=nuevo_status)
    return jsonify(res)


@app.route('/api/jobs/<job_id>/notes', methods=['POST'])
def api_job_notes(job_id):
    data = request.json or request.form
    notas = data.get('notas', '')
    res = ns.update_job(job_id, notas=notas)
    return jsonify(res)


@app.route('/api/jobs/<job_id>/notes_produccion', methods=['POST'])
def api_job_notes_prod(job_id):
    data = request.json or request.form
    notas = data.get('notas', '')
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
    res = ns.update_job(job_id, **fields)
    return jsonify(res)


@app.route('/api/jobs/<job_id>/update', methods=['POST'])
def api_job_update(job_id):
    data = request.json or request.form
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


# ============================================================
# API - CREAR COTIZACIÓN / INVOICE desde JOB
# ============================================================

@app.route('/api/jobs/<job_id>/quotes', methods=['POST'])
def api_job_create_quote(job_id):
    """Crea una cotización en Notion COTIZ DB vinculada a este job."""
    data = request.json or request.form
    paquete = data.get('paquete')
    monto = data.get('monto')
    cuotas = data.get('cuotas', '2 (50% + 50%)')

    if not paquete or not monto:
        return jsonify({'ok': False, 'error': 'paquete y monto requeridos'}), 400

    try:
        monto_f = float(monto)
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'monto inválido'}), 400

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

@app.route('/api/payments/<pago_id>/pay', methods=['POST'])
def api_pago_pay(pago_id):
    """Marca un pago como Pagado en Notion + actualiza monto."""
    data = request.json or request.form
    fecha_pago = data.get('fecha_pago') or date.today().isoformat()
    fields = {'estado_pago': 'Pagado', 'fecha_pago': fecha_pago}
    res = ns.update_pago(pago_id, **fields)
    return jsonify(res)


@app.route('/api/payments/<pago_id>/status', methods=['POST'])
def api_pago_status(pago_id):
    data = request.json or request.form
    nuevo = data.get('estado_pago')
    if not nuevo:
        return jsonify({'ok': False, 'error': 'estado_pago requerido'}), 400
    res = ns.update_pago(pago_id, estado_pago=nuevo)
    return jsonify(res)


@app.route('/api/payments/<pago_id>/update', methods=['POST'])
def api_pago_update(pago_id):
    data = request.json or request.form
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
            'EMPRESA': {'select': {'name': 'NORKEVIN'}},
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
    res = ns.update_lead(lead_id, estado=nuevo)
    return jsonify(res)


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
    res = ns.update_client(client_id, **fields)
    return jsonify(res)


# ============================================================
# SETTINGS
# ============================================================

@app.route('/settings')
def settings():
    return render_template('settings.html',
                           ds=ns.DS,
                           job_status_options=ns.JOB_STATUS_OPTIONS,
                           pago_status_options=ns.PAGO_STATUS_OPTIONS,
                           lead_status_options=ns.LEAD_STATUS_OPTIONS)


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(500)
def server_error(e):
    logger.error(f'500: {e}')
    return render_template('500.html', error=str(e)), 500


# ============================================================
# MAIN
# ============================================================

# ============================================================
# FORMULARIO PÚBLICO (crea Lead en Notion)
# ============================================================

@app.route('/contacto')
def formulario_lead():
    """Formulario público para captar leads."""
    return render_template('formulario.html')


@app.route('/api/leads/nuevo', methods=['POST'])
def crear_lead_publico():
    """Crea un nuevo Lead desde el formulario público."""
    data = request.get_json() or {}

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

@app.route('/api/search')
def api_search():
    q = (request.args.get('q') or '').strip().lower()
    if not q or len(q) < 2:
        return jsonify({'results': []})

    results = []

    # Buscar en LEADS
    try:
        for l in ns.list_leads_full():
            nombre = (l.get('Nombre') or '').lower()
            email = (l.get('Email') or '').lower()
            telefono = (l.get('Teléfono') or '').lower()
            if q in nombre or q in email or q in telefono:
                results.append({
                    'type': 'Lead',
                    'title': l.get('Nombre') or '—',
                    'subtitle': f"{l.get('Email') or ''}  ·  {l.get('Teléfono') or ''}",
                    'url': f"/leads/{l.get('id')}",
                })
                if len(results) >= 8: break
    except Exception: pass

    # Buscar en CLIENTES
    try:
        for c in ns.list_clients_full():
            nombre = (c.get('Nombre') or '').lower()
            email = (c.get('Email') or '').lower()
            telefono = (c.get('Teléfono') or '').lower()
            if q in nombre or q in email or q in telefono:
                results.append({
                    'type': 'Cliente',
                    'title': c.get('Nombre') or '—',
                    'subtitle': f"{c.get('Email') or ''}  ·  {c.get('Teléfono') or ''}",
                    'url': f"/clients/{c.get('id')}",
                })
                if len(results) >= 16: break
    except Exception: pass

    # Buscar en JOBS (bodas)
    try:
        for j in ns.list_jobs_full():
            boda = (j.get('BODA') or '').lower()
            lugar = (j.get('Lugar de evento') or '').lower()
            if q in boda or q in lugar:
                results.append({
                    'type': 'Boda',
                    'title': j.get('BODA') or '—',
                    'subtitle': f"{j.get('Lugar de evento') or ''}  ·  {j.get('Fecha del evento') or ''}",
                    'url': f"/jobs/{j.get('id')}",
                })
                if len(results) >= 24: break
    except Exception: pass

    # Buscar en PARTNERS (equipo)
    try:
        for p in ns.list_partners_full():
            nombre = (p.get('Nombre') or '').lower()
            if q in nombre:
                results.append({
                    'type': 'Equipo',
                    'title': p.get('Nombre') or '—',
                    'subtitle': f"{p.get('Tipo') or ''}  ·  {p.get('Email') or ''}",
                    'url': f"/partners/{p.get('id')}",
                })
                if len(results) >= 30: break
    except Exception: pass

    return jsonify({'results': results[:30]})


# ============================================================
# CONFIGURACIÓN (Módulo 2 — vista admin)
# ============================================================

@app.route('/configuracion')
def configuracion_index():
    return render_template('configuracion.html')


@app.route('/api/config/paquetes', methods=['GET'])
def api_config_paquetes_list():
    return jsonify({'paquetes': ns.list_paquetes()})


@app.route('/api/config/paquetes', methods=['POST'])
def api_config_paquetes_create():
    data = request.get_json() or {}
    nombre = data.get('Name')
    if not nombre:
        return jsonify({'ok': False, 'error': 'Nombre requerido'}), 400
    props = {
        'Name': {'title': [{'type': 'text', 'text': {'content': nombre}}]},
        'Tipo': {'select': {'name': 'Paquete'}},
        'Activo': {'checkbox': data.get('Activo', True)},
    }
    if data.get('Marca'):
        props['Marca'] = {'select': {'name': data['Marca']}}
    if data.get('precio_q') is not None:
        props['Precio Q'] = {'number': data['precio_q']}
    if data.get('Notas'):
        props['Notas'] = {'rich_text': [{'type': 'text', 'text': {'content': data['Notas']}}]}
    item = ns.upsert_config_item(None, props)
    return jsonify({'ok': True, 'item': item})


@app.route('/api/config/paquetes/<item_id>', methods=['PATCH'])
def api_config_paquetes_update(item_id):
    data = request.get_json() or {}
    props = {}
    if 'Name' in data:
        props['Name'] = {'title': [{'type': 'text', 'text': {'content': data['Name']}}]}
    if 'precio_q' in data and data['precio_q'] is not None:
        props['Precio Q'] = {'number': data['precio_q']}
    if 'Activo' in data:
        props['Activo'] = {'checkbox': bool(data['Activo'])}
    if 'Notas' in data:
        props['Notas'] = {'rich_text': [{'type': 'text', 'text': {'content': data['Notas'] or ''}}]}
    item = ns.upsert_config_item(item_id, props)
    return jsonify({'ok': True, 'item': item})


@app.route('/api/config/cuentas', methods=['GET'])
def api_config_cuentas_list():
    return jsonify({'cuentas': ns.list_cuentas_activas()})


@app.route('/api/config/cuentas', methods=['POST'])
def api_config_cuentas_create():
    data = request.get_json() or {}
    if not data.get('Name'):
        return jsonify({'ok': False, 'error': 'Nombre requerido'}), 400
    props = {
        'Name': {'title': [{'type': 'text', 'text': {'content': data['Name']}}]},
        'Tipo': {'select': {'name': 'Cuenta Bancaria'}},
        'Activo': {'checkbox': data.get('Activo', True)},
    }
    if data.get('Marca'):
        props['Marca'] = {'select': {'name': data['Marca']}}
    if data.get('Notas'):
        props['Notas'] = {'rich_text': [{'type': 'text', 'text': {'content': data['Notas']}}]}
    item = ns.upsert_config_item(None, props)
    return jsonify({'ok': True, 'item': item})


@app.route('/api/config/cuentas/<item_id>', methods=['PATCH'])
def api_config_cuentas_update(item_id):
    data = request.get_json() or {}
    props = {}
    if 'Name' in data:
        props['Name'] = {'title': [{'type': 'text', 'text': {'content': data['Name']}}]}
    if 'Notas' in data:
        props['Notas'] = {'rich_text': [{'type': 'text', 'text': {'content': data['Notas'] or ''}}]}
    item = ns.upsert_config_item(item_id, props)
    return jsonify({'ok': True, 'item': item})


@app.route('/api/config/reglas', methods=['GET'])
def api_config_reglas_list():
    return jsonify({'reglas': ns.list_reglas_liquidacion()})


@app.route('/api/config/reglas/<item_id>', methods=['PATCH'])
def api_config_reglas_update(item_id):
    data = request.get_json() or {}
    props = {}
    if 'Name' in data:
        props['Name'] = {'title': [{'type': 'text', 'text': {'content': data['Name']}}]}
    if 'porcentaje' in data and data['porcentaje'] is not None:
        props['Porcentaje'] = {'number': data['porcentaje']}
    if 'Notas' in data:
        props['Notas'] = {'rich_text': [{'type': 'text', 'text': {'content': data['Notas'] or ''}}]}
    item = ns.upsert_config_item(item_id, props)
    return jsonify({'ok': True, 'item': item})


@app.route('/api/config/fuentes', methods=['GET'])
def api_config_fuentes_list():
    return jsonify({'fuentes': ns.list_fuentes_activas()})


@app.route('/api/config/fuentes/<item_id>/activo', methods=['PATCH'])
def api_config_fuentes_toggle(item_id):
    data = request.get_json() or {}
    props = {'Activo': {'checkbox': bool(data.get('Activo', True))}}
    item = ns.upsert_config_item(item_id, props)
    return jsonify({'ok': True, 'item': item})


@app.route('/api/config/datos', methods=['GET'])
def api_config_datos_list():
    return jsonify({'datos': ns.list_datos_estudio()})


@app.route('/api/config/datos', methods=['POST'])
def api_config_datos_create():
    data = request.get_json() or {}
    if not data.get('Name'):
        return jsonify({'ok': False, 'error': 'Nombre requerido'}), 400
    props = {
        'Name': {'title': [{'type': 'text', 'text': {'content': data['Name']}}]},
        'Tipo': {'select': {'name': 'Dato del Estudio'}},
        'Activo': {'checkbox': True},
        'Notas': {'rich_text': [{'type': 'text', 'text': {'content': data.get('Notas', '')}}]},
    }
    item = ns.upsert_config_item(None, props)
    return jsonify({'ok': True, 'item': item})


@app.route('/api/config/datos/<item_id>', methods=['PATCH'])
def api_config_datos_update(item_id):
    data = request.get_json() or {}
    props = {}
    if 'Name' in data:
        props['Name'] = {'title': [{'type': 'text', 'text': {'content': data['Name']}}]}
    if 'Notas' in data:
        props['Notas'] = {'rich_text': [{'type': 'text', 'text': {'content': data['Notas'] or ''}}]}
    item = ns.upsert_config_item(item_id, props)
    return jsonify({'ok': True, 'item': item})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8765))
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    logger.info(f'CRM Norkevin arrancando en puerto {port} (debug={debug})')
    app.run(debug=debug, port=port, host='0.0.0.0')
