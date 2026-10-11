# Flowing móvil — auditoría del repositorio

Fecha: 10 de octubre de 2026. Alcance: código local y fuentes oficiales; no inspección del despliegue vivo, cuentas Apple ni dispositivo físico.

## Sistema real

| Área | Evidencia y conclusión |
| --- | --- |
| Aplicación | `app.py`: Flask, Jinja, HTML/CSS/JavaScript. No React, Next.js ni frontend compilable independiente. `requirements.txt` declara Flask, Gunicorn, cryptography y pytest. |
| Persistencia | `src/storage.py`: JSON por tabla, archivos reemplazados atómicamente, caché y bloqueos por archivo dentro de un proceso. `locked_tables` permite rollback por excepción, pero no una transacción durable entre archivos ni coordinación entre procesos. Los SQL de migraciones no demuestran que el CRM operativo use ese esquema. |
| Hosting declarado | `render.yaml`, `Procfile`, `wsgi.py`: Gunicorn, Render y disco `/var/data`. La configuración efectiva de producción no se verificó. |
| Identidad | Google OAuth de servidor, `src/google_login.py`, sesión Flask y `tenants.login_email`. Un propietario por empresa en el esquema actual. Portales de clientes y miembros tienen otra autorización y no obtienen acceso a esta API móvil. |
| Empresas | `JsonStore` filtra lecturas/escrituras según contexto. `tenant-norkevin` corresponde a Astral; nunca inferir la marca desde el identificador. El bearer móvil fija su empresa en `g.mobile_identity` antes del acceso a datos, sin cambiar la cookie web. |
| Teams | `src/teams.py`: SQLite independiente, auditorías de respuestas de portal y Google Calendar. Registro de Teams condicionado por `wsgi.py`; el worker debe usar ese entrypoint y las mismas variables/configuración. |
| Avisos existentes | `_build_recent_notifications`: reconstruye leads, actividad de correo y respuestas Teams. `notification_reads` guarda lecturas por empresa. No era una cola persistente por usuario/dispositivo. Se conserva sin modificar. |
| PWA | `static/manifest.webmanifest`: standalone, scope `/`, iconos 192/512 y máscara. `service-worker.js` precarga offline/manifest y permite caché de `/static/`; no contiene push/notificationclick. Es base instalable, no prueba de instalación actual del iPhone. |
| Seguridad HTTP | Cookies HttpOnly/SameSite Lax y Secure según entorno; cabeceras de seguridad en `app.py`; guardia de cambios web por Origin/Referer/Sec-Fetch-Site. HTTPS real, proxy, CSP/CORS y cabeceras de producción pendientes de inspección. No se habilitó CORS para la app: URLSession nativo no lo necesita. |
| Trabajos de fondo | Hilo de workflow existente; recordatorios de correo apagados por defecto tras incidente histórico. La nueva cola móvil tiene su propio proceso explícito y no envía correos ni activa esos hilos. |
| Herramientas Apple | Xcode 27.0 (27A266a) instalado; propietario completó licencia y primera apertura. Builds Simulator y destino iOS sin firma aprobadas. Tras reiniciar el Mac (boot `1791684578`), el registro volvió a invalidarse por preferencia Cryptex; reimportar el DMG existente mediante API oficial, retirando registros de prueba, restituyó iOS 26 `Ready` (UUID `3165D51A-D387-46BB-861B-3B9A126068F9`). El Simulator propio iPhone 16 instala, ejecuta y captura la app. La build unsigned falló en `SecItemAdd`; la build normal de Simulator con firma ad hoc (`CODE_SIGN_IDENTITY=-`) pasó y permitió Keychain sin modificar seguridad del código. |

## Eventos y significado

| Tipo | Fuente persistida | Qué comunica la primera versión |
| --- | --- | --- |
| Lead | `leads`, empresa del registro | Consulta nueva después del baseline. Excluye imports reconocidos de Studio Ninja. |
| Pago | `payments.status == Pagado`, trabajo existente de la misma empresa | **Pago registrado en Flowing**, no confirmación independiente del banco/proveedor. No avisa pendientes, fallidos, honorarios ni pagos históricos importados. Abonos parciales y confirmación de proveedor requieren integración específica posterior. |
| Boda | Trabajo Confirmado, `boda_date`, Guatemala | T-7 y T-1. Clave incluye fecha y ventana; cambios/cancelación impiden entregar recordatorios obsoletos aún en cola. |
| Teams | Auditoría `response`/`calendar_response`, aceptada/rechazada | Respuesta real de cobertura a la administración autorizada. Invitación/asignación al trabajador queda fuera de este primer incremento de propietarios. |
| Correo | `mail_log`: failed/blocked/bounced | Revisión de un correo no entregado. La apertura/clic **no** se convierte en respuesta del cliente. No se encontró en este análisis un pipeline de correo entrante utilizable para respuestas. |

Primera lectura por empresa: registra un baseline y no genera avisos del historial. Hay que inicializar el worker en staging antes de provocar eventos de prueba. La reconciliación de snapshots no captura necesariamente cambios que se crean y borran entre escaneos; sustituirla por un outbox dentro de la transacción del dominio cuando se migren esas operaciones a una base transaccional.

## Bloqueos y riesgos

1. Xcode y runtime permiten QA en el Simulator propio iPhone 16 (`BC5B50F1-67F2-4A21-BB5E-3B2EB1FA385F`). No hay iPhone físico conectado ni identidades de firma disponibles; firma de distribución, identificador definitivo y capacidad push siguen pendientes.
2. APNs requiere configuración de servidor y prueba física. El adaptador existe, pero no se enviaron notificaciones reales.
3. Fuente JSON y journal SQLite no comparten transacción. La cola propia sí es transaccional, con claves únicas y leases; no prometer captura instantánea/exactamente una vez de todos los cambios del CRM.
4. Worker necesita acceso al mismo disco persistente, Teams y variables del servidor. No añadir un servicio separado con otro disco creyendo que tendrá los mismos datos. La topología Render actual debe validarse antes de activar.
5. Sesión nativa conectada y capturas reales obtenidas con datos sintéticos; aún no hay aprobación visual global, validación física con app cerrada, firma de distribución ni revisión de privacidad App Store. Esta primera etapa permite consulta nativa; edición, documentos y operaciones completas siguen en las fichas web.

El bloqueo anterior de instalación/capturas se superó después del reinicio y reimportación del runtime. El error Cryptex no prueba contenido corrupto; el [caso reportado en Apple Developer Forums](https://developer.apple.com/forums/thread/830152) es contexto, no prueba de la causa exacta de este Mac.

## Evidencia nativa observada

Fixture HTTPS local `.local/iphone-qa-9fqio9g3`, con archivo de control privado y datos ficticios. Conexión por código aprobada mediante sesión/CSRF; la app quedó conectada. Inicio mostró 3 leads, 4 trabajos, Q8,500 cobrados y Q14,500 por cobrar. Bandeja mostró avisos de boda y lead; abrir Sofía llevó a su ficha y redujo sin leer de 6 a 5. Capturas PNG reales en `docs/mobile/screenshots/` documentan Inicio claro/oscuro/texto grande, listas, calendario, pagos, avisos y detalle. Son evidencia de ese fixture, no de producción.

Los dos ajustes de revisión quedaron verificados: fechas de avisos usan `label` (contraste 21:1 en claro y 17.01:1 en oscuro; `secondaryLabel` daba 3.44:1 en claro) y pagos muestran fecha larga `es_GT`. El dictamen permite entregar los ajustes dentro del alcance de las capturas reales del Simulator iPhone 16; no certifica toda la superficie, dispositivos físicos ni push. El seguimiento quedó pausado al completar el hito Simulator; estado registrado en `05-compilacion-y-qa.md`.

No se modificaron datos reales, producción, DNS, facturación ni credenciales.
