# Calendario y bandeja conectados a las fuentes de la web

Estado al 10 de octubre de 2026, Guatemala: correcciones implementadas, verificadas con datos ficticios y compiladas. **La publicación de este incremento en el servidor y su instalación actualizada en el iPhone siguen pendientes.** La preview anterior sí estaba instalada y conectada según el propietario; esa confirmación no acredita estas correcciones nuevas.

## Calendario

La app consultaba solo registros directos de `calendar`; la web también incorpora trabajos canónicos y fechas tentativas. Ahora ambas usan `_calendar_events()` en `app.py`. `GET /api/mobile/calendar?month=YYYY-MM` devuelve los registros del mes de la empresa representada por el bearer.

La respuesta incluye trabajos, eventos, bloqueos y tentativas. Incluye rangos que empiezan en otro mes y siguen dentro del consultado. Excluye bloqueos liberados y espejos antiguos de trabajos, siguiendo la fuente web existente. Los trabajos completados siguen visibles cuando la web los incluye. La consulta mensual no hereda el corte de 50 registros de las listas generales; una prueba compara 61 eventos del mismo día.

SwiftUI marca fechas ocupadas con punto lleno y tentativas con círculo. La agenda muestra el rango y el tipo de registro. Los días y meses usan `America/Guatemala`. Cambiar de mes, volver a primer plano o actualizar consulta de nuevo el servidor; una respuesta tardía de otro mes no sustituye el mes elegido.

Con texto accesible, la agenda seleccionada aparece antes de una lista vertical de días. Cada día anuncia disponibilidad y conserva un área pulsable de al menos 44 puntos. Las capturas verifican únicamente lo visible; no certifican VoiceOver integral.

## Bandeja

La web construía su actividad mediante `_build_recent_notifications()`, mientras la app leía solo un journal alimentado por un worker manual. Al no arrancar ese worker, la bandeja podía estar vacía aunque existieran avisos web. Su baseline inicial tampoco importaba el historial.

Ahora cada `GET /api/mobile/notifications` llama a la fuente web y `MobileJournal.sync_inbox()` refleja sus leads, correos y respuestas de Teams en SQLite. No exige iniciar el worker ni configurar APNs o Fernet para consultar la bandeja. Los IDs son deterministas por empresa, destinatario y evento; usan las claves existentes `lead:`, `mail-alert:` y `teams:` para evitar duplicar un aviso ya observado por el worker.

El estado leído de la web se copia solo al insertar por primera vez. Las consultas posteriores actualizan título, destino y fecha, preservando `read_at` móvil. Marcar un aviso desde la app no escribe `notification_reads` web ni modifica JSON del CRM. La sincronización no toca `observed`, `baselines` ni `deliveries`; importar historial no crea un backlog push.

La lista se ordena por fecha de actividad y rowid. El cursor conserva su contrato entero y ancla la fecha del aviso dentro de la empresa y destinatario autorizados. Fechas iguales y fechas actualizadas fuera del orden de inserción se paginan sin omisiones en las pruebas. Cursores inexistentes o de otro destinatario/empresa devuelven una página vacía; los valores fuera de int64 se rechazan.

Los destinos admiten solo rutas internas de leads, trabajos y Teams. Se vuelven a comprobar al sincronizar y abrir. Un correo cuyo lead desapareció conserva el trabajo válido indicado por la fuente web. Las respuestas de Teams de coberturas secundarias se resuelven desde trabajos y calendario filtrados por empresa. El bearer mantiene su empresa aunque el navegador tenga una cookie de otra cuenta.

## Avisos con la app cerrada

El servidor mantiene `FLOW_MOBILE_PUSH_ENABLED=0`; la preview de Personal Team no incluye la capacidad APNs. La configuración ahora distingue la bandeja de los avisos con la app cerrada y deshabilita activar/probar cuando el servidor no los ofrece. No se compró membresía, no se configuró APNs y no se probó recepción física push.

El worker sigue siendo una herramienta independiente para eventos y entregas APNs futuros, sujeto a autorización y configuración específicas. Consultar el calendario o la bandeja no inicia ese proceso ni envía correos.

## Pruebas y compilación

| Comprobación | Resultado y alcance |
| --- | --- |
| Suite completa del CRM | 1572 aprobadas, 1 omitida; 29.04 s. Ejecutada antes de la guardia final del cursor. |
| Pruebas específicas tras la guardia | 36 aprobadas; 1.31 s. API nativa, bandeja y validación del cursor. |
| Swift/Foundation | 28 verificaciones compiladas y ejecutadas; contratos, rangos y fechas. No equivalen a una build iOS. |
| Checker HTTPS loopback | Aprobado con datos ficticios, TLS verificado, pairing sesión/CSRF, consultas, aislamiento, calendario, lectura/destino de avisos y logout. |
| Build Simulator final | Exit 0; firma local `CODE_SIGN_IDENTITY=-`, 4.91 s. |
| Build iOS para dispositivo | Exit 0; preview firmada con Personal Team y bundle `com.flowingcrm.mobile.preview`. Build con el iPhone como destino: 9.46 s; build genérica final: 6.00 s. |

Las pruebas de calendario comparan su salida con el contexto real de la web, rangos entre meses, año bisiesto, fechas inválidas, trabajos canónicos/completados y aislamiento por bearer. Las de bandeja comprueban actividad histórica y nueva sin worker, ausencia de entregas, JSON web intacto, lecturas persistentes, correo y Teams secundario, deduplicación, 105 avisos paginados y cursores ajenos.

Logs completos locales, conservados por ContextShears:

- Suite: `.context-shears/artifacts/command-20261011T051952.593132Z-8d8d55a48efb.log`.
- API/bandeja tras guardia: `.context-shears/artifacts/command-20261011T052144.386206Z-693b1a8b1358.log`.
- Swift: `.context-shears/artifacts/command-20261011T051919.801748Z-0db23ca2da17.log`.
- HTTPS loopback final: `.context-shears/artifacts/command-20261011T054758.788398Z-61a9112b2ad5.log`. La comprobación consulta el mes del bloqueo ficticio incluso si empieza en el mes siguiente.
- Build Simulator final: `.context-shears/artifacts/command-20261011T053902.233581Z-704c9bc29db0.log`.
- Build Personal Team final genérica: `.context-shears/artifacts/command-20261011T053952.545173Z-aecf2d7941a1.log`; destino físico: `command-20261011T052552.653739Z-e9d978b600bc.log`.

## Evidencia visual y entrega pendiente

Las imágenes `calendario-ocupadas-claro.png`, `calendario-ocupadas-oscuro.png` y `calendario-ocupadas-texto-grande.png` son UI SwiftUI ejecutada en el Simulator propio iPhone 16/iOS 26, con datos ficticios y HTTPS loopback. Claro y oscuro muestran ocupación, tentativa y bloqueo por rango. Texto grande acredita la agenda visible; no el selector completo ni toda la vista. Ver [registro de capturas](screenshots/README.md).

La comprobación independiente final cerró los dos hallazgos de la revisión: fecha/rango con contraste `.primary` y «Hoy» con área pulsable de 44×44 puntos. `avisos-web-sincronizados.png` muestra la bandeja ejecutada con datos ficticios; `avisos-push-apagado.png` muestra activar/probar deshabilitados con push no disponible.

La build física está firmada en una copia aislada sin APNs; el proyecto original conserva sus entitlements. El perfil comprobado vence el **17 de octubre de 2026 a las 21:10:20 en Guatemala**, equivalente al 18 de octubre a las 03:10:20 UTC. Esta corrección no cambia el vencimiento.

Quedan por registrar el despliegue de estas correcciones, la instalación actualizada y la comprobación del calendario y la bandeja con la cuenta real del iPhone. La evidencia sintética, la compilación y la firma por sí solas no confirman esos pasos. App Store, TestFlight, compras y push físico quedan fuera de esta entrega.
