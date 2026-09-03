"""
SERVICIOS ESTRUCTURADOS DE COTIZACION -- tests  (3-sep-2026)

Kevin: "yo ingreso QUE estoy vendiendo, FlowCRM decide COMO SE VE".
Estos tests verifican esa separacion y, sobre todo, que las cosas que
antes salian mal no puedan volver a salir mal:

  - "1 fotografos" (concordancia)
  - "8 horas de cobertura" / "extra" partido en dos conceptos
  - iconos adivinados leyendo el texto
  - una cotizacion vieja que deja de renderizar

Corren sin Flask.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import quote_services as qs


# ============================================================
# Concordancia -- criterio de aceptacion explicito de Kevin
# ============================================================
def test_singular_y_plural_de_personas():
    assert qs.formatear_servicio({'tipo': 'fotografos', 'cantidad': 1}) == '1 fotógrafo'
    assert qs.formatear_servicio({'tipo': 'fotografos', 'cantidad': 2}) == '2 fotógrafos'
    assert qs.formatear_servicio({'tipo': 'videografos', 'cantidad': 1}) == '1 videógrafo'
    assert qs.formatear_servicio({'tipo': 'videografos', 'cantidad': 3}) == '3 videógrafos'


def test_singular_y_plural_de_unidades():
    assert qs.formatear_servicio({'tipo': 'horas_cobertura', 'cantidad': 1}) == '1 hora de cobertura'
    assert qs.formatear_servicio({'tipo': 'horas_cobertura', 'cantidad': 8}) == '8 horas de cobertura'
    assert qs.formatear_servicio({'tipo': 'video_principal', 'cantidad': 1}) == 'Video de 1 minuto'
    assert qs.formatear_servicio({'tipo': 'video_principal', 'cantidad': 5}) == 'Video de 5 minutos'


def test_cantidades_grandes_llevan_separador_de_miles():
    assert qs.formatear_servicio({'tipo': 'fotos_digitales', 'cantidad': 500}) == '500 fotografías digitales'
    assert qs.formatear_servicio({'tipo': 'fotos_digitales', 'cantidad': 1500}) == '1,500 fotografías digitales'


# ============================================================
# Horas extra: UNA frase, nunca dos conceptos
# ============================================================
def test_las_horas_extra_no_se_parten_en_otro_concepto():
    """El bug original: "8 horas de cobertura + 1 hora" y "extra" aparecian
    como dos items. Aca las extra son un CAMPO del mismo servicio, asi que
    no hay forma de que se separen."""
    s = {'tipo': 'horas_cobertura', 'cantidad': 8, 'extra': 1}
    assert qs.formatear_servicio(s) == '8 horas de cobertura + 1 hora extra'
    assert len(qs.derivar_incluye([s])) == 1, 'genero mas de una linea'


def test_horas_extra_en_plural():
    s = {'tipo': 'horas_cobertura', 'cantidad': 8, 'extra': 2}
    assert qs.formatear_servicio(s) == '8 horas de cobertura + 2 horas extra'


# ============================================================
# Iconos: por TIPO, nunca leyendo el texto
# ============================================================
def test_cada_tipo_resuelve_su_icono():
    esperado = {
        'fotografos': 'camera', 'videografos': 'video', 'horas_cobertura': 'clock',
        'fotos_digitales': 'image', 'galeria_online': 'image', 'transporte': 'pin',
        'hospedaje': 'pin', 'alimentacion': 'pin', 'album': 'file', 'asistente': 'users',
    }
    for tipo, icono in esperado.items():
        assert qs.resolver_icono({'tipo': tipo}) == icono, f'{tipo} resolvio mal'


def test_tipo_desconocido_cae_en_el_fallback():
    assert qs.resolver_icono({'tipo': 'algo_que_no_existe'}) == qs.ICONO_FALLBACK
    assert qs.resolver_icono({}) == qs.ICONO_FALLBACK
    assert qs.resolver_icono(None) == qs.ICONO_FALLBACK
    assert qs.resolver_icono('un string') == qs.ICONO_FALLBACK


def test_nunca_se_guarda_un_icono_dentro_del_texto():
    """El texto es informacion; el icono es presentacion. Si se mezclan,
    cambiar de libreria de iconos obligaria a reescribir los datos."""
    servicios = [{'tipo': t} for t in qs.CATALOGO]
    for s in servicios:
        texto = qs.formatear_servicio(s)
        assert '<svg' not in texto and '<' not in texto, f'markup en el texto: {texto!r}'
        for emoji in ('📷', '🎥', '✓', '●', '→'):
            assert emoji not in texto, f'emoji en el texto: {texto!r}'


# ============================================================
# Agrupacion y orden automaticos
# ============================================================
def test_los_servicios_se_agrupan_solos_por_categoria():
    servicios = [
        {'tipo': 'hospedaje'}, {'tipo': 'fotografos', 'cantidad': 2},
        {'tipo': 'videografos', 'cantidad': 1}, {'tipo': 'transporte'},
        {'tipo': 'galeria_online'},
    ]
    grupos = qs.agrupar(servicios)
    titulos = [g['titulo'] for g in grupos]
    # El orden lo define el sistema, no el orden en que se agregaron.
    assert titulos == ['Fotografía', 'Video', 'Entrega', 'Viáticos'], titulos


def test_una_categoria_sin_servicios_no_aparece():
    grupos = qs.agrupar([{'tipo': 'fotografos', 'cantidad': 1}])
    assert [g['titulo'] for g in grupos] == ['Fotografía']
    for g in grupos:
        assert g['servicios'], 'se genero un grupo vacio'


def test_el_usuario_puede_forzar_el_orden():
    a = {'tipo': 'fotografos', 'cantidad': 1, 'orden': 99}
    b = {'tipo': 'segundo_fotografo', 'cantidad': 1, 'orden': 1}
    textos = [s['texto'] for s in qs.agrupar([a, b])[0]['servicios']]
    assert textos[0].endswith('segundo fotógrafo'), textos


# ============================================================
# Conceptos personalizados
# ============================================================
def test_concepto_personalizado_respeta_su_categoria():
    s = {'tipo': 'personalizado', 'texto': 'Drone durante la recepción', 'categoria': 'extras'}
    assert qs.categoria_de(s) == 'extras'
    assert qs.formatear_servicio(s) == 'Drone durante la recepción'
    grupos = qs.agrupar([s])
    assert grupos[0]['titulo'] == 'Extras'


def test_un_tipo_del_catalogo_no_se_puede_recategorizar():
    """'fotografos' es Fotografia siempre. Si una cotizacion pudiera
    moverlo, dos documentos del mismo estudio se verian distintos."""
    s = {'tipo': 'fotografos', 'cantidad': 2, 'categoria': 'viaticos'}
    assert qs.categoria_de(s) == 'fotografia'


def test_concepto_personalizado_sin_texto_se_descarta():
    assert qs.normalizar_servicio({'tipo': 'personalizado', 'texto': '   '}) is None
    assert qs.normalizar_servicios([{'tipo': 'personalizado', 'texto': ''}]) == []


# ============================================================
# Normalizacion: basura del formulario que no debe llegar al cliente
# ============================================================
def test_la_normalizacion_descarta_lo_que_no_aporta():
    entrada = [
        {'tipo': 'fotografos', 'cantidad': 2},
        {'tipo': 'fotografos', 'cantidad': 0},       # cantidad invalida
        {'tipo': 'fotografos', 'cantidad': 'abc'},   # no numerico
        {'tipo': 'personalizado', 'texto': ''},      # vacio
        None, 'texto suelto', 123,
    ]
    salida = qs.normalizar_servicios(entrada)
    textos = [qs.formatear_servicio(s) for s in salida]
    assert '2 fotógrafos' in textos
    assert 'texto suelto' in textos, 'un string suelto deberia guardarse como personalizado'
    assert all(t.strip() for t in textos), 'quedo un item vacio'
    assert len(salida) == 3, textos


def test_normalizar_no_revienta_con_entradas_raras():
    for entrada in (None, [], 'texto', {'a': 1}, 5):
        qs.normalizar_servicios(entrada)


# ============================================================
# Compatibilidad hacia atras
# ============================================================
def test_incluye_plano_se_deriva_de_los_servicios():
    """El PDF y las vistas viejas solo saben leer `incluye`. Tienen que
    seguir viendo exactamente lo mismo."""
    servicios = [{'tipo': 'fotografos', 'cantidad': 2},
                 {'tipo': 'horas_cobertura', 'cantidad': 8, 'extra': 1},
                 {'tipo': 'galeria_online'}]
    incluye = qs.derivar_incluye(servicios)
    assert incluye == ['2 fotógrafos', '8 horas de cobertura + 1 hora extra', 'Galería online']
    assert all(isinstance(x, str) for x in incluye)


def test_groups_se_derivan_de_los_servicios():
    servicios = [{'tipo': 'fotografos', 'cantidad': 2}, {'tipo': 'transporte'}]
    groups = qs.derivar_groups(servicios)
    assert [g['title'] for g in groups] == ['Fotografía', 'Viáticos']
    assert groups[0]['items'] == ['2 fotógrafos']


# ============================================================
# Legacy: clasificar sin inventar
# ============================================================
def test_legacy_reconoce_lo_inequivoco():
    casos = {
        '2 fotografos': 'fotografos',
        '1 videografo': 'videografos',
        '500 imagenes digitales': 'fotos_digitales',
        '8 horas de cobertura': 'horas_cobertura',
        '12 horas continuas de cobertura': 'horas_cobertura',
        'Galeria online': 'galeria_online',
        'Save the Date': 'save_the_date',
        'Transporte': 'transporte',
    }
    for texto, tipo in casos.items():
        s = qs.clasificar_legacy(texto)
        assert s and s['tipo'] == tipo, f'{texto!r} -> {s}'


def test_legacy_no_inventa_cuando_hay_duda():
    """Un icono generico es un detalle estetico. Uno equivocado le dice al
    cliente algo que no es."""
    ambiguos = ['Cobertura completa para capturar cada momento', 'personalizada - Boda',
                'extra', 'Weddings', 'Eventos:', 'ya pagadas', '']
    for texto in ambiguos:
        assert qs.clasificar_legacy(texto) is None, f'clasifico de mas: {texto!r}'
        assert qs.icono_para_texto_legacy(texto) == qs.ICONO_FALLBACK


def test_legacy_con_acentos_y_sin_acentos():
    for texto in ('2 fotógrafos', '2 fotografos', '2 FOTÓGRAFOS'):
        s = qs.clasificar_legacy(texto)
        assert s and s['tipo'] == 'fotografos', f'{texto!r} -> {s}'


def test_el_informe_de_migracion_no_modifica_nada():
    original = ['2 fotografos', 'algo raro', 'Galeria online']
    copia = list(original)
    inf = qs.informe_migracion(original)
    assert original == copia, 'el informe modifico la lista'
    assert inf['total'] == 3 and inf['reconocidas'] == 2 and inf['sin_reconocer'] == 1


# ============================================================
# El catalogo del builder son DATOS
# ============================================================
def test_el_catalogo_del_selector_no_trae_html():
    for _clave, titulo, tipos in qs.catalogo_para_selector():
        assert '<' not in titulo
        for tipo, etiqueta, icono, pide_cantidad, unidad in tipos:
            assert tipo in qs.CATALOGO
            assert '<' not in etiqueta and '<' not in icono
            assert isinstance(pide_cantidad, bool)


def test_todo_tipo_del_catalogo_se_formatea_e_iconifica():
    """Un tipo mal declarado saldria como texto vacio en la cotizacion de un
    cliente. Esto lo caza antes."""
    for tipo, meta in qs.CATALOGO.items():
        s = {'tipo': tipo}
        if not meta.get('sin_cantidad') and not meta.get('libre'):
            s['cantidad'] = 2
        if meta.get('libre'):
            s['texto'] = 'Concepto libre'
        texto = qs.formatear_servicio(s)
        assert texto and texto.strip(), f'{tipo} no produce texto'
        assert qs.resolver_icono(s), f'{tipo} no resuelve icono'
        assert qs.categoria_de(s) in dict((c, t) for c, t, _ in qs.CATEGORIAS), tipo
