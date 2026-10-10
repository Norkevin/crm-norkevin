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
