"""
llenar_crm.py - Llena el CRM con info plausible para visualizar.

Solo ADITIVO:
- Completa campos vacíos en registros existentes (Jobs, Leads, Clientes)
- Agrega ~15 leads demo nuevos al final del pipeline
- Agrega ~8 cotizaciones/Smart Files demo

NO borra ni renombra nada.
"""
import sys
import os
import random
import secrets
from datetime import date, timedelta
from dotenv import load_dotenv

sys.path.insert(0, '.')
load_dotenv()
import notion_sync as ns
from notion_client import Client

client = Client(auth=os.environ['NOTION_TOKEN'])

# ============================================================
# CONFIG
# ============================================================
DRY_RUN = os.environ.get('DRY_RUN', '0') == '1'
random.seed(42)  # resultados reproducibles

# Paquetes y precios típicos
PAQUETES = {
    'Foto Silver':    (9500, 'Silver'),
    'Foto Gold':      (13500, 'Gold'),
    'Foto Platinum':  (17500, 'Platinum'),
    'Video Silver':   (9500, 'Silver'),
    'Video Gold':     (13500, 'Gold'),
    'Video Platinum': (17500, 'Platinum'),
    'Mix Gold':       (20500, 'Gold'),
    'Mix Platinum':   (29500, 'Platinum'),
}

LUGARES_GT = [
    'Casa Santo Domingo, Antigua Guatemala',
    'Hotel Atitlán, Panajachel',
    'Porta Hotel Antigua',
    'Finca San Cayetano',
    'Hacienda San Jerónimo',
    'Jardín Botánico, Guatemala',
    'Hotel Westin Camino Real',
    'Casa Colonial, Antigua',
    'Roquefort, Guatemala',
    'Hotel Casa Santo Domingo',
    'Villa Bokeh, Guatemala',
    'Cerro de la Cruz, Antigua',
    'Playa Blanca, Monterrico',
]

WP_FAMILIAS = ['Isalim', 'Momenti', 'Geraldine Barberena', 'Fenny Torres', 'Boda de portada', 'Pendiete']

NOMBRES_NOVIO = ['Sebastián','Diego','Mateo','Alejandro','Luis','Carlos','Andrés','José','Pablo','Daniel','Ricardo','Fernando','José Manuel','Eduardo','Tomás']
NOMBRES_NOVIA = ['Valeria','Camila','Sofía','Isabela','Andrea','María José','Daniela','Paula','Carolina','Fernanda','Melissa','Adriana','Ana Lucía','Jimena','Victoria']

# ============================================================
# HELPERS
# ============================================================

def _prop(name, value, ptype):
    """Wrapper para construir propiedad Notion."""
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
    if ptype == 'phone_number':
        return {name: {'phone_number': value if value else None}}
    if ptype == 'email':
        return {name: {'email': value if value else None}}


def safe_update(page_id, props):
    if DRY_RUN:
        return {'ok': True, 'dry_run': True, 'id': page_id}
    try:
        client.pages.update(page_id=page_id, properties=props)
        return {'ok': True, 'id': page_id}
    except Exception as e:
        return {'ok': False, 'error': str(e), 'id': page_id}


def random_token(n=24):
    return secrets.token_urlsafe(n)[:n].replace('-', 'a').replace('_', 'b')


# ============================================================
# 1. LLENAR HUECOS EN JOBS EXISTENTES
# ============================================================

def llenar_jobs():
    jobs = ns.list_jobs_full()
    updates = []
    for j in jobs:
        props = {}
        boda = (j.get('BODA') or '').lower()
        # Total facturado - estimar según tipo de evento
        if not j.get('Total facturado al cliente (Q)'):
            # Paquete "wedding" típico: Q20K, civil: Q8K, compromiso: Q5K, save the date: Q2K
            tipo = (j.get('Tipo de evento') or '').lower()
            if 'civil' in tipo:
                fact = random.choice([6000, 8000, 10000, 12000])
            elif 'save' in tipo:
                fact = 2000
            elif 'graduacion' in tipo or 'graduación' in tipo:
                fact = 5000
            else:  # boda / boda religiosa / wedding
                fact = random.choice([13500, 17500, 20500, 29500])
            props.update(_prop('Total facturado al cliente (Q)', fact, 'number'))

        # Total pagado - 50% si es confirmado, 100% si está en Listo/Post, 0 si no
        if not j.get('Total pagado por cliente (Q)'):
            fact = props.get('Total facturado al cliente (Q)', {}).get('number') or j.get('Total facturado al cliente (Q)') or 0
            estado = j.get('Estado') or ''
            if 'Listo' in estado or 'Post' in estado:
                paid = fact
            elif 'Confirmado' in estado or 'produccion' in estado:
                paid = fact * 0.5
            else:
                paid = 0
            props.update(_prop('Total pagado por cliente (Q)', paid, 'number'))

        # Smart File URL placeholder (no tocar si ya existe)
        if not j.get('Link al Smart File') and (j.get('Estado') in ('Confirmado','En produccion','Cotizando') or 'Listo' in (j.get('Estado') or '')):
            token = random_token(20)
            url = f'https://norkevinphoto.com/portal/?t={token}&c=DEMO-{secrets.token_hex(4)}'
            props.update(_prop('Link al Smart File', url, 'url'))

        if props:
            updates.append((j['id'], boda, props))

    print(f'JOBS: {len(updates)} updates pendientes')
    success = 0
    for page_id, boda, props in updates:
        r = safe_update(page_id, props)
        if r['ok']:
            success += 1
    print(f'JOBS: {success}/{len(updates)} OK')


# ============================================================
# 2. LLENAR HUECOS EN LEADS EXISTENTES
# ============================================================

def llenar_leads():
    leads = ns.list_leads_full()
    updates = []
    hoy = date.today()
    for l in leads:
        props = {}
        # Tags según presupuesto y estado
        if not l.get('Tags'):
            tags = []
            presupuesto = l.get('Presupuesto estimado') or ''
            if 'Mas de' in presupuesto or '35000' in presupuesto:
                tags.append('VIP')
            if 'No definido' not in presupuesto and presupuesto:
                tags.append('Recomienda')
            estado = l.get('Estado') or ''
            if estado == 'Nuevo':
                tags.append('Urgente')
            if tags:
                props.update(_prop('Tags', tags, 'multi_select'))

        # Próximo follow-up si falta
        if not l.get('Próximo follow-up'):
            estado = l.get('Estado') or 'Nuevo'
            if estado == 'Nuevo':
                days = random.randint(1, 3)
            elif estado == 'Contactado':
                days = random.randint(2, 5)
            elif estado == 'Cotizando':
                days = random.randint(5, 10)
            elif estado == 'Propuesta Enviada':
                days = random.randint(3, 7)
            elif estado == 'Negociando':
                days = random.randint(2, 4)
            else:
                days = random.randint(7, 14)
            fu = (hoy + timedelta(days=days)).isoformat()
            props.update(_prop('Próximo follow-up', fu, 'date'))

        if props:
            updates.append((l['id'], l.get('Nombre'), props))

    print(f'LEADS: {len(updates)} updates pendientes')
    success = 0
    for page_id, nombre, props in updates:
        r = safe_update(page_id, props)
        if r['ok']:
            success += 1
    print(f'LEADS: {success}/{len(updates)} OK')


# ============================================================
# 3. LLENAR HUECOS EN CLIENTES EXISTENTES
# ============================================================

def llenar_clientes():
    clientes = ns.list_clients_full()
    updates = []
    for c in clientes:
        props = {}
        # Tags
        if not c.get('Tags'):
            tags = []
            fuente = c.get('Fuente') or ''
            if 'Recomendaci' in fuente:
                tags.append('Recomienda')
            if c.get('Estado') == 'Activo':
                tags.append('VIP' if random.random() < 0.3 else 'Recurrente')
            if tags:
                props.update(_prop('Tags', tags, 'multi_select'))

        # Carpeta Drive placeholder
        if not c.get('Carpeta Drive') and c.get('Estado') == 'Activo':
            token = secrets.token_hex(8)
            url = f'https://drive.google.com/drive/folders/DEMO-{token}'
            props.update(_prop('Carpeta Drive', url, 'url'))

        # Galería URL placeholder
        if not c.get('Galería URL') and c.get('Estado') == 'Activo':
            token = random_token(20)
            url = f'https://norkevinphoto.com/galeria/?t={token}&c=DEMO'
            props.update(_prop('Galería URL', url, 'url'))

        if props:
            updates.append((c['id'], c.get('Nombre'), props))

    print(f'CLIENTES: {len(updates)} updates pendientes')
    success = 0
    for page_id, nombre, props in updates:
        r = safe_update(page_id, props)
        if r['ok']:
            success += 1
    print(f'CLIENTES: {success}/{len(updates)} OK')


# ============================================================
# 4. AGREGAR LEADS DEMO NUEVOS
# ============================================================

NOMBRES_DEMO_PAREJAS = [
    ('Sebastián & Valentina Reyes', 'Boda', 'Q20000 a Q35000'),
    ('Diego & Sofía Méndez', 'Boda', 'Mas de Q35000'),
    ('Mateo & Isabela Torres', 'Save the date', 'Menos de Q5000'),
    ('Alejandro & Camila Juárez', 'Boda', 'Q20000 a Q35000'),
    ('Luis & Andrea López', 'Boda', 'Q10000 a Q20000'),
    ('Carlos & Daniela Pereira', 'Compromiso', 'Menos de Q5000'),
    ('Andrés & María José Solís', 'Boda civil', 'Menos de Q5000'),
    ('José & Paula Ramírez', 'Boda', 'Q10000 a Q20000'),
    ('Pablo & Carolina Estrada', 'Boda', 'Q20000 a Q35000'),
    ('Daniel & Fernanda Ortiz', 'Aniversario', 'Q5000 a Q10000'),
    ('Ricardo & Melissa Salazar', 'Boda', 'Mas de Q35000'),
    ('Fernando & Adriana Bonilla', 'Save the date', 'Menos de Q5000'),
    ('Tomás & Jimena Castillo', 'Boda', 'Q10000 a Q20000'),
    ('Eduardo & Victoria Pérez', 'Boda', 'Q20000 a Q35000'),
    ('Roberto & Ana Lucía García', 'Boda civil', 'Menos de Q5000'),
]

def agregar_leads_demo():
    leads_added = []
    ds_id = ns.DS['LEADS']
    hoy = date.today()

    for i, (nombre, tipo, presupuesto) in enumerate(NOMBRES_DEMO_PAREJAS):
        # Variar fechas tentativas entre +30d y +300d
        fecha_evento = hoy + timedelta(days=random.randint(30, 300))
        # Variar fuentes
        fuente = random.choice(['Instagram', 'WhatsApp', 'Web', 'Recomendación', 'Facebook'])
        # Variar estados para que se vea el kanban lleno
        estados_pool = ['Nuevo', 'Nuevo', 'Nuevo', 'Contactado', 'Contactado', 'Cotizando', 'Propuesta Enviada']
        estado = random.choice(estados_pool)

        # Tags
        tags = []
        if 'Mas de' in presupuesto:
            tags.append('VIP')
        if estado == 'Nuevo':
            tags.append('Urgente')
        tags.append(random.choice(['Recomienda', 'Recurrente']) if random.random() < 0.4 else 'Recomienda')

        # Próximo follow-up
        if estado == 'Nuevo':
            fu_days = random.randint(1, 3)
        elif estado == 'Contactado':
            fu_days = random.randint(2, 5)
        elif estado == 'Cotizando':
            fu_days = random.randint(5, 10)
        else:
            fu_days = random.randint(3, 7)
        fu = hoy + timedelta(days=fu_days)

        # Teléfono/email ficticios
        tels = [f'+502 5{random.randint(100,999)} {random.randint(1000,9999)}' for _ in range(1)]
        emails = [f'{nombre.split(" & ")[0].lower().replace(" ", "").replace("á","a").replace("é","e")}@demo.com']

        # Notas con info plausible
        notas = f'Lead generado automáticamente para visualización. Interesados en {tipo.lower()}. Contactar por WhatsApp.'

        props = {
            'Nombre': {'title': [{'type': 'text', 'text': {'content': nombre + ' [DEMO]'}}]},
            'Estado': {'status': {'name': estado}},
            'Fuente': {'select': {'name': fuente}},
            'Presupuesto estimado': {'select': {'name': presupuesto}},
            'Tipo de evento': {'select': {'name': tipo}},
            'Fecha tentativa del evento': {'date': {'start': fecha_evento.isoformat()}},
            'Próximo follow-up': {'date': {'start': fu.isoformat()}},
            'Locación tentativa': {'rich_text': [{'type': 'text', 'text': {'content': random.choice(LUGARES_GT)}}]},
            'Teléfono': {'phone_number': tels[0]},
            'Email': {'email': emails[0]},
            'Tags': {'multi_select': [{'name': t} for t in tags]},
            'Notas': {'rich_text': [{'type': 'text', 'text': {'content': notas}}]},
        }

        if DRY_RUN:
            leads_added.append(nombre)
            continue

        try:
            r = client.pages.create(
                parent={'data_source_id': ds_id},
                properties=props,
            )
            leads_added.append(nombre)
        except Exception as e:
            print(f'  FAIL {nombre}: {e}')

    print(f'LEADS DEMO: {len(leads_added)} agregados')


# ============================================================
# 5. AGREGAR COTIZACIONES DEMO
# ============================================================

def agregar_cotizaciones_demo():
    cotiz_added = []
    ds_id = ns.DS['COTIZ']

    clientes = ns.list_clients_full()
    # Tomar clientes reales para relacionar cotizaciones
    clientes_sample = random.sample(clientes, min(8, len(clientes))) if clientes else []

    for i, c in enumerate(clientes_sample):
        paquete_nombre = random.choice(list(PAQUETES.keys()))
        precio, _ = PAQUETES[paquete_nombre]
        cuotas = random.choice(['2 (50% + 50%)', '3 (33% + 33% + 34%)', '2 (50% + 50%)', '1 (total al reservar)'])
        anticipo_pct = {'1 (total al reservar)': 1.0, '2 (50% + 50%)': 0.5, '3 (33% + 33% + 34%)': 0.33}.get(cuotas, 0.5)
        anticipo = int(precio * anticipo_pct)
        pagado = anticipo if random.random() < 0.4 else int(anticipo * random.uniform(0, 0.8))

        # Token Smart File
        token = random_token(24)
        smart_url = f'https://norkevinphoto.com/portal/?t={token}&c={c["id"][:8]}'
        recurrente_url = f'https://app.recurrente.com/checkout/demo-{random_token(12)}'

        estados_pool = ['Enviada', 'Vista por cliente', 'Aceptada', 'Pagada']
        estado = random.choice(estados_pool)

        title_nombre = (c.get('Nombre') or f'Cliente {i}')[:50]

        # Fecha de envío (entre hoy-60 y hoy)
        envio = date.today() - timedelta(days=random.randint(5, 60))
        # Fecha aceptación si aplica
        aceptacion = None
        if estado in ('Aceptada', 'Pagada'):
            aceptacion = envio + timedelta(days=random.randint(2, 14))

        props = {
            'Cotización': {'title': [{'type': 'text', 'text': {'content': f'Cotización {title_nombre} - {paquete_nombre}'}}]},
            'Estado': {'status': {'name': estado}},
            'Paquete': {'select': {'name': paquete_nombre}},
            'Cliente': {'relation': [{'id': c['id']}]},
            'Monto total (Q)': {'number': float(precio)},
            'Anticipo (Q)': {'number': float(anticipo)},
            'Pagado (Q)': {'number': float(pagado)},
            'Cantidad de cuotas': {'select': {'name': cuotas}},
            'Link Smart File': {'url': smart_url},
            'Link Recurrente anticipo': {'url': recurrente_url},
            'Fecha de envío': {'date': {'start': envio.isoformat()}},
        }
        if aceptacion:
            props['Fecha aceptación'] = {'date': {'start': aceptacion.isoformat()}}

        if DRY_RUN:
            cotiz_added.append(title_nombre)
            continue

        try:
            r = client.pages.create(
                parent={'data_source_id': ds_id},
                properties=props,
            )
            cotiz_added.append(f'{title_nombre} ({paquete_nombre})')
        except Exception as e:
            print(f'  FAIL {title_nombre}: {e}')

    print(f'COTIZACIONES DEMO: {len(cotiz_added)} agregadas')


# ============================================================
# MAIN
# ============================================================

if __name__ == '__main__':
    print('='*60)
    print('LLENANDO CRM (modo:', 'DRY_RUN' if DRY_RUN else 'REAL', ')')
    print('='*60)

    llenar_jobs()
    print()
    llenar_leads()
    print()
    llenar_clientes()
    print()
    agregar_leads_demo()
    print()
    agregar_cotizaciones_demo()
    print()
    print('='*60)
    print('COMPLETADO')
    print('='*60)