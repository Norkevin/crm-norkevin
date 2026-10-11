# Hitos y pruebas — primer incremento

Fecha inicial: 10 de octubre de 2026; actualización Teams: 11 de octubre de 2026.

## Estado

| Hito | Estado y salida verificable |
| --- | --- |
| Auditoría y arquitectura | Código local inspeccionado; ADR y mapa en `00-auditoria.md` y `01-arquitectura.md`. Hosting efectivo pendiente. |
| API y journal | Implementados con flags, conexión por código, bearer, tenant, bandeja paginada, lectura, preferencias, dispositivo, privacidad y prueba propia. Pruebas de aislamiento ejecutadas. |
| Primer cliente iPhone | Proyecto Xcode y fuentes SwiftUI creados; contratos Foundation compilados/ejecutados. Build Simulator con firma local y destino iOS físico sin firma aprobadas; sesión HTTPS nativa, capturas claro/oscuro/texto grande y correcciones visuales revisadas en iPhone 16. Falta prueba física. |
| Teams nativo | Pestaña principal, Coberturas/Equipo y detalle de lectura implementados; allowlist, empresa bearer, flags y guardas locales. QA HTTPS sintético aprobado; contraste Cobertura/Miembro corregido (21:1 claro, 17.01:1 oscuro), dictamen favorable acotado a capturas revisadas, sin sección inferior ni toda la app. |
| APNs | Adaptador y cola implementados, pruebas simuladas de aceptación, reintentos, caducidad/revocación. No hay envío real, firma Apple ni prueba física. |
| Producto completo | Pendientes login integrado, edición nativa, trabajo/cliente completo, adjuntos, nuevas asignaciones al trabajador, respuestas entrantes, quiet hours, paridad y Android. |

## Validación ejecutada

- `.venv/bin/python -m pytest -q`: 1550 passed, 1 skipped; 25.31 s. Suite usa datos temporales y proveedores simulados.
- API móvil: 25 casos aprobados; cubren aislamiento, privacidad, revisión de dispositivo, cobros y proyección Teams. La repetición focalizada después de las guardas locales también aprobó 25 casos.
- Prueba focalizada inicial de móvil/avisos/navegación: 26 passed.
- `swift run --package-path mobile/ios FlowingContractChecks`: 14 verificaciones Foundation aprobadas; incluye URL HTTPS, separación de origen, segmentos, fecha y JSON paginado.
- `swiftc -frontend -parse ...`: sintaxis de las cuatro fuentes Swift aceptada. Esto no valida APIs SwiftUI/UIKit ni linkage.
- `plutil -lint`: proyecto Xcode, Info.plist y entitlements válidos como plist. No acredita firma ni build.
- Intento inicial `swift test`: bloqueado por ausencia de XCTest en CommandLineTools. Se reemplazó por comprobaciones ejecutables Foundation sin dependencia de XCTest.

Suite actual: `.context-shears/artifacts/command-20261011T022731.337498Z-1e8e5a00d9c0.log`. Repetición focalizada: `.context-shears/artifacts/command-20261011T022931.142707Z-7cfe103df9a3.log`. QA HTTPS del fixture `.local/iphone-qa-58y53m7e` aprobado con dos miembros, Q3,000 de coste y Q2,500 pendientes; conexión autorizada por sesión/CSRF real y datos ficticios.

Registros completos: `.context-shears/artifacts/` (local, ignorado por Git). Nunca se imprimieron claves APNs ni bearer reales. No se llamó a Apple para enviar avisos.

## Siguiente hito exacto

1. QA de las capturas Teams revisadas cerrada con alcance acotado; queda ampliar cobertura visual a la sección inferior y demás superficies antes de certificar la app completa. Firma e iPhone físico pospuestos por el propietario («Lo haré después»). Simulator comprobado después del reinicio: sesión y Keychain funcionales, capturas reales y dos correcciones visuales aprobadas a su alcance. Cuando el propietario retome la prueba física, conectar y desbloquear iPhone y configurar Apple Account/equipo de firma en Xcode. Evidencia en `05-compilacion-y-qa.md`.
2. Preparar staging con copia sintética, HTTPS, mismo disco del worker y las variables de `mobile/.env.example`. API activada, push desactivado. Conectar propietario y comprobar datos nativos sin operaciones financieras.
3. Confirmar Bundle ID, equipo Apple y capacidad Push Notifications. Instalar en iPhone físico. Guardar clave APNs/Fernet como secretos del servidor; instalar requisitos opcionales del transporte. No hacer compras/publicaciones automáticamente.
4. Inicializar baseline del worker, producir un lead sintético y activar envíos solo para ese entorno. Cerrar app, recibir aviso, tocar y comprobar ficha y empresa; registrar SO/modelo/hora/capturas. Repetir logout, cambio de cuenta, correcciones/cancelaciones y conexión caída.
5. Completar operaciones nativas prioritarias y QA; luego TestFlight privado. Evaluar App Store con privacidad, cuenta de revisión, soporte, capturas y firma. Publicar exige aprobación explícita.

## Matriz de aceptación física pendiente

| Escenario | Resultado que debe documentarse |
| --- | --- |
| App cerrada | Lead nuevo y prueba propia visibles; no basta respuesta 200 de APNs. |
| Toque | Abre ficha autorizada correcta o conexión/login; nunca otra marca. |
| Privacidad | Detalles ocultos por defecto y cambios respetados en avisos posteriores. |
| Dos empresas | Identidades, pagos, bandeja, preferencias y badges separados. |
| Logout/shared device | Nueva cola no entrega a sesión revocada; cambio de cuenta elimina registro anterior del token. |
| Permiso denegado | Bandeja sigue funcionando; UI indica cómo activar en Ajustes. |
| Reintentos/cambio de fecha | Sin registros duplicados en journal; recordatorios obsoletos aún en cola cancelados. |
| UI | Safe area, VoiceOver, Dynamic Type grande, claro/oscuro, back gesture y 44 pt. |

## Rollback

`FLOW_MOBILE_PUSH_ENABLED=0` detiene entrega; detener el worker detiene reconciliación; `FLOW_MOBILE_ENABLED=0` deja API no disponible. Se conservan datos del journal para recuperación. El CRM web no depende de la base móvil. No borrar el journal para desactivar ni girar Fernet sin plan de migración, porque dejaría tokens push ilegibles.

Cambios de esquema móviles: solo tablas nuevas en base nueva. Para revertir código, retirar registro móvil y ganchos de bearer después de desactivar flags/worker; preservar `mobile.sqlite3` y sus backups. No se requieren cambios en producción para revisar este incremento local.
