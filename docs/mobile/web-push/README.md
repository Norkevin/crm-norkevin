# Flowing Web instalada y notificaciones

11 de octubre de 2026, Guatemala. El propietario decidió aplazar Apple Developer y eligió la web instalada. Confirmó que «Flowing Web» abre en el iPhone con sus datos reales. La preparación de App Store permanece aislada en su rama; la app nativa conserva su firma semanal y APNs apagado.

## Experiencia y alcance

En Safari: abrir `https://flowingcrm.com`, Compartir → Añadir a pantalla de inicio, y abrir desde ese icono. La web instalada no depende del perfil de Xcode. Apple admite Web Push para aplicaciones de pantalla de inicio desde iOS 16.4 sin membresía Apple Developer.

La campana distingue permiso del navegador, suscripción registrada y disponibilidad del proceso de envío. Activar requiere un toque del usuario. Desactivar afecta únicamente este navegador. La prueba confirma primero que quedó en cola; la recepción con la app cerrada requiere confirmación física. Los detalles están ocultos en la pantalla bloqueada por defecto y el aviso abre la bandeja.

La fuente son los avisos existentes de la web: leads, actividad de correo y respuestas de Teams, incluidas coberturas secundarias. Aperturas/clics de correo no se presentan como respuestas del cliente. No se inventan avisos de otros módulos. La lectura de la bandeja conserva sus reglas web.

## Servidor

`FLOW_WEB_PUSH_ENABLED` es independiente de `FLOW_MOBILE_PUSH_ENABLED`. Este último permanece en 0. `requirements.txt` incorpora `pywebpush==2.5.0`; el arranque web usa `python tools/start_web_service.py` para supervisar el proceso de envío en el mismo servicio y disco persistente. No se crea un servicio comercial adicional.

El almacenamiento está bajo `CRM_DATA_DIR/webpush`: journal SQLite, clave de cifrado de suscripciones y clave VAPID persistente. Directorio 0700 y archivos de credenciales 0600; creación bloqueada entre procesos, sin claves en stdout, HTML, logs o Git. La clave pública VAPID se devuelve únicamente en la API autenticada. Conservar estos archivos en las copias del disco; no rotarlos para cada despliegue.

Registro, vinculación de una suscripción existente, baja y prueba exigen propietario activo de la empresa, Origin propio y CSRF. Las suscripciones activas de otra cuenta no pueden apropiarse. La baja y logout cancelan solo las entregas del navegador correspondiente. El registro en una nueva sesión vincula la suscripción ya autorizada sin revivir una revocada.

Solo se aceptan proveedores HTTPS conocidos de Apple, Chrome/FCM y Firefox, con DNS público, sin redirects ni proxies de entorno. Las credenciales de cada suscripción se cifran; la biblioteca estándar de Web Push cifra el contenido de transporte y firma VAPID. Los avisos no contienen cookies ni códigos de acceso.

La primera observación y la reactivación tras pausa establecen baseline: no enviar historial. Un reinicio normal conserva los reintentos. Un fallo de lectura conserva cola y observaciones; no se interpreta como desaparición de eventos. El worker importa la app con schedulers de correo desactivados y lee TeamsStore directamente, sin registrar Calendar ni enviar invitaciones. La web principal debe continuar aunque falle push.

## Comprobación y entrega

Pruebas completas del CRM: **1612 aprobadas y 1 omitida**, 25.39 segundos; log `.context-shears/artifacts/command-20261011T064158.327527Z-489ce7f574d6.log`. Frontend: **26 pruebas Node VM aprobadas** tras recuperar el vínculo de una sesión nueva. Pruebas específicas de backend/auth/aislamiento: **87 aprobadas** tras las correcciones de supervisión, heartbeat, vinculación y lecturas fallidas.

La revisión independiente confirmó el cierre de tres defectos: el worker o una pausa fallidos ya no cortan la web; una sesión nueva vincula el navegador antes del logout; errores de leads o Teams conservan cola, observaciones y heartbeat. Confirmación focal: 6 pruebas Python y 4 Node, sin proveedor real.

Revisión de UI ejecutada en Chrome, viewport 430×932 y datos ficticios: se corrigió el popover móvil que salía por la izquierda. Confirmación final: x=16, ancho=398, borde derecho=414 y botón de activar de 44px. `avisos-movil.png` captura esa interfaz ejecutada; no es una captura de iPhone físico ni acredita push recibido. No se solicitó permiso al navegador durante esa revisión.

Tras integrar la actualización de galerías del PR #20 en la rama, 42 pruebas de galería/Web Push/fixture/supervisor aprobadas; log `.context-shears/artifacts/command-20261011T065213.183467Z-a064449e094b.log`. Las dos comprobaciones finales del runner y las nueve de PWA también pasaron tras el ajuste visual y el auto-reload exclusivo del fixture.

## Publicación y activación

El [PR #21](https://github.com/Norkevin/crm-norkevin/pull/21) quedó fusionado en `af83ffb0d793675f456c2982ef400f2d8a35ed71`. Render confirmó el código publicado. Su configuración manual todavía usaba el comando antiguo; se guardó exclusivamente `python tools/start_web_service.py` en Start Command después de confirmar el código nuevo Live.

Despliegue de arranque `dep-db5j61g473hc73bc8b90`: iniciado el 11 de octubre a las 00:56:06 Guatemala, estado **Deploy succeeded | Live**, 20.5 segundos. El log acreditó el comando nuevo, pausa sin envíos y Gunicorn atendiendo en el puerto existente.

Después se añadió únicamente `FLOW_WEB_PUSH_ENABLED=1` en el panel. `FLOW_MOBILE_PUSH_ENABLED` y las demás variables no se editaron. No se creó servicio ni se cambió plan/disco. Despliegue `dep-db5j7v4s728c73815tsg`: iniciado el 11 de octubre a las 01:00:12 Guatemala, estado **Deploy succeeded | Live**, 1m22s, fuente `af83ffb`. El log confirmó el wrapper y dos scans del worker con cero aceptaciones antes del opt-in. Captura privada de estado: `.local/render-web-push-live.png` en el checkout original de la conversación.

Probes sin credenciales después de publicar: `/login` HTTP 200 y `/api/web-push/status` HTTP 401 «Sesion expirada, inicia sesion de nuevo». Son comprobaciones de disponibilidad y protección. La campana real de la sesión autenticada mostró el estado disponible y el botón Activar habilitado; no se solicitaron permisos ni se suscribió ese navegador de revisión.

El propietario ya confirmó que la web instalada abre con sus datos reales. Se le dieron los pasos de cerrar/reabrir desde el icono, activar en la campana, Permitir en iPhone y enviar una prueba propia antes de cerrar la web. **Recepción física todavía pendiente de su respuesta.** Los tests con proveedor ficticio y un HTTP 2xx de un proveedor no sustituyen esa comprobación.

El fixture ficticio se cerró al finalizar la revisión. La preparación pagada de App Store continúa aplazada; este incremento no requiere esa membresía ni cambia el vencimiento de la preview nativa.

El runner `tools/run_mobile_qa.py --browser --web-push-ui` sirve solo datos ficticios en loopback HTTP, considerado contexto seguro por Chrome. Su sesión de revisión y proveedor falso existen únicamente dentro del runner; no se añaden a `wsgi`. El modo predeterminado sigue usando HTTPS para el QA nativo. Nunca se confía un certificado en el Mac ni se desactiva TLS de producción.

Fuentes: [Web Push en Apple](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers), [WebKit para pantalla de inicio](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/), [discos y procesos de Render](https://render.com/docs/disks).
