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
| Herramientas Apple | Auditoría inicial: Swift 6.3 y CommandLineTools, sin Xcode. Actualización del mismo día: Xcode 27.0 (27A266a) instalado en `/Applications/Xcode.app`; falta aceptar su licencia y completar primera apertura. Build iOS devolvió exit 69; aún no hay Simulator ejecutado ni UI compilada. Verificaciones Foundation con herramientas existentes aprobadas. |

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

1. Xcode completo ya instalado. Primera apertura/licencia de Apple, firma, identificador de aplicación y capacidad push pendientes. No se ha comprado ninguna cuenta.
2. APNs requiere configuración de servidor y prueba física. El adaptador existe, pero no se enviaron notificaciones reales.
3. Fuente JSON y journal SQLite no comparten transacción. La cola propia sí es transaccional, con claves únicas y leases; no prometer captura instantánea/exactamente una vez de todos los cambios del CRM.
4. Worker necesita acceso al mismo disco persistente, Teams y variables del servidor. No añadir un servicio separado con otro disco creyendo que tendrá los mismos datos. La topología Render actual debe validarse antes de activar.
5. Pantallas nativas, API y contrato están escritos; UI iOS, OAuth en Safari, firma, privacidad App Store y comportamiento con app cerrada no están validados. No se publica este incremento como aplicación terminada.

No se modificaron datos reales, producción, DNS, facturación ni credenciales.
