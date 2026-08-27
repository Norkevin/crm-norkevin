"""
Auditoria independiente Codex V5.2.

No usa la DB real, no migra datos y no toca app.py. Cada prueba crea una
SQLite temporal en memoria o un directorio temporal aislado.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCHEMA = ROOT / "schema_v5.2.sql"
VERIFY = ROOT / "verify_v5_consistency.py"
NOW = "2026-07-01T10:00:00Z"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return conn


def object_counts(conn: sqlite3.Connection) -> tuple[int, int, int]:
    tables = conn.execute("""
        SELECT COUNT(*) FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
    """).fetchone()[0]
    indexes = conn.execute("""
        SELECT COUNT(*) FROM sqlite_master
        WHERE type='index' AND name NOT LIKE 'sqlite_%'
    """).fetchone()[0]
    triggers = conn.execute("""
        SELECT COUNT(*) FROM sqlite_master
        WHERE type='trigger'
    """).fetchone()[0]
    return tables, indexes, triggers


def seed_two_companies(conn: sqlite3.Connection) -> None:
    conn.executescript(f"""
        INSERT INTO tenants (id, name, created_at) VALUES
            ('tenant_a', 'Tenant A', '{NOW}'),
            ('tenant_b', 'Tenant B', '{NOW}');
        INSERT INTO companies
        (id, tenant_id, slug, name, logo_letter, color, created_at, updated_at)
        VALUES
            ('company_a', 'tenant_a', 'a', 'A', 'A', '#111111', '{NOW}', '{NOW}'),
            ('company_b', 'tenant_b', 'b', 'B', 'B', '#222222', '{NOW}', '{NOW}');
        INSERT INTO clients (id, tenant_id, first_name, last_name, created_at, updated_at)
        VALUES
            ('client_a', 'tenant_a', 'Ana', 'A', '{NOW}', '{NOW}'),
            ('client_b', 'tenant_b', 'Bea', 'B', '{NOW}', '{NOW}');
        INSERT INTO projects
        (id, tenant_id, company_id, name, type, commercial_status,
         operational_status, created_at, updated_at)
        VALUES
            ('project_a', 'tenant_a', 'company_a', 'Project A', 'boda',
             'new_lead', 'lead', '{NOW}', '{NOW}'),
            ('project_b', 'tenant_b', 'company_b', 'Project B', 'boda',
             'new_lead', 'lead', '{NOW}', '{NOW}');
        INSERT INTO project_clients
        (id, tenant_id, project_id, client_id, role, created_at)
        VALUES
            ('pc_a', 'tenant_a', 'project_a', 'client_a', 'cliente', '{NOW}'),
            ('pc_b', 'tenant_b', 'project_b', 'client_b', 'cliente', '{NOW}');
        INSERT INTO workflow_template_families
        (id, tenant_id, company_id, name, created_at)
        VALUES
            ('family_a', 'tenant_a', 'company_a', 'PRODUCTION', '{NOW}'),
            ('family_b', 'tenant_b', 'company_b', 'PRODUCTION', '{NOW}');
        INSERT INTO workflow_template_versions
        (id, tenant_id, company_id, family_id, version, mode, created_at)
        VALUES
            ('version_a', 'tenant_a', 'company_a', 'family_a', 1, 'dynamic', '{NOW}'),
            ('version_b', 'tenant_b', 'company_b', 'family_b', 1, 'dynamic', '{NOW}');
        INSERT INTO workflow_task_template_versions
        (id, template_version_id, stage, order_index, name, action_type,
         action_config_json, due_rule_mode, active)
        VALUES
            ('task_tpl_a', 'version_a', 'production', 1, 'Task A', 'noop',
             '{{}}', 'manual', 1),
            ('task_tpl_b', 'version_b', 'production', 1, 'Task B', 'noop',
             '{{}}', 'manual', 1);
        INSERT INTO workflow_instances
        (id, tenant_id, company_id, project_id, template_version_id,
         template_version, mode, status, started_at)
        VALUES
            ('workflow_a', 'tenant_a', 'company_a', 'project_a', 'version_a',
             1, 'dynamic', 'active', '{NOW}'),
            ('workflow_b', 'tenant_b', 'company_b', 'project_b', 'version_b',
             1, 'dynamic', 'active', '{NOW}');
        INSERT INTO workflow_task_instances
        (id, workflow_instance_id, template_version_id, task_template_version_id,
         name, status, due_rule_mode, idempotency_key)
        VALUES
            ('task_a', 'workflow_a', 'version_a', 'task_tpl_a',
             'Task A', 'pending', 'manual', 'idem-task-a'),
            ('task_b', 'workflow_b', 'version_b', 'task_tpl_b',
             'Task B', 'pending', 'manual', 'idem-task-b');
        INSERT INTO quotes
        (id, tenant_id, company_id, project_id, billing_project_client_id,
         number, status, subtotal_units, tax_units, total_units,
         currency_code, currency_exponent, created_at)
        VALUES
            ('quote_a', 'tenant_a', 'company_a', 'project_a', 'pc_a',
             'Q-A', 'draft', 1000, 120, 1120, 'GTQ', 2, '{NOW}');
        INSERT INTO products
        (id, tenant_id, company_id, type, name, price_units, currency_code,
         tax_rate_bps, active, created_at)
        VALUES
            ('product_a', 'tenant_a', 'company_a', 'package', 'A',
             1000, 'GTQ', 1200, 1, '{NOW}'),
            ('product_b', 'tenant_b', 'company_b', 'package', 'B',
             1000, 'GTQ', 1200, 1, '{NOW}');
        INSERT INTO quote_items
        (id, quote_id, product_id, name, price_units, subtotal_units)
        VALUES ('quote_item_a', 'quote_a', 'product_a', 'Snapshot', 1000, 1000);
        INSERT INTO invoices
        (id, tenant_id, company_id, project_id, billing_project_client_id,
         quote_id, number, status, subtotal_units, tax_units, total_units,
         currency_code, currency_exponent, created_at)
        VALUES
            ('invoice_a', 'tenant_a', 'company_a', 'project_a', 'pc_a',
             'quote_a', 'INV-A', 'sent', 1000, 120, 1120, 'GTQ', 2, '{NOW}'),
            ('invoice_b', 'tenant_b', 'company_b', 'project_b', 'pc_b',
             NULL, 'INV-B', 'sent', 1000, 120, 1120, 'GTQ', 2, '{NOW}');
        INSERT INTO payment_installments
        (id, invoice_id, number, total_installments, due_date, amount_units, created_at)
        VALUES ('installment_a', 'invoice_a', 1, 1, '2026-07-15', 1000, '{NOW}');
        INSERT INTO payment_transactions
        (id, tenant_id, company_id, project_id, invoice_id, transaction_type,
         amount_units, currency_code, currency_exponent, date, method,
         idempotency_key, status, created_at)
        VALUES ('payment_a', 'tenant_a', 'company_a', 'project_a', 'invoice_a',
                'payment', 700, 'GTQ', 2, '2026-07-01', 'transfer',
                'idem-payment-a', 'confirmed', '{NOW}');
        INSERT INTO payment_allocations
        (id, invoice_id, transaction_id, installment_id, amount_units, created_at)
        VALUES ('allocation_a', 'invoice_a', 'payment_a', 'installment_a', 600, '{NOW}');
        INSERT INTO payment_transactions
        (id, tenant_id, company_id, project_id, invoice_id, original_transaction_id,
         transaction_type, amount_units, currency_code, currency_exponent, date,
         method, idempotency_key, status, created_at)
        VALUES ('refund_a', 'tenant_a', 'company_a', 'project_a', 'invoice_a',
                'payment_a', 'refund', 50, 'GTQ', 2, '2026-07-02',
                'transfer', 'idem-refund-a', 'confirmed', '{NOW}');
    """)
    conn.commit()


def must_fail(name: str, sql: str, expected: str | None = None) -> tuple[bool, str]:
    with connect() as conn:
        seed_two_companies(conn)
        try:
            conn.execute(sql)
            conn.commit()
            return False, f"{name}: se acepto indebidamente"
        except sqlite3.IntegrityError as exc:
            msg = str(exc)
            if expected and expected not in msg:
                return False, f"{name}: mensaje inesperado: {msg}"
            return True, f"{name}: rechazado ({msg})"


def t_inventory() -> tuple[bool, str]:
    with connect() as conn:
        counts = object_counts(conn)
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    ok = counts == (35, 27, 13) and not fk and integrity == "ok"
    return ok, f"counts={counts} fk={len(fk)} integrity={integrity}"


def t_workflow_task_mismatch() -> tuple[bool, str]:
    return must_fail(
        "workflow_task_template_mismatch",
        """
        INSERT INTO workflow_task_instances
        (id, workflow_instance_id, template_version_id, task_template_version_id,
         name, status, due_rule_mode, idempotency_key)
        VALUES ('task_bad', 'workflow_a', 'version_a', 'task_tpl_b',
                'Bad', 'pending', 'manual', 'idem-task-bad')
        """,
    )


def t_automation_project_mismatch() -> tuple[bool, str]:
    return must_fail(
        "automation_project_mismatch",
        """
        INSERT INTO automation_runs
        (id, tenant_id, company_id, workflow_instance_id, project_id,
         event_type, idempotency_key)
        VALUES ('ar_bad_project', 'tenant_a', 'company_a', 'workflow_a',
                'project_b', 'evt', 'idem-ar-bad-project')
        """,
    )


def t_automation_workflow_mismatch() -> tuple[bool, str]:
    return must_fail(
        "automation_workflow_mismatch",
        """
        INSERT INTO automation_runs
        (id, tenant_id, company_id, workflow_instance_id, project_id,
         event_type, idempotency_key)
        VALUES ('ar_bad_workflow', 'tenant_a', 'company_a', 'workflow_b',
                'project_a', 'evt', 'idem-ar-bad-workflow')
        """,
    )


def t_quote_item_update_mismatch() -> tuple[bool, str]:
    return must_fail(
        "quote_item_update_product_mismatch",
        "UPDATE quote_items SET product_id='product_b' WHERE id='quote_item_a'",
        "quote_item_product_company_mismatch",
    )


def t_payment_below_allocations() -> tuple[bool, str]:
    return must_fail(
        "payment_below_allocations",
        "UPDATE payment_transactions SET amount_units=500 WHERE id='payment_a'",
        "payment_amount_below_existing_allocations",
    )


def t_payment_with_refund_locked() -> tuple[bool, str]:
    return must_fail(
        "payment_with_refund_locked",
        "UPDATE payment_transactions SET status='reversed' WHERE id='payment_a'",
        "payment_original_has_refunds_locked",
    )


def t_payment_invoice_update_mismatch() -> tuple[bool, str]:
    ok, detail = must_fail(
        "payment_invoice_update_mismatch",
        "UPDATE payment_transactions SET invoice_id='invoice_b' WHERE id='payment_a'",
    )
    valid_messages = (
        "payment_original_has_refunds_locked",
        "payment_tx_project_mismatch_invoice",
    )
    return ok and any(msg in detail for msg in valid_messages), detail


def t_calendar_all_day_check() -> tuple[bool, str]:
    return must_fail(
        "calendar_all_day_check",
        """
        INSERT INTO calendar_events
        (id, tenant_id, company_id, type, title, all_day, start_date,
         start_at, timezone, created_at)
        VALUES ('cal_bad', 'tenant_a', 'company_a', 'event', 'Bad',
                1, '2026-07-20', '2026-07-20T10:00:00Z',
                'America/Guatemala', '2026-07-01T10:00:00Z')
        """,
    )


def t_rollback_atomicity() -> tuple[bool, str]:
    with connect() as conn:
        seed_two_companies(conn)
        before = conn.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0]
        try:
            conn.execute("BEGIN")
            conn.execute("""
                INSERT INTO outbox_events
                (id, tenant_id, company_id, event_type, entity_type, entity_id,
                 handler_name, payload, available_at, dedupe_key, created_at)
                VALUES ('out_atomic', 'tenant_a', 'company_a', 'evt', 'x', 'x',
                        'handler', '{}', '2026-07-01T10:00:00Z',
                        'dedupe-atomic', '2026-07-01T10:00:00Z')
            """)
            conn.execute("""
                INSERT INTO automation_runs
                (id, tenant_id, company_id, workflow_instance_id, project_id,
                 event_type, idempotency_key)
                VALUES ('ar_atomic_bad', 'tenant_a', 'company_a', 'workflow_b',
                        'project_a', 'evt', 'idem-ar-atomic-bad')
            """)
            conn.commit()
            return False, "rollback: transaccion invalida fue aceptada"
        except sqlite3.IntegrityError:
            conn.rollback()
        after = conn.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0]
    return before == after, f"rollback: before={before} after={after}"


def t_verifier_detects_missing_trigger() -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="codex_v52_verify_") as tmp:
        tmp_path = Path(tmp)
        shutil.copy2(VERIFY, tmp_path / "verify_v5_consistency.py")
        sql = SCHEMA.read_text(encoding="utf-8")
        marker = "CREATE TRIGGER trg_quote_item_product_same_company_update"
        start = sql.index(marker)
        end = sql.index("\nEND;", start) + len("\nEND;")
        mutated = sql[:start] + "-- trigger removed by codex audit\n" + sql[end:]
        (tmp_path / "schema_v5.2.sql").write_text(mutated, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "verify_v5_consistency.py"],
            cwd=tmp_path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
        )
    ok = (
        result.returncode != 0
        and "FALTA: trg_quote_item_product_same_company_update" in result.stdout
    )
    return ok, f"returncode={result.returncode}"


TESTS = [
    ("Inventario + PRAGMAs", t_inventory),
    ("Workflow task mismatch", t_workflow_task_mismatch),
    ("Automation project mismatch", t_automation_project_mismatch),
    ("Automation workflow mismatch", t_automation_workflow_mismatch),
    ("Quote item UPDATE mismatch", t_quote_item_update_mismatch),
    ("Payment below allocations", t_payment_below_allocations),
    ("Payment with refund locked", t_payment_with_refund_locked),
    ("Payment invoice UPDATE mismatch", t_payment_invoice_update_mismatch),
    ("Calendar all_day CHECK", t_calendar_all_day_check),
    ("Rollback atomicity", t_rollback_atomicity),
    ("Verifier mutation catches missing trigger", t_verifier_detects_missing_trigger),
]


def main() -> int:
    print("=" * 80)
    print("CODEX_AUDIT_V5_2.PY - auditoria independiente")
    print("=" * 80)
    print(f"Python: {sys.version}")
    print(f"SQLite: {sqlite3.sqlite_version}")
    print(f"schema_v5.2.sql SHA-256: {sha256(SCHEMA)}")
    print(f"PID: {os.getpid()}")
    print()

    failures = 0
    for name, fn in TESTS:
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"EXCEPCION {type(exc).__name__}: {exc}"
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        failures += 0 if ok else 1

    print()
    print(f"Resultado: {len(TESTS) - failures} PASS / {failures} FAIL")
    print("Confirmacion: app.py NO; crm.db NO; JSON NO; Alembic NO")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
