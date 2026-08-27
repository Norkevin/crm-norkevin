@echo off
REM ============================================================
REM  abrir_crm.bat -- arranca el CRM en STAGE 1
REM ============================================================
REM
REM STAGE 1 (ver CONTROLLED_CUTOVER_PLAN.md, fase 5): el CRM queda
REM operativo para leads, clientes, jobs, cotizaciones, pagos,
REM contratos y workflows, pero NINGUN correo real sale.
REM
REM Los correos se arman, se registran en mail_log y quedan en estado
REM bloqueado -- visibles y auditables, pero no enviados. Pasar a
REM STAGE 2 (envio manual aprobado) es una decision aparte, despues de
REM operar un tiempo sin incidentes.
REM
REM Doble clic para abrir. Se abre el navegador solo.
REM Para apagarlo: cerrar esta ventana.
REM
REM ------------------------------------------------------------
REM  POR QUE ESTO ESCRIBE UN LOG
REM ------------------------------------------------------------
REM  app.py usa logging.basicConfig(), que manda todo a la consola y
REM  NO deja rastro en disco. Sin un archivo, no hay forma de revisar
REM  despues el arranque ni los errores 500 (ni de confirmar que el
REM  proceso llego a levantar). Todo el output -- incluidos los
REM  tracebacks de Flask -- queda en logs\crm_runtime.log.

cd /d "%~dp0"

REM --- Kill switches de correo: STAGE 1, no negociable ---
set DISABLE_OUTBOUND_EMAIL=1
set OUTBOUND_EMAIL_ENABLED=0

REM --- Operaciones destructivas apagadas ---
REM /api/admin/reset-test-data queda bloqueado con 403 mientras esto
REM valga 0. Encenderlo es un acto deliberado, no un default.
set ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS=0

REM --- El cutover no se dispara desde aca ---
set ALLOW_CONTROLLED_CUTOVER=0

set PORT=8765
set FLASK_DEBUG=0

if not exist logs mkdir logs

REM ------------------------------------------------------------
REM  DETENER UNA INSTANCIA PREVIA ANTES DE ARRANCAR
REM ------------------------------------------------------------
REM  Bug real (21-ago-2026): al hacer doble clic teniendo el CRM ya
REM  abierto, el proceso viejo seguia ocupando el puerto 8765 Y el
REM  archivo de log. El .bat escribia CRM_STARTED.marker, abria el
REM  navegador... y el python nuevo no llegaba a levantar. Parecia
REM  reiniciado -- el navegador respondia-- pero seguia sirviendo el
REM  CODIGO VIEJO, sin ningun error visible. Cualquier cambio reciente
REM  simplemente no aparecia.
REM
REM  Ahora se detiene explicitamente lo que este escuchando en el puerto
REM  antes de arrancar. Solo mata al proceso duenno de ESE puerto, no a
REM  todos los python de la maquina. Los datos viven en data\, asi que
REM  detener el proceso no pierde nada.
set CRM_PID=
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT%" ^| findstr LISTENING') do set CRM_PID=%%p
if defined CRM_PID (
    echo Deteniendo la instancia previa del CRM ^(PID %CRM_PID%^)...
    taskkill /PID %CRM_PID% /F >nul 2>&1
    REM Esperar a que el puerto quede libre de verdad antes de continuar.
    ping -n 3 127.0.0.1 >nul
)

REM ------------------------------------------------------------
REM  HEALTH CHECK REAL (no basta con lanzar el proceso)
REM ------------------------------------------------------------
REM  Antes este .bat escribia CRM_STARTED.marker ANTES de saber si el
REM  CRM habia levantado. El 21-ago eso enganno: el marker decia
REM  "arrancando" mientras el proceso nuevo nunca llego a bindear el
REM  puerto (lo tenia ocupado el viejo), y el navegador respondia con
REM  codigo viejo sin ningun error visible.
REM
REM  Ahora se lanza un verificador en segundo plano que espera a que
REM  http://localhost:PORT/login responda de verdad y recien entonces
REM  escribe el marker -- o CRM_START_FAILED.marker si no responde.
del /q CRM_STARTED.marker CRM_START_FAILED.marker >nul 2>&1
start "" /min powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command ^
  "$ok=$false; foreach($i in 1..20){ Start-Sleep -Milliseconds 750; try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 'http://localhost:%PORT%/login'; if($r.StatusCode -ge 200 -and $r.StatusCode -lt 500){$ok=$true; break} } catch {} };" ^
  "$info=[ordered]@{estado=if($ok){'OPERATIVO'}else{'NO_RESPONDE'};fecha=(Get-Date -Format 'o');puerto=%PORT%;url='http://localhost:%PORT%';stage=1;DISABLE_OUTBOUND_EMAIL='%DISABLE_OUTBOUND_EMAIL%';OUTBOUND_EMAIL_ENABLED='%OUTBOUND_EMAIL_ENABLED%';ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS='%ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS%';ALLOW_CONTROLLED_CUTOVER='%ALLOW_CONTROLLED_CUTOVER%';log='logs\crm_runtime.log'};" ^
  "$dest=if($ok){'CRM_STARTED.marker'}else{'CRM_START_FAILED.marker'};" ^
  "$info | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path '%~dp0' $dest);" ^
  "if(-not $ok){ Add-Content -Encoding UTF8 (Join-Path '%~dp0' 'logs\crm_runtime.log') ('HEALTH CHECK FALLIDO: el CRM no respondio en el puerto %PORT% tras 15s.') }"

REM  El marker lo escribe UNICAMENTE el health check de arriba, cuando
REM  ya comprobo que el CRM responde. Escribir "ARRANCANDO" aca dejaba
REM  dos markers contradictorios si el arranque fallaba.

echo ============================================================
echo   CRM Norkevin / Astral -- STAGE 1 (correo saliente APAGADO)
echo ============================================================
echo.
echo   Astral Weddings      -^> astralweddingsgt@gmail.com
echo   Norkevin Photography -^> norkevinfoto@gmail.com
echo.
echo   Abriendo http://localhost:%PORT%
echo   Log del CRM: logs\crm_runtime.log
echo   Para apagar el CRM: cerrar esta ventana.
echo.

start "" http://localhost:%PORT%

echo ==================== ARRANQUE %date% %time% ==================== >> logs\crm_runtime.log
echo FLAGS: DISABLE_OUTBOUND_EMAIL=%DISABLE_OUTBOUND_EMAIL% OUTBOUND_EMAIL_ENABLED=%OUTBOUND_EMAIL_ENABLED% ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS=%ALLOW_DESTRUCTIVE_ADMIN_OPERATIONS% >> logs\crm_runtime.log

python app.py >> logs\crm_runtime.log 2>&1

echo.
echo El CRM se detuvo. Codigo de salida: %ERRORLEVEL%
echo ==================== DETENIDO %date% %time% (exit %ERRORLEVEL%) ==================== >> logs\crm_runtime.log
pause
