# Rediseño editorial de la cotización pública — Reporte de entrega

29 de agosto de 2026. Alcance del pedido: rediseñar únicamente la pantalla que ve el cliente al abrir su cotización (`/quotes/<id>` y `/q/<token>`, ambas servidas por el mismo `quote_view.html`). Nada más del CRM debía cambiar visualmente, y ninguna regla de negocio existente debía tocarse.

## Archivos modificados

`templates/quote_view.html` — reescritura completa (era la única forma honesta de llegar a la dirección de arte pedida; no extiende `base.html` ni comparte hoja de estilos con nada, así que no hay forma de que esto contamine el resto del CRM). `templates/quote_accepted.html` — la pantalla de confirmación ya no mostraba un `alert()`, ya era una página propia; se retocó solo para que use la misma tipografía nueva (`theme.serif_font`/`sans_font`) en vez de tenerla hardcodeada aparte. `templates/settings_quotes.html` — tres campos nuevos (símbolo/nombre de moneda, video destacado) siguiendo el mismo patrón que los campos de color que ya existían ahí. `app.py` — `_quote_theme_for_tenant()` con seis claves nuevas en el diccionario de tema (todas con default, ninguna cotización vieja se rompe); función nueva `_video_embed_url()` que traduce un link de YouTube/Vimeo pegado a mano a su embed; el whitelist de `/api/settings/quote-theme` extendido con los tres campos nuevos. `tests/test_public_quote_editorial_redesign.py` — nuevo.

## Qué cambió visualmente

Paleta blanco/negro/gris, tipografía serif editorial (Fraunces) para nombres/precios/títulos y sans limpia (Inter) para el resto, con contraste entre las dos. Header minimalista: nombre de la cuenta a la izquierda, tagline a la derecha, sin logo circular salvo que la cuenta tenga uno cargado. Hero con el nombre de los novios en grande, con el segundo nombre en itálica cuando el texto trae "&" o "y" en el medio (`Mónica & *Jacobo*`), metadata debajo sin cards. Las opciones de paquete pasaron de bloques apilados con sombra a columnas separadas por líneas finas, con el precio como protagonista y un badge "Recomendada" discreto que solo aparece si el paquete ya tenía esa etiqueta puesta desde el editor (nunca se inventa cuál es la mejor opción). Extras como lista editorial con "Agregar"/"Agregado ✓" en vez de checkbox. Plan de pago como pastillas negro/blanco. Resumen con el total y — esto es nuevo — el desglose por cuota antes de aceptar, no solo el monto de una cuota. Portafolio con video destacado opcional, galería en filas (no grilla de cards) y contraseñas con botón "Copiar"/"Copiado". Sección de condiciones como grilla con líneas finas en ambas direcciones, no una tabla. WhatsApp flotante que se esconde solo mientras el botón de aceptar está en pantalla, para no taparlo. Todo con `overflow-x: hidden` y `word-break`/`overflow-wrap` en los textos variables (nombres largos, ubicaciones largas) para que nada desborde.

## Qué lógica se preservó

`quote_accept()`, la conversión lead→job, el candado de idempotencia (doble-aceptar no duplica nada), la resolución de precio de extras siempre desde el catálogo del servidor (nunca lo que mande el navegador), la validación de `plan_pago` contra las cuotas que el admin realmente ofreció, el snapshot que congela tema/portafolio/condiciones al momento de enviar. Nada de esto se tocó — el formulario nuevo manda exactamente los mismos tres campos (`option_id`, `plan_pago`, `extra_ids`) al mismo endpoint de siempre. La selección de paquete y de extras ahora es un radiogroup/checkbox de verdad (con `role`, `tabindex`, `aria-checked` y manejo de teclado), no solo clickeable con el mouse, que era un pedido explícito.

## Qué tests ejecutaste

No hay pytest/Flask instalado en este entorno de trabajo (limitación ya conocida de todo este proyecto). Validé de tres formas: (1) reescribí la plantilla y la rendericé con Jinja2 puro fuera de Flask, con seis escenarios de datos representativos — cotización con varias opciones, con una sola opción, cotización vieja sin el campo `options`, cotización ya aceptada con extras y calendario de pagos, cotización rechazada, y una cuenta sin WhatsApp/logo/video configurados — los seis renderizan sin error; (2) corrí `tools/verificacion_final.py` (compilación de los 134 módulos, parseo de las 44 plantillas, y las guardas propias del repo, incluida la que prohíbe texto bajo 10px en móvil) en verde; (3) le pedí a un segundo revisor (un subagente sin acceso a mi propio razonamiento, instruido para asumir que había un bug y encontrarlo) que revisara todo el diff de forma adversarial.

Ese revisor encontró 2 problemas reales, los dos corregidos: un `font-size: 9.5px` en la moneda del precio que quedaba técnicamente fuera del bloque `@media` que vigila el test de legibilidad móvil (subido a 10.5px), y un test mío de aislamiento de Ramiro que sin querer dependía de que otros tests del mismo archivo hubieran corrido antes (lo reescribí para que escriba sus propios valores de prueba y no dependa de nada externo). Confirmó, sin encontrar nada, que el formulario de aceptar sigue mandando los campos correctos, que no hay fuga de datos entre cuentas en los campos nuevos de tema, que el dinero no se duplica ni diverge de lo que cobra el backend, que no quedó ningún `<table>`, y que la selección por teclado no tiene un target muerto ni un doble-disparo.

También verifiqué a mano (grep exhaustivo) que ningún test existente del proyecto depende de una clase CSS o de un id que este rediseño haya renombrado — todos los tests que tocan esta pantalla revisan texto/JSON, no estructura visual, así que la suite completa (idempotencia, aislamiento cross-tenant, precio de extras) sigue protegiendo lo mismo que protegía antes sin que yo tuviera que tocarla.

## Resultados

Todo lo anterior en verde. `tools/verificacion_final.py`: `RESULTADO: TODO VERDE`. Los 6 escenarios de render: `OK`. Los 2 hallazgos de la revisión adversarial: corregidos y reverificados.

## Screenshot desktop / Screenshot mobile

No pude generarlas. Es la misma limitación que ya quedó documentada en `PUBLIC_QUOTES_DELIVERY_REPORT.md`: no hay forma de instalar un navegador headless en este sandbox (intenté de nuevo, bloqueado por la política de red), y el navegador interno de este entorno no puede abrir un archivo local para tomarle una captura. Lo que hice en su lugar: verifiqué matemáticamente cada `clamp()` de tipografía y el layout de columnas de las opciones en los 4 anchos que pediste (390, 768, 1440, 1728px) — ningún valor cae fuera de un rango legible ni se desborda en ninguno de los cuatro. La validación visual real en un teléfono y una pantalla grande la tenés que hacer vos (o alguien) una vez que esto esté publicado — te dejo abajo cómo revisarlo rápido.

Para revisarlo vos: es exactamente el mismo commit local sin publicar de siempre — falta el mismo paso de GitHub Desktop que ya conocés. Una vez publicado, abrí cualquier cotización enviada (o armá una de prueba) en el teléfono y en la computadora.

## Cualquier limitación real

Screenshots (arriba). El selector de tipografía/fuente por cuenta no tiene un campo en Settings todavía — el sistema de tema ya lo soporta (`serif_font`/`sans_font`/`google_fonts_href` en el diccionario de tema), pero armar un selector de fuentes en la UI de Settings quedó fuera de este alcance porque no lo pediste explícitamente y no quería inflar una pantalla de configuración sin necesidad real todavía; si en algún momento una cuenta necesita otra tipografía, se puede cambiar sin tocar la plantilla. El video destacado necesita que alguien pegue el link de YouTube/Vimeo en Settings > Cotizaciones — arranca vacío para las tres cuentas. Portafolio y condiciones, igual que en la entrega anterior, siguen siendo contenido tuyo por cargar.

## Estado final

**PASS.**
