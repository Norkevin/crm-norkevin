"""
storage.py - Capa de persistencia local basada en archivos JSON.

Aislamiento multi-tenant (3 cuentas independientes: Astral Weddings,
Norkevin Photography, Ramiro Cruz Photo): en vez de que cada una de las
~250 rutas de app.py se acuerde de filtrar por tenant_id, el filtrado vive
aca, en el unico choke point por el que pasan todas las lecturas/escrituras.
`app.py` configura `store.tenant_resolver` una sola vez al arrancar
(`store.tenant_resolver = lambda: session.get('tenant_id')`) -- este modulo
NO importa Flask directamente para poder seguir siendo testeable/usable
fuera de un request (scripts de migracion, el hilo de recordatorios en
segundo plano).

Cuando el resolver devuelve None (sin sesion activa -- el hilo en segundo
plano que revisa recordatorios de pago de TODAS las cuentas, o un script),
list()/get() devuelven todo sin filtrar a proposito: esa es la unica
situacion legitima donde "sin tenant" significa "todas las cuentas", no un
bug. upsert() en cambio nunca escribe sin tenant_id si hay uno explicito en
el propio registro (asi los procesos en segundo plano que ya tienen el
job/lead/payment en mano pueden pasar el tenant_id correcto a mano)."""
import copy
import json
import os
import shutil
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
}


class TenantMismatchError(Exception):
    """Se intento leer/escribir un registro de una cuenta distinta a la
    activa -- nunca deberia pasar salvo un bug o un intento de acceso
    cruzado entre cuentas."""


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
        # None (default) = sin aislamiento, se comporta como antes (usado
        # tambien por scripts/tests que no pasan por una sesion de Flask).
        self.tenant_resolver = None

    def _path(self, table):
        return os.path.join(self.data_dir, f'{table}.json')

    def _current_tenant_id(self):
        if self.tenant_resolver is None:
            return None
        try:
            return self.tenant_resolver()
        except Exception:
            return None

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
        if not os.path.exists(path):
            return []
        mtime = os.path.getmtime(path)
        cached = self._cache.get(table)
        if cached and cached[0] == mtime:
            return copy.deepcopy(cached[1])
        with open(path, 'r', encoding='utf-8') as f:
            records = json.load(f)
        self._cache[table] = (mtime, records)
        return copy.deepcopy(records)

    def list(self, table):
        records = self._read_raw(table)
        if table in TENANT_SCOPED_TABLES:
            tenant_id = self._current_tenant_id()
            if tenant_id:
                records = [r for r in records if r.get('tenant_id') == tenant_id]
        return records

    def get(self, table, record_id):
        # Ya filtrado por list() -- pedir el id de otra cuenta devuelve
        # None, como si el registro no existiera.
        for record in self.list(table):
            if record.get('id') == record_id:
                return record
        return None

    def upsert(self, table, record):
        scoped = table in TENANT_SCOPED_TABLES
        tenant_id = self._current_tenant_id() if scoped else None
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
        records = self._read_raw(table)
        if table in TENANT_SCOPED_TABLES:
            tenant_id = self._current_tenant_id()
            if tenant_id:
                target = next((r for r in records if r.get('id') == record_id), None)
                if target and target.get('tenant_id') and target.get('tenant_id') != tenant_id:
                    # No pertenece a esta cuenta -- se comporta como si no
                    # existiera, no se borra nada.
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
            tenant_id = self._current_tenant_id()
            if tenant_id:
                records = self._read_raw(table)
                remaining = [r for r in records if r.get('tenant_id') != tenant_id]
                self._save(table, remaining)
                return
        self._save(table, [])

    def _save(self, table, records):
        path = self._path(table)
        self._backup_existing_file(table, path)
        tmp_path = path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        shutil.move(tmp_path, path)
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
        backups_root = os.path.join(self.data_dir, 'backups')
        if not os.path.isdir(backups_root):
            return
        matches = []
        for root, _dirs, files in os.walk(backups_root):
            for filename in files:
                if filename.startswith(f'{table}_') and filename.endswith('.json'):
                    path = os.path.join(root, filename)
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
        if not os.path.exists(path):
            return {}
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def save_dict(self, name: str, data: Dict[str, Any]):
        """Guarda un dict en JSON."""
        path = os.path.join(self.data_dir, f'{name}.json')
        self._backup_existing_file(name, path)
        tmp_path = path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        shutil.move(tmp_path, path)

    def _tenant_dict_key(self, name, tenant_id=None):
        """Nombre de archivo para un dict que SI es distinto por cuenta
        (settings, credenciales de Gmail/Recurrente, estado de OAuth).
        `tenant_id` explicito (para el hilo de recordatorios, que ya sabe
        de que cuenta es el job/payment que esta procesando y no puede
        depender del resolver de sesion) tiene prioridad sobre el resolver
        ambiente. Sin ninguno de los dos, cae al archivo compartido de
        siempre -- mismo comportamiento que antes de multi-tenant."""
        resolved = tenant_id or self._current_tenant_id()
        return f'{name}_{resolved}' if resolved else name

    def get_tenant_dict(self, name: str, tenant_id: str = None) -> Dict[str, Any]:
        return self.get_dict(self._tenant_dict_key(name, tenant_id))

    def save_tenant_dict(self, name: str, data: Dict[str, Any], tenant_id: str = None):
        self.save_dict(self._tenant_dict_key(name, tenant_id), data)


# Singleton
store = JsonStore()
