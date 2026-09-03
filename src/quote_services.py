"""
SERVICIOS ESTRUCTURADOS DE COTIZACION  (3-sep-2026)

El principio, en una linea: el Quote Builder guarda INFORMACION, el
renderer decide COMO SE VE.

Hasta ahora una inclusion era un string suelto ("2 fotografos"). Eso
obligaba a adivinar el icono leyendo el texto, hacia imposible pluralizar
bien, y dejaba que se guardaran cosas como "8 horas de cobertura + 1 hora
extra" partidas en dos lineas. Aca un servicio es un dato:

    {'tipo': 'fotografos', 'cantidad': 2}

y este modulo se encarga de que eso se lea "2 fotografos" con un icono de
camara, agrupado bajo Fotografia, en la posicion que le toca.

Lo que este modulo NO hace, a proposito:
  - no guarda iconos: devuelve el NOMBRE de un icono que la plantilla ya
    conoce (los de _document_parts.html);
  - no guarda HTML ni emojis;
  - no decide columnas ni colores: eso es del CSS.

Compatibilidad: `derivar_incluye()` produce la lista plana de strings de
siempre, asi que el PDF, el portal y cualquier vista que solo sepa leer
`incluye` siguen funcionando sin enterarse de que esto existe. Y
`clasificar_legacy()` permite que una cotizacion vieja se beneficie del
renderer nuevo sin migrar nada en la base.
"""
import re
import unicodedata


# ============================================================
# CATEGORIAS -- el orden es el orden en que se muestran
# ============================================================
CATEGORIAS = [
    ('fotografia', 'Fotografía', 10),
    ('video',      'Video',      20),
    ('cobertura',  'Cobertura',  30),
    ('entrega',    'Entrega',    40),
    ('eventos',    'Eventos',    50),
    ('extras',     'Extras',     60),
    ('viaticos',   'Viáticos',   70),
    ('equipo',     'Equipo',     80),
    ('personal',   'Personal',   85),
    ('otros',      'Otros',      99),
]
_CAT_TITULO = {c: t for c, t, _ in CATEGORIAS}
_CAT_ORDEN = {c: o for c, _, o in CATEGORIAS}

ICONO_FALLBACK = 'check-circle'


# ============================================================
# CATALOGO DE TIPOS CONOCIDOS
# ============================================================
# Cada tipo declara:
#   categoria  -> a que grupo pertenece (y por lo tanto su orden)
#   icono      -> nombre del icono en _document_parts.html
#   singular / plural -> para que "1 fotografo" no diga "1 fotografos"
#   unidad     -> texto que acompaña a un valor con unidad (horas, minutos)
#   etiqueta   -> nombre para el selector del builder
#   sin_cantidad -> el servicio es binario: esta o no esta (ej. galeria)
#
# Añadir un tipo aca es todo lo que hace falta: el builder lo ofrece, el
# renderer le pone icono y lo agrupa, y el formateador lo escribe bien.
CATALOGO = {
    # ---- Fotografia ----
    'fotografos': dict(categoria='fotografia', icono='camera', orden=10,
                       singular='fotógrafo', plural='fotógrafos',
                       etiqueta='Fotógrafos'),
    'segundo_fotografo': dict(categoria='fotografia', icono='camera', orden=11,
                              singular='segundo fotógrafo', plural='segundos fotógrafos',
                              etiqueta='Segundo fotógrafo'),
    'fotos_digitales': dict(categoria='entrega', icono='image', orden=41,
                            singular='fotografía digital', plural='fotografías digitales',
                            etiqueta='Fotografías digitales'),
    'sesion': dict(categoria='fotografia', icono='sparkle', orden=14,
                   singular='sesión', plural='sesiones', etiqueta='Sesión'),
    'save_the_date': dict(categoria='fotografia', icono='sparkle', orden=15,
                          sin_cantidad=True, texto='Save the Date',
                          etiqueta='Save the Date'),

    # ---- Video ----
    'videografos': dict(categoria='video', icono='video', orden=20,
                        singular='videógrafo', plural='videógrafos',
                        etiqueta='Videógrafos'),
    'video_principal': dict(categoria='video', icono='video', orden=22,
                            unidad='minutos', unidad_singular='minuto',
                            plantilla='Video de {valor} {unidad}',
                            etiqueta='Video principal'),
    'video_vertical': dict(categoria='video', icono='video', orden=23,
                           unidad='minutos', unidad_singular='minuto',
                           plantilla='Video vertical de {valor} {unidad}',
                           etiqueta='Video vertical'),

    # ---- Cobertura ----
    'horas_cobertura': dict(categoria='cobertura', icono='clock', orden=30,
                            unidad='horas', unidad_singular='hora',
                            plantilla='{valor} {unidad} de cobertura',
                            etiqueta='Horas de cobertura'),
    'horas_cobertura_video': dict(categoria='cobertura', icono='clock', orden=31,
                                  unidad='horas', unidad_singular='hora',
                                  plantilla='{valor} {unidad} de cobertura de video',
                                  etiqueta='Horas de cobertura de video'),
    'hora_extra': dict(categoria='extras', icono='clock', orden=60,
                       singular='hora adicional', plural='horas adicionales',
                       etiqueta='Hora adicional'),

    # ---- Entrega ----
    'galeria_online': dict(categoria='entrega', icono='image', orden=42,
                           sin_cantidad=True, texto='Galería online',
                           etiqueta='Galería online'),
    'galeria_fotografica': dict(categoria='entrega', icono='image', orden=43,
                                sin_cantidad=True, texto='Galería fotográfica',
                                etiqueta='Galería fotográfica'),
    'descarga_alta': dict(categoria='entrega', icono='download', orden=44,
                          sin_cantidad=True, texto='Descarga en alta resolución',
                          etiqueta='Descarga en alta resolución'),
    'dias_entrega': dict(categoria='entrega', icono='download', orden=45,
                         unidad='días hábiles', unidad_singular='día hábil',
                         plantilla='Entrega en {valor} {unidad}',
                         etiqueta='Tiempo de entrega'),
    'album': dict(categoria='entrega', icono='file', orden=46,
                  singular='álbum impreso', plural='álbumes impresos',
                  etiqueta='Álbum impreso'),

    # ---- Extras ----
    'drone': dict(categoria='extras', icono='sparkle', orden=61,
                  sin_cantidad=True, texto='Cobertura con drone',
                  etiqueta='Drone'),
    'cabina_fotos': dict(categoria='extras', icono='camera', orden=62,
                         sin_cantidad=True, texto='Cabina de fotos',
                         etiqueta='Cabina de fotos'),

    # ---- Viaticos ----
    'transporte': dict(categoria='viaticos', icono='pin', orden=70,
                       sin_cantidad=True, texto='Transporte', etiqueta='Transporte'),
    'hospedaje': dict(categoria='viaticos', icono='pin', orden=71,
                      sin_cantidad=True, texto='Hospedaje', etiqueta='Hospedaje'),
    'alimentacion': dict(categoria='viaticos', icono='pin', orden=72,
                         sin_cantidad=True, texto='Alimentación', etiqueta='Alimentación'),

    # ---- Personal / equipo ----
    'asistente': dict(categoria='personal', icono='users', orden=85,
                      singular='asistente', plural='asistentes', etiqueta='Asistente'),

    # ---- Comodin ----
    'personalizado': dict(categoria='otros', icono=ICONO_FALLBACK, orden=99,
                          libre=True, etiqueta='Concepto personalizado'),
}


def catalogo_para_selector():
    """Tipos agrupados por categoria, para el selector del builder.

    Devuelve datos, no HTML: [(clave_categoria, titulo, [(tipo, etiqueta,
    icono, pide_cantidad, unidad)])]. La plantilla decide como dibujarlo.
    """
    por_cat = {}
    for tipo, meta in CATALOGO.items():
        cat = meta['categoria']
        por_cat.setdefault(cat, []).append((
            tipo,
            meta.get('etiqueta') or tipo,
            meta.get('icono') or ICONO_FALLBACK,
            not meta.get('sin_cantidad') and not meta.get('libre'),
            meta.get('unidad') or '',
        ))
    salida = []
    for clave, titulo, _orden in CATEGORIAS:
        if clave in por_cat:
            salida.append((clave, titulo,
                           sorted(por_cat[clave], key=lambda t: CATALOGO[t[0]].get('orden', 99))))
    return salida


# ============================================================
# ICON RESOLVER
# ============================================================
def resolver_icono(servicio):
    """Nombre del icono para un servicio. Nunca devuelve vacio.

    Se resuelve por TIPO, no leyendo el texto: es lo que permite que el
    icono sea correcto aunque el texto cambie, y que un concepto
    personalizado tenga un icono neutro en vez de uno adivinado mal.
    """
    if not isinstance(servicio, dict):
        return ICONO_FALLBACK
    meta = CATALOGO.get(servicio.get('tipo'))
    if meta and meta.get('icono') and not meta.get('libre'):
        return meta['icono']
    # Sin tipo conocido, la categoria todavia dice algo util.
    por_categoria = {
        'fotografia': 'camera', 'video': 'video', 'cobertura': 'clock',
        'entrega': 'download', 'eventos': 'calendar', 'viaticos': 'pin',
        'personal': 'users', 'equipo': 'camera',
    }
    return por_categoria.get(servicio.get('categoria'), ICONO_FALLBACK)


def categoria_de(servicio):
    """Categoria efectiva.

    En un tipo del catalogo manda el catalogo: "fotografos" es
    Fotografia y no tiene sentido que una cotizacion lo mueva. Pero en
    un concepto PERSONALIZADO manda lo que eligio el usuario: si
    escribio "Segundo fotografo por 2 horas" y lo puso en Extras, va en
    Extras. Mandarlo a Otros seria ignorar la unica clasificacion que
    tenemos de ese concepto.
    """
    if not isinstance(servicio, dict):
        return 'otros'
    declarada = servicio.get('categoria')
    declarada = declarada if declarada in _CAT_TITULO else None
    meta = CATALOGO.get(servicio.get('tipo'))
    if meta and not meta.get('libre'):
        return meta['categoria']
    return declarada or 'otros'


def orden_de(servicio):
    """Posicion sugerida. El usuario puede sobreescribirla con 'orden'."""
    if isinstance(servicio, dict) and isinstance(servicio.get('orden'), (int, float)):
        return float(servicio['orden'])
    meta = CATALOGO.get((servicio or {}).get('tipo'))
    if meta:
        return float(meta.get('orden', 99))
    return float(_CAT_ORDEN.get(categoria_de(servicio), 99))


# ============================================================
# FORMATEADOR CENTRAL
# ============================================================
def _plural(n, singular, plural):
    """Concordancia. Es la razon por la que esto vive en un solo lugar:
    escrito a mano en quince plantillas, en alguna iba a decir
    '1 fotografos'."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return plural
    return singular if abs(n - 1) < 1e-9 else plural


def _numero(valor):
    """4.0 -> "4", 4.5 -> "4.5", 500 -> "500". Sin decimales de adorno."""
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return str(valor or '')
    if abs(f - round(f)) < 1e-9:
        return f'{int(round(f)):,}'.replace(',', ',')
    return f'{f:g}'


def formatear_servicio(servicio):
    """Texto que ve el cliente. Una sola frase, entera, nunca partida.

    Es el punto que resuelve lo de "8 horas de cobertura + 1 hora extra":
    las horas extra son un CAMPO del servicio de cobertura, no otra linea,
    asi que no hay forma de que se separen.
    """
    if not isinstance(servicio, dict):
        return str(servicio or '')

    tipo = servicio.get('tipo')
    meta = CATALOGO.get(tipo)

    # Concepto libre: manda el texto que escribio el usuario.
    if not meta or meta.get('libre'):
        return (servicio.get('texto') or servicio.get('descripcion') or '').strip()

    # Servicio binario: esta o no esta.
    if meta.get('sin_cantidad'):
        base = servicio.get('texto') or meta.get('texto') or meta.get('etiqueta') or ''
        return base.strip()

    valor = servicio.get('cantidad')
    if valor in (None, ''):
        valor = servicio.get('valor')

    # Con plantilla y unidad: "8 horas de cobertura", "Video de 5 minutos".
    if meta.get('plantilla'):
        unidad = _plural(valor, meta.get('unidad_singular') or meta.get('unidad', ''),
                         meta.get('unidad', ''))
        texto = meta['plantilla'].format(valor=_numero(valor), unidad=unidad)
    else:
        # Cantidad + sustantivo: "2 fotografos".
        sustantivo = _plural(valor, meta.get('singular', ''), meta.get('plural', ''))
        texto = f'{_numero(valor)} {sustantivo}'.strip()

    # Horas adicionales: parte de la MISMA frase.
    extra = servicio.get('extra')
    if extra not in (None, '', 0):
        unidad_extra = _plural(extra, 'hora extra', 'horas extra')
        texto += f' + {_numero(extra)} {unidad_extra}'

    return texto


def formatear_servicios(servicios):
    """Lista de textos, en el orden en que se van a mostrar."""
    return [formatear_servicio(s) for s in ordenar(servicios) if formatear_servicio(s)]


# ============================================================
# AGRUPACION Y ORDEN
# ============================================================
def ordenar(servicios):
    """Orden sugerido: por categoria y luego por tipo. Estable, asi que si
    el usuario reordena a mano (campo 'orden') se respeta."""
    if not isinstance(servicios, list):
        return []
    validos = [s for s in servicios if isinstance(s, dict)]
    return sorted(validos, key=lambda s: (_CAT_ORDEN.get(categoria_de(s), 99), orden_de(s)))


def agrupar(servicios):
    """Agrupa por categoria para que el documento no sea una lista plana.

    Devuelve [{'clave', 'titulo', 'servicios': [{'texto','icono','precio'}]}].
    Una categoria sin servicios no aparece: no se inventan secciones vacias.
    """
    grupos = {}
    for s in ordenar(servicios):
        texto = formatear_servicio(s)
        if not texto:
            continue
        cat = categoria_de(s)
        grupos.setdefault(cat, []).append({
            'texto': texto,
            'icono': resolver_icono(s),
            'precio': s.get('precio'),
            'tipo': s.get('tipo'),
        })
    salida = []
    for clave, titulo, _o in CATEGORIAS:
        if grupos.get(clave):
            salida.append({'clave': clave, 'titulo': titulo, 'servicios': grupos[clave]})
    return salida


# ============================================================
# COMPATIBILIDAD
# ============================================================
def derivar_incluye(servicios):
    """Lista plana de strings, como la de siempre.

    Es lo que mantiene funcionando al PDF, al portal y a cualquier vista
    que solo sepa leer `incluye`. Los datos estructurados son la fuente;
    esto es una proyeccion de ellos.
    """
    return formatear_servicios(servicios)


def derivar_groups(servicios):
    """Los `groups` [{title, items[]}] que ya guarda el modelo, derivados de
    los servicios. Asi una cotizacion nueva es legible por el codigo viejo
    sin ninguna rama especial."""
    return [{'title': g['titulo'], 'items': [s['texto'] for s in g['servicios']]}
            for g in agrupar(servicios)]


def normalizar_servicio(dato):
    """Sanea un servicio que llega del formulario. Devuelve None si no tiene
    contenido util -- asi una fila vacia no termina como un bullet en blanco
    en la cotizacion del cliente."""
    if not isinstance(dato, dict):
        # Un string suelto sigue siendo valido: se guarda como personalizado
        # sin adivinar nada.
        texto = str(dato or '').strip()
        return {'tipo': 'personalizado', 'categoria': 'otros', 'texto': texto} if texto else None

    tipo = (dato.get('tipo') or '').strip() or 'personalizado'
    meta = CATALOGO.get(tipo)
    servicio = {'tipo': tipo if meta else 'personalizado'}

    if not meta or meta.get('libre'):
        texto = (dato.get('texto') or dato.get('descripcion') or '').strip()
        if not texto:
            return None
        servicio['texto'] = texto
        cat = (dato.get('categoria') or '').strip()
        servicio['categoria'] = cat if cat in _CAT_TITULO else 'otros'
    elif meta.get('sin_cantidad'):
        if dato.get('texto'):
            servicio['texto'] = str(dato['texto']).strip()
    else:
        valor = dato.get('cantidad', dato.get('valor'))
        try:
            valor = float(valor)
        except (TypeError, ValueError):
            return None
        if valor <= 0:
            return None
        servicio['cantidad'] = int(valor) if abs(valor - round(valor)) < 1e-9 else valor
        extra = dato.get('extra')
        if extra not in (None, ''):
            try:
                extra = float(extra)
                if extra > 0:
                    servicio['extra'] = int(extra) if abs(extra - round(extra)) < 1e-9 else extra
            except (TypeError, ValueError):
                pass

    precio = dato.get('precio')
    if precio not in (None, ''):
        try:
            precio = float(precio)
            if precio > 0:
                servicio['precio'] = precio
        except (TypeError, ValueError):
            pass

    if isinstance(dato.get('orden'), (int, float)):
        servicio['orden'] = dato['orden']
    return servicio


def normalizar_servicios(datos):
    """Sanea una lista completa, descartando lo que no aporta nada."""
    if not isinstance(datos, list):
        return []
    salida = []
    for d in datos:
        s = normalizar_servicio(d)
        if s:
            salida.append(s)
    return salida


# ============================================================
# LEGACY -- clasificar sin destruir
# ============================================================
def _sin_acentos(texto):
    return ''.join(c for c in unicodedata.normalize('NFD', texto or '')
                   if unicodedata.category(c) != 'Mn').lower()


# Patrones para reconocer inclusiones viejas. Deliberadamente
# conservadores: si hay duda, NO se clasifica. Un icono generico es un
# problema estetico; un servicio mal clasificado es un problema comercial.
_PATRONES = [
    (r'^(\d+)\s+fotografos?$', 'fotografos'),
    (r'^(\d+)\s+videografos?$', 'videografos'),
    (r'^(\d+)\s+camarografos?$', 'videografos'),
    (r'^(\d+)\s+asistentes?$', 'asistente'),
    (r'^(\d+)\s+horas?\s+(?:continuas?\s+)?de\s+cobertura$', 'horas_cobertura'),
    (r'^(\d+)\s+horas?\s+de\s+cobertura\s+de\s+video$', 'horas_cobertura_video'),
    (r'^(\d+)\s+(?:imagenes|fotografias|fotos)\s+digitales$', 'fotos_digitales'),
    (r'^(\d+)\s+(?:imagenes|fotografias|fotos)$', 'fotos_digitales'),
]
_EXACTOS = {
    'galeria online': 'galeria_online',
    'galeria fotografica': 'galeria_fotografica',
    'galeria fotografica online': 'galeria_online',
    'save the date': 'save_the_date',
    'transporte': 'transporte',
    'hospedaje': 'hospedaje',
    'alimentacion': 'alimentacion',
    'viaticos': 'transporte',
    'drone': 'drone',
    'descarga en alta resolucion': 'descarga_alta',
}


def clasificar_legacy(texto):
    """Intenta reconocer una inclusion vieja (string) como servicio.

    Devuelve un dict de servicio, o None si no se reconoce con seguridad.
    NUNCA se llama para modificar la base: sirve para que el renderer le
    ponga un icono decente a una cotizacion vieja, y como insumo para una
    migracion que se revise a mano antes de ejecutar.
    """
    if not isinstance(texto, str):
        return None
    limpio = _sin_acentos(texto.strip()).rstrip('.').strip()
    if not limpio:
        return None

    if limpio in _EXACTOS:
        return {'tipo': _EXACTOS[limpio], 'texto': texto.strip()}

    for patron, tipo in _PATRONES:
        m = re.match(patron, limpio)
        if m:
            return {'tipo': tipo, 'cantidad': int(m.group(1))}

    m = re.match(r'^(\d+):(\d+)\s+min(?:utos)?\s+de\s+video$', limpio)
    if m:
        return {'tipo': 'video_principal', 'cantidad': int(m.group(1)) or 1}
    m = re.match(r'^(\d+):(\d+)\s+min(?:utos)?\s+(?:de\s+)?video\s+vertical$', limpio)
    if m:
        return {'tipo': 'video_vertical', 'cantidad': int(m.group(1)) or 1}
    return None


def icono_para_texto_legacy(texto):
    """Icono para una inclusion vieja. Si no se reconoce, el neutro.

    Reemplaza al truco anterior de adivinar por palabras sueltas dentro del
    texto, que acertaba a veces y fallaba callado el resto.
    """
    s = clasificar_legacy(texto)
    return resolver_icono(s) if s else ICONO_FALLBACK


def informe_migracion(inclusiones):
    """Cuantas inclusiones viejas se podrian clasificar y cuales no.

    Para decidir con datos si vale la pena migrar, sin ejecutar nada.
    """
    reconocidas, sin_reconocer = [], []
    for texto in (inclusiones or []):
        s = clasificar_legacy(texto)
        (reconocidas if s else sin_reconocer).append((texto, s))
    total = len(inclusiones or [])
    return {
        'total': total,
        'reconocidas': len(reconocidas),
        'sin_reconocer': len(sin_reconocer),
        'porcentaje': round(100.0 * len(reconocidas) / total, 1) if total else 0.0,
        'detalle_reconocidas': reconocidas,
        'detalle_sin_reconocer': [t for t, _ in sin_reconocer],
    }
