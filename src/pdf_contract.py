"""
CONTRATO PDF -- adaptacion print del contrato web  (3-sep-2026)

Tercer documento sobre el mismo sistema. Igual que hicimos con la factura,
este renderer reemplaza al anterior (hero navy a sangre completa,
estructura y tipografia propias) por una adaptacion print del contrato web:
mismos tokens, misma escala, mismas cards, mismo pie.

Comparte pdf_document_system con la factura, que a su vez es el espejo en
Python de los tokens del CSS. Un cambio de color o de radio se hace en un
solo lugar y llega a los tres documentos.

Lo especifico de un contrato frente a una factura:
  - no hay cifras de cobranza, hay UN valor de contrato
  - los terminos son parrafos largos: la paginacion tiene que poder partir
    un termino por dentro sin dejar un titulo huerfano al pie
  - hay firmas, con su trazo cuando existe

NO decide nada de negocio: el estado de firma y los terminos llegan
resueltos desde app.py.
"""
import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from src.pdf_document_system import (
    COLOR, TIPO, MARGEN,
    registrar_fuente_documental, texto, label, regla, card, badge,
    recortar, envolver, moneda,
)

_MARCA_NO_RESUELTA = {
    'display_name': 'Estudio no identificado',
    'tagline': '', 'phone': '', 'email': '', 'initial': '?',
}


class _Lienzo:
    """Mismo cursor con paginacion que usa la factura. Se mantiene aparte y
    no se importa de pdf_invoice para que el contrato pueda evolucionar sin
    arrastrar reglas pensadas para cuotas de pago."""

    def __init__(self, c, ancho, alto, marca, referencia):
        self.c = c
        self.ancho, self.alto = ancho, alto
        self.marca = marca
        self.referencia = referencia
        self.pagina = 1
        self.x0 = MARGEN
        self.x1 = ancho - MARGEN
        self.util = self.x1 - self.x0
        self.y = alto - MARGEN
        self.pie_alto = 13 * mm

    def espacio_libre(self):
        return self.y - (MARGEN + self.pie_alto)

    def asegurar(self, alto_necesario):
        if self.espacio_libre() < alto_necesario:
            self.nueva_pagina()
            return True
        return False

    def nueva_pagina(self):
        self.pie()
        self.c.showPage()
        self.pagina += 1
        self.y = self.alto - MARGEN
        c = self.c
        texto(c, self.x0, self.y - 4 * mm, self.marca['display_name'],
              tam='apoyo', peso='bold', color='text')
        texto(c, self.ancho / 2, self.y - 4 * mm, f'Contrato {self.referencia}',
              tam='apoyo', color='text_secondary', ancla='center')
        texto(c, self.x1, self.y - 4 * mm, f'Página {self.pagina}',
              tam='apoyo', color='text_secondary', ancla='right')
        self.y -= 7 * mm
        regla(c, self.x0, self.y, self.x1)
        self.y -= 8 * mm

    def pie(self):
        c = self.c
        y = MARGEN + 10 * mm
        regla(c, self.x0, y, self.x1)
        c.setFillColor(COLOR['primary'])
        c.rect(self.x0, y - 0.15 * mm, 16 * mm, 0.7 * mm, fill=True, stroke=False)
        izq = self.marca['display_name']
        if self.marca.get('email'):
            izq += f" · {self.marca['email']}"
        texto(c, self.x0, y - 6 * mm, recortar(izq, self.util * 0.7, tam='apoyo'),
              tam='apoyo', color='text_secondary')
        texto(c, self.x1, y - 6 * mm, f'Página {self.pagina}',
              tam='apoyo', color='muted', ancla='right')

    def bajar(self, mm_):
        self.y -= mm_


def _encabezado(L, estado_label, estado_tono):
    c, m = L.c, L.marca
    texto(c, L.x0, L.y - 5 * mm,
          recortar(m['display_name'], L.util * 0.55, tam='subtitulo', peso='bold'),
          tam='subtitulo', peso='bold', color='text')
    if m.get('tagline'):
        texto(c, L.x0, L.y - 10 * mm, recortar(m['tagline'], L.util * 0.5, tam='apoyo'),
              tam='apoyo', color='text_secondary')

    label(c, L.x1, L.y - 4 * mm, 'Contrato', ancla='right')
    texto(c, L.x1, L.y - 9.5 * mm, L.referencia, tam='cuerpo', peso='medium',
          color='text', ancla='right')
    if estado_label:
        from reportlab.pdfbase import pdfmetrics
        f = registrar_fuente_documental()
        ancho = pdfmetrics.stringWidth(estado_label, f['medium'], TIPO['apoyo']) + 4.8 * mm
        badge(c, L.x1 - ancho, L.y - 16 * mm, estado_label, estado_tono)

    L.bajar(22 * mm)
    regla(c, L.x0, L.y, L.x1)
    L.bajar(9 * mm)


def _partes(L, cliente, marca):
    """Las dos partes, lado a lado. Es lo primero que un contrato tiene que
    dejar claro: quien contrata a quien."""
    c = L.c
    alto = 26 * mm
    L.asegurar(alto + 6 * mm)
    ancho_col = (L.util - 6 * mm) / 2
    for i, (rol, nombre, lineas) in enumerate([
        ('Cliente', cliente['nombre'], cliente['contacto']),
        ('Estudio', marca['display_name'],
         [x for x in (marca.get('email'), marca.get('phone')) if x]),
    ]):
        x = L.x0 + i * (ancho_col + 6 * mm)
        card(c, x, L.y - alto, ancho_col, alto)
        label(c, x + 5 * mm, L.y - 7 * mm, rol)
        texto(c, x + 5 * mm, L.y - 13.5 * mm,
              recortar(nombre, ancho_col - 10 * mm, tam='cuerpo', peso='bold'),
              tam='cuerpo', peso='bold', color='text')
        for j, ln in enumerate(lineas[:2]):
            texto(c, x + 5 * mm, L.y - 18.5 * mm - j * 4.4 * mm,
                  recortar(ln, ancho_col - 10 * mm, tam='apoyo'),
                  tam='apoyo', color='text_secondary')
    L.bajar(alto + 6 * mm)


def _resumen(L, simbolo, evento, fecha, valor):
    campos = [(k, v) for k, v in
              (('Evento', evento), ('Fecha del evento', fecha),
               ('Valor del contrato', moneda(valor, simbolo))) if v]
    if not campos:
        return
    L.asegurar(16 * mm)
    ancho_col = L.util / len(campos)
    for i, (k, v) in enumerate(campos):
        x = L.x0 + i * ancho_col
        label(L.c, x, L.y, k)
        lineas = envolver(str(v), ancho_col - 5 * mm, tam='cuerpo', peso='medium', max_lineas=2)
        for j, ln in enumerate(lineas):
            texto(L.c, x, L.y - 5.5 * mm - j * 4.6 * mm, ln,
                  tam='cuerpo', peso='medium', color='text')
    L.bajar(15 * mm)
    regla(L.c, L.x0, L.y, L.x1)
    L.bajar(8 * mm)


def _seccion(L, titulo, necesita=0):
    """Cabecera de seccion.

    `necesita` es el alto del bloque que viene INMEDIATAMENTE despues. Sin
    eso, el titulo cabe al pie de la pagina, el contenido no, y queda un
    encabezado huerfano colgando -- que es justo lo que se ve mal. Con eso,
    titulo y primer contenido saltan juntos.
    """
    L.asegurar(16 * mm + necesita)
    texto(L.c, L.x0, L.y, titulo, tam='seccion', peso='bold', color='text')
    L.bajar(5 * mm)
    regla(L.c, L.x0, L.y, L.x1)
    L.bajar(6 * mm)


def _partir_titulo(titulo):
    """"3. Responsabilidades del Cliente" -> ("3", "Responsabilidades...").

    El numero de clausula se pinta aparte, como indice, para que el
    contrato se pueda citar sin que el numero compita con el texto. Si el
    titulo no viene numerado, se devuelve entero.
    """
    partes = str(titulo or '').split('. ', 1)
    if len(partes) == 2 and partes[0].strip().isdigit():
        return partes[0].strip(), partes[1].strip()
    return '', str(titulo or '').strip()


def _terminos(L, terms):
    """Los terminos pueden ser largos. Se parten por LINEA, no por bloque:
    aplicar 'no cortar' a un termino entero dejaria media pagina vacia. Lo
    que si se garantiza es que un titulo nunca quede solo al pie -- se exige
    espacio para el titulo mas las dos primeras lineas.

    Una linea que empieza con "* " es un item de lista, no un parrafo con un
    asterisco adelante: se pinta con su marcador y su sangria, igual que en
    la web. El texto guardado no cambia.
    """
    c = L.c
    sangria = 9 * mm       # el cuerpo alinea con el titulo, no con el numero
    for titulo, cuerpo in terms:
        num, titulo_limpio = _partir_titulo(titulo)
        x_texto = L.x0 + (sangria if num else 0)
        ancho = L.x1 - x_texto

        # Cada linea se resuelve a (es_item, lineas_envueltas)
        bloques = []
        for parrafo in str(cuerpo or '').split('\n'):
            crudo = parrafo.strip()
            if not crudo:
                continue
            item = crudo.startswith('*')
            limpio = crudo.lstrip('*').strip() if item else crudo
            bloques.append((item, envolver(limpio, ancho - (5 * mm if item else 0),
                                           tam='apoyo')))

        primeras = sum(len(b[1]) for b in bloques[:1]) or 1
        L.asegurar(6 * mm + min(primeras, 2) * 4.6 * mm + 5 * mm)

        if titulo_limpio:
            if num:
                texto(c, L.x0, L.y, num, tam='apoyo', peso='bold', color='primary')
            texto(c, x_texto, L.y, titulo_limpio, tam='cuerpo', peso='bold', color='text')
            L.bajar(6 * mm)

        for item, lineas in bloques:
            for i, ln in enumerate(lineas):
                if L.espacio_libre() < 6 * mm:
                    L.nueva_pagina()
                if item and i == 0:
                    c.setFillColor(COLOR['primary'])
                    c.circle(x_texto + 1.2 * mm, L.y + 1.2 * mm, 0.7 * mm,
                             fill=True, stroke=False)
                texto(c, x_texto + (5 * mm if item else 0), L.y, ln,
                      tam='apoyo', color='text_secondary')
                L.bajar(4.6 * mm)
            if item:
                L.bajar(0.8 * mm)
        L.bajar(4.5 * mm)


def _firmas(L, contrato, cliente, marca, firmas_es):
    """Dos recuadros con el trazo cuando existe. Si falta, una linea para
    firmar a mano: un contrato impreso sin firmar tiene que poder firmarse
    con lapicero."""
    c = L.c
    alto = 42 * mm
    L.asegurar(alto + 6 * mm)
    ancho_col = (L.util - 6 * mm) / 2

    partes = [
        # Las fechas llegan YA formateadas desde app.py: un documento que ve
        # el cliente no puede mostrar 2026-01-15.
        ('Estudio', marca['display_name'],
         contrato.get('photographer_signed'),
         contrato.get('photographer_signature_preview'),
         firmas_es.get('estudio', '')),
        ('Cliente', cliente['nombre'],
         contrato.get('signed'),
         contrato.get('signature_preview'),
         firmas_es.get('cliente', '')),
    ]
    for i, (rol, nombre, firmado, trazo, cuando) in enumerate(partes):
        x = L.x0 + i * (ancho_col + 6 * mm)
        card(c, x, L.y - alto, ancho_col, alto, fondo='surface_2')
        label(c, x + 5 * mm, L.y - 7 * mm, rol)
        texto(c, x + 5 * mm, L.y - 13 * mm,
              recortar(nombre, ancho_col - 10 * mm, tam='cuerpo', peso='bold'),
              tam='cuerpo', peso='bold', color='text')

        y_trazo = L.y - 30 * mm
        dibujado = False
        if firmado and trazo and str(trazo).startswith('data:image'):
            try:
                import base64
                cabecera, datos = str(trazo).split(',', 1)
                img = ImageReader(io.BytesIO(base64.b64decode(datos)))
                c.drawImage(img, x + 6 * mm, y_trazo, width=ancho_col - 12 * mm,
                            height=12 * mm, preserveAspectRatio=True,
                            anchor='sw', mask='auto')
                dibujado = True
            except Exception:
                # Una firma corrupta no puede tumbar la descarga del contrato.
                dibujado = False
        if not dibujado:
            regla(c, x + 6 * mm, y_trazo, x + ancho_col - 6 * mm, color='border_strong')

        if firmado:
            texto(c, x + 5 * mm, L.y - 35 * mm, 'Firmado' + (f' · {cuando}' if cuando else ''),
                  tam='apoyo', peso='medium', color='success_text')
        else:
            texto(c, x + 5 * mm, L.y - 35 * mm, 'Pendiente de firma',
                  tam='apoyo', color='text_secondary')
    L.bajar(alto + 6 * mm)


def render_contract_pdf(contrato, job, cliente, marca, *, terms=None,
                        simbolo='Q', fecha_evento='', firmas_es=None):
    """Dibuja el contrato y devuelve los bytes del PDF.

    `cliente` es un dict {'nombre', 'contacto': [lineas]} ya resuelto por el
    llamador, para que este modulo no tenga que saber como se arma un nombre
    en el CRM. `firmas_es` trae las fechas de firma ya formateadas
    ({'estudio': '15 enero 2026', 'cliente': ...}).
    """
    marca = marca or _MARCA_NO_RESUELTA
    registrar_fuente_documental()

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    ancho, alto = A4
    referencia = contrato.get('id') or ''
    c.setTitle(f"Contrato {referencia} · {marca['display_name']}")

    L = _Lienzo(c, ancho, alto, marca, referencia)

    firmado_todo = bool(contrato.get('signed') and contrato.get('photographer_signed'))
    if firmado_todo:
        etiqueta, tono = 'Firmado', 'success'
    elif contrato.get('signed'):
        etiqueta, tono = 'Falta la firma del estudio', 'warning'
    else:
        etiqueta, tono = 'Pendiente de firma', 'warning'

    _encabezado(L, etiqueta, tono)
    _partes(L, cliente, marca)
    _resumen(L, simbolo, (job or {}).get('nombre'), fecha_evento,
             (job or {}).get('price_total'))

    if terms:
        _seccion(L, 'Términos y condiciones', necesita=18 * mm)
        _terminos(L, terms)

    _seccion(L, 'Firmas', necesita=48 * mm)
    _firmas(L, contrato, cliente, marca, firmas_es or {})

    L.pie()
    c.save()
    datos = buffer.getvalue()
    buffer.close()
    return datos
