"""
conversion_registry.py -- identidad unica y persistente de una conversion
lead -> job, garantizada por la base de datos.

POR QUE EXISTE
--------------
El incidente original (Camila Rios, julio 2026) fue exactamente esto: una
misma conversion logica produjo 4 jobs distintos, con 4 workflow_instances
distintos. El parche de julio agrego un guardia en Python
(`_find_job_for_lead()` antes de crear), que resuelve el caso comun --
doble click, llamadas secuenciales-- pero NO cierra la ventana de carrera:

    hilo A: busca job existente -> no hay
    hilo B: busca job existente -> no hay      <-- ambos pasaron el check
    hilo A: crea boda-AAA y guarda
    hilo B: crea boda-BBB y guarda             <-- dos jobs para un lead

Ningun chequeo "leer y despues decidir" en Python puede cerrar esa
ventana, porque entre el leer y el escribir no hay atomicidad. La unica
forma de que "solo uno gane" es que la decision la tome un recurso que
sepa serializar: una base de datos con un UNIQUE de verdad.

COMO FUNCIONA
-------------
Una conversion logica se identifica por una clave canonica:

    conversion_key = "<tenant_id>::<lead_id>"

Esa clave es PRIMARY KEY en SQLite. Dos hilos/procesos que intenten
reclamar la misma conversion producen exactamente un ganador:

    ganador  -> el INSERT entra. Hace la conversion completa y al terminar
                escribe el job_id resultante en la fila (finalize()).
    perdedor -> el INSERT choca con IntegrityError. Entonces RECONSULTA
                hasta que el ganador publique el job_id, y devuelve ESE
                job. Nunca crea un segundo job.

La eleccion de (tenant_id, lead_id) como identidad -- y no
(tenant, lead, quote) -- es deliberada: el invariante de negocio es "un
lead convertido tiene UN job". Aceptar una cotizacion distinta del mismo
lead no debe abrir un job nuevo, debe caer sobre el mismo.

POR QUE SQLite Y NO UN LOCK DE ARCHIVO
--------------------------------------
Un lock de archivo (O_EXCL) tambien seria atomico, pero deja el problema
de que nadie sabe QUIEN gano ni CUAL fue el job resultante; habria que
inventar un protocolo encima. SQLite ya da las dos cosas: exclusion mutua
y un lugar donde el ganador publica su resultado para que el perdedor lo
lea. Ademas es la misma tecnologia a la que migra el CRM (V5.2), asi que
esta tabla es el paso natural hacia `projects.origin_action_key`.

NO toca data/crm.db ni ninguna tabla de negocio: vive en su propio archivo
`conversion_registry.db` dentro del directorio de datos activo (en tests,
el tempdir aislado de conftest.py).
"""
import logging
import os
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)

_DB_FILENAME = 'conversion_registry.db'

# Cuanto espera un perdedor a que el ganador publique el job_id antes de
# rendirse. La conversion completa (cliente + job + workflow + cuotas +
# cuestionario) tarda milisegundos; 15 s es holgadisimo y solo se agota si
# el ganador murio a mitad de camino.
_ESPERA_MAX_SEGUNDOS = 15.0
_INTERVALO_SONDEO = 0.01

_init_lock = threading.Lock()
_inicializadas = set()


def _db_path():
    """Ruta del registro, siempre dentro del directorio de datos ACTIVO.

    Se resuelve en cada llamada (no se cachea) porque en tests
    CRM_DATA_DIR apunta a un tempdir distinto por sesion, y cachearlo
    haria que los tests escribieran en el directorio equivocado."""
    from .storage import store
    return os.path.join(store.data_dir, _DB_FILENAME)


def _conectar():
    path = _db_path()
    # timeout: si otro proceso tiene la DB bloqueada, esperar en vez de
    # fallar de inmediato. isolation_level=None -> autocommit, para que el
    # INSERT del reclamo sea visible a los demas apenas ocurre (si quedara
    # dentro de una transaccion abierta, los perdedores no lo verian y
    # podrian creer que ganaron).
    conn = sqlite3.connect(path, timeout=15.0, isolation_level=None)
    # busy_timeout ANTES que cualquier otra sentencia.
    #
    # BUG encontrado en la corrida 20260820_143142: este PRAGMA se ejecutaba
    # DESPUES de 'PRAGMA journal_mode=WAL'. Cambiar a WAL una base recien
    # creada necesita un lock exclusivo un instante; con 5 hilos abriendo la
    # base por PRIMERA vez a la vez, cuatro chocaban y -- sin busy_timeout
    # todavia configurado-- fallaban al instante con
    # OperationalError('database is locked') en vez de esperar su turno.
    # Se veia exactamente asi: iteracion 1 con 4 hilos caidos, iteraciones
    # 2-20 perfectas (la base ya existia y ya estaba en WAL).
    conn.execute('PRAGMA busy_timeout=15000')
    _asegurar_tabla(conn, path)
    return conn


def _asegurar_tabla(conn, path):
    """Inicializacion de UNA sola vez por archivo, serializada.

    journal_mode se fija aca dentro (y no en cada _conectar) porque es un
    ajuste PERSISTENTE de la base: basta con establecerlo la primera vez.
    Ejecutarlo en cada conexion era lo que generaba la contienda del primer
    acceso concurrente."""
    if path in _inicializadas:
        return
    with _init_lock:
        if path in _inicializadas:
            return
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS conversion_claims (
                conversion_key TEXT PRIMARY KEY,
                tenant_id      TEXT NOT NULL,
                lead_id        TEXT NOT NULL,
                job_id         TEXT,
                claimed_at     REAL NOT NULL,
                finalized_at   REAL
            )
        ''')
        _inicializadas.add(path)


def conversion_key(tenant_id, lead_id):
    """Identidad canonica de una conversion logica."""
    return f'{tenant_id or "SIN_TENANT"}::{lead_id}'


def claim(tenant_id, lead_id):
    """Intenta reclamar la conversion.

    Devuelve (rol, job_id):
      ('winner', None)        -> este llamador debe hacer la conversion y
                                 despues llamar a finalize().
      ('loser', '<job_id>')   -> otro ya la hizo (o la esta haciendo y ya
                                 termino); usar ESE job, no crear otro.
      ('timeout', None)       -> el ganador reclamo pero nunca publico el
                                 job_id (murio a mitad). El llamador debe
                                 seguir por el camino normal, que todavia
                                 tiene su guardia de aplicacion.
    """
    key = conversion_key(tenant_id, lead_id)
    ahora = time.time()
    conn = _conectar()
    try:
        try:
            conn.execute(
                'INSERT INTO conversion_claims '
                '(conversion_key, tenant_id, lead_id, job_id, claimed_at, finalized_at) '
                'VALUES (?, ?, ?, NULL, ?, NULL)',
                (key, tenant_id, lead_id, ahora),
            )
            return 'winner', None
        except sqlite3.IntegrityError:
            pass  # ya reclamada: somos perdedores, hay que esperar el resultado

        limite = time.time() + _ESPERA_MAX_SEGUNDOS
        while time.time() < limite:
            fila = conn.execute(
                'SELECT job_id FROM conversion_claims WHERE conversion_key = ?',
                (key,),
            ).fetchone()
            if fila and fila[0]:
                return 'loser', fila[0]
            time.sleep(_INTERVALO_SONDEO)

        logger.warning(
            'conversion_registry: la conversion %s fue reclamada pero nunca se '
            'publico un job_id en %.0fs. Se continua por el camino normal.',
            key, _ESPERA_MAX_SEGUNDOS,
        )
        return 'timeout', None
    finally:
        conn.close()


def finalize(tenant_id, lead_id, job_id):
    """Publica el job resultante para que los perdedores lo encuentren."""
    key = conversion_key(tenant_id, lead_id)
    conn = _conectar()
    try:
        conn.execute(
            'UPDATE conversion_claims SET job_id = ?, finalized_at = ? '
            'WHERE conversion_key = ?',
            (job_id, time.time(), key),
        )
    finally:
        conn.close()


def release(tenant_id, lead_id):
    """Libera un reclamo que NO llego a producir un job.

    Sin esto, una conversion que falla a mitad dejaria la clave reclamada
    para siempre y ningun reintento posterior podria volver a intentarlo:
    todos entrarian como perdedores esperando un job_id que nunca va a
    llegar. Solo borra si el job_id sigue en NULL -- jamas puede borrar el
    reclamo de una conversion que si termino."""
    key = conversion_key(tenant_id, lead_id)
    conn = _conectar()
    try:
        conn.execute(
            'DELETE FROM conversion_claims WHERE conversion_key = ? AND job_id IS NULL',
            (key,),
        )
    finally:
        conn.close()


def lookup(tenant_id, lead_id):
    """job_id ya registrado para esta conversion, o None. Solo lectura."""
    key = conversion_key(tenant_id, lead_id)
    conn = _conectar()
    try:
        fila = conn.execute(
            'SELECT job_id FROM conversion_claims WHERE conversion_key = ?',
            (key,),
        ).fetchone()
        return fila[0] if fila else None
    finally:
        conn.close()
