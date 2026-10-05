# Evidencia y alcance de las pruebas

5 de octubre de 2026. Datos sintéticos; CRM aislado y SQLite temporal. No se prueban proveedores de mensajería externos. Se verificó el registro WSGI de producción en un directorio temporal aislado.

## Resultado actual

- Teams: 61 pruebas aprobadas.
- Suite completa: 1,248 aprobadas, 1 omitida, ninguna fallida.
- Registro íntegro: `.context-shears/artifacts/command-20261005T112845.456141Z-42015bf7029d.log`.
- Python compilado, lanzador revisado con zsh -n y git diff --check sin errores.
- Revisión visual móvil: ficha por persona, navegación de documentos y portal independiente. Ajuste final de importes para evitar recorte en pantallas estrechas.

## Casos comprobados

Finanzas: abono Q500/Q1,500 con Q1,000 pendiente; presupuesto Q300/final Q280; salida distribuida; reversos y reintentos; pagos concurrentes sin duplicación/sobrepago; bases año de boda/año de pago separadas; GTQ y marca restringidos. Fondo Q600, gasto Q500 y devolución Q100 dejan Q500 de caja neta, un solo costo y saldo cero. Reembolso aprobado produce una obligación única; gasto compartido exige suma exacta. Cuotas incompletas y ajustes incompatibles se bloquean. Cierre conserva el corte cuando el ingreso comercial cambia y permite saldar deudas; reapertura motivada.

Portal: solo trabajos/importes propios publicados; honorarios en borrador, precio al cliente, margen y datos ajenos excluidos de la API. Códigos de un uso, expiración y ausencia de texto secreto en registros. CSRF, revocación de sesiones y rechazo de acciones de propietario. Condiciones antiguas no aceptan versión nueva; dos aceptaciones simultáneas con solapamiento no producen dos coberturas firmes. Rechazo libera plaza y conserva deuda anterior. Disponibilidad retirada conserva historial y permite confirmar.

Documentos y tareas: audiencias separadas, lectura por versión, descarga privada, retirada, firma de archivo válida y rechazo de HTML disfrazado. Nuevos términos cancelan tareas/avisos anteriores. Call sheet faltante bloquea recordatorio; reintentos no duplican avisos. Aprobación y simulación revalidan destinatario y condiciones. Calendario ICS solo incluye turnos propios y no dinero ni compañeros. CSV filtra marca, año de boda, miembro y categoría, neutraliza fórmulas y no exporta secretos.

Regresión: suite existente del CRM conservada; Teams se registra desde el launcher aislado y desde WSGI con autenticación del propietario y una clave de sesión segura. app.py sin cambios. Las pruebas no certifican una integración externa todavía inexistente.

## Recorrido real de la interfaz local

Se conservó el abono de Valeria y su servicio realizado. En la boda sintética Rosanela se publicó la cobertura de Diego, se confirmó desde la vista de prueba del propietario, se creó/publicó un call sheet destinado a él y se registró su lectura. Un aviso pasó preparado → aprobado → simulado. No salió correo, invitación ni dinero. Los cambios persisten tras reiniciar.

Capturas: `artifacts/flow-teams-simplificado.jpg` y `artifacts/flow-teams-portal-local.jpg`. Los registros de prueba quedan explícitamente rotulados. Respaldo anterior al incremento: `artifacts/teams-backups/teams-20261005-022449.sqlite3`.

## Límites de aceptación

F01–F13 y F16 tienen evidencia financiera automatizada; F14 cuenta con conservación/bloqueos locales, pero compensaciones externas no se ejecutan. El conteo usa jobs únicos para varios segmentos; no se modela todavía un catálogo separado de segmentos. S01–S04, S08–S11 y S13–S16 cuentan con controles locales y regresión. S05–S07 se verifican en avisos/términos locales, no contra Google/correo. S12 (OAuth revocado) y efectos de transporte externo quedan pendientes. No se declara terminado el producto publicable.

## Reproducir

```sh
.venv/bin/python -m pytest -q tests/test_flow_teams.py
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile src/teams.py src/teams_features.py src/teams_portal.py tools/run_flow_teams_local.py
zsh -n 'Abrir Flow Teams.command'
git diff --check
```

## Roles y diseño

Dos pruebas adicionales comprueban roles diferentes por cobertura para un mismo miembro, catálogo por marca sin reescribir acuerdos y cambio de rol con términos anteriores/reconfirmación. Verificación en pantalla: elegir Valeria no selecciona un rol; elegir Asistente y cambiar a Diego conserva Asistente. No se guardó esa cobertura de prueba. Editar roles desde la boda abre el catálogo correcto.

Revisión visual a 1280×900 y en el tamaño móvil original: cabecera, resumen, navegación, tarjetas y formulario sin recortes. Portal del equipo revisado con el nuevo acabado. La ampliación temporal se restableció. Capturas: artifacts/flow-teams-diseno-equipo.jpg, artifacts/flow-teams-roles-independientes.jpg.

## Directorio y evidencia adjunta

Cinco pruebas adicionales: carga de comprobante y reintento idempotente, aislamiento de descargas y aprobación; archivos disfrazados/sobredimensionados, importe cero y CSRF; resumen propio para dos bodas con filtro de historial sin alterar saldos; exclusión de DPI/cuenta de resumen, portal, auditoría y resultado de comandos; importación de identidades compartidas, homónimos, correos faltantes, ceros iniciales y conservación de ediciones en reimportación. Las pruebas usan datos ficticios.

La importación real del ZIP autorizado tiene 17 perfiles privados y 17 membresías importadas por marca. Repetirla crea cero registros. No se registran sus datos personales en estos documentos ni en logs de validación. Se conserva una copia privada de la fuente para la futura migración.

Diseño corregido: fondo claro cálido #F7F6F4, tarjetas blancas y acento ciruela #6F5D78. Encabezado compacto, selector CRM / Teams y marca independiente. Fechas completas en español, sin cambiar los valores almacenados. Capturas: artifacts/flow-teams-resumen-claro.jpg y artifacts/flow-teams-boda-claro.jpg.

Revisión de diseño final: resumen y ficha de boda comprobados a 1280 × 900 y 369 × 857. Fecha seleccionada en abono cambia a “6 de octubre de 2026”, conservando el valor canónico del formulario; no se guardó un movimiento. La prueba de formato verifica cambio de año, año bisiesto y marcas con zona horaria sin desplazar el día. Selector nativo para cuotas en lugar de texto con fechas ISO.

Capturas finales del diseño claro: `artifacts/flow-teams-resumen-claro.jpg`, `artifacts/flow-teams-boda-claro.jpg`, `artifacts/flow-teams-pagos-claro.jpg`. Capturas de móvil en `artifacts/flow-teams-resumen-movil.jpg` y `artifacts/flow-teams-boda-movil.jpg`. Se revisó también el portal y se comprobó la actualización de la fecha al añadir una segunda cuota, sin guardar cambios financieros.

## Abonos con comprobantes y agenda

Pruebas nuevas: pago con PDF de más de 5 MB, reintento sin duplicación, saldo exacto, descarga de propietario y miembro, bloqueo a compañero y otra marca, exclusión del archivo en resumen/auditoría, HTML disfrazado, distribución inválida y CSRF. Límite exacto de 10 MB aceptado; 10 MB más un byte rechazado. Reembolso multipart de 6 MB aceptado.

Se verificaron en navegador la selección de persona, cálculo automático de Q500, selección de archivo local sin guardar un pago, búsqueda de persona en la agenda y explicación de comunicaciones. Capturas: artifacts/flow-teams-pagos-sencillos.jpg, artifacts/flow-teams-abono-comprobante.jpg y artifacts/flow-teams-agenda-clara.jpg.

## Revisión para publicación

Pruebas nuevas: ruta persistente sin semillas de bodas/pagos, acceso del propietario desde una IP externa, modo desactivado, vista previa de consulta, acceso independiente del miembro, importación ZIP con CSRF por marca y sin datos bancarios en API/auditoría, saldo completo después de un abono con centavos y rechazo de repetición.

Recorrido móvil con emulación de 430 × 932 y 430 × 480 CSS px: editor de workflows y correo de una boda. Se comprobó que Guardar/Enviar quedan dentro del formulario desplazable y que no hay desbordamiento horizontal. No se guardó ningún workflow ni se envió correo. Esta emulación no sustituye una comprobación física en Safari del iPhone.

En Pagos se abrió el saldo completo de un proveedor sintético con tres conceptos (Q600 + Q300 + Q100); el total se rellenó en Q1,000 y la referencia quedó vacía para su revisión. No se guardó un movimiento durante esta prueba visual.
