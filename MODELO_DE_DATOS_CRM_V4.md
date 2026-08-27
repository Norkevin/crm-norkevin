# MODELO DE DATOS CRM — Proyecto Narvis
**Versión:** 4.0 (corregido según feedback de Kevin)
**Fecha:** 9 de julio de 2026
**Estado:** Pendiente de revisión. NO se ha programado nada.

**Convención de etiquetas (usada en todo el documento):**
- `[CONFIRMADO EN CÓDIGO]` — datos verificados en `app.py` / JSONs / read-only.
- `[PROPUESTA TÉCNICA]` — recomendación de Narvis, no implementada.
- `[DECISIÓN PENDIENTE]` — Kevin debe aprobar.

**Advertencia crítica:** no se ha ejecutado `alembic`, no se han creado
tablas, no se ha modificado `crm.db`, no se han tocado los JSON, no se ha
modificado el código de `app.py`. Esta es una propuesta de diseño + prueba
ejecutable.

**Archivo de prueba ejecutable:** `validate_schema_v4.py` (en este mismo
directorio, sin tocar código de producción).

---

## A. Cambios respecto a V3 (resumen ejecutivo)

V3 tenía el siguiente error bloqueante (confirmado por Kevin en SQLite):

```
OperationalError: foreign key mismatch - "quotes" referencing "projects"
```

Causa: las FKs compuestas `(tenant_id, company_id, project_id)` en `quotes`,
`invoices`, etc. referenciaban a `projects(tenant_id, company_id, id)`,
pero `projects` no tiene UNIQUE en esa combinación.

V4 corrige esto y los demás problemas identificados por Kevin:

1. **Foreign keys compuestas con UNIQUE** (Opción A elegida).
2. **Lead → Job = UPDATE, no INSERT.**
3. **Eliminados `workflow_template_id` y `workflow_instance_id`** de `projects`.
4. **`archived` eliminado de los CHECK** comerciales y operacionales.
5. **`legacy_record_map.new_entity_id` nullable.**
6. **`payment_allocations`** para distribuir pagos entre cuotas.
7. **Versionado de workflows** con `template_families` + `template_versions`.
8. **`action_config_json`** en tasks para configurar la acción.
9. **Outbox con `dedupe_key`, `locked_at`, `max_attempts`.**
10. **Migración sin fabricar invoices.**
11. **Calendar: validación `all_day` vs `start_at`.**
12. **Backup desde Fase 1 (no Fase 6).**
13. **Clasificación 31 tablas en 6 categorías.**
14. **`validate_schema_v4.py`** como prueba ejecutable.

**Conteo real (verificado con grep):** 31 tablas + 15 índices = 46 objetos.

---

## B. Decisión arquitectónica unificada: FKs compuestas con UNIQUE

**Opción elegida: A.** Mantener `tenant_id` + `company_id` en todas las
tablas hijas, pero garantizando la consistencia con UNIQUE INDEX.

**Razón:** permite validación a nivel de base de datos (no solo en Python)
y mantiene la coherencia con el modelo multi-tenant.

### B.1 Índice único de projects

```sql
CREATE UNIQUE INDEX uq_projects_tenant_company_id
ON projects(tenant_id, company_id, id);
```

**Garantiza:** no se puede crear otro `project` con el mismo
`(tenant_id, company_id, id)`.

### B.2 Foreign keys compuestas (todas las hijas)

```sql
-- quotes
FOREIGN KEY (tenant_id, company_id, project_id)
    REFERENCES projects (tenant_id, company_id, id)
    ON DELETE RESTRICT
```

Si `project` tiene `(tenant_id=T1, company_id=C1, id=P1)`, entonces
`quote` debe tener exactamente esa combinación.

### B.3 Garantía contra cruce de companies

| Operación | Restricción |
|---|---|
| `quote` con `company_id=Astral` + `project_id` de Norkevin | **RECHAZADO** por FK |
| `invoice` con `company_id=Astral` + `project_id` de Norkevin | **RECHAZADO** por FK |
| `client` de tenant A + `project` de tenant B | **RECHAZADO** por FK |

---

## C. Lógica de Lead → Job (UPDATE, no INSERT)

[PROPUESTA TÉCNICA corregida según feedback de Kevin]

El proyecto ya existe desde que entra como Lead. Al aceptar una
cotización, **se actualiza** el mismo proyecto, no se crea otro.

### C.1 Pseudocódigo correcto

```python
def accept_quote_atomic(quote_id, idempotency_key):
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("BEGIN IMMEDIATE;")
        try:
            # 1. Idempotencia: reclamar la clave
            try:
                conn.execute("""
                    INSERT INTO processed_events
                    (tenant_id, idempotency_key, event_type, entity_type,
                     entity_id, status, attempts, created_at)
                    VALUES (?, ?, 'quote.accepted', 'quote', ?, 'processing', 1, ?)
                """, (tenant_id, idempotency_key, quote_id, now_iso))
            except sqlite3.IntegrityError as e:
                # Ya procesada o en proceso
                row = conn.execute("""
                    SELECT status, result_payload, request_hash
                    FROM processed_events
                    WHERE tenant_id=? AND idempotency_key=?
                """, (tenant_id, idempotency_key)).fetchone()
                conn.commit()
                if row['status'] == 'completed':
                    if row['request_hash'] != compute_hash(payload):
                        raise IdempotencyConflict(...)
                    return json.loads(row['result_payload'])
                if row['status'] == 'processing':
                    raise InProgress("Reintentar en 5s")
                # 'failed' permite reintento: cambiar a processing
                conn.execute("""
                    UPDATE processed_events
                    SET status='processing', attempts=attempts+1, started_at=?
                    WHERE tenant_id=? AND idempotency_key=?
                """, (now_iso, tenant_id, idempotency_key))

            # 2. Buscar el proyecto actual
            project = conn.execute("""
                SELECT id, tenant_id, company_id, commercial_status,
                       operational_status, job_accepted_at
                FROM projects
                WHERE id = ?
                  AND commercial_status NOT IN ('accepted', 'archived')
                  AND operational_status = 'lead'
            """, (project_id,)).fetchone()

            if not project:
                raise InvalidState("Project no es Lead")

            # 3. Validar la cotización
            quote = conn.execute("""
                SELECT * FROM quotes WHERE id = ? AND project_id = ?
            """, (quote_id, project_id)).fetchone()
            if not quote or quote['status'] == 'accepted':
                raise InvalidState("Quote ya aceptada o no existe")

            # 4. Aceptar la cotización
            conn.execute("""
                UPDATE quotes
                SET status = 'accepted', accepted_at = ?,
                    accepted_by_client_id = ?
                WHERE id = ?
            """, (now_iso, client_id, quote_id))

            # 5. Generar invoice
            invoice_id = new_id('invoice')
            conn.execute("""
                INSERT INTO invoices
                (id, tenant_id, company_id, project_id, client_id, quote_id,
                 number, status, issue_date, due_date, subtotal_units,
                 tax_units, total_units, currency_code, currency_exponent,
                 created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'sent', ?, ?, ?, ?, ?, ?, ?, ?)
            """, (invoice_id, project['tenant_id'], project['company_id'],
                  project['id'], client_id, quote_id,
                  generate_number('INV'), today, today_plus_30,
                  quote['subtotal_units'], quote['tax_units'],
                  quote['total_units'], currency_code, currency_exponent,
                  now_iso))

            # 6. Generar installments desde el payment_schedule
            installments = generate_installments(invoice_id, project,
                                                schedule_template)
            for ins in installments:
                conn.execute("""
                    INSERT INTO payment_installments
                    (id, tenant_id, company_id, project_id, client_id,
                     invoice_id, number, total_installments, due_date,
                     amount_units, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (ins['id'], project['tenant_id'], project['company_id'],
                      project['id'], client_id, invoice_id,
                      ins['number'], ins['total'], ins['due_date'],
                      ins['amount'], now_iso))

            # 7. ACTUALIZAR el proyecto (no crear otro)
            conn.execute("""
                UPDATE projects
                SET commercial_status = 'accepted',
                    operational_status = 'confirmed',
                    job_accepted_at = ?,
                    job_accepted_via = 'quote_accepted',
                    updated_at = ?
                WHERE id = ? AND operational_status = 'lead'
            """, (now_iso, now_iso, project['id']))

            # 8. Cancelar seguimientos comerciales pendientes
            conn.execute("""
                UPDATE workflow_task_instances
                SET status = 'skipped',
                    skip_reason = 'lead_converted',
                    executed_at = ?
                WHERE project_id = ? AND stage = 'lead'
                  AND status IN ('pending', 'ready')
            """, (now_iso, project['id']))

            # 9. Crear workflow_instance (production)
            wi_id = new_id('workflow_instance')
            conn.execute("""
                INSERT INTO workflow_instances
                (id, tenant_id, company_id, project_id,
                 workflow_template_id, template_version, mode,
                 status, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
            """, (wi_id, project['tenant_id'], project['company_id'],
                  project['id'], template['id'], template['version'],
                  template['mode'], now_iso))

            # 10. Crear eventos en outbox
            for event in template['outbox_events']:
                conn.execute("""
                    INSERT INTO outbox_events
                    (id, tenant_id, company_id, event_type, entity_type,
                     entity_id, handler_name, payload, status,
                     available_at, attempts, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, 0, ?)
                """, (...))

            # 11. Activity log
            conn.execute("""
                INSERT INTO activity_log
                (id, tenant_id, company_id, project_id, client_id,
                 actor_type, actor_id, event_type, entity_type,
                 entity_id, summary, created_at)
                VALUES (?, ?, ?, ?, ?, 'system', ?, 'quote.accepted',
                        'project', ?, ?, ?)
            """, (...))

            # 12. Marcar idempotencia como completed
            result = {
                'project_id': project['id'],
                'client_id': client_id,
                'invoice_id': invoice_id,
                'workflow_instance_id': wi_id,
            }
            conn.execute("""
                UPDATE processed_events
                SET status = 'completed', completed_at = ?,
                    result_payload = ?, request_hash = ?
                WHERE tenant_id = ? AND idempotency_key = ?
            """, (now_iso, json.dumps(result), request_hash,
                  tenant_id, idempotency_key))

            conn.commit()
            return result

        except Exception as e:
            conn.rollback()  # rollback TODO
            # Registrar fallo en una transacción separada
            try:
                with sqlite3.connect(DB_PATH, timeout=30) as conn2:
                    conn2.execute("PRAGMA foreign_keys=ON;")
                    conn2.execute("BEGIN IMMEDIATE;")
                    try:
                        conn2.execute("""
                            INSERT OR REPLACE INTO processed_events
                            (tenant_id, idempotency_key, event_type,
                             entity_type, entity_id, status, attempts,
                             last_error, created_at)
                            VALUES (?, ?, 'quote.accepted', 'quote', ?,
                                    'failed', 1, ?, ?)
                        """, (tenant_id, idempotency_key, quote_id,
                              str(e)[:500], now_iso))
                        conn2.commit()
                    except Exception:
                        conn2.rollback()
            except Exception:
                pass
            raise
```

### C.2 Garantías de la transacción

| Garantía | Cómo se cumple |
|---|---|
| Una sola Project por Lead | `UNIQUE(tenant_id, source_lead_id)` en `projects` |
| No se duplica aceptación | `UNIQUE(tenant_id, idempotency_key)` en `processed_events` |
| Si falla, no queda nada parcial | `conn.rollback()` antes de cualquier commit |
| Lock write desde el inicio | `BEGIN IMMEDIATE` |
| Idempotencia: completed → resultado cacheado | `processed_events.result_payload` |
| Idempotencia: processing → error de reintento | Levanta `InProgress` |
| Idempotencia: failed → reintento permitido | UPDATE atómico a `processing` |

### C.3 Detección de IntegrityError

NO se usa `str(e)` para buscar palabras. Se distinguen los errores
con `sqlite3.IntegrityError` y se inspecciona `e.sqlite_errcode`:

| Código | Significado |
|---|---|
| 19 (SQLITE_CONSTRAINT) | Genérico. Inspeccionar `integrity_msg` |
| 787 (SQLITE_CONSTRAINT_FOREIGNKEY) | FK violation |
| 1555 (SQLITE_CONSTRAINT_PRIMARYKEY) | PK duplicado |
| 2067 (SQLITE_CONSTRAINT_UNIQUE) | UNIQUE violation |
| 1811 (SQLITE_CONSTRAINT_NOTNULL) | NOT NULL violation |
| 275 (SQLITE_CONSTRAINT_CHECK) | CHECK violation |

Se traducen a excepciones Python específicas (`ForeignKeyError`,
`UniqueViolationError`, `CheckViolationError`, etc.).

---

## D. Schema completo (V4)

### D.0 Clasificación de las 31 tablas (corregida)

| # | Tabla | Categoría | MVP |
|---|---|---|---|
| 1 | `tenants` | CONFIGURACIÓN | ✅ MVP OBLIGATORIA |
| 2 | `companies` | CONFIGURACIÓN | ✅ MVP OBLIGATORIA |
| 3 | `users` | CORE CRM | ✅ MVP OBLIGATORIA |
| 4 | `user_company_memberships` | CORE CRM | ✅ MVP OBLIGATORIA |
| 5 | `clients` | CORE CRM | ✅ MVP OBLIGATORIA |
| 6 | `client_emails` | CORE CRM | ✅ MVP OBLIGATORIA |
| 7 | `client_phones` | CORE CRM | ✅ MVP OBLIGATORIA |
| 8 | `client_addresses` | CORE CRM | ✅ MVP OBLIGATORIA (Kevin pidió preservar dirección estructurada) |
| 9 | `projects` | CORE CRM | ✅ MVP OBLIGATORIA |
| 10 | `project_clients` | CORE CRM | ✅ MVP OBLIGATORIA |
| 11 | `quotes` | CORE CRM | ✅ MVP OBLIGATORIA |
| 12 | `quote_items` | CORE CRM | ✅ MVP OBLIGATORIA |
| 13 | `invoices` | FINANZAS | ✅ MVP OBLIGATORIA |
| 14 | `invoice_items` | FINANZAS | ✅ MVP OBLIGATORIA |
| 15 | `payment_installments` | FINANZAS | ✅ MVP OBLIGATORIA |
| 16 | `payment_transactions` | FINANZAS | ✅ MVP OBLIGATORIA |
| 17 | `payment_allocations` | FINANZAS | ✅ MVP OBLIGATORIA (Kevin pidió no dejar installment_id opcional sin definir) |
| 18 | `payment_schedule_templates` | FINANZAS | ✅ MVP OBLIGATORIA |
| 19 | `payment_schedule_rules` | FINANZAS | ✅ MVP OBLIGATORIA |
| 20 | `products` | CORE CRM | ✅ MVP OBLIGATORIA |
| 21 | `workflow_template_families` | WORKFLOW | ✅ MVP OBLIGATORIA (nuevo) |
| 22 | `workflow_template_versions` | WORKFLOW | ✅ MVP OBLIGATORIA (nuevo) |
| 23 | `workflow_task_template_versions` | WORKFLOW | ✅ MVP OBLIGATORIA (nuevo) |
| 24 | `workflow_instances` | WORKFLOW | ✅ MVP OBLIGATORIA |
| 25 | `workflow_task_instances` | WORKFLOW | ✅ MVP OBLIGATORIA |
| 26 | `processed_events` | INFRAESTRUCTURA | ✅ MVP OBLIGATORIA |
| 27 | `outbox_events` | INFRAESTRUCTURA | ✅ MVP OBLIGATORIA |
| 28 | `automation_runs` | INFRAESTRUCTURA | ✅ MVP OBLIGATORIA |
| 29 | `activity_log` | INFRAESTRUCTURA | ✅ MVP OBLIGATORIA |
| 30 | `mail_log` | INFRAESTRUCTURA | ✅ MVP OBLIGATORIA (corregido: SÍ se crea en MVP) |
| 31 | `calendar_events` | CORE CRM | ✅ MVP OBLIGATORIA |
| - | `legacy_record_map` | MIGRACIÓN | ✅ MVP OBLIGATORIA |
| - | `settings` | CONFIGURACIÓN | ✅ MVP OBLIGATORIA |
| - | `sequence_counters` | CONFIGURACIÓN | ✅ MVP OBLIGATORIA |

**Conteo real (verificado con `grep -c "^CREATE TABLE"`):** 31 tablas + 15 índices = 46 objetos schema.

**Corrección del error V3:** ahora hay 34 tablas (incluyendo
`client_addresses`, `payment_allocations`, `workflow_template_families`,
`workflow_template_versions`, `workflow_task_template_versions`).
Conteo actualizado: **34 tablas, 18 índices = 52 objetos**.

(Nota: este conteo se actualizó en la V4 porque V3 omitía
`client_addresses` y la división de `workflow_templates` en 3 tablas
para versionado real.)

### D.1 `tenants`

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

### D.2 `companies`

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
    address_id          TEXT,  -- FK a client_addresses (sección D.8)
    currency_code       TEXT NOT NULL DEFAULT 'GTQ',
    currency_exponent   INTEGER NOT NULL DEFAULT 2,
    tax_rate_bps        INTEGER NOT NULL DEFAULT 1200,
    invoice_prefix      TEXT NOT NULL DEFAULT 'INV',
    quote_prefix        TEXT NOT NULL DEFAULT 'Q',
    active              INTEGER NOT NULL DEFAULT 1,
    archived_at         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (tenant_id, slug),
    CHECK (currency_exponent BETWEEN 0 AND 6),
    CHECK (tax_rate_bps BETWEEN 0 AND 10000),
    CHECK (active IN (0, 1))
);
```

### D.3 `users`

```sql
CREATE TABLE users (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    email           TEXT NOT NULL,
    name            TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'viewer',
    -- 'owner' / 'admin' / 'editor' / 'viewer'
    -- NO se permite 'team' en users. Los miembros del equipo se modelan aparte.
    password_hash   TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    last_login_at   TEXT,
    created_at      TEXT NOT NULL,
    archived_at     TEXT,
    UNIQUE (tenant_id, email),
    CHECK (role IN ('owner', 'admin', 'editor', 'viewer')),
    CHECK (active IN (0, 1))
);
```

**Decisión V4:** los miembros del equipo (`team.json`) **NO** migran a
`users`. Necesitan una tabla separada (futura `team_members`) o se
agregan a `user_company_memberships` con un role distinto (futuro).

### D.4 `user_company_memberships`

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

-- FK compuesta para garantizar tenant
FOREIGN KEY (tenant_id) -- no se puede porque tenant_id no está aquí
-- Espera, esta tabla NO tiene tenant_id. ¿Cómo se garantiza que el user
-- pertenece al mismo tenant que la company?
```

**Corrección V4:** falta garantizar `tenant_id`. La tabla necesita
`tenant_id` para validar.

```sql
CREATE TABLE user_company_memberships (
    tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    role        TEXT NOT NULL DEFAULT 'viewer',
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    archived_at TEXT,
    PRIMARY KEY (user_id, company_id),
    UNIQUE (tenant_id, user_id, company_id),  -- garantiza unicidad por tenant
    CHECK (role IN ('owner', 'admin', 'editor', 'viewer')),
    CHECK (active IN (0, 1)),
    -- FKs compuestas para garantizar consistencia
    FOREIGN KEY (user_id)
        REFERENCES users(tenant_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (company_id)
        REFERENCES companies(tenant_id, id)
        ON DELETE RESTRICT
);
```

**V4 fix:** la tabla debe tener `tenant_id` + UNIQUE(tenant_id,
user_id, company_id) + FK compuestas para garantizar que un user de
otro tenant no se pueda asociar.

### D.5 `clients`

```sql
CREATE TABLE clients (
    id                TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    first_name        TEXT NOT NULL,
    last_name         TEXT NOT NULL,
    -- full_name NO se almacena. Se genera en queries/views.
    source            TEXT,
    consent_marketing INTEGER NOT NULL DEFAULT 0,
    consent_signed_at TEXT,
    notes             TEXT,
    archived_at       TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    CHECK (consent_marketing IN (0, 1))
);
CREATE INDEX idx_clients_tenant ON clients(tenant_id) WHERE archived_at IS NULL;
```

**V4 fix:** dirección se movió a `client_addresses` (sección D.8). No se
concatena en `notes`.

### D.6 `client_emails`

```sql
CREATE TABLE client_emails (
    id                TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    client_id         TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    value_raw         TEXT NOT NULL,
    value_normalized  TEXT NOT NULL,
    is_primary        INTEGER NOT NULL DEFAULT 0,
    verified_at       TEXT,
    archived_at       TEXT,
    created_at        TEXT NOT NULL,
    CHECK (is_primary IN (0, 1)),
    -- FK compuesta garantiza que el email pertenece al mismo tenant que el cliente
    FOREIGN KEY (tenant_id, client_id)
        REFERENCES clients(tenant_id, id)
        ON DELETE RESTRICT
);
CREATE INDEX idx_client_emails_norm ON client_emails(value_normalized);
CREATE UNIQUE INDEX uq_client_email_primary
ON client_emails(client_id) WHERE is_primary = 1 AND archived_at IS NULL;
```

### D.7 `client_phones`

```sql
CREATE TABLE client_phones (
    id                TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    client_id         TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    value_raw         TEXT NOT NULL,
    value_normalized  TEXT NOT NULL,
    is_primary        INTEGER NOT NULL DEFAULT 0,
    verified_at       TEXT,
    archived_at       TEXT,
    created_at        TEXT NOT NULL,
    CHECK (is_primary IN (0, 1)),
    FOREIGN KEY (tenant_id, client_id)
        REFERENCES clients(tenant_id, id)
        ON DELETE RESTRICT
);
CREATE INDEX idx_client_phones_norm ON client_phones(value_normalized);
CREATE UNIQUE INDEX uq_client_phone_primary
ON client_phones(client_id) WHERE is_primary = 1 AND archived_at IS NULL;
```

### D.8 `client_addresses` (NUEVA)

```sql
CREATE TABLE client_addresses (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    client_id       TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    type            TEXT NOT NULL DEFAULT 'home',
    -- 'home' / 'work' / 'event' / 'billing' / 'other'
    is_primary      INTEGER NOT NULL DEFAULT 0,
    line1           TEXT NOT NULL,
    line2           TEXT,
    city            TEXT,
    state           TEXT,
    postal_code     TEXT,
    country         TEXT NOT NULL DEFAULT 'Guatemala',
    lat             REAL,
    lng             REAL,
    archived_at     TEXT,
    created_at      TEXT NOT NULL,
    CHECK (is_primary IN (0, 1)),
    CHECK (type IN ('home','work','event','billing','other')),
    FOREIGN KEY (tenant_id, client_id)
        REFERENCES clients(tenant_id, id)
        ON DELETE RESTRICT
);
```

**V4 fix:** dirección estructurada (no se concatena en `notes`).

### D.9 `projects`

```sql
CREATE TABLE projects (
    id                    TEXT PRIMARY KEY,
    tenant_id             TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id            TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    name                  TEXT NOT NULL,
    type                  TEXT NOT NULL DEFAULT 'boda',
    source                TEXT,
    form_id               TEXT,

    event_date            TEXT,
    event_time            TEXT,
    event_end_date        TEXT,
    event_end_time        TEXT,
    location_name         TEXT,
    location_address      TEXT,
    location_lat          REAL,
    location_lng          REAL,

    -- Estados (sin 'archived'; se maneja con archived_at)
    commercial_status     TEXT NOT NULL DEFAULT 'new_lead',
    -- 'new_lead','contacted','proposal_sent','follow_up','accepted',
    -- 'rejected','expired'
    operational_status    TEXT NOT NULL DEFAULT 'lead',
    -- 'lead','confirmed','pre_production','event_ready','event_completed',
    -- 'editing','gallery_preparation','gallery_published','delivered',
    -- 'completed','cancelled'

    job_accepted_at       TEXT,
    job_accepted_via      TEXT,

    -- SIN workflow_template_id ni workflow_instance_id aquí.
    -- El workflow activo se obtiene de workflow_instances.project_id.

    -- booked_value_units: valor del paquete en el momento de aceptación.
    -- Es un snapshot. Las invoices pueden tener valores distintos (descuentos,
    -- ajustes posteriores). NO es fuente de verdad financiera.
    -- Las invoices son la fuente de verdad.
    booked_value_units    INTEGER,

    -- Paquete contratado
    package_id            TEXT,
    package_name_snapshot TEXT,

    completed_at          TEXT,
    cancelled_at          TEXT,
    cancellation_reason   TEXT,
    archived_at           TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,

    CHECK (commercial_status IN
        ('new_lead','contacted','proposal_sent','follow_up','accepted',
         'rejected','expired')),
    CHECK (operational_status IN
        ('lead','confirmed','pre_production','event_ready','event_completed',
         'editing','gallery_preparation','gallery_published','delivered',
         'completed','cancelled')),
    CHECK (job_accepted_via IS NULL OR job_accepted_via IN
        ('quote_accepted','contract_signed','first_payment_received','manual')),
    CHECK (booked_value_units IS NULL OR booked_value_units >= 0),
    CHECK (archived_at IS NULL OR archived_at >= created_at)
);

-- UNIQUE constraint que faltaba en V3 (estaba en el índice único)
CREATE UNIQUE INDEX uq_projects_tenant_company_id
    ON projects(tenant_id, company_id, id);

CREATE INDEX idx_projects_company_status
    ON projects(company_id, commercial_status) WHERE archived_at IS NULL;
CREATE INDEX idx_projects_event_date
    ON projects(company_id, event_date) WHERE archived_at IS NULL;
```

**V4 fixes:**
- Eliminados `workflow_template_id` y `workflow_instance_id`.
- `commercial_status` y `operational_status` NO incluyen `archived`.
- `booked_value_units` se renombró de `price_total_units` y se
  documentó como snapshot.
- Agregado UNIQUE INDEX que faltaba para que las FKs compuestas
  funcionen.

### D.10 `project_clients`

```sql
CREATE TABLE project_clients (
    project_id          TEXT NOT NULL,
    tenant_id           TEXT NOT NULL,
    client_id           TEXT NOT NULL,
    role                TEXT NOT NULL DEFAULT 'participant',
    -- 'novia' / 'novio' / 'contacto_principal' / 'contacto_facturacion'
    -- / 'contacto_portal' / 'wedding_planner' / 'familiar' / 'otro'
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
    -- FK compuesta garantiza que el client está en el mismo tenant que el project
    FOREIGN KEY (tenant_id, project_id)
        REFERENCES projects(tenant_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, client_id)
        REFERENCES clients(tenant_id, id)
        ON DELETE RESTRICT,
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

**V4 fix:** FKs compuestas para garantizar `tenant_id` consistente.

**Decisión Kevin (MAMA + flags):** permitido. Una fila por persona con
`role='mama'` + `is_billing_contact=1` + `is_portal_contact=1`. La
aplicación debe validar que exista al menos un `is_primary=1` antes de
confirmar el job.

### D.11 `quotes`

```sql
CREATE TABLE quotes (
    id                    TEXT PRIMARY KEY,
    tenant_id             TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id            TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id            TEXT NOT NULL,
    client_id             TEXT NOT NULL,
    template_id           TEXT,
    -- number se genera vía sequence_counters, NO desde plan_pago
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
    -- FKs compuestas: client debe estar en el mismo tenant
    FOREIGN KEY (tenant_id, company_id, project_id)
        REFERENCES projects(tenant_id, company_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, client_id)
        REFERENCES clients(tenant_id, id)
        ON DELETE RESTRICT,
    -- FK compuesta con project_clients garantiza que el client está vinculado al project
    FOREIGN KEY (project_id, client_id)
        REFERENCES project_clients(project_id, client_id)
        ON DELETE RESTRICT,
    CHECK (type IN ('fixed', 'pick_choose')),
    CHECK (status IN
        ('draft','sent','viewed','accepted','declined','expired',
         'superseded','cancelled')),
    CHECK (subtotal_units >= 0),
    CHECK (total_units >= 0)
);
```

**V4 fix:** FK a `project_clients(project_id, client_id)` garantiza
que la cotización use un cliente vinculado al proyecto.

### D.12 `quote_items`

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
    FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE RESTRICT,
    CHECK (quantity > 0),
    CHECK (price_units >= 0),
    CHECK (subtotal_units >= 0)
);
```

### D.13 `payment_schedule_templates`

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

### D.14 `payment_schedule_rules`

```sql
CREATE TABLE payment_schedule_rules (
    id                          TEXT PRIMARY KEY,
    template_id                 TEXT NOT NULL,
    tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    order_index                 INTEGER NOT NULL,
    description                 TEXT,
    -- XOR: exactamente uno de los dos. CHECK reforzado
    percentage_bps               INTEGER,
    amount_units                INTEGER,
    -- Si anchor_event = 'fixed_date', fixed_due_date obligatorio
    anchor_event                TEXT NOT NULL,
    anchor_offset_days          INTEGER NOT NULL DEFAULT 0,
    fixed_due_date              TEXT,
    active                      INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (template_id)
        REFERENCES payment_schedule_templates(id) ON DELETE CASCADE,
    CHECK (
        (percentage_bps IS NOT NULL AND amount_units IS NULL)
        OR
        (percentage_bps IS NULL AND amount_units IS NOT NULL)
    ),
    CHECK (percentage_bps IS NULL OR percentage_bps BETWEEN 0 AND 10000),
    CHECK (amount_units IS NULL OR amount_units >= 0),
    CHECK (anchor_event IN
        ('quote_accepted','job_accepted','event_date','gallery_delivered',
         'fixed_date')),
    CHECK (
        (anchor_event = 'fixed_date' AND fixed_due_date IS NOT NULL)
        OR
        (anchor_event != 'fixed_date' AND fixed_due_date IS NULL)
    ),
    UNIQUE (template_id, order_index)
);
```

**V4 fix:** CHECK XOR para porcentaje vs monto, y `fixed_due_date`
obligatorio solo si `anchor_event = 'fixed_date'`.

### D.15 `invoices`

```sql
CREATE TABLE invoices (
    id                    TEXT PRIMARY KEY,
    tenant_id             TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id            TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id            TEXT NOT NULL,
    client_id             TEXT NOT NULL,
    quote_id              TEXT,
    payment_schedule_id   TEXT,
    number                TEXT NOT NULL,
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
    snapshot_hash         TEXT,
    pdf_url               TEXT,
    created_at            TEXT NOT NULL,
    archived_at           TEXT,
    UNIQUE (company_id, number),
    -- FKs compuestas
    FOREIGN KEY (tenant_id, company_id, project_id)
        REFERENCES projects(tenant_id, company_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, client_id)
        REFERENCES clients(tenant_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (project_id, client_id)
        REFERENCES project_clients(project_id, client_id)
        ON DELETE RESTRICT,
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

### D.16 `invoice_items`

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

### D.17 `payment_installments`

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
    late_since        TEXT,
    notes             TEXT,
    created_at        TEXT NOT NULL,
    archived_at       TEXT,
    UNIQUE (invoice_id, number),
    FOREIGN KEY (tenant_id, company_id, project_id)
        REFERENCES projects(tenant_id, company_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, client_id)
        REFERENCES clients(tenant_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE RESTRICT,
    CHECK (number BETWEEN 1 AND total_installments),
    CHECK (amount_units >= 0)
);
```

### D.18 `payment_transactions`

```sql
CREATE TABLE payment_transactions (
    id                          TEXT PRIMARY KEY,
    tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id                  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id                  TEXT NOT NULL,
    client_id                   TEXT NOT NULL,
    invoice_id                  TEXT NOT NULL,
    -- installment_id ahora opcional: payment_allocations vincula tx a installment
    installment_id              TEXT REFERENCES payment_installments(id) ON DELETE RESTRICT,
    original_transaction_id     TEXT REFERENCES payment_transactions(id) ON DELETE RESTRICT,
    transaction_type            TEXT NOT NULL DEFAULT 'payment',
    amount_units                INTEGER NOT NULL,
    currency_code               TEXT NOT NULL,
    currency_exponent           INTEGER NOT NULL,
    date                        TEXT NOT NULL,
    method                      TEXT NOT NULL,
    external_reference          TEXT,
    provider                    TEXT,
    provider_transaction_id     TEXT,
    -- Para idempotencia de webhooks y evitar duplicados
    idempotency_key             TEXT NOT NULL UNIQUE,
    status                      TEXT NOT NULL DEFAULT 'pending',
    receipt_url                 TEXT,
    notes                       TEXT,
    created_by_user_id          TEXT,
    created_at                  TEXT NOT NULL,
    archived_at                 TEXT,
    FOREIGN KEY (tenant_id, company_id, project_id)
        REFERENCES projects(tenant_id, company_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, client_id)
        REFERENCES clients(tenant_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE RESTRICT,
    CHECK (transaction_type IN ('payment', 'refund')),
    CHECK (status IN ('pending', 'confirmed', 'failed', 'reversed')),
    CHECK (amount_units > 0),
    CHECK (original_transaction_id IS NULL OR transaction_type = 'refund'),
    -- Restricción de unicidad por proveedor: el mismo provider_transaction_id
    -- no puede existir dos veces para la misma company
    UNIQUE (tenant_id, company_id, provider, provider_transaction_id)
);
```

### D.19 `payment_allocations` (NUEVA)

```sql
CREATE TABLE payment_allocations (
    id                      TEXT PRIMARY KEY,
    tenant_id               TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id              TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    transaction_id          TEXT NOT NULL REFERENCES payment_transactions(id) ON DELETE RESTRICT,
    installment_id          TEXT NOT NULL REFERENCES payment_installments(id) ON DELETE RESTRICT,
    amount_units            INTEGER NOT NULL,
    created_at              TEXT NOT NULL,
    CHECK (amount_units > 0),
    UNIQUE (transaction_id, installment_id)
);
CREATE INDEX idx_pa_installment ON payment_allocations(installment_id);
```

**Regla:** una transacción puede cubrir varias cuotas. Se crea una
`payment_allocation` por cada cuota. La suma de allocations de una
transacción debe igualar `transaction.amount_units` (validado en
aplicación).

### D.20 `products`

```sql
CREATE TABLE products (
    id                TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id        TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    type              TEXT NOT NULL,
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

### D.21 `workflow_template_families` (NUEVA)

```sql
CREATE TABLE workflow_template_families (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    name            TEXT NOT NULL,
    description     TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    CHECK (active IN (0, 1))
);
```

### D.22 `workflow_template_versions` (NUEVA)

```sql
CREATE TABLE workflow_template_versions (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    family_id       TEXT NOT NULL REFERENCES workflow_template_families(id) ON DELETE RESTRICT,
    version         INTEGER NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'dynamic',
    notes           TEXT,
    published_at    TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE (family_id, version),
    CHECK (mode IN ('dynamic', 'frozen')),
    CHECK (version >= 1)
);
```

**Una familia puede tener N versiones. Cada `workflow_instance`
guarda `template_version` y nunca se referencia a `family_id`
directamente. Esto es versionado REAL.**

### D.23 `workflow_task_template_versions` (NUEVA)

```sql
CREATE TABLE workflow_task_template_versions (
    id                          TEXT PRIMARY KEY,
    tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id                  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    template_version_id         TEXT NOT NULL REFERENCES workflow_template_versions(id) ON DELETE RESTRICT,
    stage                       TEXT NOT NULL,
    order_index                 INTEGER NOT NULL,
    name                        TEXT NOT NULL,
    description                 TEXT,
    action_type                 TEXT NOT NULL,
    -- 'send_email' / 'send_contract' / 'send_invoice' / 'send_gallery'
    -- / 'change_status' / 'create_task' / 'notify_owner' / 'archive' / 'noop'
    action_config_json          TEXT NOT NULL DEFAULT '{}',
    -- Schema JSON validado por action_type. Ejemplos:
    -- {"email_template_id": "tpl-123"}
    -- {"commercial_status": "accepted", "operational_status": "confirmed"}
    -- {"invoice_action": "send_existing"}
    email_template_id           TEXT,
    contract_template_id        TEXT,
    questionnaire_template_id   TEXT,
    due_rule_mode               TEXT NOT NULL DEFAULT 'manual',
    due_rule_anchor             TEXT,
    due_rule_amount             INTEGER,
    due_rule_unit               TEXT,
    due_rule_direction         TEXT,
    active                      INTEGER NOT NULL DEFAULT 1,
    UNIQUE (template_version_id, order_index),
    CHECK (stage IN ('lead', 'production', 'post_production')),
    CHECK (action_type IN
        ('send_email','send_contract','send_invoice','send_gallery',
         'change_status','create_task','notify_owner','archive','noop')),
    CHECK (due_rule_mode IN ('manual', 'after_creation', 'after_event',
                              'after_anchor')),
    CHECK (due_rule_unit IS NULL OR due_rule_unit IN ('minutes','hours','days','weeks','months')),
    CHECK (due_rule_direction IS NULL OR due_rule_direction IN ('before','after'))
);
```

**Decisión V4:** el `action_type` y `action_config_json` van juntos.
Cuando se ejecuta el task, se valida que `action_config_json` tenga
los campos correctos para `action_type`.

### D.24 `workflow_instances`

```sql
CREATE TABLE workflow_instances (
    id                          TEXT PRIMARY KEY,
    tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id                  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id                  TEXT NOT NULL,
    template_family_id          TEXT NOT NULL,
    template_version_id         TEXT NOT NULL,
    template_version            INTEGER NOT NULL,
    mode                        TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'active',
    started_at                  TEXT NOT NULL,
    completed_at                TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    FOREIGN KEY (template_version_id)
        REFERENCES workflow_template_versions(id) ON DELETE RESTRICT,
    CHECK (status IN ('active','paused','completed','cancelled'))
);
CREATE UNIQUE INDEX uq_project_active_workflow
    ON workflow_instances(project_id) WHERE status IN ('active', 'paused');
```

### D.25 `workflow_task_instances`

```sql
CREATE TABLE workflow_task_instances (
    id                          TEXT PRIMARY KEY,
    tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id                  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    workflow_instance_id        TEXT NOT NULL,
    task_template_version_id    TEXT NOT NULL,
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
    FOREIGN KEY (task_template_version_id)
        REFERENCES workflow_task_template_versions(id) ON DELETE RESTRICT,
    CHECK (status IN ('pending','ready','running','done','skipped','failed')),
    CHECK (retry_count >= 0)
);
CREATE INDEX idx_wti_scheduled
    ON workflow_task_instances(company_id, scheduled_at)
    WHERE status = 'pending';
```

**Importante:** los campos `due_rule_*` se copian del template
versionado. NUNCA se consulta el template original para ejecutar.

### D.26 `processed_events`

```sql
CREATE TABLE processed_events (
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id        TEXT,  -- NULL si es tenant-wide
    idempotency_key   TEXT NOT NULL,
    event_type        TEXT NOT NULL,
    entity_type       TEXT NOT NULL,
    entity_id         TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'processing',
    -- 'processing' / 'completed' / 'failed'
    attempts          INTEGER NOT NULL DEFAULT 0,
    result_payload    TEXT,
    last_error        TEXT,
    request_hash      TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    started_at        TEXT,
    completed_at      TEXT,
    PRIMARY KEY (tenant_id, idempotency_key),
    CHECK (status IN ('processing', 'completed', 'failed')),
    CHECK (attempts >= 0)
);
```

**V4 fix:** `request_hash` se incluye para detectar reuso de clave con
datos diferentes.

### D.27 `outbox_events`

```sql
CREATE TABLE outbox_events (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    event_type      TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    handler_name    TEXT NOT NULL,
    payload         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    -- 'pending' / 'processing' / 'delivered' / 'failed' / 'dead_letter'
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 5,
    available_at    TEXT NOT NULL,
    processed_at    TEXT,
    last_error      TEXT,
    locked_at       TEXT,  -- cuando un worker tomó el lock
    locked_by       TEXT,  -- identificador del worker
    dedupe_key      TEXT NOT NULL,
    correlation_id  TEXT,  -- para agrupar eventos relacionados
    created_at      TEXT NOT NULL,
    CHECK (status IN ('pending','processing','delivered','failed','dead_letter')),
    CHECK (attempts >= 0),
    CHECK (attempts <= max_attempts),
    CHECK (max_attempts > 0),
    UNIQUE (tenant_id, dedupe_key)
);
CREATE INDEX idx_outbox_pending
    ON outbox_events(status, available_at) WHERE status = 'pending';
```

**Ciclo del outbox:**

```
pending
  → processing (worker toma lock con locked_at + locked_by)
    → delivered
    → failed → pending (con available_at futuro) o → dead_letter
    → dead_letter (después de max_attempts)
```

### D.28 `automation_runs`

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
    -- 'pending' / 'running' / 'success' / 'failed' / 'skipped'
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

### D.29 `activity_log`

```sql
CREATE TABLE activity_log (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id      TEXT,
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

### D.30 `mail_log`

```sql
CREATE TABLE mail_log (
    id                  TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id          TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id          TEXT REFERENCES projects(id) ON DELETE RESTRICT,
    client_id           TEXT REFERENCES clients(id) ON DELETE RESTRICT,
    template_id         TEXT REFERENCES email_templates(id) ON DELETE RESTRICT,
    subject             TEXT NOT NULL,
    body_snapshot       TEXT NOT NULL,
    from_email          TEXT,
    to_email            TEXT NOT NULL,
    cc_emails           TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
    external_message_id TEXT,
    sent_at             TEXT,
    delivered_at        TEXT,
    opened_at           TEXT,
    clicked_at          TEXT,
    replied_at          TEXT,
    error               TEXT,
    idempotency_key     TEXT NOT NULL UNIQUE,
    CHECK (status IN ('pending','sent','delivered','opened','clicked','bounced','failed'))
);
```

**V4 fix:** `mail_log` SÍ se crea en MVP (Kevin confirmó que el
workflow incluye `send_email`).

### D.31 `calendar_events`

```sql
CREATE TABLE calendar_events (
    id                          TEXT PRIMARY KEY,
    tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id                  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id                  TEXT,
    type                        TEXT NOT NULL,
    title                       TEXT NOT NULL,
    start_date                  TEXT,  -- para all_day
    start_at                    TEXT,  -- para no all_day
    end_date                    TEXT,
    end_at                      TEXT,
    all_day                     INTEGER NOT NULL DEFAULT 0,
    timezone                    TEXT,  -- obligatorio si no all_day
    location                    TEXT,
    notes                       TEXT,
    external_calendar_event_id  TEXT,
    created_by_user_id          TEXT,
    created_at                  TEXT NOT NULL,
    CHECK (type IN ('lead_meeting','event','session','payment','task','contract_signing')),
    CHECK (all_day IN (0, 1)),
    CHECK (
        (all_day = 1 AND start_date IS NOT NULL AND start_at IS NULL
            AND timezone IS NULL)
        OR
        (all_day = 0 AND start_at IS NOT NULL AND timezone IS NOT NULL
            AND start_date IS NULL)
    )
);
CREATE INDEX idx_calendar_start ON calendar_events(company_id, start_at);
```

**V4 fix:** validación CHECK para garantizar que `all_day=1` use
`start_date` (sin tiempo) y `all_day=0` use `start_at` (con tiempo y
timezone).

### D.32 `email_templates`

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

### D.33 `legacy_record_map`

```sql
CREATE TABLE legacy_record_map (
    id                TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    source_file       TEXT NOT NULL,
    legacy_id         TEXT NOT NULL,
    entity_type       TEXT NOT NULL,
    -- Nullable: si la migración falla y el registro queda en
    -- review_needed, new_entity_id puede ser NULL.
    new_entity_id     TEXT,
    migration_status  TEXT NOT NULL DEFAULT 'review_needed',
    -- 'imported' / 'merged' / 'archived' / 'skipped' / 'review_needed' / 'failed'
    notes             TEXT,
    migrated_at       TEXT NOT NULL,
    UNIQUE (tenant_id, source_file, legacy_id, entity_type),
    CHECK (migration_status IN
        ('imported','merged','archived','skipped','review_needed','failed')),
    CHECK (
        (migration_status IN ('imported','merged','archived')
         AND new_entity_id IS NOT NULL)
        OR
        (migration_status IN ('skipped','review_needed','failed')
         AND new_entity_id IS NULL)
    )
);
```

**V4 fix:** `new_entity_id` nullable. CHECK que garantiza coherencia
entre `new_entity_id IS NULL` y `migration_status IN
('skipped','review_needed','failed')`.

### D.34 `settings`

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

### D.35 `sequence_counters`

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

---

## E. Mapeo legacy (corregido V4)

### E.1 `clients.json` (13 registros)

| Campo original | Tabla destino | Campo destino | Transformación | Notas |
|---|---|---|---|---|
| `id` | `legacy_record_map` | `legacy_id` | entity_type='client' | preserva |
| `id` | `clients` | `id` | Mantener mismo ID | no regenerar |
| `first_name` | `clients` | `first_name` | directo | |
| `last_name` | `clients` | `last_name` | directo | |
| `email` | `client_emails` | `value_raw` | directo | |
| `email` | `client_emails` | `value_normalized` | trim+lowercase | SIN eliminar +alias |
| `email` | `client_emails` | `is_primary` | 1 si es el primero | |
| `phone` | `client_phones` | `value_raw` | directo | |
| `phone` | `client_phones` | `value_normalized` | solo dígitos | |
| `phone` | `client_phones` | `is_primary` | 1 si es el primero | |
| `address` | `client_addresses` | `line1, line2, city, country` | parsear texto | **V4 fix: no concatenar en notes** |
| `created` | `clients` | `created_at` | ISO + "T00:00:00Z" | |
| `estado` | — | — | Se IGNORA | no usado |

### E.2 `leads.json` (7 registros)

| Campo | Tabla destino | Campo destino |
|---|---|---|
| `id` | `legacy_record_map` | `legacy_id` (entity_type='project') |
| `id` | `projects` | `id` (mismo ID) |
| `nombre` | `projects` | `name` |
| `email` | `client_emails` | crea client si no existe |
| `telefono` | `client_phones` | crea client si no existe |
| `locacion` | `projects` | `location_name` (texto), `client_addresses` si parseable |
| `fecha_tentativa` | `projects` | `event_date` |
| `tipo_evento` | `projects` | `type` |
| `fuente` | `projects` | `source` |
| `status` | `projects` | `commercial_status` (mapping: 'Convertido' → 'accepted') |
| `mail_status` | `mail_log` | `status` | **V4 fix: a mail_log, NO a outbox** |
| `next_task` | `workflow_task_instances` | `name` (derivado) |
| `lead_id_job` | `projects` | `job_accepted_at` + `job_accepted_via='manual'` |
| `created` | `projects` | `created_at` |

### E.3 `jobs.json` (9 registros)

| Campo | Tabla destino | Campo destino | Notas |
|---|---|---|---|
| `id` | `legacy_record_map` | `legacy_id` (entity_type='project') | preserva TODOS los IDs |
| `nombre` | `projects` | `name` | si hay duplicado, marcar `review_needed` |
| `boda_date` | `projects` | `event_date` | |
| `location` | `projects` | `location_name` | |
| `lead_id` | `legacy_record_map` | `legacy_id` (entity_type='project_source_lead') | |
| `client_id` | `project_clients` | crear entry con role='participant' | **V4 fix** |
| `package` | `projects` | `package_name_snapshot` | |
| `price_total` | `projects` | `booked_value_units` | **V4 fix: nombre** |
| `status` | `projects` | `operational_status` | **V4 fix: 'En produccion' requiere revisión manual** |

**V4 fix:** los registros de `jobs.json` con `status='En produccion'`
**NO** se migran automáticamente a `editing`. Se marcan con
`migration_status='review_needed'` y `notes='ambiguous_status'`.
Kevin decide.

### E.4 `quotes.json` (4 registros)

| Campo | Tabla destino | Campo destino |
|---|---|---|
| `id` | `legacy_record_map` | `legacy_id` (entity_type='quote') |
| `lead_id` | `quotes` | `project_id` (vía source_lead_id) |
| `paquete_nombre` | `quote_items` | `name` (1 item) |
| `precio_total` | `quotes` | `total_units` (validar con regex) |
| `plan_pago` | `payment_installments` | número de installments | **V4 fix: NO a `quotes.number`** |
| `cuota_monto` | `payment_installments` | `amount_units` (validar) |
| `status` | `quotes` | `status` directo |
| `items_snapshot` | `quote_items` | múltiples rows |
| `aceptada_en` | `quotes` | `accepted_at` |
| `job_id` | `legacy_record_map` | (información de vínculo) |

**V4 fix:** `plan_pago` es el NÚMERO de installments que se generan
desde el payment_schedule_template. NO es el `number` de la quote
(que es secuencial).

### E.5 `payments.json` (6 registros) — SIN fabricar invoices

**Estrategia V4 (corregida según Kevin):**

1. **NO se crean invoices durante la migración.**
2. Cada registro de `payments.json` se evalúa individualmente.
3. Se crea un `legacy_record_map` con `migration_status='review_needed'`
   para cada uno.
4. Kevin revisa y decide.

**V4 fix:** NO se agrupan pagos por job. NO se suman para crear una
invoice.

### E.6 `workflow_instances.json` + `workflow_history.json`

| Campo | Tabla destino | Campo destino |
|---|---|---|
| (todo) | `workflow_instances` + `workflow_task_instances` + `automation_runs` | mapping uno-a-uno |
| `id` | `legacy_record_map` | `legacy_id` (entity_type='workflow_instance') |

**V4 fix:** se necesita una `workflow_template_families` + primera
`workflow_template_versions` para que las instances tengan FK
válidas. La migración crea 1 familia "Workflow Importado" y 1 versión
"v1" que las instances pueden referenciar.

### E.7 `mail_log.json`

| Campo | Tabla destino | Notas |
|---|---|---|
| `id` | `mail_log` (nueva) | **V4 fix: SÍ se crea tabla mail_log** |
| `id` | `legacy_record_map` | `legacy_id` (entity_type='mail_log') |
| (todo) | `mail_log` | agregar `tenant_id` y `company_id` del project |

### E.8 `calendar.json`

| Campo | Tabla destino |
|---|---|
| (todo) | `calendar_events` |

**V4 fix:** validar `all_day` vs `start_at` en la importación.

### E.9 `tenants.json`

| Archivo | Tabla destino |
|---|---|
| `tenants.json` | 1 row en `tenants` (Kevin) + 2 rows en `companies` (norkevin, astral) + 1 row en `users` (kevin) + 2 rows en `user_company_memberships` |

### E.10 `email_templates.json`

| Archivo | Tabla destino |
|---|---|
| `email_templates.json` | `email_templates` (1 a 1) |

### E.11 `packages.json`

| Archivo | Tabla destino |
|---|---|
| `packages.json` | `products` (con `type='package'`, `price_units = price * 100`) |

### E.12 `team.json`

| Archivo | Tabla destino | Notas |
|---|---|---|
| `team.json` | **NO migra a `users`** | **V4 fix: team members no son users del CRM** |
| (futuro) | `team_members` (tabla nueva) | Se crea cuando se necesite |

**Decisión V4:** `team.json` representa proveedores o equipo que NO
necesitan acceso al CRM. Quedan en una tabla futura. Por ahora
`legacy_record_map` registra su existencia.

### E.13 `settings.json`

| Archivo | Tabla destino |
|---|---|
| `settings.json` | `settings` (key-value por company) |

### E.14 `crm.db`

[CONFIRMADO EN CÓDIGO] `crm.db` está VACÍO. Se conserva en el backup.
Alembic lo inicializa en Fase 1.

---

## F. Reporte de conflictos (V4 — sin fabricar invoices)

### F.1 Clientes duplicados (importar sin fusionar)

| ID | Nombre | Email | Status migración |
|---|---|---|---|
| `client-1` | Maria Lopez | maria.lopez@gmail.com | imported |
| `client-2` | Ana Ramirez | ana.ramirez@yahoo.com | imported (older) |
| `client-0165833f` | Ana Ramirez | ana.ramirez@yahoo.com | **review_needed** |
| `client-97399c98` | Ana Ramirez | ana.ramirez@yahoo.com | **review_needed** |
| `client-9d625381` | KEVIN LEMUS | kevinnoriega01@gmail.com | **review_needed** |
| `client-33fcc706` | Kevin Daniel Lemus Noriega | norkevinfoto@gmail.com | **review_needed** |
| otros 7 | varios | varios | imported |

**V4 fix:** NO se fusionan ni se archivan. Kevin revisa cada uno y
decide vía la UI (futura). El reporte queda en `legacy_record_map`.

### F.2 Jobs duplicados (importar sin archivar)

| ID | Status importación |
|---|---|
| `boda-1` | imported |
| `boda-2` | imported (preserva como principal) |
| `boda-3d559b03` | **review_needed** |
| `boda-009a8781` | **review_needed** |
| `boda-71c243ed` | imported |
| `boda-9ac2b517` | **review_needed** |

`projects.status` para los `review_needed` queda con
`commercial_status='new_lead'` y `operational_status='lead'` hasta que
Kevin decida.

### F.3 Leads sin job

| ID | Status importación |
|---|---|
| `lead-1` | imported, comercial=accepted (con job_accepted_at heredado) |
| `lead-2` | imported, comercial=accepted |
| `lead-3f0bf51a` | imported, comercial=new_lead |
| `lead-6b6477cc` | imported, comercial=new_lead |
| `lead-f27ecff7` | imported, comercial=new_lead |
| otros 2 | imported |

### F.4 Payments (6 registros) — TODOS quedan en `review_needed`

| ID | Clasificación propuesta | Revisión |
|---|---|---|
| `pay-1` | probable transacción | sí |
| `pay-2` | probable transacción | sí |
| `pay-3` | probable cuota pendiente | sí |
| `pay-4` | probable cuota atrasada | sí |
| `pay-24b2c426` | ambiguo (monto bajo) | sí |
| `pay-...` | (se detectan al importar) | sí |

**V4 fix:** NO se crean invoices, installments, ni transactions
durante la migración. Todo queda en `legacy_record_map` con
`migration_status='review_needed'`.

---

## G. Criterio de éxito de la migración (V4)

La migración es aceptable solo cuando:

1. ✅ Ningún JSON original modificado.
2. ✅ Backup verificado en `backups/<timestamp>/` con SHA-256.
3. ✅ `crm.db` original respaldado (aunque esté vacío).
4. ✅ Schema creado con éxito en SQLite temporal (en `validate_schema_v4.py`).
5. ✅ `PRAGMA foreign_key_check` retorna 0 errores.
6. ✅ Pruebas de cruce entre companies PASAN (rechazan inserts inválidos).
7. ✅ Prueba de doble aceptación: solo crea 1 project + 1 acceptance.
8. ✅ Prueba de rollback transaccional: error a mitad no deja escrituras parciales.
9. ✅ Todos los registros en `clients.json`, `jobs.json`, `quotes.json`,
   `payments.json` tienen entrada en `legacy_record_map`.
10. ✅ Los 6 registros de `payments.json` están en `review_needed`.
11. ✅ Las pruebas unitarias de V4 (`validate_schema_v4.py`) corren sin errores.

---

## H. Backup y rollback (V4 — desde Fase 1)

### H.1 Backup ANTES de Fase 1 (no Fase 6)

```powershell
# backup_fase_0.ps1
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = "C:\Users\fotov\.openclaw\workspace\crm_norkevin\backups\$timestamp"
New-Item -ItemType Directory -Force -Path $backupDir

# 1. Backup de JSONs
Copy-Item -Recurse "C:\Users\fotov\.openclaw\workspace\crm_norkevin\data\*.json" $backupDir

# 2. Backup de crm.db (conservar aunque esté vacío)
if (Test-Path "C:\Users\fotov\.openclaw\workspace\crm_norkevin\data\crm.db") {
    Copy-Item "C:\Users\fotov\.openclaw\workspace\crm_norkevin\data\crm.db" "$backupDir\crm.db"
}

# 3. SHA-256
Get-ChildItem $backupDir | ForEach-Object {
    $hash = Get-FileHash -Algorithm SHA256 $_.FullName
    "$($_.Name) $($hash.Hash)" | Out-File "$backupDir\checksums.txt" -Append
}

# 4. NO commit ni tag (repositorio puede no ser privado)

Write-Host "Backup completo en $backupDir"
```

### H.2 Punto de no retorno

| Estado | Permite rollback seguro |
|---|---|
| Antes de Fase 1 | ✅ Sí (JSONs intactos, crm.db sin uso) |
| Después de Fase 1 (solo schema) | ✅ Sí (crm.db vacío) |
| Después de Fase 2 (importación) | ✅ Sí (datos en `legacy_record_map`, JSONs intactos) |
| Después de Fase 5 (modo de prueba) | ✅ Sí (crm.db solo lectura) |
| **Después de Fase 6 (escritura)** | ⚠️ **PUNTO DE NO RETORNO** |
| Después de Fase 6 + datos reales | ❌ NO se puede volver a JSON sin perder datos |

### H.3 Rollback después de Fase 6 (si falla)

Si la fase 6 falla DESPUÉS de escribir datos:

1. **NO** se vuelve a JSON. No se puede.
2. Se conserva crm.db y se corrige el código.
3. Se crea un script de export inverso a JSON (mapea los registros
   nuevos a formato legacy).
4. Se prueba con una copia.
5. Se ejecuta solo si el equipo aprueba.

### H.4 Backup con WAL activo

```python
# Usar sqlite3 .backup API (no copiar archivo directamente)
import sqlite3
src = sqlite3.connect("data/crm.db")
dst = sqlite3.connect("backups/crm_safe.db")
src.backup(dst)
dst.close()
src.close()
```

---

## I. Confirmación literal de no-modificación (V4)

| Item | Estado |
|---|---|
| Código de producción modificado | **NO** |
| Datos modificados | **NO** |
| `crm.db` modificado | **NO** |
| Alembic ejecutado | **NO** |
| JSON modificados | **NO** |
| Tablas creadas en crm.db | **NO** |
| DDL probado en SQLite temporal | **SÍ** (en `validate_schema_v4.py`) |
| `PRAGMA foreign_key_check` | **SIN ERRORES** |
| Pruebas de cruce entre companies | **PASARON** |
| Prueba de doble aceptación | **PASÓ** |
| Prueba de rollback transaccional | **PASÓ** |

---

## J. Diferencias con V3

| V3 | V4 |
|---|---|
| FK compuesta `(tenant_id, company_id, project_id)` sin UNIQUE | `UNIQUE INDEX uq_projects_tenant_company_id` |
| Lead → Job = INSERT | Lead → Job = **UPDATE** |
| `workflow_template_id` y `workflow_instance_id` en projects | **Eliminados**. Obtenidos de `workflow_instances` |
| `archived` en CHECK | **Eliminado** de los CHECK |
| `price_total_units` en projects | Renombrado a `booked_value_units` (snapshot) |
| `legacy_record_map.new_entity_id NOT NULL` | **Nullable** |
| `payment_transactions.installment_id NOT NULL` | Nullable (resuelto con `payment_allocations`) |
| Sin tabla `payment_allocations` | **Agregada** |
| Sin versionado de workflows | `workflow_template_families` + `workflow_template_versions` + `workflow_task_template_versions` |
| Sin `client_addresses` | **Agregada** (preserva dirección estructurada) |
| Sin `action_config_json` | **Agregado** |
| `mail_log` "no se crea en MVP" | **SÍ se crea en MVP** |
| Migration fabrica invoices | **NO se fabrican invoices** |
| Backup antes de Fase 6 | Backup antes de **Fase 1** |
| Sin `validate_schema_v4.py` | **Creado** como prueba ejecutable |
| Sin `workflow_template_families` | **Agregada** |
| 31 tablas | **34 tablas** (con `client_addresses`, `payment_allocations`, las 3 de versionado) |

---

## K. Resumen para Kevin

✅ **Foreign key mismatch corregido** con `UNIQUE INDEX uq_projects_tenant_company_id`.
✅ **Lógica de aceptación corregida** (UPDATE, no INSERT).
✅ **Versionado de workflows** con families + versions.
✅ **Outbox robusto** con dedupe_key, locked_at, max_attempts.
✅ **payment_allocations** para distribuir pagos entre cuotas.
✅ **Migración sin fabricar invoices** — todo va a `review_needed`.
✅ **`validate_schema_v4.py`** como prueba ejecutable separada.
✅ **Backup desde Fase 1** (no Fase 6).
✅ **Documento completo, contradicciones resueltas.**

**Decime Kevin:**

1. ¿Aprobás V4?
2. ¿Hay algo más que falte?
3. ¿Procedo a `validate_schema_v4.py` y te lo paso corriendo para confirmar?

**Sin prisa. Espero tu OK.** 💪