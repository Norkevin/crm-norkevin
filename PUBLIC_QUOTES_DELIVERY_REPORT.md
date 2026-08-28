# Cotizaciones Premium (Public Quote Experience) — Reporte de entrega

28 de agosto de 2026. Este documento resume, en lenguaje simple, qué se construyó a partir del pedido "IMPLEMENTAR COTIZACIONES PREMIUM TIPO NORKITO EN FLOW CRM" y en qué estado queda. No es documentación técnica exhaustiva — para eso está `PUBLIC_QUOTES_AUDIT.md` (el mapa completo hecho antes de tocar nada) y el historial de commits, cada uno con una explicación larga de qué cambió y por qué.

## 1. Qué se encontró (BLOQUE A)

El sistema de Quotes ya existía y funcionaba: crear cotización, armar hasta 3 opciones de paquete, elegir plan de pago, enviar por correo, el cliente la ve y la acepta en `/quotes/<id>`, y al aceptar se convierte el lead en job, se generan los pagos y se marca todo como aceptado — con protección real contra aceptar dos veces por accidente (ya probada con concurrencia real en el trabajo de estabilización de este mismo mes). Lo que faltaba era todo lo "premium": una vista pública que no pareciera un documento interno, paquetes con estructura (grupos, fotos, etiquetas), agregados opcionales, portafolio, condiciones configurables, numeración por cuenta, y una capa de seguridad para el link público que ya estaba escrita (`src/public_tokens.py`) pero nunca se había conectado a nada.

## 2. Qué se reutilizó

Todo lo que ya funcionaba y era delicado se dejó intacto: la función que acepta una cotización, la conversión de lead a job, el candado que evita crear el mismo job dos veces, el motor que genera el calendario de pagos, y la resolución de marca/tenant (Astral vs. Norkevin vs. Ramiro). Nada de esto se reescribió — se le agregaron datos nuevos por afuera y se conectó lo nuevo llamando a las mismas funciones de siempre, nunca copiándolas.

## 3. Qué se agregó

Un número de cotización por cuenta y por año (AST-2026-0007, NORK-2026-0012). Opciones de paquete con subtítulo, etiqueta, precio anterior tachado, horas de cobertura, descripción y grupos de inclusiones con título (en vez de una sola lista plana). Agregados opcionales (extras) que el cliente puede sumar a su paquete, con precio siempre controlado por el servidor. Un portafolio de bodas de referencia, plantillas de condiciones reutilizables, y plantillas de cotización completas (paquetes + plan de pago + condiciones) para no empezar de cero cada vez. Un tema visual (colores, texto del botón) configurable por cuenta. Y la vista pública nueva, con diseño propio, totalmente distinta al resto del CRM.

## 4. Cómo se crea una cotización

Igual que antes — desde un lead o un job, "Nueva cotización" — pero el editor (`/quotes/<id>/edit`) ahora tiene más campos por opción, una sección para armar los agregados, una para elegir qué fotos de portafolio y qué condiciones mostrar, y un botón "Guardar como plantilla" para reutilizar todo eso en la próxima cotización parecida. Nada de esto es obligatorio: se puede seguir armando una cotización simple con nombre + precio + lista de inclusiones, exactamente como siempre.

## 5. Cómo la ve el cliente

Entra por un link tipo `flowingcrm.com/q/<código largo>` que llega en el correo. La página es un diseño editorial: fondo claro, tipografía grande tipo revista para el nombre de la pareja y los precios, una sola franja oscura al final (el pie de página), sin nada que se parezca al dashboard del CRM. Arriba el número de cotización y los datos de la boda, después cada opción de paquete con su precio y lo que incluye, los agregados si los hay, el plan de pago, el portafolio de bodas de referencia y las condiciones. Pensada primero para el teléfono, porque así es como la mayoría la va a abrir.

## 6. Cómo acepta

Elige una opción, marca los agregados que quiera, elige en cuántas cuotas quiere pagar, y confirma. El botón queda deshabilitado hasta que eligió opción y plan. Puede rechazar en vez de aceptar. El link viejo (`/quotes/<id>`) sigue funcionando para cotizaciones enviadas antes de este cambio — nadie se queda con un link roto.

## 7. Qué ocurre automáticamente al aceptar

Exactamente lo mismo que antes (conversión a job, cálculo del calendario de pagos, factura), más lo nuevo: el precio de los agregados elegidos se suma al total usando el precio guardado en el servidor — nunca lo que mande el navegador — y ese total (paquete + agregados) es el que se reparte entre las cuotas. Si el cliente vuelve a entrar y aprieta aceptar otra vez (doble click, recargar la página), no se genera un segundo job ni un segundo calendario de pagos, y los agregados ya cobrados no cambian.

## 8. Pruebas ejecutadas y resultados

Este sandbox no tiene pytest instalado (mismo problema que ya venía arrastrando el proyecto desde la fase de estabilización — no es nuevo de este trabajo). Se escribieron 46 pruebas nuevas repartidas en dos archivos (`test_public_quote_experience_bloque_b.py` y `_bloque_c_a_f.py`) cubriendo numeración, aislamiento entre cuentas, snapshot al enviar, el link público, y sobre todo la parte de dinero: que el precio de los agregados salga siempre del catálogo del servidor, que un id inventado se ignore, que un id repetido no cobre doble, y que aceptar dos veces no duplique ni recalcule nada. No se pudieron correr con pytest real, así que se verificaron de otra forma: `tools/verificacion_final.py` (compilación, sintaxis, y las guardas propias del repo) en verde en cada bloque; cada plantilla se renderizó a mano con datos reales fuera de Flask para confirmar que el HTML que produce es el esperado (así se encontró y corrigió un bug real de plantilla que ningún chequeo estático hubiera visto); y, antes de cerrar el bloque de dinero, se usó un segundo agente sin mi propio análisis para revisar el código de aceptar-con-agregados buscando fallas — encontró 4 problemas reales (plan de pago sin límite superior, precio infinito/NaN, id repetido cobrando dos veces) que se corrigieron con su propia prueba de regresión cada uno.

## 9. Validación Norkevin Photography

No hay forma de abrir la app real en este sandbox (no hay Flask instalado, y el navegador interno no llega a un servidor que yo levante). La validación fue a nivel de código: todas las pruebas de aislamiento (tema, portafolio, condiciones, presentación de una cotización) se escribieron probando explícitamente con la cuenta de Norkevin Photography como una de las dos partes, confirmando que sus datos nunca aparecen del lado de Astral y viceversa. Falta la prueba real de "entrar con esta cuenta y tocarlo" — eso queda pendiente de que Kevin lo haga una vez publicado.

## 10. Validación Astral Weddings

Mismo caso que el punto anterior, con Astral como la otra mitad de cada prueba de aislamiento. Astral es además la cuenta que ya tenía datos reales de prueba en el CRM, así que buena parte de las pruebas usan su tenant_id por default (mismo fixture que ya usaba el resto del repo antes de este trabajo).

## 11. Evidencia visual

No se pudieron tomar capturas de pantalla reales — el navegador interno de este entorno no genera capturas en ninguna página (falla conocida, no específica de este trabajo), y no hay manera de instalar un navegador headless para generarlas por otra vía (se intentó, bloqueado por la política de red del sandbox). Lo que sí se hizo: se visitó la página de referencia que Kevin dio (`cotizacion-norkevin.pages.dev`) y se extrajeron los valores exactos de diseño (colores, tipografías, tamaños, espaciados) directamente del CSS ya calculado de esa página, no a ojo — esos son los valores que terminaron en el tema por default. Y se verificó matemáticamente que la tipografía escalable (`clamp()`) del diseño nuevo se comporta bien en los 4 anchos que pidió Kevin (390, 430, 768, 1440), sin desbordes ni tamaños raros en el más angosto. La validación visual real — abrirlo en un teléfono y mirarlo — todavía la tiene que hacer alguien una vez que esto esté publicado.

## 12. Archivos principales modificados

`app.py` es donde vive casi toda la lógica nueva (unas 700 líneas agregadas sobre ~11.000 que ya tenía). `templates/quote_view.html` y `quote_accepted.html` se rediseñaron por completo (lo que ve el cliente). `templates/quote_edit.html` se extendió (el editor interno). `templates/settings_quotes.html` es una página nueva (tema, portafolio, condiciones, plantillas). `templates/settings.html` tiene un enlace nuevo a esa página. `src/quote_numbering.py` es un archivo nuevo, chico, solo para la numeración. `src/storage.py` tiene dos funciones nuevas (contador atómico, resolución de token público). Los dos archivos de pruebas ya mencionados.

## 13. Migraciones realizadas

Ninguna migración de datos — el CRM guarda todo en archivos JSON, no hay una base de datos con esquema que migrar. Los campos nuevos (número, tema, agregados, grupos, etc.) son todos opcionales: una cotización vieja que no los tiene sigue funcionando exactamente igual que antes, con una cotización de un solo paquete y una lista plana de inclusiones. Las tablas nuevas (`portfolio_items`, `quote_terms_templates`, `quote_templates`) se crean solas la primera vez que se guarda algo ahí — no hace falta ningún paso manual.

## 14. Riesgos y pendientes reales

Nada de esto está probado con pytest de verdad todavía — hay que correrlo en Windows como se viene haciendo con el resto del proyecto. El PDF de la cotización sigue mostrando el total correcto (agregados incluidos) pero no lista los agregados como líneas separadas — es una mejora menor, no un error. Portafolio y condiciones arrancan vacíos para las tres cuentas: hay que cargar fotos y textos reales desde Settings > Cotizaciones antes de que se vea completo (es contenido de Kevin, no algo que yo debiera inventar). Y lo más importante: nada de esto es visible en producción todavía — ver el punto siguiente.

## 15. Estado de producción / deploy

**No está publicado.** Los 9 commits de este trabajo están guardados en este repositorio local, no en GitHub ni en Render. Para que llegue a `flowingcrm.com` hace falta lo mismo de siempre: subir los cambios a GitHub desde GitHub Desktop (yo no tengo forma de hacer `git push` desde este entorno) y esperar a que Render haga el deploy automático. Una vez publicado, recién ahí tiene sentido hacer la validación visual real en un teléfono y la prueba de extremo a extremo con una cotización de verdad.
