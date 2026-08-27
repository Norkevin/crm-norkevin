#!/usr/bin/env python3
"""
audit_location_contamination.py -- clasifica, NO corrige.

Punto 4 de la lista de uso diario: "audita por que algunos jobs tienen
email/telefono u otra informacion metida en location".

CAUSA RAIZ (ya corregida en app.py): habia un bucle de contaminacion en
los dos sentidos entre el venue del evento y la direccion de facturacion
del cliente:

    _ensure_client_for_lead()  ->  client['address'] = lead['locacion']
    api_lead_create()          ->  lead['locacion'] = client['address']

Mas un formulario en client_detail.html etiquetado "Location" que en
realidad escribia en `locacion` mientras mostraba la direccion de
facturacion. Entre los tres, lo que el usuario escribia en un campo
terminaba apareciendo en el otro, y cualquier email/telefono/nota que
alguien metiera en "Dirección facturación" acababa dentro de location.

Este script NO reescribe nada. Solo recorre los datos y clasifica cada
valor de location/locacion:

    LIMPIO                -- parece un lugar
    CONTIENE_EMAIL        -- hay una direccion de correo adentro
    CONTIENE_TELEFONO     -- hay un telefono adentro
    SOSPECHOSO_NO_LUGAR   -- no parece un lugar (URL, solo numeros, etc)
    VACIO

Kevin fue explicito: "no borres informacion legacy ambigua; clasificala
si no se puede reubicar con certeza". Por eso la salida es un reporte,
no una migracion: un venue como "Casa del Mundo, Atitlan - contacto
5555-1234" tiene informacion util MEZCLADA, y separarla automaticamente
es adivinar. Se reporta para que la persona decida.

Uso:
    python tools/audit_location_contamination.py
    python tools/audit_location_contamination.py --source artifacts/fixtures/legacy_20260712
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RE_EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+')
# Telefono guatemalteco y formatos comunes: 5555-1234, +502 5555 1234,
# 55551234. Se exige >=7 digitos para no marcar un numero de casa.
RE_TELEFONO = re.compile(r'(?:\+?\d[\d\s().-]{6,}\d)')
RE_URL = re.compile(r'https?://|www\.', re.I)


def clasificar(valor):
    """Devuelve (categoria, detalles). No modifica el valor."""
    if valor is None or not str(valor).strip():
        return 'VACIO', {}

    texto = str(valor).strip()
    detalles = {}

    emails = RE_EMAIL.findall(texto)
    if emails:
        detalles['emails'] = emails

    # Se quitan los emails antes de buscar telefonos: un email con
    # numeros no debe contarse como telefono.
    sin_emails = RE_EMAIL.sub(' ', texto)
    telefonos = [t.strip() for t in RE_TELEFONO.findall(sin_emails)
                 if len(re.sub(r'\D', '', t)) >= 7]
    if telefonos:
        detalles['telefonos'] = telefonos

    if emails:
        return 'CONTIENE_EMAIL', detalles
    if telefonos:
        return 'CONTIENE_TELEFONO', detalles
    if RE_URL.search(texto):
        detalles['motivo'] = 'parece una URL, no un lugar'
        return 'SOSPECHOSO_NO_LUGAR', detalles
    if re.fullmatch(r'[\d\s.-]+', texto):
        detalles['motivo'] = 'solo digitos/separadores, no parece un lugar'
        return 'SOSPECHOSO_NO_LUGAR', detalles

    return 'LIMPIO', detalles


def _cargar(source_dir, nombre):
    path = os.path.join(source_dir, f'{nombre}.json')
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8-sig') as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def auditar(source_dir):
    hallazgos = []
    resumen = {}

    for tabla, campo in (('jobs', 'location'), ('leads', 'locacion'),
                         ('clients', 'address')):
        for registro in _cargar(source_dir, tabla):
            categoria, detalles = clasificar(registro.get(campo))
            clave = f'{tabla}.{campo}'
            resumen.setdefault(clave, {})
            resumen[clave][categoria] = resumen[clave].get(categoria, 0) + 1
            if categoria not in ('LIMPIO', 'VACIO'):
                hallazgos.append({
                    'tabla': tabla,
                    'campo': campo,
                    'id': registro.get('id'),
                    'tenant_id': registro.get('tenant_id'),
                    'valor_actual': registro.get(campo),
                    'categoria': categoria,
                    'detalles': detalles,
                    'accion': 'PRESERVAR_Y_REVISAR_A_MANO',
                })

    return {
        'source': source_dir,
        'resumen_por_campo': resumen,
        'total_contaminados': len(hallazgos),
        'nota': ('Ningun valor fue modificado. La causa raiz (bucle '
                 'venue <-> address) ya esta corregida en app.py, asi que '
                 'esto no vuelve a crecer; lo que queda es historico y se '
                 'decide a mano.'),
        'hallazgos': hallazgos,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', default=os.path.join(ROOT, 'data'))
    parser.add_argument('--out', default=os.path.join(ROOT, 'artifacts',
                                                      'location_contamination_report.json'))
    args = parser.parse_args()

    reporte = auditar(args.source)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(reporte, fh, indent=2, ensure_ascii=False)

    print(json.dumps({k: v for k, v in reporte.items() if k != 'hallazgos'},
                     indent=2, ensure_ascii=False))
    print(f'\nReporte completo: {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
