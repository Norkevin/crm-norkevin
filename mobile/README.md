# Flowing para iPhone

Complemento SwiftUI del CRM Flask existente. La preview sin push está firmada e instalada en el iPhone del propietario, quien confirmó que abre. El CRM real en `https://flowingcrm.com` tiene la API móvil activa y push apagado desde el despliegue del 10 de octubre de 2026. Falta aprobar el código con la cuenta Google correcta y comprobar una sesión física con datos reales. No está publicada en App Store.

- Proyecto: `ios/Flowing.xcodeproj`; scheme compartido `Flowing`; iPhone, iOS 17 o posterior.
- API: `/api/mobile/`, desactivada por defecto en código; activada expresamente en el CRM real. Datos existentes, sin seeds reales ni escrituras financieras nuevas.
- Notificaciones: journal persistente, APNs opcional, preferencias y privacidad. Worker manual, separado de correo/workflow.
- Diagnóstico, arquitectura y pruebas: `../docs/mobile/`.

## Conectar la preview instalada al CRM real

1. Abre Flowing en el iPhone. Usa `https://flowingcrm.com` y pulsa **Conectar mi cuenta** para generar el código.
2. Abre [Conectar iPhone](https://flowingcrm.com/mobile/connect) en el navegador e inicia sesión con la cuenta Google de la empresa correcta.
3. Comprueba el nombre de la empresa e introduce el código del iPhone. La aprobación usa la sesión del propietario y CSRF.
4. Vuelve a Flowing y compara una ficha y un importe conocidos con la web. Cierra y vuelve a abrir la app para comprobar que conserva la sesión.

Render confirmó `FLOW_MOBILE_ENABLED=1` y `FLOW_MOBILE_PUSH_ENABLED=0`; el despliegue está Live. Los probes independientes dieron `/login` 200 y `/api/mobile/overview` 401 «Conecta tu cuenta de Flowing.». Esto acredita disponibilidad y rechazo sin bearer, no una sesión física conectada. No se inició el worker móvil: la bandeja requiere reconciliación posterior. Detalles y evidencia: [Conexión al CRM real](../docs/mobile/06-conexion-crm-real.md).

## Comprobar localmente

```sh
.venv/bin/python -m pytest -q tests/test_mobile_native.py
swift run --package-path mobile/ios FlowingContractChecks
```

## Probar en staging

1. Revisar `docs/mobile/02-hitos-y-pruebas.md`, usar datos sintéticos y HTTPS. Configurar las variables de `mobile/.env.example` para ese entorno; activar `FLOW_MOBILE_ENABLED=1` y mantener `FLOW_MOBILE_PUSH_ENABLED=0` inicialmente.
2. Abrir el proyecto en Xcode completo, configurar equipo/firma y confirmar Bundle ID. La app solicita la URL HTTPS del CRM; la conexión usa un código autorizado en `/mobile/connect` con login Google vigente.
3. Inicializar la reconciliación en el mismo disco persistente: `python tools/run_mobile_worker.py --once`. La primera lectura es baseline y no envía historial.
4. Para push, instalar `pip install -r mobile/requirements.txt`, guardar claves APNs y Fernet en servidor y habilitar `FLOW_MOBILE_PUSH_ENABLED=1` solo en staging autorizado. Supervisar `python tools/run_mobile_worker.py`.
5. Activar avisos desde el iPhone, enviar prueba propia, cerrar app y registrar evidencia física antes de declarar recepción funcional.

El proyecto original conserva APNs: Debug usa sandbox y entitlement development; Release usa production. La preview instalada usa `com.flowingcrm.mobile.preview`, Personal Team y una copia aislada sin APNs. No acredita recepción push.

## Reinstalar la preview si hace falta

La instalación y apertura ya están confirmadas. El perfil de la preview expira el **18 de octubre de 2026 a las 03:10:20 UTC** (17 de octubre, 21:10:20 en Guatemala); después hará falta firmarla e instalarla de nuevo.

1. Conecta el iPhone al Mac, desbloquéalo y acepta «Confiar» en el teléfono si aparece. Activa Developer Mode si Xcode lo requiere.
2. En Xcode, abre Settings → Accounts y añade tu Apple Account directamente. No compartas contraseña ni códigos por chat.
3. Para Personal Team, usa la copia aislada del proyecto y fuentes en `.local/iphone-personal-build`, con `Flowing.entitlements` vacío y bundle `com.flowingcrm.mobile.preview`. Selecciona el equipo en Signing & Capabilities y usa Automatically manage signing. Conserva el proyecto original con APNs.
4. Selecciona tu iPhone como destino del scheme Flowing y pulsa Run. Completa personalmente las confirmaciones de confianza que solicite iOS.
5. Abre la preview y sigue **Conectar la preview instalada al CRM real**. El fixture QA en `127.0.0.1:5443` sirve al Simulator del Mac; `localhost` en el teléfono apunta al teléfono y no permite consultarlo.

Personal Team rechazó Push Notifications. El override `CODE_SIGN_ENTITLEMENTS=""` no bastó: Xcode seguía leyendo la capacidad del archivo. La instalación aprobada se obtuvo con la copia aislada sin APNs. La capacidad, claves APNs y recepción con app cerrada requieren configuración y prueba independientes. No se ha comprado membresía ni publicado la app.

Guías Apple: [ejecutar en dispositivo](https://help.apple.com/xcode/mac/current/en.lproj/dev5a825a1ca.html), [capacidades según membresía](https://developer.apple.com/help/account/reference/supported-capabilities-ios).

## Assets

El icono deriva del asset existente `static/icons/icon-512.png` (F azul sobre blanco) y se redimensionó para el catálogo 1024. No es una imagen nueva generada. Revisar nitidez contra el original vectorial antes de publicación. Referencia visual del usuario: interfaces claras, azul sobrio y listas con navegación inferior; sus cifras/personas son ejemplos y no se incorporan como datos.

`FlowingMark.imageset` reutiliza exactamente el PNG 512 existente, sin alterarlo, para la marca del inicio.

## QA HTTPS en Simulator

`python tools/run_mobile_qa.py` crea un directorio independiente bajo `.local/iphone-qa-*`, datos ficticios, certificado local y un archivo `control.json` privado. Sirve exclusivamente en `127.0.0.1:5443`; no añade rutas de login de prueba ni expone el CRM real. Correo y push quedan desactivados.

`python tools/check_mobile_qa.py <ruta/control.json> --approve-code <código>` autoriza solo el código de la app de prueba con la cuenta ficticia, sin imprimir secretos. El modo normal verifica TLS y recorre conexión por código, aprobación con sesión/CSRF, consultas, gráfica financiera, aislamiento, destino/lectura de avisos y logout. Nunca imprime cookie ni bearer. El certificado debe confiarse solo en un Simulator de prueba creado para este proyecto; no se instaló en el Mac ni en un iPhone real.

Xcode 27 instalado y licencia aceptada por el propietario. Builds aprobadas; estado de pruebas: ver `docs/mobile/05-compilacion-y-qa.md`.

La prueba nativa usa firma local de Simulator (`CODE_SIGN_IDENTITY=-`). No usar `CODE_SIGNING_ALLOWED=NO` para validar el llavero: abrir la app sin firma no acredita guardar la sesión. Las capturas reales y los límites de la revisión están en `docs/mobile/05-compilacion-y-qa.md`.

## Teams en la app

Pestaña Teams: coberturas, equipo, horarios, respuestas, conflictos e importes de la misma empresa. Trabajos queda en Inicio y Más. La consulta usa el snapshot y cálculos de Teams existentes, con autorización nativa por bearer; asignaciones, invitaciones y pagos se gestionan en la ficha web. El servidor debe registrar Teams (entrada `wsgi.py`) y habilitarlo; no basta activar la API móvil en un proceso que no registra Teams.

La preview física ya abre y el servidor real está disponible. Sigue pendiente comprobar Teams desde una sesión física autorizada de la empresa correcta; las pruebas sintéticas de Simulator no sustituyen esa comprobación.
