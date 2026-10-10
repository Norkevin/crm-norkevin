# Flowing para iPhone

Complemento SwiftUI del CRM Flask existente. Primer incremento local; no es una app publicada ni una build iOS validada todavía.

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

## Assets

El icono deriva del asset existente `static/icons/icon-512.png` (F azul sobre blanco) y se redimensionó para el catálogo 1024. No es una imagen nueva generada. Revisar nitidez contra el original vectorial antes de publicación. Referencia visual del usuario: interfaces claras, azul sobrio y listas con navegación inferior; sus cifras/personas son ejemplos y no se incorporan como datos.

`FlowingMark.imageset` reutiliza exactamente el PNG 512 existente, sin alterarlo, para la marca del inicio.

## QA HTTPS en Simulator

`python tools/run_mobile_qa.py` crea un directorio independiente bajo `.local/iphone-qa-*`, datos ficticios, certificado local y un archivo `control.json` privado. Sirve exclusivamente en `127.0.0.1:5443`; no añade rutas de login de prueba ni expone el CRM real. Correo y push quedan desactivados.

`python tools/check_mobile_qa.py <ruta/control.json>` verifica TLS y recorre conexión por código, aprobación con sesión/CSRF, consultas, gráfica financiera, aislamiento, destino/lectura de avisos y logout. Nunca imprime cookie ni bearer. El certificado debe confiarse solo en un Simulator de prueba creado para este proyecto; no se instaló en el Mac ni en un iPhone real.

Xcode 27 ya instalado. Primera apertura/licencia pendiente: ver `docs/mobile/05-compilacion-y-qa.md`.
