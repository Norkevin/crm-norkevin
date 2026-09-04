"""
FACTURAS CON invoice_id REPETIDO  (4-sep-2026)

El importador de Studio Ninja derivaba el invoice_id de slug[:8]:

    'job_20270123_keller-zapote' -> 'JOB20270' -> 'INV-SN-JOB20270-1'

Los primeros 8 caracteres son iguales para todas las bodas del mismo ano,
asi que varios clientes terminaron compartiendo el mismo invoice_id. Toda
pantalla que resolvia por invoice_id caia en la PRIMERA fila -- por eso
cualquier job llevaba a la factura de la misma persona.

El codigo ya no depende de esa llave (resuelve por el id de la fila, que si
es unico), asi que el CRM se ve bien aunque los datos sigan repetidos. Esta
herramienta existe para ver cuanto queda repetido y decidir si vale la pena
renumerar.

SOLO LEE. No escribe, no migra, no toca montos ni pagos.

    python tools/facturas_ambiguas.py
    python tools/facturas_ambiguas.py --propuesta   # que id tendria cada una
"""
import argparse
import hashlib
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def _propuesto(job_id, cuota_grupo):
    """El id que le tocaria con la formula corregida. Deterministico: el
    hash del identificador completo del job, no de sus primeras letras."""
    slug = str(job_id or '').replace('boda-sn-', '')
    firma = hashlib.sha1(slug.encode('utf-8')).hexdigest()[:8].upper()
    return f'INV-SN-{firma}-{cuota_grupo}'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--propuesta', action='store_true',
                    help='mostrar que invoice_id le tocaria a cada boda')
    args = ap.parse_args()

    payments = [p for p in _leer('payments') if isinstance(p, dict)]
    jobs = {j.get('id'): j for j in _leer('jobs') if isinstance(j, dict)}
    clients = {c.get('id'): c for c in _leer('clients') if isinstance(c, dict)}

    if not payments:
        print('No hay data/payments.json (o esta vacio). Nada que revisar.')
        print('Corre esto en la maquina que tiene los datos reales.')
        return 0

    por_invoice = {}
    for p in payments:
        iid = p.get('invoice_id')
        if iid:
            por_invoice.setdefault(iid, []).append(p)

    ambiguos = {k: v for k, v in por_invoice.items()
                if len({r.get('job_id') for r in v}) > 1}

    print(f'{len(payments)} cuotas | {len(por_invoice)} invoice_id distintos')
    print(f'{len(ambiguos)} invoice_id apuntan a MAS DE UNA boda')
    print()

    if not ambiguos:
        print('Ningun invoice_id se repite entre bodas. Nada que renumerar.')
        return 0

    for iid, filas in sorted(ambiguos.items()):
        bodas = sorted({r.get('job_id') for r in filas})
        print(f'{iid}  ->  {len(bodas)} bodas')
        for job_id in bodas:
            del_job = [r for r in filas if r.get('job_id') == job_id]
            job = jobs.get(job_id) or {}
            cli = clients.get((del_job[0] or {}).get('client_id')) or {}
            nombre = (f"{cli.get('first_name','')} {cli.get('last_name','')}".strip()
                      or job.get('client_name') or '?')
            monto = sum(float(r.get('original_amount') or r.get('amount') or 0)
                        for r in del_job)
            linea = (f"    {nombre[:28]:28} {job.get('nombre','')[:26]:26} "
                     f"{len(del_job)} cuota(s)  Q{monto:,.2f}")
            if args.propuesta:
                linea += f"   -> {_propuesto(job_id, iid.rsplit('-', 1)[-1])}"
            print(linea)
        print()

    print('-' * 72)
    print('El CRM YA no usa esta llave para navegar: los enlaces van por el id')
    print('de la fila (unico), asi que las pantallas muestran la boda correcta')
    print('aunque estos ids sigan repetidos. Lo unico que queda repetido es el')
    print('numero de factura IMPRESO en el documento.')
    print()
    print('Renumerar es opcional y es decision tuya: cambia un dato que ya')
    print('salio en documentos enviados. Esta herramienta no lo hace.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
