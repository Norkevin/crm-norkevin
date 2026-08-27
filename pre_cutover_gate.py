#!/usr/bin/env python3
"""
pre_cutover_gate.py

Chequeo NO destructivo, puramente de lectura. No corre pytest, no toca
data/, no envia nada, no hace deployment -- solo lee lo que YA se generó
(reportes de reconciliation, resultados de la suite Flask/pytest si
existen, y hace unos greps estaticos) y decide, con criterios explícitos y
verificables, si el estado es:

    READY_FOR_CONTROLLED_CUTOVER
    NOT_READY_FOR_CUTOVER

La idea (Kevin, prioridad 11): que nadie pueda hacer cutover basandose en
una impresion subjetiva de "ya quedo verde". Este script es el arbitro; si
falta evidencia de un check, cuenta como NO cumplido, nunca se asume.

Uso:
    python pre_cutover_gate.py
    python pre_cutover_gate.py --validation-dir artifacts/pre_cutover_validation/latest
"""
import argparse
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def _read_json(path):
    """Lee JSON tolerando BOM.

    BUG encontrado en la corrida final de Windows (agosto 2026): este
    lector usaba encoding='utf-8' puro. PowerShell escribe sus archivos
    con `Out-File -Encoding utf8`, que en Windows PowerShell 5.1 emite
    UTF-8 **con BOM** (EF BB BF). json.load() sobre eso lanza
    JSONDecodeError, el except lo tragaba y devolvia None -- y el gate
    interpretaba "None" como "el archivo no existe", reportando
    `flask_suite: NOT_RUN` y un NOT_READY_FOR_CUTOVER FALSO aunque las 11
    fases hubieran pasado. Es decir: el gate no podia leer su propia
    evidencia.

    utf-8-sig lee correctamente CON y SIN BOM, asi que sirve para los
    archivos que genera PowerShell y para los que genera Python.

    Se mantiene el fail-closed: si el archivo de verdad no existe o esta
    corrupto, sigue devolviendo None y el check correspondiente cuenta
    como NO cumplido."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8-sig') as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


def check_migrations(results, validation_dir=None):
    """Ambos reconciliation reports (clean state + legacy fixture) deben
    existir, con integrity_check=='ok', 0 FK violations, 0 silently_dropped.

    PRIORIDAD DE EVIDENCIA (corregido en la corrida final de Windows,
    agosto 2026): si se paso --validation-dir, se leen los reportes que
    genero ESA corrida. Antes se leian siempre los de artifacts/ a nivel
    de repo, que pueden ser de una corrida vieja -- el gate podia dar
    'migrations: pass' con evidencia de hace dias mientras la migracion
    de la corrida actual habia fallado. Solo se cae a los del repo si no
    hay --validation-dir (uso manual del gate sin una corrida asociada), y
    en ese caso queda anotado explicitamente en el detalle."""
    candidatos = []
    if validation_dir:
        candidatos = [
            os.path.join(validation_dir, 'reconciliation_clean', 'migration_reconciliation_report.json'),
            os.path.join(validation_dir, 'reconciliation_legacy_20260712', 'migration_reconciliation_report.json'),
        ]
    usando_corrida = bool(validation_dir) and all(os.path.exists(p) for p in candidatos)

    if usando_corrida:
        paths = candidatos
        fuente = f'reportes de la corrida ({validation_dir})'
    else:
        paths = [
            os.path.join(ROOT, 'artifacts', 'reconciliation_clean', 'migration_reconciliation_report.json'),
            os.path.join(ROOT, 'artifacts', 'reconciliation_legacy_20260712', 'migration_reconciliation_report.json'),
        ]
        fuente = ('reportes a nivel de repo (artifacts/) -- NO son de una corrida '
                  'especifica del runner')

    detail = {'fuente_de_evidencia': fuente}
    ok = True
    for p in paths:
        report = _read_json(p)
        name = os.path.basename(os.path.dirname(p))
        if not report:
            detail[name] = 'REPORTE NO ENCONTRADO -- correr migrate_json_to_v5_shadow.py'
            ok = False
            continue
        integ = report.get('integrity_check')
        fk = report.get('foreign_key_check_violations')
        dropped = report.get('silently_dropped_records')
        good = (integ == ['ok'] and fk == 0 and dropped == 0)
        detail[name] = {
            'integrity_check': integ, 'foreign_key_check_violations': fk,
            'silently_dropped_records': dropped, 'pass': good,
        }
        ok = ok and good
    results['migrations'] = {'pass': ok, 'detail': detail}


def check_reset_endpoint_hardening(results):
    """Verificacion estatica de que el hardening de prioridad 6 esta en el
    codigo (no reemplaza correr los tests reales, que van en
    RESET_ENDPOINT_HARDENING dentro de la suite pytest -- esto es un piso
    minimo verificable sin Flask)."""
    app_py = os.path.join(ROOT, 'app.py')
    src = open(app_py, 'r', encoding='utf-8').read()
    markers = {
        'ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS gate': 'ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS' in src,
        'confirmacion especifica por tenant (BORRAR-<tenant_id>)': "f'BORRAR-{tenant_id}'" in src,
        'backup_now() verificado antes de vaciar': 'store.backup_now(' in src,
        'audit event RESET_TEST_DATA_EJECUTADO': 'RESET_TEST_DATA_EJECUTADO' in src,
    }
    ok = all(markers.values())
    results['reset_endpoint_hardening'] = {'pass': ok, 'detail': markers}


def check_pdf_brand_hardcodes(results):
    """Grep de FUNCTIONAL_BLOCKER P0: strings de marca hardcodeados en
    contenido REALMENTE dibujado en un PDF (c.drawString/drawCentredString
    con 'Astral' literal) o en el subject/body literal de un correo
    cliente-facing (f-string con 'ASTRAL WEDDINGS' fuera de un llamado a
    _brand_display_name_for_tenant()/brand[...]). Debe dar 0 -- si algun
    futuro cambio reintroduce una marca hardcodeada en un documento o
    correo cliente-facing, este check lo atrapa.

    Deliberadamente MAS ESTRECHO que 'cualquier ocurrencia de ASTRAL
    WEDDINGS en el repo': los defaults de configuracion interna (paquetes,
    reglas de liquidacion, cuentas bancarias placeholder, sync de Notion,
    bootstrap de login, iCal PRODID) son un problema real pero de menor
    prioridad (CONFIG_LEGACY/FUNCTIONAL_RISK, no P0) y se listan aparte en
    `remaining_lower_priority_hardcodes` para que sigan siendo visibles sin
    bloquear el gate por ellos."""
    pdf_gen_path = os.path.join(ROOT, 'src', 'pdf_generator.py')
    app_py_path = os.path.join(ROOT, 'app.py')
    pdf_gen = open(pdf_gen_path, encoding='utf-8').read()
    app_src = open(app_py_path, encoding='utf-8').read()

    pdf_functional_blockers = [
        ln.strip() for ln in pdf_gen.splitlines()
        if re.search(r'c\.draw\w*String', ln) and re.search(r"['\"]Astral", ln)
    ]

    # Lineas de app.py con 'ASTRAL WEDDINGS' literal DENTRO de un f-string
    # que arma subject/body/workflow_name (patron: comillas simples
    # 'ASTRAL WEDDINGS' pegado a texto de correo/documento). Se excluyen
    # las que ya pasan por _brand_display_name_for_tenant/brand[...]
    # (esas usan la variable `empresa`/`brand`, no el string).
    email_blocker_pattern = re.compile(r"['\"]ASTRAL WEDDINGS['\"]")
    app_literal_lines = [
        ln.strip() for ln in app_src.splitlines()
        if email_blocker_pattern.search(ln) and not ln.strip().startswith('#')
    ]
    # De esas, las que son P0 (subject/body de email o nombre de workflow
    # mostrado al usuario) vs config interna -- clasificacion explicita por
    # patron conocido, no adivinada.
    email_or_workflow_markers = ('subject', 'Mensaje de', 'BODAS', 'CONTRATO DE BODAS')
    p0_lines = [ln for ln in app_literal_lines
                if any(m in ln for m in email_or_workflow_markers)]
    lower_priority_lines = [ln for ln in app_literal_lines if ln not in p0_lines]

    detail = {
        'pdf_generator_p0_hardcodes': pdf_functional_blockers,
        'app_py_p0_hardcodes': p0_lines,
        'remaining_lower_priority_hardcodes_count': len(lower_priority_lines),
        'remaining_lower_priority_hardcodes_sample': lower_priority_lines[:15],
    }
    ok = len(pdf_functional_blockers) == 0 and len(p0_lines) == 0
    results['pdf_brand_isolation'] = {'pass': ok, 'detail': detail}


def check_flask_suite_results(results, validation_dir):
    """Lee artifacts/pre_cutover_validation/<dir>/summary.json, generado
    por run_pre_cutover_validation.ps1 en Windows. Si no existe, todas las
    fases dependientes de Flask/pytest cuentan como NO cumplidas -- nunca
    se asumen en verde por default."""
    summary_path = os.path.join(validation_dir, 'summary.json') if validation_dir else None
    summary = _read_json(summary_path)
    # Toda fase que el runner produce DEBE estar aca. Si el runner genera
    # una fase que el gate no exige, existe un hueco real: esa fase podria
    # fallar y el gate igual daria READY. Paso justo con
    # sqlite_mount_safety y post_cutover_smoke cuando se agregaron al
    # runner (detectado en el sanity final del paquete de cutover,
    # agosto 2026) -- se cierra agregandolas aca. Esto ENDURECE el gate,
    # nunca lo relaja: agregar fases solo puede hacer que sea mas dificil
    # llegar a READY_FOR_CONTROLLED_CUTOVER, nunca mas facil.
    phases = [
        'regression_stabilization', 'tenant_isolation', 'email_safety',
        'pdf_brand_tests', 'reset_endpoint_safety', 'idempotency',
        'concurrency', 'storage_locking', 'concurrency_stress', 'daily_usage',
        'migration_tests', 'sqlite_mount_safety', 'post_cutover_smoke',
        'full_suite',
    ]
    if not summary:
        results['flask_suite'] = {
            'pass': False,
            'detail': f'No se encontro {summary_path or "(sin --validation-dir)"} -- '
                      'correr run_pre_cutover_validation.ps1 en Windows primero.',
            'phases': {p: 'NOT_RUN' for p in phases},
        }
        return
    phase_results = summary.get('phases', {})
    all_ok = all(phase_results.get(p, {}).get('exit_code') == 0 for p in phases)
    results['flask_suite'] = {'pass': all_ok, 'phases': phase_results}


def check_conversion_concurrency(results, validation_dir=None):
    """EXACTAMENTE 1 job por conversion concurrente -- demostrado con datos,
    no con un exit code.

    Por que este check existe aparte de la fase de pytest (agosto 2026):
    el gate daba `concurrency: green` con solo mirar `exit_code == 0`. Pero
    el criterio que ese test aplicaba era `<= 2 jobs`, asi que un exit code
    0 era compatible con DOS jobs para una misma conversion -- exactamente
    el bug de Camila Rios en version pequena. Peor: en la corrida del
    20-ago-2026 el verde era hueco, porque 4 de los 5 hilos se caian antes
    de crear nada y el test pasaba sin haber ejercitado la carrera.

    Ahora el gate exige la PROPIEDAD DE NEGOCIO, leida del archivo de
    evidencia que produce tests/test_conversion_concurrency_stress.py:

        canonical_job_count == 1        en TODAS las iteraciones
        all_requests_same_job_id        en TODAS las iteraciones
        duplicate_workflows == 0
        duplicate_payment_schedules == 0
        total_errors == 0
        y para AMBAS marcas (Astral y Norkevin)

    Si el archivo no existe, o no cubre las dos marcas, o alguna iteracion
    incumple: NOT_READY. Nunca se asume."""
    candidatos = []
    if validation_dir:
        candidatos.append(os.path.join(validation_dir, 'concurrency_stress_evidence.json'))
    candidatos.append(os.path.join(ROOT, 'artifacts', 'concurrency_stress_evidence.json'))

    data = None
    fuente = None
    for p in candidatos:
        data = _read_json(p)
        if data:
            fuente = p
            break

    if not data:
        results['conversion_concurrency'] = {
            'pass': False,
            'detail': ('No se encontro concurrency_stress_evidence.json -- correr '
                       'tests/test_conversion_concurrency_stress.py. Sin evidencia '
                       'no se puede afirmar "exactamente 1 job"; se cuenta como NO cumplido.'),
        }
        return

    por_tenant = (data or {}).get('por_tenant') or {}
    # Los tenants del stress son SINTETICOS y dedicados a proposito: usar
    # los reales hacia que el stress sembrara ~20 jobs visibles para el
    # resto de la suite y rompiera tests/test_dashboard_upcoming_sessions.py.
    # Se exigen DOS de todos modos, para seguir demostrando que la
    # exclusividad de la conversion funciona por separado en dos tenants y
    # que uno no interfiere con el otro. El aislamiento entre las marcas
    # REALES lo cubren tenant_isolation y post_cutover_smoke, que si usan
    # tenant-norkevin y tenant-norkevin-photography.
    marcas_requeridas = ['tenant-stress-marca-a', 'tenant-stress-marca-b']
    detail = {'fuente_de_evidencia': fuente}
    ok = True

    for marca in marcas_requeridas:
        resumen = por_tenant.get(marca)
        if not resumen:
            detail[marca] = 'SIN EVIDENCIA para esta marca'
            ok = False
            continue

        iteraciones = resumen.get('detalle_por_iteracion') or []
        canonical_ok = all(i.get('canonical_job_count') == 1 for i in iteraciones)
        mismos_ids = all(i.get('all_requests_same_job_id') for i in iteraciones)
        dup_wf = resumen.get('duplicate_workflows')
        dup_pay = resumen.get('duplicate_payment_schedules')
        errores = resumen.get('total_errors')
        fallidas = resumen.get('iteraciones_fail')

        marca_ok = (
            bool(iteraciones)
            and canonical_ok and mismos_ids
            and dup_wf == 0 and dup_pay == 0
            and errores == 0 and fallidas == 0
        )
        detail[marca] = {
            'iteraciones': len(iteraciones),
            'canonical_job_count_siempre_1': canonical_ok,
            'all_requests_same_job_id': mismos_ids,
            'duplicate_workflows': dup_wf,
            'duplicate_payment_schedules': dup_pay,
            'total_errors': errores,
            'iteraciones_fail': fallidas,
            'pass': marca_ok,
        }
        ok = ok and marca_ok

    results['conversion_concurrency'] = {'pass': ok, 'detail': detail}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validation-dir', default=None,
                         help='Directorio con summary.json de run_pre_cutover_validation.ps1')
    args = parser.parse_args()

    results = {}
    check_migrations(results, args.validation_dir)
    check_reset_endpoint_hardening(results)
    check_pdf_brand_hardcodes(results)
    check_conversion_concurrency(results, args.validation_dir)
    check_flask_suite_results(results, args.validation_dir)

    all_pass = all(v.get('pass') for v in results.values())
    verdict = 'READY_FOR_CONTROLLED_CUTOVER' if all_pass else 'NOT_READY_FOR_CUTOVER'

    output = {'verdict': verdict, 'checks': results}
    print(json.dumps(output, indent=2, ensure_ascii=False))

    os.makedirs(os.path.join(ROOT, 'artifacts'), exist_ok=True)
    with open(os.path.join(ROOT, 'artifacts', 'pre_cutover_gate_result.json'), 'w', encoding='utf-8') as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False)

    print(f'\nVEREDICTO: {verdict}', file=sys.stderr)
    return 0 if all_pass else 1


if __name__ == '__main__':
    sys.exit(main())
