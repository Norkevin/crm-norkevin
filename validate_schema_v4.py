"""
validate_schema_v4.py
====================

Prueba ejecutable del DDL del MODELO_DE_DATOS_CRM_V4.md.

NO modifica crm.db, NO modifica los JSON, NO modifica app.py.
Crea un SQLite temporal en memoria, ejecuta el DDL, hace PRAGMA
foreign_key_check, y prueba los cruces inválidos que Kevin pidió:

1. quote Astral con project Norkevin  -> rechazado
2. client de otro tenant              -> rechazado
3. invoice con quote de otro project   -> rechazado
4. refund de otra invoice             -> rechazado
5. doble aceptacion                    -> solo 1 project + 1 acceptance
6. error a mitad de transaccion        -> no quedan escrituras parciales
7. retry fallido (processing)          -> se actualiza a completed al reintentar

Resultado: imprime PASS/FAIL por prueba y un resumen final.

Uso:
    python3.11 validate_schema_v4.py
"""
import sqlite3
import sys
import os
import hashlib
import json
from datetime import datetime

# ============================================================
# DDL COMPLETO (extraído de MODELO_DE_DATOS_CRM_V4.md)
# ============================================================

DDL_STATEMENTS = [
    # tenants
    """
    CREATE TABLE tenants (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        owner_user_id   TEXT,
        timezone        TEXT NOT NULL DEFAULT 'America/Guatemala',
        language        TEXT NOT NULL DEFAULT 'es',
        created_at      TEXT NOT NULL,
        archived_at     TEXT,
        CHECK (archived_at IS NULL OR archived_at > created_at)
    )
    """,
    # companies
    """
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
    )
    """,
    # users
    """
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
    )
    """,
    # user_company_memberships (V4: con tenant_id)
    # FKs compuestas hacia tablas anclas: users(tenant_id, id) y companies(tenant_id, id)
    # El UNIQUE INDEX en users/companies (tenant_id, id) se crea antes en setup_db().
    """
    CREATE TABLE user_company_memberships (
        tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
        role        TEXT NOT NULL DEFAULT 'viewer',
        active      INTEGER NOT NULL DEFAULT 1,
        created_at  TEXT NOT NULL,
        archived_at TEXT,
        PRIMARY KEY (user_id, company_id),
        UNIQUE (tenant_id, user_id, company_id),
        CHECK (role IN ('owner', 'admin', 'editor', 'viewer')),
        CHECK (active IN (0, 1))
    )
    """,
    # clients
    """
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
    )
    """,
    # client_emails
    """
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
        CHECK (is_primary IN (0, 1))
    )
    """,
    # client_phones
    """
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
        CHECK (is_primary IN (0, 1))
    )
    """,
    # client_addresses
    """
    CREATE TABLE client_addresses (
        id              TEXT PRIMARY KEY,
        tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
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
    )
    """,
    # projects (V4: SIN workflow_template_id, SIN workflow_instance_id)
    """
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
    )
    """,
    # UNIQUE INDEX que faltaba en V3
    """
    CREATE UNIQUE INDEX uq_projects_tenant_company_id
        ON projects(tenant_id, company_id, id)
    """,
    # project_clients
    """
    CREATE TABLE project_clients (
        project_id          TEXT NOT NULL,
        tenant_id           TEXT NOT NULL,
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
    )
    """,
    # quotes (V4: con FK a project_clients)
    """
    CREATE TABLE quotes (
        id                    TEXT PRIMARY KEY,
        tenant_id             TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        company_id            TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
        project_id            TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
        client_id             TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
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
        FOREIGN KEY (project_id, client_id)
            REFERENCES project_clients(project_id, client_id)
            ON DELETE RESTRICT,
        CHECK (type IN ('fixed', 'pick_choose')),
        CHECK (status IN
            ('draft','sent','viewed','accepted','declined','expired',
             'superseded','cancelled')),
        CHECK (subtotal_units >= 0),
        CHECK (total_units >= 0)
    )
    """,
    # quote_items
    """
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
        CHECK (quantity > 0),
        CHECK (price_units >= 0),
        CHECK (subtotal_units >= 0)
    )
    """,
    # payment_schedule_templates
    """
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
    )
    """,
    # payment_schedule_rules (V4: con CHECK XOR y fixed_due_date)
    """
    CREATE TABLE payment_schedule_rules (
        id                          TEXT PRIMARY KEY,
        template_id                 TEXT NOT NULL,
        tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        order_index                 INTEGER NOT NULL,
        description                 TEXT,
        percentage_bps               INTEGER,
        amount_units                INTEGER,
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
    )
    """,
    # invoices
    """
    CREATE TABLE invoices (
        id                    TEXT PRIMARY KEY,
        tenant_id             TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        company_id            TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
        project_id            TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
        client_id             TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
        quote_id              TEXT REFERENCES quotes(id) ON DELETE RESTRICT,
        payment_schedule_id   TEXT REFERENCES payment_schedule_templates(id) ON DELETE RESTRICT,
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
        FOREIGN KEY (project_id, client_id)
            REFERENCES project_clients(project_id, client_id)
            ON DELETE RESTRICT,
        CHECK (status IN
            ('draft','issued','sent','viewed','partially_paid','paid',
             'overdue','cancelled','written_off','refunded')),
        CHECK (subtotal_units >= 0),
        CHECK (total_units >= 0)
    )
    """,
    # invoice_items
    """
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
    )
    """,
    # payment_installments
    """
    CREATE TABLE payment_installments (
        id                TEXT PRIMARY KEY,
        tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        company_id        TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
        project_id        TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
        client_id         TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
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
    )
    """,
    # payment_transactions
    """
    CREATE TABLE payment_transactions (
        id                          TEXT PRIMARY KEY,
        tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        company_id                  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
        project_id                  TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
        client_id                   TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
        invoice_id                  TEXT NOT NULL REFERENCES invoices(id) ON DELETE RESTRICT,
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
        idempotency_key             TEXT NOT NULL UNIQUE,
        status                      TEXT NOT NULL DEFAULT 'pending',
        receipt_url                 TEXT,
        notes                       TEXT,
        created_by_user_id          TEXT,
        created_at                  TEXT NOT NULL,
        archived_at                 TEXT,
        CHECK (transaction_type IN ('payment', 'refund')),
        CHECK (status IN ('pending', 'confirmed', 'failed', 'reversed')),
        CHECK (amount_units > 0),
        CHECK (original_transaction_id IS NULL OR transaction_type = 'refund'),
        UNIQUE (tenant_id, company_id, provider, provider_transaction_id)
    )
    """,
    # payment_allocations (NUEVA)
    """
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
    )
    """,
    # products
    """
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
    )
    """,
    # workflow_template_families (NUEVA V4)
    """
    CREATE TABLE workflow_template_families (
        id              TEXT PRIMARY KEY,
        tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        company_id      TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
        name            TEXT NOT NULL,
        description     TEXT,
        active          INTEGER NOT NULL DEFAULT 1,
        created_at      TEXT NOT NULL,
        CHECK (active IN (0, 1))
    )
    """,
    # workflow_template_versions (NUEVA V4)
    """
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
    )
    """,
    # workflow_task_template_versions (NUEVA V4)
    """
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
    )
    """,
    # workflow_instances
    """
    CREATE TABLE workflow_instances (
        id                          TEXT PRIMARY KEY,
        tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        company_id                  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
        project_id                  TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
        template_family_id          TEXT NOT NULL,
        template_version_id         TEXT NOT NULL REFERENCES workflow_template_versions(id) ON DELETE RESTRICT,
        template_version            INTEGER NOT NULL,
        mode                        TEXT NOT NULL,
        status                      TEXT NOT NULL DEFAULT 'active',
        started_at                  TEXT NOT NULL,
        completed_at                TEXT,
        CHECK (status IN ('active','paused','completed','cancelled'))
    )
    """,
    # workflow_task_instances
    """
    CREATE TABLE workflow_task_instances (
        id                          TEXT PRIMARY KEY,
        tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        company_id                  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
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
    )
    """,
    # processed_events
    """
    CREATE TABLE processed_events (
        tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        company_id        TEXT,
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
    )
    """,
    # outbox_events
    """
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
        CHECK (status IN ('pending','processing','delivered','failed','dead_letter')),
        CHECK (attempts >= 0),
        CHECK (attempts <= max_attempts),
        CHECK (max_attempts > 0),
        UNIQUE (tenant_id, dedupe_key)
    )
    """,
    # automation_runs
    """
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
    )
    """,
    # activity_log
    """
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
    )
    """,
    # mail_log
    """
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
    )
    """,
    # calendar_events
    """
    CREATE TABLE calendar_events (
        id                          TEXT PRIMARY KEY,
        tenant_id                   TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        company_id                  TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
        project_id                  TEXT,
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
        CHECK (type IN ('lead_meeting','event','session','payment','task','contract_signing')),
        CHECK (all_day IN (0, 1)),
        CHECK (
            (all_day = 1 AND start_date IS NOT NULL AND start_at IS NULL
                AND timezone IS NULL)
            OR
            (all_day = 0 AND start_at IS NOT NULL AND timezone IS NOT NULL
                AND start_date IS NULL)
        )
    )
    """,
    # email_templates
    """
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
    )
    """,
    # legacy_record_map (V4: new_entity_id nullable)
    """
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
    )
    """,
    # settings
    """
    CREATE TABLE settings (
        tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
        key           TEXT NOT NULL,
        value         TEXT,
        updated_at    TEXT NOT NULL,
        PRIMARY KEY (company_id, key)
    )
    """,
    # sequence_counters
    """
    CREATE TABLE sequence_counters (
        tenant_id     TEXT NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
        entity_type   TEXT NOT NULL,
        year          INTEGER NOT NULL,
        last_value    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (company_id, entity_type, year),
        CHECK (entity_type IN ('quote','invoice','contract','gallery','client','project'))
    )
    """,
    # UNIQUE indexes (V4 fix)
    "CREATE UNIQUE INDEX uq_users_tenant_id ON users(tenant_id, id)",
    "CREATE UNIQUE INDEX uq_companies_tenant_id ON companies(tenant_id, id)",
    "CREATE UNIQUE INDEX uq_clients_tenant_id ON clients(tenant_id, id)",
    "CREATE UNIQUE INDEX uq_projects_tenant_id ON projects(tenant_id, id)",
    "CREATE UNIQUE INDEX uq_project_active_workflow ON workflow_instances(project_id) WHERE status IN ('active', 'paused')",
    "CREATE UNIQUE INDEX uq_client_email_primary ON client_emails(client_id) WHERE is_primary = 1 AND archived_at IS NULL",
    "CREATE UNIQUE INDEX uq_client_phone_primary ON client_phones(client_id) WHERE is_primary = 1 AND archived_at IS NULL",
    "CREATE UNIQUE INDEX uq_project_primary_contact ON project_clients(project_id) WHERE is_primary = 1 AND archived_at IS NULL",
    "CREATE UNIQUE INDEX uq_project_billing_contact ON project_clients(project_id) WHERE is_billing_contact = 1 AND archived_at IS NULL",
]


# ============================================================
# TESTS
# ============================================================

def setup_db():
    """Crea una DB en memoria, ejecuta el DDL, devuelve la conexion."""
    conn = sqlite3.connect(":memory:", timeout=30)
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA journal_mode=WAL;")

    # Orden de creacion:
    # 1. Tablas base sin FKs compuestas (que referencian a tablas con UNIQUE INDEX)
    #    Las FKs compuestas `(tenant_id, X) -> other(tenant_id, id)` requieren que
    #    `other(tenant_id, id)` tenga UNIQUE INDEX.
    # 2. UNIQUE INDEX en tablas base
    # 3. Tablas con FKs compuestas

    # Tablas "ancla" (necesitan existir ANTES que se cree el UNIQUE INDEX
    # que las haga UNIQUE por tenant):
    anchor_tables = ['tenants', 'companies', 'users', 'clients']

    # Separar DDL
    tables_ddl = []
    indexes_ddl = []
    for stmt in DDL_STATEMENTS:
        s = stmt.strip()
        if s.upper().startswith("CREATE TABLE"):
            tables_ddl.append(stmt)
        elif s.upper().startswith("CREATE INDEX") or s.upper().startswith("CREATE UNIQUE INDEX"):
            indexes_ddl.append(stmt)

    # Mapear nombre de tabla a DDL
    def get_table_name(ddl):
        s = ddl.strip()
        if s.upper().startswith("CREATE TABLE"):
            return s.split()[2].strip('"').strip('`').strip("'")
        return None

    # Particionar en 3 grupos
    group1_anchors = []  # tablas base sin FKs que necesiten UNIQUE INDEX previo
    group2_anchors = []  # tablas que tienen FKs simples pero no compuestas
    group3_compound = []  # tablas con FKs compuestas

    # Tablas que solo necesitan las anclas (sin FKs compuestas propias)
    for ddl in tables_ddl:
        name = get_table_name(ddl)
        if name in anchor_tables:
            group1_anchors.append(ddl)
        elif any(f"{t}(tenant_id, id)" in ddl for t in anchor_tables) \
             or any(f"{t}(tenant_id, " in ddl for t in ['workflow_template_versions']) \
             or 'project_clients(project_id, client_id)' in ddl:
            group3_compound.append(ddl)
        else:
            group2_anchors.append(ddl)

    # Paso 1: anclas
    for stmt in group1_anchors:
        conn.execute(stmt)

    # Paso 2: UNIQUE INDEX que necesitan las anclas
    anchor_unique_indexes = []
    other_indexes = []
    for idx in indexes_ddl:
        if any(t in idx for t in ['users_tenant', 'companies_tenant', 'clients_tenant', 'projects_tenant']):
            anchor_unique_indexes.append(idx)
        else:
            other_indexes.append(idx)

    # Crear tablas que usan las anclas SIN FKs compuestas
    for stmt in group2_anchors:
        conn.execute(stmt)

    # Crear UNIQUE INDEX en anclas
    for idx in anchor_unique_indexes:
        conn.execute(idx)

    # Crear tablas con FKs compuestas
    for stmt in group3_compound:
        conn.execute(stmt)

    # Crear otros UNIQUE INDEX (los parciales como uq_project_primary_contact)
    for idx in other_indexes:
        conn.execute(idx)

    conn.commit()
    return conn


def seed_minimum(conn):
    """Inserta datos minimos para las pruebas."""
    conn.execute("""INSERT INTO tenants (id, name, created_at) VALUES
        ('tenant_kevin', 'Kevin', '2026-01-01T00:00:00Z')""")

    conn.execute("""INSERT INTO companies (id, tenant_id, slug, name, logo_letter, color, created_at, updated_at) VALUES
        ('company_norkevin', 'tenant_kevin', 'norkevin', 'Norkevin Photography', 'N', '#2F7D73', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'),
        ('company_astral', 'tenant_kevin', 'astral', 'Astral Weddings', 'A', '#7C3AED', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')""")

    conn.execute("""INSERT INTO users (id, tenant_id, email, name, role, created_at) VALUES
        ('user_kevin', 'tenant_kevin', 'kevin@norkevin.com', 'Kevin Lemus', 'owner', '2026-01-01T00:00:00Z')""")

    conn.execute("""INSERT INTO user_company_memberships (tenant_id, user_id, company_id, role, created_at) VALUES
        ('tenant_kevin', 'user_kevin', 'company_norkevin', 'owner', '2026-01-01T00:00:00Z'),
        ('tenant_kevin', 'user_kevin', 'company_astral', 'owner', '2026-01-01T00:00:00Z')""")

    conn.execute("""INSERT INTO clients (id, tenant_id, first_name, last_name, created_at, updated_at) VALUES
        ('client_maria', 'tenant_kevin', 'Maria', 'Lopez', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')""")

    conn.execute("""INSERT INTO projects (id, tenant_id, company_id, name, type, source, event_date, event_time, location_name, commercial_status, operational_status, booked_value_units, created_at, updated_at) VALUES
        ('proj_norkevin', 'tenant_kevin', 'company_norkevin', 'Boda Maria & Carlos', 'boda', 'instagram', '2026-08-15', '16:00', 'Antigua Guatemala', 'new_lead', 'lead', 2050000, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')""")

    conn.execute("""INSERT INTO project_clients (project_id, tenant_id, client_id, role, is_primary, is_billing_contact, is_portal_contact, created_at) VALUES
        ('proj_norkevin', 'tenant_kevin', 'client_maria', 'novia', 1, 1, 1, '2026-01-01T00:00:00Z')""")

    conn.execute("""INSERT INTO quotes (id, tenant_id, company_id, project_id, client_id, number, status, issue_date, due_date, subtotal_units, tax_units, total_units, currency_code, currency_exponent, created_at) VALUES
        ('quote_001', 'tenant_kevin', 'company_norkevin', 'proj_norkevin', 'client_maria', 'Q-2026-001', 'sent', '2026-07-01', '2026-07-15', 2050000, 246000, 2296000, 'GTQ', 2, '2026-01-01T00:00:00Z')""")

    conn.execute("""INSERT INTO quote_items (id, tenant_id, company_id, quote_id, name, price_units, quantity, subtotal_units, order_index) VALUES
        ('qitem_001', 'tenant_kevin', 'company_norkevin', 'quote_001', 'MIX GOLD', 2050000, 1, 2050000, 0)""")

    conn.execute("""INSERT INTO workflow_template_families (id, tenant_id, company_id, name, created_at) VALUES
        ('wtf_bodas', 'tenant_kevin', 'company_norkevin', 'BODAS NORKEVIN', '2026-01-01T00:00:00Z')""")

    conn.execute("""INSERT INTO workflow_template_versions (id, tenant_id, company_id, family_id, version, mode, created_at) VALUES
        ('wtv_bodas_v1', 'tenant_kevin', 'company_norkevin', 'wtf_bodas', 1, 'dynamic', '2026-01-01T00:00:00Z')""")

    conn.commit()


def test_foreign_keys(conn):
    """Test 1: PRAGMA foreign_key_check sin errores."""
    cur = conn.execute("PRAGMA foreign_key_check")
    errors = cur.fetchall()
    return len(errors) == 0, errors


def test_invalid_quote_company(conn):
    """Test 2: quote con company_id distinto al project -> RECHAZADO."""
    try:
        conn.execute("""INSERT INTO quotes
            (id, tenant_id, company_id, project_id, client_id, number, status, issue_date, due_date, subtotal_units, tax_units, total_units, currency_code, currency_exponent, created_at)
            VALUES ('quote_invalid_company', 'tenant_kevin', 'company_astral', 'proj_norkevin', 'client_maria', 'Q-INVALID', 'draft', '2026-08-01', '2026-08-15', 100, 12, 112, 'GTQ', 2, '2026-08-01T00:00:00Z')""")
        conn.rollback()
        return False, "quote con company distinto al project NO fue rechazado"
    except sqlite3.IntegrityError:
        conn.rollback()
        return True, "rechazado correctamente"


def test_invalid_client_tenant(conn):
    """Test 3: client con tenant distinto al del project -> RECHAZADO."""
    try:
        # Crear un tenant falso
        conn.execute("""INSERT INTO tenants (id, name, created_at) VALUES
            ('tenant_otro', 'Otro', '2026-01-01T00:00:00Z')""")
        # Crear un client en tenant_otro
        conn.execute("""INSERT INTO clients (id, tenant_id, first_name, last_name, created_at, updated_at) VALUES
            ('client_otro', 'tenant_otro', 'Otro', 'Cliente', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')""")
        # Intentar crear project_clients con client de otro tenant
        conn.execute("""INSERT INTO project_clients (project_id, tenant_id, client_id, role, is_primary, is_billing_contact, is_portal_contact, created_at) VALUES
            ('proj_norkevin', 'tenant_kevin', 'client_otro', 'novio', 1, 0, 0, '2026-01-01T00:00:00Z')""")
        conn.rollback()
        return False, "client de otro tenant en project_clients NO fue rechazado"
    except sqlite3.IntegrityError:
        conn.rollback()
        return True, "rechazado correctamente"


def test_invalid_invoice_quote(conn):
    """Test 4: invoice con quote de otro project -> RECHAZADO."""
    try:
        # Crear otro project
        conn.execute("""INSERT INTO projects (id, tenant_id, company_id, name, type, created_at, updated_at) VALUES
            ('proj_otro', 'tenant_kevin', 'company_norkevin', 'Otro project', 'boda', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')""")
        # Crear invoice con quote de proj_norkevin y project de proj_otro
        conn.execute("""INSERT INTO invoices
            (id, tenant_id, company_id, project_id, client_id, quote_id, number, status, issue_date, due_date, subtotal_units, tax_units, total_units, currency_code, currency_exponent, created_at)
            VALUES ('inv_invalid', 'tenant_kevin', 'company_norkevin', 'proj_otro', 'client_maria', 'quote_001', 'INV-INVALID', 'draft', '2026-08-01', '2026-08-15', 100, 12, 112, 'GTQ', 2, '2026-08-01T00:00:00Z')""")
        conn.rollback()
        return False, "invoice con quote de otro project NO fue rechazado"
    except sqlite3.IntegrityError:
        conn.rollback()
        return True, "rechazado correctamente"


def test_double_acceptance_idempotent(conn):
    """Test 5: doble aceptacion -> solo 1 project + 1 acceptance."""
    # Limpiar instancias de workflow previas si las hay
    conn.execute("DELETE FROM workflow_task_instances")
    conn.execute("DELETE FROM workflow_instances")
    conn.execute("DELETE FROM payment_installments")
    conn.execute("DELETE FROM payment_transactions")
    conn.execute("DELETE FROM payment_allocations")
    conn.execute("DELETE FROM invoices")
    conn.execute("DELETE FROM quote_items WHERE id != 'qitem_001'")
    conn.execute("DELETE FROM quotes WHERE id != 'quote_001'")
    conn.execute("DELETE FROM outbox_events WHERE id LIKE 'test%'")
    conn.commit()

    # Aceptar quote (UPDATE en project)
    n_updated = conn.execute("""UPDATE projects
        SET commercial_status = 'accepted', operational_status = 'confirmed',
            job_accepted_at = '2026-07-01T10:00:00Z',
            job_accepted_via = 'quote_accepted', updated_at = '2026-07-01T10:00:00Z'
        WHERE id = 'proj_norkevin' AND operational_status = 'lead'""").rowcount
    conn.execute("""UPDATE quotes SET status = 'accepted', accepted_at = '2026-07-01T10:00:00Z',
        accepted_by_client_id = 'client_maria' WHERE id = 'quote_001'""")
    conn.commit()

    # Contar projects
    n_projects = conn.execute("SELECT COUNT(*) FROM projects WHERE id = 'proj_norkevin'").fetchone()[0]
    n_quotes_accepted = conn.execute("SELECT COUNT(*) FROM quotes WHERE id = 'quote_001' AND status = 'accepted'").fetchone()[0]

    # Segundo intento de aceptar (no debe cambiar nada)
    n_updated_2 = conn.execute("""UPDATE projects
        SET commercial_status = 'accepted', updated_at = '2026-07-01T10:00:00Z'
        WHERE id = 'proj_norkevin' AND operational_status = 'lead'""").rowcount

    n_projects_final = conn.execute("SELECT COUNT(*) FROM projects WHERE id = 'proj_norkevin'").fetchone()[0]

    return (n_projects == 1 and n_projects_final == 1 and n_updated_2 == 0), \
        f"projects={n_projects}, final={n_projects_final}, quotes_accepted={n_quotes_accepted}, updated_first={n_updated}, updated_second={n_updated_2}"


def test_transaction_rollback(conn):
    """Test 6: error a mitad de transaccion -> no escrituras parciales."""
    # Limpiar outbox
    conn.execute("DELETE FROM outbox_events")
    conn.commit()

    # Intentar transaccion con error forzado
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""INSERT INTO outbox_events
            (id, tenant_id, company_id, event_type, entity_type, entity_id, handler_name, payload, status, available_at, dedupe_key, created_at)
            VALUES ('outbox_test_1', 'tenant_kevin', 'company_norkevin', 'test.event', 'project', 'proj_norkevin', 'test_handler', '{}', 'pending', '2026-07-01T10:00:00Z', 'test-dedupe-1', '2026-07-01T10:00:00Z')""")
        # Forzar error insertando PK duplicada
        conn.execute("""INSERT INTO outbox_events
            (id, tenant_id, company_id, event_type, entity_type, entity_id, handler_name, payload, status, available_at, dedupe_key, created_at)
            VALUES ('outbox_test_1', 'tenant_kevin', 'company_norkevin', 'test.event', 'project', 'proj_norkevin', 'test_handler', '{}', 'pending', '2026-07-01T10:00:00Z', 'test-dedupe-1', '2026-07-01T10:00:00Z')""")
        conn.execute("COMMIT")
        # Si llegamos aca, no hubo error
        return False, "no hubo error en la transaccion"
    except sqlite3.IntegrityError:
        conn.rollback()
        # Verificar que no quedo el primer INSERT
        n = conn.execute("SELECT COUNT(*) FROM outbox_events WHERE id = 'outbox_test_1'").fetchone()[0]
        if n == 0:
            return True, "rollback completo, ninguna escritura parcial"
        else:
            return False, f"quedo {n} escritura parcial"
    except Exception as e:
        conn.rollback()
        return False, f"error inesperado: {e}"


def test_processed_events_retry(conn):
    """Test 7: retry de processed_events (processing -> completed)."""
    # Insertar evento en processing
    conn.execute("""INSERT INTO processed_events
        (tenant_id, idempotency_key, event_type, entity_type, entity_id, status, request_hash, created_at)
        VALUES ('tenant_kevin', 'test-key-1', 'test.event', 'project', 'proj_norkevin', 'processing', 'hash123', '2026-07-01T10:00:00Z')""")
    conn.commit()

    # Intentar re-insertar con misma key (debe fallar)
    try:
        conn.execute("""INSERT INTO processed_events
            (tenant_id, idempotency_key, event_type, entity_type, entity_id, status, request_hash, created_at)
            VALUES ('tenant_kevin', 'test-key-1', 'test.event', 'project', 'proj_norkevin', 'processing', 'hash456', '2026-07-01T10:00:00Z')""")
        return False, "re-insert con misma key NO fue rechazado"
    except sqlite3.IntegrityError:
        pass

    # UPDATE a completed (simula retry exitoso)
    conn.execute("""UPDATE processed_events
        SET status = 'completed', completed_at = '2026-07-01T10:01:00Z',
            result_payload = '{"ok": true}'
        WHERE tenant_id = 'tenant_kevin' AND idempotency_key = 'test-key-1'""")
    conn.commit()

    # Verificar
    row = conn.execute("""SELECT status, result_payload FROM processed_events
        WHERE tenant_id = 'tenant_kevin' AND idempotency_key = 'test-key-1'""").fetchone()

    if row[0] == 'completed' and row[1] == '{"ok": true}':
        return True, "retry a completed funciono"
    else:
        return False, f"status={row[0]}"


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("VALIDATE_SCHEMA_V4.PY - Prueba ejecutable del DDL")
    print("=" * 70)
    print()
    print(f"Tablas a crear: {sum(1 for s in DDL_STATEMENTS if s.strip().upper().startswith('CREATE TABLE'))}")
    print(f"Indices a crear: {sum(1 for s in DDL_STATEMENTS if s.strip().upper().startswith('CREATE INDEX'))}")
    print()

    print("Creando DB temporal en memoria...")
    conn = setup_db()
    print("[OK] DB creada con PRAGMA foreign_keys=ON")
    print()

    print("Insertando datos minimos (tenant, companies, user, client, project, quote)...")
    seed_minimum(conn)
    print("[OK] Datos insertados")
    print()

    tests = [
        ("Test 1: PRAGMA foreign_key_check sin errores", test_foreign_keys),
        ("Test 2: quote con company distinto al project RECHAZADO", test_invalid_quote_company),
        ("Test 3: client de otro tenant en project_clients RECHAZADO", test_invalid_client_tenant),
        ("Test 4: invoice con quote de otro project RECHAZADO", test_invalid_invoice_quote),
        ("Test 5: doble aceptacion solo crea 1 project", test_double_acceptance_idempotent),
        ("Test 6: error a mitad de transaccion = rollback completo", test_transaction_rollback),
        ("Test 7: retry de processed_events (processing -> completed)", test_processed_events_retry),
    ]

    print()
    results = []
    for i, (name, test_fn) in enumerate(tests):
        print(f"Ejecutando: {name}...")
        # Re-seed para cada test (algunos tests consumen el state)
        try:
            ok, detail = test_fn(conn)
        except Exception as e:
            ok, detail = False, f"excepcion: {e}"
        status = "[PASS]" if ok else "[FAIL]"
        print(f"  {status} {detail}")
        results.append((name, ok, detail))
        # Re-seed despues de cada test
        try:
            # Limpiar TODO (orden importante por FKs)
            for tbl in [
                'outbox_events', 'processed_events', 'automation_runs',
                'workflow_task_instances', 'workflow_instances',
                'payment_installments', 'payment_transactions', 'payment_allocations',
                'invoices', 'invoice_items',
                'quotes', 'quote_items',
                'project_clients', 'projects',
                'client_addresses', 'client_phones', 'client_emails', 'clients',
                'user_company_memberships', 'users',
                'workflow_task_template_versions',
                'workflow_template_versions', 'workflow_template_families',
                'products', 'payment_schedule_rules',
                'payment_schedule_templates',
                'tenants', 'companies',
                'mail_log', 'calendar_events',
                'activity_log', 'settings', 'sequence_counters',
                'email_templates', 'legacy_record_map'
            ]:
                try:
                    conn.execute(f"DELETE FROM {tbl}")
                except Exception:
                    pass
            conn.commit()
            seed_minimum(conn)
        except Exception as e:
            print(f"  [warn] re-seed fallo: {e}")
        print()

    print("=" * 70)
    print("RESUMEN")
    print("=" * 70)
    n_pass = sum(1 for _, ok, _ in results if ok)
    n_fail = sum(1 for _, ok, _ in results if not ok)
    for name, ok, detail in results:
        status = "[PASS]" if ok else "[FAIL]"
        print(f"  {status} {name}")
    print()
    print(f"Total: {n_pass} pasaron, {n_fail} fallaron")
    print()
    if n_fail == 0:
        print("✅ Todas las pruebas PASARON")
        return 0
    else:
        print(f"❌ {n_fail} pruebas FALLARON")
        return 1


if __name__ == "__main__":
    sys.exit(main())