# Instalación, dashboard y comprobaciones HTTPS

Fecha: 10 de octubre de 2026. Estado: Xcode instalado; autorización de primera apertura pendiente.

## Avance comprobado

Xcode 27.0, build 27A266a, instalado en `/Applications/Xcode.app` desde App Store. `xcodebuild -version` lo identifica. No se compró una membresía Apple ni se cambió producción.

El intento real de build para iOS Simulator devolvió **exit 69**, con el mensaje de Apple que exige aceptar la licencia Xcode and Apple SDKs. La ventana de licencia quedó abierta en Xcode. No se aceptó el contrato ni se introdujeron contraseñas en nombre del propietario.

La solicitud de instalar y continuar no sustituye la confirmación de un acuerdo legal. La primera apertura puede pedir la contraseña del Mac y descarga de componentes iOS. No hace falta enviar contraseñas por chat. Una vez completada, continuar con compilación/Simulator; firma y APNs físicos son otro paso.

## Pantallas preparadas

Inicio con marca existente, empresa, fecha de Guatemala, cuatro métricas, gráfica de cobros por mes y próximos trabajos. Cuadrícula adaptable a una columna en Dynamic Type accesible. Datos salen de las reglas financieras del CRM; no porcentajes, importes ni tendencias inventadas. La lista de próximas bodas excluye fechas pasadas y mantiene empresa activa.

Más identifica empresa y correo del propietario. Se conserva la navegación nativa y el diseño web global. El logo `FlowingMark` es una copia idéntica del icono ya existente, no una ilustración nueva.

Estos cambios fueron revisados estáticamente; **no son capturas de una app ejecutada**. La calidad visual y el funcionamiento iOS siguen pendientes de compilación y Simulator.

## Pruebas ejecutadas

| Comprobación | Resultado |
| --- | --- |
| Suite CRM final | 1548 passed, 1 skipped; 23.33 s. |
| API móvil específica | 24 casos; incluye gráfica con fechas de cobro y trabajos próximos. |
| Contratos Swift/Foundation | 14 verificaciones compiladas y ejecutadas con las herramientas existentes. |
| HTTPS local verificado | Conexión por código, aprobación sesión/CSRF, consultas, cobros, aislamiento, avisos, lectura y logout aprobados. |
| Compilación iOS Simulator | Bloqueada, exit 69 por licencia Apple pendiente. |
| Capturas claro/oscuro/texto grande | No ejecutadas todavía. |
| iPhone físico / push cerrado | No ejecutados. |

Logs locales recuperables:

- Build: `.context-shears/artifacts/command-20261010T105345.969048Z-bc9ed4c9f22d.log`.
- Suite: `.context-shears/artifacts/command-20261010T110012.222378Z-1b7905ecce3d.log`.
- Swift: `.context-shears/artifacts/command-20261010T110012.222546Z-3cda38548b93.log`.

## Entorno de prueba

`tools/run_mobile_qa.py`: fixture HTTPS exclusiva de loopback con almacenamiento sintético, certificado de duración corta y archivo de control 0600. Envía cero correos y cero push. No importa datos reales ni añade un bypass HTTP de login. `tools/check_mobile_qa.py` verifica certificado/origen y usa las rutas reales de autorización con una cuenta ficticia.

El servidor local iniciado en esta ejecución usa `.local/iphone-qa-4f6y_z2u/`. Las credenciales de control no deben publicarse ni copiarse al repositorio. Un futuro proceso de QA puede generar un fixture nuevo; comprobar que el puerto 5443 corresponda al runner propio antes de detenerlo.

## Continuación solicitada durante la noche

Seguimiento asociado a este chat: «Continuar Flowing para iPhone», cada 30 minutos. Mantiene silencio mientras el requisito conocido de licencia siga igual. Cuando el propietario lo complete, intenta compilar y revisar Simulator, sin aceptar contratos ni eludir autorización. Debe detenerse al completar esas pruebas o si el usuario lo cancela.

El siguiente hito verificable es build iOS exit 0 + app instalada en un Simulator propio + capturas reales en claro, oscuro y texto grande. No declarar «versión final para iPhone» antes de esa evidencia y de la firma/prueba en dispositivo físico.
