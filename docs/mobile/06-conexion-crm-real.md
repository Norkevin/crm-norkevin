# Conectar el iPhone al CRM real

El CRM real en `https://flowingcrm.com` tiene la API móvil activa y push apagado. La preview física está firmada e instalada. Tras el despliegue del PR #17, el propietario confirmó **«veo todo»** (Inicio con sus datos y Teams) y **«Sí, conserva la cuenta»** al volver a abrirla. La conexión, las consultas y la persistencia quedan confirmadas por el propietario; la comparación con la web y la observación independiente de esas vistas siguen pendientes. No se ha publicado la app en App Store ni comprado una membresía de Apple.

## Estado confirmado — 10 de octubre de 2026

| Comprobación | Evidencia |
| --- | --- |
| Servicio Render | `crm-norkevin`, ID `srv-d9b6aauq1p3s73espps0` |
| Revisión publicada | `67acfaa0e47a9869aaacd0f1e5128dc2aeabf8b7`, fusionada mediante [PR #17](https://github.com/Norkevin/crm-norkevin/pull/17) |
| Configuración guardada | El panel confirmó `FLOW_MOBILE_ENABLED=1` y `FLOW_MOBILE_PUSH_ENABLED=0` |
| Despliegue | `dep-db5guj740ujc73cfnij0`, fuente `67acfaa`, iniciado el 10 de octubre a las 22:23:40 Guatemala (UTC−06:00); completado en 58.6 segundos, estado **Live** |
| Probes independientes sin credenciales | `/login`: 200; `/api/mobile/overview`: 401 con el mensaje exacto «Conecta tu cuenta de Flowing.» |
| Consulta física | El propietario confirmó «veo todo» al preguntarle por Inicio con sus datos y Teams; todavía sin observación independiente de esas vistas |
| Reapertura física | El propietario confirmó «Sí, conserva la cuenta» sin generar otro código |

Se cambiaron solo los dos flags móviles en el panel. La evidencia privada de los valores guardados está en `.local/render-mobile-enabled.jpg` y permanece local, fuera del repositorio. Los probes acreditan disponibilidad del servidor y rechazo sin bearer; no consultaron clientes ni importes. La confirmación de conexión física procede del propietario, no de esos probes.

La preview instalada usa `com.flowingcrm.mobile.preview`, Personal Team y una copia aislada sin APNs. Su perfil expira el **18 de octubre de 2026 a las 03:10:20 UTC** (17 de octubre, 21:10:20 en Guatemala). La apertura física está confirmada; push no está incluido en esta build ni probado.

## Alcance de la conexión

La preview permite consultas por empresa a los datos existentes de Inicio, Leads, Trabajos, Calendario, Pagos y Teams. Trabajos se consulta desde Inicio y Más; asignaciones, invitaciones, pagos y demás acciones complejas continúan en las fichas web. Esta confirmación no acredita paridad nativa completa ni una revisión completa de VoiceOver. Las consultas no dependen del bundle ID de la preview ni de APNs. El manifiesto por sí solo no acreditaba la activación: los flags se aplicaron expresamente en Render y se comprobaron tras guardarlos. No se copiaron variables del fixture ni se instaló un servidor con datos reales en el Mac.

La API crea únicamente su journal `mobile.sqlite3` en el mismo disco persistente, con permisos 0600. Sesiones, preferencias y lecturas de avisos se guardan allí; no modifica trabajos, clientes, importes ni asignaciones del CRM. La primera conexión debe aprobarla el propietario desde la sesión Google de la empresa correcta. El bearer de la app no autentica rutas web y vuelve a comprobar que el propietario y la empresa sigan activos.

No se inició el worker móvil ni se activó push para esta primera conexión. La bandeja móvil necesitará una reconciliación posterior para recibir eventos; un inbox vacío no prueba que no existan actividades en la web.

## Confirmación física y comprobaciones pendientes

La generación y aprobación del código y la carga de consultas ya no son pasos pendientes: el propietario confirmó que ve Inicio con sus datos y Teams tras la corrección del formulario. La aprobación conserva sesión Google del propietario, CSRF y aislamiento por empresa.

1. Comparar una ficha y un importe conocidos con la web de esa empresa. No editar datos para esta validación.
2. Observar las vistas reales y revisar VoiceOver completo; la respuesta del propietario no sustituye esas comprobaciones.
3. La reapertura sin solicitar otro código está confirmada por el propietario. La captura independiente de las vistas sigue pendiente.

Registrar evidencia física sin cookies, códigos de autenticación ni bearer. Push permanece apagado y fuera de esta prueba.

Device Hub mostró «Screen Sharing Unavailable»: la vista interactiva del iPhone con iOS 26.5 requiere iOS 27 en este Mac. No se actualizó el teléfono. El propietario realizó los toques de conexión y la autorización Google; las capturas por CoreDevice siguen siendo una vía de revisión una vez abierta Flowing.

## Reversión

Desactivar `FLOW_MOBILE_ENABLED` bloquea la API y nuevas conexiones. Mantener el journal y su copia de seguridad; no borrar datos del CRM ni rotar secretos OAuth/Flask. `FLOW_MOBILE_PUSH_ENABLED` permanece en 0. La web conserva sus rutas, permisos y funcionamiento.

## Preparación y publicación

Antes de publicar, `main` en `a1f6b00` no contenía `src/mobile.py` y `POST /api/mobile/pair/start` sin sesión devolvía el 401 de autenticación antiguo. Ese 401 no demostraba que la API móvil existiera. El propietario cambió la elección de demostración por sus datos reales; el gateway de demostración se canceló antes de crear archivos. No se abrió un túnel ni se expuso el fixture del Mac.

Validación de esta preparación: 59 pruebas móviles, autenticación, aislamiento por empresa y credenciales aprobadas. Registro `command-20261011T031853.044682Z-87df56f0186d.log`. La revisión independiente no encontró defectos bloqueantes para consultas del propietario con push apagado; verificó además con datos sintéticos pairing de un uso, journal persistente/0600, bearer almacenado como hash, invalidación por cambio de propietario y logout. Esa revisión de preparación, por sí sola, no acreditaba la configuración efectiva de producción ni una sesión física conectada.

El 10 de octubre a las 21:25 Guatemala, el propietario autorizó expresamente publicar el cambio y activar el acceso con datos reales, manteniendo push apagado. El [PR #16](https://github.com/Norkevin/crm-norkevin/pull/16) quedó fusionado en `2fa2adf2ae47f5b089ddfab4dfbef79d2d469def`.

Antes de guardar los flags en Render, los probes públicos sin credenciales observaron 502 transitorio, seguido de recuperación: `/login` volvió a 200 y `/api/mobile/overview` pasó del mensaje antiguo 401 a 404 «No encontrado». Ese cambio coincidía con la nueva API aún desactivada y no acreditaba sus variables efectivas. Tras la activación se obtuvo el 401 «Conecta tu cuenta de Flowing.» indicado en el estado actual. No se consultaron clientes ni importes reales mediante esos probes.

El propietario completó el acceso al panel de Render. Se aplicaron y guardaron los dos flags móviles con Save, rebuild, deploy el 10 de octubre a las 21:32:30 Guatemala (UTC−06:00). El despliegue inicial `dep-db5g6jh42hec738rkung`, revisión `2fa2adf`, quedó Live; después fue sustituido por el despliegue del PR #17 indicado en el estado actual.

CoreDevice abrió la preview física con `-FlowingServer https://flowingcrm.com`, exit 0. Captura nativa privada `.local/iphone-real-onboarding.png` (1290×2796) confirmó esa dirección en la pantalla de conexión. La captura permanece local y no se sube al repositorio. En ese momento todavía faltaba generar y aprobar el código; la captura de onboarding no acreditaba consultas reales ni push.

## Corrección del origen en el formulario de vinculación

El propietario reportó «Solicitud desde otro sitio bloqueada. Abre Flow y vuelve a intentarlo.». El blueprint aplicaba `Referrer-Policy: no-referrer` también al formulario HTML `/mobile/connect`. En un POST de navegación esa política serializa `Origin` como `null`, según [Fetch Standard](https://fetch.spec.whatwg.org/#append-a-request-origin-header), y el guard de origen lo rechaza.

La corrección conserva `no-referrer` en `/api/mobile/*` y deja que el HTML herede la política web existente `strict-origin-when-cross-origin`. No cambia el guard, la aprobación del propietario, CSRF, la expiración del código ni el aislamiento. Una prueba real en Chrome, con formulario ficticio servido solo en loopback, reprodujo `Origin: null` antes y origen propio después. Evidencia privada `.local/pair-origin-fixed-browser.jpg`; no contiene datos reales ni se publica en Git.

La regresión falló antes de corregir la cabecera y pasó después. Resultado: 76 pruebas móviles, de seguridad web, autenticación y aislamiento aprobadas; log `command-20261011T042131.834287Z-aa0c01b5b67a.log`. Se verifican CSRF incorrecto, origen externo y `Origin: null` rechazados, origen propio autorizado y cabecera privada de la API intacta. Revisión independiente sin bloqueantes.

[PR #17](https://github.com/Norkevin/crm-norkevin/pull/17) fusionado en `67acfaa0e47a9869aaacd0f1e5128dc2aeabf8b7`. Render confirmó «Deploy succeeded» del despliegue `dep-db5guj740ujc73cfnij0`, fuente `67acfaa`, iniciado el 10 de octubre a las 22:23:40 Guatemala y terminado en 58.6 segundos. Captura privada `.local/render-pairing-fix-deployed.jpg`. Una ronda pública posterior confirmó `/login` 200 y la API móvil sin bearer 401 «Conecta tu cuenta de Flowing.».

El propietario confirmó que el error apareció después de introducir el código en el navegador, coherente con la reproducción. Recibió instrucciones para cancelar el código anterior, generar uno nuevo, abrir y recargar el formulario y volver a Flowing. Tras el reintento respondió «veo todo» al preguntarle por Inicio con sus datos y Teams, y «Sí, conserva la cuenta» tras reabrir. La conexión y la persistencia quedan confirmadas por él sin reinstalar la app; faltan la observación independiente y las comprobaciones de comparación y accesibilidad indicadas arriba. El servidor de reproducción local quedó cerrado.

CoreDevice aceptó la reapertura física con exit 0 y el proceso Flowing siguió activo. La captura posterior mostró el inicio de iOS, por lo que no se conservó ni se usó como evidencia de datos o sesión. La confirmación de persistencia procede del propietario. El seguimiento antiguo permanece pausado; el intento de eliminarlo fue rechazado por revisión automática porque solo estaba autorizado detenerlo. No se modificó su archivo ni se eludió esa revisión.
