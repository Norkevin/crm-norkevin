# Capturas nativas de Flowing

10 de octubre de 2026, Guatemala. iPhone 16 en Simulator iOS 26.0 (23A343), app SwiftUI compilada con Xcode 27 y firma local ad hoc. Capturas originales mediante `simctl io` sobre UDID BC5B50F1-67F2-4A21-BB5E-3B2EB1FA385F. Sin edición de píxeles ni mockups.

Datos ficticios del runner HTTPS loopback, empresa «Norkevin · Demostración». Correo y APNs desactivados. Las capturas no contienen clientes reales.

Inicio, leads, trabajos, detalle, calendario, pagos y avisos en claro. Inicio y avisos en oscuro. Inicio y leads con la categoría `accessibility-extra-extra-extra-large`; leads también oscuro con ese tamaño. Pagos y avisos claros, y avisos oscuros se recapturaron tras corregir fechas/contraste. La sesión permanece en Keychain después de relanzar.

Revisión independiente inicial y dos pases de correcciones: `ship` acotado a fechas de pagos y contraste de avisos. No certifica toda la app, dashboard inferior completo, VoiceOver integral, todos los dispositivos, instalación física ni recepción push. El detalle conserva acceso a operaciones completas en la web durante esta etapa. Ver `../05-compilacion-y-qa.md`.


Capturas Teams posteriores: listas de coberturas/equipo, detalle de cobertura claro/oscuro y texto accesible máximo, detalle de miembro claro/oscuro. Importes recapturados tras contraste explícito. Inicio claro actualizado muestra la nueva pestaña Teams y los iconos de líneas adaptados de la web. Las otras capturas anteriores conservan la navegación histórica del incremento previo y no acreditan el menú actual.

## Calendario con fechas ocupadas — incremento del 10 de octubre

Estas capturas corresponden al calendario actualizado, ejecutado en el mismo Simulator y fixture HTTPS sintético:

| Archivo | Evidencia visible |
| --- | --- |
| `calendario-ocupadas-claro.png` | Mes en claro, puntos de fechas ocupadas y tentativas, selección del 14 y bloqueo del 13 al 15. |
| `calendario-ocupadas-oscuro.png` | Misma consulta en oscuro, con leyenda y rango de bloqueo. |
| `calendario-ocupadas-texto-grande.png` | Agenda del día en tamaño accesible máximo, colocada antes de la selección de fechas. |
| `avisos-web-sincronizados.png` | Bandeja ejecutada con avisos ficticios, incluidos los leads que incorpora la fuente web. |
| `avisos-push-apagado.png` | Ajustes ejecutados que distinguen la bandeja de las alertas con la app cerrada; activar y probar push deshabilitados. |

Son capturas nativas de UI ejecutada, sin modificación de píxeles. El texto grande hace que un registro ocupe buena parte de la pantalla: esta imagen acredita la agenda visible, no el selector completo, la lista inferior ni toda la navegación. Las capturas claro y oscuro muestran el mes y el inicio de la agenda; el contenido inferior requiere desplazamiento. No equivalen a una auditoría integral de accesibilidad o VoiceOver.

La antigua `calendario-claro.png` conserva el calendario del incremento anterior; no sirve para acreditar estas fechas ocupadas. Esta evidencia sintética no prueba aún la actualización instalada en el iPhone físico ni el calendario de clientes reales. No acredita notificaciones con la app cerrada. Estado, pruebas y límites: [Calendario y bandeja](../07-calendario-y-bandeja.md).
