# Flowing para iPhone

Complemento SwiftUI del CRM Flask existente. Primer incremento local; compila para Simulator y destino iOS físico sin firma. Aún no está publicada ni instalada en un iPhone físico.

- Proyecto: `ios/Flowing.xcodeproj`; scheme compartido `Flowing`; iPhone, iOS 17 o posterior.
- API: `/api/mobile/`, desactivada por defecto. Datos existentes, sin seeds reales ni escrituras financieras nuevas.
- Notificaciones: journal persistente, APNs opcional, preferencias y privacidad. Worker manual, separado de correo/workflow.
- Diagnóstico, arquitectura y pruebas: `../docs/mobile/`.

## Comprobar localmente

```sh
.venv/bin/python -m pytest -q tests/test_mobile_native.py
swift run --package-path mobile/ios FlowingContractChecks
```

## Probar en staging

1. Revisar `docs/mobile/02-hitos-y-pruebas.md`, usar datos sintéticos y HTTPS. Cargar `.env.example` como configuración segura del servidor; activar solo `FLOW_MOBILE_ENABLED=1` inicialmente.
2. Abrir el proyecto en Xcode completo, configurar equipo/firma y confirmar Bundle ID. La app solicita la URL HTTPS del CRM; la conexión usa un código autorizado en `/mobile/connect` con login Google vigente.
3. Inicializar la reconciliación en el mismo disco persistente: `python tools/run_mobile_worker.py --once`. La primera lectura es baseline y no envía historial.
4. Para push, instalar `pip install -r mobile/requirements.txt`, guardar claves APNs y Fernet en servidor y habilitar `FLOW_MOBILE_PUSH_ENABLED=1` solo en staging autorizado. Supervisar `python tools/run_mobile_worker.py`.
5. Activar avisos desde el iPhone, enviar prueba propia, cerrar app y registrar evidencia física antes de declarar recepción funcional.

Debug usa APNs sandbox y entitlement development; Release usa production. Identificador `com.flowingcrm.mobile` es provisional y no representa una inscripción Apple realizada.

## Instalar en tu iPhone

1. Conecta el iPhone al Mac, desbloquéalo y acepta «Confiar» en el teléfono si aparece. Activa Developer Mode si Xcode lo requiere.
2. En Xcode, abre Settings → Accounts y añade tu Apple Account directamente. No compartas contraseña ni códigos por chat.
3. Abre `Flowing.xcodeproj`, selecciona el target Flowing → Signing & Capabilities, marca Automatically manage signing y elige tu equipo. Confirma un Bundle ID disponible para ese equipo.
4. Selecciona tu iPhone como destino del scheme Flowing y pulsa Run. La build sin firma ya aprobada no puede instalarse por sí sola.
5. Conecta la app a un servidor HTTPS de prueba con la API móvil activada y autoriza su código desde la web. El servidor QA de loopback sirve al Simulator del Mac; `localhost` en el teléfono apunta al propio teléfono y no permite consultar ese fixture.

Si el equipo de Apple no admite Push Notifications, primero se puede compilar una prueba de consulta con `CODE_SIGN_ENTITLEMENTS=""`; esa prueba no acredita push. La capacidad, claves APNs y recepción con app cerrada requieren configuración y prueba independientes. No se ha comprado membresía ni publicado la app.

Guías Apple: [ejecutar en dispositivo](https://help.apple.com/xcode/mac/current/en.lproj/dev5a825a1ca.html), [capacidades según membresía](https://developer.apple.com/help/account/reference/supported-capabilities-ios).

## Assets

El icono deriva del asset existente `static/icons/icon-512.png` (F azul sobre blanco) y se redimensionó para el catálogo 1024. No es una imagen nueva generada. Revisar nitidez contra el original vectorial antes de publicación. Referencia visual del usuario: interfaces claras, azul sobrio y listas con navegación inferior; sus cifras/personas son ejemplos y no se incorporan como datos.

`FlowingMark.imageset` reutiliza exactamente el PNG 512 existente, sin alterarlo, para la marca del inicio.

## QA HTTPS en Simulator

`python tools/run_mobile_qa.py` crea un directorio independiente bajo `.local/iphone-qa-*`, datos ficticios, certificado local y un archivo `control.json` privado. Sirve exclusivamente en `127.0.0.1:5443`; no añade rutas de login de prueba ni expone el CRM real. Correo y push quedan desactivados.

`python tools/check_mobile_qa.py <ruta/control.json> --approve-code <código>` autoriza solo el código de la app de prueba con la cuenta ficticia, sin imprimir secretos. El modo normal verifica TLS y recorre conexión por código, aprobación con sesión/CSRF, consultas, gráfica financiera, aislamiento, destino/lectura de avisos y logout. Nunca imprime cookie ni bearer. El certificado debe confiarse solo en un Simulator de prueba creado para este proyecto; no se instaló en el Mac ni en un iPhone real.

Xcode 27 instalado y licencia aceptada por el propietario. Builds aprobadas; estado de pruebas: ver `docs/mobile/05-compilacion-y-qa.md`.
