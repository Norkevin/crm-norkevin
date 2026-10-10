# Instalación, dashboard y comprobaciones HTTPS

Fecha: 10 de octubre de 2026. Estado: licencia completada; builds iOS aprobadas; revisión visual en curso.

## Avance comprobado

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

## Continuación y siguiente acción

Seguimiento asociado a este chat: «Continuar Flowing para iPhone», cada 30 minutos. Comprueba cambios del entorno sin repetir el bloqueo ni descargar otra vez los mismos componentes. Último arranque del Mac registrado: `kern.boottime.sec = 1790879994`; un arranque nuevo o intervención del propietario permite reintentar las operaciones con timeout.

El próximo hito es sesión conectada y capturas claro/oscuro/texto grande en el Simulator verificado. Después instalar en un iPhone físico con firma y servidor HTTPS apropiado. Para el propietario: guardar trabajo y reiniciar Mac; después conectar iPhone y añadir Apple Account en Xcode. La guía está en `mobile/README.md`.

No declarar «versión final para iPhone» ni «push probado» antes de esas evidencias. No se activó la API ni push en producción.
