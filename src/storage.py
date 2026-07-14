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
        tmp_path = path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        shutil.move(tmp_path, path)
        # Invalida el cache; la proxima list() vuelve a leer y re-cachear.
        self._cache.pop(table, None)



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
        tmp_path = path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        shutil.move(tmp_path, path)


# Singleton
store = JsonStore()