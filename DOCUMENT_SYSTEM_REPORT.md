# Sistema de documentos de Flow CRM — Reporte de entrega

29 de agosto de 2026. Cotizaciones y facturas ahora son dos documentos del mismo producto, no tres páginas hechas por personas distintas.

## 1. Qué cambió

Existe una capa visual compartida nueva: tres archivos (`_document_tokens.html`, `_document_base.html`, `_document_parts.html`) que definen los colores, la tipografía, los botones, las tarjetas, los badges, los totales y el historial de pagos **una sola vez**, y que usan tanto la cotización como la factura. Los valores son los mismos que ya usa el CRM por dentro (el violeta `#7357F6`, los grises, los radios, las sombras), así que el parecido entre el dashboard, la cotización y la factura no es una coincidencia: es literalmente el mismo sistema. Si mañana cambiás el color de una marca, cambian los dos documentos a la vez.

La cotización conservó su disposición, su jerarquía y toda su interacción — era lo que pediste explícitamente: no rediseñarla, vestirla. Lo único que cambió es con qué está vestida.

La factura pasó de ser un PDF a ser una página real. Tiene su propia URL pública con enlace seguro (`/i/<token>`, el mismo mecanismo que ya usaban las cotizaciones: en la base solo vive el hash del token, nunca el token). Muestra marca, número, estado, fechas, cliente, evento, conceptos, subtotal, pagado, saldo, calendario o historial de pagos, próximo pago, notas y condiciones. Los estados —borrador, pendiente, parcialmente pagada, pagada, vencida, cancelada— se deducen del modelo real, y nunca dependen solo del color: cada badge lleva su texto, y el punto de una vencida tiene forma distinta al de una pagada.

Desde la vista interna de una factura ahora hay un botón "Ver como el cliente" que abre exactamente el mismo documento sin tocar ni invalidar el enlace del cliente.

## 2. Qué se reutilizó

Todo lo que ya funcionaba. No hay tabla de facturas en este CRM: una factura es el conjunto de pagos que comparten `invoice_id`, y así se sigue tratando. Los cálculos usan los helpers de dinero de siempre. No se tocó la aceptación de cotizaciones, la conversión de lead a job, la idempotencia, el calendario de pagos, la numeración, los permisos ni el aislamiento entre cuentas. El enlace público de factura usa el mismo `public_tokens.py` que ya existía. Las condiciones y el branding se resuelven por cuenta con las funciones que ya estaban.

## 3. Cómo quedaron las cotizaciones

Iguales en estructura, distintas en piel: superficies blancas sobre lienzo gris claro, bordes finos con radios suaves, el paquete seleccionado se marca con un realce de color de marca y una línea superior en vez de un bloque plano, los botones son los del producto, y los montos usan cifras tabulares para que se alineen dígito con dígito. Sigue siendo un documento editorial y premium, no una pantalla administrativa.

## 4. Cómo quedaron las facturas

Es un documento hermano de la cotización: mismo header, misma tipografía, mismos componentes, pero más sobrio, como corresponde a información financiera. Lo primero que ve el cliente son las tres cifras —Total, Pagado, Pendiente— en un bloque de tres columnas que en el teléfono se apilan. Debajo, el próximo pago si lo hay, el detalle de conceptos, los totales y el historial completo de pagos con su estado.

## 5. Cómo quedó el PDF

Adoptó los mismos colores del documento web. Antes era azul marino con dorado, una paleta que no se parecía ni al CRM ni a la cotización. Sigue siendo la acción secundaria: la experiencia principal es la página web, y desde ahí se descarga.

## 6. Qué pruebas ejecuté

`tools/verificacion_final.py` en verde (134 módulos, 45 plantillas, todas las guardas de regresión del repo). Rendericé los 6 escenarios de cotización y los 5 estados de factura con Jinja standalone, verificando que no queda ni una variable CSS sin resolver. Comprobé a mano 8 casos de dinero. Generé PDFs reales de factura y cotización. Escribí `tests/test_document_system.py` con 20 tests nuevos.

Después le pedí a un revisor independiente —un subagente sin acceso a mi razonamiento, instruido para asumir que había bugs y encontrarlos— que auditara todo. Encontró 8 problemas, y tenía razón en los importantes:

- **Crítico**: las cotizaciones ya enviadas se habrían roto. Yo había creado la función que mezcla el tema congelado con los tokens nuevos, pero no la conecté en las dos rutas de cotización. Resultado: toda cotización en la bandeja de un cliente habría quedado con las variables CSS vacías y el botón de aceptar blanco sobre blanco.
- **Alto**: el saldo por cuota estaba mal calculado. Cuando un sobrepago se traslada como crédito a la cuota siguiente, esa cuota queda saldada sin que suba su campo de "pagado". Yo restaba pagado del original, así que una factura completamente saldada le habría dicho al cliente "Vencida — 1 pago vencido por Q5,000".
- **Alto**: el botón "Pagar en línea" leía un campo con el nombre equivocado y no aparecía nunca.
- **Medios**: una cuota cancelada inflaba el total y podía figurar como vencida; previsualizar el correo invalidaba el enlace que el cliente ya tenía; se ofrecía descargar un PDF que a veces no se podía generar.
- **Bajos**: una fecha de vencimiento vacía podía tumbar la página; la inicial de marca del PDF quedaba con poco contraste.

Los ocho quedaron corregidos, cada uno con su test de regresión. El revisor confirmó, sin encontrar nada, que no hay fugas entre cuentas y que la cotización no perdió nada de su lógica.

## 7. Deuda técnica pendiente

Tres cosas, ninguna bloqueante:

**Reenviar una factura invalida el enlace anterior.** Es el mismo comportamiento que ya tienen las cotizaciones (cada envío emite un token nuevo), así que es consistente, pero conviene saberlo: si se encolan dos envíos de la misma factura y se aprueba el más viejo, ese llevará un enlace ya rotado. Resolverlo bien requiere decidir una política de enlaces, no un parche.

**La tipografía del PDF no es exactamente la de la web.** El PDF usa Helvetica porque incrustar Inter y Fraunces significa meter archivos de fuente al repositorio, con su peso y sus licencias. Visualmente son cercanas; si querés paridad exacta, es una decisión aparte.

**Sin capturas de pantalla reales.** Misma limitación de siempre: no puedo instalar un navegador en este entorno. La validación fue por render, estructura y matemática. Mirarlo en un teléfono sigue pendiente.

Todo esto está en un commit local. Falta el mismo paso de GitHub Desktop de siempre para que llegue a flowingcrm.com.
