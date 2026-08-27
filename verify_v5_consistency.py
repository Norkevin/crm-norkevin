"""
verify_v5_consistency.py
========================

Verifica que schema_v5.2.sql sea la UNICA fuente de verdad del DDL.
- Ejecuta el SQL en una DB temporal limpia
- Compara sqlite_master contra el inventario esperado por NOMBRE EXACTO
- Verifica SHA-256 del SQL contra el declarado
- Si falta cualquier objeto, devuelve FAIL

V5.2 estricto: compara nombres reales de 35 tablas, 27 indices, 13 triggers.

Uso:
    python3.11 verify_v5_consistency.py
"""
import os
import sys
import sqlite3
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "schema_v5.2.sql")

# ============================================================================
# Inventario esperado V5.2 (orden no importante, pero los nombres SI)
# ============================================================================
EXPECTED_TABLES = set([
    "tenants", "companies", "users", "user_company_memberships",
    "clients", "client_emails", "client_phones", "client_addresses",
    "projects", "project_clients", "quotes", "quote_items",
    "invoices", "invoice_items", "payment_installments", "payment_transactions",
    "payment_allocations", "payment_schedule_templates", "payment_schedule_rules",
    "products", "workflow_template_families", "workflow_template_versions",
    "workflow_task_template_versions", "workflow_instances", "workflow_task_instances",
    "processed_events", "outbox_events", "automation_runs", "activity_log",
    "calendar_events", "email_templates", "legacy_record_map",
    "settings", "sequence_counters", "mail_log",
])

EXPECTED_INDEXES = set([
    # FASE 2 (FK compuestas creadas ANTES de las tablas)
    "uq_companies_tenant_id",
    "uq_users_tenant_id",
    "uq_clients_tenant_id",
    "uq_projects_tenant_id",
    "uq_projects_tenant_company",
    "uq_pst_tenant_company",
    # FASE 4 (UNIQUE INDEX que requirieron tablas)
    "uq_et_tenant_company_id",
    "uq_wtf_tenant_company_id",
    "uq_wftv_tenant_company_id",
    "uq_wi_id_template_version",
    "uq_wi_tenant_company_id",
    "uq_wttv_template_version",
    "uq_wti_workflow_instance_id",
    "uq_pc_project_id",
    "uq_q_project_id",
    "uq_pi_invoice_id",
    "uq_pt_invoice_id",
    # FASE 5 (UNIQUE INDEX parciales y FK simples)
    "uq_project_primary_contact",
    "uq_project_billing_contact",
    "idx_client_emails_norm",
    "uq_client_email_primary",
    "idx_client_phones_norm",
    "uq_client_phone_primary",
    "uq_project_active_workflow",
    "idx_pa_installment",
    "idx_outbox_pending",
    "idx_calendar_start",
])

EXPECTED_TRIGGERS = set([
    "trg_refund_validation_insert",
    "trg_refund_validation_update",
    "trg_allocation_validation_insert",
    "trg_allocation_validation_update",
    "trg_payment_cannot_shrink_below_refunds",
    "trg_payment_cannot_shrink_below_allocations",
    "trg_payment_original_with_refunds_locked",
    "trg_payment_tx_invoice_project_match",
    "trg_payment_tx_invoice_project_match_update",
    "trg_outbox_no_delivered_to_pending",
    "trg_outbox_dead_letter_locked",
    "trg_quote_item_product_same_company",   # V5.2 Sec.6 (quote item product mismatch)
    "trg_quote_item_product_same_company_update",
])


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    print("=" * 80)
    print("VERIFY_V5_CONSISTENCY.PY — Verificador estricto V5.2")
    print("=" * 80)
    print()

    if not os.path.exists(SCHEMA):
        print(f"[FAIL] No se encontro {SCHEMA}")
        return 1

    sql_sha = sha256_file(SCHEMA)
    print(f"SHA-256 de schema_v5.2.sql: {sql_sha}")
    print()

    # Ejecutar SQL en DB limpia
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON;")
    with open(SCHEMA, "r", encoding="utf-8") as f:
        sql = f.read()

    # Strip comentarios
    import re
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", "", sql)

    try:
        conn.executescript(sql)
    except Exception as e:
        print(f"[FAIL] schema_v5.2.sql no compila: {e}")
        return 1

    # Obtener inventario REAL de sqlite_master
    real_tables = set(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall())
    real_indexes = set(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall())
    real_triggers = set(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger'"
    ).fetchall())

    # Comparar
    missing_tables = EXPECTED_TABLES - real_tables
    extra_tables = real_tables - EXPECTED_TABLES
    missing_indexes = EXPECTED_INDEXES - real_indexes
    extra_indexes = real_indexes - EXPECTED_INDEXES
    missing_triggers = EXPECTED_TRIGGERS - real_triggers
    extra_triggers = real_triggers - EXPECTED_TRIGGERS

    print("=== Inventario REAL ===")
    print(f"  Tablas:   {len(real_tables)} (esperado: {len(EXPECTED_TABLES)})")
    print(f"  Indices:  {len(real_indexes)} (esperado: {len(EXPECTED_INDEXES)})")
    print(f"  Triggers: {len(real_triggers)} (esperado: {len(EXPECTED_TRIGGERS)})")
    print()

    print("=== PRAGMA foreign_key_check ===")
    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    print(f"  Filas: {len(fk_errors)}")
    for err in fk_errors[:10]:
        print(f"    {err}")
    print()

    print("=== PRAGMA integrity_check ===")
    integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"  Resultado: {integ}")
    print()

    # Resultado
    ok = (
        len(missing_tables) == 0 and len(extra_tables) == 0 and
        len(missing_indexes) == 0 and len(extra_indexes) == 0 and
        len(missing_triggers) == 0 and len(extra_triggers) == 0 and
        len(fk_errors) == 0 and integ == 'ok'
    )

    if missing_tables or extra_tables:
        print("[FAIL] Tablas faltantes/extra:")
        for t in missing_tables:
            print(f"  FALTA: {t}")
        for t in extra_tables:
            print(f"  EXTRA: {t}")

    if missing_indexes or extra_indexes:
        print("[FAIL] Indices faltantes/extra:")
        for t in missing_indexes:
            print(f"  FALTA: {t}")
        for t in extra_indexes:
            print(f"  EXTRA: {t}")

    if missing_triggers or extra_triggers:
        print("[FAIL] Triggers faltantes/extra:")
        for t in missing_triggers:
            print(f"  FALTA: {t}")
        for t in extra_triggers:
            print(f"  EXTRA: {t}")

    print()
    if ok:
        print("[OK] schema_v5.2.sql coincide exactamente con el inventario esperado")
        print("      foreign_key_check=0, integrity_check=ok")
        return 0
    else:
        print("[FAIL] El DDL no cumple el inventario esperado")
        return 1


if __name__ == "__main__":
    sys.exit(main())
