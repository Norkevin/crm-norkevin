"""Verificacion del locking por archivo de JsonStore (agosto 2026).

Nace de un fallo real: el stress de conversion concurrente
(corrida 20260820_134955) produjo 71 errores en 40 iteraciones --
46 FileNotFoundError y 25 PermissionError-- porque JsonStore no
serializaba a los escritores. Ningun dato se duplico (la unicidad de la
conversion la garantiza src/conversion_registry.py), pero los hilos se
caian, y un hilo caido enmascaraba el escenario que se queria probar.

Estos tests son pequenos y directos: no prueban el CRM, prueban la capa
de almacenamiento. Cubren exactamente las cinco propiedades pedidas:

  1. dos writers sobre el mismo archivo no se pierden actualizaciones;
  2. un reader puede convivir con un writer que hace replace;
  3. el backup ocurre dentro de la misma exclusion que la escritura;
  4. el lock es reentrante para el mismo hilo (RLock, no Lock);
  5. dos archivos distintos NO se bloquean entre si (granularidad por
     path -- el CRM no se serializa globalmente).
"""
import json
import os
import threading

import pytest

from src.storage import JsonStore, _lock_for_path


@pytest.fixture()
def store_tmp(tmp_path):
    """Store propio en un directorio temporal: estos tests no comparten
    estado con el resto de la suite."""
    return JsonStore(str(tmp_path))


def test_dos_writers_al_mismo_archivo_no_pierden_actualizaciones(store_tmp):
    """Sin lock, dos hilos leen la misma lista, cada uno agrega su
    registro y el segundo _save() pisa al primero (lost update)."""
    errores = []

    def writer(n):
        try:
            for i in range(60):
                store_tmp.upsert('jobs', {'id': f'job-{n}-{i}', 'nombre': f'J{n}{i}'})
        except Exception as exc:
            errores.append(repr(exc))

    hilos = [threading.Thread(target=writer, args=(n,)) for n in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert not errores, f'writers con error: {errores}'
    registros = store_tmp._read_raw('jobs')
    assert len(registros) == 120, (
        f'se esperaban 120 registros y hay {len(registros)} -- se perdieron '
        'actualizaciones entre hilos')
    ids = {r['id'] for r in registros}
    assert len(ids) == 120


def test_lector_concurrente_con_replace_no_falla(store_tmp):
    """Un lector no puede reventar porque otro hilo este sustituyendo el
    archivo. Este era el origen de los PermissionError/FileNotFoundError
    en Windows."""
    store_tmp.upsert('jobs', {'id': 'job-inicial', 'nombre': 'X'})

    errores_lectura = []
    lecturas = []
    parar = threading.Event()

    def lector():
        while not parar.is_set():
            try:
                lecturas.append(len(store_tmp._read_raw('jobs')))
            except Exception as exc:
                errores_lectura.append(repr(exc))

    def escritor():
        for i in range(80):
            store_tmp.upsert('jobs', {'id': f'job-w-{i}', 'nombre': 'w'})

    lr = threading.Thread(target=lector)
    wr = threading.Thread(target=escritor)
    lr.start()
    wr.start()
    wr.join()
    parar.set()
    lr.join()

    assert not errores_lectura, f'el lector fallo durante un replace: {errores_lectura[:3]}'
    assert lecturas, 'el lector no llego a leer nada'
    # Y ninguna lectura puede haber devuelto JSON parcial: si parseo, es valido.


def test_backup_ocurre_dentro_de_la_exclusion_del_write(store_tmp):
    """El backup debe corresponder a un estado anterior VALIDO. Con
    escritores concurrentes, ningun backup puede quedar corrupto ni
    desaparecer a medias."""
    for i in range(5):
        store_tmp.upsert('jobs', {'id': f'seed-{i}', 'nombre': 'seed'})

    errores = []

    def writer(n):
        try:
            for i in range(30):
                store_tmp.upsert('jobs', {'id': f'job-{n}-{i}', 'nombre': 'x'})
        except Exception as exc:
            errores.append(repr(exc))

    hilos = [threading.Thread(target=writer, args=(n,)) for n in range(3)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert not errores, f'errores durante escrituras concurrentes: {errores[:3]}'

    backups_root = os.path.join(store_tmp.data_dir, 'backups')
    archivos = []
    for base, _d, files in os.walk(backups_root):
        archivos.extend(os.path.join(base, f) for f in files if f.endswith('.json'))
    assert archivos, 'no se genero ningun backup'
    for path in archivos:
        with open(path, 'r', encoding='utf-8') as fh:
            json.load(fh)  # si esta corrupto, esto levanta y el test falla


def test_el_lock_es_reentrante_para_el_mismo_hilo(store_tmp):
    """Debe ser RLock: upsert() toma el lock y adentro llama a _read_raw()
    y _save(), que vuelven a tomarlo. Con un Lock normal seria deadlock."""
    lock = _lock_for_path(store_tmp._path('clients'))
    with lock:
        with lock:  # reentrada explicita
            store_tmp.upsert('clients', {'id': 'c1', 'first_name': 'A'})
    assert store_tmp._read_raw('clients')[0]['id'] == 'c1'


def test_archivos_distintos_no_se_bloquean_entre_si(store_tmp):
    """Granularidad por path: el CRM no debe serializarse globalmente.
    Escribir en 'payments' no puede quedar esperando a 'jobs'."""
    lock_jobs = _lock_for_path(store_tmp._path('jobs'))
    lock_payments = _lock_for_path(store_tmp._path('payments'))
    assert lock_jobs is not lock_payments, 'dos tablas comparten lock -- cuello de botella'

    adquirido = []

    def tomar_payments():
        conseguido = lock_payments.acquire(timeout=3)
        adquirido.append(conseguido)
        if conseguido:
            lock_payments.release()

    with lock_jobs:  # mantenemos jobs tomado
        h = threading.Thread(target=tomar_payments)
        h.start()
        h.join()

    assert adquirido == [True], (
        'un hilo no pudo escribir en payments mientras otro tenia jobs tomado')


def test_dos_stores_al_mismo_directorio_comparten_lock(tmp_path):
    """Un lock por INSTANCIA no protegeria nada: dos JsonStore apuntando al
    mismo archivo tienen que compartir el mismo lock."""
    a = JsonStore(str(tmp_path))
    b = JsonStore(str(tmp_path))
    assert _lock_for_path(a._path('jobs')) is _lock_for_path(b._path('jobs'))


def test_save_dict_concurrente_no_falla(store_tmp):
    """save_dict/get_dict (los usa el motor de workflows para
    workflow_instances) tenian el mismo defecto: temporal compartido."""
    errores = []

    def writer(n):
        try:
            for i in range(25):
                d = store_tmp.get_dict('workflow_instances')
                d[f'wi-{n}-{i}'] = {'x': 1}
                store_tmp.save_dict('workflow_instances', d)
        except Exception as exc:
            errores.append(repr(exc))

    hilos = [threading.Thread(target=writer, args=(n,)) for n in range(3)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert not errores, f'save_dict concurrente fallo: {errores[:3]}'
    assert isinstance(store_tmp.get_dict('workflow_instances'), dict)
