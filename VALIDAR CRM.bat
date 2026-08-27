@echo off
REM ============================================================
REM  VALIDAR CRM.bat  --  acceso directo de Escritorio
REM ============================================================
REM  Ruta real del proyecto:
REM    C:\Users\fotov\.openclaw\workspace\crm_norkevin
REM
REM  POR QUE ESTE ARCHIVO ES ASI
REM
REM  1. CRLF obligatorio. La primera version quedo con finales de linea
REM     de Linux (solo LF). cmd.exe no parsea bien las etiquetas ni los
REM     bloques if(...) multilinea asi: la ventana se abria y se cerraba
REM     al instante, sin ningun mensaje. Ese fue el fallo real.
REM
REM  2. Sin bloques if(...) multilinea: solo ifs de una linea con goto.
REM
REM  3. Espacio antes de cada >> cuando lo precede una variable. Si la
REM     variable termina en un digito (por ejemplo %errorlevel% valiendo
REM     0), cmd lee "0>>" como redireccion del handle 0 y se rompe.
REM
REM  4. Toda salida pasa por :FIN, que siempre hace pause. La ventana no
REM     se cierra sola ni cuando algo falla.
REM
REM  5. El log se escribe al lado de este .bat (%~dp0), no a una ruta
REM     adivinada: si el Escritorio estuviera redirigido a OneDrive,
REM     %USERPROFILE%\Desktop podria no existir.
REM ============================================================

setlocal enabledelayedexpansion

set "PROYECTO=C:\Users\fotov\.openclaw\workspace\crm_norkevin"
set "LAUNCHER=%PROYECTO%\run_windows_validation_launcher.bat"
set "LOG=%~dp0VALIDACION_CRM_LOG.txt"
set "RESULTADO=DESCONOCIDO"

title Validar CRM Norkevin / Astral

echo ============================================================ > "%LOG%"
echo  VALIDACION DEL CRM - %date% %time% >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo  ============================================================
echo    VALIDACION DEL CRM  --  Norkevin / Astral
echo  ============================================================
echo.

REM --- 1. Carpeta del proyecto ---
if not exist "%PROYECTO%" goto SIN_PROYECTO
echo   [ok] Carpeta del proyecto encontrada.
echo   [ok] Carpeta: %PROYECTO% >> "%LOG%"

REM --- 2. Launcher de validacion ---
if not exist "%LAUNCHER%" goto SIN_LAUNCHER
echo   [ok] Launcher de validacion encontrado.
echo   [ok] Launcher: %LAUNCHER% >> "%LOG%"

REM --- 3. Python (lo necesita la suite). Se acepta python o el
REM        lanzador py, porque no todas las instalaciones ponen
REM        python.exe en el PATH.
set "TIENE_PY=no"
where python >nul 2>&1
if not errorlevel 1 set "TIENE_PY=si"
where py >nul 2>&1
if not errorlevel 1 set "TIENE_PY=si"
if "!TIENE_PY!"=="no" goto SIN_PYTHON
echo   [ok] Python disponible.
echo   [ok] Python disponible. >> "%LOG%"

cd /d "%PROYECTO%"
if errorlevel 1 goto SIN_CD

REM Markers viejos fuera, para no confundir una corrida anterior con esta.
del /q "VALIDATION_STARTED.marker" >nul 2>&1
del /q "VALIDATION_COMPLETE.marker" >nul 2>&1
del /q "VALIDATION_FAILED.marker" >nul 2>&1

echo.
echo   Ejecutando las 14 fases y la suite completa.
echo   Tarda entre 1 y 3 minutos. No cierres esta ventana.
echo.
echo   Lanzando validacion... >> "%LOG%"

call "%LAUNCHER%"
set "SALIDA=!errorlevel!"
echo   Launcher devuelto con errorlevel !SALIDA! >> "%LOG%"

REM El launcher interno lanza PowerShell oculto y vuelve enseguida, asi
REM que aca se espera al marker real en vez de suponer que ya termino.
set /a INTENTOS=0

:ESPERAR
ping -n 4 127.0.0.1 >nul 2>&1
set /a INTENTOS+=1
echo   ... esperando  !INTENTOS!
if exist "VALIDATION_COMPLETE.marker" goto TERMINO_OK
if exist "VALIDATION_FAILED.marker" goto TERMINO_FALLO
if !INTENTOS! GEQ 150 goto SIN_RESPUESTA
goto ESPERAR

:TERMINO_OK
set "RESULTADO=TERMINO"
echo.
echo  ============================================================
echo    LA VALIDACION TERMINO
echo  ============================================================
echo.
type "VALIDATION_COMPLETE.marker"
echo ---- VALIDATION_COMPLETE.marker ---- >> "%LOG%"
type "VALIDATION_COMPLETE.marker" >> "%LOG%"
goto RESUMEN

:TERMINO_FALLO
set "RESULTADO=FALLO"
echo.
echo  ============================================================
echo    LA VALIDACION FALLO A MEDIO CAMINO
echo  ============================================================
echo.
type "VALIDATION_FAILED.marker"
echo ---- VALIDATION_FAILED.marker ---- >> "%LOG%"
type "VALIDATION_FAILED.marker" >> "%LOG%"
goto RESUMEN

:RESUMEN
echo.
echo  ------------------------------------------------------------
echo   Resultado: !RESULTADO!
echo   Log en el Escritorio: VALIDACION_CRM_LOG.txt
echo   Pasale este resultado a Claude.
echo  ------------------------------------------------------------
goto FIN

:SIN_PROYECTO
echo   ERROR: no existe la carpeta del proyecto:
echo   %PROYECTO%
echo   ERROR: no existe la carpeta %PROYECTO% >> "%LOG%"
goto FIN

:SIN_LAUNCHER
echo   ERROR: falta run_windows_validation_launcher.bat en:
echo   %PROYECTO%
echo   ERROR: falta el launcher en %PROYECTO% >> "%LOG%"
goto FIN

:SIN_PYTHON
echo   ERROR: Windows no encuentra Python ni el lanzador py.
echo   La suite de pruebas lo necesita.
echo   ERROR: no hay python ni py en el PATH. >> "%LOG%"
goto FIN

:SIN_CD
echo   ERROR: no se pudo entrar a la carpeta del proyecto.
echo   ERROR: cd fallo hacia %PROYECTO% >> "%LOG%"
goto FIN

:SIN_RESPUESTA
echo.
echo  ============================================================
echo    LA CORRIDA NO RESPONDIO EN ~10 MINUTOS
echo  ============================================================
echo.
echo   Revisa: artifacts\pre_cutover_validation\windows_full.log
echo   ERROR: sin respuesta tras 150 intentos. >> "%LOG%"
goto FIN

:FIN
echo.
echo   (Esta ventana no se cierra sola. Presiona una tecla para salir.)
echo.
pause
endlocal
exit /b 0
