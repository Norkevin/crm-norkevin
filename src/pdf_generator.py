"""
pdf_generator.py - Genera PDFs de cotizaciones, contratos y facturas.

Usa reportlab (puro Python, sin dependencias externas).
Look & feel: Studio Ninja con banda verde, tipografia fina, layout A4.
"""
import io
import os
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# Colores Astral -- sobrios y planos (sin degradado), acorde a una marca de
# fotografia de bodas: navy oscuro + acento dorado, no azul corporativo.
BRAND = HexColor('#232B3A')
INK = HexColor('#0A0E1A')
INK_SOFT = HexColor('#4B5563')
MUTE = HexColor('#6B7280')
LINE = HexColor('#E4E7EC')
LINE_SOFT = HexColor('#F1F3F6')
GOLD = HexColor('#B08D57')
EMERALD = HexColor('#059669')
ROSE = HexColor('#DC2626')
AMBER = HexColor('#D97706')


def _draw_hero(c, width, height, doc_type, doc_id, total=None):
    """Dibuja el hero (plano, sin degradado) con logo, tipo de doc, ID y total.
    Navy solido + una linea dorada delgada al pie -- sobrio en vez del banner
    con degradado y circulo decorativo que se veia generico."""
    hero_h = 62*mm

    # Fondo plano
    c.setFillColor(BRAND)
    c.rect(0, height - hero_h, width, hero_h, fill=True, stroke=False)

    # Linea de acento dorada al pie del hero
    c.setFillColor(GOLD)
    c.rect(0, height - hero_h, width, 0.9*mm, fill=True, stroke=False)

    # Marca (glyph con borde dorado + nombre)
    c.setStrokeColor(GOLD)
    c.setLineWidth(0.6)
    c.roundRect(15*mm, height - 26*mm, 11*mm, 11*mm, 2.5*mm, fill=False, stroke=True)
    c.setFillColor(GOLD)
    c.setFont("Helvetica", 12)
    c.drawCentredString(20.5*mm, height - 22.3*mm, "A")

    c.setFillColor(HexColor('#FFFFFF'))
    c.setFont("Helvetica-Bold", 12)
    c.drawString(30*mm, height - 18.5*mm, "ASTRAL WEDDINGS")
    c.setFillColor(HexColor('#FFFFFF'))
    c.setFillAlpha(0.75)
    c.setFont("Helvetica", 6.5)
    c.drawString(30*mm, height - 23.5*mm, "PHOTOGRAPHY & FILMS")
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


def _draw_footer(c, width, doc_id):
    """Dibuja el footer."""
    c.setFillColor(LINE)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.3)
    c.line(15*mm, 30*mm, width - 15*mm, 30*mm)

    c.setFillColor(MUTE)
    c.setFont("Helvetica", 7)
    c.drawCentredString(width/2, 22*mm, "Astral Weddings  *  info@astralweddings.com  *  +502 2222 3333")
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


def generate_quote_pdf(quote, lead):
    """Genera PDF de cotizacion. Retorna bytes."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Hero
    _draw_hero(c, width, height, 'cotizacion', quote.get('id', 'Q-XXXX'),
               total=quote.get('precio_total', 0))

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
    _draw_footer(c, width, quote.get('id', ''))

    c.save()
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


def contract_terms(job):
    """Terminos del contrato, parametrizados por job. Usado por el PDF y la vista web."""
    return [
        ('1. Compromiso del Fotografo',
         'Astral Weddings se compromete a capturar, editar y entregar el material fotografico contratado en las fechas y plazos acordados. La calidad del trabajo se ajustara a los estandares profesionales de la industria.'),
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
         'Astral Weddings retendra los derechos de autor sobre todas las imagenes. El cliente recibira una licencia personal no comercial para uso privado.'),
    ]


def generate_contract_pdf(contract, job, client):
    """Genera PDF de contrato."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Hero
    _draw_hero(c, width, height, 'contrato', contract.get('id', 'C-XXXX'))

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
    _draw_client_block(c, y, 'DE', 'Astral Weddings',
                       ['+502 2222 3333', 'info@astralweddings.com', 'Guatemala'], width)

    # Bloque titulo contrato
    y -= 25*mm
    c.setFillColor(LINE_SOFT)
    c.rect(15*mm, y - 8*mm, width - 30*mm, 8*mm, fill=True, stroke=False)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20*mm, y - 5*mm, "CONTRATO DE BODAS ASTRAL WEDDINGS")
    y -= 15*mm

    # Terminos
    terminos = contract_terms(job)

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
    c.drawString(15*mm, y - 9*mm, "Astral Weddings")

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
    _draw_footer(c, width, contract.get('id', ''))

    c.save()
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


def generate_invoice_pdf(invoice, job, client, schedule=None, package_name=None, package_incluye=None):
    """Genera PDF de factura. Si se pasa `schedule` (lista de cuotas del mismo
    job/cotizacion), se genera UNA sola factura con el desglose de todos los
    pagos internamente, en vez de un documento separado por cuota.

    `package_name`/`package_incluye` (opcionales): paquete contratado y su
    descripcion, para que el cliente vea que incluye ademas del monto."""
    import textwrap

    rows = schedule if schedule else [invoice]
    total_amount = sum(float(r.get('amount') or 0) for r in rows)
    paid_amount = sum(float(r.get('amount') or 0) for r in rows if r.get('status') == 'Pagado')

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Hero (usa el ID de la factura representativa, pero el TOTAL es la suma de todas las cuotas)
    _draw_hero(c, width, height, 'factura', invoice.get('invoice_id', 'F-XXXX'), total=total_amount)

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
        c.setFillColor(EMERALD)
        overall_status = "FACTURA PAGADA EN SU TOTALIDAD"
    elif paid_amount > 0:
        c.setFillColor(AMBER)
        overall_status = "PAGO PARCIAL RECIBIDO"
    else:
        c.setFillColor(AMBER)
        overall_status = "PENDIENTE DE PAGO"
    c.setFont("Helvetica-Bold", 10)
    c.drawString(15*mm, y, overall_status)

    # Footer
    _draw_footer(c, width, invoice.get('invoice_id', ''))

    c.save()
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
