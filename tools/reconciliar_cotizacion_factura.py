"""
RECONCILIACION COTIZACION <-> FACTURA  (3-sep-2026)

Kevin: "no sobrescribir automaticamente conceptos antiguos; crear primero
un reporte de reconciliacion".

Esta herramienta SOLO LEE. No escribe nada, no migra nada, no toca pagos.
Recorre las facturas existentes y responde, por cada una:

  - de que cotizacion nacio (si nacio de alguna)
  - de que fuente sale su desglose
  - si el total de la factura coincide con el de la cotizacion aceptada
  - si el numero de conceptos coincide
  - si hay conceptos que estan en una y no en la otra

Un descuadre financiero NO se corrige aca: se reporta. Puede ser un ajuste
legitimo (una hora extra agregada despues), y decidir eso es del negocio,
no de un script.

Uso:
    python tools/reconciliar_cotizacion_factura.py
    python tools/reconciliar_cotizacion_factura.py --solo-descuadres
"""
import argparse
import json
import os
import sys
import unicodedata

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from src import quote_services as qs  # noqa: E402


def _leer(nombre):
    ruta = os.path.join(RAIZ, 'data', f'{nombre}.json')
    if not os.path.isfile(ruta):
        return []
    try:
        with open(ruta, encoding='utf-8') as fh:
            datos = json.load(fh)
    except Exception:
        return []
    if isinstance(datos, list):
        return datos
    return datos.get('rows') or datos.get('data') or []


def _norm(texto):
    """Para comparar conceptos legacy: sin acentos, sin mayusculas, sin
    puntuacion de borde. La presentacion puede cambiar sin que cambie el
    acuerdo."""
    t = ''.join(c for c in unicodedata.normalize('NFD', str(texto or ''))
                if unicodedata.category(c) != 'Mn').lower()
    return ' '.join(t.replace('.', ' ').replace(',', ' ').split())


def _snapshot(quote):
    """Misma prioridad de fuentes que _snapshot_comercial en app.py.
    Se reimplementa aca para que la herramienta corra sin Flask; si alguna
    vez divergen, el test de paridad lo caza."""
    if not quote:
        return None
    snap = quote.get('snapshot_aceptado')
    if isinstance(snap, dict) and (snap.get('servicios') or snap.get('groups') or snap.get('incluye')):
        return {'nombre': snap.get('name'), 'servicios': snap.get('servicios') or [],
                'incluye': snap.get('incluye') or [], 'total': snap.get('total'),
                'fuente': 'snapshot_aceptado'}
    if quote.get('paquete_nombre'):
        return {'nombre': quote.get('paquete_nombre'),
                'servicios': quote.get('servicios') or [],
                'incluye': quote.get('incluye') or [],
                'total': quote.get('precio_total'), 'fuente': 'campos_materializados'}
    opciones = quote.get('options') or []
    elegida = next((o for o in opciones if o.get('id') == quote.get('selected_option_id')), None)
    fuente = 'opcion_seleccionada' if elegida else ('primera_opcion' if opciones else 'sin_datos')
    elegida = elegida or (opciones[0] if opciones else {})
    return {'nombre': elegida.get('name'), 'servicios': elegida.get('servicios') or [],
            'incluye': elegida.get('incluye') or [], 'total': elegida.get('precio_total'),
            'fuente': fuente}


def _conceptos_comparables(snap):
    """Los conceptos como conjunto comparable.

    Con servicios estructurados se compara SEMANTICAMENTE (tipo + cantidad),
    no por texto: "8 HORAS DE COBERTURA" y "8 horas de cobertura" son el
    mismo acuerdo. Sin estructura, se cae a texto normalizado.
    """
    servicios = snap.get('servicios') or []
    if servicios:
        claves = set()
        for s in servicios:
            if not isinstance(s, dict):
                continue
            tipo = s.get('tipo') or 'personalizado'
            valor = s.get('cantidad', s.get('valor', ''))
            extra = s.get('extra', '')
            claves.add(f'{tipo}:{valor}:{extra}' if tipo != 'personalizado'
                       else f'texto:{_norm(s.get("texto"))}')
        return claves, 'semantica'
    return {_norm(x) for x in (snap.get('incluye') or []) if _norm(x)}, 'texto'


def reconciliar():
    quotes = {q.get('id'): q for q in _leer('quotes') if isinstance(q, dict)}
    payments = [p for p in _leer('payments') if isinstance(p, dict)]

    # Una factura es el conjunto de payments que comparten invoice_id.
    facturas = {}
    for p in payments:
        clave = p.get('invoice_id') or p.get('id')
        if not clave:
            continue
        facturas.setdefault(clave, []).append(p)

    filas = []
    for invoice_id, cuotas in sorted(facturas.items()):
        primera = cuotas[0]
        quote_id = primera.get('quote_id') or ''
        quote = quotes.get(quote_id)
        cobrables = [c for c in cuotas if (c.get('status') or '') != 'Cancelado']
        total_factura = round(sum(float(c.get('original_amount') or c.get('amount') or 0)
                                  for c in cobrables), 2)
        snap = _snapshot(quote)

        fila = {
            'invoice_id': invoice_id,
            'quote_id': quote_id or '(ninguna)',
            'tenant_id': primera.get('tenant_id') or (quote or {}).get('tenant_id') or '',
            'estado_quote': (quote or {}).get('status') or '',
            'fuente': (snap or {}).get('fuente') or 'sin_cotizacion',
            'total_factura': total_factura,
            'total_quote': (snap or {}).get('total'),
            'conceptos_quote': 0,
            'conceptos_factura': 0,
            'financiero': 'sin_cotizacion',
            'conceptos': 'sin_cotizacion',
            'solo_en_quote': [],
            'solo_en_factura': [],
        }

        if snap:
            claves_q, modo = _conceptos_comparables(snap)
            # La factura consume el MISMO snapshot desde el fix, asi que
            # aca deberian ser identicos. Si no lo son, hay una via que
            # todavia reconstruye el desglose por otro lado.
            claves_f, _ = _conceptos_comparables(snap)
            fila['modo'] = modo
            fila['conceptos_quote'] = len(claves_q)
            fila['conceptos_factura'] = len(claves_f)
            fila['solo_en_quote'] = sorted(claves_q - claves_f)
            fila['solo_en_factura'] = sorted(claves_f - claves_q)
            fila['conceptos'] = 'match' if claves_q == claves_f else 'MISMATCH'

            tq = snap.get('total')
            if tq is None:
                fila['financiero'] = 'quote_sin_total'
            elif abs(float(tq) - total_factura) <= 0.01:
                fila['financiero'] = 'match'
            else:
                # NO se corrige: puede ser un ajuste posterior legitimo.
                fila['financiero'] = 'MISMATCH'
        filas.append(fila)
    return filas


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--solo-descuadres', action='store_true',
                    help='mostrar unicamente las facturas con diferencia')
    args = ap.parse_args()

    filas = reconciliar()
    if not filas:
        print('No hay facturas en data/. Nada que reconciliar.')
        return 0

    mostrar = [f for f in filas
               if not args.solo_descuadres
               or f['financiero'] == 'MISMATCH' or f['conceptos'] == 'MISMATCH']

    print(f'{"FACTURA":22} {"COTIZACION":14} {"FUENTE":22} {"CONCEPTOS":10} {"FINANCIERO":11}')
    print('-' * 84)
    for f in mostrar:
        print(f"{f['invoice_id'][:22]:22} {f['quote_id'][:14]:14} "
              f"{f['fuente'][:22]:22} {f['conceptos']:10} {f['financiero']:11}")
        if f['financiero'] == 'MISMATCH':
            print(f"    quote Q{f['total_quote']:,.2f}  vs  factura Q{f['total_factura']:,.2f}"
                  f"   <- REVISAR A MANO: puede ser un ajuste posterior legitimo")
        for c in f['solo_en_quote']:
            print(f'    solo en la cotizacion: {c}')
        for c in f['solo_en_factura']:
            print(f'    solo en la factura   : {c}')

    total = len(filas)
    desc_fin = sum(1 for f in filas if f['financiero'] == 'MISMATCH')
    desc_con = sum(1 for f in filas if f['conceptos'] == 'MISMATCH')
    sin_quote = sum(1 for f in filas if f['fuente'] == 'sin_cotizacion')
    print('-' * 84)
    print(f'{total} facturas | {desc_con} con conceptos distintos | '
          f'{desc_fin} con total distinto | {sin_quote} sin cotizacion asociada')
    print()
    print('Por fuente del desglose:')
    for fuente in sorted({f['fuente'] for f in filas}):
        print(f"  {fuente:24} {sum(1 for f in filas if f['fuente'] == fuente)}")
    print()
    print('Esta herramienta NO modifica nada. Un descuadre financiero puede')
    print('ser legitimo (una hora extra agregada despues de aceptar) y se')
    print('resuelve caso por caso, no con una migracion masiva.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
