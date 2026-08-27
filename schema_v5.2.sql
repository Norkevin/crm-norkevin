-- =============================================================================
-- SCHEMA V5.2 - CRM Narvis (Proyecto Kevin / Norkevin / Astral)
-- =============================================================================
-- Version: 5.2 (correcciones segun feedback real de Kevin sobre V5.1)
-- Inventario esperado:
--   - 35 tablas
--   - 27 indices
--   - 13 triggers
--
-- Este archivo es la UNICA fuente de verdad del DDL.
-- Cualquier cambio se hace aqui y se valida con validate_schema_v5.2.py +
-- verify_v5_consistency_v5.2.py.
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
    FOREIGN KEY (tenant_id, company_id)
        REFERENCES companies(tenant_id, id) ON DELETE RESTRICT,
    CHECK (active IN (0, 1))
);

-- =============================================================================
-- FASE 2: UNIQUE INDEXes para FKs compuestas
-- (Creados ANTES de las tablas con FKs compuestas; referenciados en FASE 3)
-- =============================================================================

CREATE UNIQUE INDEX uq_companies_tenant_id          ON companies(tenant_id, id);
CREATE UNIQUE INDEX uq_users_tenant_id              ON users(tenant_id, id);
CREATE UNIQUE INDEX uq_clients_tenant_id            ON clients(tenant_id, id);
CREATE UNIQUE INDEX uq_projects_tenant_id           ON projects(tenant_id, id);
CREATE UNIQUE INDEX uq_projects_tenant_company      ON projects(tenant_id, company_id, id);
CREATE UNIQUE INDEX uq_pst_tenant_company           ON payment_schedule_templates(tenant_id, company_id, id);
-- uq_wft_tenant_company, uq_wtf_tenant_company, uq_wttv_template_version
-- uq_ce_client_id, uq_cp_client_id, uq_ca_client_id
-- se crean despues de las tablas correspondientes

-- =============================================================================
-- FASE 3: Tablas compuestas y derivadas
-- =============================================================================

-- user_company_memberships: conecta user con company (ambos independientes).
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

-- project_clients: conecta project (independiente) con client (independiente).
-- El id es propio para que quotes/invoices puedan referenciar un contacto especifico.
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

-- email_templates: tenant + company
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

-- settings: tenant + company
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

-- workflow_template_families
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

-- workflow_template_versions: Sec. 2 — FK compuesta (tenant,company,family)
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
    FOREIGN KEY (tenant_id, company_id, family_id)
        REFERENCES workflow_template_families(tenant_id, company_id, id) ON DELETE RESTRICT,
    CHECK (mode IN ('dynamic', 'frozen')),
    CHECK (version >= 1)
);

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

-- workflow_instances
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
    FOREIGN KEY (tenant_id, company_id, project_id)
        REFERENCES projects(tenant_id, company_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, company_id, template_version_id)
        REFERENCES workflow_template_versions(tenant_id, company_id, id) ON DELETE RESTRICT,
    CHECK (status IN ('active','paused','completed','cancelled'))
);

CREATE UNIQUE INDEX uq_project_active_workflow
    ON workflow_instances(project_id) WHERE status IN ('active', 'paused');

-- workflow_task_instances: Sec. 3 — project_id ELIMINADO, FK compuesta via instance
CREATE TABLE workflow_task_instances (
    id                          TEXT PRIMARY KEY,
    workflow_instance_id        TEXT NOT NULL REFERENCES workflow_instances(id) ON DELETE CASCADE,
    template_version_id         TEXT NOT NULL REFERENCES workflow_template_versions(id) ON DELETE RESTRICT,
    task_template_version_id    TEXT NOT NULL REFERENCES workflow_task_template_versions(id) ON DELETE RESTRICT,
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
    FOREIGN KEY (workflow_instance_id, template_version_id)
        REFERENCES workflow_instances(id, template_version_id) ON DELETE CASCADE,
    FOREIGN KEY (template_version_id, task_template_version_id)
        REFERENCES workflow_task_template_versions(template_version_id, id) ON DELETE RESTRICT,
    CHECK (status IN ('pending','ready','running','done','skipped','failed')),
    CHECK (retry_count >= 0)
);
-- uq_wttv_template_version para la FK de coherencia se crea despues

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
    FOREIGN KEY (tenant_id, company_id, project_id)
        REFERENCES projects(tenant_id, company_id, id) ON DELETE RESTRICT,
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

-- quote_items: Sec. 6 V5.2 — Snapshot puro, product_id solo referencia historica.
-- El documento congelado no depende del Product (name/price copiados al enviar).
-- Si tiene product_id, debe pertenecer a la misma company de la quote.
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

-- Trigger 9 (V5.2 — Sec. 6): si tiene product_id, valida company_id matching
CREATE TRIGGER trg_quote_item_product_same_company
BEFORE INSERT ON quote_items
WHEN NEW.product_id IS NOT NULL
BEGIN
    SELECT CASE
        WHEN (SELECT company_id FROM products WHERE id = NEW.product_id)
             IS NOT (SELECT company_id FROM quotes WHERE id = NEW.quote_id)
        THEN RAISE(ABORT, 'quote_item_product_company_mismatch')
    END;
END;

CREATE TRIGGER trg_quote_item_product_same_company_update
BEFORE UPDATE OF quote_id, product_id ON quote_items
WHEN NEW.product_id IS NOT NULL
BEGIN
    SELECT CASE
        WHEN (SELECT company_id FROM products WHERE id = NEW.product_id)
             IS NOT (SELECT company_id FROM quotes WHERE id = NEW.quote_id)
        THEN RAISE(ABORT, 'quote_item_product_company_mismatch')
    END;
END;

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
    FOREIGN KEY (tenant_id, company_id, project_id)
        REFERENCES projects(tenant_id, company_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (project_id, billing_project_client_id)
        REFERENCES project_clients(project_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (project_id, quote_id)
        REFERENCES quotes(project_id, id) ON DELETE RESTRICT,
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

-- payment_transactions: Sec. 4 — FK compuesta real (tenant, company, project, invoice)
CREATE TABLE payment_transactions (
    id                          TEXT PRIMARY KEY,
    tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    company_id                  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    project_id                  TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    invoice_id                  TEXT NOT NULL REFERENCES invoices(id) ON DELETE RESTRICT,
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
    -- Sec. 4: FK compuesta real evita tx de project A hacia invoice de project B
    FOREIGN KEY (tenant_id, company_id, project_id)
        REFERENCES projects(tenant_id, company_id, id) ON DELETE RESTRICT,
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

-- payment_allocations: Sec. 7 — debe validar tx es payment, esta confirmada, etc.
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

-- processed_events: PRIMARY KEY (tenant_id, idempotency_key)
-- Sec. 5: company_id debe pertenecer al mismo tenant (via FK compuesto)
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
    FOREIGN KEY (tenant_id, company_id)
        REFERENCES companies(tenant_id, id) ON DELETE RESTRICT,
    CHECK (status IN ('processing', 'completed', 'failed')),
    CHECK (attempts >= 0)
);

-- outbox_events: Sec. 10 — dead_letter, lock_by worker, etc.
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
    CHECK (status IN ('pending','processing','delivered','failed','dead_letter','pending_admin_reset')),
    CHECK (attempts >= 0),
    CHECK (attempts <= max_attempts),
    CHECK (max_attempts > 0),
    UNIQUE (tenant_id, dedupe_key)
);

-- automation_runs: Sec. 5 — FKs compuestas con project/workflow/task
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
    FOREIGN KEY (tenant_id, company_id, project_id)
        REFERENCES projects(tenant_id, company_id, id) ON DELETE SET NULL,
    FOREIGN KEY (tenant_id, company_id, workflow_instance_id)
        REFERENCES workflow_instances(tenant_id, company_id, id) ON DELETE SET NULL,
    FOREIGN KEY (workflow_instance_id, task_instance_id)
        REFERENCES workflow_task_instances(workflow_instance_id, id) ON DELETE SET NULL,
    CHECK (status IN ('pending','running','success','failed','skipped'))
);

-- activity_log: Sec. 5 — FKs compuestas con project/client
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
    FOREIGN KEY (tenant_id, company_id)
        REFERENCES companies(tenant_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (tenant_id, project_id)
        REFERENCES projects(tenant_id, id) ON DELETE SET NULL,
    FOREIGN KEY (tenant_id, client_id)
        REFERENCES clients(tenant_id, id) ON DELETE SET NULL,
    CHECK (actor_type IN ('system','user','client','automation'))
);

-- mail_log: Sec. 5 — FKs compuestas con project/client/template
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
    FOREIGN KEY (tenant_id, project_id)
        REFERENCES projects(tenant_id, id) ON DELETE SET NULL,
    FOREIGN KEY (tenant_id, client_id)
        REFERENCES clients(tenant_id, id) ON DELETE SET NULL,
    FOREIGN KEY (tenant_id, company_id, template_id)
        REFERENCES email_templates(tenant_id, company_id, id) ON DELETE SET NULL,
    CHECK (status IN ('pending','sent','delivered','opened','clicked','bounced','failed'))
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

-- calendar_events: Sec. 5 — FKs compuestas
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
    FOREIGN KEY (tenant_id, project_id)
        REFERENCES projects(tenant_id, id) ON DELETE SET NULL,
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
-- FASE 4: UNIQUE INDEXes que requieren tablas creadas
-- (Estos UNIQUE INDEX deben existir antes que las FKs que los referencian
-- en FASE 5. Aqui los creamos DESPUES de las tablas)
-- =============================================================================

-- UNIQUE INDEXs para FKs compuestas que se crean en tablas definidas en FASE 3+
-- (algunos UNIQUE INDEX no pueden existir en FASE 2 porque la tabla aun no se creo)
CREATE UNIQUE INDEX uq_et_tenant_company_id        ON email_templates(tenant_id, company_id, id);
CREATE UNIQUE INDEX uq_wtf_tenant_company_id       ON workflow_template_families(tenant_id, company_id, id);
CREATE UNIQUE INDEX uq_wftv_tenant_company_id      ON workflow_template_versions(tenant_id, company_id, id);
CREATE UNIQUE INDEX uq_wi_id_template_version      ON workflow_instances(id, template_version_id);
CREATE UNIQUE INDEX uq_wi_tenant_company_id        ON workflow_instances(tenant_id, company_id, id);
CREATE UNIQUE INDEX uq_wttv_template_version       ON workflow_task_template_versions(template_version_id, id);
CREATE UNIQUE INDEX uq_wti_workflow_instance_id    ON workflow_task_instances(workflow_instance_id, id);
CREATE UNIQUE INDEX uq_pc_project_id               ON project_clients(project_id, id);
CREATE UNIQUE INDEX uq_q_project_id                ON quotes(project_id, id);
CREATE UNIQUE INDEX uq_pi_invoice_id               ON payment_installments(invoice_id, id);
CREATE UNIQUE INDEX uq_pt_invoice_id               ON payment_transactions(invoice_id, id);

-- =============================================================================
-- FASE 5: UNIQUE INDEX PARCIALES y demas indices
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
    SELECT CASE
        WHEN (SELECT transaction_type FROM payment_transactions
              WHERE id = NEW.original_transaction_id) != 'payment'
        THEN RAISE(ABORT, 'refund_original_must_be_payment')
    END;
    SELECT CASE
        WHEN (SELECT status FROM payment_transactions
              WHERE id = NEW.original_transaction_id) != 'confirmed'
        THEN RAISE(ABORT, 'refund_original_not_confirmed')
    END;
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
    SELECT CASE
        WHEN (SELECT transaction_type FROM payment_transactions
              WHERE id = NEW.original_transaction_id) != 'payment'
        THEN RAISE(ABORT, 'refund_original_must_be_payment')
    END;
    SELECT CASE
        WHEN (SELECT status FROM payment_transactions
              WHERE id = NEW.original_transaction_id) != 'confirmed'
        THEN RAISE(ABORT, 'refund_original_not_confirmed')
    END;
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
                      WHERE transaction_id = NEW.transaction_id), 0)
        ) < NEW.amount_units
        THEN RAISE(ABORT, 'allocation_exceeds_transaction_amount')
    END;
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

-- Trigger 5 (V5.1): Prevenir reducir payment por debajo de refunds
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

CREATE TRIGGER trg_payment_cannot_shrink_below_allocations
BEFORE UPDATE ON payment_transactions
WHEN OLD.transaction_type = 'payment' AND NEW.amount_units < OLD.amount_units
BEGIN
    SELECT CASE
        WHEN NEW.amount_units < COALESCE(
            (SELECT SUM(amount_units) FROM payment_allocations
             WHERE transaction_id = NEW.id), 0)
        THEN RAISE(ABORT, 'payment_amount_below_existing_allocations')
    END;
END;

CREATE TRIGGER trg_payment_original_with_refunds_locked
BEFORE UPDATE OF transaction_type, status, currency_code, currency_exponent, invoice_id, project_id ON payment_transactions
WHEN OLD.transaction_type = 'payment'
     AND EXISTS (
        SELECT 1 FROM payment_transactions
        WHERE original_transaction_id = OLD.id
          AND transaction_type = 'refund'
          AND status IN ('confirmed', 'pending')
     )
BEGIN
    SELECT CASE
        WHEN NEW.transaction_type != OLD.transaction_type
          OR NEW.status != OLD.status
          OR NEW.currency_code != OLD.currency_code
          OR NEW.currency_exponent != OLD.currency_exponent
          OR NEW.invoice_id != OLD.invoice_id
          OR NEW.project_id != OLD.project_id
        THEN RAISE(ABORT, 'payment_original_has_refunds_locked')
    END;
END;

-- Trigger 6: Garantizar coherencia project_id de tx con invoice
CREATE TRIGGER trg_payment_tx_invoice_project_match
BEFORE INSERT ON payment_transactions
BEGIN
    SELECT CASE
        WHEN (SELECT project_id FROM invoices WHERE id = NEW.invoice_id)
             IS NOT NEW.project_id
        THEN RAISE(ABORT, 'payment_tx_project_mismatch_invoice')
    END;
END;

CREATE TRIGGER trg_payment_tx_invoice_project_match_update
BEFORE UPDATE OF invoice_id, project_id ON payment_transactions
BEGIN
    SELECT CASE
        WHEN (SELECT project_id FROM invoices WHERE id = NEW.invoice_id)
             IS NOT NEW.project_id
        THEN RAISE(ABORT, 'payment_tx_project_mismatch_invoice')
    END;
END;

-- Trigger 7 (V5.2 — Sec. 10): outbox no puede volver de delivered a pending
CREATE TRIGGER trg_outbox_no_delivered_to_pending
BEFORE UPDATE ON outbox_events
WHEN OLD.status = 'delivered' AND NEW.status = 'pending'
BEGIN
    SELECT RAISE(ABORT, 'outbox_delivered_cannot_return_to_pending');
END;

-- Trigger 8 (V5.2 — Sec. 10): outbox no puede volver de dead_letter sin admin
CREATE TRIGGER trg_outbox_dead_letter_locked
BEFORE UPDATE ON outbox_events
WHEN OLD.status = 'dead_letter' AND NEW.status != 'dead_letter'
     AND NEW.status != 'pending_admin_reset'
BEGIN
    SELECT RAISE(ABORT, 'outbox_dead_letter_requires_admin');
END;

-- =============================================================================
-- FIN SCHEMA V5.2
-- =============================================================================
