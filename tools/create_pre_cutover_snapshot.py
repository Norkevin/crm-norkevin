#!/usr/bin/env python3
"""
create_pre_cutover_snapshot.py

Snapshot COMPLETO y PROTEGIDO del estado del CRM justo antes de un cutover
controlado. Esta es LA copia de la que depende el rollback entero
(ver ROLLBACK_PLAN.md) -- si esto no existe o esta incompleto, el cutover
NO debe ejecutarse.

Diseno (agosto 2026, bloque "paquete de controlled cutover"):

  - Destino: protected_snapshots/pre_cutover_<timestamp>/
    Ese directorio raiz esta en src/storage.PROTECTED_PATHS, asi que
    _prune_backups() nunca puede alcanzarlo, sin importar cuantos ciclos
    de pruebas se corran despues.

  - Solo COPIA. Nunca mueve, nunca borra, nunca sobreescribe un snapshot
    existente (si el directorio destino ya existe, aborta).

  - Manifest (MANIFEST.json) con, por cada archivo copiado:
      source_path (relativo al repo), snapshot_path, sha256, size_bytes,
      source_mtime
    mas metadatos globales: timestamp, hostname, total de archivos/bytes,
    y la lista de categorias cubiertas.

  - Verificacion post-copia OBLIGATORIA: se recalcula el SHA-256 de cada
    archivo YA COPIADO y se compara contra el del origen. Si un solo
    archivo no coincide, el snapshot se marca INVALIDO en el manifest y
    el script sale con codigo 1 -- controlled_cutover.py trata eso como
    abort inmediato.

SECRETOS: los archivos de credenciales (tokens de Google, llaves de
Recurrente, .env) SI se copian -- son necesarios para un rollback real,
un CRM restaurado sin sus credenciales no vuelve a funcionar. Pero su
CONTENIDO nunca se imprime ni se loguea: en el manifest solo aparecen
ruta, tamano y hash. La lista de estos archivos se marca
`contains_secrets: true` para que quede explicito que ese snapshot debe
tratarse con el mismo cuidado que el .env de produccion.

Uso:
    python tools/create_pre_cutover_snapshot.py --dry-run
    python tools/create_pre_cutover_snapshot.py --execute

NO ejecutar --execute hasta que pre_cutover_gate.py de
READY_FOR_CONTROLLED_CUTOVER (ver CONTROLLED_CUTOVER_PLAN.md, fase 0).
"""
import argparse
import datetime
import hashlib
import json
import os
import platform
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT_ROOT = os.path.join(ROOT, 'protected_snapshots')

# Que se copia, agrupado por categoria. Cada entrada es (categoria,
# ruta relativa al repo, es_obligatorio, contiene_secretos).
#
# "es_obligatorio" = si falta, el snapshot es invalido y el cutover debe
# abortar. Un archivo opcional que falta solo se anota en el manifest
# (ej. settings_tenant-norkevin-photography.json todavia no existe, ver
# STABILIZATION_EXECUTION_REPORT.md seccion 13 punto 7).
SNAPSHOT_SPEC = [
    # --- Datos operacionales completos ---
    ('data', 'data', True, False),   # directorio entero: *.json, backups/, seeds/, uploads/

    # --- Bases de datos SQLite existentes ---
    ('sqlite_db', 'data/crm.db', False, False),
    ('sqlite_db', 'data/crm_v5_shadow.db', False, False),

    # --- Schema y migraciones (para reconstruir/verificar la version) ---
    ('schema', 'schema_v5.2.sql', True, False),
    ('schema', 'schema_v5.1_BASELINE.sql', False, False),
    ('schema', 'migrations', True, False),

    # --- Configuracion de entorno ---
    ('config', '.env', False, True),
    ('config', '.env.example', False, False),
    ('config', 'render.yaml', False, False),
    ('config', 'Procfile', False, False),
    ('config', 'pytest.ini', False, False),

    # --- Evidencia historica que nunca debe perderse ---
    ('evidence', 'evidencia', False, False),
]

# Archivos DENTRO de data/ que contienen secretos -- se copian igual
# (hacen falta para el rollback), pero se marcan para que el manifest lo
# diga explicitamente y nadie trate este snapshot como algo publicable.
SECRET_FILENAME_MARKERS = (
    'google_token', 'google_oauth_state', 'recurrente_credentials', '.env',
)


def _sha256(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _looks_secret(path):
    name = os.path.basename(path).lower()
    return any(marker in name for marker in SECRET_FILENAME_MARKERS)


def _iter_files(abs_path):
    """Devuelve rutas absolutas de archivos. Si abs_path es un archivo,
    devuelve ese; si es un directorio, camina recursivamente."""
    if os.path.isfile(abs_path):
        yield abs_path
        return
    for dirpath, dirnames, filenames in os.walk(abs_path):
        # __pycache__ y .pyc no aportan nada a un rollback y solo hacen
        # ruido/peso en el manifest.
        dirnames[:] = [d for d in dirnames if d != '__pycache__']
        for name in sorted(filenames):
            if name.endswith('.pyc'):
                continue
            yield os.path.join(dirpath, name)


def plan_snapshot():
    """Calcula QUE se copiaria, sin copiar nada. Devuelve
    (entries, missing_required, total_bytes)."""
    entries = []
    missing_required = []
    total_bytes = 0

    for category, rel_path, required, category_has_secrets in SNAPSHOT_SPEC:
        abs_path = os.path.join(ROOT, rel_path)
        if not os.path.exists(abs_path):
            if required:
                missing_required.append(rel_path)
            else:
                entries.append({
                    'category': category,
                    'source_path': rel_path,
                    'status': 'MISSING_OPTIONAL',
                })
            continue

        for file_abs in _iter_files(abs_path):
            file_rel = os.path.relpath(file_abs, ROOT)
            size = os.path.getsize(file_abs)
            total_bytes += size
            entries.append({
                'category': category,
                'source_path': file_rel.replace(os.sep, '/'),
                'size_bytes': size,
                'source_mtime': datetime.datetime.fromtimestamp(
                    os.path.getmtime(file_abs)).isoformat(),
                'contains_secrets': category_has_secrets or _looks_secret(file_abs),
                'status': 'PLANNED',
            })

    return entries, missing_required, total_bytes


def create_snapshot(dry_run=True):
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    dest_dir = os.path.join(SNAPSHOT_ROOT, f'pre_cutover_{timestamp}')

    entries, missing_required, total_bytes = plan_snapshot()

    header = {
        'snapshot_dir': os.path.relpath(dest_dir, ROOT).replace(os.sep, '/'),
        'timestamp': datetime.datetime.now().isoformat(),
        'hostname': platform.node(),
        'python_version': sys.version.split()[0],
        'repo_root': ROOT,
        'mode': 'DRY_RUN' if dry_run else 'EXECUTE',
        'files_planned': sum(1 for e in entries if e.get('status') == 'PLANNED'),
        'total_bytes_planned': total_bytes,
        'missing_required': missing_required,
        'categories': sorted({e['category'] for e in entries}),
        'protected': True,
        'protected_reason': (
            'protected_snapshots/ esta en src/storage.PROTECTED_PATHS -- '
            '_prune_backups() nunca puede alcanzarlo.'
        ),
        'contains_secrets': any(e.get('contains_secrets') for e in entries),
    }

    if missing_required:
        header['valid'] = False
        header['abort_reason'] = (
            f'Faltan archivos OBLIGATORIOS: {missing_required}. '
            'El snapshot seria incompleto y el rollback no estaria garantizado.'
        )
        print(json.dumps(header, indent=2, ensure_ascii=False))
        return header, 1

    if dry_run:
        header['valid'] = True
        header['note'] = (
            'DRY RUN -- no se copio ni se escribio absolutamente nada. '
            'Para crear el snapshot de verdad: --execute'
        )
        print(json.dumps(header, indent=2, ensure_ascii=False))
        return header, 0

    # --- EXECUTE ---
    if os.path.exists(dest_dir):
        header['valid'] = False
        header['abort_reason'] = (
            f'El directorio destino ya existe: {dest_dir}. '
            'Un snapshot NUNCA se sobreescribe -- abortado sin tocar nada.'
        )
        print(json.dumps(header, indent=2, ensure_ascii=False))
        return header, 1

    os.makedirs(dest_dir, exist_ok=False)

    manifest_files = []
    verification_failures = []

    for entry in entries:
        if entry.get('status') != 'PLANNED':
            manifest_files.append(entry)
            continue

        rel = entry['source_path']
        src_abs = os.path.join(ROOT, rel)
        dst_abs = os.path.join(dest_dir, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(dst_abs), exist_ok=True)

        source_hash = _sha256(src_abs)
        shutil.copy2(src_abs, dst_abs)
        copied_hash = _sha256(dst_abs)

        verified = (source_hash == copied_hash)
        if not verified:
            verification_failures.append(rel)

        entry.update({
            'status': 'COPIED' if verified else 'HASH_MISMATCH',
            'sha256': source_hash,
            'sha256_verified_in_snapshot': copied_hash,
            'verified': verified,
            'snapshot_path': os.path.relpath(dst_abs, ROOT).replace(os.sep, '/'),
        })
        manifest_files.append(entry)

    header['files_copied'] = sum(1 for e in manifest_files if e.get('status') == 'COPIED')
    header['total_bytes_copied'] = sum(e.get('size_bytes', 0) for e in manifest_files
                                       if e.get('status') == 'COPIED')
    header['verification_failures'] = verification_failures
    header['valid'] = (not verification_failures)
    if verification_failures:
        header['abort_reason'] = (
            f'{len(verification_failures)} archivo(s) copiados con hash distinto al '
            'origen -- el snapshot NO es confiable para rollback. NO se borro nada: '
            'el snapshot queda en disco marcado como invalido para inspeccion.'
        )

    manifest = {'snapshot': header, 'files': manifest_files}
    manifest_path = os.path.join(dest_dir, 'MANIFEST.json')
    with open(manifest_path, 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    # El manifest se imprime SIN la lista completa de archivos (puede tener
    # cientos) y sin contenido de ningun archivo -- solo el encabezado.
    print(json.dumps(header, indent=2, ensure_ascii=False))
    print(f'\nManifest completo: {os.path.relpath(manifest_path, ROOT)}', file=sys.stderr)

    return header, (0 if header['valid'] else 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--dry-run', action='store_true',
                       help='Calcula y muestra que se copiaria, sin escribir nada.')
    group.add_argument('--execute', action='store_true',
                       help='Crea el snapshot de verdad en protected_snapshots/.')
    args = parser.parse_args()

    _header, code = create_snapshot(dry_run=not args.execute)
    return code


if __name__ == '__main__':
    sys.exit(main())
