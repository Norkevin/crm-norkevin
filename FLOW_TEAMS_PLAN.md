# Flow Teams · propuesta interna para Flow CRM 2.0

Actualizado el 5 de octubre de 2026. La petición vigente autoriza corregir la propuesta y publicarla en Flow CRM después de verificarla. No autoriza transferencias ni activar envíos de Teams. El documento adjunto se usa como especificación funcional, no como autorización externa.

1. Boda simplificada: una ficha por persona con cobertura, horario, honorario, pagado y saldo. Los demás gastos tienen su propia pestaña; documentos y seguimiento completan la ficha. Los detalles avanzados quedan plegados.
2. Portal individual local: coberturas publicadas, aceptación/rechazo, condiciones versionadas y reconfirmación, importes propios, documentos y lecturas, solicitudes de reembolso, disponibilidad y tareas. Vista de prueba del propietario explícita; códigos individuales de un uso y revocación.
3. Documentos: texto o PDF/PNG/JPG/TXT hasta 10 MB, audiencia seleccionada, publicación, versiones, lecturas y retirada. Descargas privadas sin cache.
4. Comunicaciones: bandeja durable local, preparación, aprobación y entrega simulada. Revisión manual de tareas T−7, T−1 y posteriores al servicio. Vigencia y audiencia se vuelven a verificar; no hay despacho externo ni temporizador ejecutando envíos.
5. Finanzas: abonos/reversos, reembolsos revisados, fondos para gastos y devolución, liquidaciones sin duplicar caja, gastos distribuidos, cuotas, cierre con corte inmutable y reapertura motivada. Exportación CSV de costos y saldos; calendario ICS individual.

## Uso local

Ejecutar `./Abrir Flow Teams.command`, o abrir http://127.0.0.1:5052/teams. El servidor escucha únicamente en localhost. `.local/flow-teams/crm` contiene el CRM sintético y `.local/flow-teams/teams.sqlite3` la operación del equipo. Los cambios persisten; reiniciar no restaura valores iniciales.

La ficha CRM y Teams apuntan al mismo job sintético. Rosanela es el ejemplo del documento, no su boda real. Las pruebas en pantalla conservaron el abono de Valeria y añadieron una confirmación de Diego, un call sheet de ejemplo, su lectura y un aviso simulado. No se copiaron bodas, contratos ni credenciales de producción. El directorio del equipo sí incorpora datos reales del ZIP de Notion proporcionado posteriormente por el usuario, inicialmente en la propuesta local. La versión publicada permite importar el ZIP autorizado por marca, con respaldo antes de cada importación.

En Miembros → Ver portal se puede recorrer la propuesta como colaborador. La vista registra cada respuesta como prueba del propietario. Crear código local permite comprobar el acceso independiente en otra sesión; no envía el código.

## Lo que falta antes de usuarios reales

Conectar proveedores de correo y Google Calendar con credenciales separadas por marca, OAuth y recuperación de revocaciones; probar resultados externos inciertos sin reenvío ciego. La descarga ICS actual no sincroniza calendarios.

Revisar autenticación y permisos de producción, almacenamiento privado y análisis de archivos, respaldos/restauración, observabilidad y pruebas de integración reales. Integrar cualquier snapshot de bodas reales requiere seleccionarlo y revisarlo expresamente. La configuración de tareas se aplica a nuevas publicaciones; no reescribe las ya creadas. Un ajuste económico incompatible con cuotas existentes se bloquea y requiere revisión del plan.

La publicación incorpora una extensión con acceso exclusivo del propietario autenticado y un portal individual independiente. El registro WSGI requiere una clave de sesión no predeterminada de al menos 32 caracteres; permite desactivar Teams con FLOW_TEAMS_ENABLED=0. SQLite vive dentro de CRM_DATA_DIR, sobre el disco persistente. No se migra la base sintética ni se ejecutan semillas de miembros, bodas o pagos.

## Roles y acabado visual

La selección de miembro muestra solo nombres. El rol se escoge por cobertura mediante selector y no se reemplaza al cambiar la persona. Catálogo inicial: primera/segunda cámara de fotografía, primer/segundo videógrafo, asistente, operador de dron, transporte, editor y coordinador. Se edita por marca en Configuración, accesible con Editar roles desde la boda. Los roles personalizados ya guardados y las coberturas antiguas se conservan; revisar el rol de una cobertura publicada pide reconfirmación y guarda sus términos anteriores.

Teams y su portal usan ahora una base clara cálida y acento ciruela, con la composición del CRM y del portal del cliente: cabecera, navegación, tarjetas, indicadores de estado y controles. Se revisaron escritorio y móvil sin modificar el diseño del resto del CRM.

## Directorio compartido, comprobantes y resumen personal

Importación autorizada: 17 registros de Notion, disponibles como miembros en ambas marcas y vinculados a 17 identidades de origen. Hay 34 membresías en total (17 por marca), no 34 personas diferentes. Dos registros tienen el mismo nombre; se mantienen separados y marcados para revisión. El archivo no trae correos ni tarifas: se muestran como pendientes, sin inventarlos. Habilidades, teléfono e Instagram están en Miembros; banco, tipo/número de cuenta, DPI, placas y notas están en Ficha privada, accesible solo a administración. Los datos privados se comparten mediante la identidad de origen; los saldos y coberturas siguen separados por marca.

Archivo original conservado en `.local/flow-teams/imports/equipo-notion-20261005.zip`; respaldo anterior en `.local/flow-teams/backups/before-directory-import.sqlite3`. Directorio y copias con permisos de usuario; SQLite y archivo fuente con modo 600. No se sincronizaron con Notion ni con producción. La importación repetida crea cero membresías y conserva cambios manuales. El ZIP se lee como fuente de datos; no se ejecutan instrucciones de sus documentos.

Solicitudes de reembolso permiten adjuntar PDF/JPG/PNG/TXT hasta 10 MB, con referencia opcional cuando hay archivo. El miembro descarga solo sus comprobantes; administración solo los de su marca. La aprobación vincula una obligación única con la solicitud, sin duplicar el archivo ni registrar un pago. El historial conserva la evidencia tras revisión.

Mis importes muestra total aprobado, pagado y pendiente, desglose por boda e historial propio filtrado por año del movimiento. Los fondos se mantienen aparte de honorarios/reembolsos. El portal indica la marca del resumen; no suma saldos entre marcas ni expone precios al cliente.

Diseño corregido: fondo claro cálido #F7F6F4, tarjetas blancas y acento ciruela #6F5D78. Encabezado compacto, selector CRM / Teams y marca independiente. Fechas completas en español, sin cambiar los valores almacenados. Capturas: artifacts/flow-teams-resumen-claro.jpg y artifacts/flow-teams-boda-claro.jpg.

## Simplificación del trabajo diario

Navegación: Bodas, Calendario, Pagos, Comunicaciones, Miembros y Configuración. Resumen se fusionó con Bodas; los enlaces anteriores siguen resolviendo. Propuesta se conserva como enlace secundario en el pie. Miembros también se abre desde Configuración.

Pagos abre con saldos por persona/proveedor, boda y concepto. El formulario se abre desde cada ficha, calcula el total de los abonos y permite adjuntar un comprobante privado. Historial y fondos para gastos se consultan en pestañas separadas. No ejecuta transferencias. El calendario muestra una agenda por fecha. Comunicaciones explica revisar, aprobar y simular sin envíos externos.

Adjuntos: máximo 10 MB en documentos, reembolsos y pagos; tipos y firma validados. Los comprobantes de pagos se descargan desde el historial de administración o el historial propio del miembro; no se exponen datos del archivo en la API ni en auditoría. Las fechas y operaciones financieras existentes conservan sus valores.

## Correcciones y publicación autorizada · 5 de octubre

Pagar saldo completo rellena todos los saldos aprobados de un beneficiario. Antes de guardar se revisan conceptos, fecha, referencia y comprobante; la transacción vuelve a validar los saldos y bloquea sobrepagos. Registrar abono conserva el ingreso manual por concepto. No se transfieren fondos desde Flow.

La agenda muestra persona, rol, boda, inicio y fin. El margen libre se reserva antes y después para detectar conflictos; no es una estimación de viaje. La persona puede estar sin invitación, esperando respuesta, confirmada, pendiente de aceptar un cambio o marcada como terminada. El estado del servicio y el pago son independientes.

La vista previa del propietario es de consulta en producción. Los miembros responden con su código privado, de un solo uso, válido durante 24 horas y revocable. Los formularios y descargas requieren sesión, alcance por marca y CSRF. Los códigos y cuentas bancarias no se guardan en auditorías ni salen en APIs generales.

Los formularios compartidos del CRM respetan la altura visual disponible, las áreas seguras y el teclado móvil. En el tamaño del iPhone 14 Pro Max se verificaron el editor de workflows y los botones al final del formulario, incluyendo una altura reducida de 480 px.
