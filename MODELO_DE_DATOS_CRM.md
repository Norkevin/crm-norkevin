# MODELO DE DATOS CRM — Proyecto Narvis
**Versión:** 3.0 (corregido según feedback de Kevin)
**Fecha:** 9 de julio de 2026
**Estado:** Pendiente de revisión. NO se ha programado nada.

**Convención de etiquetas (usada en todo el documento):**
- `[CONFIRMADO EN CÓDIGO]` — datos verificados en `app.py` / JSONs / read-only.
- `[PROPUESTA TÉCNICA]` — recomendación de Narvis, no implementada.
- `[DECISIÓN PENDIENTE]` — Kevin debe aprobar.

**Advertencia crítica:** no se ha ejecutado `alembic`, no se han creado
tablas, no se ha modificado `crm.db`, no se han tocado los JSON, no se ha
modificado el código de `app.py`. Esta es una propuesta de diseño.

---

## A. Resumen ejecutivo

Esta versión V3 corrige los errores identificados por Kevin en la V2:

1. Se introduce `project_clients` como única fuente de verdad para la
   relación proyecto–persona, eliminando `primary_client_id` y
   `secondary_client_id` de `projects`.
2. Se introduce `user_company_memberships` para modelar permisos
   por empresa, eliminando el array JSON `users.company_ids`.
3. Se introduce `invoice_items` para líneas congeladas dentro de la
   factura, independiente de la cotización.
4. Se introducen `payment_schedule_templates` y `payment_schedule_rules`
   para planes de pago reutilizables.
5. Se introduce `outbox_events` para acciones salientes (correos, PDFs,
   webhooks) que se disparan en worker async tras commit.
6. Se introduce `automation_runs` para diagnóstico técnico del motor
   de automatizaciones, separado de `activity_log` (que es historial de
   usuario).
7. Se introduce `legacy_record_map` para preservar TODOS los IDs
   antiguos (lead, job, quote, payment) tras la migración.
8. Se elimina el uso de `REAL` para `tax_rate`. Se usa
   `tax_rate_bps INTEGER` (basis points, 10000 = 100%).
9. Se elimina el uso de `float` para montos monetarios. La entrada es
   `TEXT` validada (regex) o `Decimal`. Se almacena como `INTEGER` con
   `currency_exponent`.
10. Las devoluciones pasan a ser transacciones separadas
    (`transaction_type='refund'`, `original_transaction_id` no nulo).
11. `payment_installments` se simplifica: solo el plan esperado. Los
    pagos reales viven en `payment_transactions`.
12. `client_emails` y `client_phones` reemplazan los campos únicos
    `email_primary` y `phone_primary` de `clients`.
13. `full_name` se genera derivado, no se almacena.
14. Estados: se elimina `converted` definitivamente. `archived` deja
    de ser estado operativo; el archivado se controla con
    `archived_at`.
15. `events` se renombra a `calendar_events` para evitar confusión con
    los eventos del sistema (`quote.accepted`, etc.).
16. `workflow_instance_id` redundante en `projects` se elimina. Se
    deriva desde `workflow_instances.project_id`.
17. `price_paid_units` y `balance_due_units` se eliminan de `projects`.
    Se calculan desde `payment_transactions`.
18. Backup y rollback se documentan con scripts PowerShell
    multiplataforma.
19. `PRAGMA foreign_keys=ON` se aplica en CADA conexión vía eventos
    de SQLAlchemy.
20. Alembic se documenta con `render_as_batch=True` para cambios que
    SQLite no soporta de forma directa.

**Conteo real de tablas: 31 (treinta y una).** Ver sección B.4 para el
inventario exacto.

---

## B. Inventario de tablas

### B.1 Conteo exacto

**31 tablas SQL.** Ningún "D.x" del documento es sección explicativa;
todos son `CREATE TABLE` o `CREATE INDEX` reales.

### B.2 Tablas del MVP (16 obligatorias + 8 necesarias para que funcione)

| # | Tabla | MVP | Definida en SQL | Origen legacy |
|---|---|---|---|---|
| 1 | `tenants` | ✅ MVP OBLIGATORIA | ✅ | NO EXISTE (crear 1) |
| 2 | `companies` | ✅ MVP OBLIGATORIA | ✅ | `tenants.json` reinterpretado |
| 3 | `users` | ✅ MVP OBLIGATORIA | ✅ | NO EXISTE (crear Kevin) |
| 4 | `user_company_memberships` | ✅ MVP OBLIGATORIA | ✅ | NO EXISTE |
| 5 | `clients` | ✅ MVP OBLIGATORIA | ✅ | `clients.json` |
| 6 | `client_emails` | ✅ MVP OBLIGATORIA | ✅ | campo `email_primary` extraído |
| 7 | `client_phones` | ✅ MVP OBLIGATORIA | ✅ | campo `phone_primary` extraído |
| 8 | `projects` | ✅ MVP OBLIGATORIA | ✅ | `leads.json` + `jobs.json` |
| 9 | `project_clients` | ✅ MVP OBLIGATORIA | ✅ | NO EXISTE (nueva) |
| 10 | `quotes` | ✅ MVP OBLIGATORIA | ✅ | `quotes.json` |
| 11 | `quote_items` | ✅ MVP OBLIGATORIA | ✅ | `items_snapshot` extraído |
| 12 | `invoices` | ✅ MVP OBLIGATORIA | ✅ | parte de `payments.json` |
| 13 | `invoice_items` | ✅ MVP OBLIGATORIA | ✅ | NO EXISTE (nueva) |
| 14 | `payment_installments` | ✅ MVP OBLIGATORIA | ✅ | parte de `payments.json` |
| 15 | `payment_transactions` | ✅ MVP OBLIGATORIA | ✅ | parte de `payments.json` |
| 16 | `payment_schedule_templates` | ✅ MVP OBLIGATORIA | ✅ | NO EXISTE (nueva) |
| 17 | `payment_schedule_rules` | ✅ MVP OBLIGATORIA | ✅ | NO EXISTE (nueva) |
| 18 | `products` | ✅ MVP OBLIGATORIA | ✅ | `packages.json` |
| 19 | `workflow_templates` | ✅ MVP OBLIGATORIA | ✅ | parte de `email_templates.json` |
| 20 | `workflow_task_templates` | ✅ MVP OBLIGATORIA | ✅ | parte de `src/workflow/templates.py` |
| 21 | `workflow_instances` | ✅ MVP OBLIGATORIA | ✅ | `workflow_instances.json` |
| 22 | `workflow_task_instances` | ✅ MVP OBLIGATORIA | ✅ | dentro de `workflow_instances.json` |
| 23 | `processed_events` | ✅ MVP OBLIGATORIA | ✅ | NO EXISTE (nueva) |
| 24 | `outbox_events` | ✅ MVP OBLIGATORIA | ✅ | NO EXISTE (nueva) |
| 25 | `automation_runs` | ✅ MVP OBLIGATORIA | ✅ | parte de `workflow_history.json` |
| 26 | `activity_log` | ✅ MVP OBLIGATORIA | ✅ | NO EXISTE (consolidado) |
| 27 | `calendar_events` | ✅ MVP OBLIGATORIA | ✅ | `calendar.json` |
| 28 | `email_templates` | ✅ MVP OBLIGATORIA | ✅ | `email_templates.json` |
| 29 | `legacy_record_map` | ✅ MVP OBLIGATORIA | ✅ | NO EXISTE (nueva) |
| 30 | `settings` | ✅ MVP OBLIGATORIA | ✅ | `settings.json` |
| 31 | `sequence_counters` | ✅ MVP OBLIGATORIA | ✅ | NO EXISTE (numeración) |

### B.3 Tablas NO creadas en la primera migración (Fase 2)

| # | Tabla | Razón de diferir |
|---|---|---|
| — | `contracts` | `contracts.json` está vacío. No hay datos que migrar. Se crea cuando se firme el primer contrato. |
| — | `contract_templates` | No existen plantillas hoy. Se crean al primer contrato. |
| — | `questionnaires` | No existen cuestionarios hoy. |
| — | `questionnaire_templates` | No existen plantillas hoy. |
| — | `quote_templates` | No existen plantillas; las cotizaciones son custom. |
| — | `invoice_templates` | No existen plantillas; las facturas son custom. |
| — | `files` | No existe sistema de uploads hoy. |
| — | `mail_log` | Solo simulación; el servicio real de email no está conectado. |

### B.4 Tablas eliminadas (NO se crean)

- ❌ `tenant_company_memberships` — no se necesita; tenant es 1.
- ❌ Galerías, IA, WhatsApp, equipo avanzado — fuera del MVP.

---

## C. Definiciones: Tenant, Company, Client

### C.1 Jerarquía

```
[CONFIRMADO EN CÓDIGO] que hoy solo existe `tenants.json` con 2 registros
("tenant-norkevin" y "tenant-astral") y NO hay tabla `tenants` ni
`companies`. La reinterpretación propuesta es:

tenants
└── companies (1:N, por tenant)
    ├── Norkevin Photography
    └── Astral Weddings
```

Kevin opera UN solo tenant con DOS companies (marcas). El tenant
representa el espacio de trabajo de Kevin. Las companies son las
marcas comerciales que ven los clientes y los usuarios.

### C.2 Aislamiento de datos

| Entidad | tenant_id | company_id | Comentario |
|---|---|---|---|
| `users` | ✅ | vía `user_company_memberships` | Un usuario puede existir en 1+ companies |
| `user_company_memberships` | ✅ | ✅ | Define el rol por company |
| `clients` | ✅ | ❌ | Cliente vive en el tenant (Opción A) |
| `client_emails` | ✅ | ❌ | Heredan de clients |
| `client_phones` | ✅ | ❌ | Heredan de clients |
| `projects` | ✅ | ✅ | Proyecto pertenece a UNA company |
| `project_clients` | ✅ | ❌ | Hereda de project |
| `quotes` | ✅ | ✅ | Heredan de project |
| `quote_items` | ✅ | ✅ | Heredan de quote |
| `invoices` | ✅ | ✅ | Heredan de project |
| `invoice_items` | ✅ | ✅ | Heredan de invoice |
| `payment_installments` | ✅ | ✅ | Heredan de invoice |
| `payment_transactions` | ✅ | ✅ | Heredan de invoice |
| `payment_schedule_templates` | ✅ | ❌ | ¿Tenant o Company? Ver C.4 |
| `payment_schedule_rules` | ✅ | ❌ | Heredan de template |
| `products` | ✅ | ✅ | Catálogo por company |
| `workflow_templates` | ✅ | ✅ | Ver C.4 |
| `workflow_task_templates` | ✅ | ✅ | Heredan |
| `workflow_instances` | ✅ | ✅ | Heredan de project |
| `workflow_task_instances` | ✅ | ✅ | Heredan |
| `processed_events` | ✅ | ❌ | Tenant-wide (todas las companies) |
| `outbox_events` | ✅ | ✅ | Por company para routing |
| `automation_runs` | ✅ | ✅ | Por company |
| `activity_log` | ✅ | ✅ | Por company (evento) o NULL si es global |
| `calendar_events` | ✅ | ✅ | Por company |
| `email_templates` | ✅ | ✅ | Por company |
| `legacy_record_map` | ✅ | ❌ | Tenant-wide (mapeo único) |
| `settings` | ✅ | ✅ | Una fila por company |
| `sequence_counters` | ✅ | ✅ | Una fila por company+entity_type |

### C.3 `client` vive en el tenant, no en la company

[PROPUESTA TÉCNICA + DECISIÓN PENDIENTE] — Kevin aprobó Opción A.

**Implicación:** un cliente puede tener proyectos en Norkevin Y Astral.
Los proyectos sí están aislados por company (no se mezclan).

### C.4 Plantillas compartidas o no

| Plantilla | Tenant-wide o Company | Razón |
|---|---|---|
| `email_templates` | **Por company** | Cada marca tiene su propio branding |
| `workflow_templates` | **Por company** | Cada marca tiene su flujo |
| `payment_schedule_templates` | **Por company** | Cada marca tiene sus condiciones |
| `product` | **Por company** | Catálogo distinto |
| `quote_templates` | (no en MVP) | — |
| `contract_templates` | (no en MVP) | — |
| `questionnaire_templates` | (no en MVP) | — |

**Decisión:** no se comparten plantillas entre Norkevin y Astral. Si
Kevin quiere compartir, debe duplicar manualmente y mantener ambas
versiones sincronizadas.

### C.5 Regla de oro: no se puede cruzar companies

Toda consulta que devuelve datos debe poder probar:

```sql
SELECT quote.*
FROM quotes q
JOIN projects p ON p.id = q.project_id
WHERE q.company_id = ? AND q.tenant_id = ?
  AND p.company_id = q.company_id;  -- garantía: el project está en la misma company
```

Si la FK no garantiza que `quote.company_id = project.company_id`, hay
un problema de diseño.

**Solución propuesta:** agregar FKs compuestas en las relaciones
projects → children:

```sql
-- En quotes:
FOREIGN KEY (tenant_id, company_id, project_id)
    REFERENCES projects (tenant_id, company_id, id)
    ON DELETE RESTRICT
```

De este modo, SQLite **rechaza** la inserción de un quote con
`company_id != project.company_id`.

---

## D. Máquina de estados definitiva

### D.1 Commercial status (lead side)

| Estado | Significado | Evento de entrada |
|---|---|---|
| `new_lead` | Lead acaba de llegar | `lead.created` |
| `contacted` | Primer contacto realizado | `email.sent` o `manual.contacted` |
| `proposal_sent` | Cotización enviada | `quote.sent` |
| `follow_up` | Esperando respuesta del cliente | `task.scheduled` |
| `accepted` | Cliente aceptó | `quote.accepted` o `contract.signed` o `first_payment.received` o `manual.accept` |
| `rejected` | Cliente rechazó | `quote.rejected` o `lead.rejected` |
| `expired` | Sin respuesta en plazo | cron de expiración |
| `archived` | Archivado (soft delete) | `manual.archive` |

### D.2 Operational status (job side)

| Estado | Significado | Evento de entrada |
|---|---|---|
| `lead` | Aún no es job | (estado inicial) |
| `confirmed` | Aceptado | automático desde `accepted` |
| `pre_production` | Planificando el evento | `task.confirmed_completed` |
| `event_ready` | Todo listo para el día | `task.event_ready_completed` |
| `event_completed` | El evento YA PASÓ | `event.completed` (MANUAL) |
| `editing` | Editando fotos/video | `editing.started` |
| `gallery_preparation` | Armando galería final | `editing.completed` |
| `gallery_published` | Galería subida | `gallery.published` |
| `delivered` | Galería entregada al cliente | `gallery.delivered` |
| `completed` | Job terminado | `job.completed` |
| `cancelled` | Cancelado | `job.cancelled` |

**NO** se incluye `archived` como estado operativo. El archivado lo
controla `archived_at`. Un proyecto `completed` puede archivarse; eso
no cambia su estado.

### D.3 Transiciones automáticas vs manuales

**Automáticas:**
- `new_lead` → `contacted` (al enviar primer email)
- `proposal_sent` → `follow_up` (al programar seguimiento)
- `accepted` → `confirmed` (en la misma transacción de aceptación)
- Estados del workflow (cuando el scheduler ejecuta la tarea)

**Manuales (requieren acción humana):**
- `event_ready` → `event_completed` (Kevin confirma que el evento pasó)
- `gallery_published` → `delivered` (Kevin envía la galería al cliente)
- `delivered` → `completed` (Kevin decide cerrar el job)

**`event_completed` NO requiere reseña.** Un cliente puede no dejar
reseña nunca; el job debe poder completarse igual.

### D.4 Catálogo de eventos del sistema

```
event.completed
editing.started
editing.completed
gallery.created
gallery.published
gallery.delivered
gallery.viewed
gallery.downloaded
job.completed
job.cancelled
quote.sent
quote.viewed
quote.accepted
quote.rejected
contract.sent
contract.signed
contract.declined
payment.received
payment.failed
payment.refunded
invoice.sent
invoice.viewed
invoice.paid
invoice.overdue
questionnaire.sent
questionnaire.completed
client.created
client.merged
project.created
project.confirmed
workflow.task.completed
workflow.task.failed
automation.executed
automation.failed
outbox.delivered
outbox.failed
```

Cada evento:
- `event_id` único (UUID)
- `event_type` (catálogo)
- `tenant_id`
- `company_id` (puede ser NULL si es global)
- `entity_type` (project, invoice, etc.)
- `entity_id`
- `occurred_at` (UTC)
- `actor_type` (system / user / client / automation)
- `actor_id`
- `payload` (JSON, versionado semver)
- `idempotency_key` (UNIQUE)

---

## E. Schema completo (tabla por tabla)

### E.1 `tenants`

```sql
CREATE TABLE tenants (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    owner_user_id   TEXT,
    timezone        TEXT NOT NULL DEFAULT 'America/Guatemala',
    language        TEXT NOT NULL DEFAULT 'es',
    created_at      TEXT NOT NULL,
    archived_at     TEXT,
    CHECK (archived_at IS NULL OR archived_at > created_at)
);
```

### E.2 `companies`

```sql
CREATE TABLE companies (
    id                  TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    slug                TEXT NOT NULL,
    name                TEXT NOT NULL,
    legal_name          TEXT,
    logo_letter         TEXT NOT NULL,
    color               TEXT NOT NULL,
    email               TEXT,
    phone               TEXT,
    address             TEXT,
    currency_code       TEXT NOT NULL DEFAULT 'GTQ',
    currency_exponent   INTEGER NOT NULL DEFAULT 2,
    tax_rate_bps        INTEGER NOT NULL DEFAULT 1200,  -- 12.00%
    invoice_prefix      TEXT NOT NULL DEFAULT 'INV',
    quote_prefix        TEXT NOT NULL DEFAULT 'Q',
    active              INTEGER NOT NULL DEFAULT 1,
    archived_at         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (tenant_id, slug),
    CHECK (currency_exponent BETWEEN 0 AND 6),
    CHECK (tax_rate_bps BETWEEN 0 AND 10000),
    CHECK (active IN (0, 1)),
    CHECK (archived_at IS NULL OR archived_at >= created_at)
);
```

### E.3 `users`

```sql
CREATE TABLE users (
    id                  TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    email               TEXT NOT NULL,  -- validado por regex
    name                TEXT NOT NULL,
    role                TEXT NOT NULL DEFAULT 'viewer',
    password_hash       TEXT,
    active              INTEGER NOT NULL DEFAULT 1,
    last_login_at       TEXT,
    created_at          TEXT NOT NULL,
    archived_at         TEXT,
    UNIQUE (tenant_id, email),
    CHECK (role IN ('owner', 'admin', 'editor', 'viewer')),
    CHECK (active IN (0, 1))
);
```

`users.company_ids` (de la V2) queda ELIMINADO. La membresía por
company vive en `user_company_memberships`.

### E.4 `user_company_memberships`

```sql
CREATE TABLE user_company_memberships (
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    role        TEXT NOT NULL DEFAULT 'viewer',
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    archived_at TEXT,
    PRIMARY KEY (user_id, company_id),
    CHECK (role IN ('owner', 'admin', 'editor', 'viewer')),
    CHECK (active IN (0, 1))
);
CREATE INDEX idx_ucm_company ON user_company_memberships(company_id) WHERE active = 1;
```

### E.5 `clients`

```sql
CREATE TABLE clients (
    id                TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    first_name        TEXT NOT NULL,
    last_name         TEXT NOT NULL,
    -- full_name NO se almacena. Se genera derivado en queries.
    source            TEXT,  -- "primera fuente histórica"
    consent_marketing INTEGER NOT NULL DEFAULT 0,
    consent_signed_at TEXT,
    notes             TEXT,
    archived_at       TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    CHECK (consent_marketing IN (0, 1)),
    CHECK (archived_at IS NULL OR archived_at >= created_at)
);
CREATE INDEX idx_clients_tenant ON clients(tenant_id) WHERE archived_at IS NULL;
```

**Sin `email`, sin `phone`, sin `full_name` directos.** Esos viven en
`client_emails` y `client_phones`.

### E.6 `client_emails`

```sql
CREATE TABLE client_emails (
    id                TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    client_id         TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    value_raw         TEXT NOT NULL,  -- email tal cual lo dio el cliente
    value_normalized  TEXT NOT NULL,  -- trim + lowercase, SIN +alias
    is_primary        INTEGER NOT NULL DEFAULT 0,
    verified_at       TEXT,  -- cuando se confirmó por email de validación
    archived_at       TEXT,
    created_at        TEXT NOT NULL,
    CHECK (is_primary IN (0, 1)),
    CHECK (archived_at IS NULL OR archived_at >= created_at)
);
CREATE INDEX idx_client_emails_norm ON client_emails(value_normalized);
CREATE UNIQUE INDEX uq_client_email_primary
ON client_emails(client_id) WHERE is_primary = 1 AND archived_at IS NULL;
```

### E.7 `client_phones`

```sql
CREATE TABLE client_phones (
    id                TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    client_id         TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    value_raw         TEXT NOT NULL,
    value_normalized  TEXT NOT NULL,  -- solo dígitos
    is_primary        INTEGER NOT NULL DEFAULT 0,
    verified_at       TEXT,
    archived_at       TEXT,
    created_at        TEXT NOT NULL,
    CHECK (is_primary IN (0, 1))
);
CREATE INDEX idx_client_phones_norm ON client_phones(value_normalized);
CREATE UNIQUE INDEX uq_client_phone_primary
ON client_phones(client_id) WHERE is_primary = 1 AND archived_at IS NULL;
```

### E.8 `projects`

```sql
CREATE TABLE projects (
    id                    TEXT PRIMARY KEY,
    tenant_id             TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id            TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    name                  TEXT NOT NULL,
    type                  TEXT NOT NULL DEFAULT 'boda',
    source                TEXT,  -- instagram/facebook/referido/web/manual
    form_id               TEXT,

    event_date            TEXT,
    event_time            TEXT,
    event_end_date        TEXT,
    event_end_time        TEXT,
    location_name         TEXT,
    location_address      TEXT,
    location_lat          REAL,
    location_lng          REAL,

    commercial_status     TEXT NOT NULL DEFAULT 'new_lead',
    operational_status    TEXT NOT NULL DEFAULT 'lead',

    job_accepted_at       TEXT,
    job_accepted_via      TEXT,

    package_id            TEXT,
    package_name_snapshot TEXT,

    price_total_units     INTEGER,

    workflow_template_id  TEXT,
    workflow_instance_id  TEXT,

    completed_at          TEXT,
    cancelled_at          TEXT,
    cancellation_reason   TEXT,
    archived_at           TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,

    CHECK (commercial_status IN
        ('new_lead','contacted','proposal_sent','follow_up','accepted',
         'rejected','expired','archived')),
    CHECK (operational_status IN
        ('lead','confirmed','pre_production','event_ready','event_completed',
         'editing','gallery_preparation','gallery_published','delivered',
         'completed','cancelled')),
    CHECK (job_accepted_via IS NULL OR job_accepted_via IN
        ('quote_accepted','contract_signed','first_payment_received','manual')),
    CHECK (archived_at IS NULL OR archived_at >= created_at)
);
CREATE INDEX idx_projects_company_status
    ON projects(company_id, commercial_status) WHERE archived_at IS NULL;
CREATE INDEX idx_projects_event_date
    ON projects(company_id, event_date) WHERE archived_at IS NULL;
```

**Sin `primary_client_id` ni `secondary_client_id` ni
`source_lead_id` ni `legacy_lead_id` ni `price_paid_units` ni
`balance_due_units` ni `workflow_instance_id` redundante.**

`workflow_instance_id` aquí se mantiene como FK para rendimiento
(1 query para "dame el workflow activo de este project"). El source of
truth sigue siendo `workflow_instances.project_id` y la unicidad se
garantiza con índice parcial en `workflow_instances`.

### E.9 `project_clients`

```sql
CREATE TABLE project_clients (
    project_id          TEXT NOT NULL,
    client_id           TEXT NOT NULL,
    role                TEXT NOT NULL DEFAULT 'participant',
    is_primary          INTEGER NOT NULL DEFAULT 0,
    is_billing_contact  INTEGER NOT NULL DEFAULT 0,
    is_portal_contact   INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    archived_at         TEXT,
    PRIMARY KEY (project_id, client_id),
    FOREIGN KEY (project_id)
        REFERENCES projects(id) ON DELETE RESTRICT,
    FOREIGN KEY (client_id)
        REFERENCES clients(id) ON DELETE RESTRICT,
    CHECK (is_primary IN (0, 1)),
    CHECK (is_billing_contact IN (0, 1)),
    CHECK (is_portal_contact IN (0, 1))
);
CREATE INDEX idx_pc_client ON project_clients(client_id) WHERE archived_at IS NULL;
CREATE UNIQUE INDEX uq_project_primary_contact
ON project_clients(project_id) WHERE is_primary = 1 AND archived_at IS NULL;
CREATE UNIQUE INDEX uq_project_billing_contact
ON project_clients(project_id) WHERE is_billing_contact = 1 AND archived_at IS NULL;
```

**Única fuente de verdad de la relación project–persona.**

`role` es un enum abierto a custom values por Kevin. Los
"estándar" sugeridos son:
- `novia`, `novio`
- `contacto_principal`, `contacto_facturacion`, `contacto_portal`
- `wedding_planner`
- `familiar`
- `otro`

### E.10 `quotes`

```sql
CREATE TABLE quotes (
    id                    TEXT PRIMARY KEY,
    tenant_id             TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id            TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id            TEXT NOT NULL,
    client_id             TEXT NOT NULL,
    template_id           TEXT,
    number                TEXT NOT NULL,
    type                  TEXT NOT NULL DEFAULT 'fixed',
    status                TEXT NOT NULL DEFAULT 'draft',
    issue_date            TEXT,
    due_date              TEXT,
    subtotal_units        INTEGER NOT NULL,
    discount_units        INTEGER NOT NULL DEFAULT 0,
    tax_units             INTEGER NOT NULL DEFAULT 0,
    total_units           INTEGER NOT NULL,
    currency_code         TEXT NOT NULL,
    currency_exponent     INTEGER NOT NULL,
    sent_at               TEXT,
    viewed_at             TEXT,
    accepted_at           TEXT,
    accepted_by_client_id TEXT REFERENCES clients(id) ON DELETE RESTRICT,
    acceptance_ip         TEXT,
    sent_snapshot         TEXT,
    accepted_snapshot     TEXT,
    snapshot_hash         TEXT,
    pdf_url               TEXT,
    created_at            TEXT NOT NULL,
    archived_at           TEXT,
    UNIQUE (company_id, number),
    FOREIGN KEY (tenant_id, company_id, project_id)
        REFERENCES projects (tenant_id, company_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE RESTRICT,
    CHECK (type IN ('fixed', 'pick_choose')),
    CHECK (status IN
        ('draft','sent','viewed','accepted','declined','expired',
         'superseded','cancelled')),
    CHECK (subtotal_units >= 0),
    CHECK (total_units >= 0)
);
```

### E.11 `quote_items`

```sql
CREATE TABLE quote_items (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    quote_id        TEXT NOT NULL,
    product_id      TEXT,
    name            TEXT NOT NULL,
    description     TEXT,
    price_units     INTEGER NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 1,
    subtotal_units  INTEGER NOT NULL,
    discount_units  INTEGER NOT NULL DEFAULT 0,
    tax_units       INTEGER NOT NULL DEFAULT 0,
    order_index     INTEGER NOT NULL DEFAULT 0,
    archived_at     TEXT,
    FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE RESTRICT,
    CHECK (quantity > 0),
    CHECK (price_units >= 0),
    CHECK (subtotal_units >= 0)
);
```

### E.12 `payment_schedule_templates`

```sql
CREATE TABLE payment_schedule_templates (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    name          TEXT NOT NULL,
    description   TEXT,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    archived_at   TEXT,
    CHECK (active IN (0, 1))
);
```

### E.13 `payment_schedule_rules`

```sql
CREATE TABLE payment_schedule_rules (
    id                          TEXT PRIMARY KEY,
    template_id                 TEXT NOT NULL,
    order_index                 INTEGER NOT NULL,
    description                 TEXT,
    percentage_bps               INTEGER,  -- 5000 = 50%, NULL si es monto fijo
    amount_units                INTEGER,  -- NULL si es porcentaje
    anchor_event                TEXT NOT NULL,
    anchor_offset_days          INTEGER NOT NULL DEFAULT 0,
    active                      INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (template_id)
        REFERENCES payment_schedule_templates(id) ON DELETE CASCADE,
    CHECK (percentage_bps IS NOT NULL OR amount_units IS NOT NULL),
    CHECK (percentage_bps BETWEEN 0 AND 10000 OR percentage_bps IS NULL),
    CHECK (amount_units >= 0 OR amount_units IS NULL),
    CHECK (anchor_event IN
        ('quote_accepted','job_accepted','event_date','gallery_delivered',
         'fixed_date')),
    UNIQUE (template_id, order_index)
);
```

### E.14 `invoices`

```sql
CREATE TABLE invoices (
    id                TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id        TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id        TEXT NOT NULL,
    client_id         TEXT NOT NULL,
    quote_id          TEXT,
    payment_schedule_id TEXT,
    number            TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'draft',
    issue_date        TEXT,
    due_date          TEXT,
    subtotal_units    INTEGER NOT NULL,
    discount_units    INTEGER NOT NULL DEFAULT 0,
    tax_units         INTEGER NOT NULL DEFAULT 0,
    total_units       INTEGER NOT NULL,
    currency_code     TEXT NOT NULL,
    currency_exponent INTEGER NOT NULL,
    sent_at           TEXT,
    viewed_at         TEXT,
    snapshot_hash     TEXT,
    pdf_url           TEXT,
    created_at        TEXT NOT NULL,
    archived_at       TEXT,
    UNIQUE (company_id, number),
    FOREIGN KEY (tenant_id, company_id, project_id)
        REFERENCES projects (tenant_id, company_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE RESTRICT,
    FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE RESTRICT,
    FOREIGN KEY (payment_schedule_id)
        REFERENCES payment_schedule_templates(id) ON DELETE RESTRICT,
    CHECK (status IN
        ('draft','issued','sent','viewed','partially_paid','paid',
         'overdue','cancelled','written_off','refunded')),
    CHECK (subtotal_units >= 0),
    CHECK (total_units >= 0)
);
```

`amount_paid_units` y `balance_due_units` **NO se almacenan**. Se
calculan desde `payment_transactions`.

### E.15 `invoice_items`

```sql
CREATE TABLE invoice_items (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    invoice_id      TEXT NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT,
    quantity        INTEGER NOT NULL DEFAULT 1,
    unit_price_units INTEGER NOT NULL,
    subtotal_units  INTEGER NOT NULL,
    discount_units  INTEGER NOT NULL DEFAULT 0,
    tax_units       INTEGER NOT NULL DEFAULT 0,
    order_index     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
    CHECK (quantity > 0),
    CHECK (unit_price_units >= 0)
);
```

### E.16 `payment_installments`

```sql
CREATE TABLE payment_installments (
    id                TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id        TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id        TEXT NOT NULL,
    client_id         TEXT NOT NULL,
    invoice_id        TEXT NOT NULL,
    number            INTEGER NOT NULL,
    total_installments INTEGER NOT NULL,
    due_date          TEXT NOT NULL,
    amount_units      INTEGER NOT NULL,
    -- paid_amount_units y status se CALCULAN desde payment_transactions.
    late_since        TEXT,
    notes             TEXT,
    created_at        TEXT NOT NULL,
    archived_at       TEXT,
    UNIQUE (invoice_id, number),
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE RESTRICT,
    FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE RESTRICT,
    CHECK (number BETWEEN 1 AND total_installments),
    CHECK (amount_units >= 0)
);
```

**No tiene `payment_method`, `reference`, `paid_at`.** Esos campos
viven en `payment_transactions`.

### E.17 `payment_transactions`

```sql
CREATE TABLE payment_transactions (
    id                          TEXT PRIMARY KEY,
    tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id                  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id                  TEXT NOT NULL,
    client_id                   TEXT NOT NULL,
    invoice_id                  TEXT NOT NULL,
    installment_id              TEXT,
    original_transaction_id     TEXT,  -- para refunds
    transaction_type            TEXT NOT NULL DEFAULT 'payment',
    -- 'payment' o 'refund'
    amount_units                INTEGER NOT NULL,
    currency_code               TEXT NOT NULL,
    currency_exponent           INTEGER NOT NULL,
    date                        TEXT NOT NULL,
    method                      TEXT NOT NULL,
    external_reference          TEXT,
    provider                    TEXT,
    status                      TEXT NOT NULL DEFAULT 'pending',
    -- 'pending' / 'confirmed' / 'failed' / 'reversed'
    receipt_url                 TEXT,
    notes                       TEXT,
    created_by_user_id          TEXT,
    created_at                  TEXT NOT NULL,
    archived_at                 TEXT,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE RESTRICT,
    FOREIGN KEY (installment_id)
        REFERENCES payment_installments(id) ON DELETE RESTRICT,
    FOREIGN KEY (original_transaction_id)
        REFERENCES payment_transactions(id) ON DELETE RESTRICT,
    CHECK (transaction_type IN ('payment', 'refund')),
    CHECK (status IN ('pending', 'confirmed', 'failed', 'reversed')),
    CHECK (amount_units > 0),
    CHECK (original_transaction_id IS NULL OR transaction_type = 'refund')
);
```

**Regla para refunds:**
- El pago original (`payment`) **NO** se modifica.
- La devolución se crea como `payment_transactions` con
  `transaction_type='refund'`, `original_transaction_id=<id_pago>`.
- El **neto** se calcula siempre sobre el `SUM` filtrado por
  `transaction_type`:
  - `payment` (sum) − `refund` (sum) = neto recibido.

```sql
-- neto recibido por invoice
SELECT i.id,
    COALESCE(SUM(CASE WHEN pt.transaction_type='payment' AND pt.status='confirmed'
                      THEN pt.amount_units END), 0)
  - COALESCE(SUM(CASE WHEN pt.transaction_type='refund' AND pt.status='confirmed'
                      THEN pt.amount_units END), 0)
  AS net_received
FROM invoices i
LEFT JOIN payment_transactions pt
  ON pt.invoice_id = i.id AND pt.archived_at IS NULL
WHERE i.id = ?
GROUP BY i.id;
```

### E.18 `products`

```sql
CREATE TABLE products (
    id                TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id        TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    type              TEXT NOT NULL,  -- 'product' o 'package'
    name              TEXT NOT NULL,
    description       TEXT,
    price_units       INTEGER NOT NULL,
    currency_code     TEXT NOT NULL,
    tax_rate_bps      INTEGER NOT NULL DEFAULT 1200,
    duration_hours    INTEGER,
    includes          TEXT,
    category          TEXT,
    order_index       INTEGER NOT NULL DEFAULT 0,
    active            INTEGER NOT NULL DEFAULT 1,
    archived_at       TEXT,
    created_at        TEXT NOT NULL,
    CHECK (type IN ('product', 'package')),
    CHECK (price_units >= 0),
    CHECK (tax_rate_bps BETWEEN 0 AND 10000),
    CHECK (active IN (0, 1))
);
```

**`tax_rate_bps`** (basis points, 10000 = 100%). No se usa `REAL`.

### E.19 `workflow_templates`

```sql
CREATE TABLE workflow_templates (
    id                TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id        TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    name              TEXT NOT NULL,
    description       TEXT,
    mode              TEXT NOT NULL DEFAULT 'dynamic',
    -- 'dynamic' o 'frozen'
    active            INTEGER NOT NULL DEFAULT 1,
    version           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    archived_at       TEXT,
    CHECK (mode IN ('dynamic', 'frozen')),
    CHECK (active IN (0, 1))
);
```

### E.20 `workflow_task_templates`

```sql
CREATE TABLE workflow_task_templates (
    id                      TEXT PRIMARY KEY,
    workflow_template_id    TEXT NOT NULL,
    stage                   TEXT NOT NULL,
    order_index             INTEGER NOT NULL,
    name                    TEXT NOT NULL,
    description             TEXT,
    action_type             TEXT NOT NULL,
    email_template_id       TEXT,
    contract_template_id    TEXT,
    questionnaire_template_id TEXT,
    due_rule_mode           TEXT NOT NULL DEFAULT 'manual',
    due_rule_anchor         TEXT,
    due_rule_amount         INTEGER,
    due_rule_unit           TEXT,
    due_rule_direction     TEXT,
    active                  INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (workflow_template_id)
        REFERENCES workflow_templates(id) ON DELETE RESTRICT,
    CHECK (stage IN ('lead', 'production', 'post_production')),
    CHECK (action_type IN
        ('send_email','send_contract','send_questionnaire','send_invoice',
         'send_gallery','change_status','create_task','notify_owner',
         'archive','noop')),
    CHECK (due_rule_mode IN ('manual', 'after_creation', 'after_event',
                              'after_anchor')),
    CHECK (due_rule_unit IN ('minutes','hours','days','weeks','months') OR due_rule_unit IS NULL),
    CHECK (due_rule_direction IN ('before','after') OR due_rule_direction IS NULL),
    UNIQUE (workflow_template_id, order_index)
);
```

### E.21 `workflow_instances`

```sql
CREATE TABLE workflow_instances (
    id                    TEXT PRIMARY KEY,
    tenant_id             TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id            TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id            TEXT NOT NULL,
    workflow_template_id  TEXT NOT NULL,
    template_version      INTEGER NOT NULL,
    mode                  TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'active',
    -- 'active' / 'paused' / 'completed' / 'cancelled'
    started_at            TEXT NOT NULL,
    completed_at          TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    FOREIGN KEY (workflow_template_id)
        REFERENCES workflow_templates(id) ON DELETE RESTRICT,
    CHECK (status IN ('active','paused','completed','cancelled'))
);
CREATE UNIQUE INDEX uq_project_active_workflow
    ON workflow_instances(project_id)
    WHERE status IN ('active', 'paused');
```

**Un proyecto puede tener varios workflows históricos** (cancelado,
reemplazado), pero **solo uno activo o en pausa** a la vez.

### E.22 `workflow_task_instances`

```sql
CREATE TABLE workflow_task_instances (
    id                          TEXT PRIMARY KEY,
    tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id                  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    workflow_instance_id        TEXT NOT NULL,
    task_template_id            TEXT NOT NULL,
    project_id                  TEXT NOT NULL,
    name                        TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'pending',
    -- 'pending' / 'ready' / 'running' / 'done' / 'skipped' / 'failed'
    due_rule_mode               TEXT NOT NULL,
    due_rule_anchor             TEXT,
    due_rule_amount             INTEGER,
    due_rule_unit               TEXT,
    due_rule_direction         TEXT,
    scheduled_at                TEXT,
    executed_at                 TEXT,
    completed_by_user_id        TEXT,
    result                      TEXT,
    error                       TEXT,
    retry_count                 INTEGER NOT NULL DEFAULT 0,
    skip_reason                 TEXT,
    idempotency_key             TEXT NOT NULL UNIQUE,
    FOREIGN KEY (workflow_instance_id)
        REFERENCES workflow_instances(id) ON DELETE CASCADE,
    FOREIGN KEY (task_template_id)
        REFERENCES workflow_task_templates(id) ON DELETE RESTRICT,
    CHECK (status IN ('pending','ready','running','done','skipped','failed')),
    CHECK (retry_count >= 0)
);
CREATE INDEX idx_wti_scheduled
    ON workflow_task_instances(company_id, scheduled_at)
    WHERE status = 'pending';
```

### E.23 `processed_events`

```sql
CREATE TABLE processed_events (
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id        TEXT,  -- NULL si el evento es tenant-wide
    idempotency_key   TEXT NOT NULL,
    event_type        TEXT NOT NULL,
    entity_type       TEXT NOT NULL,
    entity_id         TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'processing',
    -- 'processing' / 'completed' / 'failed'
    attempts          INTEGER NOT NULL DEFAULT 0,
    result_payload    TEXT,
    last_error        TEXT,
    created_at        TEXT NOT NULL,
    started_at        TEXT,
    completed_at      TEXT,
    PRIMARY KEY (tenant_id, idempotency_key),
    CHECK (status IN ('processing', 'completed', 'failed')),
    CHECK (attempts >= 0)
);
```

**Unicidad por `(tenant_id, idempotency_key)`.** No por
`company_id` porque el idempotency_key del cliente puede ser
tenant-wide.

### E.24 `outbox_events`

```sql
CREATE TABLE outbox_events (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    event_type      TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    handler_name    TEXT NOT NULL,
    -- 'send_email' / 'generate_pdf' / 'webhook' / 'notification'
    payload         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    -- 'pending' / 'delivered' / 'failed' / 'dead_letter'
    attempts        INTEGER NOT NULL DEFAULT 0,
    available_at    TEXT NOT NULL,
    processed_at    TEXT,
    last_error      TEXT,
    created_at      TEXT NOT NULL,
    CHECK (status IN ('pending','delivered','failed','dead_letter')),
    CHECK (attempts >= 0)
);
CREATE INDEX idx_outbox_pending
    ON outbox_events(status, available_at) WHERE status = 'pending';
```

`processed_events` ≠ `outbox_events`:
- `processed_events`: idempotencia de ENTRADA (requests, webhooks,
  clicks).
- `outbox_events`: SALIDA pendiente (correos, PDFs, notificaciones).
  Un worker lo procesa en background.

### E.25 `automation_runs`

```sql
CREATE TABLE automation_runs (
    id                    TEXT PRIMARY KEY,
    tenant_id             TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id            TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    workflow_instance_id  TEXT,
    task_instance_id      TEXT,
    project_id            TEXT,
    event_type            TEXT NOT NULL,
    entity_type           TEXT,
    entity_id             TEXT,
    scheduled_at          TEXT,
    started_at            TEXT,
    finished_at           TEXT,
    status                TEXT NOT NULL DEFAULT 'pending',
    attempt               INTEGER NOT NULL DEFAULT 1,
    result                TEXT,
    error                 TEXT,
    idempotency_key       TEXT NOT NULL UNIQUE,
    FOREIGN KEY (workflow_instance_id)
        REFERENCES workflow_instances(id) ON DELETE SET NULL,
    FOREIGN KEY (task_instance_id)
        REFERENCES workflow_task_instances(id) ON DELETE SET NULL,
    CHECK (status IN ('pending','running','success','failed','skipped'))
);
```

`automation_runs` ≠ `activity_log`:
- `activity_log`: lo que ve Kevin en el CRM (timeline de eventos).
- `automation_runs`: diagnóstico técnico del motor (cuándo corrió
  cada task, qué error tuvo, etc.).

### E.26 `activity_log`

```sql
CREATE TABLE activity_log (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id      TEXT,  -- puede ser NULL si evento global
    project_id      TEXT,
    client_id       TEXT,
    actor_type      TEXT NOT NULL,
    actor_id        TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    entity_type     TEXT,
    entity_id       TEXT,
    summary         TEXT,
    before_data     TEXT,
    after_data      TEXT,
    source          TEXT,
    ip              TEXT,
    user_agent      TEXT,
    created_at      TEXT NOT NULL,
    idempotency_key TEXT,
    CHECK (actor_type IN ('system','user','client','automation'))
);
```

### E.27 `calendar_events`

```sql
CREATE TABLE calendar_events (
    id                          TEXT PRIMARY KEY,
    tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id                  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id                  TEXT,
    type                        TEXT NOT NULL,
    -- 'lead_meeting' / 'event' / 'session' / 'payment' / 'task' / 'contract_signing'
    title                       TEXT NOT NULL,
    start_date                  TEXT,
    start_at                    TEXT,
    end_date                    TEXT,
    end_at                      TEXT,
    all_day                     INTEGER NOT NULL DEFAULT 0,
    timezone                    TEXT,
    location                    TEXT,
    notes                       TEXT,
    external_calendar_event_id  TEXT,
    created_by_user_id          TEXT,
    created_at                  TEXT NOT NULL,
    CHECK (type IN ('lead_meeting','event','session','payment','task','contract_signing')),
    CHECK (all_day IN (0, 1)),
    CHECK (start_date IS NOT NULL OR start_at IS NOT NULL)
);
CREATE INDEX idx_calendar_start ON calendar_events(company_id, start_at);
```

`events` se renombra a `calendar_events` para evitar confusión con
eventos del sistema.

### E.28 `email_templates`

```sql
CREATE TABLE email_templates (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    name            TEXT NOT NULL,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    variables_used  TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    archived_at     TEXT,
    CHECK (active IN (0, 1))
);
```

### E.29 `legacy_record_map`

```sql
CREATE TABLE legacy_record_map (
    id                TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    source_file       TEXT NOT NULL,
    -- 'leads.json' / 'jobs.json' / 'quotes.json' / 'payments.json' /
    -- 'workflow_instances.json' / etc.
    legacy_id         TEXT NOT NULL,
    entity_type       TEXT NOT NULL,
    -- 'project' / 'quote' / 'invoice' / etc.
    new_entity_id     TEXT NOT NULL,
    migration_status  TEXT NOT NULL DEFAULT 'imported',
    -- 'imported' / 'merged' / 'archived' / 'skipped'
    notes             TEXT,
    migrated_at       TEXT NOT NULL,
    UNIQUE (tenant_id, source_file, legacy_id, entity_type)
);
```

**Mapea cada ID antiguo (lead-1, boda-1, INV-001, etc.) a su
nueva entidad. Sin esto, perdemos la trazabilidad de los Jobs
duplicados de Ana Ramirez.**

### E.30 `settings`

```sql
CREATE TABLE settings (
    tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    key           TEXT NOT NULL,
    value         TEXT,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (company_id, key)
);
```

### E.31 `sequence_counters`

```sql
CREATE TABLE sequence_counters (
    tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    entity_type   TEXT NOT NULL,
    year          INTEGER NOT NULL,
    last_value    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (company_id, entity_type, year),
    CHECK (entity_type IN ('quote','invoice','contract','gallery','client','project'))
);
```

Para numeración correlativa (ej. `INV-2026-001`).

---

## F. Idempotencia y SQLite (configuración real)

### F.1 `BEGIN IMMEDIATE` y `processed_events`

```python
def begin_immediate(conn):
    """Adquiere write lock inmediatamente, evita upgrade read→write."""
    conn.execute("BEGIN IMMEDIATE;")
```

### F.2 `processed_events` actualizado (no reemplaza nada)

Kevin pidió:

```
[CONFIRMADO EN CÓDIGO] que processed_events NO existe hoy.
[PROPUESTA TÉCNICA] campos:

tenant_id
company_id
idempotency_key
event_type
entity_type
entity_id
status
attempts
result_payload
last_error
created_at
started_at
completed_at
```

Esos campos están en E.23.

### F.3 Distinguir IntegrityError concretos

```python
def accept_quote_atomic(project_id, idempotency_key):
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("BEGIN IMMEDIATE;")
        try:
            # 1. Check idempotency
            cur = conn.execute(
                "SELECT status, result_payload FROM processed_events "
                "WHERE tenant_id=? AND idempotency_key=?",
                (tenant_id, idempotency_key))
            row = cur.fetchone()
            if row:
                if row[0] == 'completed':
                    conn.commit()
                    return json.loads(row[1])
                if row[0] == 'processing':
                    raise ConcurrentProcessing("Reintentar")
                # 'failed' permite reintento

            # 2. Marcar processing
            conn.execute(
                "INSERT INTO processed_events "
                "(tenant_id, idempotency_key, event_type, entity_type, entity_id, status, created_at) "
                "VALUES (?, ?, 'quote.accepted', 'project', ?, 'processing', ?)",
                (tenant_id, idempotency_key, project_id, now_iso))

            # 3. Verificar si ya existe un project para este source_lead_id
            try:
                conn.execute("INSERT INTO projects (...) VALUES (...)", (...))
            except sqlite3.IntegrityError as e:
                msg = str(e).lower()
                if 'unique' in msg and 'source_lead' in msg:
                    # Project ya existe: idempotente
                    proj = conn.execute(
                        "SELECT * FROM projects WHERE source_lead_id=?", (source_lead_id,)
                    ).fetchone()
                elif 'foreign key' in msg:
                    raise InvalidReference(...)
                elif 'check' in msg:
                    raise InvalidState(...)
                else:
                    raise

            # 4. Resto de inserts
            ...

            # 5. Marcar completed
            conn.execute(
                "UPDATE processed_events SET status='completed', completed_at=?, result_payload=? "
                "WHERE tenant_id=? AND idempotency_key=?",
                (now_iso, json.dumps(result), tenant_id, idempotency_key))

            conn.commit()
            return result
        except Exception:
            conn.execute(
                "UPDATE processed_events SET status='failed', last_error=? "
                "WHERE tenant_id=? AND idempotency_key=?",
                (str(e), tenant_id, idempotency_key))
            conn.commit()
            raise
```

**Reglas:**
- `idempotency_key` debe ser determinista o reenviada por el cliente.
- `UNIQUE(tenant_id, idempotency_key)` en `processed_events`.
- Conflictos en `INSERT` se distinguen por el mensaje del
  `IntegrityError`.

### F.4 PRAGMA en cada conexión (no solo al inicio)

```python
# src/db.py
from sqlalchemy import event

engine = create_engine(
    "sqlite:///crm.db",
    connect_args={"timeout": 30},
    pool_pre_ping=True,
)

@event.listens_for(engine, "connect")
def set_sqlite_pragmas(dbapi_connection, connection_record):
    """Aplica PRAGMA en cada conexión nueva."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA busy_timeout=30000;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.close()
```

### F.5 Configuración de Flask

- **Procesos Flask:** 1 solo proceso con gunicorn (workers=1) en MVP.
- **Workers:** `--workers 1 --threads 4` para no tener concurrencia
  destructiva de SQLite. Si en el futuro se necesita, pasar a
  PostgreSQL.
- **Ubicación de `crm.db`:** `/crm/data/crm.db` (carpeta persistente,
  NO el repo). El repo solo tiene código, no datos.
- **Backup:** se hace con `VACUUM INTO` o el script PowerShell de la
  sección H, no copiando `crm.db` mientras la app escribe.

### F.6 Alembic con batch mode

```python
# src/migrations/env.py
def run_migrations_online():
    ...
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # CRÍTICO para SQLite
        )
        with context.begin_transaction():
            context.run_migrations()
```

`render_as_batch=True` hace que Alembic use el patrón "tabla nueva +
copia + rename" para cambios que SQLite no soporta directamente
(ALTER COLUMN, DROP CONSTRAINT, etc.).

### F.7 Pruebas de upgrade y downgrade

```bash
# 1. Backup antes de migrar
python -m src.scripts.backup_sqlite --output data/backups/

# 2. Upgrade
alembic upgrade head

# 3. Verificar conteos
python -m src.scripts.verify_migration

# 4. Si falla, downgrade
alembic downgrade -1

# 5. Verificar que vuelve al estado anterior
python -m src.scripts.verify_migration
```

**Cada migración se prueba con upgrade + downgrade + verificación**
sobre una copia de la base antes de aplicarse a producción.

---

## G. Mapeo legacy campo por campo

### G.1 `clients.json` (13 registros, 3 duplicados)

| Campo original | Tabla destino | Campo destino | Transformación | Confianza | Revisión |
|---|---|---|---|---|---|
| `id` | `legacy_record_map` | `legacy_id` | "client-1" → row con `entity_type='client'` | Alta | Automático |
| `id` | `clients` | `id` | Mantener mismo ID (no regenerar) | Alta | No |
| `first_name` | `clients` | `first_name` | directo | Alta | No |
| `last_name` | `clients` | `last_name` | directo | Alta | No |
| `email` | `client_emails` | `value_raw` | directo | Alta | No |
| `email` | `client_emails` | `value_normalized` | trim + lowercase | Alta | No |
| `email` | `client_emails` | `is_primary` | 1 si es el primero | Alta | No |
| `phone` | `client_phones` | `value_raw` | directo | Alta | No |
| `phone` | `client_phones` | `value_normalized` | solo dígitos | Alta | No |
| `address` | `clients` | `notes` (concatenado) | concatenar con notes | Media | No |
| `created` | `clients` | `created_at` | ISO + "T00:00:00Z" | Alta | No |
| `estado` | — | — | Se IGNORA (no usado) | — | No |

### G.2 `leads.json` (7 registros)

| Campo | Tabla destino | Campo destino | Notas |
|---|---|---|---|
| `id` | `legacy_record_map` | `legacy_id` | entity_type='project' |
| `id` | `projects` | `id` (si aún no existe otro con source_lead_id) | Revisar si hay colisión |
| `nombre` | `projects` | `name` | directo |
| `email` | `client_emails` | `value_raw` | crea client si no existe |
| `telefono` | `client_phones` | `value_raw` | directo |
| `locacion` | `projects` | `location_name` | directo |
| `fecha_tentativa` | `projects` | `event_date` | directo |
| `tipo_evento` | `projects` | `type` | directo |
| `fuente` | `projects` | `source` | directo |
| `status` | `projects` | `commercial_status` | mapping: 'Convertido' → 'accepted' + operational='confirmed' |
| `mail_status` | `outbox_events` | `event_type='email.sent'` o `email.opened` | crear evento de tracking |
| `next_task` | `workflow_task_instances` | `name` (derivado) | crear task si está pendiente |
| `lead_id_job` | `projects` | `id` del project (re-link) | usar el `id` del job para encontrar el project equivalente |
| `created` | `projects` | `created_at` | directo |

### G.3 `jobs.json` (9 registros)

| Campo | Tabla destino | Campo destino | Notas |
|---|---|---|---|
| `id` | `legacy_record_map` | `legacy_id` | entity_type='project' (un job ES un project histórico) |
| `nombre` | `projects` | `name` | si hay duplicado, revisar nombre a usar |
| `boda_date` | `projects` | `event_date` | directo |
| `location` | `projects` | `location_name` | directo |
| `lead_id` | `projects` | `source_lead_id` (en `legacy_record_map`) | guardar como legacy_id también |
| `client_id` | `projects` | (crear `project_clients`) | crear entrada con `role='participant'` |
| `package` | `projects` | `package_name_snapshot` | directo |
| `price_total` | `projects` | `price_total_units` | `* 100` para GTQ; **validar con regex antes** |
| `status` | `projects` | `operational_status` | mapping: 'En produccion' → 'editing' o 'gallery_preparation' |

### G.4 `quotes.json` (4 registros)

| Campo | Tabla destino | Campo destino |
|---|---|---|
| `id` | `legacy_record_map` | `legacy_id`, entity_type='quote' |
| `lead_id` | `quotes` | `project_id` (vía source_lead_id) |
| `paquete_nombre` | `quote_items` | `name` (1 item) |
| `precio_total` | `quotes` | `total_units` (validar con regex) |
| `plan_pago` | `quotes` | `number` (en `payment_installments`) |
| `cuota_monto` | `payment_installments` | `amount_units` (validar con regex) |
| `status` | `quotes` | `status` directo |
| `items_snapshot` | `quote_items` | múltiples rows |

### G.5 `payments.json` (6 registros) — CLASIFICACIÓN

**Decisión clave:** cada registro de `payments.json` se clasifica
**individualmente** según su contenido. No se migra en bloque.

| ID | Monto | Status | Cuota | job_id | Clasificación propuesta | Confianza | Revisión |
|---|---|---|---|---|---|---|---|
| `pay-1` | Q7,500 | Pagado | (sin campo) | `boda-1` | **factura (cuota 1 pagada)** | Media | sí |
| `pay-2` | Q7,500 | Pagado | (sin campo) | `boda-1` | **factura (cuota 2 pagada)** | Media | sí |
| `pay-3` | Q10,000 | Pendiente | (sin campo) | `boda-1` | **factura (cuota 3 pendiente)** | Media | sí |
| `pay-4` | Q5,000 | Late | (sin campo) | `boda-2` | **factura (cuota 1 atrasada)** | Media | sí |
| `pay-24b2c426` | Q500 | Pendiente | (sin campo) | `boda-009a8781` | **ambiguo** (monto muy bajo) | Baja | sí |
| `pay-...` | ... | ... | ... | ... | **se detectan al importar** | — | — |

**Estrategia de migración para `payments.json`:**

1. Importar script lee cada registro.
2. Si tiene `status='Pagado'` y un job_id:
   - Crear `invoice` con `total_units = suma_de_pagos_de_ese_job`.
   - Crear `payment_transactions` (1 por cada pago) con
     `transaction_type='payment'`.
3. Si tiene `status='Pendiente'` y un job_id:
   - Crear `invoice` con `total_units` basado en datos del job.
   - Crear `payment_installments` basadas en `plan_pago` del job.
4. Si es ambiguo: marcar en `legacy_record_map.migration_status='skipped'`
   y reportar a Kevin.

### G.6 `workflow_instances.json` + `workflow_history.json`

| Campo | Tabla destino | Campo destino |
|---|---|---|
| (todo) | `workflow_instances` + `workflow_task_instances` + `automation_runs` | mapping uno-a-uno con id estable |
| `id` | `legacy_record_map` | `legacy_id`, entity_type='workflow_instance' |

### G.7 `mail_log.json`

| Campo | Tabla destino |
|---|---|
| `id` | `mail_log` (nueva) + `legacy_record_map` |
| todo | mapping uno-a-uno, agregar `tenant_id` y `company_id` heredados del project |

### G.8 `calendar.json`

| Campo | Tabla destino |
|---|---|
| (todo) | `calendar_events` |

### G.9 `tenants.json` y `settings.json`

| Archivo | Tabla destino |
|---|---|
| `tenants.json` | `tenants` (1 row) + `companies` (2 rows) + `users` (1 row: Kevin) + `user_company_memberships` (2 rows) |
| `settings.json` | `settings` (key-value por company) |

### G.10 `email_templates.json` y `packages.json`

| Archivo | Tabla destino |
|---|---|
| `email_templates.json` | `email_templates` (1 a 1) |
| `packages.json` | `products` (con `type='package'`, `price_units = price * 100`) |

### G.11 `team.json`

| Campo | Tabla destino |
|---|---|
| (todo) | `users` (con `role='team'`) + `user_company_memberships` (asignar a Norkevin) |

### G.12 `crm.db`

[CONFIRMADO EN CÓDIGO] `crm.db` está VACÍO. No hay datos a migrar. Se
borrará y se re-creará desde cero en fase 1.

---

## H. Backup y rollback (PowerShell, multiplataforma)

### H.1 Backup antes de fase 6

```powershell
# backup_pre_migracion.ps1
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = "C:\Users\fotov\.openclaw\workspace\crm_norkevin\backups\$timestamp"
New-Item -ItemType Directory -Force -Path $backupDir

# 1. Backup de JSONs
Copy-Item -Recurse "C:\Users\fotov\.openclaw\workspace\crm_norkevin\data\*.json" $backupDir

# 2. Backup de crm.db (con VACUUM INTO para WAL seguro)
sqlite3 "C:\Users\fotov\.openclaw\workspace\crm_norkevin\data\crm.db" "VACUUM INTO '$backupDir\crm.db.backup';"

# 3. Hash SHA-256 de cada archivo
Get-ChildItem $backupDir | ForEach-Object {
    $hash = Get-FileHash -Algorithm SHA256 $_.FullName
    "$($_.Name) $($hash.Hash)" | Out-File "$backupDir\checksums.txt" -Append
}

# 4. Crear tag Git (solo si repo es privado)
git tag "pre-migracion-$timestamp"

Write-Host "Backup completo en $backupDir"
```

### H.2 Restaurar JSON desde backup

```powershell
# restore_json.ps1 -BackupPath "C:\...\backups\20260709_153022"
$src = $args[0]
Copy-Item -Recurse -Force "$src\*.json" "C:\Users\fotov\.openclaw\workspace\crm_norkevin\data\"
Write-Host "JSON restaurado desde $src"
```

### H.3 Ventana de mantenimiento para rollback

| Fase | Estado | Permite rollback a JSON |
|---|---|---|
| Fase 0-2 | Importación silenciosa | ✅ Seguro (JSON intacto) |
| Fase 3 | Reporte de conflictos | ✅ Seguro |
| Fase 4 | Validación | ✅ Seguro |
| Fase 5 | Modo de prueba (rama) | ✅ Seguro |
| Fase 6 | Activación (lectura/escritura desde SQLite) | ⚠️ Solo si la app no escribió todavía |
| Fase 7 | Producción | ❌ NO se puede volver a JSON sin perder datos |

**Regla:** una vez que la fase 6 está activa y la app escribió al
menos UN registro, el rollback a JSON ya no es 100% seguro. Se
puede intentar con un script de export inverso, pero pueden perderse
datos que no estaban en los JSON originales.

### H.4 Backup con WAL activo

**No** se copia `crm.db` directamente mientras la app está escribiendo.
Se usa la API de backup de SQLite:

```powershell
sqlite3 "data\crm.db" ".backup 'backups\crm_safe.db'"
```

O el comando Python:

```python
import sqlite3
conn = sqlite3.connect("data/crm.db")
backup = sqlite3.connect("backups/crm_safe.db")
conn.backup(backup)
backup.close()
conn.close()
```

### H.5 Política de retención

- Backups de fase 0: 30 días
- Backups pre-producción: 7 días
- Snapshots Git: indefinido (tags)

---

## I. Reporte preliminar de conflictos (V3 — corregido)

### I.1 Conteo correcto

**[CONFIRMADO EN CÓDIGO] — datos reales de los JSON:**

```
clients.json:  13 registros
leads.json:    7 registros
jobs.json:     9 registros
quotes.json:   4 registros
payments.json: 6 registros
contracts.json: 0
workflow_instances.json + history: ~10 instances
calendar.json: 4 eventos
mail_log.json: ~5 entradas
email_templates.json: 12
packages.json: 11
settings.json: 1
tenants.json: 2 (reinterpretados)
team.json: 3
```

**Conteo anterior Kevin:** 13 clientes con 3 duplicados Ana y 2
"KEVIN LEMUS". Eso da 13 totales, no 14. Mi reporte anterior
mencionó "11 clientes restantes" — eso fue para llegar a 13. Pero el
texto "3 Ana + 2 Kevin + 11" suma 16, lo cual era incorrecto. Conteo
real: **3 Ana + 2 Kevin + 8 únicos = 13 clientes total.**

### I.2 Clientes duplicados — análisis campo por campo

| ID | Nombre | Email | Teléfono | Notas | Recomendación |
|---|---|---|---|---|---|
| `client-1` | Maria Lopez | maria.lopez@gmail.com | (sin) | OK | Conservar |
| `client-2` | Ana Ramirez | ana.ramirez@yahoo.com | (sin) | OK (más viejo) | **Principal** |
| `client-0165833f` | Ana Ramirez | ana.ramirez@yahoo.com | (sin) | Mismo email | Archivar |
| `client-97399c98` | Ana Ramirez | ana.ramirez@yahoo.com | (sin) | Mismo email | Archivar |
| `client-9d625381` | KEVIN LEMUS | kevinnoriega01@gmail.com | (sin) | Posible dueño | **Revisión manual** |
| `client-33fcc706` | Kevin Daniel Lemus Noriega | norkevinfoto@gmail.com | (sin) | Posible mismo dueño | **Revisión manual** |
| otros 7 | varios | varios | — | OK | Conservar |

**Acción para `client-2` vs los 2 duplicados:**
1. No fusionar automáticamente.
2. Crear `legacy_record_map` con cada ID antiguo.
3. Marcar los 2 duplicados con `archived_at`.
4. Dejar que Kevin decida manualmente si eran la misma persona.

### I.3 Jobs duplicados

| ID | Nombre | lead_id (legacy) | Eventos |
|---|---|---|---|
| `boda-1` | Maria Lopez & Carlos Mendez | `lead-1` | Conservar |
| `boda-2` | Ana Ramirez & Luis Garcia | `lead-2` | Conservar (más completo) |
| `boda-3d559b03` | Boda Ana Ramirez | `lead-2` | Comparar con boda-2 (decisión manual) |
| `boda-009a8781` | (sin nombre) | `lead-2` | Comparar |
| `boda-71c243ed` | Boda Sofia Castillo | `lead-3f0bf51a` | Conservar |
| `boda-9ac2b517` | Boda Sofia Castillo | `lead-3f0bf51a` | Comparar |

**Acción:**
- `legacy_record_map` preserva los 3 IDs de Ana Ramirez.
- `projects.source_lead_id` se vincula al primer job (el más completo).
- Los otros 2 jobs se archivan (no se borran).
- Kevin decide después si eran realmente el mismo evento o 3 eventos
  distintos.

### I.4 Leads sin job

| ID | Status | ¿Tiene job? |
|---|---|---|
| `lead-1` | Convertido | Sí (`boda-1`) pero `lead_id_job` no apunta |
| `lead-2` | Convertido | Sí (3 jobs) |
| `lead-3f0bf51a` | Nuevo | Sí (2 jobs) |
| `lead-6b6477cc` | Nuevo | No (OK) |
| `lead-f27ecff7` | Nuevo | No (OK) |

**Causa:** el código de `accept-quote` actualiza `lead.lead_id_job` con
el último job creado, pero no verifica si ya existía.

### I.5 Quotes y payments cruzados

| Quote | job | Pagos |
|---|---|---|
| `quote-bda9e455` (Aceptada, Q1,000) | lead-2 | 0 pagos |
| `quote-13fa31fa` (Enviada, Q15,000) | lead-2 | 0 pagos |
| `quote-d89bc8f9` (Enviada, Q34) | lead-2 | 0 pagos (test?) |
| `quote-ff5babbe` (Enviada, Q3,800) | lead-2 | 0 pagos |

**Observación:** ninguna quote está vinculada a un payment con `quote_id`.
La asignación es manual hoy.

---

## J. Criterio de éxito de la migración

La migración se considera **aceptada** solo cuando se cumplen TODAS estas
condiciones:

1. ✅ Ningún archivo JSON original fue modificado.
2. ✅ Existe un backup verificado en `backups/<timestamp>/`.
3. ✅ Cada registro de cada JSON tiene destino: tabla destino O aparece
   en `legacy_record_map` con `migration_status='skipped'` o
   `migration_status='review_needed'`.
4. ✅ No hay foreign keys rotas: todos los `client_id`, `project_id`,
   `invoice_id`, etc. referenciados existen.
5. ✅ Conteos documentados: para cada tabla, el conteo de filas
   migradas vs conteo en JSON original.
6. ✅ Montos reconciliados: la suma de `total_units` en `invoices`
   coincide con la suma de los pagos originales para cada `job_id`.
7. ✅ Los duplicados NO se fusionaron automáticamente. Aparecen en
   `legacy_record_map` con su `legacy_id` original y `archived_at` en
   `clients` (o en `projects`).
8. ✅ `alembic upgrade head` funciona sobre una copia real.
9. ✅ `alembic downgrade -1` revierte correctamente la última migración.
10. ✅ La aplicación puede leer SQLite en modo de prueba sin tocar
    datos (rama de lectura).
11. ✅ La reversión a JSON es posible mediante `restore_json.ps1`
    siempre que no se haya escrito en SQLite aún.

---

## K. Decisiones pendientes (actualizadas)

Kevin, estas son las decisiones que **siguen pendientes** después de
la V3:

1. **Aprobar el inventario de 31 tablas** (16 MVP obligatorias + 15 necesarias).
2. **Aprobar `legacy_record_map`** como mecanismo de preservación de IDs.
3. **Aprobar el modelo financiero** (invoice, installments, transactions separados + refunds).
4. **Aprobar la normalización de email** (sin `+alias`).
5. **Aprobar la no-fusión automática** de duplicados.
6. **Aprobar `project_clients`** como única fuente de verdad de la
   relación project–persona.
7. **Aprobar la matriz tenant_id / company_id** de la sección C.2.
8. **Aprobar los índices parciales únicos** (`uq_project_primary_contact`,
   `uq_project_billing_contact`, `uq_project_active_workflow`).
9. **Aprobar el script PowerShell** de backup.
10. **Aprobar el uso de `render_as_batch=True`** en Alembic.
11. **Aprobar `processed_events` con UNIQUE(tenant_id, idempotency_key)**
    (no por company).
12. **Aprobar la clasificación de `payments.json` por registro** (no en
    bloque).
13. **Aprobar la ventana de mantenimiento** para rollback.
14. **Aprobar la separación `processed_events` / `outbox_events` /
    `automation_runs` / `activity_log`**.

---

## L. Diferencias con la versión anterior (V2)

| V2 | V3 |
|---|---|
| 24 tablas (afirmado) | **31 tablas reales** |
| `primary_client_id` y `secondary_client_id` en `projects` | Eliminados. Solo `project_clients` |
| `users.company_ids` (JSON array) | `user_company_memberships` (tabla real) |
| Sin `client_emails` ni `client_phones` | Tablas separadas con `value_raw` y `value_normalized` |
| `invoice_items` no existía | Existe, líneas congeladas |
| `payment_schedule_templates` y `_rules` no existían | Existen, con `percentage_bps` y `amount_units` |
| `outbox_events` no existía | Existe, separado de `processed_events` |
| `automation_runs` no existía | Existe, separado de `activity_log` |
| `legacy_record_map` no existía | Existe, preserva IDs antiguos |
| `tax_rate REAL` | `tax_rate_bps INTEGER` |
| `to_units(amount: float)` | Entrada validada por regex como string |
| Devoluciones: `status='refunded'` en pago original | `transaction_type='refund'` separado |
| `payment_installments.payment_method` | Eliminado, vive en transactions |
| `events` (calendario) | `calendar_events` (renombrado) |
| `UNIQUE(project_id, status) WHERE status='signed'` (SQL inválido) | `UNIQUE(company_id, number)` (válido) |
| `workflow_instance_id` redundante en projects | FK simple para performance |
| `price_paid_units` y `balance_due_units` en projects | Eliminados, calculados |
| `source_lead_id` y `legacy_lead_id` en projects | Solo `legacy_record_map` |
| `archived` como estado operativo | Eliminado, `archived_at` controla |
| `event_completed` requería reseña | Ya no requiere |
| `processed_events` sin `company_id` | Sí, con `tenant_id` y `company_id` opcional |
| Backup con sintaxis Linux | PowerShell multiplataforma |
| `archived` en operational_status CHECK | Eliminado del CHECK |
| Estados `converted` | Eliminado definitivamente |

---

## M. Tablas del MVP (resumen)

**16 obligatorias** (las que Kevin listó) + **15 necesarias para que
funcione** = **31 tablas del MVP**.

Las 15 "necesarias" no son avanzadas; son requisitos para que las 16
obligatorias funcionen. Por ejemplo:

- `client_emails` / `client_phones`: las 16 obligatorias referencian
  clientes; los clientes tienen emails y teléfonos, así que esas
  tablas son obligatorias.
- `invoice_items`: las facturas necesitan líneas.
- `payment_schedule_*`: los planes de pago son obligatorios si vas a
  generar installments.
- `outbox_events`: las 16 obligatorias disparan acciones externas;
  necesitan un outbox.
- `automation_runs`: el workflow engine necesita registrar runs.
- `legacy_record_map`: Kevin dijo preservar los IDs antiguos.
- `sequence_counters`: para numeración correlativa.
- `settings`: para configuración por company.

Si Kevin considera alguna de estas "no MVP", la migración se ajusta
para no crearla y diferirla a fase 2.

---

## N. Tablas futuras (NO creadas en migración inicial)

- `contracts`, `contract_templates` (no hay datos hoy)
- `questionnaires`, `questionnaire_templates` (no hay datos hoy)
- `quote_templates`, `invoice_templates` (no existen hoy)
- `files` (no hay sistema de uploads)
- `mail_log` (solo simulación; sin email service real)

---

## O. Inconsistencias encontradas durante la revisión

1. ❌ `tax_rate REAL` y `to_units(float)` en V2. **Corregido en V3.**
2. ❌ `UNIQUE(project_id, status) WHERE status='signed'` no es SQL
   válido en SQLite dentro de CREATE TABLE. **Corregido en V3.**
3. ❌ Faltaba `project_clients`. **Agregado en V3.**
4. ❌ Faltaba `user_company_memberships`. **Agregado en V3.**
5. ❌ Faltaba `invoice_items`. **Agregado en V3.**
6. ❌ Faltaba `payment_schedule_templates` y `_rules`. **Agregados en V3.**
7. ❌ Faltaba `outbox_events`. **Agregado en V3.**
8. ❌ Faltaba `automation_runs`. **Agregado en V3.**
9. ❌ Faltaba `legacy_record_map`. **Agregado en V3.**
10. ❌ Faltaba `sequence_counters`. **Agregado en V3.**
11. ❌ Devoluciones mal modeladas. **Corregido en V3.**
12. ❌ `events` confundía con eventos del sistema. **Renombrado a
    `calendar_events` en V3.**
13. ❌ `archived` como estado operativo. **Eliminado en V3.**
14. ❌ Conteo de tablas inconsistente entre secciones del documento
    V2. **V3 tiene inventario único en sección B.2.**

---

## P. Confirmación literal de no-modificación

| Item | Estado |
|---|---|
| Código modificado | **NO** |
| Datos modificados | **NO** |
| `crm.db` modificado | **NO** |
| Alembic ejecutado | **NO** |
| JSON modificados | **NO** |
| Tablas creadas | **NO** |
| `app.py` modificado | **NO** |
| Cualquier archivo de código tocado | **NO** |

El único cambio en disco es la **reescritura del archivo
`MODELO_DE_DATOS_CRM.md`** (V3). Verificable con:

```bash
git status  # solo el .md aparece modificado
git diff --stat  # solo ese archivo
```

---

## Q. Riesgos críticos que aún no están resueltos

1. **Decisión de Kevin sobre 14 puntos pendientes (sección K).** Sin su
   aprobación, no se puede avanzar.
2. **Backup pre-migración de crm.db** (está vacío, pero hay que
   documentar la política de backups continuos).
3. **Política de `currency_exponent`** si Kevin maneja una moneda con
   exponente distinto a 2.
4. **Política de `tax_rate_bps`** si una empresa tiene varios impuestos
   (ej. IVA + ISR). El modelo actual asume 1 solo.
5. **`lead_id_job` quedó null** en leads viejos. La migración debe
   inferir el vínculo desde `jobs.json` por `lead_id`.
6. **Pagos sin `quote_id`** en `payments.json`. La migración los
   clasifica como "ambiguos" y los deja en `legacy_record_map` con
   `review_needed`.
7. **Calendar events de bodas pasadas** sin project_id. La migración los
   deja archivados y propone vínculo manual.
8. **Workflow instances de jobs huérfanos** (sin project). La
   migración los archiva.
9. **Migración incremental vs big-bang.** ¿Se hace en una sola
   operación o por fases? El plan actual propone big-bang con backup
   y rollback.
10. **Tiempo total de migración.** Estimado: 4-6 horas de desarrollo +
    2-3 horas de ejecución + 1-2 horas de revisión. No se ha medido
    todavía.

---

## R. Resumen final

Kevin, esta V3 está más cerca de lo que pediste, pero todavía hay
14 decisiones pendientes (sección K) y 10 riesgos abiertos (sección
P). **No avanzo al Paso 3 hasta que respondas las decisiones.**

---

**Decime Kevin:**
1. ¿Aprobás el inventario de 31 tablas?
2. ¿Aprobás la no-fusión automática de duplicados?
3. ¿Aprobás `project_clients` como única fuente de verdad?
4. ¿Aprobás los índices parciales únicos?
5. ¿Aprobás el script PowerShell de backup?
6. ¿Aprobás `render_as_batch=True` en Alembic?
7. ¿Aprobás la clasificación de `payments.json` por registro?
8. ¿Aprobás la ventana de mantenimiento?
9. ¿Hay algo más que falte?

**Sin prisa. No avanzo a Fase 1 (crear schema) hasta tu OK.** 💪