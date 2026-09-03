# run_pre_cutover_validation.ps1
#
# Runner definitivo para correr en Windows (donde SI hay flask/pytest
# instalados) todo lo que el sandbox de Claude NO puede correr. Prioridad
# 10 del bloque de cierre de brechas, reescrito en la fase "cerrar el
# pre-cutover gate" (agosto 2026) para ser TOTALMENTE no interactivo:
#
#   - No pide click, confirmacion ni input de ningun tipo.
#   - No necesita que la ventana quede en primer plano ni que nadie la
#     mire mientras corre.
#   - No necesita que la Terminal se quede abierta despues: corre, escribe
#     sus markers/logs/summary.json a disco, y termina solo.
#   - Deja SIEMPRE un estado inequivoco en disco (ver seccion MARKERS abajo)
#     para que un proceso externo (o Claude leyendo el filesystem) pueda
#     saber si esto nunca arranco, sigue corriendo, termino bien o fallo a
#     mitad de camino -- sin depender de leer la pantalla.
#
# Reglas duras, no negociables:
#   - Fuerza OUTBOUND_EMAIL_ENABLED=0 y DISABLE_OUTBOUND_EMAIL=1 ANTES de
#     importar la app o correr pytest -- ningun test de este runner puede
#     enviar un correo real, sin importar que test sea o que falle.
#   - Fuerza ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS=0 (ausente) a nivel de
#     entorno del runner -- los tests de hardening del endpoint de reset
#     (tests/test_reset_endpoint_hardening.py) ya activan esa flag ELLOS
#     MISMOS via monkeypatch.setenv() solo dentro de su propio proceso de
#     test y solo contra el CRM_DATA_DIR aislado de conftest.py; el runner
#     no necesita (ni debe) prenderla de forma ambiental.
#   - Si detecta que alguna flag peligrosa SI esta activa en el entorno
#     heredado (ej. alguien ya tenia OUTBOUND_EMAIL_ENABLED=1 exportado en
#     su sesion de PowerShell antes de correr esto), la pisa a la fuerza
#     Y ademas lo deja anotado en el log -- nunca corre con una flag
#     peligrosa activa sin decirlo.
#   - Nunca levanta un tunel (ngrok/cloudflared/etc), nunca hace
#     deployment, nunca hace cutover, nunca instala dependencias, nunca
#     toca internet.
#   - No instala nada: si falta python/flask/pytest, lo reporta como
#     ENVIRONMENT_FAILURE y sigue con lo que SI pueda correr (ej. el script
#     de migracion, que solo necesita python + sqlite3 de la stdlib).
#   - Corre las fases EN ORDEN, pero si una falla, las demas SIGUEN
#     corriendo igual -- el objetivo es diagnostico completo, no parar en
#     el primer error.
#   - Guarda el output completo (stdout+stderr) de cada fase Y un log
#     combinado (windows_full.log), mas un summary.json que
#     pre_cutover_gate.py puede leer directamente.
#
# Uso (una sola vez, sin argumentos, sin que nadie se quede mirando):
#   cd C:\Users\fotov\.openclaw\workspace\crm_norkevin
#   powershell -NoProfile -ExecutionPolicy Bypass -File run_pre_cutover_validation.ps1
#
# Despues:
#   python pre_cutover_gate.py --validation-dir artifacts\pre_cutover_validation\latest

# --- No interactivo, no se detiene ante nada, no requiere ventana visible ---
$ErrorActionPreference = "Continue"   # una fase que falla no debe frenar las demas
$ProgressPreference = "SilentlyContinue"  # las barras de progreso de Invoke-* pueden colgar consolas no interactivas
$ConfirmPreference = "None"            # ningun cmdlet debe poder pedir confirmacion

Set-StrictMode -Off

# Working directory explicito e independiente de donde se invoque esto
# desde (doble click, Tarea Programada, otra shell) -- nunca asume el cwd.
# Se captura ANTES de entrar a ninguna funcion: dentro de una funcion,
# $MyInvocation.MyCommand.Path apunta a la funcion, no al script -- por
# eso se guarda aca, a nivel de script, y Write-Marker usa esta variable
# en vez de volver a leer $MyInvocation.
$ScriptPath = $MyInvocation.MyCommand.Path
$ScriptDir = Split-Path -Parent $ScriptPath
Set-Location -Path $ScriptDir

# ============================================================
# MARKERS: estado inequivoco en disco, sin depender de pantalla
# ============================================================
$MarkerStarted  = Join-Path $ScriptDir 'VALIDATION_STARTED.marker'
$MarkerComplete = Join-Path $ScriptDir 'VALIDATION_COMPLETE.marker'
$MarkerFailed   = Join-Path $ScriptDir 'VALIDATION_FAILED.marker'
$FullLog        = Join-Path $ScriptDir 'artifacts\pre_cutover_validation\windows_full.log'

function Write-Marker {
    param(
        [string]$Path,
        [string]$Status,
        [Nullable[int]]$ExitCode = $null,
        [string]$LogPath = '',
        [string]$Detail = ''
    )
    $payload = [ordered]@{
        status      = $Status
        timestamp   = (Get-Date -Format "o")
        exit_code   = $ExitCode
        log_path    = $LogPath
        detail      = $Detail
        script      = $ScriptPath
    }
    $payload | ConvertTo-Json -Depth 4 | Out-File -Encoding utf8 -FilePath $Path -Force
}

function Write-FullLog {
    param([string]$Line)
    $stamped = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Line
    Add-Content -Path $FullLog -Value $stamped -Encoding utf8
    Write-Host $stamped
}

# Limpia markers de una corrida anterior ANTES de arrancar -- si el proceso
# muere sin llegar a escribir COMPLETE/FAILED, un STARTED viejo sin
# resolucion es indistinguible de "sigue corriendo" o "murio sin avisar";
# mejor arrancar limpio cada vez.
Remove-Item -Path $MarkerComplete, $MarkerFailed -Force -ErrorAction SilentlyContinue

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $FullLog) | Out-Null
if (Test-Path $FullLog) { Remove-Item $FullLog -Force }
New-Item -ItemType File -Path $FullLog -Force | Out-Null

Write-Marker -Path $MarkerStarted -Status 'STARTED' -LogPath $FullLog -Detail 'Runner arranco. Si este marker sigue siendo el mas reciente y no aparecio ni COMPLETE ni FAILED, el proceso murio a mitad de camino o sigue corriendo.'
Write-FullLog "=== run_pre_cutover_validation.ps1 iniciado ==="
Write-FullLog "PID: $PID   PSVersion: $($PSVersionTable.PSVersion)   Directorio: $ScriptDir"

$startedAt = Get-Date -Format "o"

try {
    # ============================================================
    # 1. Safety flags -- ANTES de tocar python/pytest/la app
    # ============================================================
    $peligrosas = @()
    if ($env:OUTBOUND_EMAIL_ENABLED -eq '1') { $peligrosas += 'OUTBOUND_EMAIL_ENABLED ya estaba en 1 en el entorno heredado' }
    if ($env:ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS -eq '1') { $peligrosas += 'ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS ya estaba en 1 en el entorno heredado' }
    if ($env:DISABLE_OUTBOUND_EMAIL -eq '0') { $peligrosas += 'DISABLE_OUTBOUND_EMAIL ya estaba forzado a 0 en el entorno heredado' }
    foreach ($p in $peligrosas) { Write-FullLog "ADVERTENCIA (flag peligrosa detectada, se pisa a la fuerza): $p" }

    $env:DISABLE_OUTBOUND_EMAIL = "1"
    $env:OUTBOUND_EMAIL_ENABLED = "0"
    # Ausente/0 a proposito -- ver nota arriba, cada test de reset-endpoint
    # activa esta flag SOLO dentro de su propio proceso via monkeypatch.
    $env:ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS = "0"
    $env:RECURRENTE_SECRET_KEY = ""
    $env:RECURRENTE_SECRET_KEY_TEST = ""
    $env:RECURRENTE_MODE = ""
    # No tunel, no red saliente para nada que no sea localhost.
    Remove-Item Env:\HTTP_PROXY,Env:\HTTPS_PROXY -ErrorAction SilentlyContinue

    Write-FullLog "Safety flags fijadas: DISABLE_OUTBOUND_EMAIL=$($env:DISABLE_OUTBOUND_EMAIL) OUTBOUND_EMAIL_ENABLED=$($env:OUTBOUND_EMAIL_ENABLED) ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS=$($env:ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS)"
    if ($peligrosas.Count -gt 0) {
        Write-FullLog "Flags peligrosas detectadas y corregidas: $($peligrosas.Count) -- ver detalle arriba."
    }

    # ============================================================
    # 2. Deteccion de entorno -- SIN instalar nada, SIN usar internet
    # ============================================================
    function Test-Command($cmd) {
        return [bool](Get-Command $cmd -ErrorAction SilentlyContinue)
    }

    $envReport = [ordered]@{}

    $pythonCmd = $null
    foreach ($candidate in @('python', 'python3', 'py')) {
        if (Test-Command $candidate) { $pythonCmd = $candidate; break }
    }
    $envReport['python_found'] = [bool]$pythonCmd
    $envReport['python_command'] = $pythonCmd

    if ($pythonCmd) {
        $pyVersion = & $pythonCmd --version 2>&1 | Out-String
        $envReport['python_version'] = $pyVersion.Trim()
        Write-FullLog "python detectado: $pythonCmd -> $($pyVersion.Trim())"
    } else {
        Write-FullLog "python NO detectado (se probo python/python3/py) -- ninguna fase que dependa de python puede correr."
    }

    $inVenv = $false
    if ($pythonCmd) {
        $venvCheck = & $pythonCmd -c "import sys; print(bool(getattr(sys,'base_prefix',sys.prefix)!=sys.prefix or hasattr(sys,'real_prefix')))" 2>&1
        $inVenv = ($venvCheck -match 'True')
    }
    $envReport['virtualenv_activo'] = $inVenv
    Write-FullLog "virtualenv activo: $inVenv (informativo -- no se activa ni crea ninguno, se usa el interprete tal como esta en PATH)"

    $flaskOk = $false
    $pytestOk = $false
    $flaskVersion = ''
    $pytestVersion = ''
    if ($pythonCmd) {
        $flaskCheck = & $pythonCmd -c "import flask; print(flask.__version__)" 2>&1
        if ($LASTEXITCODE -eq 0) { $flaskOk = $true; $flaskVersion = ($flaskCheck | Out-String).Trim() }
        $pytestCheck = & $pythonCmd -c "import pytest; print(pytest.__version__)" 2>&1
        if ($LASTEXITCODE -eq 0) { $pytestOk = $true; $pytestVersion = ($pytestCheck | Out-String).Trim() }
    }
    $envReport['flask_found'] = $flaskOk
    $envReport['flask_version'] = $flaskVersion
    $envReport['pytest_found'] = $pytestOk
    $envReport['pytest_version'] = $pytestVersion
    Write-FullLog "flask: found=$flaskOk version=$flaskVersion"
    Write-FullLog "pytest: found=$pytestOk version=$pytestVersion"

    $canRunPytest = ($pythonCmd -and $flaskOk -and $pytestOk)
    $canRunMigrationOnly = [bool]$pythonCmd   # migrate_json_to_v5_shadow.py solo usa stdlib (sqlite3, json, argparse)

    if (-not $canRunPytest) {
        Write-FullLog "ENVIRONMENT_FAILURE: faltan dependencias para correr pytest (python=$([bool]$pythonCmd) flask=$flaskOk pytest=$pytestOk). Las fases basadas en pytest se marcaran NOT_RUN con motivo ENVIRONMENT_FAILURE. NO se instala nada automaticamente (regla explicita)."
    }

    $envReportPath = Join-Path $ScriptDir 'artifacts\pre_cutover_validation\environment_report.json'
    $envReport | ConvertTo-Json -Depth 4 | Out-File -Encoding utf8 -FilePath $envReportPath -Force

    # ============================================================
    # 3. Fases
    # ============================================================
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $outDir = "artifacts\pre_cutover_validation\$timestamp"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    Write-FullLog "Directorio de esta corrida: $outDir"

    # (nombre interno, clave de summary.json que pide Kevin, descripcion,
    # argumentos, requiere_pytest)
    #
    # NOTA de preflight (agosto 2026, "cerrar el pre-cutover gate"): la
    # version anterior de este runner armaba un STRING de comando y lo
    # pasaba a 'cmd /c "el string entero"'. Eso tenia dos problemas reales:
    #   1. Anidar comillas dentro de ese string (para citar el ejecutable
    #      de python o un filtro -k con espacios) es fragil de verdad --
    #      cmd.exe tiene reglas propias, no-obvias, de como colapsa pares
    #      de comillas en 'cmd /c "..."', y un path con espacios (ej.
    #      "C:\Program Files\Python312\python.exe") podia romper el
    #      parseo entero silenciosamente.
    #   2. Las dos invocaciones de migrate_json_to_v5_shadow.py estaban
    #      separadas por ';' dentro del mismo string -- ';' NO es un
    #      separador de comandos en cmd.exe (eso es shell Unix/PowerShell),
    #      asi que la segunda migracion (LEGACY_20260712) nunca llegaba a
    #      correr de verdad.
    # Se reemplaza por invocacion NATIVA de PowerShell con argumentos en
    # ARRAY (exe + argsList), sin pasar nunca por cmd.exe: PowerShell arma
    # el proceso hijo con CreateProcess y cita cada argumento el mismo,
    # asi que un path con espacios o un filtro con espacios funciona sin
    # ningun escapeo manual de nuestra parte.
    $phases = @(
        @{ name = "regression_stabilization"; summaryKey = "stabilization_tests";
           desc = "18+ tests de regresion de la fase de estabilizacion";
           exe = $pythonCmd; argsList = @('-m', 'pytest', 'tests\test_stabilization_phase_regression.py', '-v'); needsPytest = $true },
        @{ name = "tenant_isolation"; summaryKey = "cross_tenant";
           desc = "Aislamiento cross-tenant Norkevin/Astral (incluye relaciones N-clientes)";
           exe = $pythonCmd; argsList = @('-m', 'pytest', 'tests\test_incident_cross_company_email.py', 'tests\test_credential_isolation.py', 'tests\test_tenant_isolation.py', '-v'); needsPytest = $true },
        @{ name = "daily_usage"; summaryKey = "daily_usage";
           desc = "Uso diario: estados job, N clientes, schedules, locacion, orden, fichas de cliente, workflows/calendario por marca, navegacion, paginas de error y rendimiento";
           exe = $pythonCmd; argsList = @('-m', 'pytest', 'tests\test_job_estado_activo_completo.py', 'tests\test_uso_diario_jobs.py', 'tests\test_job_clients_n_y_schedules.py', 'tests\test_uso_diario_clientes.py', 'tests\test_uso_diario_workflows_calendario.py', 'tests\test_navegacion_diaria.py', 'tests\test_paginas_de_error_y_marca.py', 'tests\test_rendimiento_vistas.py', 'tests\test_responsive_movil.py', 'tests\test_documento_web_pdf_paridad.py', 'tests\test_uso_diario_pantallas.py', 'tests\test_marca_en_documentos_cliente.py', '-v'); needsPytest = $true },
        @{ name = "email_safety"; summaryKey = "email_safety";
           desc = "Kill switch, aprobacion, retry, cross-company block, guardia anti-proveedor-real";
           exe = $pythonCmd; argsList = @('-m', 'pytest', 'tests\test_manual_retry_and_audit.py', 'tests\test_incident_cross_company_email.py', '-v', '-k', 'email'); needsPytest = $true },
        @{ name = "pdf_brand_tests"; summaryKey = "pdf_brand";
           desc = "Aislamiento de marca en PDF/contract_terms (prioridad 3)";
           exe = $pythonCmd; argsList = @('-m', 'pytest', 'tests\test_pdf_brand_isolation.py', '-v'); needsPytest = $true },
        @{ name = "reset_endpoint_safety"; summaryKey = "reset_endpoint";
           desc = "Hardening de /api/admin/reset-test-data (prioridad 6)";
           exe = $pythonCmd; argsList = @('-m', 'pytest', 'tests\test_reset_endpoint_hardening.py', '-v'); needsPytest = $true },
        @{ name = "idempotency"; summaryKey = "idempotency";
           desc = "idempotency_key + guardia /api/jobs/new + consolidacion accept-quote";
           exe = $pythonCmd; argsList = @('-m', 'pytest', 'tests\test_stabilization_phase_regression.py', '-v', '-k', 'idempot or consolida'); needsPytest = $true },
        @{ name = "concurrency"; summaryKey = "concurrency";
           desc = "5 requests concurrentes al mismo lead (capa de aplicacion Flask real)";
           exe = $pythonCmd; argsList = @('-m', 'pytest', 'tests\test_stabilization_phase_regression.py', '-v', '-k', 'concurrente'); needsPytest = $true },
        @{ name = "storage_locking"; summaryKey = "storage_locking";
           desc = "Locking por archivo de JsonStore + stress aislado de 50 iteraciones";
           argsLists = @(
               @('-m', 'pytest', 'tests\test_storage_locking.py', '-v'),
               @('tools\stress_storage_concurrency.py', '--iteraciones', '50', '--workers', '5')
           );
           exe = $pythonCmd; needsPytest = $true },
        @{ name = "concurrency_stress"; summaryKey = "concurrency_stress";
           desc = "20 iteraciones x 5 requests simultaneos, por marca: exactamente 1 job";
           exe = $pythonCmd; argsList = @('-m', 'pytest', 'tests\test_conversion_concurrency_stress.py', '-v'); needsPytest = $true },
        @{ name = "migration_tests"; summaryKey = "migration";
           desc = "Migracion shadow CLEAN_STATE + LEGACY_20260712 (script directo, no pytest)";
           argsLists = @(
               @('migrate_json_to_v5_shadow.py', '--source', 'data', '--db-path', "artifacts\pre_cutover_validation\$timestamp\shadow_clean.db", '--out', "artifacts\pre_cutover_validation\$timestamp\reconciliation_clean"),
               @('migrate_json_to_v5_shadow.py', '--source', 'artifacts\fixtures\legacy_20260712', '--db-path', "artifacts\pre_cutover_validation\$timestamp\shadow_legacy_20260712.db", '--out', "artifacts\pre_cutover_validation\$timestamp\reconciliation_legacy_20260712", '--quarantine-report', 'artifacts\quarantine_legacy_20260712\camila_daniel_report.json')
           );
           exe = $pythonCmd; needsPytest = $false },
        @{ name = "sqlite_mount_safety"; summaryKey = "sqlite_mount_safety";
           desc = "Regresion: ninguna herramienta abre un .db in-place sobre el volumen montado";
           exe = $pythonCmd; argsList = @('-m', 'pytest', 'tests\test_sqlite_mount_safety.py', '-v'); needsPytest = $true },
        @{ name = "post_cutover_smoke"; summaryKey = "post_cutover_smoke";
           desc = "Smoke post-cutover: recorrido completo x2 marcas + negativos cross-tenant";
           exe = $pythonCmd; argsList = @('-m', 'pytest', 'tests\test_post_cutover_smoke.py', '-v'); needsPytest = $true },
        @{ name = "full_suite"; summaryKey = "full_suite";
           desc = "Suite completa de pytest existente";
           exe = $pythonCmd; argsList = @('-m', 'pytest', '-v'); needsPytest = $true }
    )

    $summary = [ordered]@{
        started_at   = $startedAt
        completed_at = $null
        exit_code    = $null
        environment  = $envReport
        phases       = [ordered]@{}
    }
    # Claves de nivel superior que pide Kevin explicitamente, apuntando al
    # mismo objeto de detalle que summary.phases[<name>] para no duplicar
    # informacion -- se llenan al vuelo en el loop de abajo.
    foreach ($k in @('stabilization_tests','full_suite','concurrency','cross_tenant','email_safety','pdf_brand','reset_endpoint','migration','idempotency','post_cutover_smoke','sqlite_mount_safety','concurrency_stress','daily_usage')) {
        $summary[$k] = $null
    }

    # --basetemp propio por fase (fix de ENVIRONMENT_FAILURE encontrado en la
    # primera corrida real en Windows, agosto 2026): por defecto pytest usa
    # %TEMP%\pytest-of-<usuario>\ con directorios numerados y un symlink
    # 'pytest-current'. Al terminar, intenta limpiar ese symlink y en Windows
    # eso falla con "PermissionError: [WinError 5] Acceso denegado"
    # (crear/borrar symlinks requiere permisos que una sesion normal no tiene
    # sin Developer Mode). El error ocurre en el TEARDOWN, despues de que
    # todos los tests corrieron, pero hace que pytest termine con exit code
    # distinto de 0 -- convirtiendo fases 100% verdes en FAIL. Con --basetemp
    # explicito no hay numeracion ni symlink 'pytest-current', y el exit code
    # vuelve a reflejar solo el resultado de los tests.
    $pytestTempRoot = Join-Path $env:TEMP "crm_pytest_$timestamp"
    New-Item -ItemType Directory -Force -Path $pytestTempRoot | Out-Null
    Write-FullLog "Basetemp de pytest para esta corrida: $pytestTempRoot"

    foreach ($phase in $phases) {
        $name = $phase.name
        # Solo las fases de un solo comando pytest llevan --basetemp. Las
        # fases multi-comando (argsLists) no: mezclan pytest con scripts
        # directos, y agregarle --basetemp a un script suelto lo romperia.
        if ($phase.needsPytest -and $phase.ContainsKey('argsList')) {
            $phase.argsList = @($phase.argsList) + @('--basetemp', (Join-Path $pytestTempRoot $name))
        }
        Write-FullLog "--- Fase: $name ($($phase.desc)) ---"
        $logPath = Join-Path $outDir "$name.log"
        $phaseStartedAt = Get-Date -Format "o"

        if ($phase.needsPytest -and -not $canRunPytest) {
            $motivo = "ENVIRONMENT_FAILURE: falta python y/o flask y/o pytest en este entorno (python_found=$([bool]$pythonCmd) flask_found=$flaskOk pytest_found=$pytestOk). No se instalo nada automaticamente (regla explicita del runner)."
            "$motivo" | Out-File -Encoding utf8 -FilePath $logPath -Force
            Write-FullLog "    SALTADA -- $motivo"
            $detail = [ordered]@{
                description  = $phase.desc
                status       = 'ENVIRONMENT_FAILURE'
                exit_code    = $null
                log_file     = $logPath
                started_at   = $phaseStartedAt
                completed_at = (Get-Date -Format "o")
                motivo       = $motivo
            }
            $summary.phases[$name] = $detail
            if ($phase.summaryKey) { $summary[$phase.summaryKey] = $detail }
            continue
        }
        if (-not $phase.needsPytest -and -not $canRunMigrationOnly) {
            $motivo = "ENVIRONMENT_FAILURE: no se encontro python en PATH -- ni siquiera el script de migracion (stdlib pura) puede correr."
            "$motivo" | Out-File -Encoding utf8 -FilePath $logPath -Force
            Write-FullLog "    SALTADA -- $motivo"
            $detail = [ordered]@{
                description = $phase.desc; status = 'ENVIRONMENT_FAILURE'; exit_code = $null
                log_file = $logPath; started_at = $phaseStartedAt; completed_at = (Get-Date -Format "o"); motivo = $motivo
            }
            $summary.phases[$name] = $detail
            if ($phase.summaryKey) { $summary[$phase.summaryKey] = $detail }
            continue
        }

        # Invocacion NATIVA de PowerShell (exe + argumentos en array), NUNCA
        # via cmd.exe ni Invoke-Expression -- ver nota de preflight arriba
        # de $phases. '2>&1' fusiona stderr al stream de salida ANTES del
        # pipe, y el pipe va a un archivo -- $LASTEXITCODE sigue siendo el
        # del ultimo proceso NATIVO ejecutado (& $exe ...), un cmdlet como
        # Out-File despues en el pipeline no lo pisa.
        if ($phase.ContainsKey('argsLists')) {
            # Fase con varios comandos independientes (ej. 2 migraciones):
            # cada uno corre por separado, con su propio exit code, y el
            # resultado de la fase es FAIL si CUALQUIERA de los dos fallo
            # -- nunca se enmascara un fallo del primero con un exito del
            # segundo (o viceversa).
            $exitCodes = @()
            $stepNum = 0
            foreach ($stepArgs in $phase.argsLists) {
                $stepNum++
                $stepLogPath = Join-Path $outDir "$name.step$stepNum.log"
                # --basetemp tambien para los pasos de pytest DENTRO de una
                # fase multi-comando. La version anterior solo se lo agregaba
                # a las fases de un unico comando ($phase.argsList), asi que
                # storage_locking -- la unica fase pytest que usa argsLists --
                # quedaba sin basetemp y volvia a morir en el teardown por el
                # symlink 'pytest-current' (WinError 5): 7/7 tests PASSED y
                # exit code 1 igual. Se aplica solo a los pasos que invocan
                # pytest; a un script suelto --basetemp lo romperia.
                $stepArgsFinal = @($stepArgs)
                if ($stepArgsFinal -contains 'pytest') {
                    $stepArgsFinal = $stepArgsFinal + @('--basetemp', (Join-Path $pytestTempRoot "$name`_step$stepNum"))
                }
                & $phase.exe @stepArgsFinal 2>&1 | Out-File -FilePath $stepLogPath -Encoding utf8
                $stepExit = $LASTEXITCODE
                $exitCodes += $stepExit
                Write-FullLog "    [$name step $stepNum] exit_code=$stepExit log=$stepLogPath"
                "=== step $stepNum (exit_code=$stepExit) ===" | Out-File -Encoding utf8 -FilePath $logPath -Append
                if (Test-Path $stepLogPath) { Get-Content $stepLogPath | Out-File -Encoding utf8 -FilePath $logPath -Append }
            }
            $exitCode = ($exitCodes | Where-Object { $_ -ne 0 } | Select-Object -First 1)
            if ($null -eq $exitCode) { $exitCode = 0 }
        } else {
            & $phase.exe @($phase.argsList) 2>&1 | Out-File -FilePath $logPath -Encoding utf8
            $exitCode = $LASTEXITCODE
        }
        $status = if ($exitCode -eq 0) { 'PASS' } else { 'FAIL' }

        Write-FullLog "    exit_code=$exitCode status=$status log=$logPath"
        # Vuelca tambien el contenido de la fase al log combinado, para que
        # windows_full.log sea autosuficiente sin tener que abrir cada
        # archivo por separado.
        if (Test-Path $logPath) {
            Write-FullLog "    --- contenido de $name.log ---"
            Get-Content $logPath | ForEach-Object { Add-Content -Path $FullLog -Value "        $_" -Encoding utf8 }
        }

        $detail = [ordered]@{
            description  = $phase.desc
            status       = $status
            exit_code    = $exitCode
            log_file     = $logPath
            started_at   = $phaseStartedAt
            completed_at = (Get-Date -Format "o")
        }
        $summary.phases[$name] = $detail
        if ($phase.summaryKey) { $summary[$phase.summaryKey] = $detail }
    }

    # ============================================================
    # 4. Gate final -- consume los resultados que se acaban de generar
    # ============================================================
    # La evidencia de concurrencia la escribe el propio test en
    # artifacts\concurrency_stress_evidence.json. Se copia al directorio de
    # ESTA corrida para que quede archivada junto al resto y para que el
    # gate la lea desde ahi (evidencia de la corrida, no un archivo suelto
    # que podria ser de otro dia).
    $stressEvidence = Join-Path $ScriptDir 'artifacts\concurrency_stress_evidence.json'
    if (Test-Path $stressEvidence) {
        Copy-Item -Force $stressEvidence (Join-Path $outDir 'concurrency_stress_evidence.json')
        Write-FullLog "Evidencia de concurrencia copiada a $outDir"
    } else {
        Write-FullLog "ADVERTENCIA: no se encontro $stressEvidence -- el gate marcara conversion_concurrency como NO cumplido."
    }

    $latestDir = "artifacts\pre_cutover_validation\latest"
    if (Test-Path $latestDir) { Remove-Item -Recurse -Force $latestDir }
    Copy-Item -Recurse -Force $outDir $latestDir

    # summary.json debe existir en $latestDir ANTES de invocar el gate.
    $summary['completed_at'] = Get-Date -Format "o"
    $anyEnvFailure = ($summary.phases.Values | Where-Object { $_.status -eq 'ENVIRONMENT_FAILURE' }).Count -gt 0
    $anyFail = ($summary.phases.Values | Where-Object { $_.status -eq 'FAIL' }).Count -gt 0
    $summary['exit_code'] = if ($anyFail -or $anyEnvFailure) { 1 } else { 0 }

    $summaryPathTimestamped = Join-Path $outDir "summary.json"
    $summary | ConvertTo-Json -Depth 8 | Out-File -Encoding utf8 -FilePath $summaryPathTimestamped -Force
    $summaryPathLatest = Join-Path $latestDir "summary.json"
    $summary | ConvertTo-Json -Depth 8 | Out-File -Encoding utf8 -FilePath $summaryPathLatest -Force

    Write-FullLog "summary.json escrito en: $summaryPathTimestamped y $summaryPathLatest"

    $gateLogPath = Join-Path $outDir "pre_cutover_gate.log"
    if ($pythonCmd) {
        & $pythonCmd 'pre_cutover_gate.py' '--validation-dir' $latestDir 2>&1 | Out-File -FilePath $gateLogPath -Encoding utf8
        $gateExit = $LASTEXITCODE
        Write-FullLog "pre_cutover_gate.py exit_code=$gateExit -- ver $gateLogPath y artifacts\pre_cutover_gate_result.json"
        if (Test-Path $gateLogPath) {
            Get-Content $gateLogPath | ForEach-Object { Add-Content -Path $FullLog -Value "    [gate] $_" -Encoding utf8 }
        }
        $summary['gate_result'] = [ordered]@{
            exit_code = $gateExit
            log_file  = $gateLogPath
            result_json = (Join-Path $ScriptDir 'artifacts\pre_cutover_gate_result.json')
        }
    } else {
        $summary['gate_result'] = [ordered]@{ exit_code = $null; motivo = 'python no disponible, no se pudo correr pre_cutover_gate.py' }
    }
    $summary | ConvertTo-Json -Depth 8 | Out-File -Encoding utf8 -FilePath $summaryPathTimestamped -Force
    $summary | ConvertTo-Json -Depth 8 | Out-File -Encoding utf8 -FilePath $summaryPathLatest -Force

    # ============================================================
    # 5. Resultado final + marker
    # ============================================================
    $anyFailedFinal = ($summary.phases.Values | Where-Object { $_.status -ne 'PASS' }).Count -gt 0
    Write-FullLog ""
    foreach ($p in $summary.phases.Keys) {
        $st = $summary.phases[$p].status
        Write-FullLog ("    {0,-28} {1}" -f $p, $st)
    }

    if ($anyFailedFinal) {
        Write-FullLog "`nAl menos una fase no termino en PASS -- resultado final: FAIL (ver detalle arriba, y logs por fase en $outDir)."
        Write-Marker -Path $MarkerComplete -Status 'COMPLETE_WITH_FAILURES' -ExitCode 1 -LogPath $FullLog -Detail "Corrio completo pero al menos una fase no fue PASS. summary: $summaryPathLatest"
        exit 1
    } else {
        Write-FullLog "`nTodas las fases terminaron en PASS."
        Write-Marker -Path $MarkerComplete -Status 'COMPLETE_ALL_PASS' -ExitCode 0 -LogPath $FullLog -Detail "Todas las fases PASS. summary: $summaryPathLatest"
        exit 0
    }
} catch {
    $err = $_ | Out-String
    Write-FullLog "EXCEPCION NO MANEJADA -- el runner se interrumpio antes de terminar todas las fases: $err"
    Write-Marker -Path $MarkerFailed -Status 'FAILED' -ExitCode 1 -LogPath $FullLog -Detail $err
    exit 1
}
