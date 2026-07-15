"""
storage.py - Capa de persistencia local basada en archivos JSON.
"""
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

    def _path(self, table):
        return os.path.join(self.data_dir, f'{table}.json')

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

    def list(self, table):
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

    def get(self, table, record_id):
        for record in self.list(table):
            if record.get('id') == record_id:
                return record
        return None

    def upsert(self, table, record):
        records = self.list(table)
        existing_idx = None
        for i, r in enumerate(records):
            if r.get('id') == record.get('id'):
                existing_idx = i
                break

        if existing_idx is not None:
            records[existing_idx].update(record)
        else:
            records.append(record)

        self._save(table, records)
        return record

    def delete(self, table, record_id):
        records = self.list(table)
        records = [r for r in records if r.get('id') != record_id]
        self._save(table, records)
        return True

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


# Singleton
store = JsonStore()
