# Instalación, dashboard y comprobaciones HTTPS

Fecha: 10 de octubre de 2026. Estado: sesión nativa y capturas reales verificadas en Simulator; correcciones visuales aprobadas a su alcance.

## Historial previo al reinicio

Xcode 27.0, build 27A266a, instalado en `/Applications/Xcode.app` desde App Store. `xcodebuild -version` lo identifica. No se compró una membresía Apple ni se cambió producción.

El propietario completó la licencia y primera apertura; `xcodebuild -checkFirstLaunchStatus` devuelve exit 0. El bloqueo exit 69 del primer intento es histórico.

Dos errores de inicializadores `Section` con footer se corrigieron usando header explícito. Builds reales Debug para Simulator y destino iOS físico devolvieron **exit 0** con `CODE_SIGNING_ALLOWED=NO`. La build física aún necesita firma para instalarse.

El primer runtime iOS 27 permitió instalar y observar ConnectView nativa en Device Hub, pero el puente `simctl` quedó esperando al instalar la CA y capturar. Dos descargas oficiales iOS 27 y una iOS 26 presentaron el error de verificación **-67054, sealed resource is missing or invalid** en la entrega Patchable Cryptex. Esto no demuestra corrupción del contenido descargado: la importación de la DMG oficial por `simctl runtime add` resolvió la verificación. La imagen iOS 26.0 (23A343) ahora figura **Verified / Ready**, UUID `76E6DC20-9781-4780-A4FD-745598105B0E`. Se conservó el asset y retiró solo el registro Cryptex inutilizable del runtime de prueba.

Referencia diagnóstica: [caso de entrega Cryptex en Apple Developer Forums](https://developer.apple.com/forums/thread/830152). La importación oficial verifica la imagen; no se desactivó firma, TLS ni SIP.

El problema del puente continúa con el runtime verificado. `simctl keychain` quedó esperando; `devicectl device install app` y `device capture screenshot`, cada uno con timeout de 30 segundos, devolvieron **exit 2 / Command timeout exceeded**. Se seleccionó Xcode como toolchain activa y reiniciaron servicios CoreSimulator con autorización del propietario, sin resolver estas llamadas. El siguiente paso diagnóstico es reiniciar el Mac cuando el propietario pueda guardar su trabajo; no se reinició automáticamente. No repetir descargas ni reparar firmando recursos Apple manualmente.

Simulator propio: `Flowing QA iOS26`, iPhone 16, UDID `BC5B50F1-67F2-4A21-BB5E-3B2EB1FA385F`. La identidad anterior `Flowing QA` de iOS 27 no tiene runtime instalado. El Simulator de prueba se apagó al terminar. Estado de la imagen guardado en `.local/runtime-state.json`.

`devicectl list devices`: solo el iPhone 16 **simulated** propio; ningún iPhone físico. `security find-identity -v -p codesigning`: 0 valid identities found. No hay iPhone conectado ni identidad de firma disponible para completar la instalación física sin intervención del propietario.

## Pantallas preparadas

Inicio con marca existente, empresa, fecha de Guatemala, cuatro métricas, gráfica de cobros por mes y próximos trabajos. Cuadrícula adaptable a una columna en Dynamic Type accesible. Datos salen de las reglas financieras del CRM; no porcentajes, importes ni tendencias inventadas. La lista de próximas bodas excluye fechas pasadas y mantiene empresa activa.

Más identifica empresa y correo del propietario. Se conserva la navegación nativa y el diseño web global. El logo `FlowingMark` es una copia idéntica del icono ya existente, no una ilustración nueva.

La pantalla inicial de conexión fue observada en la app instalada. El dashboard conectado y las capturas claro/oscuro/texto grande siguen pendientes; aún no se certifica la revisión visual.

## Pruebas ejecutadas

| Comprobación | Resultado |
| --- | --- |
| Suite CRM final | 1548 passed, 1 skipped; 23.33 s. |
| API móvil específica | 24 casos; incluye gráfica con fechas de cobro y trabajos próximos. |
| Contratos Swift/Foundation | 14 verificaciones compiladas y ejecutadas con las herramientas existentes. |
| HTTPS local verificado | Conexión por código, aprobación sesión/CSRF, consultas, cobros, aislamiento, avisos, lectura y logout aprobados. |
| Compilación iOS Simulator | Exit 0; iOS SDK 27.0, arm64 y x86_64. |
| Compilación iOS físico | Exit 0, arm64; sin firma. |
| Capturas claro/oscuro/texto grande | No ejecutadas todavía. |
| iPhone físico / push cerrado | No ejecutados. |

Logs locales recuperables:

- Build Simulator: `.context-shears/artifacts/command-20261010T170649.952965Z-e18e026d5f36.log`.
- Build iOS físico sin firma: `.context-shears/artifacts/command-20261010T170726.675091Z-5b0d2905a344.log`.
- API móvil: `.context-shears/artifacts/command-20261010T170752.488759Z-93d7c388ed39.log`.
- Importación de runtime verificado: `.context-shears/artifacts/command-20261010T172555.190202Z-2420b08af2b8.log`.
- Suite: `.context-shears/artifacts/command-20261010T110012.222378Z-1b7905ecce3d.log`.
- Swift: `.context-shears/artifacts/command-20261010T110012.222546Z-3cda38548b93.log`.

## Entorno de prueba

`tools/run_mobile_qa.py`: fixture HTTPS exclusiva de loopback con almacenamiento sintético, certificado de duración corta y archivo de control 0600. Envía cero correos y cero push. No importa datos reales ni añade un bypass HTTP de login. `tools/check_mobile_qa.py --approve-code <código>` permite autorizar el código mostrado por la app usando exclusivamente el propietario ficticio del fixture; su ruta sesión/CSRF y poll conectado se comprobaron sin imprimir secretos. El modo normal verifica certificado/origen y usa las rutas reales de autorización con una cuenta ficticia.

El servidor local iniciado en esta ejecución usa `.local/iphone-qa-4f6y_z2u/`. Las credenciales de control no deben publicarse ni copiarse al repositorio. Un futuro proceso de QA puede generar un fixture nuevo; comprobar que el puerto 5443 corresponda al runner propio antes de detenerlo.

## Seguimiento previo al reinicio

Seguimiento asociado a este chat: «Continuar Flowing para iPhone», cada 30 minutos. Comprueba cambios del entorno sin repetir el bloqueo ni descargar otra vez los mismos componentes. Último arranque del Mac registrado: `kern.boottime.sec = 1790879994`; un arranque nuevo o intervención del propietario permite reintentar las operaciones con timeout.

El próximo hito es sesión conectada y capturas claro/oscuro/texto grande en el Simulator verificado. Después instalar en un iPhone físico con firma y servidor HTTPS apropiado. Para el propietario: guardar trabajo y reiniciar Mac; después conectar iPhone y añadir Apple Account en Xcode. La guía está en `mobile/README.md`.

No declarar «versión final para iPhone» ni «push probado» antes de esas evidencias. No se activó la API ni push en producción.

## Pruebas después del reinicio — 10 de octubre, 20:18 Guatemala

Arranque nuevo confirmado: `kern.boottime.sec = 1791684578`. El registro anterior del runtime pasó a «Cryptex Mount Preferred»; scan-and-mount recuperó un registro Cryptex, pero no un runtime utilizable. Se retiraron únicamente registros de la prueba conservando el asset, y se reimportó la misma DMG oficial sin descargar componentes. Runtime actual iOS 26.0: `3165D51A-D387-46BB-861B-3B9A126068F9`, Ready. Importación exit 0, log `command-20261011T021155.613697Z-f0257c280cb4.log`.

El Simulator propio vuelve a arrancar. `devicectl install` agotó 30 segundos inicialmente; posteriormente `simctl install`, `launch`, `keychain add-root-cert` y `io screenshot` finalizaron. La CA se confió exclusivamente en ese Simulator. Sin excepciones TLS en la app.

La build sin firma abrió la app, pero SecItemAdd no guardó la sesión. Una build normal del Simulator con `CODE_SIGN_IDENTITY=-` y firma local ad hoc solucionó ese fallo; no se sustituyó Keychain por almacenamiento inseguro. La build final tras las correcciones de UI devolvió exit 0 en 6.27 s, log `command-20261011T021751.075904Z-26a275a03909.log`. La sesión se conservó después de terminar, reinstalar y abrir la app.

Fixture nuevo: `.local/iphone-qa-9fqio9g3/`, HTTPS loopback con datos ficticios y comunicaciones desactivadas. Checker completo aprobado. Conexión nativa aprobada mediante sesión/CSRF real del propietario ficticio. Inicio mostró 3 leads, 4 trabajos, Q8,500 cobrado y Q14,500 pendiente. Se recorrieron leads, detalle de trabajo, calendario, pagos y avisos. El aviso de Sofía abrió su ficha y el contador bajó de 6 a 5 sin leer.

Capturas reales en `screenshots/`: inicio claro/oscuro y texto accesible máximo; leads claro/texto grande/oscuro con texto grande; trabajos, detalle, calendario, pagos y avisos. No son mockups. Un primer inicio-claro capturó otra pestaña y fue reemplazado tras verificar Inicio; no usar la captura descartada como evidencia.

La revisión independiente pidió dos correcciones: contraste de timestamps en avisos (Color.label explícito, sin heredar tint del Button) y fechas largas es_GT en pagos, conservando fallback para fechas inválidas. Se recompiló y recapturó pagos claro y avisos claro/oscuro. Quedan fuera de esta evidencia el recorrido VoiceOver completo, hardware, push físico y operaciones de edición que todavía abren la web.

Continúa faltando un iPhone físico conectado y una identidad Apple de firma. `devicectl list devices` muestra solo el Simulator; `security find-identity` devuelve 0 identidades. El siguiente paso del propietario es conectar y desbloquear su iPhone y configurar Apple Account/equipo en Xcode según `mobile/README.md`. No repetir el paso de reiniciar Mac ya completado.

### Dictamen final y límites

La revisión independiente devolvió `ship` para las dos correcciones, con fechas de pagos resueltas y contraste de avisos 21:1 en claro / 17.01:1 en oscuro. Dictamen acotado: no certifica toda la app. Se revisaron las capturas iPhone 16/iOS 26 indicadas; no se observó completo el dashboard inferior, ni se validó VoiceOver integral. Las operaciones de edición/documentos continúan en web durante esta etapa. El contraste final usa Color.label porque secondaryLabel dio 3.44:1 en claro.

Build final Simulator: exit 0, 3.63 s, log `command-20261011T021922.208063Z-b3a8e8895173.log`. El seguimiento automático del bloqueo de Simulator se detiene tras esta revisión. La continuación física necesita iPhone, equipo de firma y servidor HTTPS accesible desde el teléfono.

Build física final sin firma tras los cambios visuales: exit 0, 4.44 s; log `command-20261011T022001.485688Z-46e3e968bb14.log`. No instala sin firma.
