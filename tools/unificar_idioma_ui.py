"""Unifica el idioma de la interfaz: el CRM es en espanol.

Kevin trabaja en espanol y le muestra estas pantallas a sus clientes. La
interfaz habia quedado mezclada: la pestana decia "Facturas" y la tarjeta
de adentro "Invoices"; la tabla de Equipo tenia "First Name / Last Name /
Date Created"; el dashboard, "Job / Client / Date / Status / Progress".

Solo se toca TEXTO VISIBLE en posiciones seguras (encabezados de tabla,
titulos de tarjeta, etiquetas, botones sin marcado adentro, data-label y
opciones de select). Nunca se tocan `name=`, `id=`, `value=`, clases,
identificadores de JavaScript ni claves de datos: eso romperia formularios
y peticiones.

Uso:
    python tools/unificar_idioma_ui.py            # dry-run, solo reporta
    python tools/unificar_idioma_ui.py --aplicar  # escribe los cambios
"""
import glob
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Solo terminos inequivocos. Se dejan fuera a proposito los que en
# Guatemala se usan igual en ingles ("Email", "Total", "Lead", "Leads")
# porque cambiarlos seria ruido, no consistencia.
TRADUCCION = {
    'Invoices': 'Facturas',
    'Invoice': 'Factura',
    'Files': 'Archivos',
    'File': 'Archivo',
    'Notes': 'Notas',
    'Note': 'Nota',
    'Questionnaires': 'Cuestionarios',
    'Questionnaire': 'Cuestionario',
    'Quotes': 'Cotizaciones',
    'Quote': 'Cotizacion',
    'Contracts': 'Contratos',
    'Contract': 'Contrato',
    'Payments': 'Pagos',
    'Payment': 'Pago',
    'Clients': 'Clientes',
    'Client': 'Cliente',
    'Jobs': 'Trabajos',
    'Job': 'Trabajo',
    'Status': 'Estado',
    'Progress': 'Avance',
    'Amount': 'Monto',
    'Due Date': 'Vence',
    'Date Created': 'Creado',
    'Created': 'Creado',
    'First Name': 'Nombre',
    'Last Name': 'Apellido',
    'Phone': 'Telefono',
    'Company': 'Empresa',
    'Type': 'Tipo',
    'Value': 'Valor',
    'Actions': 'Acciones',
    'Action': 'Accion',
    'Save': 'Guardar',
    'Cancel': 'Cancelar',
    'Edit': 'Editar',
    'Delete': 'Eliminar',
    'Send': 'Enviar',
    'Search': 'Buscar',
    'Settings': 'Configuracion',
    'Overview': 'Resumen',
}

# Posiciones donde el texto es visible y cambiarlo no rompe nada.
PATRONES = [
    # <th ...>Texto</th>, <label>, <h1..h4>, <option>
    (re.compile(r'(<(?:th|label|h1|h2|h3|h4|option)\b[^>]*>)([^<>{}]+?)(</(?:th|label|h1|h2|h3|h4|option)>)'), 2),
    # <div class="sn-card-title">Texto</div>
    (re.compile(r'(sn-card-title"[^>]*>)([^<>{}]+?)(<)'), 2),
    # data-label="Texto"
    (re.compile(r'(data-label=")([^"{}]+?)(")'), 2),
    # <button ...>Texto</button>  (solo si adentro no hay marcado)
    (re.compile(r'(<button\b[^>]*>)([^<>{}]+?)(</button>)'), 2),
    # <div class="dashboard-section-label">Texto</div> y similares
    (re.compile(r'(class="(?:dashboard-section-label|label-eyebrow|doc-eyebrow|sn-section-title)"[^>]*>)([^<>{}]+?)(<)'), 2),
]


def traducir_texto(txt):
    limpio = txt.strip()
    if not limpio or limpio not in TRADUCCION:
        return None
    nuevo = TRADUCCION[limpio]
    return txt.replace(limpio, nuevo)


def procesar(ruta, aplicar):
    raw = open(ruta, 'rb').read().decode('utf-8')
    original = raw
    cambios = []

    for patron, grupo in PATRONES:
        def repl(m):
            partes = list(m.groups())
            nuevo = traducir_texto(partes[grupo - 1])
            if nuevo is None:
                return m.group(0)
            cambios.append((partes[grupo - 1].strip(), nuevo.strip()))
            partes[grupo - 1] = nuevo
            return ''.join(partes)
        raw = patron.sub(repl, raw)

    if cambios and aplicar:
        open(ruta, 'wb').write(raw.encode('utf-8'))
    return cambios, raw != original


def main():
    aplicar = '--aplicar' in sys.argv
    total = 0
    tocadas = 0
    for ruta in sorted(glob.glob(os.path.join(RAIZ, 'templates', '*.html'))):
        cambios, hubo = procesar(ruta, aplicar)
        if not cambios:
            continue
        tocadas += 1
        total += len(cambios)
        print(f'\n{os.path.basename(ruta)}  ({len(cambios)})')
        for viejo, nuevo in sorted(set(cambios)):
            print(f'    {viejo:<18} -> {nuevo}')

    print('\n' + '=' * 60)
    print(f'{total} textos en {tocadas} plantillas')
    print('APLICADO' if aplicar else 'DRY-RUN (usa --aplicar para escribir)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
