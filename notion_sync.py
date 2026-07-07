"""
notion_sync.py - Capa de sincronización con Notion para CRM Norkevin
Centraliza todas las llamadas a la API de Notion para que app.py no toque el cliente directamente.
"""
from dotenv import load_dotenv
import os
import logging
from typing import Any, Dict, List, Optional

load_dotenv()
from notion_client import Client

logger = logging.getLogger(__name__)

# Cliente Notion
_client: Optional[Client] = None

def client() -> Client:
    global _client
    if _client is None:
        token = os.environ.get('NOTION_TOKEN')
        if not token:
            raise RuntimeError('NOTION_TOKEN no configurado en .env')
        _client = Client(auth=token)
    return _client

# Data sources canónicos (Notion API 2025+)
DS = {
    'JOBS_BODAS': '24456821-236e-81d1-939b-000b614b0937',
    'CLIENTES':   '309f50dd-944e-4af1-ae0d-625c1c485298',
    'LEADS':      '06ddcd5d-db6d-4b33-ac3e-9160369e08ee',
    'PAGOS_EQ':   '24056821-236e-8092-875b-000b196c3eed',
    'COTIZ':      '6f0bc40e-df95-4608-98e4-f2b2f74e6c92',
    'PARTNERS':   '35756821-236e-80d6-b168-000bef2d69b4',
}

def _strip(d: Dict[str, Any]) -> Dict[str, Any]:
    """Quita metadata interna de Notion."""
    d.pop('object', None)
    d.pop('id', None) if 'id' in d else None
    return d


# ============================================================
# LEER
# ============================================================

def query(ds_name: str, filter_obj: Optional[Dict] = None, sorts: Optional[List] = None,
          page_size: int = 100) -> List[Dict[str, Any]]:
    """Query genérico a un data source. Retorna lista de páginas completas."""
    ds_id = DS[ds_name]
    pages: List[Dict] = []
    cursor = None
    while True:
        kw = {'data_source_id': ds_id, 'page_size': min(page_size, 100)}
        if filter_obj:
            kw['filter'] = filter_obj
        if sorts:
            kw['sorts'] = sorts
        if cursor:
            kw['start_cursor'] = cursor
        r = client().data_sources.query(**kw)
        pages.extend(r.get('results', []))
        if not r.get('has_more'):
            break
        cursor = r.get('next_cursor')
    return pages


def get_page(page_id: str) -> Dict[str, Any]:
    """Obtiene una página por ID (sin propiedades expandidas)."""
    return client().pages.retrieve(page_id=page_id)


def get_page_props(page_id: str) -> Dict[str, Any]:
    """Obtiene SOLO las propiedades de una página en formato limpio."""
    p = get_page(page_id)
    return _normalize_props(p.get('properties', {}))


def _normalize_props(props: Dict[str, Any]) -> Dict[str, Any]:
    """Convierte props crudas de Notion a dict plano {nombre: valor}."""
    out: Dict[str, Any] = {}
    for name, p in props.items():
        t = p.get('type')
        if t == 'title':
            arr = p['title']
            out[name] = arr[0]['plain_text'] if arr else ''
        elif t == 'rich_text':
            arr = p['rich_text']
            out[name] = ''.join(x.get('plain_text', '') for x in arr)
        elif t == 'select':
            sel = p.get('select')
            out[name] = sel.get('name') if sel else None
        elif t == 'multi_select':
            out[name] = [x.get('name') for x in p.get('multi_select', [])]
        elif t == 'status':
            st = p.get('status')
            out[name] = st.get('name') if st else None
        elif t == 'date':
            d = p.get('date')
            out[name] = d.get('start') if d else None
        elif t == 'checkbox':
            out[name] = bool(p.get('checkbox'))
        elif t == 'number':
            out[name] = p.get('number')
        elif t == 'url':
            out[name] = p.get('url')
        elif t == 'email':
            out[name] = p.get('email')
        elif t == 'phone_number':
            out[name] = p.get('phone_number')
        elif t == 'relation':
            out[name] = [x.get('id') for x in p.get('relation', [])]
        elif t == 'files':
            out[name] = [{'name': f.get('name'), 'url': f.get('external', {}).get('url') or f.get('file', {}).get('url')}
                         for f in p.get('files', [])]
        elif t == 'formula':
            f = p.get('formula', {})
            if f.get('type') == 'number':
                out[name] = f.get('number')
            elif f.get('type') == 'string':
                out[name] = f.get('string')
            elif f.get('type') == 'date':
                d = f.get('date')
                out[name] = d.get('start') if d else None
            else:
                out[name] = None
        else:
            out[name] = None
    return out


def list_jobs_full() -> List[Dict[str, Any]]:
    """Todos los jobs con props normalizadas + cliente embebido."""
    pages = query('JOBS_BODAS', sorts=[{'property': 'Fecha del evento', 'direction': 'ascending'}])
    out = []
    for p in pages:
        props = _normalize_props(p.get('properties', {}))
        props['id'] = p['id']
        props['created_time'] = p.get('created_time')
        props['last_edited_time'] = p.get('last_edited_time')
        out.append(props)
    return out


def get_job_full(job_id: str) -> Dict[str, Any]:
    """Job completo con cliente + cotizacion + pagos del equipo."""
    page = get_page(job_id)
    props = _normalize_props(page.get('properties', {}))
    props['id'] = page['id']
    props['created_time'] = page.get('created_time')
    props['last_edited_time'] = page.get('last_edited_time')

    # Cliente embebido
    cliente_ids = props.get('Cliente') or []
    props['cliente'] = None
    if cliente_ids:
        cp = get_page(cliente_ids[0])
        props['cliente'] = _normalize_props(cp.get('properties', {}))
        props['cliente']['id'] = cliente_ids[0]

    # Pagos del equipo relacionados (por nombre de boda)
    return props


def list_clients_full() -> List[Dict[str, Any]]:
    pages = query('CLIENTES', sorts=[{'property': 'Nombre', 'direction': 'ascending'}])
    out = []
    for p in pages:
        props = _normalize_props(p.get('properties', {}))
        props['id'] = p['id']
        out.append(props)
    return out


def list_leads_full() -> List[Dict[str, Any]]:
    pages = query('LEADS', sorts=[{'property': 'Fecha tentativa del evento', 'direction': 'ascending'}])
    out = []
    for p in pages:
        props = _normalize_props(p.get('properties', {}))
        props['id'] = p['id']
        out.append(props)
    return out


def list_pagos_eq_full() -> List[Dict[str, Any]]:
    pages = query('PAGOS_EQ', sorts=[{'property': 'Fecha del evento', 'direction': 'ascending'}])
    out = []
    for p in pages:
        props = _normalize_props(p.get('properties', {}))
        props['id'] = p['id']
        out.append(props)
    return out


def list_partners_full() -> List[Dict[str, Any]]:
    pages = query('PARTNERS', sorts=[{'property': 'Nombre', 'direction': 'ascending'}])
    out = []
    for p in pages:
        props = _normalize_props(p.get('properties', {}))
        props['id'] = p['id']
        out.append(props)
    return out


# ============================================================
# ESCRIBIR (PATCH)
# ============================================================

def _prop(name: str, value: Any, ptype: str) -> Dict[str, Any]:
    """Helper para construir un objeto de propiedad Notion."""
    if ptype == 'select':
        return {name: {'select': {'name': value} if value else None}}
    if ptype == 'status':
        return {name: {'status': {'name': value} if value else None}}
    if ptype == 'rich_text':
        if not isinstance(value, str):
            value = str(value or '')
        return {name: {'rich_text': [{'type': 'text', 'text': {'content': value[:1900]}}]}}
    if ptype == 'title':
        return {name: {'title': [{'type': 'text', 'text': {'content': str(value)}}]}}
    if ptype == 'date':
        return {name: {'date': {'start': value} if value else None}}
    if ptype == 'checkbox':
        return {name: {'checkbox': bool(value)}}
    if ptype == 'number':
        return {name: {'number': float(value) if value is not None else None}}
    if ptype == 'url':
        return {name: {'url': value if value else None}}
    if ptype == 'multi_select':
        if not isinstance(value, list):
            value = [value] if value else []
        return {name: {'multi_select': [{'name': v} for v in value]}}
    raise ValueError(f'Tipo de propiedad no soportado: {ptype}')


def update_page(page_id: str, updates: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """PATCH una página con un dict {prop_name: prop_value} ya en formato Notion."""
    try:
        r = client().pages.update(page_id=page_id, properties=updates)
        return {'ok': True, 'id': page_id}
    except Exception as e:
        logger.error(f'Error actualizando página {page_id}: {e}')
        return {'ok': False, 'error': str(e)}


# Wrappers específicos por entidad (los que usa app.py)
def update_job(job_id: str, **fields) -> Dict[str, Any]:
    """Update job. fields acepta: status, notas, notas_produccion, primera_camara, segunda_camara,
    videografo_1, videografo_2, asistencia, confirmado, confirmado_video, confirmado_video_2,
    fecha_evento, lugar_evento, hora_ceremonia."""
    TYPE_MAP = {
        # nombre_python -> (nombre_notion, tipo)
        'status':                 ('Estado',                       'status'),
        'notas':                  ('NOTAS',                        'rich_text'),
        'notas_produccion':       ('Notas de producción',          'rich_text'),
        'primera_camara':         ('Primera Camara',               'select'),
        'segunda_camara':         ('Segunda Camara',               'select'),
        'videografo_1':           ('Videografo 1',                 'select'),
        'videografo_2':           ('Videografo 2',                 'select'),
        'asistencia':             ('Asistencia',                   'select'),
        'confirmado':             ('Confirmado',                   'checkbox'),
        'confirmado_video':       ('Confirmado video',             'checkbox'),
        'confirmado_video_2':     ('Confirmado video 2',           'checkbox'),
        'confirmado_1':           ('Confirmado (1)',               'checkbox'),
        'fecha_evento':           ('Fecha del evento',             'date'),
        'fecha_entrega_estimada': ('Fecha estimada de entrega',    'date'),
        'fecha_entrega_real':     ('Fecha de entrega real',        'date'),
        'fecha_anticipo':         ('Fecha anticipo recibido',      'date'),
        'fecha_contrato':         ('Fecha contrato firmado',       'date'),
        'lugar_evento':           ('Lugar de evento',              'rich_text'),
        'paquete':                ('Notas de producción',          'rich_text'),  # no hay paquete directo
        'total_pagado':           ('Total pagado por cliente (Q)', 'number'),
        'total_facturado':        ('Total facturado al cliente (Q)','number'),
        'smart_file_url':         ('Link al Smart File',           'url'),
        'cotizacion_pdfs':        ('COTIZACION PDFS',              'url'),
    }
    props = {}
    for k, v in fields.items():
        if k not in TYPE_MAP:
            continue
        notion_name, ptype = TYPE_MAP[k]
        # casos especiales
        if ptype == 'select' and v in ('NO APLICA', 'PENDIENTE', ''):
            props.update(_prop(notion_name, v if v else None, 'select'))
        elif ptype == 'select' and v == '__CLEAR__':
            props.update(_prop(notion_name, None, 'select'))
        else:
            props.update(_prop(notion_name, v, ptype))
    if not props:
        return {'ok': False, 'error': 'Sin campos para actualizar'}
    return update_page(job_id, props)


def update_lead(lead_id: str, **fields) -> Dict[str, Any]:
    TYPE_MAP = {
        'estado':          ('Estado',                  'status'),
        'notas':           ('Notas',                   'rich_text'),
        'historial':       ('Historial interacciones', 'rich_text'),
        'presupuesto':     ('Presupuesto estimado',    'select'),
        'proximo_followup':('Próximo follow-up',       'date'),
        'email':           ('Email',                   'email'),
        'telefono':        ('Teléfono',                'phone_number'),
        'fecha_evento':    ('Fecha tentativa del evento','date'),
        'tipo_evento':     ('Tipo de evento',          'select'),
        'ubicacion':       ('Locación tentativa',      'rich_text'),
        'fuente':          ('Fuente',                  'select'),
        'tags':            ('Tags',                    'multi_select'),
    }
    props = {}
    for k, v in fields.items():
        if k not in TYPE_MAP:
            continue
        notion_name, ptype = TYPE_MAP[k]
        props.update(_prop(notion_name, v, ptype))
    if not props:
        return {'ok': False, 'error': 'Sin campos para actualizar'}
    return update_page(lead_id, props)


def update_client(client_id: str, **fields) -> Dict[str, Any]:
    TYPE_MAP = {
        'nombre':          ('Nombre',                  'title'),
        'telefono':        ('Teléfono',                'phone_number'),
        'telefono_secundario':('Teléfono secundario',  'phone_number'),
        'email':           ('Email',                   'email'),
        'portal_url':      ('Portal URL',              'url'),
        'galeria_url':     ('Galería URL',             'url'),
        'galeria_cliente_pwd': ('Galería contraseña cliente','rich_text'),
        'galeria_invitado_pwd': ('Galería contraseña invitado','rich_text'),
        'token_acceso':    ('Token de acceso',         'rich_text'),
        'tags':            ('Tags',                    'multi_select'),
        'estado':          ('Estado',                  'status'),
        'fuente':          ('Fuente',                  'select'),
        'notas':           ('Notas',                   'rich_text'),
        'direccion_fact':  ('Dirección facturación',   'rich_text'),
        'carpeta_drive':   ('Carpeta Drive',           'url'),
    }
    props = {}
    for k, v in fields.items():
        if k not in TYPE_MAP:
            continue
        notion_name, ptype = TYPE_MAP[k]
        props.update(_prop(notion_name, v, ptype))
    if not props:
        return {'ok': False, 'error': 'Sin campos para actualizar'}
    return update_page(client_id, props)


def update_pago(pago_id: str, **fields) -> Dict[str, Any]:
    TYPE_MAP = {
        'estado_pago':     ('Estado de pago',  'status'),
        'fecha_pago':      ('Fecha de pago',   'date'),
        'monto_acordado':  ('Monto acordado',  'number'),
        'comprobante_url': ('Comprobante',     'url'),
        'evento':          ('Evento específico','rich_text'),
    }
    props = {}
    for k, v in fields.items():
        if k not in TYPE_MAP:
            continue
        notion_name, ptype = TYPE_MAP[k]
        props.update(_prop(notion_name, v, ptype))
    if not props:
        return {'ok': False, 'error': 'Sin campos para actualizar'}
    return update_page(pago_id, props)


# ============================================================
# HELPERS para la UI
# ============================================================

JOB_STATUS_OPTIONS = ['Sin empezar', 'En progreso', 'Listo', 'Lead', 'Cotizando', 'Confirmado', 'En produccion', 'Post produccion']
PAGO_STATUS_OPTIONS = ['Pendiente', 'Mitad pagado', 'En proceso', 'Pagado']
LEAD_STATUS_OPTIONS = ['Nuevo', 'Contactado', 'Cotizando', 'Propuesta Enviada', 'Negociando', 'Convertido', 'Perdido']
CLIENT_STATUS_OPTIONS = ['Activo', 'Inactivo', 'Archivado']

PARTNER_NAMES = [
    'Alfredo Yuman', 'Josema', 'Pablo Rubio', 'Ramiro Cruz', 'Erick Gonzales',
    'Henry Gil', 'Alejandro Mazariegos', 'Jose Teque', 'Harder Capriel',
    'Luis Lemus', 'PENDIENTE', 'NO APLICA',
]
PARTNER_FOTO = ['Ramiro Cruz', 'Josema', 'Pablo Rubio', 'Alfredo Yuman', 'Henry Gil',
                'Erick Gonzales', 'Alejandro Mazariegos', 'Luis Lemus', 'NO APLICA', 'PENDIENTE']
PARTNER_VIDEO = ['Jose Teque', 'Alfredo Yuman', 'Henry Gil', 'Pablo Rubio', 'Erick Gonzales',
                 'Harder Capriel', 'NO APLICA', 'PENDIENTE']