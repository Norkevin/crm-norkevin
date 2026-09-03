"""
pdf_generator.py - Genera PDFs de cotizaciones, contratos y facturas.

Usa reportlab (puro Python, sin dependencias externas).
Look & feel: Studio Ninja con banda verde, tipografia fina, layout A4.

Identidad de marca (estabilizacion agosto 2026): este modulo generaba TODO
el contenido (header del PDF, "De", firma del fotografo, footer, e incluso
el texto legal de contract_terms()) con "Astral Weddings" /
"info@astralweddings.com" escritos a mano, sin importar de que tenant era
el contrato/cotizacion/factura -- exactamente el mismo tipo de bug que
causo el incidente de correo del 16 de agosto de 2026, pero en documentos
legales/facturacion en vez de en el cuerpo de un email. Un contrato de un
cliente de Norkevin Photography decia "Astral Weddings" en la firma del
fotografo y en cada pagina del footer.

Todas las funciones de este modulo ahora reciben un `brand` (dict) opcional
-- resuelto por el llamador via `resolve_pdf_brand(tenant_id)` (abajo),
que a su vez usa la capa canonica `src.tenant_brand_map`, nunca un string
fijo. Si no se pasa `brand`, se usa un placeholder neutro explicito
("Estudio no identificado") en vez de asumir silenciosamente una marca --
eso habria sido el mismo bug con otro nombre."""
import io
import os
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# Placeholder neutro -- NUNCA una marca real. Si esto aparece en un PDF real,
# significa que el llamador no resolvio brand=resolve_pdf_brand(tenant_id)
# antes de invocar esta funcion; es preferible que se note (texto raro) a
# que se filtre silenciosamente el nombre de la otra empresa.
_UNRESOLVED_BRAND = {
    'display_name': 'Estudio no identificado',
    'tagline': '',
    'phone': '',
    'email': '',
    'initial': '?',
}


def resolve_pdf_brand(tenant_id):
    """Resuelve la identidad de marca para un tenant_id via la capa
    canonica (src.tenant_brand_map), completando telefono/email de negocio
    desde la config guardada por-tenant (settings_<tenant_id>.json) cuando
    existe. Nunca decide por el nombre del tenant_id ni por un default fijo.
    Si el tenant_id no resuelve (None, o no esta en el mapa canonico),
    devuelve el placeholder neutro _UNRESOLVED_BRAND -- no "Astral Weddings"
    ni ninguna otra marca real."""
    if not tenant_id:
        return dict(_UNRESOLVED_BRAND)
    try:
        from src.tenant_brand_map import resolve_brand, UnresolvedBrandError
    except ImportError:
        return dict(_UNRESOLVED_BRAND)
    try:
        identity = resolve_brand(tenant_id)
    except UnresolvedBrandError:
        return dict(_UNRESOLVED_BRAND)

    phone = ''
    email = identity.sender_email or ''
    try:
        from src.storage import store
        company = (store.get_tenant_dict('settings', tenant_id=tenant_id) or {}).get('company') or {}
        phone = company.get('phone') or ''
        email = company.get('email') or email
    except Exception:
        pass  # sin settings guardados todavia -- se usa lo que ya resolvio tenant_brand_map

    return {
        'display_name': identity.display_name,
        'tagline': 'PHOTOGRAPHY',
        'phone': phone,
        'email': email,
        'initial': (identity.display_name or '?')[:1].upper(),
    }


# Colores del sistema de documentos de Flow CRM (29-ago-2026).
#
# Kevin: "no quiero factura web = diseño nuevo y PDF = plantilla antigua
# totalmente diferente. Deben representar el mismo documento en dos medios".
# Por eso estos valores son EXACTAMENTE los mismos tokens que emite
# templates/_document_tokens.html (que a su vez son las variables --sn-* del
# CRM en templates/base.html). Antes esta paleta era navy + dorado, sin
# relacion con nada del producto: un PDF que no se parecia ni al CRM ni a la
# cotizacion.
#
# Si algun dia una marca define colores propios, se resuelven igual que
# display_name (via el brand que ya recibe cada generate_*_pdf) sin tocar
# estas constantes, que son el default compartido.
BRAND = HexColor('#111827')      # text-primary: cabecera del documento
INK = HexColor('#111827')        # text-primary
INK_SOFT = HexColor('#667085')   # text-secondary
MUTE = HexColor('#98A2B3')       # muted
LINE = HexColor('#E7EAF0')       # border
LINE_SOFT = HexColor('#F4F5F9')  # surface-2
GOLD = HexColor('#7357F6')       # primary (el acento del producto; el nombre
                                 # de la constante se conserva para no tocar
                                 # las ~40 referencias que ya la usan)
EMERALD = HexColor('#2FB66D')    # success -- rellenos y marcadores
ROSE = HexColor('#EF5B5B')       # danger  -- rellenos y marcadores
AMBER = HexColor('#F59E0B')      # warning -- rellenos y marcadores
# Variantes para TEXTO. Los tres de arriba son colores de superficie: sobre
# papel blanco dan 2.6:1, 3.3:1 y 2.2:1, muy por debajo del minimo legible,
# y un PDF se imprime. Mismo tono, oscurecido hasta pasar 4.5:1. Es la misma
# distincion que hace base.html entre --sn-success y el color de texto de
# .badge-success.
EMERALD_TEXT = HexColor('#158048')
ROSE_TEXT = HexColor('#C93636')
AMBER_TEXT = HexColor('#97620C')


def _draw_hero(c, width, height, doc_type, doc_id, total=None, brand=None):
    """Dibuja el hero (plano, sin degradado) con logo, tipo de doc, ID y total.
    Navy solido + una linea dorada delgada al pie -- sobrio en vez del banner
    con degradado y circulo decorativo que se veia generico."""
    brand = brand or _UNRESOLVED_BRAND
    hero_h = 62*mm

    # Fondo plano
    c.setFillColor(BRAND)
    c.rect(0, height - hero_h, width, hero_h, fill=True, stroke=False)

    # Linea de acento dorada al pie del hero
    c.setFillColor(GOLD)
    c.rect(0, height - hero_h, width, 0.9*mm, fill=True, stroke=False)

    # Marca (glyph enmarcado + nombre). El borde lleva el color de acento
    # del producto, pero la INICIAL va en blanco: sobre la cabecera oscura,
    # el acento contra ese fondo queda en ~3.7:1, por debajo del minimo
    # legible. El acento se ve igual en el marco, donde el contraste no
    # decide si se puede leer o no.
    c.setStrokeColor(GOLD)
    c.setLineWidth(0.6)
    c.roundRect(15*mm, height - 26*mm, 11*mm, 11*mm, 2.5*mm, fill=False, stroke=True)
    c.setFillColor(HexColor('#FFFFFF'))
    c.setFont("Helvetica", 12)
    c.drawCentredString(20.5*mm, height - 22.3*mm, brand['initial'])

    c.setFillColor(HexColor('#FFFFFF'))
    c.setFont("Helvetica-Bold", 12)
    c.drawString(30*mm, height - 18.5*mm, brand['display_name'].upper())
    if brand.get('tagline'):
        c.setFillColor(HexColor('#FFFFFF'))
        c.setFillAlpha(0.75)
        c.setFont("Helvetica", 6.5)
        c.drawString(30*mm, height - 23.5*mm, brand['tagline'])
        c.setFillAlpha(1)

    # Cuadro ID/tipo (esquina superior derecha)
    c.setFillColor(HexColor('#FFFFFF'))
    c.setFillAlpha(0.8)
    c.setFont("Helvetica", 7)
    label = {'cotizacion': 'COTIZACION', 'contrato': 'CONTRATO', 'factura': 'FACTURA'}.get(doc_type, 'DOC')
    c.drawRightString(width - 15*mm, height - 18*mm, label)
    c.setFillAlpha(1)

    c.setFillColor(HexColor('#FFFFFF'))
    c.setFont("Helvetica", 10)
    c.drawRightString(width - 15*mm, height - 24*mm, doc_id or '')

    # Total (grande, alineado a la izquierda debajo de la marca)
    if total is not None:
        c.setFillColor(HexColor('#FFFFFF'))
        c.setFillAlpha(0.75)
        c.setFont("Helvetica", 7)
        c.drawString(15*mm, height - 42*mm, "TOTAL")
        c.setFillAlpha(1)
        c.setFillColor(HexColor('#FFFFFF'))
        c.setFont("Helvetica-Bold", 24)
        c.drawString(15*mm, height - 52*mm, f"Q{total:,.2f}")


def _draw_client_block(c, y, label, name, info_lines, width):
    """Dibuja un bloque cliente (Para, De, Job)."""
    c.setFillColor(MUTE)
    c.setFont("Helvetica", 7)
    c.drawString(15*mm, y, label)

    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(15*mm, y - 5*mm, name)

    c.setFillColor(INK_SOFT)
    c.setFont("Helvetica", 9)
    line_y = y - 10*mm
    for line in info_lines:
        c.drawString(15*mm, line_y, line)
        line_y -= 4*mm


def _draw_footer(c, width, doc_id, brand=None):
    """Dibuja el footer."""
    brand = brand or _UNRESOLVED_BRAND
    c.setFillColor(LINE)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.3)
    c.line(15*mm, 30*mm, width - 15*mm, 30*mm)

    c.setFillColor(MUTE)
    c.setFont("Helvetica", 7)
    footer_parts = [brand['display_name']]
    if brand.get('email'):
        footer_parts.append(brand['email'])
    if brand.get('phone'):
        footer_parts.append(brand['phone'])
    c.drawCentredString(width/2, 22*mm, '  *  '.join(footer_parts))
    c.drawCentredString(width/2, 17*mm, f"Documento generado el {datetime.now().strftime('%d %b %Y')}")


def _draw_items_table(c, y, items, width, col_widths=None):
    """Dibuja tabla de items con cabecera y filas."""
    if col_widths is None:
        col_widths = [width * 0.50, width * 0.10, width * 0.20, width * 0.20]
        # Restar margenes
        available = width - 30*mm
        col_widths = [available * 0.50, available * 0.10, available * 0.20, available * 0.20]

    x_starts = [15*mm]
    for w in col_widths[:-1]:
        x_starts.append(x_starts[-1] + w)

    # Cabecera
    c.setFillColor(MUTE)
    c.setFont("Helvetica", 7)
    headers = ['PRODUCTO', 'CANT.', 'PRECIO UNIT.', 'IMPORTE']
    for i, h in enumerate(headers):
        if i == 0:
            c.drawString(x_starts[i], y, h)
        elif i == 1:
            c.drawCentredString(x_starts[i] + col_widths[i]/2, y, h)
        else:
            c.drawRightString(x_starts[i] + col_widths[i] - 5*mm, y, h)

    # Linea bajo cabecera
    c.setStrokeColor(LINE)
    c.setLineWidth(0.5)
    c.line(15*mm, y - 2*mm, width - 15*mm, y - 2*mm)

    # Filas
    row_y = y - 8*mm
    for item in items:
        # Nombre
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x_starts[0], row_y, item['name'][:60])

        # Descripcion
        if item.get('desc'):
            c.setFillColor(MUTE)
            c.setFont("Helvetica", 8)
            c.drawString(x_starts[0], row_y - 4*mm, item['desc'][:80])

        # Cantidad
        c.setFillColor(INK)
        c.setFont("Helvetica", 10)
        c.drawCentredString(x_starts[1] + col_widths[1]/2, row_y, str(item.get('qty', 1)))

        # Precio
        c.drawRightString(x_starts[2] + col_widths[2] - 5*mm, row_y, f"Q{item.get('price', 0):,.2f}")

        # Total
        c.setFont("Helvetica-Bold", 10)
        c.drawRightString(x_starts[3] + col_widths[3] - 5*mm, row_y, f"Q{item.get('total', 0):,.2f}")

        # Linea entre filas
        c.setStrokeColor(LINE_SOFT)
        c.setLineWidth(0.3)
        c.line(15*mm, row_y - 6*mm, width - 15*mm, row_y - 6*mm)

        row_y -= 12*mm

    return row_y


def _draw_totals(c, y, subtotal, total, width):
    """Dibuja los totales (subtotal + total)."""
    box_width = 80*mm
    box_x = width - 15*mm - box_width

    # Subtotal
    c.setFillColor(INK_SOFT)
    c.setFont("Helvetica", 9)
    c.drawString(box_x, y, "Subtotal")
    c.drawRightString(width - 15*mm, y, f"Q{subtotal:,.2f}")

    # Linea divisoria
    c.setStrokeColor(INK)
    c.setLineWidth(1)
    y -= 4*mm
    c.line(box_x, y, width - 15*mm, y)
    y -= 6*mm

    # Total
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(box_x, y, "Total")
    c.drawRightString(width - 15*mm, y, f"Q{total:,.2f}")

    return y - 10*mm


def generate_quote_pdf(quote, lead, brand=None):
    """Genera PDF de cotizacion. Retorna bytes. `brand`: dict de
    resolve_pdf_brand(tenant_id) -- si no se pasa, usa el placeholder
    neutro (nunca asume una marca real)."""
    brand = brand or _UNRESOLVED_BRAND
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Hero
    _draw_hero(c, width, height, 'cotizacion', quote.get('id', 'Q-XXXX'),
               total=quote.get('precio_total', 0), brand=brand)

    # Cliente (Para)
    y = height - 78*mm
    _draw_client_block(c, y, 'PARA', lead.get('nombre', ''), [
        lead.get('telefono') or '',
        lead.get('email') or '',
        'Guatemala'
    ], width)

    # Job info
    y -= 25*mm
    c.setFillColor(MUTE)
    c.setFont("Helvetica", 7)
    c.drawString(15*mm, y, "JOB")
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(15*mm, y - 5*mm, lead.get('nombre', ''))

    c.setFillColor(MUTE)
    c.setFont("Helvetica", 8)
    info_y = y - 10*mm
    for line in [
        f"Issue Date {quote.get('created', '-')}",
        f"Boda {lead.get('fecha_tentativa', '-')}",
        lead.get('locacion', 'Guatemala')
    ]:
        c.drawString(15*mm, info_y, line)
        info_y -= 4*mm

    # Paquete
    c.setFillColor(MUTE)
    c.setFont("Helvetica", 7)
    c.drawString(width/2, y, "PAQUETE")
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(width/2, y - 5*mm, quote.get('paquete_nombre', ''))
    if quote.get('plan_pago') and quote.get('plan_pago') > 1:
        c.setFillColor(MUTE)
        c.setFont("Helvetica", 8)
        c.drawString(width/2, y - 10*mm, f"{quote['plan_pago']} cuotas de Q{quote.get('cuota_monto', 0):,.2f}")

    # Tabla de items
    y = y - 25*mm
    items = [{
        'name': quote.get('paquete_nombre', 'Paquete'),
        'desc': ' * '.join(quote.get('incluye', [])),
        'qty': 1,
        'price': quote.get('precio_total', 0),
        'total': quote.get('precio_total', 0)
    }]
    y = _draw_items_table(c, y, items, width)

    # Totales
    y -= 5*mm
    y = _draw_totals(c, y, quote.get('precio_total', 0), quote.get('precio_total', 0), width)

    # Notas
    if quote.get('notas'):
        c.setFillColor(HexColor('#92400E'))
        c.setFont("Helvetica", 7)
        c.drawString(15*mm, y - 10*mm, "NOTAS")
        c.setFillColor(INK_SOFT)
        c.setFont("Helvetica", 9)
        c.drawString(15*mm, y - 15*mm, quote['notas'][:100])

    # Footer
    _draw_footer(c, width, quote.get('id', ''), brand=brand)

    c.save()
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


def contract_terms(job, brand=None):
    """Terminos del contrato, parametrizados por job. Usado por el PDF y la
    vista web. `brand` (dict de resolve_pdf_brand) determina el nombre del
    estudio en el texto legal -- antes decia "Astral Weddings" sin importar
    el tenant real del job."""
    brand = brand or _UNRESOLVED_BRAND
    name = brand['display_name']
    return [
        ('1. Compromiso del Fotografo',
         f'{name} se compromete a capturar, editar y entregar el material fotografico contratado en las fechas y plazos acordados. La calidad del trabajo se ajustara a los estandares profesionales de la industria.'),
        ('2. Tarifas y Deposito',
         f'La tarifa total es de Q{job.get("price_total", 0):,.2f}. '
         + (f'Pago en {job.get("plan_pago", 1)} cuotas de Q{job.get("cuota_monto", 0):,.2f}.' if job.get('plan_pago', 1) > 1 else 'Pago en una sola exhibicion.')
         + f' Deposito requerido: Q{job.get("price_total", 0) * 0.3:,.2f}.'),
        ('3. Responsabilidades del Cliente',
         '* Proporcionar informacion veraz y oportuna sobre el evento\n* Realizar los pagos en las fechas acordadas\n* Coordinar con el fotografo los horarios y ubicaciones del evento\n* Notificar cualquier cambio con al menos 30 dias de anticipacion'),
        ('4. Responsabilidades del Fotografo',
         '* Asistir puntualmente a todos los eventos acordados\n* Entregar las fotografias editadas en un plazo maximo de 30 dias\n* Mantener una copia de seguridad de todas las imagenes por 1 ano\n* Proveer una galeria online privada para revision del cliente'),
        ('5. Cancelacion y Reembolso',
         'En caso de cancelacion por parte del cliente con menos de 60 dias de anticipacion, el deposito no sera reembolsable. Cancelaciones con mas de 60 dias tendran un reembolso del 50% del deposito.'),
        ('6. Propiedad Intelectual',
         f'{name} retendra los derechos de autor sobre todas las imagenes. El cliente recibira una licencia personal no comercial para uso privado.'),
    ]


def generate_contract_pdf(contract, job, client, brand=None):
    """Genera PDF de contrato. `brand`: dict de resolve_pdf_brand(tenant_id)."""
    brand = brand or _UNRESOLVED_BRAND
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Hero
    _draw_hero(c, width, height, 'contrato', contract.get('id', 'C-XXXX'), brand=brand)

    # Cliente
    y = height - 78*mm
    _draw_client_block(c, y, 'CONTRATO PARA',
                       f"{client.get('first_name', '')} {client.get('last_name', '')}",
                       [client.get('phone', '') or '',
                        client.get('email', '') or '',
                        client.get('address', '') or 'Guatemala'],
                       width)

    # De
    y -= 30*mm
    _draw_client_block(c, y, 'DE', brand['display_name'],
                       [brand.get('phone', '') or '', brand.get('email', '') or '', 'Guatemala'], width)

    # Bloque titulo contrato
    y -= 25*mm
    c.setFillColor(LINE_SOFT)
    c.rect(15*mm, y - 8*mm, width - 30*mm, 8*mm, fill=True, stroke=False)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20*mm, y - 5*mm, f"CONTRATO DE BODAS {brand['display_name'].upper()}")
    y -= 15*mm

    # Terminos
    terminos = contract_terms(job, brand=brand)

    for title, body in terminos:
        if y < 60*mm:
            c.showPage()
            y = height - 30*mm
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(15*mm, y, title)
        y -= 5*mm

        c.setFillColor(INK_SOFT)
        c.setFont("Helvetica", 9)

        # Multi-line body
        for line in body.split('\n'):
            if line.strip():
                c.drawString(15*mm, y, line.strip()[:100])
                y -= 4*mm
        y -= 4*mm

    # Firmas
    if y < 60*mm:
        c.showPage()
        y = height - 50*mm

    y -= 15*mm
    sig_width = (width - 30*mm) / 2 - 5*mm
    # Linea firma 1
    c.setStrokeColor(INK)
    c.setLineWidth(0.5)
    c.line(15*mm, y, 15*mm + sig_width, y)
    c.setFillColor(MUTE)
    c.setFont("Helvetica", 7)
    c.drawString(15*mm, y - 4*mm, "FIRMA DEL FOTOGRAFO")
    c.setFillColor(INK)
    c.setFont("Helvetica", 9)
    c.drawString(15*mm, y - 9*mm, brand['display_name'])

    # Linea firma 2
    c.line(15*mm + sig_width + 10*mm, y, width - 15*mm, y)
    c.setFillColor(MUTE)
    c.setFont("Helvetica", 7)
    c.drawString(15*mm + sig_width + 10*mm, y - 4*mm, "FIRMA DEL CLIENTE")
    c.setFillColor(INK)
    c.setFont("Helvetica", 9)
    c.drawString(15*mm + sig_width + 10*mm, y - 9*mm,
                f"{client.get('first_name', '')} {client.get('last_name', '')}")

    # Footer
    _draw_footer(c, width, contract.get('id', ''), brand=brand)

    c.save()
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


def generate_invoice_pdf(invoice, job, client, schedule=None, package_name=None, package_incluye=None, brand=None):
    """Genera PDF de factura. Si se pasa `schedule` (lista de cuotas del mismo
    job/cotizacion), se genera UNA sola factura con el desglose de todos los
    pagos internamente, en vez de un documento separado por cuota.

    `package_name`/`package_incluye` (opcionales): paquete contratado y su
    descripcion, para que el cliente vea que incluye ademas del monto.
    `brand`: dict de resolve_pdf_brand(tenant_id)."""
    import textwrap

    brand = brand or _UNRESOLVED_BRAND
    rows = schedule if schedule else [invoice]
    total_amount = sum(float(r.get('amount') or 0) for r in rows)
    paid_amount = sum(float(r.get('amount') or 0) for r in rows if r.get('status') == 'Pagado')

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Hero (usa el ID de la factura representativa, pero el TOTAL es la suma de todas las cuotas)
    _draw_hero(c, width, height, 'factura', invoice.get('invoice_id', 'F-XXXX'), total=total_amount, brand=brand)

    # Cliente
    y = height - 78*mm
    _draw_client_block(c, y, 'PARA',
                       f"{client.get('first_name', '')} {client.get('last_name', '')}",
                       [client.get('phone', '') or '',
                        client.get('email', '') or '',
                        client.get('address', '') or 'Guatemala'],
                       width)

    # Concepto
    y -= 30*mm
    c.setFillColor(MUTE)
    c.setFont("Helvetica", 7)
    c.drawString(15*mm, y, "CONCEPTO")
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(15*mm, y - 5*mm, invoice.get('concepto', '') or (job.get('nombre', '') if job else ''))

    # Paquete contratado + que incluye (para que el cliente vea el detalle,
    # no solo el nombre y el monto)
    if package_name:
        y -= 12*mm
        c.setFillColor(MUTE)
        c.setFont("Helvetica", 7)
        c.drawString(15*mm, y, "PAQUETE")
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(15*mm, y - 5*mm, package_name)
        y -= 5*mm
        if package_incluye:
            incluye_text = '  *  '.join(package_incluye)
            c.setFillColor(INK_SOFT)
            c.setFont("Helvetica", 8)
            for line in textwrap.wrap(incluye_text, 95)[:3]:
                y -= 4.5*mm
                c.drawString(15*mm, y, line)

    # Tabla: desglose de cada cuota/pago
    y -= 15*mm
    items = []
    for r in rows:
        cuota_label = f"Pago {r['cuota']}" if r.get('cuota') else 'Pago'
        status_label = {'Pagado': 'Pagado', 'Late': 'Sin pagar', 'Pendiente': 'Pendiente'}.get(r.get('status', ''), r.get('status', ''))
        items.append({
            'name': cuota_label,
            'desc': f"Vence el {r.get('due_date', '-')}  *  {status_label}",
            'qty': 1,
            'price': r.get('amount', 0),
            'total': r.get('amount', 0)
        })
    y = _draw_items_table(c, y, items, width)

    # Totales (subtotal / pagado / saldo)
    y -= 3*mm
    box_width = 80*mm
    box_x = width - 15*mm - box_width
    c.setFillColor(INK_SOFT)
    c.setFont("Helvetica", 9)
    c.drawString(box_x, y, "Total")
    c.drawRightString(width - 15*mm, y, f"Q{total_amount:,.2f}")
    y -= 6*mm
    c.drawString(box_x, y, "Pagado")
    c.drawRightString(width - 15*mm, y, f"Q{paid_amount:,.2f}")
    y -= 4*mm
    c.setStrokeColor(INK)
    c.setLineWidth(1)
    c.line(box_x, y, width - 15*mm, y)
    y -= 6*mm
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(box_x, y, "Saldo pendiente")
    c.drawRightString(width - 15*mm, y, f"Q{max(total_amount - paid_amount, 0):,.2f}")
    y -= 12*mm

    # Estado general
    if paid_amount >= total_amount and total_amount > 0:
        c.setFillColor(EMERALD_TEXT)
        overall_status = "FACTURA PAGADA EN SU TOTALIDAD"
    elif paid_amount > 0:
        c.setFillColor(AMBER_TEXT)
        overall_status = "PAGO PARCIAL RECIBIDO"
    else:
        c.setFillColor(AMBER_TEXT)
        overall_status = "PENDIENTE DE PAGO"
    c.setFont("Helvetica-Bold", 10)
    c.drawString(15*mm, y, overall_status)

    # Footer
    _draw_footer(c, width, invoice.get('invoice_id', ''), brand=brand)

    c.save()
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
