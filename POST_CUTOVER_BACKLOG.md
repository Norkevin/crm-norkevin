# POST_CUTOVER_BACKLOG.md

**Estado:** BACKLOG PREPARADO — **nada de esto está iniciado ni debe iniciarse todavía.**
**Fecha:** 20 de agosto de 2026.

Lista agrupada de lo que ya sabemos que falta, en base a la experiencia de
uso diario y a lo detectado durante la fase de estabilización. Es un
inventario, no un plan de trabajo: el orden dentro de cada prioridad es
sugerido, no comprometido.

**Regla que aplica a todo este backlog:** cualquier cambio de esta lista
se evalúa para **ambas empresas** (Astral Weddings y Norkevin
Photography). Una mejora que sólo funcione para una marca no se considera
terminada.

---

## P1 — Operación diaria

Lo que afecta el uso del CRM todos los días. Es lo que más fricción causa
hoy.

| # | Ítem | Notas de contexto |
|---|---|---|
| 1.1 | **Estados correctos de jobs** | Ya existe `_job_estado_label()` separando estado del evento (por fecha) del avance del workflow — Kevin fue explícito: *"un evento futuro no debería verse como completado si todavía no ocurrió"*. Falta revisar que los estados resultantes coincidan con cómo se piensa el negocio en la práctica |
| 1.2 | **Activos vs completos** | Distinción operativa clara entre bodas por venir, bodas pasadas por cerrar, y bodas cerradas. Hoy se infiere de `dias_restantes` + `workflow_progress`; falta una vista/filtro explícito |
| 1.3 | **Múltiples clientes por job** | Existen `secondary_client_id` y `planner_client_id` en el modelo, y `project_clients` en el schema V5.2 (con `is_primary`/`is_billing_contact`/`is_portal_contact`). Falta que la interfaz lo refleje bien: quién recibe qué correo, quién ve el portal, a quién se le factura |
| 1.4 | **Payments / statuses** | Estado financiero ya está separado del estado del job (`_job_pago_label`). Falta revisar los estados intermedios: abonos parciales, cuotas vencidas, saldo tras un abono directo, y cómo se muestran de forma no ambigua |
| 1.5 | **Datos de locación correctos** | `location`/`locacion` viaja desde el lead al job pero no siempre está limpio ni normalizado. Afecta contratos y PDFs, que lo imprimen |
| 1.6 | **Sorting newest/oldest** | Hoy `/jobs` ordena por `dias_restantes`; leads y otras vistas ordenan por criterios distintos. Falta un criterio de orden consistente y controlable por el usuario |
| 1.7 | **Filtros** | Filtrar por estado, marca, fecha, cliente, estado de pago. Hoy prácticamente no existen |

**Deuda técnica de P1 ya identificada** (de la fase de estabilización, no
de uso diario, pero afecta operación):

- `data/workflow_instances.json` tiene 143 filas huérfanas de datos demo
  que `reset-test-data` no limpia — basura operativa a remover
- `settings_tenant-norkevin-photography.json` no existe: Norkevin
  Photography no tiene datos empresariales guardados (teléfono, banco).
  No es fuga cross-tenant, pero sí un hueco antes de operar esa marca

---

## P2 — UX

| # | Ítem | Notas de contexto |
|---|---|---|
| 2.1 | **Responsive** — *cerrado 26-ago* | El CRM se usa también desde el teléfono, en eventos y reuniones con clientes. Hoy está pensado para escritorio |
| 2.2 | **Navegación** | Moverse entre lead → cliente → job → cotización → contrato → pagos requiere demasiados saltos y vueltas atrás |
| 2.3 | **Consistencia visual** — *cerrado 26-ago* | Colores, badges de estado, tipografía y espaciado varían entre vistas. Cada marca tiene su color (`#2F7D73` Astral, `#0284C7` Norkevin) — la consistencia debe respetar eso sin que cada pantalla se vea de una familia distinta |
| 2.4 | **Vistas de job / client** — *client detail cerrado 26-ago* | Las dos pantallas más usadas. Job detail concentra mucha información sin jerarquía clara; client detail estaba comparativamente vacío |

---

## P3 — Automatizaciones

**Nada de esta sección se activa antes de STAGE 3** del plan de correo
(ver `CONTROLLED_CUTOVER_PLAN.md`, fase 5). El incidente de agosto de 2026
—cientos de correos con la marca equivocada, a los clientes de la otra
empresa— nació precisamente de una automatización corriendo sin
supervisión.

| # | Ítem | Notas de contexto |
|---|---|---|
| 3.1 | **Workflows** | Revisar los pasos de `PRODUCTION_WORKFLOW` contra el proceso real de trabajo. Hoy hay pasos que se saltan en bloque en jobs importados, justamente para no re-disparar correos |
| 3.2 | **Recordatorios** | Recordatorios de pago y de entrega. Requieren identidad de marca correcta por tenant y respetar la cola de aprobación |
| 3.3 | **Emails automáticos** | Activar **uno por uno**, con ventana de observación entre cada uno. Nunca dos en la misma ventana |
| 3.4 | **Aprobación** | La cola (`queue_email` → `approve_and_send`) ya existe y funciona. Falta la interfaz para operarla cómodamente en el día a día |
| 3.5 | **Templates** | Plantillas por marca, con `%company_name%` resolviéndose por `tenant_id` canónico (ya corregido en la fase de estabilización). Falta gestión de plantillas usable y revisar que ninguna plantilla vieja tenga marca escrita a mano |

---

## Fuera de alcance de este backlog

Estos ítems ya están registrados en `STABILIZATION_EXECUTION_REPORT.md`
(sección 13, riesgos abiertos) y **no** son mejoras de producto — son
condiciones técnicas del cutover:

- Ejecución real de la suite Flask/pytest en Windows
- Contratos sin tabla destino en V5.2
- Garantía dura de idempotencia bajo concurrencia real (depende de
  `origin_action_key` en SQLite V5.2)
- Atomicidad entre tablas en `/api/admin/reset-test-data`
- `origin` explícito (`manual` vs `lead_conversion`) para `projects`

---

**No empezar ninguna de estas mejoras todavía.** El orden es: cerrar la
validación de Windows → cutover controlado → smoke test verde en ambas
marcas → operación estable en STAGE 1 → y recién entonces abrir P1.

---

# POST-CUTOVER — detectado durante el cutover del 21-ago-2026

Estado: **`CRM_OPERATIONAL`** (STAGE 1). Nada de esta lista bloquea el uso
diario; todo es mejora sobre un CRM que ya funciona.

## Reinicio pendiente para cargar el código nuevo

El proceso que está sirviendo `localhost:8765` arrancó el 20-ago a las
18:21. `app.py` cambió después (02:15 del 21-ago) con el modelo
N-clientes, el resumen único de pagos y los estados por fecha+pago. Con
`FLASK_DEBUG=0` **no hay autoreload**, así que ese proceso sirve el código
anterior.

**Acción:** cerrar la ventana del CRM y volver a hacer doble clic en
`abrir_crm.bat`. Un reinicio limpio, sin riesgo: los datos viven en
`data/`, no en el proceso.

## P0 — pendientes funcionales del bloque estructural — **CERRADOS**

| # | Ítem | Estado |
|---|---|---|
| 0.1 | Formulario de edición de clientes en `job_detail.html` | **Cerrado.** La tarjeta recorre `job_clientes` (0..N) con `<select>` de rol, Quitar y "+ Agregar cliente" |
| 0.2 | Mostrar el schedule activo en `job_detail` | **Cerrado.** Se pinta el calendario activo y el historial `superseded` |
| 0.3 | Mostrar `descuadre_cotizado_vs_cuotas` | **Cerrado.** Se muestra como chip cuando es distinto de 0 |

## P1 — operación diaria (de la lista original)

Sin cambios respecto a lo ya registrado: filtros útiles, navegación entre
entidades sin botón atrás, y jerarquía funcional de `job_detail`
(encabezado → finanzas → comercial → operación → documentos). Los tres
quedaron explícitamente fuera del cutover.

## P2 — UX

Responsive, consistencia visual, vistas de job/cliente.

## P3 — automatizaciones

Solo después de STAGE 2 (envío manual aprobado). Ninguna se activa antes.

## Deuda conocida que sigue abierta

- `data/workflow_instances.json` con 143 filas huérfanas de datos demo.
- `settings_tenant-norkevin-photography.json` no existe: Norkevin
  Photography sin datos empresariales (teléfono, banco) guardados.
- Contratos sin tabla destino en V5.2 (irrelevante hasta que se retome la
  migración a SQLite, que quedó diferida a propósito).


---

# DAILY_USE — cerrado el 21-ago-2026

Trabajo posterior al cutover, orientado a que el CRM se pueda usar con
clientes reales desde hoy. Todo probado en las **dos marcas**.

## Bugs de uso diario corregidos

| Área | Qué pasaba | Qué se hizo |
|---|---|---|
| Ficha de cliente | `/clients` y `/clients/<id>` decidían qué bodas eran de un cliente mirando sólo `job.client_id`, o sea el rol `principal`. La novia entra como `pareja`: **al abrir su ficha, su propia boda no aparecía** | Índice `_jobs_por_cliente()` que reconoce cualquier rol, lee `job_clients` una sola vez y respeta el adapter legacy |
| Clientes duplicados | El desempate entre dos fichas con el mismo correo prefería la que estuviera en un job *como principal*. Si la buena estaba como `pareja`, ganaba el duplicado huérfano | El desempate ahora cuenta cualquier rol |
| Workflows | `/api/workflow/instances`, su historial, la actividad del dashboard y el contador de Settings listaban el diccionario global del motor: **Astral veía los nombres de las bodas de Norkevin** | Las cuatro puertas se filtran por cuenta, resolviendo el dueño por el job/lead que ya pasó el filtro de tenant |
| Workflows huérfanos | Las 143 instancias de datos demo apuntaban a jobs borrados y seguían apareciendo | Una instancia sin job visible deja de listarse |
| Botón "Quitar" cliente | `onclick="...{{ x\|tojson }}"` con comillas dobles cortaba el atributo: el botón quedaba **sin función y sin error visible** | Comillas simples. La guarda de regresión ahora cubre cualquier `on*=`, no sólo `onclick` |
| Páginas de error | `404.html` y `500.html` existían pero **nunca se mostraban**: no había ningún `@app.errorhandler`. Un enlace viejo dejaba una pantalla en blanco de Flask, sin menú y sin salida | Handlers registrados. Las rutas `/api/` devuelven JSON; en pytest la excepción sigue subiendo para no esconder bugs |
| Marca en la pestaña | 24 plantillas tenían `ASTRAL WEDDINGS CRM` escrito a mano en el `<title>`. Norkevin veía la otra empresa en la pestaña del navegador | `{{ current_tenant.name }}`, con guarda de regresión |
| Ficha del job | El **Resumen de pagos** vivía dentro de la pestaña "Facturas": al cambiar a Cotizaciones o Contratos, el saldo pendiente desaparecía | Movido fuera de las pestañas: visible con cualquiera activa |
| `stats()` del motor | `by_status` nunca incrementaba (siempre 0) y sumaba las dos marcas | Se calcula en la ruta, sobre las instancias de la cuenta |

## Tests nuevos

`test_uso_diario_clientes.py`, `test_uso_diario_workflows_calendario.py`,
`test_navegacion_diaria.py`, `test_paginas_de_error_y_marca.py`,
`test_rendimiento_vistas.py` — todos parametrizados por marca y añadidos
a la fase `daily_usage` del runner. Siguen siendo 14 fases: el gate no se
tocó.

## Rendimiento

Chequeo estático sobre las 9 pantallas de uso diario: **ninguna lee el
store dentro de un loop**. Queda como guarda permanente
(`test_rendimiento_vistas.py`), porque ese patrón no se nota con 20 bodas
y sí con 300.

## Deuda que sigue abierta

- `data/workflow_instances.json` conserva las 143 filas huérfanas en
  disco. Ya no se muestran, pero conviene podarlas cuando haya un momento
  tranquilo.
- `settings_tenant-norkevin-photography.json` no existe: Norkevin
  Photography sigue sin datos empresariales guardados (teléfono, banco).
  Se llenan desde Settings al empezar a operar esa marca.
- Los *templates* de workflow siguen compartidos entre cuentas a
  propósito (ver nota en `_persist_workflow_template`). Lo que ya está
  aislado es el avance de cada boda.

---

# BLOQUE 1-4 — cerrado el 26-ago-2026

Responsive, consistencia visual, ficha de cliente con el modelo N, y
fricción de uso diario. Validado en Windows: 14/14 fases, 742 tests en
`full_suite`, 222 en `daily_usage` (corrida `20260826_181617`).

## Móvil (P2.1)

16px en inputs (evita el zoom automático de iOS), controles táctiles
llevados a ~44px (antes 25px en `.sn-btn-sm`), radios del cuestionario a
24px, tablas de 5-7 columnas (dashboard, Equipo, Pagos a equipo,
Configuración, plan de pago de la cotización) apiladas como ficha
etiqueta/valor en vez de scroll lateral.

## Consistencia visual (P2.3)

Ajustes de prosa sobre lo existente, no rediseño: tamaños de fuente y
espaciados dispares entre pantallas, sin tocar el color de marca de
ninguna de las dos empresas.

## Ficha de cliente (P2.4)

Ahora usa exclusivamente los helpers canónicos (`_relaciones_por_job`,
`_job_payment_summary`, `_job_estado_label`) en vez de recalcular nada a
mano. Por cada boda del cliente: rol, si recibe documentos, estado real
del evento, cuánto falta por cobrar, y con quién más comparte la boda. Más
un resumen agregado arriba (bodas activas, pendiente total, próximo pago,
cuotas vencidas).

## Fricción de uso diario

Estados vacíos con explicación y botón de acción en las 6 listas
principales (antes `/jobs` mostraba una tabla vacía sin ningún texto);
aviso cuando un filtro no encuentra nada; el dashboard arma el nombre de
la boda con el mismo helper que `/jobs` (antes mostraba un solo novio);
los datos semilla de Configuración (cuentas, reglas de pago) nacen con la
marca de la cuenta que los crea en vez de `'ASTRAL WEDDINGS'` fijo — el
mismo patrón de bug que causó el incidente de correo de agosto.

## Deuda que sigue abierta (siguiente bloque)

Del backlog original P1: **locación** (1.5, dato sucio que viaja a
contratos y PDFs), **orden/filtros** (1.6/1.7, sin criterio consistente ni
filtros por estado/marca/fecha/pago), y los puntos ya identificados sobre
**clientes múltiples** (1.3 — falta reflejar quién ve el portal y a quién
se factura, más allá de la edición de roles que ya está resuelta) y
**estados de pago intermedios** (1.4 — abonos parciales, cuotas vencidas,
saldo tras un abono directo, mostrados sin ambigüedad). `P2.2` navegación
queda para después de eso.

---

# BLOQUE 5 — en curso, cerrado el 26-ago-2026 (primera vuelta)

## P1.3 — portal y facturación en clientes múltiples

**Bug real encontrado y corregido:** `client_portal()` buscaba los jobs de
un cliente mirando solo `job.client_id` (el principal). La pareja -- que
SI recibe documentos segun `ROLES_DESTINATARIOS_DOCUMENTOS` -- entraba a
SU PROPIO link de portal y lo veia vacio: sin su boda, sin su cotizacion,
sin su contrato, sin sus cuotas (esas se crean siempre con el client_id
del principal). Ahora los jobs se resuelven por relacion `job_clients` con
rol, y pagos/contratos tienen fallback por `job_id`. El wedding planner
sigue sin ver nada en su portal a propósito -- es la misma regla de "el
planner nunca recibe contratos", no un bug nuevo.

**Hallazgo colateral, más grave que lo que se estaba buscando:**
`contract_view.html`, `quote_view.html`, `questionnaire_view.html`,
`client_portal.html`, `quote_edit.html` y `quote_accepted.html` tenían
"ASTRAL WEDDINGS" escrito a mano en el HTML que ve el cliente. El PDF de
cada uno de esos documentos ya estaba corregido desde la fase de
estabilización (`resolve_pdf_brand`), pero la vista web que abre el mismo
link -- la que el cliente realmente visita -- nunca pasó por el mismo
arreglo. Un cliente de Norkevin Photography veía la marca de la otra
empresa en la página de su propio contrato firmado. Mismo patrón en los
defaults de JS al componer un correo desde job_detail/lead_detail/leads
(esto Kevin lo ve antes de enviar, pero podría no notarlo), y en el
fallback de nombre de empresa de Settings cuando la cuenta no guardó su
company (el hueco que este mismo documento ya tenía anotado). Los seis
templates ahora reciben `brand=resolve_pdf_brand(tenant_id)` desde su
ruta y lo usan; se agregó una guarda estática permanente
(`tools/verificacion_final.py`, "marca fija en el cuerpo de las
plantillas") que escanea el CUERPO de las 43 plantillas, no solo el
`<title>`, para que esta clase de bug no vuelva a colarse sin que se note.

Tests nuevos: `tests/test_marca_en_documentos_cliente.py` (marca correcta
en los 6 documentos + compose de correo + fallback de Settings) y 2 tests
nuevos en `test_public_client_pages.py` (pareja ve su boda en su portal,
planner no ve nada). Ambos ya en la fase `daily_usage` del runner.

## Confirmado ya cerrado (de vueltas anteriores, no de hoy)

Al investigar P1.3 se revisaron también los otros puntos pendientes del
backlog original y ya estaban resueltos: **P1.1** (estados activo/completo
por fecha+pago, `_job_estado_label` + `estado_key` canónico),
**P1.2** (mismo mecanismo), **P1.4a** (`_job_payment_summary` como única
fuente, sin fórmulas duplicadas), **P1.5** (locación — causa raíz del
bucle venue↔address cerrada en las 3 puertas, 0 contaminados en el dataset
legado), **P1.6** (orden por relevancia, `_job_orden_relevancia`) y la
mayor parte de **P1.7** (filtro de estado -- incluye "por cobrar" -- y
búsqueda libre). Todo con tests ya en el runner.

## Deuda que sigue abierta

- **P1.7 restante** — falta específicamente un filtro por rango de fecha
  en `/jobs`.
- **P2.2** — navegación entre lead → cliente → job → cotización →
  contrato → pagos: reducir saltos y vueltas atrás.

**P1.4b — cerrado 26-ago.** `job_detail.html`, `payments.html` y
`quote_view.html` ahora muestran, en cuotas parciales/vencidas, "de
Q{original_amount}" junto al saldo actual, y los chips distinguen
Pagado/Vencida/Parcial/Pendiente (antes solo existían Pagado/Pendiente).
`job_detail.html` además muestra "(abonado QX)" usando el campo
`paid_amount` **tal cual** -- nunca `original_amount - amount`, porque esa
resta también cuenta el crédito que una cuota recibe de otra sobrepagada
como si fuera un abono directo en ella, exactamente el error que el propio
docstring de `_apply_payment_sequentially` ya advierte ("bug encontrado al
probar dos pagos seguidos"). Causa raíz → corrección con el campo
canónico → test de regresión que prueba las dos cuotas a la vez (una con
abono directo, una que solo recibió crédito) en
`test_job_detail_muestra_abonado_directo_pero_no_inventa_uno_por_credito`
(`tests/test_smart_payment_distribution.py`), cubierto por `full_suite`.
`invoices.html` ya mostraba Total/Saldo por separado -- no necesitó cambio.
