"""
FACTURA PDF -- adaptacion print del documento web  (3-sep-2026)

Reemplaza el PDF anterior, que era de otra epoca del producto: cabecera
navy a sangre completa, tipografia y estructura propias, conceptos como
texto corrido. Al lado de la factura web parecia generada por otro
software, que es exactamente lo que Kevin reporto.

Este renderer dibuja el MISMO documento que templates/invoice_document.html,
adaptado a papel:

    marca / tipo + numero + estado
    metadata en cuatro columnas (cliente, evento, fecha, vence)
    tres cifras: total / pagado / pendiente
    proximo pago
    conceptos facturados + que incluye
    calendario de pagos
    subtotal / pagado / saldo
    footer discreto

No es un screenshot del HTML: es un PDF de verdad, con su paginacion y su
propia densidad. Pero los tokens, la escala tipografica, los estados y el
formato de fechas y montos salen de pdf_document_system, que es el espejo
en Python de los tokens del CSS. Esa es la unica forma de que dentro de seis
meses la web no se adelante otra vez.

NO calcula dinero. Todos los montos llegan ya resueltos desde app.py, igual
que en la plantilla web.
"""
import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from src.pdf_document_system import (
    COLOR, TIPO, MARGEN, RADIO,
    registrar_fuente_documental, texto, label, regla, card, badge,
    marcador, recortar, envolver, moneda,
)

_MARCA_NO_RESUELTA = {
    'display_name': 'Estudio no identificado',
    'tagline': '', 'phone': '', 'email': '', 'initial': '?',
}

# Alto minimo que debe quedar libre para empezar un bloque. Si no cabe, se
# salta de pagina ANTES de dibujarlo: es como se evita cortar una fila por
# la mitad sin depender de break-inside, que en un canvas no existe.
_ALTO_FILA_PAGO = 9 * mm
_ALTO_CONCEPTO = 12 * mm


class _Lienzo:
    """Envoltura del canvas que lleva la cuenta del cursor vertical y sabe
    cuando saltar de pagina, poniendo el encabezado compacto de la pagina 2
    en adelante y el pie en todas."""

    def __init__(self, c, ancho, alto, marca, titulo_doc, referencia):
        self.c = c
        self.ancho = ancho
        self.alto = alto
        self.marca = marca
        self.titulo_doc = titulo_doc
        self.referencia = referencia
        self.pagina = 1
        self.generado_el = ''
        self.x0 = MARGEN
        self.x1 = ancho - MARGEN
        self.util = self.x1 - self.x0
        self.y = alto - MARGEN
        self.pie_alto = 13 * mm

    # -- paginacion ------------------------------------------------
    def espacio_libre(self):
        return self.y - (MARGEN + self.pie_alto)

    def asegurar(self, alto_necesario):
        """Salta de pagina si el bloque que viene no entra completo."""
        if self.espacio_libre() < alto_necesario:
            self.nueva_pagina()
            return True
        return False

    def nueva_pagina(self):
        self.pie()
        self.c.showPage()
        self.pagina += 1
        self.y = self.alto - MARGEN
        self.encabezado_compacto()

    def encabezado_compacto(self):
        """Paginas 2+: una linea con marca, documento y numero de pagina."""
        c = self.c
        texto(c, self.x0, self.y - 4 * mm, self.marca['display_name'],
              tam='apoyo', peso='bold', color='text')
        texto(c, self.ancho / 2, self.y - 4 * mm,
              f"{self.titulo_doc} {self.referencia}", tam='apoyo',
              color='text_secondary', ancla='center')
        texto(c, self.x1, self.y - 4 * mm, f'Página {self.pagina}',
              tam='apoyo', color='text_secondary', ancla='right')
        self.y -= 7 * mm
        regla(c, self.x0, self.y, self.x1)
        self.y -= 8 * mm

    def pie(self):
        """Pie discreto, igual en todas las paginas. La linea de acento
        morada es el unico elemento de color fuerte del documento: sustituye
        a la cabecera navy que habia antes."""
        c = self.c
        y = MARGEN + 10 * mm
        regla(c, self.x0, y, self.x1)
        # Acento: un segmento corto, no una banda
        c.setFillColor(COLOR['primary'])
        c.rect(self.x0, y - 0.15 * mm, 16 * mm, 0.7 * mm, fill=True, stroke=False)

        izq = self.marca['display_name']
        if self.marca.get('email'):
            izq += f" · {self.marca['email']}"
        texto(c, self.x0, y - 6 * mm, recortar(izq, self.util * 0.7, tam='apoyo'),
              tam='apoyo', color='text_secondary')
        der = f'Página {self.pagina}'
        if self.generado_el:
            der = f'Documento generado el {self.generado_el} · {der}'
        texto(c, self.x1, y - 6 * mm, der, tam='apoyo', color='muted', ancla='right')

    def bajar(self, mm_):
        self.y -= mm_


# ============================================================
# BLOQUES
# ============================================================
def _encabezado(L, estado_label, estado_tono):
    """Marca a la izquierda, tipo + numero + estado a la derecha. Misma
    composicion que .doc-topbar en la web."""
    c, m = L.c, L.marca
    texto(c, L.x0, L.y - 5 * mm, recortar(m['display_name'], L.util * 0.55,
                                          tam='subtitulo', peso='bold'),
          tam='subtitulo', peso='bold', color='text')
    if m.get('tagline'):
        texto(c, L.x0, L.y - 10 * mm, recortar(m['tagline'], L.util * 0.5, tam='apoyo'),
              tam='apoyo', color='text_secondary')

    label(c, L.x1, L.y - 4 * mm, L.titulo_doc, ancla='right')
    texto(c, L.x1, L.y - 9.5 * mm, L.referencia, tam='cuerpo', peso='medium',
          color='text', ancla='right')
    if estado_label:
        f = registrar_fuente_documental()
        from reportlab.pdfbase import pdfmetrics
        ancho_badge = pdfmetrics.stringWidth(estado_label, f['medium'], TIPO['apoyo']) + 4.8 * mm
        badge(c, L.x1 - ancho_badge, L.y - 16 * mm, estado_label, estado_tono)

    L.bajar(22 * mm)
    regla(c, L.x0, L.y, L.x1)
    L.bajar(9 * mm)


def _metadata(L, campos):
    """Cuatro columnas: label pequeño arriba, valor debajo. Los campos
    vacios no dejan hueco -- se filtran antes de repartir el ancho."""
    campos = [(k, v) for k, v in campos if v]
    if not campos:
        return
    cols = min(4, len(campos))
    ancho_col = L.util / cols
    filas = [campos[i:i + cols] for i in range(0, len(campos), cols)]
    for fila in filas:
        alto_fila = 12 * mm
        for i, (k, v) in enumerate(fila):
            x = L.x0 + i * ancho_col
            label(L.c, x, L.y, k)
            lineas = envolver(str(v), ancho_col - 5 * mm, tam='cuerpo',
                              peso='medium', max_lineas=2)
            for j, ln in enumerate(lineas):
                texto(L.c, x, L.y - 5 * mm - j * 4.6 * mm, ln,
                      tam='cuerpo', peso='medium', color='text')
            alto_fila = max(alto_fila, 8 * mm + len(lineas) * 4.6 * mm)
        L.bajar(alto_fila)
    L.bajar(3 * mm)


def _tres_cifras(L, simbolo, total, pagado, pendiente):
    """Total / Pagado / Pendiente. Mismos colores semanticos y misma
    jerarquia que las summary cards de la web. El saldo solo va en rojo si
    de verdad hay algo pendiente; saldado se lee en verde."""
    c = L.c
    alto = 20 * mm
    L.asegurar(alto + 6 * mm)
    ancho_col = (L.util - 8 * mm) / 3
    saldado = (pendiente or 0) <= 0
    datos = [
        ('Total', total, 'primary'),
        ('Pagado', pagado, 'success_text'),
        ('Saldo' if saldado else 'Pendiente', pendiente,
         'success_text' if saldado else 'danger_text'),
    ]
    for i, (etq, val, color) in enumerate(datos):
        x = L.x0 + i * (ancho_col + 4 * mm)
        card(c, x, L.y - alto, ancho_col, alto)
        label(c, x + 5 * mm, L.y - 7 * mm, etq)
        texto(c, x + 5 * mm, L.y - 15 * mm, moneda(val, simbolo),
              tam='cifra', peso='bold', color=color)
    L.bajar(alto + 5 * mm)


def _proximo_pago(L, simbolo, cuando, monto):
    c = L.c
    alto = 15 * mm
    L.asegurar(alto + 6 * mm)
    card(c, L.x0, L.y - alto, L.util, alto, fondo='surface_2', borde='border')
    label(c, L.x0 + 5 * mm, L.y - 5.5 * mm, 'Próximo pago')
    texto(c, L.x0 + 5 * mm, L.y - 11.5 * mm, cuando, tam='cuerpo',
          peso='medium', color='text')
    texto(c, L.x1 - 5 * mm, L.y - 10.5 * mm, moneda(monto, simbolo),
          tam='cifra', peso='bold', color='text', ancla='right')
    L.bajar(alto + 5 * mm)


def _seccion(L, titulo, meta=''):
    L.asegurar(14 * mm)
    texto(L.c, L.x0, L.y, titulo, tam='seccion', peso='bold', color='text')
    if meta:
        texto(L.c, L.x1, L.y, meta, tam='apoyo', color='text_secondary', ancla='right')
    L.bajar(4.5 * mm)
    regla(L.c, L.x0, L.y, L.x1)
    L.bajar(5.5 * mm)


def _conceptos(L, simbolo, concepto, monto, incluye):
    """Concepto principal con su monto a la derecha, y debajo el desglose
    en dos columnas. Dos, no cuatro: mas angosto que eso una inclusion se
    parte y deja palabras sueltas que parecen conceptos aparte."""
    c = L.c
    L.asegurar(_ALTO_CONCEPTO)
    ancho_monto = 30 * mm
    lineas = envolver(concepto, L.util - ancho_monto - 4 * mm,
                      tam='cuerpo', peso='bold', max_lineas=2)
    for j, ln in enumerate(lineas):
        texto(c, L.x0, L.y - j * 5 * mm, ln, tam='cuerpo', peso='bold', color='text')
    texto(c, L.x1, L.y, moneda(monto, simbolo), tam='cuerpo', peso='bold',
          color='text', ancla='right')
    L.bajar(len(lineas) * 5 * mm + 3 * mm)

    if not incluye:
        L.bajar(3 * mm)
        return
    label(c, L.x0, L.y, 'Incluye')
    L.bajar(6 * mm)

    ancho_col = (L.util - 10 * mm) / 2
    items = [str(x) for x in incluye]
    # Reparto por columnas balanceando alturas: se mide cuantas lineas
    # ocupa cada item para que las dos columnas terminen parejas.
    envueltos = [envolver(t, ancho_col - 5 * mm, tam='apoyo', max_lineas=3) for t in items]
    total_lineas = sum(len(e) for e in envueltos)
    objetivo = (total_lineas + 1) // 2
    izq, der, acum = [], [], 0
    for it, env in zip(items, envueltos):
        if acum < objetivo:
            izq.append(env)
            acum += len(env)
        else:
            der.append(env)

    y_inicio = L.y
    max_bajada = 0
    for col_i, columna in enumerate((izq, der)):
        x = L.x0 + col_i * (ancho_col + 10 * mm)
        y = y_inicio
        for env in columna:
            alto_item = len(env) * 4.4 * mm + 1.4 * mm
            if y - alto_item < MARGEN + L.pie_alto:
                # La columna no cabe: se cierra la pagina y se sigue arriba.
                max_bajada = max(max_bajada, y_inicio - y)
                L.y = y
                L.nueva_pagina()
                y_inicio = L.y
                y = L.y
            c.setFillColor(COLOR['primary'])
            c.circle(x + 1.1 * mm, y - 1.6 * mm, 0.85 * mm, fill=True, stroke=False)
            for j, ln in enumerate(env):
                texto(c, x + 4.5 * mm, y - 2.4 * mm - j * 4.4 * mm, ln,
                      tam='apoyo', color='text')
            y -= alto_item
        max_bajada = max(max_bajada, y_inicio - y)
    L.y = y_inicio - max_bajada
    L.bajar(7 * mm)


def _calendario(L, simbolo, filas):
    """Timeline: marcador, fecha, posicion+estado, monto a la derecha.
    Cada fila se dibuja entera o se pasa a la pagina siguiente."""
    c = L.c
    for i, f in enumerate(filas):
        salto = L.asegurar(_ALTO_FILA_PAGO)
        if i and not salto:
            # Separador fino entre pagos: ordena la lectura sin encerrar
            # cada fila en una caja. Igual que .timeline__item + .timeline__item.
            regla(c, L.x0, L.y + 4.2 * mm, L.x1)
        estado = f.get('estado') or 'scheduled'
        # El marcador va a la altura de la FECHA, no entre las dos lineas.
        marcador(c, L.x0, L.y + 0.1 * mm, estado)
        texto(c, L.x0 + 7 * mm, L.y, recortar(f.get('cuando') or '', L.util * 0.45,
                                              tam='cuerpo', peso='medium'),
              tam='cuerpo', peso='medium', color='text')
        detalle = ' · '.join(x for x in (f.get('posicion'), f.get('etiqueta')) if x)
        texto(c, L.x0 + 7 * mm, L.y - 4.2 * mm,
              recortar(detalle, L.util * 0.5, tam='apoyo'),
              tam='apoyo', color='text_secondary')
        color_monto = 'danger_text' if estado == 'due' else 'text'
        texto(c, L.x1, L.y, moneda(f.get('monto'), simbolo), tam='cuerpo',
              peso='medium', color=color_monto, ancla='right')
        L.bajar(_ALTO_FILA_PAGO)
    L.bajar(1 * mm)


def _totales(L, simbolo, total, pagado, pendiente):
    """Subtotal / Pagado / Saldo, alineados a la derecha. El saldo cierra
    con mas peso y una regla por encima, igual que en la web."""
    c = L.c
    L.asegurar(23 * mm)
    x_etq = L.x1 - 62 * mm
    filas = [('Subtotal', moneda(total, simbolo), 'text_secondary', 'regular'),
             ('Pagado', '− ' + moneda(pagado, simbolo), 'success_text', 'regular')]
    for etq, val, color, peso in filas:
        texto(c, x_etq, L.y, etq, tam='cuerpo', color='text_secondary')
        texto(c, L.x1, L.y, val, tam='cuerpo', peso=peso, color=color, ancla='right')
        L.bajar(6 * mm)
    regla(c, x_etq, L.y + 2.5 * mm, L.x1, color='border_strong', grosor=0.8)
    L.bajar(3.5 * mm)
    saldado = (pendiente or 0) <= 0
    texto(c, x_etq, L.y, 'Saldo' if saldado else 'Saldo pendiente',
          tam='cuerpo', peso='bold', color='text')
    texto(c, L.x1, L.y, moneda(pendiente, simbolo), tam='cifra', peso='bold',
          color='success_text' if saldado else 'danger_text', ancla='right')
    L.bajar(8 * mm)


# ============================================================
# ENTRADA PUBLICA
# ============================================================
def render_invoice_pdf(doc, marca, *, simbolo='Q', generado_el=''):
    """Dibuja la factura y devuelve los bytes del PDF.

    `doc` es el MISMO dict que consume invoice_document.html -- el que arma
    _invoice_document() en app.py. Eso es lo que garantiza que la web y el
    PDF no puedan mostrar cifras distintas: leen la misma estructura, ya
    calculada, y ninguno de los dos hace aritmetica propia.
    """
    marca = marca or _MARCA_NO_RESUELTA
    registrar_fuente_documental()

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    ancho, alto = A4
    c.setTitle(f"{doc.get('invoice_id') or 'Factura'} · {marca['display_name']}")

    L = _Lienzo(c, ancho, alto, marca, 'Factura', doc.get('invoice_id') or '')

    _encabezado(L, doc.get('estado_label') or '', doc.get('estado_tono') or 'neutral')

    _metadata(L, [
        ('Cliente', doc.get('cliente_nombre')),
        ('Evento', doc.get('job_nombre')),
        ('Fecha del evento', doc.get('boda_fecha')),
        (doc.get('vence_label') or 'Vence', doc.get('vence')),
    ])
    if doc.get('estado_detalle'):
        texto(c, L.x0, L.y, recortar(doc['estado_detalle'], L.util, tam='apoyo'),
              tam='apoyo', color='text_secondary')
        L.bajar(8 * mm)

    _tres_cifras(L, simbolo, doc.get('total'), doc.get('pagado'), doc.get('pendiente'))

    prox = doc.get('proximo')
    if prox and (doc.get('pendiente') or 0) > 0:
        _proximo_pago(L, simbolo, prox.get('cuando') or '', prox.get('monto'))

    _seccion(L, 'Conceptos facturados')
    _conceptos(L, simbolo, doc.get('concepto') or '', doc.get('total'),
               doc.get('incluye') or [])

    filas = doc.get('filas_pago') or []
    if filas:
        vigentes = [f for f in filas if f.get('estado') != 'cancelled']
        titulo = 'Calendario de pagos' if len(vigentes) > 1 else 'Historial de pagos'
        _seccion(L, titulo, f'{len(vigentes)} pagos' if len(vigentes) > 1 else '')
        _calendario(L, simbolo, filas)

    _totales(L, simbolo, doc.get('total'), doc.get('pagado'), doc.get('pendiente'))

    L.generado_el = generado_el
    L.pie()
    c.save()
    datos = buffer.getvalue()
    buffer.close()
    return datos
