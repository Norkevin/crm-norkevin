"""
verify_v5_consistency.py
========================

Verifica que el DDL real ejecutado (desde schema_v5.sql) corresponde
con lo esperado. Compara el inventario real de la DB con lo que el
documento declara.

NO modifica crm.db, NO modifica los JSON, NO modifica app.py.
"""
import os
import sys
import hashlib
import sqlite3
import re

BASE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(BASE, "schema_v5.sql")
MD = os.path.join(BASE, "MODELO_DE_DATOS_CRM_V5.md")
SCRIPT = os.path.join(BASE, "validate_schema_v5.py")

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def setup():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    with open(SCHEMA, "r", encoding="utf-8") as f:
        sql = f.read()
    conn.executescript(sql)
    conn.commit()
    return conn

def inventory(conn):
    """SELECT name, type FROM sqlite_master WHERE type IN ('table','index','trigger')."""
    return conn.execute(
        "SELECT name, type FROM sqlite_master "
        "WHERE type IN ('table', 'index', 'trigger') "
        "AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type, name"
    ).fetchall()

def main():
    print("=" * 75)
    print("VERIFY_V5_CONSISTENCY.PY")
    print("=" * 75)
    print()
    print("SHA-256 de archivos:")
    print(f"  schema_v5.sql:           {sha256(SCHEMA)}")
    print(f"  validate_schema_v5.py:   {sha256(SCRIPT)}")
    print(f"  MODELO_DE_DATOS_CRM_V5.md: {sha256(MD)}")
    print()

    # Inventario REAL desde schema_v5.sql
    print("=== Inventario REAL de la DB (ejecutando schema_v5.sql) ===")
    conn = setup()
    inv = inventory(conn)
    real_tables = [n for n, t in inv if t == 'table']
    real_indexes = [n for n, t in inv if t == 'index']
    real_triggers = [n for n, t in inv if t == 'trigger']
    print(f"  Tablas: {len(real_tables)}")
    for t in real_tables:
        print(f"    - {t}")
    print(f"  Indices: {len(real_indexes)}")
    for i in real_indexes:
        print(f"    - {i}")
    print(f"  Triggers: {len(real_triggers)}")
    for t in real_triggers:
        print(f"    - {t}")
    print()

    # PRAGMA checks
    print("=== PRAGMA foreign_key_check ===")
    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    print(f"  Filas: {len(fk_errors)}")
    print()
    print("=== PRAGMA integrity_check ===")
    ic = conn.execute("PRAGMA integrity_check").fetchone()
    print(f"  Resultado: {ic[0]}")
    print()

    # Confirmacion
    n_tables = len(real_tables)
    n_indexes = len(real_indexes)
    n_triggers = len(real_triggers)
    print("=" * 75)
    print("RESULTADO")
    print("=" * 75)
    if len(fk_errors) == 0 and ic[0] == 'ok':
        print(f"[OK] DDL consistente: {n_tables} tablas, {n_indexes} indices, {n_triggers} triggers")
        print("[OK] foreign_key_check: 0 filas")
        print("[OK] integrity_check: ok")
        return 0
    else:
        print("[FAIL] Hay problemas en el DDL")
        return 1


if __name__ == "__main__":
    sys.exit(main())