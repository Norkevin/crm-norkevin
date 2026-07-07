"""
CRM Norkevin - Backend Flask
Arquitectura: Notion-first. SQLite solo para cache de sesión.
"""
import os
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

    return render_template('dashboard.html',
                           jobs=jobs, leads=leads, pagos=pagos, clientes=clientes,
                           proximas_30=proximas_30, proximas_90=proximas_90,
                           ingresos_esperados=ingresos_esperados,
                           pipeline=pipeline, leads_por_estado=leads_por_estado,
                           primera_camara_count=primera_camara_count,
                           pagos_pendientes=pagos_pendientes,
                           monto_pendiente=monto_pendiente,
                           leads_total=len(leads),
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

    return render_template('job_detail.html',
                           job=job,
                           pagos_rel=pagos_rel,
                           quotes=quotes,
                           invoices=invoices,
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

@app.route('/leads')
def leads_list():
    leads = ns.list_leads_full()
    jobs = ns.list_jobs_full()
    clientes = ns.list_clients_full()

    # Index clientes por id para lookup rápido de Cliente generado
    clientes_by_id = {c['id']: c for c in clientes}

    # Enriquecer cada lead con info derivada
    leads_enriquecidos = []
    for l in leads:
        lid = l['id']
        # Si tiene cliente generado, buscar info del cliente
        cliente_gen_ids = l.get('Cliente generado') or []
        cliente_gen_nombre = None
        cliente_gen_email = None
        if cliente_gen_ids:
            cg = clientes_by_id.get(cliente_gen_ids[0])
            if cg:
                cliente_gen_nombre = cg.get('Nombre')
                cliente_gen_email = cg.get('Email')

        leads_enriquecidos.append({
            'id': lid,
            'nombre': l.get('Nombre') or '',
            'email': l.get('Email') or '',
            'telefono': l.get('Teléfono') or '',
            'estado': l.get('Estado') or 'Nuevo',
            'fuente': l.get('Fuente'),
            'tipo_evento': l.get('Tipo de evento'),
            'fecha_tentativa': l.get('Fecha tentativa del evento'),
            'locacion': l.get('Locación tentativa'),
            'presupuesto': l.get('Presupuesto estimado'),
            'tags': l.get('Tags') or [],
            'notas': l.get('Notas') or '',
            'proximo_followup': l.get('Próximo follow-up'),
            'cliente_generado_id': cliente_gen_ids[0] if cliente_gen_ids else None,
            'cliente_generado_nombre': cliente_gen_nombre,
            'cliente_generado_email': cliente_gen_email,
            'ultimo_contacto': l.get('Último acceso') or l.get('Fecha primer contacto'),
        })

    # Kanban: agrupar por estado
    kanban = {st: [] for st in ns.LEAD_STATUS_OPTIONS}
    for l in leads_enriquecidos:
        st = l['estado'] or 'Nuevo'
        if st not in kanban:
            kanban[st] = []
        kanban[st].append(l)

    counts = {st: len(items) for st, items in kanban.items()}

    return render_template('leads.html',
                           leads=leads_enriquecidos,
                           kanban=kanban,
                           counts=counts,
                           status_options=ns.LEAD_STATUS_OPTIONS,
                           parse_date=parse_date, days_until=days_until)


@app.route('/leads/<lead_id>')
def lead_detail(lead_id):
    try:
        page = ns.get_page(lead_id)
        lead = ns._normalize_props(page.get('properties', {}))
        lead['id'] = lead_id
    except Exception as e:
        logger.error(f'Error cargando lead {lead_id}: {e}')
        abort(404)

    return render_template('lead_detail.html',
                           lead=lead,
                           status_options=ns.LEAD_STATUS_OPTIONS,
                           parse_date=parse_date, days_until=days_until, fmt_dt=fmt_dt)


# ============================================================
# PAYMENTS (PAGOS AL EQUIPO)
# ============================================================

@app.route('/payments')
def payments_list():
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

    # Personas únicas
    personas = sorted(set(p.get('Persona') for p in ns.list_pagos_eq_full() if p.get('Persona')))

    return render_template('payments.html',
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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8765))
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    logger.info(f'CRM Norkevin arrancando en puerto {port} (debug={debug})')
    app.run(debug=debug, port=port, host='0.0.0.0')