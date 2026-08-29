"""Convierte el JSON maestro de reconciliacion de Studio Ninja
(FlowCRM_StudioNinja_Master_<fecha>.json) al payload exacto que espera
/api/admin/import-studio-ninja.

Kevin, 29-ago-2026: "ya tengo el archivo json para subir mis clientes a
flowing". Ese archivo es una reconciliacion completa (191 contactos, 19
jobs, facturas con historial de pago, estado de workflow por boda), pero su
formato no es el que el importador ya sabe leer. Este script traduce, NO
inventa: cada valor sale del archivo, y lo que el archivo no afirma se deja
en el default mas conservador.

USO:
    python tools/convertir_studio_ninja_master.py ENTRADA.json SALIDA.json

La SALIDA no debe guardarse nunca dentro de este repo: tiene nombres,
correos, telefonos y montos reales de clientes, y el repo es publico.

DECISIONES (confirmadas con Kevin antes de generar nada):
  - Solo entran las bodas reales con su gente. Los ~165 contactos sueltos
    de la libreta de Studio Ninja (sin lead ni job) quedan fuera.
  - Todo va a la cuenta Astral Weddings (tenant_brand del archivo). El
    importador usa la cuenta de la sesion, asi que hay que entrar como
    Astral antes de correrlo.
  - Los contactos marcados como posible duplicado NO se fusionan (el
    propio archivo advierte que no se deben fusionar por nombre igual).

REGLAS QUE ESTE SCRIPT RESPETA (vienen de crm_import_rules del archivo):
  - Se usa canonical.job_name / canonical.people, NUNCA el nombre original
    del job de Studio Ninja como identidad de una persona. Un job llamado
    "BODA CON <nombre>" no implica que esa persona sea la novia: en el
    archivo real hay casos donde ese nombre es el de la wedding planner y
    la pareja es otra, y el archivo lo dice explicitamente.
  - El orden de clientes es: partner_1, partner_2, y recien despues
    planner/booking. El importador mapea idx0=principal, idx1=pareja,
    idx2=planner, asi que el orden ES la asignacion de roles.
  - No se parten ni se inventan apellidos. Si el archivo tiene el contacto
    con first_name/last_name ya separados (columna original de Studio
    Ninja), se usan esos. Si no, se parte por el primer espacio, y un
    nombre de una sola palabra queda con apellido vacio en vez de
    inventarle uno.
  - El job demo queda excluido (is_demo_test / DEMO_EXCLUDED).
  - Un contrato "presente en el archivo" NO se marca como firmado: el
    archivo solo afirma que el PDF existe, nunca que este firmado.
  - gallery_delivered / review_left van en False salvo que el archivo lo
    afirme. Marcarlos de mas dejaria el historial mintiendo; marcarlos de
    menos es seguro (el importador marca SKIPPED, que igual no dispara
    ningun correo).
"""
import json
import re
import sys
import unicodedata


def _slug(texto):
    """Slug estable y sin acentos, para los ids deterministicos del
    importador (boda-sn-<slug>, client-sn-<slug>, ...). Se deriva del
    job_id del archivo, que ya es unico y estable entre corridas -- asi
    reimportar saltea en vez de duplicar."""
    txt = unicodedata.normalize('NFKD', texto or '')
    txt = ''.join(c for c in txt if not unicodedata.combining(c))
    txt = re.sub(r'[^a-zA-Z0-9]+', '-', txt).strip('-').lower()
    return re.sub(r'-{2,}', '-', txt)


def _normalizar(texto):
    txt = unicodedata.normalize('NFKD', texto or '')
    txt = ''.join(c for c in txt if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', txt).strip().lower()


def _partir_nombre(nombre_completo, contacto):
    """first_name/last_name a partir del nombre CANONICO.

    El nombre canonico manda siempre. El desglose first/last del contacto
    de Studio Ninja solo se usa cuando describe a la MISMA persona (mismo
    nombre normalizado) -- si difiere, el canonico es justamente la
    correccion de ese dato sucio y usarlo seria reintroducir el error.

    Este matiz no es teorico: en el archivo real hay al menos una boda
    donde el contacto de Studio Ninja del novio trae el apellido de la
    wedding planner pegado al suyo mas un sufijo '(WP)' que el propio
    archivo marca como no confiable, mientras que el nombre canonico esta
    correcto. Preferir el desglose del contacto metia esa contaminacion de
    vuelta al CRM -- exactamente lo que crm_import_rules.identity.never_do
    dice que no hay que hacer.

    Cuando hay que partir, se usa la convencion hispana (dos apellidos):
    4+ palabras -> 2 de nombre y el resto apellidos; 3 -> 1 y 2; 2 -> 1 y
    1; 1 sola palabra -> nombre sin apellido, nunca uno inventado."""
    nombre_completo = (nombre_completo or '').strip()
    if contacto and _normalizar(contacto.get('display_name')) == _normalizar(nombre_completo):
        first = (contacto.get('first_name') or '').strip()
        last = (contacto.get('last_name') or '').strip()
        if first or last:
            return first, last

    palabras = nombre_completo.split()
    if not palabras:
        return '', ''
    if len(palabras) == 1:
        return palabras[0], ''
    if len(palabras) == 2:
        return palabras[0], palabras[1]
    if len(palabras) == 3:
        return palabras[0], ' '.join(palabras[1:])
    return ' '.join(palabras[:2]), ' '.join(palabras[2:])


# Orden en que el importador asigna roles: principal, pareja, planner.
_PRIORIDAD_ROL = {
    'partner_1': 0, 'client': 0, 'client_named_in_contract': 0,
    'partner_2': 1,
    'wedding_planner': 2, 'booking_contact': 2,
    'wedding_planner_or_booking_contact': 2,
}


def _limpiar_linea(linea):
    return re.sub(r'\s{2,}', ' ', (linea or '').replace('°', '').strip()).strip(' -·')


def _nombre_paquete(invoice):
    """Nombre corto del paquete desde la primera linea de la factura.
    'GOLD MIX ° 2 FOTOGRAFOS Q17,000.00 1 Q17,000.00' -> 'GOLD MIX'."""
    lineas = invoice.get('service_lines_extracted') or []
    if not lineas:
        return 'Paquete Astral Weddings'
    primera = _limpiar_linea(lineas[0])
    # Corta en el primer precio o en el separador de items.
    corte = re.split(r'\s+Q[\d,]+\.?\d*|\s+°', primera)[0].strip()
    return corte or primera[:60] or 'Paquete Astral Weddings'


def convertir(master):
    jobs_out = []
    contactos = {c['contact_id']: c for c in master.get('contacts', [])}
    excluidos = []

    for job in master.get('jobs', []):
        canon = job.get('canonical') or {}
        workflow = job.get('workflow') or {}

        if canon.get('is_demo_test') or workflow.get('state') == 'DEMO_EXCLUDED':
            excluidos.append(canon.get('job_name') or job.get('job_id'))
            continue

        fecha_evento = ((job.get('event') or {}).get('main_shoot') or {}).get('date')
        if not fecha_evento:
            excluidos.append(f"{canon.get('job_name')} (sin fecha de evento)")
            continue

        # --- gente, en el orden que define los roles del importador ---
        gente = list(canon.get('people') or []) + list(canon.get('planner_or_booking_contacts') or [])
        gente = [p for p in gente if p.get('role') != 'test_contact']
        gente.sort(key=lambda p: _PRIORIDAD_ROL.get(p.get('role'), 3))

        clients = []
        for persona in gente:
            contacto = contactos.get(persona.get('contact_id'))
            first, last = _partir_nombre(persona.get('name'), contacto)
            clients.append({
                'first_name': first,
                'last_name': last,
                'email': persona.get('email') or (contacto or {}).get('email') or '',
                'phone': persona.get('phone') or (contacto or {}).get('phone') or '',
            })

        # --- facturas -> "quotes" con sus cuotas ---
        quotes = []
        for invoice in (job.get('finance') or {}).get('invoices') or []:
            cuotas = []
            for pago in invoice.get('payments') or []:
                pagado = pago.get('status_as_of_2026_08_29') == 'paid'
                cuota = {
                    'amount': float(pago.get('amount_gtq') or 0),
                    'due_date': pago.get('due_date'),
                    'status': 'Pagado' if pagado else 'Pendiente',
                }
                if pagado and pago.get('paid_date'):
                    cuota['paid_date'] = pago['paid_date']
                cuotas.append(cuota)
            if not cuotas:
                cuotas = [{
                    'amount': float(invoice.get('total_gtq') or 0),
                    'due_date': invoice.get('issued_date') or fecha_evento,
                    'status': 'Pendiente',
                }]
            # Las lineas del paquete son transcripcion literal del PDF de la
            # factura -- se dejan como estan a proposito (es lo que el
            # cliente firmo), pero se descartan los restos que no describen
            # nada: fragmentos sueltos de una linea partida, sufijos tipo
            # '(WP)' que quedaron colgando, y lineas que son solo un precio.
            incluye = []
            for linea in (invoice.get('service_lines_extracted') or [])[1:]:
                limpia = _limpiar_linea(linea)
                if len(limpia) < 4:
                    continue
                if re.fullmatch(r'\(?[A-Z]{1,4}\)?', limpia):
                    continue
                if re.fullmatch(r'Q?[\d.,]+', limpia):
                    continue
                incluye.append(limpia)
            quotes.append({
                'package_name': _nombre_paquete(invoice),
                'total': float(invoice.get('total_gtq') or 0),
                'incluye': incluye,
                'cuotas': cuotas,
            })

        # --- contrato: presente != firmado ---
        contract = None
        if (job.get('planning') or {}).get('contract_document_present'):
            contract = {'signed': False, 'photographer_signed': False}

        # --- estado de workflow ---
        planning = job.get('planning') or {}
        entry = {
            'slug': _slug(job.get('job_id') or canon.get('job_name')),
            'job_name': canon.get('job_name'),
            'boda_date': fecha_evento,
            'created': (job.get('studio_ninja') or {}).get('job_created') or fecha_evento,
            'location': canon.get('primary_location') or '',
            'lead_source': (job.get('studio_ninja') or {}).get('lead_source') or '',
            'clients': clients,
            'quotes': quotes,
            'workflow_status': {
                'questionnaire_completed': planning.get('questionnaire_status') == 'complete',
                'gallery_delivered': False,
                'review_left': False,
                'job_complete': workflow.get('state') == 'CLOSED_COMPLETED',
            },
        }
        if contract:
            entry['contract'] = contract
        jobs_out.append(entry)

    return {'jobs': jobs_out}, excluidos


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    with open(sys.argv[1], encoding='utf-8') as f:
        master = json.load(f)

    payload, excluidos = convertir(master)

    with open(sys.argv[2], 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    # --- resumen para revisar a ojo antes de importar ---
    total_facturado = sum(q['total'] for j in payload['jobs'] for q in j['quotes'])
    total_pagado = sum(
        c['amount'] for j in payload['jobs'] for q in j['quotes']
        for c in q['cuotas'] if c['status'] == 'Pagado'
    )
    personas = sum(len(j['clients']) for j in payload['jobs'])
    print(f"jobs convertidos : {len(payload['jobs'])}")
    print(f"personas         : {personas}")
    print(f"facturado (GTQ)  : {total_facturado:,.2f}")
    print(f"pagado (GTQ)     : {total_pagado:,.2f}")
    print(f"pendiente (GTQ)  : {total_facturado - total_pagado:,.2f}")
    print(f"excluidos        : {excluidos or 'ninguno'}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
