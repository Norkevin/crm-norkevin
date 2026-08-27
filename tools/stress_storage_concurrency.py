#!/usr/bin/env python3
"""
stress_storage_concurrency.py -- prueba agresiva y AISLADA del locking.

50 iteraciones x 5 workers sobre un store TEMPORAL propio. No usa Flask,
no toca data/ real, no forma parte de la suite permanente: existe para
responder una sola pregunta -- "el arreglo de storage funciona de verdad o
tuvimos suerte 20 veces seguidas?".

Ejercita exactamente las carreras que rompieron en la corrida
20260820_134955 (46 FileNotFoundError + 25 PermissionError):

  - N escritores sobre el MISMO archivo (upsert concurrente)
  - un lector permanente mientras se hacen replace
  - backup concurrente con escritura
  - save_dict/get_dict concurrentes (la ruta de workflow_instances)

Criterio: 0 errores en las 50 iteraciones, 0 actualizaciones perdidas y
0 backups corruptos. Cualquier error intermitente es motivo suficiente
para NO seguir al gate.

Uso:
    python tools/stress_storage_concurrency.py
    python tools/stress_storage_concurrency.py --iteraciones 50 --workers 5
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _una_iteracion(numero, workers):
    from src.storage import JsonStore

    tmpdir = tempfile.mkdtemp(prefix=f'stress_storage_{numero}_')
    try:
        store = JsonStore(tmpdir)
        errores = []
        lock = threading.Lock()
        listos = threading.Barrier(workers)

        # Semilla, para que exista archivo previo y el backup tenga que
        # copiar algo real en cada escritura.
        store.upsert('jobs', {'id': 'seed', 'nombre': 'seed'})

        def escritor(n):
            try:
                listos.wait(timeout=15)
                for i in range(20):
                    store.upsert('jobs', {'id': f'job-{n}-{i}', 'nombre': f'J{n}{i}'})
                    d = store.get_dict('workflow_instances')
                    d[f'wi-{n}-{i}'] = {'subject_id': f'job-{n}-{i}'}
                    store.save_dict('workflow_instances', d)
            except Exception as exc:
                with lock:
                    errores.append(f'escritor{n}: {exc!r}')

        parar = threading.Event()
        lecturas = {'n': 0}

        def lector():
            while not parar.is_set():
                try:
                    store._read_raw('jobs')
                    store.get_dict('workflow_instances')
                    lecturas['n'] += 1
                except Exception as exc:
                    with lock:
                        errores.append(f'lector: {exc!r}')

        hilo_lector = threading.Thread(target=lector, daemon=True)
        hilo_lector.start()
        hilos = [threading.Thread(target=escritor, args=(n,)) for n in range(workers)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        parar.set()
        hilo_lector.join(timeout=5)

        # Ninguna actualizacion perdida: seed + workers*20
        registros = store._read_raw('jobs')
        esperados = 1 + workers * 20
        if len(registros) != esperados:
            errores.append(
                f'actualizaciones perdidas: {len(registros)} registros, se esperaban {esperados}')

        # Ningun backup corrupto
        backups_root = os.path.join(tmpdir, 'backups')
        for base, _d, files in os.walk(backups_root):
            for f in files:
                if not f.endswith('.json'):
                    continue
                try:
                    with open(os.path.join(base, f), 'r', encoding='utf-8') as fh:
                        json.load(fh)
                except Exception as exc:
                    errores.append(f'backup corrupto {f}: {exc!r}')

        return {
            'iteracion': numero,
            'errores': errores,
            'registros': len(registros),
            'lecturas': lecturas['n'],
            'pass': not errores,
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--iteraciones', type=int, default=50)
    parser.add_argument('--workers', type=int, default=5)
    args = parser.parse_args()

    resultados = []
    for n in range(1, args.iteraciones + 1):
        resultados.append(_una_iteracion(n, args.workers))

    fallidas = [r for r in resultados if not r['pass']]
    total_errores = sum(len(r['errores']) for r in resultados)

    resumen = {
        'iteraciones': args.iteraciones,
        'workers': args.workers,
        'iteraciones_pass': len(resultados) - len(fallidas),
        'iteraciones_fail': len(fallidas),
        'total_errores': total_errores,
        'total_lecturas_concurrentes': sum(r['lecturas'] for r in resultados),
        'pass': not fallidas,
    }
    if fallidas:
        resumen['primeras_fallas'] = fallidas[:5]

    print(json.dumps(resumen, indent=2, ensure_ascii=False))

    destino = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'artifacts', 'storage_stress_evidence.json')
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    with open(destino, 'w', encoding='utf-8') as fh:
        json.dump(resumen, fh, indent=2, ensure_ascii=False)

    # A STDOUT, no a stderr. Cuando PowerShell ejecuta un comando nativo que
    # escribe en stderr, envuelve esa salida en un ErrorRecord
    # (NativeCommandError) y la fase quedaba marcada FAIL aunque el proceso
    # terminara con exit code 0 -- paso exactamente eso en la corrida
    # 20260820_143142: el stress reporto "VEREDICTO: PASS", 50/50 iteraciones,
    # 0 errores, y la fase storage_locking igual salio en FAIL.
    print(f"\nVEREDICTO: {'PASS' if resumen['pass'] else 'FAIL'}")
    return 0 if resumen['pass'] else 1


if __name__ == '__main__':
    sys.exit(main())
