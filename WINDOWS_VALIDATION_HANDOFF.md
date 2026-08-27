# WINDOWS_VALIDATION_HANDOFF.md

**Objetivo:** que la validación real en Windows requiera **una sola
acción**, sin decisiones ni pasos intermedios.

**Estado:** esperando esa única acción. No hay ningún puente automático
seguro disponible (ver `WINDOWS_EXECUTION_OPTIONS` en
`STABILIZATION_EXECUTION_REPORT.md` para el inventario completo de lo que
se buscó y por qué se descartó).

---

## 1. LA ÚNICA ACCIÓN

Elegir **una** de estas tres. Son equivalentes: producen los mismos
markers, los mismos logs y el mismo `summary.json`.

### Opción A — Doble click (la más simple)

```
C:\Users\fotov\.openclaw\workspace\crm_norkevin\run_windows_validation_launcher.bat
```

Abre una consola que se cierra sola en un segundo. El trabajo real
continúa en segundo plano, en una ventana oculta. **No hay que quedarse
mirando ni mantener nada abierto.**

### Opción B — Una línea en PowerShell

```powershell
cd C:\Users\fotov\.openclaw\workspace\crm_norkevin
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\run_pre_cutover_validation.ps1
```

Esta sí muestra el progreso en pantalla. Útil si se quiere ver qué pasa.

### Opción C — Tarea programada de una sola vez

Si se prefiere que corra sin nadie presente (por ejemplo, de madrugada).
Ejecutar **una vez** en PowerShell, **sin permisos de administrador**:

```powershell
$acc  = New-ScheduledTaskAction -Execute "cmd.exe" `
        -Argument "/c `"C:\Users\fotov\.openclaw\workspace\crm_norkevin\run_windows_validation_launcher.bat`"" `
        -WorkingDirectory "C:\Users\fotov\.openclaw\workspace\crm_norkevin"
$trg  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2)
$set  = New-ScheduledTaskSettingsSet -StartWhenAvailable -DeleteExpiredTaskAfter (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "CRM_PreCutoverValidation_OneShot" `
        -Action $acc -Trigger $trg -Settings $set `
        -Description "Validacion pre-cutover del CRM. Una sola ejecucion, se autoelimina."
```

Corre en 2 minutos, con la cuenta actual (`fotov`), **sin
`-RunLevel Highest`** (no necesita privilegios elevados) y se autoelimina
2 horas después de expirar. Para borrarla antes:

```powershell
Unregister-ScheduledTask -TaskName "CRM_PreCutoverValidation_OneShot" -Confirm:$false
```

> **Por qué no la creé yo:** no tengo forma de ejecutar nada en la máquina
> Windows. El puente sandbox↔host es **solo de archivos** (FUSE): no hay
> `cmd.exe`, ni `powershell.exe`, ni interop WSL, ni ruta de red al host.
> Puedo escribir el script; no puedo dispararlo.

---

## 2. RUTAS EXACTAS

| Qué | Ruta |
|---|---|
| Directorio de trabajo | `C:\Users\fotov\.openclaw\workspace\crm_norkevin` |
| Launcher (doble click) | `...\crm_norkevin\run_windows_validation_launcher.bat` |
| Runner real | `...\crm_norkevin\run_pre_cutover_validation.ps1` |
| Log combinado | `...\crm_norkevin\artifacts\pre_cutover_validation\windows_full.log` |
| Resultados de esta corrida | `...\crm_norkevin\artifacts\pre_cutover_validation\<timestamp>\` |
| Copia estable | `...\crm_norkevin\artifacts\pre_cutover_validation\latest\` |

El working directory se fija solo (`cd /d "%~dp0"` en el `.bat`,
`Set-Location $ScriptDir` en el `.ps1`). **No importa desde dónde se
dispare.**

---

## 3. MARKERS: CÓMO SABER EN QUÉ ESTADO ESTÁ

Los tres viven en `C:\Users\fotov\.openclaw\workspace\crm_norkevin\`.
Cada uno es un JSON con `status`, `timestamp`, `exit_code`, `log_path` y
`detail`.

| Archivo | Significa | Qué hacer |
|---|---|---|
| *(ninguno)* | Nunca arrancó | Volver al paso 1 |
| `VALIDATION_STARTED.marker` **solo** | Arrancó y **sigue corriendo** | Esperar |
| `VALIDATION_STARTED` + `VALIDATION_COMPLETE` con `status: COMPLETE_ALL_PASS` | Terminó, **todas las fases PASS** | Leer resultados (§5) |
| `VALIDATION_STARTED` + `VALIDATION_COMPLETE` con `status: COMPLETE_WITH_FAILURES` | Terminó, **alguna fase falló** | Leer resultados (§5) — esto es información útil, no un desastre |
| `VALIDATION_STARTED` + `VALIDATION_FAILED` | Se interrumpió antes de terminar | Leer `detail` del marker + `windows_full.log` |
| `STARTED` viejo, sin `COMPLETE` ni `FAILED`, y `windows_full.log` sin crecer hace >30 min | Murió sin avisar | Ver §7 |

Los markers `COMPLETE`/`FAILED` se borran al inicio de cada corrida, así
que **nunca** hay un marker viejo que se confunda con el estado actual.

---

## 4. CUÁNTO TARDA

**Estimación: 5 a 20 minutos.** Es una estimación, no una medición — la
suite nunca se ha ejecutado, que es precisamente el motivo de todo esto.

| Fase | Estimado |
|---|---|
| Detección de entorno | segundos |
| 8 fases de pytest específicas | 1–2 min c/u |
| `migration_tests` (2 migraciones) | 1–3 min |
| `full_suite` (suite completa) | 3–10 min |

Si a los **45 minutos** sigue sin aparecer `COMPLETE` ni `FAILED`, tratarlo
como colgado (§7).

---

## 5. QUÉ LEO DESPUÉS

Con que la ejecución ocurra alcanza — el resto lo hago yo leyendo estos
archivos, sin necesitar la pantalla:

1. `VALIDATION_COMPLETE.marker` / `VALIDATION_FAILED.marker` — estado y exit code
2. `artifacts/pre_cutover_validation/latest/summary.json` — resultado por fase
3. `artifacts/pre_cutover_validation/latest/<fase>.log` — salida completa de cada fase
4. `artifacts/pre_cutover_validation/windows_full.log` — todo junto
5. `artifacts/pre_cutover_validation/environment_report.json` — versiones detectadas
6. `artifacts/pre_cutover_gate_result.json` — veredicto del gate (el runner lo corre solo al final)

Con eso: clasifico cada fallo, arreglo lo que sea seguro, y actualizo el
reporte con la sección `FINAL_WINDOWS_VALIDATION`.

---

## 6. CONFIRMAR QUE EL CORREO SIGUE APAGADO

El runner fuerza las flags **antes** de tocar Python, y además hay un
guardia dentro de pytest que hace explotar cualquier intento de alcanzar
SMTP/Resend/Gmail real. Para confirmarlo después:

```powershell
cd C:\Users\fotov\.openclaw\workspace\crm_norkevin
Select-String -Path artifacts\pre_cutover_validation\windows_full.log -Pattern "Safety flags fijadas"
```

Debe mostrar:

```
DISABLE_OUTBOUND_EMAIL=1  OUTBOUND_EMAIL_ENABLED=0  ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS=0
```

Verificación adicional — la bandeja local no debe haber crecido:

```powershell
Get-Item data\mail_outbox.json | Select-Object Length, LastWriteTime
```

Si aparece la línea `ADVERTENCIA (flag peligrosa detectada...)` en el log,
significa que el entorno traía una flag peligrosa activa; el runner **la
pisó igual**, pero conviene revisar de dónde salía.

---

## 7. CÓMO ABORTAR

Es seguro cortar en cualquier momento: el runner **no escribe en datos
reales**. Los tests corren sobre una copia en un directorio temporal
(`CRM_DATA_DIR`, ver `tests/conftest.py`), y las migraciones escriben en
`artifacts\`, nunca en `data\`.

**Para detenerlo:**

```powershell
Get-Process powershell, python -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -like "*crm_norkevin*" -or $_.CommandLine -like "*pre_cutover*" } |
  Stop-Process
```

O simplemente cerrar la ventana de PowerShell (Opción B), o
`Unregister-ScheduledTask` (Opción C).

**Después de abortar:** quedará un `VALIDATION_STARTED.marker` sin
resolver. Es normal y no rompe nada — la próxima corrida limpia los
markers al arrancar.

**Si algo se ve raro y hay dudas:** cortar primero, preguntar después.
No hay nada en esta validación que sea urgente ni que se dañe por
interrumpirse a la mitad.

---

## 8. LO QUE ESTA VALIDACIÓN NO HACE

Garantizado por diseño, verificado estáticamente:

- ❌ No hace deployment ni cutover
- ❌ No envía ningún correo real (doble candado: flags de entorno + guardia en pytest)
- ❌ No levanta ningún túnel (ngrok/cloudflared)
- ❌ No toca `data\*.json` de producción (tests en tempdir aislado)
- ❌ No instala dependencias ni usa internet
- ❌ No modifica configuración global del sistema
- ❌ No activa `ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS` a nivel de entorno

Si falta Flask o pytest, **no los instala**: marca las fases como
`ENVIRONMENT_FAILURE` con el motivo exacto y continúa con lo que sí puede
correr.
