#!/usr/bin/env python3
"""
verify_snapshot.py

Verifica que un snapshot protegido siga siendo confiable para restaurar:
recalcula el SHA-256 de CADA archivo del snapshot y lo compara contra el
valor registrado en su MANIFEST.json.

Es SOLO LECTURA. No copia, no restaura, no borra, no modifica nada --
ni el snapshot ni el estado actual del CRM.

Se usa en dos momentos (ver ROLLBACK_PLAN.md):
  - Paso 4, ANTES de restaurar: confirmar que el snapshot pre-cutover no
    se corrompió/podó/truncó desde que se creó. Si un solo hash no
    coincide, el rollback se DETIENE -- restaurar desde un snapshot
    corrupto deja un estado peor que el fallido, y encima destruye el
    estado fallido como referencia.
  - Paso 6, DESPUES de restaurar: comparar los archivos YA restaurados en
    su ubicación original contra el manifest (--verify-restored).

Uso:
    python tools/verify_snapshot.py protected_snapshots/pre_cutover_20260820_120000
    python tools/verify_snapshot.py protected_snapshots/pre_cutover_... --verify-restored

Exit code 0 = todo coincide. Exit code 1 = NO restaurar / restauración no confiable.
"""
import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sha256(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify(snapshot_dir, verify_restored=False):
    snapshot_dir = os.path.abspath(snapshot_dir)
    manifest_path = os.path.join(snapshot_dir, 'MANIFEST.json')

    result = {
        'snapshot_dir': snapshot_dir,
        'mode': 'VERIFY_RESTORED_FILES' if verify_restored else 'VERIFY_SNAPSHOT_INTEGRITY',
        'read_only': True,
    }

    if not os.path.isdir(snapshot_dir):
        result['valid'] = False
        result['abort_reason'] = f'No existe el directorio del snapshot: {snapshot_dir}'
        return result, 1

    if not os.path.exists(manifest_path):
        result['valid'] = False
        result['abort_reason'] = (
            'No existe MANIFEST.json -- sin manifest no hay forma de verificar nada. '
            'Este snapshot NO debe usarse para restaurar.')
        return result, 1

    try:
        with open(manifest_path, 'r', encoding='utf-8') as fh:
            manifest = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        result['valid'] = False
        result['abort_reason'] = f'MANIFEST.json ilegible o corrupto: {exc}'
        return result, 1

    header = manifest.get('snapshot') or {}
    files = manifest.get('files') or []

    result['snapshot_timestamp'] = header.get('timestamp')
    result['snapshot_marked_valid_at_creation'] = header.get('valid')
    result['contains_secrets'] = header.get('contains_secrets')

    if header.get('valid') is False:
        result['valid'] = False
        result['abort_reason'] = (
            'El manifest marca este snapshot como INVALIDO desde su creacion '
            f'({header.get("abort_reason")}). No usar para restaurar.')
        return result, 1

    checked = 0
    mismatches = []
    missing = []
    skipped_optional = 0

    for entry in files:
        if entry.get('status') not in ('COPIED', 'HASH_MISMATCH'):
            skipped_optional += 1
            continue

        expected_hash = entry.get('sha256')
        if not expected_hash:
            mismatches.append({'path': entry.get('source_path'),
                               'problema': 'la entrada del manifest no tiene sha256'})
            continue

        if verify_restored:
            # Comparar el archivo YA RESTAURADO en su ubicacion original.
            target = os.path.join(ROOT, entry['source_path'].replace('/', os.sep))
            label = 'restaurado'
        else:
            # Comparar el archivo DENTRO del snapshot.
            target = os.path.join(snapshot_dir,
                                  entry['source_path'].replace('/', os.sep))
            label = 'en snapshot'

        if not os.path.exists(target):
            missing.append({'path': entry.get('source_path'), 'buscado_en': label})
            continue

        actual = _sha256(target)
        checked += 1
        if actual != expected_hash:
            mismatches.append({
                'path': entry.get('source_path'),
                'esperado': expected_hash,
                'actual': actual,
                'ubicacion': label,
            })

    result['files_checked'] = checked
    result['files_missing'] = missing
    result['hash_mismatches'] = mismatches
    result['optional_entries_skipped'] = skipped_optional
    result['valid'] = (not mismatches and not missing)

    if not result['valid']:
        if verify_restored:
            result['abort_reason'] = (
                f'{len(mismatches)} archivo(s) con hash distinto y {len(missing)} faltante(s) '
                'tras la restauracion -- la restauracion NO es confiable. '
                'No dar el rollback por bueno. El estado fallido preservado sigue siendo '
                'la referencia.')
        else:
            result['abort_reason'] = (
                f'{len(mismatches)} archivo(s) con hash distinto y {len(missing)} faltante(s) '
                'en el snapshot -- el snapshot esta corrupto o incompleto. '
                'NO restaurar desde aca (ver ROLLBACK_PLAN.md, paso 4). '
                'Escalar y decidir a mano, con el estado fallido ya preservado.')

    return result, (0 if result['valid'] else 1)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('snapshot_dir',
                        help='Ruta al directorio del snapshot (contiene MANIFEST.json)')
    parser.add_argument('--verify-restored', action='store_true',
                        help='Verifica los archivos YA RESTAURADOS en su ubicacion '
                             'original contra el manifest (paso 6 del rollback), en vez '
                             'de verificar el snapshot en si (paso 4).')
    args = parser.parse_args()

    result, code = verify(args.snapshot_dir, verify_restored=args.verify_restored)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    veredicto = 'OK' if result.get('valid') else 'FALLIDO -- NO CONTINUAR'
    print(f'\nVEREDICTO: {veredicto}', file=sys.stderr)
    return code


if __name__ == '__main__':
    sys.exit(main())
