# Flowing para iPhone

Complemento SwiftUI del CRM Flask existente. La preview anterior está instalada en el iPhone y el propietario confirmó sus datos, Teams y la persistencia de cuenta. Este incremento corrige el calendario y la bandeja: está compilado y probado en Simulator; **su publicación en el servidor y su instalación actualizada en el iPhone siguen pendientes**. El CRM real en `https://flowingcrm.com` mantiene la API móvil activa y push apagado. No está publicada en App Store.

- Proyecto: `ios/Flowing.xcodeproj`; scheme compartido `Flowing`; iPhone, iOS 17 o posterior.
- API: `/api/mobile/`, desactivada por defecto en código; activada expresamente en el CRM real. Datos existentes, sin seeds reales ni escrituras financieras nuevas.
- Calendario: misma fuente de la web, reservas, eventos, bloqueos por rango y fechas tentativas; consulta el mes completo sin límite de 50.
- Notificaciones: cada consulta sincroniza la actividad web existente y nueva en el journal móvil; no necesita worker. APNs permanece opcional y separado.
- Diagnóstico, arquitectura y pruebas: `../docs/mobile/`.

## Corrección local de avisos y actualización — 13 de octubre de 2026

Estado: implementado y verificado localmente; publicación del servidor e instalación de la app nativa pendientes. Base: `6b86bd9`, que contiene Web Push y los cambios de `origin/main` conocidos al iniciar. Rama: `codex/flowing-useful-alerts`. No se han modificado datos de producción ni permisos push.

- Un nuevo lead produce una alerta, aunque genere el correo interno de respaldo y correos automáticos. El envío de respaldo y el historial se conservan. Fallos, bloqueos, rebotes y respuestas de Teams siguen visibles. No se incorpora recepción de respuestas de clientes: esa integración no existe en este flujo.
- La web reutiliza el snapshot inicial, consulta cinco avisos en la campana (con total sin leer y filtros completos), actualiza al recuperar foco/visibilidad/conexión y al recibir push. Mantiene polling de 45 segundos mientras está visible como alternativa sin push. Coalesce consultas simultáneas; un push durante una consulta fuerza otra al terminar. El historial completo y «marcar todas» incluyen las entradas fuera de las cinco visibles.
- La app nativa inicia las cuatro consultas de actualización en paralelo, mantiene su refresco al primer plano y periódico, refresca tras un push en primer plano y muestra la ficha antes de esperar la confirmación de lectura.
- El journal móvil incorpora una columna `hidden` de forma aditiva e idempotente. Los correos redundantes anteriores se ocultan al reconciliar, sin borrar filas, correos ni lecturas. La bandeja y el contador APNs excluyen esos registros. Al publicar, conservar una copia de `mobile.sqlite3`; un rollback al código anterior requiere adaptar sus INSERT posicionales a columnas explícitas o restaurar coordinadamente la copia, pues el esquema tendrá una columna adicional.

Medición reproducible con 100 leads, 200 trabajos y 600 correos sintéticos (6 fallos), mismo Mac y test client Flask, sin red:

| Medida | Antes | Después |
| --- | ---: | ---: |
| Consultas a `store.list` | 1.006 | 8 |
| Respuesta completa | 196,4 ms | 10,5 ms |
| JSON completo | 130.746 bytes | 17.454 bytes |
| JSON de campana, cinco entradas | 130.746 bytes enviados anteriormente | 985 bytes |

Los tiempos son muestras locales, no un SLA ni mediciones de Safari, Chrome móvil o iPhone real. El límite de trabajo y las cantidades de datos se comprueban en `tests/test_notification_efficiency.py`, sin umbral de tiempo frágil.

Validación: suite Python completa, 1.620 aprobadas y 1 omitida; 36 pruebas JavaScript de actualización, navegación y Web Push; compilación iOS Simulator con Xcode 27 y firma local aprobada. No se ejecutó la nueva app en un teléfono ni se probó recepción real de push tras este cambio. Las pruebas JavaScript usan un entorno simulado; no equivalen a una ejecución en Safari o Chrome reales.

Reproducción desde la raíz con el entorno Python del proyecto:

```sh
python -m pytest -q
python -m pytest tests/test_notification_efficiency.py -q -s
node --test tests/notifications_refresh.test.cjs tests/web_push.test.cjs tests/mobile_navigation.test.cjs
xcodebuild -project mobile/ios/Flowing.xcodeproj -scheme Flowing -configuration Debug -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' -derivedDataPath .local/notification-build CODE_SIGN_IDENTITY=- build
```

## Conectar al CRM real para una nueva sesión

1. Abre Flowing en el iPhone. Usa `https://flowingcrm.com` y pulsa **Conectar mi cuenta** para generar el código.
2. Abre [Conectar iPhone](https://flowingcrm.com/mobile/connect) en el navegador e inicia sesión con la cuenta Google de la empresa correcta.
3. Comprueba el nombre de la empresa e introduce el código del iPhone. La aprobación usa la sesión del propietario y CSRF.
4. Vuelve a Flowing y compara una ficha y un importe conocidos con la web. Cierra y vuelve a abrir la app para comprobar que conserva la sesión.

La sesión actual ya está conectada según la confirmación del propietario; no hace falta repetir estos pasos. Render confirmó `FLOW_MOBILE_ENABLED=1` y `FLOW_MOBILE_PUSH_ENABLED=0`. El despliegue anterior `dep-db5guj740ujc73cfnij0`, revisión `67acfaa` del PR #17, está Live. Los probes independientes dieron `/login` 200 y `/api/mobile/overview` 401 «Conecta tu cuenta de Flowing.»; acreditan disponibilidad y rechazo sin bearer. Esa evidencia corresponde a la versión anterior. Estado de este incremento: [Calendario y bandeja](../docs/mobile/07-calendario-y-bandeja.md). Historial de conexión: [Conexión al CRM real](../docs/mobile/06-conexion-crm-real.md).

Con este cambio local, `GET /api/mobile/notifications` refleja leads y respuestas de Teams, y solo correos fallidos, bloqueados o rebotados. La actividad de correo normal permanece en el historial del CRM. Las lecturas web se incorporan también a avisos móviles existentes; marcar leído desde la app escribe el marcador de lectura compartido. Consultar la bandeja no envía correo ni crea entregas push. El worker sigue siendo necesario únicamente para envíos APNs.

## Comprobar localmente

```sh
.venv/bin/python -m pytest -q tests/test_mobile_native.py tests/test_mobile_calendar.py tests/test_mobile_inbox_sync.py
swift run --package-path mobile/ios FlowingContractChecks
```

## Probar en staging

1. Revisar `docs/mobile/02-hitos-y-pruebas.md`, usar datos sintéticos y HTTPS. Configurar las variables de `mobile/.env.example` para ese entorno; activar `FLOW_MOBILE_ENABLED=1` y mantener `FLOW_MOBILE_PUSH_ENABLED=0` inicialmente.
2. Abrir el proyecto en Xcode completo, configurar equipo/firma y confirmar Bundle ID. La app solicita la URL HTTPS del CRM; la conexión usa un código autorizado en `/mobile/connect` con login Google vigente.
3. Conectar y consultar calendario y bandeja sin iniciar el worker. Comparar reservas, bloqueos, tentativas y actividad existente con la web de la misma empresa.
4. Para una prueba push futura y autorizada, instalar `pip install -r mobile/requirements.txt`, guardar claves APNs y Fernet y preparar el worker en el mismo disco persistente. Su primera lectura establece baseline sin enviar historial. Habilitar `FLOW_MOBILE_PUSH_ENABLED=1` únicamente en ese staging autorizado y supervisar `python tools/run_mobile_worker.py`.
5. Activar avisos desde el iPhone, enviar prueba propia, cerrar app y registrar evidencia física antes de declarar recepción funcional.

El proyecto original conserva APNs: Debug usa sandbox y entitlement development; Release usa production. La preview instalada usa `com.flowingcrm.mobile.preview`, Personal Team y una copia aislada sin APNs. No acredita recepción push.

## Reinstalar la preview si hace falta

La instalación y apertura de la versión anterior ya están confirmadas; la actualización de calendario y bandeja aún está pendiente. La build actual para iOS está firmada con Personal Team y bundle de preview, en la copia aislada. El perfil expira el **18 de octubre de 2026 a las 03:10:20 UTC** (17 de octubre, 21:10:20 en Guatemala); después hará falta firmarla e instalarla de nuevo.

1. Conecta el iPhone al Mac, desbloquéalo y acepta «Confiar» en el teléfono si aparece. Activa Developer Mode si Xcode lo requiere.
2. En Xcode, abre Settings → Accounts y añade tu Apple Account directamente. No compartas contraseña ni códigos por chat.
3. Para Personal Team, usa la copia aislada del proyecto y fuentes en `.local/iphone-personal-build`, con `Flowing.entitlements` vacío y bundle `com.flowingcrm.mobile.preview`. Selecciona el equipo en Signing & Capabilities y usa Automatically manage signing. Conserva el proyecto original con APNs.
4. Selecciona tu iPhone como destino del scheme Flowing y pulsa Run. Completa personalmente las confirmaciones de confianza que solicite iOS.
5. Abre la preview y sigue **Conectar al CRM real para una nueva sesión** si no conserva la conexión. El fixture QA en `127.0.0.1:5443` sirve al Simulator del Mac; `localhost` en el teléfono apunta al teléfono y no permite consultarlo.

Personal Team rechazó Push Notifications. El override `CODE_SIGN_ENTITLEMENTS=""` no bastó: Xcode seguía leyendo la capacidad del archivo. La instalación aprobada se obtuvo con la copia aislada sin APNs. La capacidad, claves APNs y recepción con app cerrada requieren configuración y prueba independientes. No se ha comprado membresía ni publicado la app.

Guías Apple: [ejecutar en dispositivo](https://help.apple.com/xcode/mac/current/en.lproj/dev5a825a1ca.html), [capacidades según membresía](https://developer.apple.com/help/account/reference/supported-capabilities-ios).

## Assets

El icono deriva del asset existente `static/icons/icon-512.png` (F azul sobre blanco) y se redimensionó para el catálogo 1024. No es una imagen nueva generada. Revisar nitidez contra el original vectorial antes de publicación. Referencia visual del usuario: interfaces claras, azul sobrio y listas con navegación inferior; sus cifras/personas son ejemplos y no se incorporan como datos.

`FlowingMark.imageset` reutiliza exactamente el PNG 512 existente, sin alterarlo, para la marca del inicio.

## QA HTTPS en Simulator

`python tools/run_mobile_qa.py` crea un directorio independiente bajo `.local/iphone-qa-*`, datos ficticios, certificado local y un archivo `control.json` privado. Sirve exclusivamente en `127.0.0.1:5443`; no añade rutas de login de prueba ni expone el CRM real. Correo y push quedan desactivados.

`python tools/check_mobile_qa.py <ruta/control.json> --approve-code <código>` autoriza solo el código de la app de prueba con la cuenta ficticia, sin imprimir secretos. El modo normal verifica TLS y recorre conexión por código, aprobación con sesión/CSRF, consultas, gráfica financiera, aislamiento, destino/lectura de avisos y logout. Nunca imprime cookie ni bearer. El certificado debe confiarse solo en un Simulator de prueba creado para este proyecto; no se instaló en el Mac ni en un iPhone real.

Xcode 27 instalado y licencia aceptada por el propietario. La build actual de Simulator usa firma local; la build actual iOS usa Personal Team en la preview aislada. Suite completa: 1572 aprobadas y 1 omitida; tras la guardia final del cursor, 36 pruebas específicas aprobadas. Contratos Swift/Foundation: 28 verificaciones. Checker HTTPS sintético aprobado. Alcance y logs en [Calendario y bandeja](../docs/mobile/07-calendario-y-bandeja.md); historial en `docs/mobile/05-compilacion-y-qa.md`.

La prueba nativa usa firma local de Simulator (`CODE_SIGN_IDENTITY=-`). No usar `CODE_SIGNING_ALLOWED=NO` para validar el llavero: abrir la app sin firma no acredita guardar la sesión. Las capturas de calendario actualizado son UI nativa ejecutada en claro, oscuro y texto grande; la captura con texto grande acredita solo la agenda visible. Ver [Capturas](../docs/mobile/screenshots/README.md).

## Teams en la app

Pestaña Teams: coberturas, equipo, horarios, respuestas, conflictos e importes de la misma empresa. Trabajos queda en Inicio y Más. La consulta usa el snapshot y cálculos de Teams existentes, con autorización nativa por bearer; asignaciones, invitaciones y pagos se gestionan en la ficha web. El servidor debe registrar Teams (entrada `wsgi.py`) y habilitarlo; no basta activar la API móvil en un proceso que no registra Teams.

El propietario confirmó que ve Teams y sus datos en la preview física y que conserva la cuenta al reabrir. La comparación de una ficha y un importe con la web, la observación independiente de las vistas y la revisión completa de VoiceOver siguen pendientes. Esta preview permite consultas; las acciones complejas continúan en web y push permanece apagado.
