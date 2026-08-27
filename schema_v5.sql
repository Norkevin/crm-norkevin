-- =============================================================================
-- SCHEMA V5 - CRM Narvis (Proyecto Kevin / Norkevin / Astral)
-- =============================================================================
-- Unica fuente de verdad del DDL. Leido y ejecutado por:
--   - validate_schema_v5.py (pruebas aisladas)
--   - verify_v5_consistency.py (verificacion de inventario)
--   - alembic upgrade head (futuro, si Kevin lo aprueba)
--
-- Convenciones:
--   - tenant_id, company_id se repiten en tablas principales (necesario para FKs compuestas)
--   - Tablas profundamente derivadas (client_emails, quote_items, etc.) NO repiten
--   - Foreign keys compuestas usan UNIQUE INDEX en (tenant_id, id) o (tenant_id, company_id, id)
--   - Refunds usan FK autorreferencial + triggers
--   - Outbox usa UNIQUE (tenant_id, dedupe_key)
--   - Idempotency usa PRIMARY KEY (tenant_id, idempotency_key)
-- =============================================================================

-- =============================================================================
-- FASE 1: ANCLAS (tablas sin dependencias circulares)
-- =============================================================================

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

CREATE TABLE users (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    email           TEXT NOT NULL,
    name            TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'viewer',
    password_hash   TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    last_login_at   TEXT,
    created_at      TEXT NOT NULL,
    archived_at     TEXT,
    UNIQUE (tenant_id, email),
    CHECK (role IN ('owner', 'admin', 'editor', 'viewer')),
    CHECK (active IN (0, 1))
);

CREATE TABLE clients (
    id                TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    first_name        TEXT NOT NULL,
    last_name         TEXT NOT NULL,
    source            TEXT,
    consent_marketing INTEGER NOT NULL DEFAULT 0,
    consent_signed_at TEXT,
    notes             TEXT,
    archived_at       TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    CHECK (consent_marketing IN (0, 1))
);

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
    commercial_status     TEXT NOT NULL DEFAULT 'new_lead',
    operational_status    TEXT NOT NULL DEFAULT 'lead',
    job_accepted_at       TEXT,
    job_accepted_via      TEXT,
    booked_value_units    INTEGER,
    package_id            TEXT,
    package_name_snapshot TEXT,
    completed_at          TEXT,
    cancelled_at          TEXT,
    cancellation_reason   TEXT,
    archived_at           TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    -- FK compuesta: company_id debe ser del mismo tenant
    FOREIGN KEY (tenant_id, company_id)
        REFERENCES companies(tenant_id, id) ON DELETE RESTRICT,
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

CREATE TABLE payment_schedule_templates (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    name          TEXT NOT NULL,
    description   TEXT,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    archived_at   TEXT,
    -- FK compuesta: company del mismo tenant
    FOREIGN KEY (tenant_id, company_id)
        REFERENCES companies(tenant_id, id) ON DELETE RESTRICT,
    CHECK (active IN (0, 1))
);

-- =============================================================================
-- FASE 2: UNIQUE INDEXes para FKs compuestas (deben existir ANTES de las FKs)
-- =============================================================================

CREATE UNIQUE INDEX uq_companies_tenant_id          ON companies(tenant_id, id);
CREATE UNIQUE INDEX uq_users_tenant_id              ON users(tenant_id, id);
CREATE UNIQUE INDEX uq_clients_tenant_id            ON clients(tenant_id, id);
CREATE UNIQUE INDEX uq_projects_tenant_id           ON projects(tenant_id, id);
CREATE UNIQUE INDEX uq_projects_tenant_company      ON projects(tenant_id, company_id, id);
CREATE UNIQUE INDEX uq_pst_tenant_company           ON payment_schedule_templates(tenant_id, company_id, id);

-- =============================================================================
-- FASE 3: Tablas compuestas y derivadas
-- =============================================================================

-- user_company_memberships: conecta user (independiente) con company (independiente)
CREATE TABLE user_company_memberships (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    role        TEXT NOT NULL DEFAULT 'viewer',
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    archived_at TEXT,
    UNIQUE (user_id, company_id),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES users(tenant_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, company_id)
        REFERENCES companies(tenant_id, id) ON DELETE RESTRICT,
    CHECK (role IN ('owner', 'admin', 'editor', 'viewer')),
    CHECK (active IN (0, 1))
);

-- project_clients: conecta project (independiente) con client (independiente)
CREATE TABLE project_clients (
    id                  TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    project_id          TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    client_id           TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    role                TEXT NOT NULL DEFAULT 'participant',
    is_primary          INTEGER NOT NULL DEFAULT 0,
    is_billing_contact  INTEGER NOT NULL DEFAULT 0,
    is_portal_contact   INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    archived_at         TEXT,
    UNIQUE (project_id, client_id),
    FOREIGN KEY (tenant_id, project_id)
        REFERENCES projects(tenant_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, client_id)
        REFERENCES clients(tenant_id, id) ON DELETE RESTRICT,
    CHECK (is_primary IN (0, 1)),
    CHECK (is_billing_contact IN (0, 1)),
    CHECK (is_portal_contact IN (0, 1))
);

-- UNIQUE INDEX requerido para que las FKs compuestas de quotes/invoices funcionen
CREATE UNIQUE INDEX uq_pc_project_id
    ON project_clients(project_id, id);

-- Estrategia A: tablas profundamente derivadas
CREATE TABLE client_emails (
    id                TEXT PRIMARY KEY,
    client_id         TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    value_raw         TEXT NOT NULL,
    value_normalized  TEXT NOT NULL,
    is_primary        INTEGER NOT NULL DEFAULT 0,
    verified_at       TEXT,
    archived_at       TEXT,
    created_at        TEXT NOT NULL,
    CHECK (is_primary IN (0, 1))
);

CREATE TABLE client_phones (
    id                TEXT PRIMARY KEY,
    client_id         TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    value_raw         TEXT NOT NULL,
    value_normalized  TEXT NOT NULL,
    is_primary        INTEGER NOT NULL DEFAULT 0,
    verified_at       TEXT,
    archived_at       TEXT,
    created_at        TEXT NOT NULL,
    CHECK (is_primary IN (0, 1))
);

CREATE TABLE client_addresses (
    id              TEXT PRIMARY KEY,
    client_id       TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    type            TEXT NOT NULL DEFAULT 'home',
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
    CHECK (type IN ('home','work','event','billing','other'))
);

-- workflow
CREATE TABLE workflow_template_families (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    name            TEXT NOT NULL,
    description     TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (tenant_id, company_id)
        REFERENCES companies(tenant_id, id) ON DELETE RESTRICT,
    CHECK (active IN (0, 1))
);

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
    FOREIGN KEY (tenant_id, company_id)
        REFERENCES companies(tenant_id, id) ON DELETE RESTRICT,
    CHECK (mode IN ('dynamic', 'frozen')),
    CHECK (version >= 1)
);

CREATE UNIQUE INDEX uq_wft_tenant_company
    ON workflow_template_versions(tenant_id, company_id, id);

CREATE TABLE workflow_task_template_versions (
    id                          TEXT PRIMARY KEY,
    template_version_id         TEXT NOT NULL REFERENCES workflow_template_versions(id) ON DELETE RESTRICT,
    stage                       TEXT NOT NULL,
    order_index                 INTEGER NOT NULL,
    name                        TEXT NOT NULL,
    description                 TEXT,
    action_type                 TEXT NOT NULL,
    action_config_json          TEXT NOT NULL DEFAULT '{}',
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

-- workflow_instances con FK compuesta tenant+company
CREATE TABLE workflow_instances (
    id                          TEXT PRIMARY KEY,
    tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id                  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id                  TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    template_version_id         TEXT NOT NULL REFERENCES workflow_template_versions(id) ON DELETE RESTRICT,
    template_version            INTEGER NOT NULL,
    mode                        TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'active',
    started_at                  TEXT NOT NULL,
    completed_at                TEXT,
    -- Project del mismo tenant+company
    FOREIGN KEY (tenant_id, company_id, project_id)
        REFERENCES projects(tenant_id, company_id, id) ON DELETE RESTRICT,
    -- Template del mismo tenant+company
    FOREIGN KEY (tenant_id, company_id, template_version_id)
        REFERENCES workflow_template_versions(tenant_id, company_id, id) ON DELETE RESTRICT,
    CHECK (status IN ('active','paused','completed','cancelled'))
);

CREATE UNIQUE INDEX uq_project_active_workflow
    ON workflow_instances(project_id) WHERE status IN ('active', 'paused');

-- workflow_task_instances: deriva de workflow_instance
CREATE TABLE workflow_task_instances (
    id                          TEXT PRIMARY KEY,
    workflow_instance_id        TEXT NOT NULL REFERENCES workflow_instances(id) ON DELETE CASCADE,
    task_template_version_id    TEXT NOT NULL REFERENCES workflow_task_template_versions(id) ON DELETE RESTRICT,
    project_id                  TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    name                        TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'pending',
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
    CHECK (status IN ('pending','ready','running','done','skipped','failed')),
    CHECK (retry_count >= 0)
);

-- products: tenant + company (independiente)
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
    FOREIGN KEY (tenant_id, company_id)
        REFERENCES companies(tenant_id, id) ON DELETE RESTRICT,
    CHECK (type IN ('product', 'package')),
    CHECK (price_units >= 0),
    CHECK (tax_rate_bps BETWEEN 0 AND 10000),
    CHECK (active IN (0, 1))
);

-- quotes: documento principal con FKs compuestas
CREATE TABLE quotes (
    id                          TEXT PRIMARY KEY,
    tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id                  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id                  TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    billing_project_client_id   TEXT NOT NULL REFERENCES project_clients(id) ON DELETE RESTRICT,
    template_id                 TEXT,
    number                      TEXT NOT NULL,
    type                        TEXT NOT NULL DEFAULT 'fixed',
    status                      TEXT NOT NULL DEFAULT 'draft',
    issue_date                  TEXT,
    due_date                    TEXT,
    subtotal_units              INTEGER NOT NULL,
    discount_units              INTEGER NOT NULL DEFAULT 0,
    tax_units                   INTEGER NOT NULL DEFAULT 0,
    total_units                 INTEGER NOT NULL,
    currency_code               TEXT NOT NULL,
    currency_exponent           INTEGER NOT NULL,
    sent_at                     TEXT,
    viewed_at                   TEXT,
    accepted_at                 TEXT,
    accepted_by_user_id         TEXT,
    acceptance_ip               TEXT,
    sent_snapshot               TEXT,
    accepted_snapshot           TEXT,
    snapshot_hash               TEXT,
    pdf_url                     TEXT,
    created_at                  TEXT NOT NULL,
    archived_at                 TEXT,
    -- quote de la misma company que el project
    FOREIGN KEY (tenant_id, company_id, project_id)
        REFERENCES projects(tenant_id, company_id, id) ON DELETE RESTRICT,
    -- billing_project_client del mismo project
    FOREIGN KEY (project_id, billing_project_client_id)
        REFERENCES project_clients(project_id, id) ON DELETE RESTRICT,
    UNIQUE (project_id, id),
    UNIQUE (company_id, number),
    CHECK (type IN ('fixed', 'pick_choose')),
    CHECK (status IN
        ('draft','sent','viewed','accepted','declined','expired',
         'superseded','cancelled')),
    CHECK (subtotal_units >= 0),
    CHECK (total_units >= 0)
);

-- quote_items: deriva de quote
CREATE TABLE quote_items (
    id              TEXT PRIMARY KEY,
    quote_id        TEXT NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
    product_id      TEXT REFERENCES products(id) ON DELETE RESTRICT,
    name            TEXT NOT NULL,
    description     TEXT,
    price_units     INTEGER NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 1,
    subtotal_units  INTEGER NOT NULL,
    discount_units  INTEGER NOT NULL DEFAULT 0,
    tax_units       INTEGER NOT NULL DEFAULT 0,
    order_index     INTEGER NOT NULL DEFAULT 0,
    CHECK (quantity > 0),
    CHECK (price_units >= 0),
    CHECK (subtotal_units >= 0)
);

-- invoices: documento principal con FKs compuestas
CREATE TABLE invoices (
    id                          TEXT PRIMARY KEY,
    tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id                  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id                  TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    billing_project_client_id   TEXT NOT NULL REFERENCES project_clients(id) ON DELETE RESTRICT,
    quote_id                    TEXT REFERENCES quotes(id) ON DELETE RESTRICT,
    payment_schedule_id         TEXT REFERENCES payment_schedule_templates(id) ON DELETE RESTRICT,
    number                      TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'draft',
    issue_date                  TEXT,
    due_date                    TEXT,
    subtotal_units              INTEGER NOT NULL,
    discount_units              INTEGER NOT NULL DEFAULT 0,
    tax_units                   INTEGER NOT NULL DEFAULT 0,
    total_units                 INTEGER NOT NULL,
    currency_code               TEXT NOT NULL,
    currency_exponent           INTEGER NOT NULL,
    sent_at                     TEXT,
    viewed_at                   TEXT,
    snapshot_hash               TEXT,
    pdf_url                     TEXT,
    created_at                  TEXT NOT NULL,
    archived_at                 TEXT,
    -- invoice de la misma company que el project
    FOREIGN KEY (tenant_id, company_id, project_id)
        REFERENCES projects(tenant_id, company_id, id) ON DELETE RESTRICT,
    -- billing del mismo project
    FOREIGN KEY (project_id, billing_project_client_id)
        REFERENCES project_clients(project_id, id) ON DELETE RESTRICT,
    -- quote del mismo project
    FOREIGN KEY (project_id, quote_id)
        REFERENCES quotes(project_id, id) ON DELETE RESTRICT,
    -- payment schedule de la misma company
    FOREIGN KEY (tenant_id, company_id, payment_schedule_id)
        REFERENCES payment_schedule_templates(tenant_id, company_id, id) ON DELETE RESTRICT,
    UNIQUE (project_id, id),
    UNIQUE (company_id, number),
    CHECK (status IN
        ('draft','issued','sent','viewed','partially_paid','paid',
         'overdue','cancelled','written_off','refunded')),
    CHECK (subtotal_units >= 0),
    CHECK (total_units >= 0)
);

-- invoice_items: deriva de invoice
CREATE TABLE invoice_items (
    id              TEXT PRIMARY KEY,
    invoice_id      TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT,
    quantity        INTEGER NOT NULL DEFAULT 1,
    unit_price_units INTEGER NOT NULL,
    subtotal_units  INTEGER NOT NULL,
    discount_units  INTEGER NOT NULL DEFAULT 0,
    tax_units       INTEGER NOT NULL DEFAULT 0,
    order_index     INTEGER NOT NULL DEFAULT 0,
    CHECK (quantity > 0),
    CHECK (unit_price_units >= 0)
);

-- payment_schedule_rules: deriva de template
CREATE TABLE payment_schedule_rules (
    id                          TEXT PRIMARY KEY,
    template_id                 TEXT NOT NULL REFERENCES payment_schedule_templates(id) ON DELETE CASCADE,
    order_index                 INTEGER NOT NULL,
    description                 TEXT,
    percentage_bps              INTEGER,
    amount_units                INTEGER,
    anchor_event                TEXT NOT NULL,
    anchor_offset_days          INTEGER NOT NULL DEFAULT 0,
    fixed_due_date              TEXT,
    active                      INTEGER NOT NULL DEFAULT 1,
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

-- payment_installments: deriva de invoice (sin tenant_id, deriva via invoice.project_id.tenant_id)
CREATE TABLE payment_installments (
    id                TEXT PRIMARY KEY,
    invoice_id        TEXT NOT NULL REFERENCES invoices(id) ON DELETE RESTRICT,
    number            INTEGER NOT NULL,
    total_installments INTEGER NOT NULL,
    due_date          TEXT NOT NULL,
    amount_units      INTEGER NOT NULL,
    late_since        TEXT,
    notes             TEXT,
    created_at        TEXT NOT NULL,
    archived_at       TEXT,
    UNIQUE (invoice_id, number),
    CHECK (number BETWEEN 1 AND total_installments),
    CHECK (amount_units >= 0)
);

-- UNIQUE INDEX requerido para FKs compuestas desde payment_allocations
CREATE UNIQUE INDEX uq_pi_invoice_id
    ON payment_installments(invoice_id, id);

-- payment_transactions: UNIQUE(invoice_id, id) para FK autorreferencial
CREATE TABLE payment_transactions (
    id                          TEXT PRIMARY KEY,
    tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id                  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id                  TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    invoice_id                  TEXT NOT NULL REFERENCES invoices(id) ON DELETE RESTRICT,
    -- installment_id se elimina de la FK directa (se asigna via payment_allocations)
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
    idempotency_key             TEXT NOT NULL UNIQUE,
    status                      TEXT NOT NULL DEFAULT 'pending',
    receipt_url                 TEXT,
    notes                       TEXT,
    created_by_user_id          TEXT,
    created_at                  TEXT NOT NULL,
    archived_at                 TEXT,
    -- FK compuesta: project del mismo tenant+company
    FOREIGN KEY (tenant_id, company_id, project_id)
        REFERENCES projects(tenant_id, company_id, id) ON DELETE RESTRICT,
    -- FK autorreferencial: refund debe ser de la misma invoice
    FOREIGN KEY (invoice_id, original_transaction_id)
        REFERENCES payment_transactions(invoice_id, id) ON DELETE RESTRICT,
    UNIQUE (invoice_id, id),
    UNIQUE (tenant_id, company_id, provider, provider_transaction_id),
    CHECK (transaction_type IN ('payment', 'refund')),
    CHECK (status IN ('pending', 'confirmed', 'failed', 'reversed')),
    CHECK (amount_units > 0),
    CHECK (
        (transaction_type = 'payment' AND original_transaction_id IS NULL)
        OR
        (transaction_type = 'refund' AND original_transaction_id IS NOT NULL)
    )
);

-- payment_allocations: conecta tx con installment de la misma invoice
CREATE TABLE payment_allocations (
    id                      TEXT PRIMARY KEY,
    invoice_id              TEXT NOT NULL,
    transaction_id          TEXT NOT NULL REFERENCES payment_transactions(id) ON DELETE RESTRICT,
    installment_id          TEXT NOT NULL,
    amount_units            INTEGER NOT NULL,
    created_at              TEXT NOT NULL,
    FOREIGN KEY (invoice_id, transaction_id)
        REFERENCES payment_transactions(invoice_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (invoice_id, installment_id)
        REFERENCES payment_installments(invoice_id, id) ON DELETE RESTRICT,
    CHECK (amount_units > 0),
    UNIQUE (transaction_id, installment_id)
);

-- processed_events: PRIMARY KEY (tenant_id, idempotency_key) para idempotencia
CREATE TABLE processed_events (
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id        TEXT REFERENCES companies(id) ON DELETE RESTRICT,
    idempotency_key   TEXT NOT NULL,
    event_type        TEXT NOT NULL,
    entity_type       TEXT NOT NULL,
    entity_id         TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'processing',
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

-- outbox_events: UNIQUE(tenant_id, dedupe_key)
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
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 5,
    available_at    TEXT NOT NULL,
    processed_at    TEXT,
    last_error      TEXT,
    locked_at       TEXT,
    locked_by       TEXT,
    dedupe_key      TEXT NOT NULL,
    correlation_id  TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (tenant_id, company_id)
        REFERENCES companies(tenant_id, id) ON DELETE RESTRICT,
    CHECK (status IN ('pending','processing','delivered','failed','dead_letter')),
    CHECK (attempts >= 0),
    CHECK (attempts <= max_attempts),
    CHECK (max_attempts > 0),
    UNIQUE (tenant_id, dedupe_key)
);

-- automation_runs
CREATE TABLE automation_runs (
    id                    TEXT PRIMARY KEY,
    tenant_id             TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id            TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    workflow_instance_id  TEXT REFERENCES workflow_instances(id) ON DELETE SET NULL,
    task_instance_id      TEXT REFERENCES workflow_task_instances(id) ON DELETE SET NULL,
    project_id            TEXT REFERENCES projects(id) ON DELETE SET NULL,
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
    FOREIGN KEY (tenant_id, company_id)
        REFERENCES companies(tenant_id, id) ON DELETE RESTRICT,
    CHECK (status IN ('pending','running','success','failed','skipped'))
);

-- activity_log
CREATE TABLE activity_log (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id      TEXT REFERENCES companies(id) ON DELETE RESTRICT,
    project_id      TEXT REFERENCES projects(id) ON DELETE SET NULL,
    client_id       TEXT REFERENCES clients(id) ON DELETE SET NULL,
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

-- mail_log
CREATE TABLE mail_log (
    id                  TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id          TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id          TEXT REFERENCES projects(id) ON DELETE SET NULL,
    client_id           TEXT REFERENCES clients(id) ON DELETE SET NULL,
    template_id         TEXT REFERENCES email_templates(id) ON DELETE SET NULL,
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
    FOREIGN KEY (tenant_id, company_id)
        REFERENCES companies(tenant_id, id) ON DELETE RESTRICT,
    CHECK (status IN ('pending','sent','delivered','opened','clicked','bounced','failed'))
);

-- settings
CREATE TABLE settings (
    tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    key           TEXT NOT NULL,
    value         TEXT,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (tenant_id, company_id)
        REFERENCES companies(tenant_id, id) ON DELETE RESTRICT,
    PRIMARY KEY (company_id, key)
);

-- sequence_counters
CREATE TABLE sequence_counters (
    tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    entity_type   TEXT NOT NULL,
    year          INTEGER NOT NULL,
    last_value    INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (tenant_id, company_id)
        REFERENCES companies(tenant_id, id) ON DELETE RESTRICT,
    PRIMARY KEY (company_id, entity_type, year),
    CHECK (entity_type IN ('quote','invoice','contract','gallery','client','project'))
);

-- calendar_events
CREATE TABLE calendar_events (
    id                          TEXT PRIMARY KEY,
    tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id                  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id                  TEXT REFERENCES projects(id) ON DELETE SET NULL,
    type                        TEXT NOT NULL,
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
    FOREIGN KEY (tenant_id, company_id)
        REFERENCES companies(tenant_id, id) ON DELETE RESTRICT,
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

-- email_templates
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
    FOREIGN KEY (tenant_id, company_id)
        REFERENCES companies(tenant_id, id) ON DELETE RESTRICT,
    CHECK (active IN (0, 1))
);

-- legacy_record_map
CREATE TABLE legacy_record_map (
    id                TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    source_file       TEXT NOT NULL,
    legacy_id         TEXT NOT NULL,
    entity_type       TEXT NOT NULL,
    new_entity_id     TEXT,
    migration_status  TEXT NOT NULL DEFAULT 'review_needed',
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

-- =============================================================================
-- FASE 4: UNIQUE INDEX PARCIALES y demas indices
-- =============================================================================

CREATE UNIQUE INDEX uq_project_primary_contact
    ON project_clients(project_id) WHERE is_primary = 1 AND archived_at IS NULL;
CREATE UNIQUE INDEX uq_project_billing_contact
    ON project_clients(project_id) WHERE is_billing_contact = 1 AND archived_at IS NULL;

CREATE INDEX idx_client_emails_norm ON client_emails(value_normalized);
CREATE UNIQUE INDEX uq_client_email_primary
    ON client_emails(client_id) WHERE is_primary = 1 AND archived_at IS NULL;
CREATE INDEX idx_client_phones_norm ON client_phones(value_normalized);
CREATE UNIQUE INDEX uq_client_phone_primary
    ON client_phones(client_id) WHERE is_primary = 1 AND archived_at IS NULL;

CREATE INDEX idx_pa_installment ON payment_allocations(installment_id);
CREATE INDEX idx_outbox_pending
    ON outbox_events(status, available_at) WHERE status = 'pending';
CREATE INDEX idx_calendar_start ON calendar_events(company_id, start_at);

-- =============================================================================
-- FASE 5: TRIGGERS
-- =============================================================================

-- Trigger 1: Validar refund en INSERT
CREATE TRIGGER trg_refund_validation_insert
BEFORE INSERT ON payment_transactions
WHEN NEW.transaction_type = 'refund'
BEGIN
    -- 1. Original debe ser payment
    SELECT CASE
        WHEN (SELECT transaction_type FROM payment_transactions
              WHERE id = NEW.original_transaction_id) != 'payment'
        THEN RAISE(ABORT, 'refund_original_must_be_payment')
    END;
    -- 2. Original debe estar confirmado
    SELECT CASE
        WHEN (SELECT status FROM payment_transactions
              WHERE id = NEW.original_transaction_id) != 'confirmed'
        THEN RAISE(ABORT, 'refund_original_not_confirmed')
    END;
    -- 3. Misma moneda y exponent
    SELECT CASE
        WHEN (SELECT currency_code FROM payment_transactions
              WHERE id = NEW.original_transaction_id) != NEW.currency_code
        THEN RAISE(ABORT, 'refund_currency_mismatch')
    END;
    SELECT CASE
        WHEN (SELECT currency_exponent FROM payment_transactions
              WHERE id = NEW.original_transaction_id) != NEW.currency_exponent
        THEN RAISE(ABORT, 'refund_exponent_mismatch')
    END;
    -- 4. Mismo invoice (autorreferencial lo valida)
    -- 5. Suma acumulada no excede original
    SELECT CASE
        WHEN (
            (SELECT amount_units FROM payment_transactions
             WHERE id = NEW.original_transaction_id)
            -
            COALESCE((SELECT SUM(amount_units) FROM payment_transactions
                      WHERE original_transaction_id = NEW.original_transaction_id
                        AND status IN ('confirmed', 'pending')
                        AND transaction_type = 'refund'), 0)
        ) < NEW.amount_units
        THEN RAISE(ABORT, 'refund_exceeds_original_payment')
    END;
END;

-- Trigger 2: Validar refund en UPDATE
CREATE TRIGGER trg_refund_validation_update
BEFORE UPDATE ON payment_transactions
WHEN NEW.transaction_type = 'refund'
BEGIN
    -- 1. Original debe ser payment
    SELECT CASE
        WHEN (SELECT transaction_type FROM payment_transactions
              WHERE id = NEW.original_transaction_id) != 'payment'
        THEN RAISE(ABORT, 'refund_original_must_be_payment')
    END;
    -- 2. Original debe estar confirmado
    SELECT CASE
        WHEN (SELECT status FROM payment_transactions
              WHERE id = NEW.original_transaction_id) != 'confirmed'
        THEN RAISE(ABORT, 'refund_original_not_confirmed')
    END;
    -- 3. Misma moneda y exponent
    SELECT CASE
        WHEN (SELECT currency_code FROM payment_transactions
              WHERE id = NEW.original_transaction_id) != NEW.currency_code
        THEN RAISE(ABORT, 'refund_currency_mismatch')
    END;
    SELECT CASE
        WHEN (SELECT currency_exponent FROM payment_transactions
              WHERE id = NEW.original_transaction_id) != NEW.currency_exponent
        THEN RAISE(ABORT, 'refund_exponent_mismatch')
    END;
    -- 4. Suma acumulada no excede original
    SELECT CASE
        WHEN (
            (SELECT amount_units FROM payment_transactions
             WHERE id = NEW.original_transaction_id)
            -
            COALESCE((SELECT SUM(amount_units) FROM payment_transactions
                      WHERE original_transaction_id = NEW.original_transaction_id
                        AND id != NEW.id
                        AND status IN ('confirmed', 'pending')
                        AND transaction_type = 'refund'), 0)
        ) < NEW.amount_units
        THEN RAISE(ABORT, 'refund_exceeds_original_payment')
    END;
END;

-- Trigger 3: Validar allocation en INSERT
CREATE TRIGGER trg_allocation_validation_insert
BEFORE INSERT ON payment_allocations
BEGIN
    -- 1. La transaccion debe ser payment
    SELECT CASE
        WHEN (SELECT transaction_type FROM payment_transactions
              WHERE id = NEW.transaction_id) != 'payment'
        THEN RAISE(ABORT, 'allocation_target_must_be_payment')
    END;
    -- 2. La transaccion debe estar confirmada
    SELECT CASE
        WHEN (SELECT status FROM payment_transactions
              WHERE id = NEW.transaction_id) != 'confirmed'
        THEN RAISE(ABORT, 'allocation_target_not_confirmed')
    END;
    -- 3. Suma no excede transaction amount
    SELECT CASE
        WHEN (
            (SELECT amount_units FROM payment_transactions
             WHERE id = NEW.transaction_id)
            -
            COALESCE((SELECT SUM(amount_units) FROM payment_allocations
                      WHERE transaction_id = NEW.transaction_id), 0)
        ) < NEW.amount_units
        THEN RAISE(ABORT, 'allocation_exceeds_transaction_amount')
    END;
    -- 4. Suma de allocations por installment no excede monto de la cuota
    SELECT CASE
        WHEN (
            (SELECT amount_units FROM payment_installments
             WHERE id = NEW.installment_id)
            -
            COALESCE((SELECT SUM(amount_units) FROM payment_allocations
                      WHERE installment_id = NEW.installment_id), 0)
        ) < NEW.amount_units
        THEN RAISE(ABORT, 'allocation_exceeds_installment_amount')
    END;
END;

-- Trigger 4: Validar allocation en UPDATE
CREATE TRIGGER trg_allocation_validation_update
BEFORE UPDATE ON payment_allocations
BEGIN
    SELECT CASE
        WHEN (SELECT transaction_type FROM payment_transactions
              WHERE id = NEW.transaction_id) != 'payment'
        THEN RAISE(ABORT, 'allocation_target_must_be_payment')
    END;
    SELECT CASE
        WHEN (SELECT status FROM payment_transactions
              WHERE id = NEW.transaction_id) != 'confirmed'
        THEN RAISE(ABORT, 'allocation_target_not_confirmed')
    END;
    SELECT CASE
        WHEN (
            (SELECT amount_units FROM payment_transactions
             WHERE id = NEW.transaction_id)
            -
            COALESCE((SELECT SUM(amount_units) FROM payment_allocations
                      WHERE transaction_id = NEW.transaction_id
                        AND id != NEW.id), 0)
        ) < NEW.amount_units
        THEN RAISE(ABORT, 'allocation_exceeds_transaction_amount')
    END;
    SELECT CASE
        WHEN (
            (SELECT amount_units FROM payment_installments
             WHERE id = NEW.installment_id)
            -
            COALESCE((SELECT SUM(amount_units) FROM payment_allocations
                      WHERE installment_id = NEW.installment_id
                        AND id != NEW.id), 0)
        ) < NEW.amount_units
        THEN RAISE(ABORT, 'allocation_exceeds_installment_amount')
    END;
END;

-- Trigger 5: Prevenir que payment confirmado se reduzca por debajo de refunds
CREATE TRIGGER trg_payment_cannot_shrink_below_refunds
BEFORE UPDATE ON payment_transactions
WHEN OLD.status = 'confirmed' AND NEW.status = 'confirmed' AND NEW.amount_units < OLD.amount_units
BEGIN
    SELECT CASE
        WHEN NEW.amount_units < COALESCE(
            (SELECT SUM(amount_units) FROM payment_transactions
             WHERE original_transaction_id = NEW.id
               AND transaction_type = 'refund'
               AND status = 'confirmed'), 0)
        THEN RAISE(ABORT, 'payment_amount_below_existing_refunds')
    END;
END;

-- =============================================================================
-- FIN SCHEMA V5
-- =============================================================================