@echo off
REM run_windows_validation_launcher.bat
REM
REM Lanzador de doble-click para run_pre_cutover_validation.ps1 -- 100%
REM no interactivo (rehecho en la fase "cerrar el pre-cutover gate",
REM agosto 2026, a pedido explicito de Kevin: "no debe requerir click,
REM confirmaciones, input del usuario, ventana en primer plano, ni
REM mantener Terminal abierta").
REM
REM Que hace:
REM   1. Se para en su propia carpeta (cd /d "%~dp0"), sin importar desde
REM      donde se dispare el doble-click.
REM   2. Lanza PowerShell con -WindowStyle Hidden: no necesita quedar en
REM      primer plano ni visible para completar la corrida.
REM   3. No espera ningun input: -NoProfile evita que un profile.ps1
REM      personalizado interrumpa con un prompt; -ExecutionPolicy Bypass
REM      evita el prompt de "este script no esta firmado, desea
REM      ejecutarlo? [S/N]" que Windows muestra por defecto.
REM   4. Todo el estado real (arranco / sigue corriendo / termino bien /
REM      fallo a medio camino) queda en los markers que escribe el propio
REM      .ps1: VALIDATION_STARTED.marker, VALIDATION_COMPLETE.marker,
REM      VALIDATION_FAILED.marker -- cada uno con timestamp, exit_code,
REM      ruta de log y estado. Este .bat NO los escribe el mismo para no
REM      tener dos fuentes de verdad compitiendo.
REM   5. No hace deployment. No hace cutover. No levanta tunel. No toca
REM      data/*.json de produccion (los tests corren aislados en un
REM      tempdir, ver tests/conftest.py pytest_configure()).
REM
REM Esta ventana de consola (la del .bat) se cierra sola en cuanto lanza
REM PowerShell -- no hace falta cerrarla a mano ni mirarla.

cd /d "%~dp0"

echo Lanzando run_pre_cutover_validation.ps1 en modo oculto/no interactivo (%date% %time%) > windows_validation_launcher.log

start "" /min powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0run_pre_cutover_validation.ps1"

echo Lanzado. El progreso real queda en artifacts\pre_cutover_validation\windows_full.log y en los markers VALIDATION_*.marker de esta carpeta. >> windows_validation_launcher.log
exit /b 0
