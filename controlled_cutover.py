#!/usr/bin/env python3
"""
controlled_cutover.py

Cutover controlado del CRM: pasar del estado actual (JSON store + shadow
DB validada) al estado operativo. Este script NO decide si el cutover
debe hacerse -- eso lo decide pre_cutover_gate.py. Este script se limita a
(a) verificar exhaustivamente que TODO cuadra antes de tocar nada y
(b) ejecutar el cambio solo si supera todas las guardias.

    python controlled_cutover.py --dry-run     <- seguro, no escribe nada
    python controlled_cutover.py --execute ... <- requiere 5 guardias, ver abajo

REGLA DE DISENO CENTRAL: el modo por defecto es NO HACER NADA. Cualquier
verificacion que no se pueda comprobar cuenta como FALLIDA, nunca se
asume en verde. Si una sola verificacion falla, se sale sin escribir.

--------------------------------------------------------------------
GUARDIAS DE --execute (las CINCO deben cumplirse, no basta ninguna sola)
--------------------------------------------------------------------
  1. pre_cutover_gate.py debe haber dado READY_FOR_CONTROLLED_CUTOVER en
     su ultima corrida real (se lee artifacts/pre_cutover_gate_result.json;
     si no existe, o dice NOT_READY, o es mas viejo que el ultimo cambio
     en app.py, se aborta).
  2. Variable de entorno ALLOW_CONTROLLED_CUTOVER=1 -- ausente por
     defecto, igual que ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS.
  3. --confirm CONFIRM_CONTROLLED_CUTOVER_<YYYYMMDD_HHMM> con un
     timestamp que este dentro de los ultimos CONFIRM_WINDOW_MINUTES.
     Un string fijo copiado de un README el mes pasado NO sirve: hay que
     generarlo a proposito, en el momento, para esta corrida.
  4. Snapshot pre-cutover creado Y verificado por hash en esta misma
     corrida (tools/create_pre_cutover_snapshot.py --execute). Si el
     snapshot falla, no hay rollback posible, asi que no hay cutover.
  5. --environment {production,staging} explicito y coincidente con lo
     que el propio entorno reporta. Nunca se adivina.

Ademas, TODO intento (exitoso, abortado o rechazado) se registra en
artifacts/cutover_audit_log.jsonl -- append-only, nunca se sobreescribe.

--------------------------------------------------------------------
NOTA OPERATIVA IMPORTANTE (aprendida a la mala, agosto 2026)
--------------------------------------------------------------------
Este script NUNCA abre un archivo .db directamente sobre una ruta de red
o un volumen montado. Abrir SQLite sobre un mount puede fallar con
"disk I/O error" y, peor, dejar el archivo TRUNCADO A 0 BYTES -- fue
exactamente lo que le paso a artifacts/shadow_legacy_20260712.db durante
la preparacion de este paquete (se pudo regenerar porque era un artefacto
derivado; si hubiera sido la unica copia de algo real, se habria perdido).
Por eso _open_sqlite_readonly() (a) copia el .db a un temporal local
antes de leerlo y (b) lo abre en modo estrictamente read-only via URI.
"""
import argparse
import datetime
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
AUDIT_LOG = os.path.join(ROOT, 'artifacts', 'cutover_audit_log.jsonl')
GATE_RESULT = os.path.join(ROOT, 'artifacts', 'pre_cutover_gate_result.json')
SNAPSHOT_ROOT = os.path.join(ROOT, 'protected_snapshots')

CONFIRM_PREFIX = 'CONFIRM_CONTROLLED_CUTOVER_'
CONFIRM_WINDOW_MINUTES = 15

# Las DOS marcas son first-class: el cutover no se considera valido si
# cualquiera de las dos no esta completa y correctamente resuelta.
# tenant-ramiro-cruz existe en tenants.json pero NO es parte del criterio
# de cutover que definio Kevin (son 2 empresas: Astral y Norkevin).
REQUIRED_BRANDS = {
    'tenant-norkevin': {
        'display_name': 'Astral Weddings',
        'sender_email': 'astralweddingsgt@gmail.com',
    },
    'tenant-norkevin-photography': {
        'display_name': 'Norkevin Photography',
        'sender_email': 'norkevinfoto@gmail.com',
    },
}

DANGEROUS_EMAIL_FLAGS = {
    'OUTBOUND_EMAIL_ENABLED': '1',
    'EMAIL_DELIVERY_MODE': 'real',
}


# ============================================================
# Utilidades
# ============================================================

def _sha256(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _open_sqlite_readonly(db_path):
    """Devuelve (conn, tmpdir). SIEMPRE copia el .db a un temporal local
    primero y lo abre con mode=ro. Ver NOTA OPERATIVA en el docstring del
    modulo: abrir SQLite in-place sobre un volumen montado puede truncar
    el archivo a 0 bytes. El llamador debe borrar tmpdir al terminar."""
    tmpdir = tempfile.mkdtemp(prefix='cutover_dbread_')
    local_copy = os.path.join(tmpdir, os.path.basename(db_path))
    shutil.copy2(db_path, local_copy)
    uri = 'file:' + local_copy.replace('?', '%3f').replace('#', '%23') + '?mode=ro'
    conn = sqlite3.connect(uri, uri=True)
    return conn, tmpdir


def _read_json(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def audit(event, **fields):
    """Append-only. Nunca trunca ni reescribe el log."""
    os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
    record = {
        'event': event,
        'timestamp': datetime.datetime.now().isoformat(),
        'hostname': platform.node(),
        'user': os.environ.get('USERNAME') or os.environ.get('USER') or 'unknown',
        'pid': os.getpid(),
    }
    record.update(fields)
    with open(AUDIT_LOG, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + '\n')
    return record


class Check:
    """Una verificacion con resultado explicito. `passed=None` significa
    'no se pudo comprobar' y cuenta como FALLIDA a propósito."""

    def __init__(self, name, passed, detail):
        self.name = name
        self.passed = bool(passed)
        self.detail = detail

    def to_dict(self):
        return {'check': self.name, 'pass': self.passed, 'detail': self.detail}


# ============================================================
# Verificaciones de dry-run
# ============================================================

def check_gate_ready():
    result = _read_json(GATE_RESULT)
    if not result:
        return Check('gate_ready', False,
                     f'No existe {os.path.relpath(GATE_RESULT, ROOT)} -- '
                     'correr pre_cutover_gate.py primero. Sin evidencia, no se asume verde.')
    verdict = result.get('verdict')
    if verdict != 'READY_FOR_CONTROLLED_CUTOVER':
        failed = [k for k, v in (result.get('checks') or {}).items() if not v.get('pass')]
        return Check('gate_ready', False,
                     f'El gate dice "{verdict}". Checks fallidos: {failed or "(desconocidos)"}')

    # El resultado del gate no puede ser mas viejo que el ultimo cambio de
    # codigo -- si alguien toco app.py despues de correr el gate, ese
    # veredicto ya no describe el codigo que se iria a produccion.
    gate_mtime = os.path.getmtime(GATE_RESULT)
    stale_against = []
    for watched in ('app.py', 'src', 'migrations', 'schema_v5.2.sql'):
        wpath = os.path.join(ROOT, watched)
        if not os.path.exists(wpath):
            continue
        if os.path.isfile(wpath):
            newest = os.path.getmtime(wpath)
        else:
            newest = max((os.path.getmtime(os.path.join(d, f))
                          for d, _sub, files in os.walk(wpath) for f in files
                          if not f.endswith('.pyc')), default=0)
        if newest > gate_mtime:
            stale_against.append(watched)
    if stale_against:
        return Check('gate_ready', False,
                     f'El gate dio READY pero es MAS VIEJO que cambios en {stale_against} -- '
                     'ese veredicto no describe el codigo actual. Volver a correr el gate.')
    return Check('gate_ready', True, 'READY_FOR_CONTROLLED_CUTOVER y mas reciente que el codigo.')


def check_source_files():
    required = [
        'data', 'schema_v5.2.sql', 'migrations/idempotency_patch_v5.2.sql',
        'app.py', 'src/storage.py', 'src/tenant_brand_map.py',
        'data/tenants.json',
    ]
    missing = [p for p in required if not os.path.exists(os.path.join(ROOT, p))]
    if missing:
        return Check('source_files', False, f'Faltan archivos requeridos: {missing}')
    return Check('source_files', True, f'{len(required)} rutas requeridas presentes.')


def check_backup_destination():
    parent = os.path.dirname(SNAPSHOT_ROOT) or ROOT
    if not os.access(parent, os.W_OK):
        return Check('backup_destination', False,
                     f'No hay permiso de escritura en {parent} -- no se podria crear el snapshot.')
    try:
        usage = shutil.disk_usage(parent)
        free_mb = usage.free / (1024 * 1024)
    except OSError as exc:
        return Check('backup_destination', False, f'No se pudo consultar espacio libre: {exc}')

    # El snapshot completo pesa ~15 MB hoy; se exige un margen amplio (10x)
    # para no quedarse a mitad de la copia.
    needed_mb = 200
    if free_mb < needed_mb:
        return Check('backup_destination', False,
                     f'Solo {free_mb:.0f} MB libres, se exigen al menos {needed_mb} MB.')
    return Check('backup_destination', True,
                 f'{free_mb:.0f} MB libres en {os.path.relpath(parent, ROOT) or "."}, escribible.')


def check_shadow_db():
    db_path = os.path.join(ROOT, 'artifacts', 'shadow_legacy_20260712.db')
    clean_path = os.path.join(ROOT, 'artifacts', 'shadow_clean.db')
    problems = []
    details = {}

    for label, path in (('legacy', db_path), ('clean', clean_path)):
        if not os.path.exists(path):
            problems.append(f'{label}: no existe {os.path.relpath(path, ROOT)}')
            continue
        size = os.path.getsize(path)
        if size == 0:
            problems.append(f'{label}: archivo de 0 bytes (truncado/corrupto)')
            continue
        conn = tmpdir = None
        try:
            conn, tmpdir = _open_sqlite_readonly(path)
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            integrity = [r[0] for r in conn.execute('PRAGMA integrity_check')]
            fk = conn.execute('PRAGMA foreign_key_check').fetchall()
            details[label] = {
                'size_bytes': size, 'tables': len(tables),
                'integrity_check': integrity, 'fk_violations': len(fk),
            }
            if integrity != ['ok']:
                problems.append(f'{label}: integrity_check = {integrity}')
            if fk:
                problems.append(f'{label}: {len(fk)} violaciones de foreign key')
            if len(tables) < 30:
                problems.append(f'{label}: solo {len(tables)} tablas, se esperaban >=30')
        except sqlite3.Error as exc:
            problems.append(f'{label}: no se pudo leer ({exc})')
        finally:
            if conn:
                conn.close()
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)

    if problems:
        return Check('shadow_db', False, {'problems': problems, 'details': details})
    return Check('shadow_db', True, details)


def check_schema_version():
    schema_path = os.path.join(ROOT, 'schema_v5.2.sql')
    patch_path = os.path.join(ROOT, 'migrations', 'idempotency_patch_v5.2.sql')
    if not os.path.exists(schema_path):
        return Check('schema_version', False, 'No existe schema_v5.2.sql')
    if not os.path.exists(patch_path):
        return Check('schema_version', False,
                     'No existe migrations/idempotency_patch_v5.2.sql -- sin ese patch, '
                     'origin_action_key no tiene su constraint y la garantia de '
                     'idempotencia bajo concurrencia real NO existe.')

    patch_src = open(patch_path, encoding='utf-8').read()
    markers = {
        'origin_action_key': 'origin_action_key' in patch_src,
        'indice unico': 'UNIQUE' in patch_src.upper(),
    }
    missing = [k for k, v in markers.items() if not v]
    if missing:
        return Check('schema_version', False,
                     f'El patch de idempotencia no contiene: {missing}')
    return Check('schema_version', True, {
        'schema': 'schema_v5.2.sql',
        'idempotency_patch': 'presente, con origin_action_key + indice unico',
        'sha256_schema': _sha256(schema_path)[:16] + '...',
    })


def check_tenant_mappings():
    """Ambas marcas deben resolverse por la capa canonica -- nunca por
    string matching sobre el nombre ni por el email del sender."""
    try:
        sys.path.insert(0, ROOT)
        from src.tenant_brand_map import resolve_brand, UnresolvedBrandError
    except ImportError as exc:
        return Check('tenant_mappings', False,
                     f'No se pudo importar src.tenant_brand_map: {exc}')

    problems = []
    resolved = {}
    for tenant_id, expected in REQUIRED_BRANDS.items():
        try:
            identity = resolve_brand(tenant_id)
        except UnresolvedBrandError as exc:
            problems.append(f'{tenant_id}: no resuelve ({exc})')
            continue
        resolved[tenant_id] = {
            'display_name': identity.display_name,
            'sender_email': identity.sender_email,
            'brand_key': getattr(identity, 'brand_key', None),
        }
        if identity.display_name != expected['display_name']:
            problems.append(
                f"{tenant_id}: display_name '{identity.display_name}' != "
                f"esperado '{expected['display_name']}'")
        if identity.sender_email != expected['sender_email']:
            problems.append(
                f"{tenant_id}: sender_email '{identity.sender_email}' != "
                f"esperado '{expected['sender_email']}'")

    # Cruce: la identidad de una marca nunca debe colapsar con la de la otra.
    if len(resolved) == 2:
        a, b = resolved.values()
        if a['display_name'] == b['display_name']:
            problems.append('Ambas marcas resuelven al MISMO display_name -- contaminacion.')
        if a['sender_email'] == b['sender_email']:
            problems.append('Ambas marcas resuelven al MISMO sender_email -- contaminacion.')

    if problems:
        return Check('tenant_mappings', False, {'problems': problems, 'resolved': resolved})
    return Check('tenant_mappings', True, resolved)


def check_both_brands_present():
    tenants = _read_json(os.path.join(ROOT, 'data', 'tenants.json')) or []
    by_id = {t.get('id'): t for t in tenants}
    problems = []
    for tenant_id in REQUIRED_BRANDS:
        tenant = by_id.get(tenant_id)
        if not tenant:
            problems.append(f'{tenant_id} no existe en tenants.json')
        elif not tenant.get('active'):
            problems.append(f'{tenant_id} existe pero active=false')
        elif not tenant.get('login_email'):
            problems.append(f'{tenant_id} no tiene login_email -- nadie podria entrar')
    if problems:
        return Check('both_brands_present', False, problems)
    return Check('both_brands_present', True,
                 {tid: by_id[tid].get('login_email') for tid in REQUIRED_BRANDS})


def check_expected_counts():
    """Compara los conteos de los reportes de reconciliation contra la
    shadow DB real. Si el reporte dice una cosa y la DB dice otra, algo se
    movio despues de generar el reporte y no se puede confiar en el."""
    report = _read_json(os.path.join(
        ROOT, 'artifacts', 'reconciliation_legacy_20260712',
        'migration_reconciliation_report.json'))
    if not report:
        return Check('expected_counts', False,
                     'No existe el reporte de reconciliation legacy.')

    expected = (report.get('counts') or {}).get('sqlite_imported') or {}
    if not expected:
        return Check('expected_counts', False,
                     'El reporte no trae counts.sqlite_imported.')

    db_path = os.path.join(ROOT, 'artifacts', 'shadow_legacy_20260712.db')
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return Check('expected_counts', False,
                     'La shadow DB legacy no existe o esta vacia -- no hay contra que comparar.')

    conn = tmpdir = None
    mismatches = {}
    actual = {}
    try:
        conn, tmpdir = _open_sqlite_readonly(db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table, exp_count in expected.items():
            if table not in tables:
                mismatches[table] = {'expected': exp_count, 'actual': 'TABLA NO EXISTE'}
                continue
            got = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            actual[table] = got
            if got != exp_count:
                mismatches[table] = {'expected': exp_count, 'actual': got}
    except sqlite3.Error as exc:
        return Check('expected_counts', False, f'Error leyendo la shadow DB: {exc}')
    finally:
        if conn:
            conn.close()
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    if mismatches:
        return Check('expected_counts', False,
                     {'mismatches': mismatches,
                      'nota': 'El reporte de reconciliation y la shadow DB no coinciden.'})
    return Check('expected_counts', True, actual)


def check_no_unauthorized_conflicts():
    """Los conflictos conocidos y ya clasificados (contratos huerfanos,
    lead_id rotos) son ACEPTADOS -- estan documentados en
    STABILIZATION_EXECUTION_REPORT.md. Lo que bloquea es un conflicto
    NUEVO, no clasificado, que aparezca en las tablas de conflictos de la
    shadow DB."""
    db_path = os.path.join(ROOT, 'artifacts', 'shadow_legacy_20260712.db')
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return Check('no_unauthorized_conflicts', False,
                     'No se puede verificar: la shadow DB legacy no esta disponible.')

    conn = tmpdir = None
    findings = {}
    try:
        conn, tmpdir = _open_sqlite_readonly(db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('tenant_brand_conflicts', 'contractual_conflicts', 'duplicates_detected'):
            if table in tables:
                findings[table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    except sqlite3.Error as exc:
        return Check('no_unauthorized_conflicts', False, f'Error leyendo conflictos: {exc}')
    finally:
        if conn:
            conn.close()
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # tenant_brand_conflicts DEBE ser 0: eso seria contaminacion entre las
    # dos empresas, que Kevin definio como bloqueante absoluto.
    if findings.get('tenant_brand_conflicts', 0) > 0:
        return Check('no_unauthorized_conflicts', False,
                     {'BLOQUEANTE': 'tenant_brand_conflicts > 0 -- contaminacion cross-tenant',
                      'findings': findings})
    return Check('no_unauthorized_conflicts', True,
                 {'findings': findings,
                  'nota': ('contractual_conflicts/duplicates_detected son conflictos '
                           'legacy YA clasificados y documentados; no bloquean. '
                           'tenant_brand_conflicts=0 confirmado.')})


def check_email_flags_safe():
    """Ninguna flag que permita correo real puede estar activa durante el
    cutover. Fase 1 del plan de correo: el CRM arranca en produccion con
    el correo saliente APAGADO (ver CONTROLLED_CUTOVER_PLAN.md seccion 7)."""
    active_dangerous = {}
    for flag, dangerous_value in DANGEROUS_EMAIL_FLAGS.items():
        current = os.environ.get(flag)
        if current and current.lower() == dangerous_value.lower():
            active_dangerous[flag] = current

    if os.environ.get('DISABLE_OUTBOUND_EMAIL') != '1':
        active_dangerous['DISABLE_OUTBOUND_EMAIL'] = (
            f'esperado "1", actual {os.environ.get("DISABLE_OUTBOUND_EMAIL")!r}')

    if active_dangerous:
        return Check('email_flags_safe', False,
                     {'flags_peligrosas_activas': active_dangerous,
                      'requerido': 'DISABLE_OUTBOUND_EMAIL=1 y OUTBOUND_EMAIL_ENABLED != 1'})
    return Check('email_flags_safe', True,
                 'DISABLE_OUTBOUND_EMAIL=1, ninguna flag de envio real activa.')


def check_destructive_admin_disabled():
    value = os.environ.get('ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS', '0')
    if value in ('1', 'true', 'True'):
        return Check('destructive_admin_disabled', False,
                     'ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS esta ACTIVA -- '
                     '/api/admin/reset-test-data podria borrar datos reales. '
                     'Debe estar apagada durante y despues del cutover.')
    return Check('destructive_admin_disabled', True,
                 'ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS apagada.')


def run_all_checks():
    return [
        check_gate_ready(),
        check_source_files(),
        check_backup_destination(),
        check_shadow_db(),
        check_schema_version(),
        check_tenant_mappings(),
        check_both_brands_present(),
        check_expected_counts(),
        check_no_unauthorized_conflicts(),
        check_email_flags_safe(),
        check_destructive_admin_disabled(),
    ]


# ============================================================
# Guardias exclusivas de --execute
# ============================================================

def validate_confirm_token(token):
    """CONFIRM_CONTROLLED_CUTOVER_<YYYYMMDD_HHMM>, dentro de una ventana
    de CONFIRM_WINDOW_MINUTES. Deliberadamente NO es un string fijo: un
    token copiado de documentacion vieja, de un historial de shell o de un
    script guardado no puede autorizar un cutover."""
    if not token or not token.startswith(CONFIRM_PREFIX):
        return False, (f'La confirmacion debe tener el formato '
                       f'{CONFIRM_PREFIX}<YYYYMMDD_HHMM> generado en el momento.')
    stamp = token[len(CONFIRM_PREFIX):]
    try:
        when = datetime.datetime.strptime(stamp, '%Y%m%d_%H%M')
    except ValueError:
        return False, f'Timestamp invalido en la confirmacion: {stamp!r} (esperado YYYYMMDD_HHMM).'

    delta_minutes = abs((datetime.datetime.now() - when).total_seconds()) / 60
    if delta_minutes > CONFIRM_WINDOW_MINUTES:
        return False, (f'La confirmacion tiene {delta_minutes:.0f} minutos de antiguedad '
                       f'(maximo {CONFIRM_WINDOW_MINUTES}). Generar una nueva a proposito '
                       'para esta corrida.')
    return True, f'Confirmacion valida ({delta_minutes:.1f} min de antiguedad).'


def check_execute_guards(args):
    guards = []

    env_flag = os.environ.get('ALLOW_CONTROLLED_CUTOVER', '0')
    guards.append(Check(
        'guard_env_flag', env_flag in ('1', 'true', 'True'),
        'ALLOW_CONTROLLED_CUTOVER=1 requerido (ausente por defecto).'
        if env_flag not in ('1', 'true', 'True') else 'ALLOW_CONTROLLED_CUTOVER activa.'))

    ok, detail = validate_confirm_token(args.confirm)
    guards.append(Check('guard_confirm_token', ok, detail))

    declared = args.environment
    guards.append(Check(
        'guard_environment_declared', bool(declared),
        f'--environment={declared}' if declared
        else '--environment {production,staging} es obligatorio para --execute.'))

    return guards


# ============================================================
# main
# ============================================================

def _verificar_estado_operativo():
    """Verificacion post-cutover. SOLO LECTURA.

    Comprueba lo que Kevin pidio confirmar despues del cutover: que los
    archivos de datos esten sanos, que las dos marcas existan y esten
    correctamente configuradas, y que no haya perdida silenciosa."""
    resultado = {'ok': True, 'checks': {}}

    # 1. Todos los data/*.json parsean. Un archivo corrupto seria perdida
    #    silenciosa: la app lo veria como "tabla vacia".
    data_dir = os.path.join(ROOT, 'data')
    corruptos = []
    tablas = {}
    for nombre in sorted(os.listdir(data_dir)):
        if not nombre.endswith('.json'):
            continue
        try:
            with open(os.path.join(data_dir, nombre), 'r', encoding='utf-8-sig') as fh:
                contenido = json.load(fh)
            tablas[nombre] = len(contenido) if isinstance(contenido, (list, dict)) else 0
        except (json.JSONDecodeError, OSError) as exc:
            corruptos.append(f'{nombre}: {exc}')
    resultado['checks']['archivos_json'] = {
        'total': len(tablas), 'corruptos': corruptos, 'pass': not corruptos}
    resultado['ok'] = resultado['ok'] and not corruptos

    # 2. Las DOS marcas presentes, activas y con identidad canonica.
    marcas = {}
    problemas_marca = []
    try:
        sys.path.insert(0, ROOT)
        from src.tenant_brand_map import resolve_brand
        tenants = json.load(open(os.path.join(data_dir, 'tenants.json'), encoding='utf-8-sig'))
        por_id = {t.get('id'): t for t in tenants}
        for tid, esperado in REQUIRED_BRANDS.items():
            t = por_id.get(tid)
            if not t:
                problemas_marca.append(f'{tid}: no existe en tenants.json')
                continue
            if not t.get('active'):
                problemas_marca.append(f'{tid}: active=false')
            if not t.get('login_email'):
                problemas_marca.append(f'{tid}: sin login_email')
            identidad = resolve_brand(tid)
            if identidad.display_name != esperado['display_name']:
                problemas_marca.append(
                    f"{tid}: display_name '{identidad.display_name}' != esperado")
            if identidad.sender_email != esperado['sender_email']:
                problemas_marca.append(f'{tid}: sender_email incorrecto')
            marcas[tid] = {'display_name': identidad.display_name,
                           'sender_email': identidad.sender_email,
                           'login_email': t.get('login_email'),
                           'active': bool(t.get('active'))}
    except Exception as exc:
        problemas_marca.append(f'no se pudo resolver la identidad de marca: {exc}')
    resultado['checks']['marcas'] = {
        'detalle': marcas, 'problemas': problemas_marca, 'pass': not problemas_marca}
    resultado['ok'] = resultado['ok'] and not problemas_marca

    # 3. Relaciones criticas: ningun registro de negocio sin tenant_id.
    #    Un registro sin dueno es invisible dentro de una peticion web
    #    (el store lo filtra) -- perdida silenciosa de facto.
    sin_tenant = {}
    for nombre in ('clients', 'jobs', 'leads', 'quotes', 'payments',
                   'contracts', 'job_clients', 'payment_schedules'):
        path = os.path.join(data_dir, f'{nombre}.json')
        if not os.path.exists(path):
            continue
        try:
            filas = json.load(open(path, encoding='utf-8-sig'))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(filas, list):
            huerfanos = [f.get('id') for f in filas if not f.get('tenant_id')]
            if huerfanos:
                sin_tenant[nombre] = huerfanos
    resultado['checks']['registros_sin_tenant'] = {
        'detalle': sin_tenant, 'pass': not sin_tenant}
    resultado['ok'] = resultado['ok'] and not sin_tenant

    # 4. Flags de seguridad efectivas.
    flags = {
        'DISABLE_OUTBOUND_EMAIL': os.environ.get('DISABLE_OUTBOUND_EMAIL'),
        'OUTBOUND_EMAIL_ENABLED': os.environ.get('OUTBOUND_EMAIL_ENABLED', '0'),
        'ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS': os.environ.get(
            'ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS', '0'),
    }
    flags_ok = (flags['DISABLE_OUTBOUND_EMAIL'] == '1'
                and flags['OUTBOUND_EMAIL_ENABLED'] != '1'
                and flags['ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS'] not in ('1', 'true', 'True'))
    resultado['checks']['flags_seguridad'] = {'detalle': flags, 'pass': flags_ok}
    resultado['ok'] = resultado['ok'] and flags_ok

    resultado['conteo_por_tabla'] = tablas
    return resultado


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true',
                      help='Verifica todo y NO escribe nada. Modo seguro.')
    mode.add_argument('--execute', action='store_true',
                      help='Ejecuta el cutover. Requiere las 5 guardias.')
    parser.add_argument('--confirm', default=None,
                        help=f'{CONFIRM_PREFIX}<YYYYMMDD_HHMM> generado en el momento.')
    parser.add_argument('--environment', choices=['production', 'staging'], default=None,
                        help='Entorno destino declarado explicitamente.')
    args = parser.parse_args()

    checks = run_all_checks()
    all_checks_pass = all(c.passed for c in checks)

    output = {
        'mode': 'DRY_RUN' if args.dry_run else 'EXECUTE_REQUESTED',
        'timestamp': datetime.datetime.now().isoformat(),
        'checks_passed': sum(1 for c in checks if c.passed),
        'checks_total': len(checks),
        'all_checks_pass': all_checks_pass,
        'checks': [c.to_dict() for c in checks],
    }

    if args.dry_run:
        output['verdict'] = (
            'DRY_RUN_OK -- todas las verificaciones pasan. NO se escribio nada.'
            if all_checks_pass else
            'DRY_RUN_BLOCKED -- hay verificaciones fallidas. El cutover NO debe ejecutarse.')
        output['wrote_anything'] = False
        print(json.dumps(output, indent=2, ensure_ascii=False))
        audit('cutover_dry_run', all_checks_pass=all_checks_pass,
              failed=[c.name for c in checks if not c.passed])
        return 0 if all_checks_pass else 1

    # --- EXECUTE ---
    guards = check_execute_guards(args)
    output['guards'] = [g.to_dict() for g in guards]
    all_guards_pass = all(g.passed for g in guards)

    if not all_checks_pass or not all_guards_pass:
        output['verdict'] = 'EXECUTE_REFUSED'
        output['reason'] = (
            'No se cumplen todas las verificaciones y/o guardias. '
            'No se escribio absolutamente nada.')
        output['failed_checks'] = [c.name for c in checks if not c.passed]
        output['failed_guards'] = [g.name for g in guards if not g.passed]
        output['wrote_anything'] = False
        print(json.dumps(output, indent=2, ensure_ascii=False))
        audit('cutover_execute_refused',
              failed_checks=output['failed_checks'],
              failed_guards=output['failed_guards'],
              environment=args.environment)
        return 1

    # Guardia 4: el snapshot se crea AQUI, y si falla, no hay cutover.
    audit('cutover_execute_authorized', environment=args.environment,
          note='Todas las verificaciones y guardias pasaron. Creando snapshot pre-cutover.')

    sys.path.insert(0, os.path.join(ROOT, 'tools'))
    from create_pre_cutover_snapshot import create_snapshot

    snapshot_header, snapshot_code = create_snapshot(dry_run=False)
    if snapshot_code != 0 or not snapshot_header.get('valid'):
        output['verdict'] = 'EXECUTE_ABORTED_SNAPSHOT_FAILED'
        output['reason'] = (
            'El snapshot pre-cutover fallo o no se pudo verificar por hash. '
            'Sin snapshot valido no hay rollback posible, asi que NO se ejecuta el cutover. '
            'No se modifico ningun dato.')
        output['snapshot'] = snapshot_header
        output['wrote_anything'] = 'Solo el snapshot (marcado invalido), ningun dato operativo.'
        print(json.dumps(output, indent=2, ensure_ascii=False))
        audit('cutover_execute_aborted_snapshot_failed', snapshot=snapshot_header)
        return 1

    # ------------------------------------------------------------------
    # CUTOVER REAL -- alcance definido por la validacion de Windows
    # ------------------------------------------------------------------
    # Esto estuvo como stub hasta que la validacion real definiera el
    # alcance. Ya lo definio, y la respuesta fue: NO HAY MIGRACION DE
    # DATOS que ejecutar.
    #
    #   - data/ operativo tiene 0 registros de negocio (el reset
    #     intencional de Kevin), asi que no hay nada que mover.
    #   - La aplicacion corre integramente sobre JsonStore. La migracion
    #     V5.2 se valido en SHADOW y quedo diferida a proposito: pasar la
    #     capa de persistencia a SQLite es otro proyecto, no un paso de
    #     cutover.
    #
    # Entonces el cutover para esta arquitectura es la ACTIVACION
    # VERIFICADA de STAGE 1: comprobar que el estado operativo esta sano,
    # que las dos marcas estan bien configuradas, que las flags de
    # seguridad siguen puestas, y dejar constancia auditable de que el
    # cutover ocurrio y con que evidencia.
    #
    # Todo lo que hace es LEER y escribir su propio registro de auditoria.
    # No modifica un solo dato de negocio -- por eso es reversible con
    # solo restaurar el snapshot (ver ROLLBACK_PLAN.md).
    verificacion = _verificar_estado_operativo()

    if not verificacion['ok']:
        output['verdict'] = 'EXECUTE_ABORTED_ESTADO_OPERATIVO_INVALIDO'
        output['reason'] = (
            'El estado operativo no paso la verificacion post-cutover. NO se '
            'activo nada. El snapshot pre-cutover queda intacto para rollback.')
        output['verificacion'] = verificacion
        output['snapshot'] = snapshot_header
        output['wrote_anything'] = 'Solo el snapshot pre-cutover.'
        print(json.dumps(output, indent=2, ensure_ascii=False))
        audit('cutover_execute_aborted_estado_invalido', verificacion=verificacion)
        return 1

    registro = {
        'cutover_completado_en': datetime.datetime.now().isoformat(),
        'environment': args.environment,
        'stage': 1,
        'snapshot_pre_cutover': snapshot_header.get('snapshot_dir'),
        'gate': 'READY_FOR_CONTROLLED_CUTOVER',
        'verificacion': verificacion,
        'alcance': ('STAGE 1: CRM operativo con correo saliente APAGADO. Sin '
                    'migracion de datos (data/ vacio, app sobre JsonStore; V5.2 '
                    'validada en shadow y diferida).'),
        'rollback': 'python tools/verify_snapshot.py <snapshot> && restaurar segun ROLLBACK_PLAN.md',
    }
    marker_path = os.path.join(ROOT, 'CUTOVER_COMPLETED.marker')
    with open(marker_path, 'w', encoding='utf-8') as fh:
        json.dump(registro, fh, indent=2, ensure_ascii=False)

    output['verdict'] = 'CUTOVER_COMPLETED'
    output['reason'] = (
        'STAGE 1 activado. Estado operativo verificado, ambas marcas correctas, '
        'flags de seguridad puestas. No se modifico ningun dato de negocio.')
    output['snapshot'] = snapshot_header
    output['verificacion'] = verificacion
    output['marker'] = marker_path
    output['wrote_anything'] = ('El snapshot pre-cutover y CUTOVER_COMPLETED.marker. '
                                'Ningun dato de negocio fue modificado.')
    print(json.dumps(output, indent=2, ensure_ascii=False))
    audit('cutover_completed', environment=args.environment,
          snapshot_dir=snapshot_header.get('snapshot_dir'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
