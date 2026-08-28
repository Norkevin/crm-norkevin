# PUBLIC_QUOTES_AUDIT.md

Auditoría del sistema de Quotes existente, previa a implementar la nueva
Public Quote Experience (referencia visual: cotizacion-norkevin.pages.dev).
BLOQUE A del pedido de Kevin (28-ago-2026).

Conclusión corta: el motor de negocio (creación, opciones, aceptación,
conversión Lead→Job, pagos, idempotencia, aislamiento) ya existe, está
maduro y fuertemente probado — es el mismo motor que sobrevivió el
incidente de Camila Rios y la migración multi-tenant. Lo que falta es
casi enteramente presentación: un renderer público premium, un token de
acceso separado del id interno, extras, theming por empresa, portfolio,
condiciones configurables, numeración por empresa y templates de
cotización. Nada de esto requiere tocar ni duplicar el motor existente.

---

## 1. Arquitectura actual (dónde vive cada cosa)

| Capa | Archivo(s) |
|---|---|
| Rutas/lógica de Quotes | `app.py` (líneas ~10229-10708, más helpers en 940-1232 y 3309-3360, 8591-8670) |
| Modelo de datos | JSON files vía `JsonStore` (`src/storage.py`) — tabla `quotes`, sin ORM ni schema fijo |
| Calendario de pagos / idempotencia | `_ensure_payments_for_quote`, `_crear_schedule`, `_active_schedule_for` (app.py ~940-1052, ~4020-4118) — tabla `payment_schedules` |
| Conversión Lead→Job | `_convert_lead_to_job` / `_convert_lead_to_job_unlocked` (app.py 1144-1233) + `src/conversion_registry.py` (lock de exclusión mutua) |
| Identidad de marca | `src/tenant_brand_map.py` (canónica: tenant_id→brand_key→display_name→sender_email) + `src/pdf_generator.py::resolve_pdf_brand` |
| Tokens públicos seguros | `src/public_tokens.py` — **construido, con tests, NUNCA wireado a ninguna ruta** |
| Clasificación de enlaces activos/inactivos | `src/public_links.py` — construido, no activado |
| Templates HTML actuales | `templates/quotes.html`, `templates/quote_view.html`, `templates/quote_edit.html`, `templates/quote_accepted.html` |
| PDF | `src/pdf_generator.py::generate_quote_pdf` |
| Envío de correo | `src/mail_tracker.py` → `src/email_delivery.py` (mismo choke point que todo el CRM) |
| Catálogo de paquetes reutilizables | tabla `packages` (`_load_packages()`, app.py:3184) |

No existe ORM ni migraciones formales: cada "tabla" es un archivo JSON
(`data/<tabla>.json` local, `/var/data/<tabla>.json` en producción) leído/
escrito vía `JsonStore`, con aislamiento por `tenant_id` automático dentro
de un request (`store.list()`/`store.get()`) y `store.list_privileged()`
para los pocos casos cross-tenant legítimos (admin).

**Nota de arquitectura importante:** existen archivos `validate_schema_v4.py`,
`validate_schema_v5.1_BASELINE.py` de un intento de migración a SQLite
(tabla `sequence_counters` incluida) que **no se adoptó** — el CRM sigue
sobre JSON files, y `tools/verificacion_final.py` tiene un guardia explícito
"anti-SQLite-mount". La única excepción real es `src/conversion_registry.py`,
que usa SQLite de forma acotada solo como lock de exclusión mutua (una
PRIMARY KEY para el claim tenant_id+lead_id), no como almacén de datos.
Conclusión: la numeración por empresa (sección 9) debe implementarse sobre
JSON con escritura atómica (mismo patrón que `JsonStore._save`), no una
tabla SQL nueva.

---

## 2. Flujo actual, paso a paso

1. **Crear**: desde un lead (`api_lead_create_quote`) o un job
   (`api_job_create_quote`) — crea con paquete único (campos planos) — o
   vacío en `Borrador` multi-opción (`api_quote_create_draft` → `/quotes/<id>/edit`).
2. **Armar opciones** (multi-opción): `api_quote_option_save`/`_delete` —
   hasta **3 opciones** por cotización, cada una con `id, name, precio_total,
   incluye[], notas`. Bastante más plano que lo que pide Kevin (sin
   subtítulo, precio anterior, descuento, horas, grupos de incluidos,
   productos, fotos, orden, etiqueta).
3. **Forma de pago ofrecida**: `api_quote_payment_options` guarda
   `plan_pago_opciones` (lista de cuotas permitidas, ej. `[1,2,3,4]`).
4. **Enviar**: `api_quote_send` — genera la URL pública
   (**`/quotes/<quote_id>` — el id interno, expuesto tal cual, sin token** —
   ver gap #1), la registra vía `mail_tracker` (mismo choke point de email
   de todo el CRM, con kill switches ya probados), marca `status='Enviada'`.
5. **Vista pública**: `quote_view()` — renderiza `quote_view.html` con las
   opciones normalizadas (`_normalize_quote_options`, compatible con
   cotizaciones viejas de un solo paquete) y las cuotas ofrecidas
   (`_quote_plan_choices`). Sin tracking de vistas todavía.
6. **Aceptar**: `POST /quotes/<id>/accept` (`quote_accept`) — recibe
   `option_id` + `plan_pago`, "materializa" la opción elegida en los campos
   planos de siempre (`paquete_nombre`, `precio_total`, `incluye`, `plan_pago`,
   `cuota_monto` — calculado en backend, nunca confía en el frontend), y
   dispara `_accept_quote_for_existing_job` (si ya hay job) o
   `_convert_lead_to_job` (si viene de un lead nuevo).
7. **Conversión / pagos / workflow**: `_convert_lead_to_job` reserva la
   conversión con `conversion_registry.claim()` (PRIMARY KEY tenant+lead,
   así que llamadas concurrentes: solo una crea, las demás reentran por el
   camino idempotente y devuelven el MISMO job) → crea cliente si falta →
   crea job (`_ensure_job_for_lead`) → genera pagos
   (`_ensure_payments_for_quote`, con fechas inteligentes: primera cuota el
   día de aceptación, última 1 mes después de la boda, cuotas intermedias
   equidistantes) → marca las demás cotizaciones del mismo lead como
   `'Superada'` (`superseded_by_quote_id`).
8. **Doble aceptación / re-visita**: `quote_accept` chequea
   `quote.get('status') == 'Aceptada'` ANTES de todo — si ya está aceptada,
   muestra la misma página de éxito (`already=True`) sin re-ejecutar nada.
   Capa adicional: `_ensure_payments_for_quote` chequea si ya existe un
   `payment_schedule` ACTIVO para `tenant+job+quote_id` antes de crear uno
   nuevo. Tres capas independientes de idempotencia (status del quote,
   schedule activo, lock de conversión) — exactamente lo que pide la
   sección 9/25 del pedido, ya construido y ya stress-testeado (20
   iteraciones concurrentes, ver tasks previas de esta ingeniería).
9. **PDF**: `quote_pdf()` — usa `resolve_pdf_brand(tenant_id)`, nunca un
   nombre fijo.

---

## 3. Reutilización directa (NO tocar, solo conectar)

- `_convert_lead_to_job` / `conversion_registry` — motor de idempotencia.
  **No se toca. La nueva experiencia debe llamar exactamente a esto.**
- `_ensure_payments_for_quote` / `_crear_schedule` / `_active_schedule_for` —
  generación de calendario de pagos con guardas anti-doble-cobro.
  **No se toca.**
- `_accept_quote_for_existing_job` — reutilizable tal cual para el nuevo
  flujo de aceptación.
- `src/tenant_brand_map.py` + `resolve_pdf_brand` — identidad de marca
  canónica. La nueva capa de theming se CONSTRUYE ENCIMA de esto (agrega
  colores/logo/portfolio), nunca la reemplaza ni duplica el mapeo.
- `src/public_tokens.py` — el sistema de tokens que pide la sección 1 **ya
  existe, ya tiene diseño de seguridad revisado (hash SHA-256, comparación
  constant-time, huella segura para logs) y no está usado en ningún lado
  todavía**. Es, literalmente, la pieza que falta conectar.
- `store.list_privileged(..., reason=...)` — para cualquier lectura
  cross-tenant legítima que la nueva capa necesite (ninguna identificada
  todavía).
- Kill switches de email (`DISABLE_OUTBOUND_EMAIL`, `OUTBOUND_EMAIL_ENABLED`,
  `EMAIL_DELIVERY_MODE=test`) — se heredan automáticamente porque el envío
  sigue pasando por `mail_tracker`/`send_email`.
- `packages` (tabla) — catálogo de paquetes reutilizables por tenant, ya
  usado como base en `quote_edit.html` (`saved_packages`). Base natural
  para los "productos/incluidos" reutilizables del builder.

---

## 4. Gaps reales (lo que hay que construir — BLOQUE B en adelante)

1. **Public token**: rutas públicas (`quote_view`, `quote_accept`,
   `quote_decline`, `quote_pdf`) usan `quote_id` crudo en la URL. Hay que
   generar `public_token_hash` en creación (`public_tokens.emitir_para`),
   exponer `/q/<token>` (nueva ruta, resuelve vía
   `public_tokens.buscar_por_token`), y mantener `/quotes/<id>` como
   alias interno/admin (con sesión) — nunca como el link que se manda al
   cliente.
2. **Estructura de opciones**: extender `options[]` con subtítulo,
   descripción, precio anterior, descuento, horas, etiqueta (libre, no
   hardcodeada), orden, foto(s), y **grupos de incluidos** (hoy `incluye`
   es una lista plana de strings; hace falta soportar secciones tipo
   "Boda principal · Fotografía" / "Boda principal · Video").
3. **Extras**: concepto nuevo, no existe. Lista de add-ons por cotización
   (nombre, precio, tenant), selección del cliente, recálculo de total.
   Backend debe recalcular y validar el total recibido del frontend —
   nunca confiar en el número que manda el navegador (igual que ya hace
   `quote_accept` con `cuota_monto`).
4. **Portfolio**: tabla nueva (`portfolio_items`, tenant-scoped) + config
   en Settings → Quotes → Portfolio. Selección de cuáles mostrar por
   cotización (default de la empresa + override opcional).
5. **Condiciones**: tabla/registro nuevo de bloques reutilizables
   (`quote_terms_templates`, tenant-scoped) + snapshot inmutable dentro del
   quote al enviar (igual filosofía que ya usa `contract_terms()` en
   pdf_generator, que sí congela texto legal — replicar el patrón).
6. **Theme por empresa**: no existe hoy (colores del PDF son compartidos,
   confirmado en el código). Nuevo bloque en `settings.json` por tenant
   (`quote_theme`: colores, logo, background, acento, footer, CTA) resuelto
   con la MISMA filosofía fail-hard de `tenant_brand_map` (nunca cae a la
   marca de otro tenant).
7. **Numeración por empresa** (`NORK-2026-0041`): no existe (`quote.get('number')`
   se referencia pero nunca se asigna). Construir un contador atómico por
   tenant+año sobre JSON (archivo `data/sequence_counters.json`,
   incremento con el mismo patrón de escritura atómica que ya usa
   `JsonStore._save`, con lock de archivo) — no una tabla SQL nueva.
8. **Templates de cotización** (Boda, Boda foto+video, Civil, XV años...):
   concepto nuevo, distinto del catálogo `packages` (que es un paquete
   suelto). Un template pre-arma 1-3 opciones + condiciones + plan de pago
   default; al usarlo se copian como snapshot editable (igual patrón que
   condiciones: cambiar el template después no debe alterar cotizaciones ya
   creadas con él).
9. **Tracking de eventos**: `quote.created/sent` ya existen implícitamente
   (`created`, `sent_at`). Faltan `viewed` (primera/última vista, contador),
   `option_selected`, `expired`. Nada de fingerprinting — solo timestamps y
   contador, en el propio registro del quote.
10. **Estados faltantes**: `Rechazada`/`Superada` ya existen. Faltan
    `Vista` (viewed) y `Expirada` como estados activos (el set
    `QUOTE_CERRADA` de `public_links.py` ya los anticipa conceptualmente,
    solo falta que algo los produzca).
11. **Live preview**: no existe. Debe reusar el MISMO template/renderer que
    `/q/<token>`, alimentado con el draft en vez de con el registro
    guardado — nunca un segundo renderer.

---

## 5. Riesgos identificados

- **No romper `/quotes/<id>` como alias**: hay PDFs, emails ya enviados y
  automatizaciones (`api_quote_send` arma el link) que ya circularon con
  la URL vieja. La migración a `/q/<token>` debe ser aditiva: la ruta
  vieja sigue funcionando para lo ya enviado (mismo espíritu que
  `public_links.py`: "nada rota ni desactiva un enlace existente").
- **Opciones limitadas a 3** (`api_quote_option_save`): el pedido de Kevin
  no pide más de 3 en los ejemplos, así que no es un blocker, pero si el
  builder nuevo permite reordenar/duplicar libremente hay que decidir si
  ese límite se mantiene o se sube — lo dejo en 3 salvo que Kevin diga lo
  contrario (no es una decisión técnica).
- **`incluye` como lista plana vs. grupos**: cotizaciones YA ACEPTADAS
  tienen `incluye` en formato viejo (lista de strings). Extender el modelo
  a grupos debe ser retrocompatible (mismo patrón que `_normalize_quote_options`
  ya usa para el caso "un solo paquete legado").
- **Branding cruzado**: cualquier campo nuevo (portfolio, condiciones,
  theme) tiene que resolverse SIEMPRE por `tenant_id` del quote/job, nunca
  por sesión activa en las rutas públicas (mismo cuidado que ya tiene
  `quote_pdf`/`quote_view` con `resolve_pdf_brand`). Es el mismo tipo de
  bug que causó el incidente de agosto, aplicado a una superficie nueva.
- **`payment_schedules` no es un sistema de templates**: el pedido asume
  `payment_schedule_templates`. No existen — lo que existe es un algoritmo
  de fechas inteligente parametrizado por cantidad de cuotas. Documentado
  para no inventar un concepto que no está pedido de verdad por el
  negocio: se reutiliza el algoritmo tal cual, no se construye un sistema
  de templates de plan de pago nuevo salvo que Kevin lo pida explícitamente.

---

## 6. Ninguna decisión de negocio ambigua bloquea el arranque de BLOQUE B

Los gaps de la sección 4 son extensiones aditivas sobre un modelo ya
tenant-aislado, con el motor de aceptación/pagos ya probado. No encontré
nada que requiera credenciales, autorización de producción, ni una
decisión de negocio que cambie comportamiento financiero. Continúo con
BLOQUE B.
