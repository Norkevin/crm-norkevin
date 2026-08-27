# AUDITORIA CRM ACTUAL — Proyecto Narvis
**Fecha:** 9 de julio de 2026
**Auditor:** Norquito (asistente de Kevin)
**Estado:** Diagnóstico completado + primer parche crítico aplicado el 10 de julio de 2026.

---

## ACTUALIZACION CODEX — 10 de julio de 2026

**Estado nuevo:** Diagnostico + primer parche critico aplicado.  
**Decision tecnica tomada:** avanzar sin esperar aprobacion manual porque Kevin delego decisiones y dio acceso completo.

### Cambios aplicados

1. **Lead → Job idempotente**
   - `/api/leads/<lead_id>/accept-quote` ya no crea cliente/job/invoices nuevos si el lead ya fue convertido.
   - Si existe cliente por email o telefono, se reutiliza.
   - Si existe job vinculado al lead, se reutiliza.
   - Si ya existen invoices del quote/job, se devuelven sin duplicar.
   - Las otras cotizaciones pendientes del mismo lead pasan a `Superada` cuando una se acepta.

2. **Ruta legacy de aceptacion**
   - `/api/leads/<lead_id>/accept` usa la misma transicion segura.

3. **Clientes**
   - `/clients/<client_id>` ya funciona con JSON local; antes intentaba Notion y caia 404/401.
   - `/api/clients/<client_id>/update` guarda cambios en JSON local.
   - Las filas de `/clients` ahora navegan al detalle del cliente.

4. **Configuracion**
   - `/api/config/paquetes` usa `data/packages.json`.
   - `/api/config/cuentas`, `/api/config/reglas`, `/api/config/fuentes`, `/api/config/datos` usan `settings.json` bajo `config`.
   - La pantalla `/configuracion` ya no depende de Notion para cargar sus pestanas principales.

5. **Formulario publico legacy**
   - `/api/leads/nuevo` crea leads en JSON local y dispara workflow.
   - Antes intentaba crear el lead en Notion.

6. **Partners / Equipo**
   - `/partners` usa `team.json` local.
   - Ya no intenta Notion ni tarda por token invalido.

7. **Dependencias**
   - `requirements.txt` ahora incluye `reportlab>=4.0`, necesario para importar la app y generar PDFs.

### Pruebas ejecutadas

- `python -m py_compile app.py`
- Smoke test de pantallas:
  - `/`, `/dashboard`, `/leads`, `/leads/lead-2`, `/clients`, `/clients/client-1`
  - `/jobs`, `/jobs/boda-1`, `/calendar`, `/payments`, `/settings`
  - `/captacion`, `/contacto`, `/configuracion`, `/workflow-editor`
  - `/pagos-equipo`, `/equipo`, `/partners`, `/portal/client-1`
- Smoke test de APIs:
  - `/api/config/paquetes`
  - `/api/config/cuentas`
  - `/api/config/reglas`
  - `/api/config/fuentes`
  - `/api/config/datos`
  - `/api/workflow/templates`
  - `/api/workflow/instances`
  - `/api/search?q=ana`
  - `/api/mail/recent`
- Prueba temporal Lead → Job:
  - Lead ya convertido: aceptar quote no cambia conteos.
  - Lead nuevo + quote + doble accept: segunda aceptacion no cambia conteos y devuelve el mismo job/cliente/invoices.

### Resultado de pruebas

- Pantallas principales: **19/19 responden 200**
- APIs principales probadas: **9/9 responden 200**
- Idempotencia Lead → Job: **PASS**
- Datos reales modificados durante pruebas automatizadas: **NO**. Las pruebas de escritura usaron `CRM_DATA_DIR` temporal.

### Lo que sigue pendiente

- Limpiar duplicados historicos existentes en `clients.json` y `jobs.json` con una migracion controlada.
- Unificar rutas antiguas Notion que aun quedan para jobs avanzados, pagos avanzados y creacion de cotizaciones desde job.
- Convertir payments en `invoice/installment/transaction` real segun V5.2.
- Agregar tests permanentes al repo.
- Pasar de JSON a SQLite/Postgres con backup y modo sombra.

---

## A. Resumen sencillo

El CRM tiene muchas pantallas que se ven bien, pero la lógica de fondo tiene
problemas serios:

1. **Duplicación de clientes:** "Ana Ramirez" existe 2 veces, "KEVIN LEMUS"
   existe 2 veces con nombres ligeramente distintos.
2. **Duplicación de jobs:** El mismo lead generó varios jobs en distintos
   momentos sin consolidar.
3. **El "convertir Lead a Job" crea un cliente NUEVO cada vez** aunque el
   lead ya tenga uno asociado.
4. **Las cotizaciones no están bien ligadas:** un mismo lead puede tener
   varias cotizaciones con estados mezclados (Aceptada + Enviada).
5. **Notion está casi sin uso real.** El archivo `notion_sync.py` quedó
   como dependencia pero todos los endpoints hoy leen/escriben en JSON
   local. Eso es coherente con la decisión "olvidar Notion por ahora" pero
   `app.py` sigue intentando usarlo en algunos lugares.
6. **El sistema financiero no tiene plan de pagos real.** Las "cuotas"
   se generan como invoices duplicadas sin un schedule maestro.

---

## B. Evidencia encontrada

### B.1 Stack y entorno (CONFIRMADO EN CÓDIGO)

- **Lenguaje:** Python 3.11
- **Framework:** Flask 3.x
- **Servidor WSGI:** Werkzeug (desarrollo) / Gunicorn (Procfile)
- **Base de datos:** Archivos JSON en `data/*.json` (NO Notion, NO SQL real).
- **Almacenamiento:** Solo filesystem local.
- **Hosting actual:** Ninguno definido (Procfile sugiere Heroku/Render).
- **Archivo principal:** `app.py` (3,082 líneas, 60+ endpoints).
- **Módulos auxiliares:** `src/storage.py`, `src/workflow/engine.py`,
  `src/workflow/models.py`, `src/workflow/templates.py`,
  `src/mail_tracker.py`, `src/pdf_generator.py`.
- **Templates:** 24 archivos HTML en `templates/`.

### B.2 Datos actuales (CONFIRMADO EN JSON)

| Tabla/archivo | Registros | Notas |
|---|---|---|
| `leads.json` | 7 | 2 marcados "Convertido" pero sin job_id |
| `jobs.json` | 9 | 3 jobs duplicados para Ana Ramirez, 2 para Sofia Castillo |
| `clients.json` | 13 | "Ana Ramirez" duplicada, "KEVIN LEMUS" y "Kevin Daniel Lemus Noriega" son la misma persona |
| `quotes.json` | 4 | Solo 1 aceptada, 3 enviadas para el mismo lead |
| `payments.json` | 6 | 2 facturas con el mismo invoice_id semántico "INV-001", "INV-002" |
| `contracts.json` | 0 | Vacío |
| `email_templates.json` | 12 | Plantillas reales, pero los workflows no las usan todas |
| `packages.json` | 11 | Paquetes reales de Norkevin (precios Studio Ninja) |
| `workflow_instances.json` | Activo | Workflow engine persistido |
| `workflow_history.json` | Activo | Historial de ejecuciones |
| `mail_log.json` | Activo | Mail tracking manual funcionando |
| `calendar.json` | 4 eventos | Calendario básico |
| `team.json` | 3 | Equipo de Norkevin |
| `tenants.json` | 2 | Norkevin + Astral |

### B.3 Endpoints (CONFIRMADO EN app.py)

60+ rutas, mezclando vistas (HTML) y APIs (JSON).
Categorías:
- Vistas: `/`, `/dashboard`, `/leads`, `/leads/<id>`, `/clients`, `/jobs`,
  `/jobs/<id>`, `/calendar`, `/payments`, `/pagos-equipo`, `/equipo`,
  `/settings`, `/workflow-editor`, `/captacion`, `/portal/<id>`, etc.
- APIs: `/api/leads/<id>/trigger-step`, `/api/leads/<id>/quote`,
  `/api/leads/<id>/accept-quote`, `/api/leads/<id>/send-email`,
  `/api/mail/<id>/opened`, `/api/jobs/<id>/trigger-step`,
  `/api/payments/<id>/pay`, `/api/clients/new`, `/api/clients/<id>/update`,
  `/api/search`, etc.

---

## C. Archivos revisados

| Archivo | Líneas | Estado |
|---|---|---|
| `app.py` | 3082 | Monolítico. Mezcla vistas, lógica, multi-tenant y workflows. |
| `src/storage.py` | 77 | Wrapper JSON básico. CRUD manual. |
| `src/workflow/engine.py` | 392 | Motor funcional con persistencia JSON. |
| `src/workflow/models.py` | 196 | Modelos con dataclasses. Buena estructura. |
| `src/workflow/templates.py` | 209 | Templates LEAD + PRODUCTION. |
| `src/mail_tracker.py` | 4,048 bytes | Tracker funcional. |
| `src/pdf_generator.py` | 15,702 bytes | ReportLab puro, sin dependencias externas. |
| `notion_sync.py` | 19,408 bytes | **NO SE USA.** Los endpoints JSON lo importan pero solo algunos llaman a funciones reales; otros devuelven errores 401. |
| `llenar_crm.py` | 17,384 bytes | Script de seed. Generó los datos iniciales. |

---

## D. Archivos modificados en esta auditoría

**NINGUNO.** Esta es solo lectura.

---

## E. Tablas o datos afectados

**Análisis estático.** Sin tocar.

### E.1 Duplicaciones encontradas (MUESTRA)

```text
clients.json:
  client-1   | Maria Lopez | maria.lopez@gmail.com
  client-2   | Ana Ramirez | ana.ramirez@yahoo.com
  client-0165833f | Ana Ramirez | ana.ramirez@yahoo.com   ← DUPLICADO
  client-9d625381 | KEVIN LEMUS | kevinnoriega01@gmail.com
  client-33fcc706 | Kevin Daniel Lemus Noriega | norkevinfoto@gmail.com  ← MISMO

jobs.json:
  boda-1   | Maria Lopez & Carlos Mendez | lead-1
  boda-2   | Ana Ramirez & Luis Garcia | lead-2
  boda-3d559b03 | Boda Ana Ramirez | lead-2   ← DUPLICADO
  boda-009a8781 | (anonimo) | lead-2         ← TERCER JOB DUPLICADO
  boda-71c243ed | Boda Sofia Castillo | lead-3f0bf51a
  boda-9ac2b517 | Boda Sofia Castillo | lead-3f0bf51a   ← DUPLICADO
```

### E.2 Rompimiento Lead → Job

`api_lead_accept_quote` (app.py:829):
1. Busca cotizaciones no aceptadas.
2. **Crea un cliente NUEVO siempre** (uuid nuevo) — incluso si ya existe uno
   con el mismo nombre/correo. Esto explica los duplicados.
3. Crea un job NUEVO siempre.
4. Marca el lead como "Convertido" y le agrega `lead_id_job`.
5. Dispara el Production Workflow.
6. **Problemas críticos:**
   - No hay verificación de idempotencia (doble click = 2 clientes + 2 jobs).
   - No busca cliente existente por email/teléfono.
   - El lead conserva `status=Convertido` pero el nuevo cliente no queda
     relacionado al cliente viejo.
   - Las cotizaciones viejas siguen "Enviada" después de la conversión.

### E.3 Duplicación de money (CONFIRMADO)

`payments.json` muestra `pay-1`, `pay-2`, `pay-3`, `pay-4` con `invoice_id`
`INV-001` ... `INV-004` (auto-incremental manual). `pay-24b2c426` ya usa
`INV-95C477` (uuid). Hay dos formas de generar IDs conviviendo.

No hay **payment_schedule_template** ni **payment_installments**. Cada
"cuota" es simplemente una invoice adicional con un campo string
`cuota: "1/4"`. Eso significa que el plan de pagos vive en la cabeza del
usuario, no en el sistema.

### E.4 Workflows (CONFIRMADO PARCIALMENTE)

`workflow_engine.py` funciona y persiste en JSON. Tiene:
- LEAD_WORKFLOW (4 steps, todos manuales).
- PRODUCTION_WORKFLOW (7 steps, la mayoría automáticos con offsets).
- `start_workflow`, `execute_step`, `mark_opened`, etc.

**Problemas:**
- El engine tiene un anti-loop (no se autodispara desde production) pero
  depende de offset_minutes hardcodeados en cada step.
- Las fechas reales se calculan con `_dt.now()` en el momento de la
  conversión (no hay "scheduled_for" persistente).
- No hay scheduler real: ningún cron job ejecuta `get_due_steps()`.

### E.5 Email (CONFIRMADO PARCIAL)

`mail_tracker.py` registra emails con estado manual. El botón "Simular
apertura" marca como abierto. **No hay servicio real de email todavía.**

### E.6 PDF / Contratos (CONFIRMADO PARCIAL)

`pdf_generator.py` genera PDFs de cotización/contrato/factura con
ReportLab puro. El endpoint `/api/contracts/<id>/sign` acepta firma en
base64. **Falta:** plantillas de contrato editables, generación masiva
de contrato desde plantilla + snapshot.

### E.7 Portal del cliente (CONFIRMADO FUNCIONAL)

`/portal/<client_id>` muestra el resumen. La firma digital funciona
(canvas → base64 → backend). **Falta:** galería, downloads, multi-idioma.

### E.8 Calendario (CONFIRMADO PARCIAL)

Calendar básico con 4 eventos hardcoded-ish. No tiene recurrencia,
no muestra cuotas/pagos como eventos, no sincroniza con Google.

### E.9 Dashboard (CONFIRMADO PARCIAL)

KPIs, line chart SVG, pie chart SVG. "Eventos recientes" funciona. El dato
de `needs_attention` fue quitado por Kevin. **Falta:** comparación mes-a-mes,
filtros por empresa, tasa de conversión real.

### E.10 Notion (OBSERVADO)

`notion_sync.py` está presente pero NO se ejecuta en runtime. Los
endpoints que aún lo llaman fallan con 401 (token falso en .env). Esa
dependencia está desactivada y los datos viven 100% en JSON. Decisión
correcta por ahora.

---

## F. Pruebas ejecutadas

**NO se ejecutaron pruebas automatizadas.** No hay suite de tests.

**Verificación manual de datos:**
- `python -c "import json; ..."` sobre cada JSON para confirmar duplicados.
- `grep -n "@app.route" app.py` para listar endpoints.
- `wc -l app.py` para medir tamaño.

---

## G. Resultado de cada prueba

| Verificación | Resultado |
|---|---|
| Estructura del proyecto identificada | OK |
| Stack confirmado | OK (Python 3.11 + Flask + JSON) |
| Endpoints contados | OK (60+) |
| Tablas/datos contados | OK (15 archivos JSON) |
| Duplicación de clientes detectada | **PROBLEMA** (2 casos) |
| Duplicación de jobs detectada | **PROBLEMA** (3 casos) |
| Rompimiento Lead→Job identificado | **PROBLEMA** (crea duplicados) |
| Falta de idempotencia | **PROBLEMA** |
| Falta de scheduler de workflows | **PROBLEMA** (no hay cron) |
| Email solo simulado | **LIMITACIÓN CONOCIDA** |
| PDF funciona | OK |
| Portal del cliente funciona | OK |
| Notion desactivado correctamente | OK |

---

## H. Riesgos

1. **CRÍTICO:** Si Kevin corre 2 veces "Aceptar cotización" se crean
   2 clientes + 2 jobs + 2 sets de invoices.
2. **ALTO:** El "first payment received → job accepted" NO está
   implementado. Solo existe vía manual "accept-quote".
3. **ALTO:** No hay scheduler real para los workflow steps automáticos.
   Los delays (3h, 7d, 30d, 90d) son的理论icos.
4. **MEDIO:** Money calculations usan float (ej. `cuota_monto: 9500.0`).
   No es Decimal ni centavos.
5. **MEDIO:** Notion sync sigue en código pero inactivo. Si alguien
   reactiva el token, podría romper cosas.
6. **MEDIO:** No hay validación de `archivo` (soft delete). Se borra duro.
7. **BAJO:** No hay CSRF, no hay rate limiting.

---

## I. Rollback disponible

**No se modificó nada.** Esta auditoría es read-only.

Si en pasos siguientes se requiere migración, el rollback es:
- Restaurar `data/*.json` desde backup Git (último commit).
- Restaurar `app.py` desde backup Git.

---

## J. Decisión que Kevin debe aprobar

Antes de pasar al **Paso 2 (Modelo de Datos)**, necesito que Kevin apruebe:

1. **Arquitectura recomendada:** entidad central `project` que cambia
   de `status=lead` a `status=job` (NO dos tablas separadas).
   - ✅ Reduce duplicación.
   - ⚠️ Requiere migración de los 7 leads y 9 jobs actuales.

2. **Persistencia:** seguir con JSON hasta que el modelo esté estable,
   luego migrar a SQLite o Postgres.
   - ✅ Bajo riesgo, fácil rollback.
   - ⚠️ No escala más allá de ~1000 proyectos.

3. **Workflow scheduler:** implementar un cron job diario que ejecute
   `get_due_steps()` y mande emails reales (SendGrid/Mailgun).
   - ✅ Activa los delays reales.
   - ⚠️ Costo mensual de email service.

4. **Dinero:** migrar a `Decimal` o centavos enteros (ej. `amount_cents: 950000`).
   - ✅ Evita errores de redondeo.
   - ⚠️ Migración de campos existentes.

5. **Email service:** SendGrid/Mailgun/Resend para emails reales.
   - ✅ Real tracking (open/click).
   - ⚠️ Costo mensual.

---

## 📋 Recomendaciones inmediatas

### Conservar (no tocar)
- `data/workflows/*` (motor funciona)
- `data/packages.json` (precios Studio Ninja)
- `data/tenants.json` (multi-marca)
- `data/team.json` (equipo)
- `src/pdf_generator.py` (ReportLab funciona)
- `src/mail_tracker.py` (funciona)
- `src/workflow/*` (modelos sólidos)

### Refactorizar (con migración)
- `app.py` → dividir en `routes/clients.py`, `routes/leads.py`,
  `routes/jobs.py`, `routes/quotes.py`, `routes/payments.py`,
  `routes/workflows.py`
- `data/*.json` → consolidar a SQLite
- `notion_sync.py` → archivar (no eliminar) o borrar

### Eliminar
- `notion_sync.py` (no se usa)
- `llenar_crm.py` (ya corrió, datos ya cargados)

### Crear
- `tests/` con pytest
- `migrations/` con Alembic
- `docs/` con ADRs (Architecture Decision Records)
- `.github/workflows/` con CI

---

## ✅ Qué se hizo

1. Revisé 3082 líneas de `app.py`
2. Mapeé 60+ endpoints
3. Identifiqué 15 archivos JSON como "base de datos"
4. Confirmé duplicaciones reales (clientes + jobs)
5. Audité la lógica de conversión Lead→Job (línea 829 de app.py)
6. Confirmé que Notion está desactivado pero presente
7. Verifiqué stack: Python 3.11 + Flask + JSON
8. Listé 8 problemas críticos / altos / medios / bajos

## ⚠️ Qué falta

1. Tests automatizados
2. Scheduler real de workflows
3. Email service real
4. Idempotencia en todas las acciones críticas
5. Migración a SQLite/Postgres
6. Migración de `accept-quote` para no duplicar
7. Detección de duplicados al crear cliente
8. Snapshot de cotizaciones aceptadas
9. Payment Schedule Template + Installments
10. Decimal para dinero

## 🛠️ Qué se rompió

**NADA** (auditoría read-only).

## 🚦 Decisión que Kevin debe aprobar

¿Procedo al **Paso 2: Modelo de Datos** con las decisiones arquitectónicas sugeridas?

- ✅ Opción A: Entidad central `project` (lead+job en misma tabla)
- ❌ Opción B: Tablas separadas lead/job con FK

¿Procedo con **persistencia JSON hasta tener modelo estable** o migro a **SQLite desde ya**?

- ✅ Opción A: JSON primero, SQLite después
- ❌ Opción B: SQLite desde ya

¿Avanzo al **Paso 2**?

**Decime Kevin.** 💪
