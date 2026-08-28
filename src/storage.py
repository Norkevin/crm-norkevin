"""
storage.py - Capa de persistencia local basada en archivos JSON.

===========================================================================
ANTES DE TOCAR NADA: los ids de empresa NO dicen de quien son
===========================================================================

    tenant-norkevin              =  ASTRAL WEDDINGS
    tenant-norkevin-photography  =  Norkevin Photography
    tenant-ramiro-cruz           =  Ramiro Cruz Photo

Si, `tenant-norkevin` es la cuenta de ASTRAL. Es un id heredado de cuando el
proyecto era solo de Norkevin y Astral fue la primera cuenta creada. Por eso
`google_token_tenant-norkevin.json` contiene astralweddingsgt@gmail.com y
ESO ES CORRECTO.

Regla, sin excepciones: **nunca deducir de que empresa es algo leyendo el
string del id.** Nada de `if 'norkevin' in tenant_id`, ni startswith, ni
comparar nombres. La empresa se resuelve por el registro en `tenants`.

Quien "arregle" ese nombre asumiendo lo contrario va a mover la credencial
de Astral a la empresa equivocada y a reproducir el incidente del 16 de
agosto de 2026 -- correos firmados como Astral a clientes de Norkevin,
recordatorios de cobro incluidos.

Fijado en tests/test_credential_isolation.py.

===========================================================================
Aislamiento
===========================================================================

En vez de que cada una de las ~250 rutas de app.py se acuerde de filtrar por
tenant_id, el filtrado vive aca, en el unico choke point por el que pasan
todas las lecturas y escrituras. `app.py` configura los tres ganchos una
sola vez al arrancar:

    store.tenant_resolver        -> de que empresa es esta peticion
    store.request_context_probe  -> estamos dentro de una peticion web?
    store.admin_context_probe    -> es una ruta administrativa autorizada?

Este modulo NO importa Flask, para poder seguir siendo usable fuera de un
request (scripts de migracion, tests, el hilo de recordatorios).

La regla es distinta segun donde se este, y esa distincion es el arreglo del
incidente:

  DENTRO de una peticion web y sin empresa activa -> NO SE VE NADA.
      list() devuelve [] y lo registra, list_strict() levanta, upsert()
      levanta. Antes devolvia todo, y por ahi salieron los correos.

  FUERA de una peticion (script, test, hilo de fondo) -> sin filtro.
      Ahi no hay usuario a quien aislar; el hilo de recordatorios trabaja
      con las tres cuentas y lleva el tenant_id del registro en la mano.

Cruzar empresas a proposito se hace SOLO con list_privileged(), que exige un
motivo escrito, lo deja en el log, y para `scope='all_tenants'` ademas exige
estar fuera de una peticion o dentro de una ruta administrativa autorizada.
"""
import copy
import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

# Directorio de datos
_DATA_DIR_OVERRIDE = os.environ.get('CRM_DATA_DIR')
if _DATA_DIR_OVERRIDE:
    DATA_DIR = _DATA_DIR_OVERRIDE
else:
    # storage.py esta en crm_norkevin/src/storage.py
    # data/ esta en crm_norkevin/data/
    _storage_dir = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(os.path.dirname(_storage_dir), 'data')

# Tablas donde cada registro pertenece a una sola cuenta. El resto
# (tenants, team, workflow_templates/instances/history, settings) se
# maneja aparte -- team y workflow_* porque su aislamiento real depende de
# un registro padre (job/lead) que ya paso por una tabla de esta lista, y
# settings porque usa get_tenant_dict/save_tenant_dict en vez de list().
TENANT_SCOPED_TABLES = {
    'leads', 'clients', 'jobs', 'quotes', 'payments', 'contracts',
    'questionnaires', 'email_templates', 'packages', 'calendar',
    'files', 'mail_log',
    # Relacion N a N entre jobs y clientes (agosto 2026): reemplaza el
    # tope de 3 clientes por job. Va scoped como todo lo demas -- una
    # relacion de Astral no puede verse ni escribirse desde Norkevin.
    'job_clients',
    # Calendarios de pago con estado explicito (active/superseded/...).
    'payment_schedules',
    # Correos generados que esperan aprobacion manual. Van scoped como todo
    # lo demas: un pendiente de Astral no debe verse ni aprobarse desde
    # Norkevin.
    'pending_emails',
    # Public Quote Experience (28-ago-2026): portfolio, condiciones y
    # templates de cotizacion son datos de UNA empresa -- un trabajo de
    # Norkevin jamas debe poder aparecer en una cotizacion de Astral ni
    # viceversa. Van scoped exactamente igual que 'packages'.
    'portfolio_items', 'quote_terms_templates', 'quote_templates',
}

# 'sequence_counters' (numeracion NORK-2026-0001) NO esta en
# TENANT_SCOPED_TABLES a proposito: no se lee/escribe nunca via
# list()/get()/upsert() genericos, solo via next_sequence_number(), que
# exige tenant_id explicito y hace su propio aislamiento por clave
# compuesta (tenant_id::scope::anio). Meterla en el set generico solo
# agregaria una ruta de acceso sin control adicional, no proteccion real.


logger = logging.getLogger(__name__)


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Rutas que NUNCA deben poder ser alcanzadas por _prune_backups() ni por
# ninguna operacion destructiva automatizada (reset-test-data, migraciones,
# quarantine). Estabilizacion agosto 2026, prioridad 7. Dos categorias:
#   protected_snapshot -- copias historicas completas de data/*.json que ya
#     sirvieron de fixture legacy para pruebas (ver STABILIZATION_EXECUTION_REPORT.md).
#   incident_evidence  -- evidencia preservada de un incidente real, fuera
#     del dataset operacional por diseno.
# Si algun dia una de estas carpetas se copia adentro de data/backups/ por
# error, is_protected_path() la sigue reconociendo por ruta absoluta.
PROTECTED_PATHS = (
    os.path.join(_REPO_ROOT, 'data', '_backup_pre_migracion_sqlite_20260712_012749'),
    os.path.join(_REPO_ROOT, 'evidencia', 'mail_log_incidente_2026-08-16_preservado.json'),
    # pre_cutover_snapshot -- snapshots completos tomados JUSTO ANTES de un
    # cutover controlado (ver tools/create_pre_cutover_snapshot.py y
    # CONTROLLED_CUTOVER_PLAN.md). Es LA copia de la que depende el
    # rollback entero: si esto se poda, se pierde la unica forma de volver
    # al estado previo al cutover. Se protege el directorio raiz completo,
    # asi que cualquier snapshot nuevo queda protegido automaticamente sin
    # tener que agregarlo aca uno por uno.
    os.path.join(_REPO_ROOT, 'protected_snapshots'),
)


def is_protected_path(path):
    """True si `path` esta dentro de (o es) una ruta protegida -- nunca se
    borra ni se sobreescribe automaticamente, sin importar que funcion lo
    este pidiendo."""
    resolved = os.path.abspath(path)
    for protected in PROTECTED_PATHS:
        if resolved == protected or resolved.startswith(protected + os.sep):
            return True
    return False


def verify_protected_paths():
    """Audita que cada ruta protegida siga existiendo y devuelve un reporte
    dict {path: {'exists': bool, 'is_dir': bool, 'file_count': int|None}}.
    Pensado para correrse periodicamente (o antes/despues de una operacion
    destructiva) y confirmar que nada las toco."""
    report = {}
    for protected in PROTECTED_PATHS:
        exists = os.path.exists(protected)
        is_dir = os.path.isdir(protected) if exists else False
        file_count = None
        if is_dir:
            file_count = sum(len(files) for _r, _d, files in os.walk(protected))
        report[protected] = {'exists': exists, 'is_dir': is_dir, 'file_count': file_count}
    return report


class TenantMismatchError(Exception):
    """Se intento leer/escribir un registro de una cuenta distinta a la
    activa -- nunca deberia pasar salvo un bug o un intento de acceso
    cruzado entre cuentas."""


class MissingTenantContextError(Exception):
    """Se pidieron datos sin saber de que empresa.

    Kevin: "no quiero que dentro de seis meses tengamos una automatizacion
    que no hace nada porque perdio el contexto de tenant y nadie se da
    cuenta". Devolver [] es seguro pero se confunde con "no hay registros";
    esta excepcion existe para que las operaciones sensibles fallen fuerte
    en vez de fallar en silencio (ver list_strict)."""


# ============================================================
# Exclusion mutua por ARCHIVO (agosto 2026)
# ============================================================
# Problema demostrado con 5 peticiones simultaneas sobre la misma
# conversion (stress de concurrencia, corrida 20260820_134955): 71 errores
# en 40 iteraciones -- 46 FileNotFoundError y 25 PermissionError. Ninguno
# produjo datos duplicados (la unicidad de la conversion la garantiza
# src/conversion_registry.py con un PRIMARY KEY), pero los hilos se caian.
#
# Las tres carreras eran:
#   1. _save(): dos escritores reemplazando el mismo archivo.
#   2. _backup_existing_file(): shutil.copy2() sobre un archivo que otro
#      hilo acababa de reemplazar -> FileNotFoundError.
#   3. _read_raw(): abrir el archivo justo cuando otro hilo lo sustituye
#      -> PermissionError en Windows.
#
# Solucion: un RLock POR ARCHIVO. No un lock global: dos tablas distintas
# (jobs y payments, por ejemplo) se escriben en paralelo sin estorbarse, y
# el CRM no se convierte en un cuello de botella. Solo compiten los hilos
# que tocan EL MISMO archivo, que es exactamente donde estaba la carrera.
#
# RLock (no Lock) porque las llamadas son reentrantes por diseno:
# upsert() toma el lock y adentro llama a _read_raw() y _save(), que
# vuelven a tomarlo. Con un Lock normal eso seria un deadlock inmediato.
#
# La clave del registry es la ruta absoluta normalizada (os.path.normcase
# para que Windows no trate 'Jobs.json' y 'jobs.json' como archivos
# distintos), de modo que dos instancias de JsonStore apuntando al mismo
# directorio comparten el MISMO lock -- un lock por instancia no protegeria
# nada.
_FILE_LOCKS = {}
_FILE_LOCKS_GUARD = threading.Lock()

# Reintento como defensa SECUNDARIA, no como mecanismo de concurrencia:
# el lock ya serializa a los hilos de este proceso. Esto solo cubre el caso
# de otro PROCESO (o el antivirus de Windows) manteniendo el archivo
# abierto unos milisegundos. Acotado a proposito: si el problema persiste,
# la excepcion se propaga en vez de esconder una corrupcion real.
_REINTENTOS_IO = 5
_ESPERA_IO_BASE = 0.01


def _lock_for_path(path):
    """RLock compartido para una ruta concreta."""
    clave = os.path.normcase(os.path.abspath(path))
    lock = _FILE_LOCKS.get(clave)
    if lock is None:
        with _FILE_LOCKS_GUARD:
            lock = _FILE_LOCKS.get(clave)
            if lock is None:
                lock = threading.RLock()
                _FILE_LOCKS[clave] = lock
    return lock


def _leer_json_con_reintento(path):
    """Lee y parsea un JSON tolerando que otro proceso lo tenga tomado un
    instante. NO tolera JSON invalido: eso es corrupcion real y debe
    explotar, no reintentarse en silencio."""
    ultimo = None
    for intento in range(_REINTENTOS_IO):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (PermissionError, FileNotFoundError) as exc:
            # El archivo esta a mitad de una sustitucion hecha por otro
            # proceso. Se reintenta brevemente.
            ultimo = exc
            time.sleep(_ESPERA_IO_BASE * (intento + 1))
    raise ultimo


def log_security_event(evento, **datos):
    """Log uniforme de lo que se bloquea, para poder rastrear despues.

    A proposito NO se registra el contenido de los correos ni datos
    personales: solo ids, tablas y cuentas, que es lo que hace falta para
    investigar.
    """
    detalle = ' '.join(f'{k}={v}' for k, v in datos.items() if v is not None)
    logger.warning('SECURITY: %s %s', evento, detalle)


class JsonStore:
    """Store CRUD simple sobre archivos JSON."""

    def __init__(self, data_dir=None):
        if data_dir is None:
            data_dir = DATA_DIR
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        # Cache en memoria por tabla: {table: (mtime, records)}. Se invalida
        # solo si el archivo en disco cambio (por la app o por edicion externa),
        # asi evitamos releer/re-parsear JSON en cada request sin arriesgar
        # datos obsoletos.
        self._cache = {}
        # Callable() -> tenant_id | None. Configurado por app.py al arrancar.
        # None (default) = sin aislamiento (scripts/tests fuera de la app).
        self.tenant_resolver = None
        # Callable() -> bool: "estoy dentro de una peticion web?". app.py le
        # pasa flask.has_request_context. Sirve para exigir cuenta activa
        # solo donde hay un usuario/enlace real detras, y no romper los
        # scripts de migracion ni la siembra de datos de los tests.
        self.request_context_probe = None
        # Callable() -> bool: "esta peticion es de una ruta administrativa
        # autorizada?". Solo desde ahi se puede pedir scope='all_tenants'.
        # Kevin: "quiero que el acceso cross-tenant sea una excepcion visible
        # y deliberada, nunca el comportamiento por defecto".
        self.admin_context_probe = None

    def _path(self, table):
        return os.path.join(self.data_dir, f'{table}.json')

    def _current_tenant_id(self):
        if self.tenant_resolver is None:
            return None
        try:
            return self.tenant_resolver()
        except Exception:
            return None

    def _tenant_scope(self):
        """(aislar, tenant_id) para la operacion en curso.

        Regla: **dentro de una peticion web el aislamiento es obligatorio.**
        Si no hay cuenta activa la operacion se deniega, en vez de caer a
        "todas las cuentas" como antes.

        Ese fallback permisivo es la causa raiz de un incidente real: una
        rutina sin cuenta activa recorrio las bodas de los dos negocios
        juntos y termino mandando correos de una empresa a los clientes de la
        otra. Sin cuenta identificada la respuesta correcta es "nada", no
        "todo".

        Fuera de una peticion (scripts de migracion, tests que siembran
        datos) se mantiene el comportamiento sin aislamiento: ese codigo no
        es alcanzable desde la web y necesita ver el archivo completo. El
        riesgo de fondo -- rutinas automaticas sin cuenta -- se ataca en su
        origen: el scheduler esta apagado y Gmail no envia sin cuenta
        resuelta.
        """
        if self.tenant_resolver is None:
            return False, None
        if self.request_context_probe is not None:
            try:
                in_request = bool(self.request_context_probe())
            except Exception:
                in_request = False
            if not in_request:
                return False, None
        return True, self._current_tenant_id()

    def status(self) -> Dict[str, Any]:
        """Devuelve un resumen seguro del almacenamiento activo."""
        data_dir_abs = os.path.abspath(self.data_dir)
        render_disk = os.path.abspath(os.environ.get('CRM_DATA_DIR') or '')
        return {
            'data_dir': data_dir_abs,
            'crm_data_dir_env': render_disk or None,
            'uses_env_data_dir': bool(os.environ.get('CRM_DATA_DIR')),
            'is_render_persistent_path': data_dir_abs == os.path.abspath('/var/data'),
            'backup_dir': os.path.join(data_dir_abs, 'backups'),
        }

    def _read_raw(self, table):
        """Lee la tabla completa desde disco (o cache), SIN filtrar por
        tenant. Uso interno de upsert/delete/clear -- necesitan la lista
        completa de TODAS las cuentas para no perder los registros de las
        otras al reescribir el archivo."""
        path = self._path(table)
        # Bajo el lock del archivo: nunca se observa una sustitucion a
        # medias. Sin esto, abrir el archivo mientras otro hilo hace
        # os.replace() daba PermissionError/FileNotFoundError en Windows.
        with _lock_for_path(path):
            if not os.path.exists(path):
                return []
            mtime = os.path.getmtime(path)
            cached = self._cache.get(table)
            if cached and cached[0] == mtime:
                return copy.deepcopy(cached[1])
            records = _leer_json_con_reintento(path)
            self._cache[table] = (mtime, records)
            return copy.deepcopy(records)

    def list(self, table):
        records = self._read_raw(table)
        if table in TENANT_SCOPED_TABLES:
            aislar, tenant_id = self._tenant_scope()
            if aislar and not tenant_id:
                # Dentro de la app pero sin cuenta activa: no se ve nada.
                log_security_event('SIN_CONTEXTO_DE_EMPRESA', tabla=table, operacion='list')
                return []
            if tenant_id:
                records = [r for r in records if r.get('tenant_id') == tenant_id]
        return records

    def list_strict(self, table):
        """Como list(), pero revienta si falta el contexto de empresa.

        Kevin: "prefiero un error explicito y registrado antes que un fallo
        silencioso... no quiero que dentro de seis meses tengamos una
        automatizacion que no hace nada porque perdio el contexto".

        list() devuelve [] sin cuenta, que es seguro pero indistinguible de
        "esta tabla esta vacia". Para rutinas, workers y cualquier operacion
        que actue sobre lo que lee, usar esta: una lista vacia va a
        significar de verdad que no hay registros.
        """
        if table in TENANT_SCOPED_TABLES:
            aislar, tenant_id = self._tenant_scope()
            if aislar and not tenant_id:
                log_security_event('SIN_CONTEXTO_DE_EMPRESA', tabla=table,
                                   operacion='list_strict')
                raise MissingTenantContextError(
                    f"Se pidio '{table}' sin cuenta activa. Una rutina que "
                    'perdio el contexto de empresa no debe verse como una '
                    'tabla vacia.'
                )
        return self.list(table)

    def current_tenant_id(self):
        """Cuenta activa, o None fuera de una peticion. Publico porque otros
        modulos (mail_tracker) necesitan saber desde que cuenta se esta
        actuando para validar que todo lo del correo sea de esa misma."""
        return self._current_tenant_id()

    def list_privileged(self, table, *, tenant_id=None, scope=None, reason):
        """Lectura que se SALTA el aislamiento normal. Es una excepcion.

        Existe para tres casos legitimos, todos fuera del flujo de un usuario
        con sesion:

          - integraciones servidor-a-servidor autenticadas por token, que
            filtran por empresa ellas mismas;
          - reportes de administracion que por definicion miran todas las
            empresas (inventario, huerfanos, reporte del incidente);
          - migraciones, que tienen que ver el archivo completo.

        `reason` es obligatorio y sin default a proposito: obliga a
        justificar el salto en el punto de uso, y queda en el log de
        seguridad. Si se pasa `tenant_id` se filtra por esa empresa, que es
        lo que deberia hacer cualquier integracion.

        Kevin: "no quiero que _read_raw se convierta en la nueva forma facil
        de saltarse el sistema".
        """
        # Kevin: "no quiero que omitir tenant signifique automaticamente
        # acceso total". Ver todas las empresas hay que pedirlo por su
        # nombre, no conseguirlo por descuido.
        if tenant_id is None and scope != 'all_tenants':
            raise ValueError(
                f"list_privileged('{table}') sin tenant_id necesita "
                "scope='all_tenants' explicito. Omitir la empresa no da "
                'acceso a todas.'
            )
        if scope == 'all_tenants':
            # Ver todas las empresas solo se permite desde una ruta
            # administrativa autorizada. Dentro de una peticion normal
            # (aunque el codigo lo pida) se rechaza: si no, cualquier ruta
            # podria mirar los datos de los dos negocios.
            en_request = False
            if self.request_context_probe is not None:
                try:
                    en_request = bool(self.request_context_probe())
                except Exception:
                    en_request = False
            if en_request:
                es_admin = False
                if self.admin_context_probe is not None:
                    try:
                        es_admin = bool(self.admin_context_probe())
                    except Exception:
                        es_admin = False
                if not es_admin:
                    log_security_event('ALL_TENANTS_BLOQUEADO', tabla=table,
                                       motivo=reason)
                    raise TenantMismatchError(
                        f"scope='all_tenants' sobre '{table}' solo se permite "
                        'desde una ruta administrativa autorizada.'
                    )
        log_security_event('LECTURA_PRIVILEGIADA', tabla=table,
                           cuenta=tenant_id or 'TODAS', motivo=reason)
        records = self._read_raw(table)
        if tenant_id is not None:
            records = [r for r in records if r.get('tenant_id') == tenant_id]
        return records

    def owner_tenant_of(self, table, value, field='id'):
        """Cuenta duena de un registro, saltando el filtro por cuenta.

        UNICO lugar autorizado para mirar entre cuentas, y existe por un
        motivo concreto: las rutas publicas (portal del cliente, aceptar una
        cotizacion, firmar un contrato) llegan sin sesion y necesitan saber a
        que cuenta pertenece el enlace ANTES de poder aislarse a ella.

        `field` permite buscar por otra clave: el PDF publico de una factura
        se pide por invoice_id, no por el id del registro de pago.

        Devuelve solo el tenant_id, nunca el registro: no sirve para leer
        datos de otra cuenta.
        """
        for record in self._read_raw(table):
            if record.get(field) == value:
                return record.get('tenant_id')
        return None

    def tenants_owning(self, table, value, field='id'):
        """Todas las cuentas que tienen un registro con ese valor.

        Hermana de owner_tenant_of, para cuando el valor NO es unico entre
        empresas: la misma direccion de correo puede existir como cliente en
        Astral y en Norkevin, y son dos personas distintas. Saber que esta en
        las dos es justo lo que permite marcar un destinatario como ambiguo.

        Devuelve un set de tenant_id, nunca registros -- misma regla que
        owner_tenant_of: no sirve para leer datos de otra cuenta.
        """
        return {r.get('tenant_id') for r in self._read_raw(table)
                if r.get(field) == value and r.get('tenant_id')}

    def get(self, table, record_id):
        # Ya filtrado por list() -- pedir el id de otra cuenta devuelve
        # None, como si el registro no existiera.
        for record in self.list(table):
            if record.get('id') == record_id:
                return record
        return None

    def upsert(self, table, record):
        # El ciclo COMPLETO leer -> decidir -> escribir bajo el mismo lock.
        # Sin esto, dos hilos podian leer la misma lista, cada uno agregar
        # su registro y el segundo _save() pisaba el del primero (update
        # perdido), ademas de las carreras de archivo.
        with _lock_for_path(self._path(table)):
            return self._upsert_locked(table, record)

    def _upsert_locked(self, table, record):
        scoped = table in TENANT_SCOPED_TABLES
        aislar, tenant_id = self._tenant_scope() if scoped else (False, None)
        if scoped and aislar and not tenant_id:
            raise TenantMismatchError(
                f"No se puede escribir en '{table}' sin una cuenta activa. "
                'Escribir sin cuenta dejaba registros sin dueno, visibles '
                'desde cualquier negocio.'
            )
        if scoped and tenant_id:
            record_tenant = record.get('tenant_id')
            if record_tenant and record_tenant != tenant_id:
                raise TenantMismatchError(
                    f"No se puede escribir en '{table}' un registro de la cuenta "
                    f"'{record_tenant}' estando activa la cuenta '{tenant_id}'."
                )
            if not record_tenant:
                record = dict(record)
                record['tenant_id'] = tenant_id

        records = self._read_raw(table)
        existing_idx = None
        for i, r in enumerate(records):
            if r.get('id') == record.get('id'):
                existing_idx = i
                break

        if existing_idx is not None:
            if scoped and tenant_id:
                current_owner = records[existing_idx].get('tenant_id')
                if current_owner and current_owner != tenant_id:
                    log_security_event(
                        'CROSS_TENANT_ACCESS_BLOCKED', operacion='upsert', tabla=table,
                        registro=record.get('id'), cuenta_activa=tenant_id,
                        cuenta_del_registro=current_owner,
                    )
                    raise TenantMismatchError(
                        f"El registro '{record.get('id')}' en '{table}' pertenece a la "
                        f"cuenta '{current_owner}', no a la cuenta activa '{tenant_id}'."
                    )
            records[existing_idx].update(record)
        else:
            records.append(record)

        self._save(table, records)
        return record

    def delete(self, table, record_id):
        with _lock_for_path(self._path(table)):
            return self._delete_locked(table, record_id)

    def _delete_locked(self, table, record_id):
        records = self._read_raw(table)
        if table in TENANT_SCOPED_TABLES:
            aislar, tenant_id = self._tenant_scope()
            if aislar and not tenant_id:
                # Sin cuenta activa no se borra nada de ninguna cuenta.
                return False
            if tenant_id:
                target = next((r for r in records if r.get('id') == record_id), None)
                if target and target.get('tenant_id') and target.get('tenant_id') != tenant_id:
                    # No pertenece a esta cuenta -- se comporta como si no
                    # existiera, no se borra nada.
                    log_security_event(
                        'CROSS_TENANT_ACCESS_BLOCKED', operacion='delete', tabla=table,
                        registro=record_id, cuenta_activa=tenant_id,
                        cuenta_del_registro=target.get('tenant_id'),
                    )
                    return False
        records = [r for r in records if r.get('id') != record_id]
        self._save(table, records)
        return True

    def clear(self, table):
        """Vacia una tabla. Si hay una cuenta activa y la tabla es
        tenant-scoped, solo borra los registros de ESA cuenta (Kevin: cada
        cuenta debe funcionar como un CRM independiente -- 'Vaciar datos de
        prueba' de una cuenta no debe tocar las otras). El registro anterior
        queda respaldado automaticamente por _save/_backup_existing_file."""
        if table in TENANT_SCOPED_TABLES:
            aislar, tenant_id = self._tenant_scope()
            if aislar and not tenant_id:
                # Sin cuenta activa, vaciar habria borrado la tabla completa
                # de TODOS los negocios.
                raise TenantMismatchError(
                    f"No se puede vaciar '{table}' sin una cuenta activa."
                )
            if tenant_id:
                records = self._read_raw(table)
                remaining = [r for r in records if r.get('tenant_id') != tenant_id]
                self._save(table, remaining)
                return
        self._save(table, [])

    def backup_now(self, table):
        """Respaldo explicito y VERIFICADO, para operaciones destructivas
        que deben poder abortar si el backup no se pudo confirmar (ver
        api_admin_reset_test_data en app.py). A diferencia de
        `_backup_existing_file` (que se llama automaticamente dentro de
        `_save` y no se verifica despues), esta funcion:
          1. Copia el archivo actual a data/backups/<fecha>/.
          2. Verifica que la copia exista y tenga el MISMO tamano que el
             original (deteccion barata de una copia truncada/corrupta).
          3. Devuelve la ruta del backup si todo salio bien, o lanza
             RuntimeError si algo fallo -- el llamador debe interpretar
             eso como "no borrar nada".
        Si la tabla no tiene archivo todavia (tabla vacia/nunca escrita),
        no hay nada que respaldar y devuelve None (no es un fallo)."""
        path = self._path(table)
        if not os.path.exists(path):
            return None
        backups_root = os.path.join(self.data_dir, 'backups', datetime.now().strftime('%Y%m%d'))
        os.makedirs(backups_root, exist_ok=True)
        timestamp = datetime.now().strftime('%H%M%S_%f')
        backup_path = os.path.join(backups_root, f'{table}_manual_{timestamp}.json')
        shutil.copy2(path, backup_path)
        if not os.path.exists(backup_path):
            raise RuntimeError(f"Backup de '{table}' no se pudo verificar (archivo no existe tras copiar).")
        if os.path.getsize(backup_path) != os.path.getsize(path):
            raise RuntimeError(f"Backup de '{table}' no se pudo verificar (tamano distinto al original).")
        return backup_path

    def next_sequence_number(self, scope, *, tenant_id=None, year=None):
        """Numero secuencial atomico, por cuenta y por 'scope' (ej. 'quotes'),
        reiniciado cada anio (NORK-2026-0001, NORK-2027-0001...). Reemplaza a
        MAX(id)+1 -- que dos escrituras concurrentes pueden leer igual y
        devolver el mismo numero dos veces -- con el mismo patron de
        exclusion mutua por archivo que ya usa upsert(): todo el ciclo
        leer -> incrementar -> escribir bajo el MISMO RLock del archivo de
        contadores (ver _FILE_LOCKS arriba). Guarda en 'sequence_counters',
        una tabla nueva, sin tocar ninguna tabla ni logica existente.

        A proposito NO pasa por _tenant_scope(): quien llama debe conocer
        el tenant_id (normalmente ya lo tiene: es el mismo que va en el
        registro que esta creando) en vez de depender de la sesion activa.
        Asi este metodo tambien sirve desde scripts/migraciones sin
        request, y nunca puede devolver un numero "sin dueno" por perder el
        contexto -- revienta en vez de adivinar."""
        tenant_id = tenant_id or self._current_tenant_id()
        if not tenant_id:
            raise MissingTenantContextError(
                f"next_sequence_number('{scope}') sin tenant_id: un numero "
                'de secuencia sin cuenta quedaria compartido entre negocios.'
            )
        year = year or datetime.now().year
        key = f'{tenant_id}::{scope}::{year}'
        path = self._path('sequence_counters')
        with _lock_for_path(path):
            counters = self._read_raw('sequence_counters')
            row = next((c for c in counters if c.get('id') == key), None)
            nuevo = (row.get('next') if row else 0) + 1
            if row:
                row['next'] = nuevo
            else:
                counters.append({
                    'id': key, 'tenant_id': tenant_id, 'scope': scope,
                    'year': year, 'next': nuevo,
                })
            self._save('sequence_counters', counters)
        return nuevo

    def owner_tenant_of_public_token(self, table, token, *, hash_field='public_token_hash'):
        """Como owner_tenant_of (arriba), pero para enlaces con token seguro
        (ver src/public_tokens.py) en vez de un id: el valor guardado es un
        HASH, no el token en claro, asi que la comparacion no puede ser una
        igualdad directa -- hay que hashear lo recibido y comparar en tiempo
        constante, que es exactamente lo que ya hace
        public_tokens.token_coincide().

        Devuelve solo el tenant_id, nunca el registro completo: misma regla
        que owner_tenant_of, este es el UNICO tipo de lectura entre cuentas
        que se permite antes de que el aislamiento se pueda aplicar."""
        from src import public_tokens
        if not token:
            return None
        for record in self._read_raw(table):
            guardado = record.get(hash_field)
            if guardado and public_tokens.token_coincide(token, guardado):
                return record.get('tenant_id')
        return None

    def _save(self, table, records):
        """Escribe la tabla completa de forma atomica.

        CONCURRENCIA (bug real encontrado en la corrida de Windows del
        20-ago-2026): la version anterior usaba SIEMPRE el mismo nombre de
        temporal, `<tabla>.json.tmp`, y luego shutil.move(). Con dos o mas
        hilos escribiendo la misma tabla a la vez eso rompe de dos formas:

          1. Los hilos se pisan el MISMO archivo temporal (uno lo trunca
             mientras el otro todavia lo esta escribiendo).
          2. En Windows, os.rename() sobre un destino que otro hilo tiene
             abierto falla con PermissionError [WinError 5].

        Lo observado: en el test de 5 requests concurrentes, 4 de los 5
        hilos MURIERON con WinError 5 y solo 1 completo la peticion. El
        test daba PASS pero no porque la idempotencia funcionara, sino
        porque los otros 4 nunca llegaron a crear nada. Un verde falso.

        Ahora: (a) el temporal lleva pid+thread id, asi que dos escritores
        nunca comparten archivo; (b) el reemplazo usa os.replace(), que es
        atomico en POSIX y en Windows; (c) si aun asi Windows devuelve un
        error transitorio de comparticion, se reintenta brevemente en vez
        de matar el hilo.

        Esto NO sustituye la garantia de unicidad de la conversion
        lead->job (eso vive en src/conversion_registry.py, con un UNIQUE
        de verdad). Aca solo se garantiza que una escritura concurrente no
        corrompa el archivo ni explote."""
        path = self._path(table)
        # TODO el ciclo backup -> escribir temporal -> reemplazar ocurre
        # bajo el mismo lock, para que el backup corresponda siempre a un
        # estado anterior VALIDO y para que ningun lector vea el archivo a
        # mitad de la sustitucion.
        with _lock_for_path(path):
            self._backup_existing_file(table, path)
            tmp_path = f'{path}.{os.getpid()}.{threading.get_ident()}.tmp'
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(records, f, indent=2, ensure_ascii=False)

            ultimo_error = None
            for intento in range(_REINTENTOS_IO):
                try:
                    os.replace(tmp_path, path)
                    ultimo_error = None
                    break
                except PermissionError as exc:
                    # Defensa SECUNDARIA: dentro del proceso el lock ya
                    # serializa. Esto solo cubre otro proceso/antivirus
                    # tocando el archivo un instante.
                    ultimo_error = exc
                    time.sleep(_ESPERA_IO_BASE * (intento + 1))
            if ultimo_error is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise ultimo_error

            # Invalida el cache; la proxima list() vuelve a leer y re-cachear.
            self._cache.pop(table, None)

    def _backup_existing_file(self, table, path):
        if not os.path.exists(path):
            return
        backups_root = os.path.join(self.data_dir, 'backups', datetime.now().strftime('%Y%m%d'))
        os.makedirs(backups_root, exist_ok=True)
        timestamp = datetime.now().strftime('%H%M%S_%f')
        backup_path = os.path.join(backups_root, f'{table}_{timestamp}.json')
        shutil.copy2(path, backup_path)
        self._prune_backups(table, keep=50)

    def _prune_backups(self, table, keep=50):
        """Poda SOLO dentro de data/backups/<fecha>/ -- los backups
        rotativos automaticos de cada _save()/clear(). Nunca debe poder
        alcanzar nada fuera de ahi.

        Distincion explicita (estabilizacion, agosto 2026, prioridad 7 --
        Kevin: 'revisa si un ciclo intensivo de pruebas podria terminar
        eliminando la ultima copia util de datos reales'):
          - rotational_backup:  esto. Se poda a `keep` mas recientes por
            tabla. Vive en data/backups/.
          - protected_snapshot: copias como
            data/_backup_pre_migracion_sqlite_20260712_012749/ -- NO viven
            en data/backups/, esta funcion nunca las toca por construccion
            (os.walk esta acotado a backups_root), y ademas se listan en
            PROTECTED_PATHS como defensa en profundidad por si algun dia
            alguien cambia backups_root o copia una de estas carpetas
            adentro de data/backups/ sin darse cuenta.
          - incident_evidence:  evidencia/mail_log_incidente_*.json -- fuera
            del directorio de datos operacional por completo, tampoco
            alcanzable por _save()/clear() de ninguna tabla (no es una
            'tabla' del store)."""
        backups_root = os.path.join(self.data_dir, 'backups')
        if not os.path.isdir(backups_root):
            return
        matches = []
        for root, _dirs, files in os.walk(backups_root):
            for filename in files:
                if filename.startswith(f'{table}_') and filename.endswith('.json'):
                    path = os.path.join(root, filename)
                    if is_protected_path(path):
                        continue  # defensa en profundidad -- nunca deberia matchear aca
                    try:
                        matches.append((os.path.getmtime(path), path))
                    except OSError:
                        continue
        for _mtime, old_path in sorted(matches, reverse=True)[keep:]:
            try:
                os.remove(old_path)
            except OSError:
                pass



    def get_dict(self, name: str) -> Dict[str, Any]:
        """Lee un archivo JSON como dict (no como lista de records)."""
        path = os.path.join(self.data_dir, f'{name}.json')
        with _lock_for_path(path):
            if not os.path.exists(path):
                return {}
            return _leer_json_con_reintento(path)

    def save_dict(self, name: str, data: Dict[str, Any]):
        """Guarda un dict en JSON, de forma atomica y serializada.

        Mismo tratamiento que _save(): esta ruta la usa el motor de
        workflows (`workflow_instances`), asi que tenia exactamente el
        mismo defecto -- temporal compartido `<name>.json.tmp` y
        shutil.move() -- y por lo tanto las mismas carreras."""
        path = os.path.join(self.data_dir, f'{name}.json')
        with _lock_for_path(path):
            self._backup_existing_file(name, path)
            tmp_path = f'{path}.{os.getpid()}.{threading.get_ident()}.tmp'
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            ultimo_error = None
            for intento in range(_REINTENTOS_IO):
                try:
                    os.replace(tmp_path, path)
                    ultimo_error = None
                    break
                except PermissionError as exc:
                    ultimo_error = exc
                    time.sleep(_ESPERA_IO_BASE * (intento + 1))
            if ultimo_error is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise ultimo_error

    def _tenant_dict_key(self, name, tenant_id=None):
        """Nombre de archivo para un dict que SI es distinto por cuenta
        (settings, credenciales de Gmail/Recurrente, estado de OAuth).
        `tenant_id` explicito (para el hilo de recordatorios, que ya sabe
        de que cuenta es el job/payment que esta procesando y no puede
        depender del resolver de sesion) tiene prioridad sobre el resolver
        ambiente. Sin ninguno de los dos cae al archivo compartido, y ese
        fallback es EXACTAMENTE el que creo el google_token.json global que
        se uso durante el incidente. Se deja solo para lectura (config vieja
        que todavia puede existir en disco) y queda registrado; escribir sin
        cuenta esta prohibido, ver save_tenant_dict."""
        resolved = tenant_id or self._current_tenant_id()
        if not resolved:
            log_security_event('FALLBACK_A_CONFIG_GLOBAL', archivo=name, operacion='lectura')
        return f'{name}_{resolved}' if resolved else name

    def get_tenant_dict(self, name: str, tenant_id: str = None) -> Dict[str, Any]:
        return self.get_dict(self._tenant_dict_key(name, tenant_id))

    def save_tenant_dict(self, name: str, data: Dict[str, Any], tenant_id: str = None):
        """Guardar sin cuenta creaba archivos de configuracion/credenciales
        globales, sin dueno y compartidos entre negocios. Prohibido."""
        if not (tenant_id or self._current_tenant_id()):
            log_security_event('ESCRITURA_GLOBAL_BLOQUEADA', archivo=name)
            raise MissingTenantContextError(
                f"No se puede guardar '{name}' sin cuenta activa: crearia un "
                'archivo global compartido entre negocios.'
            )
        self.save_dict(self._tenant_dict_key(name, tenant_id), data)


# Singleton
store = JsonStore()
