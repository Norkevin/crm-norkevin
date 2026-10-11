# Primera entrega de Flowing para iPhone

Fecha: 10 de octubre de 2026. Estado: base local implementada y comprobada en servidor; compilación iOS y prueba física pendientes.

Reporte histórico de la primera entrega, anterior a instalar Xcode; los pendientes descritos corresponden a ese momento. El avance posterior, la sesión nativa conectada en Simulator, las capturas reales, los ajustes visuales verificados con alcance acotado y los pendientes de firma/prueba física se registran en [Compilación y QA](05-compilacion-y-qa.md).

## 1. Diagnóstico real

Flowing usa Flask/Jinja y JSON operativo, con Teams en SQLite. Ya existe una PWA y bandeja web; no había entrega push persistente al iPhone. No hay frontend React compilable para empaquetar directamente. [Auditoría](00-auditoria.md).

## 2. Implementación y motivo

Complemento nativo SwiftUI con Inicio, Leads, Trabajos, Calendario y Más; Clientes, Pagos, Notificaciones y Preferencias. Consulta datos reales de su cuenta mediante API nueva, conservando reglas financieras del CRM. Conexión por código aprobado tras login Google, bearer en Keychain y autorización por empresa.

Journal SQLite con baseline, eventos idempotentes, bandeja paginada, preferencias, token APNs cifrado y cola con reintentos/leases. Adaptador APNs HTTP/2 y prueba propia limitada. Por defecto API y envíos están apagados. No hay worker nuevo automático en Flask ni nuevos envíos de correo.

La imagen del usuario guía blanco/azul/listas; no se incorporaron cifras ni identidades de muestra. No hay un mockup que se presente como pantalla ejecutada. [Decisión de arquitectura](01-arquitectura.md).

## 3. Archivos y migración

- Integración mínima: `app.py`, `.gitignore`.
- Servidor: `src/mobile.py`, `src/mobile_push.py`, `tools/run_mobile_worker.py`, `templates/mobile_connect.html`, `tests/test_mobile_native.py`.
- Cliente y configuración: `mobile/ios/Flowing.xcodeproj/`, `mobile/ios/Flowing/`, `mobile/ios/Package.swift`, `mobile/ios/Tests/`, `mobile/requirements.txt`, `mobile/.env.example`, `mobile/README.md`.
- Documentación: `docs/mobile/00-auditoria.md` a `04-entrega.md`.

Migración aditiva/idempotente en una base separada `CRM_DATA_DIR/mobile.sqlite3`; incluye revisión de dispositivo para impedir que una petición pendiente deshaga la privacidad o la desactivación. No se cambiaron datos/esquemas del CRM existente. Rollback por flags y detención de worker, preservando journal.

## 4. Pruebas reales

- Suite final CRM: **1547 passed, 1 skipped**, 20.96 segundos. Incluye 23 casos nuevos de API, aislamiento, código/CSRF, idempotencia, paginación, privacidad/revocación, firma P-256 y proveedores simulados.
- Swift Foundation: **14 comprobaciones aprobadas**, con compilación y ejecución en este Mac. No son pruebas de UI iOS.
- Sintaxis de cuatro fuentes Swift, plist de proyecto/Info/entitlements y `git diff --check` comprobados.
- Revisión estática independiente: se corrigieron respuestas de sesión anterior, enlace de calendario y carrera de renovación/desactivación APNs. Dictamen limitado: hallazgos resueltos; captura/validación iOS pendiente.
- Revisión documental independiente: alcance, diseño heredado, procedencia del icono y límites documentados; se conserva `DESIGN.md`.

Logs completos locales: `.context-shears/artifacts/command-20261010T103554.578570Z-9ed6d871662a.log` (suite) y `command-20261010T103638.683220Z-a0f489c8e477.log` (Swift). No se usaron clientes reales ni proveedores de correo/push reales.

## 5. Lo que falta comprobar

Este Mac carece del SDK iPhone: `xcrun --sdk iphoneos --show-sdk-path` falla y no se encontró Xcode mediante su identificador de aplicación. Falta instalar Xcode completo, compilar la app, corregir eventuales errores del SDK, ejecutar Simulator y probar en iPhone físico con app cerrada. No se ha verificado APNs ni login Safari desde la app instalada.

No hay paridad completa todavía: edición, archivos, contratos y operaciones complejas siguen en web. Respuestas entrantes de correo, nuevos avisos a trabajadores, quiet hours, Android, métricas operativas/retención y experiencia completa de dispositivos requieren siguientes incrementos. La bandeja móvil conserva lectura separada de la web en esta etapa.

## 6. Riesgos y límites

JSON operativo y journal no comparten transacción; el scanner lee snapshots cada 30 segundos y puede omitir cambios transitorios entre scans. Imports no identificados podrían parecer eventos nuevos. Primer scan es baseline sin avisos, incluidos recordatorios que ya existían; inicializar antes de pruebas.

La cola reintenta fallos transitorios, con límite de cinco; timeouts ambiguos no permiten garantizar exactamente una entrega visible. Un aviso ya aceptado por APNs o mostrado por iOS no se retira por revocar sesión. Antes de producción, verificar topología del worker/disco, proxy, supervisión, retención y rollback. Privacidad oculta por defecto; aprobación de Apple y auditoría física pendientes.

## 7. Costos y cuentas

No se compró ni publicó nada. Apple Developer Program figura a USD 99 por año según Apple, con variación regional. Es el siguiente costo a evaluar para firma/distribución/push, sin autorización de compra implícita. [Apple](https://developer.apple.com/programs/enroll/).

No se añadieron servicios comerciales de notificaciones. Worker/almacenamiento podrían requerir capacidad adicional de hosting: costo no estimable sin verificar configuración real. Claves APNs/Fernet y Bundle ID deben configurarse por el propietario en staging seguro.

## 8. Siguiente hito exacto

Instalar Xcode completo → compilar y revisar pantallas en Simulator → conectar staging con datos sintéticos → firma/APNs autorizados → instalar y recibir prueba real con app cerrada en iPhone. Después, completar operaciones nativas y preparar TestFlight. App Store solo tras validación, privacidad (incluido manifest de APIs de razón requerida cuando corresponda), cuenta de revisión y aprobación expresa de publicación.

[Pasos y aceptación](02-hitos-y-pruebas.md). [Proyecto y ejecución](../../mobile/README.md).
