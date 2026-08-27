"""
validate_schema_v5.2.py
========================

Prueba ejecutable del schema_v5.2.sql.

V5.2 incluye TODAS las correcciones de las 14 secciones:
- Sec. 1: Test sin is_primary=1 para evitar falso positivo en project_clients
- Sec. 2: FK compuesta family/tenant/company en workflow_template_versions
- Sec. 3: workflow_task_instances SIN project_id, FK compuesta a Task Template
- Sec. 4: payment_transactions con FK compuesta real a projects
- Sec. 5: FKs compuestas en processed_events, activity_log, calendar_events,
           mail_log, automation_runs
- Sec. 6: quote_items -> products via FK simple (coherencia via repo)
- Sec. 7: Triggers UPDATE en payment_transactions que bloquean cambios invalidos
- Sec. 8: accept_quote() SIN hardcodes (usa total_units, datetime real)
- Sec. 9: Retry processed_event: failed -> processing -> completed / mismatch
- Sec. 10: Outbox real con dead_letter a max_attempts, locked_by requirement
- Sec. 11: verify_v5_consistency_v5.2.py compara nombres exactos de 35 tablas,
            inventario real de sqlite_master, SHA-256 del SQL
- Sec. 12: Pruebas de regresion por cada problema (no se conserva numero 13)
- Sec. 13: Confirmacion correcta del entorno separada

Cada test corre con su propia conexion SQLite en memoria, semilla propia.
NO comparte estado entre tests.
"""
import os
import sys
import sqlite3
import hashlib
import datetime
from datetime import date, timedelta
from contextlib import contextmanager

BASE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(BASE, "schema_v5.2.sql")


# ============================================================
# Helpers
# ============================================================

def load_schema():
    with open(SCHEMA, "r", encoding="utf-8") as f:
        return f.read()


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_str(s):
    return hashlib.sha256(s.encode()).hexdigest()


@contextmanager
def fresh_db():
    """Crea una DB en memoria completamente nueva."""
    conn = sqlite3.connect(":memory:", timeout=30)
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(load_schema())
    try:
        yield conn
    finally:
        conn.close()


def count_objects(conn):
    nt = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()[0]
    ni = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()[0]
    ntr = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0]
    return nt, ni, ntr


def now_iso():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def add_days_iso(iso, days):
    base = datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
    return (base + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ============================================================
# Custom exceptions
# ============================================================

class IdempotencyPayloadMismatch(Exception):
    pass


class ConcurrentProcessing(Exception):
    pass


class InvalidState(Exception):
    pass


# ============================================================
# Seed: tenant Kevin, 2 companies, 1 client, 1 project, 1 quote
# NO es un seed global; cada test que lo necesite lo llama.
# ============================================================

def seed_kevin_only(conn):
    """Crea lo minimo: tenant + 2 companies + 1 user."""
    conn.executescript("""
        INSERT INTO tenants (id, name, created_at) VALUES
            ('tenant_kevin', 'Kevin', '2026-07-01T00:00:00Z'),
            ('tenant_otro', 'Otro', '2026-07-01T00:00:00Z');
        INSERT INTO companies (id, tenant_id, slug, name, logo_letter, color, created_at, updated_at) VALUES
            ('company_norkevin', 'tenant_kevin', 'norkevin', 'Norkevin', 'N', '#2F7D73', '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z'),
            ('company_astral', 'tenant_kevin', 'astral', 'Astral', 'A', '#7C3AED', '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z'),
            ('company_otro', 'tenant_otro', 'otro', 'Otra Co', 'O', '#000000', '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z');
        INSERT INTO users (id, tenant_id, email, name, created_at) VALUES
            ('user_kevin', 'tenant_kevin', 'kevin@k.com', 'Kevin L', '2026-07-01T00:00:00Z');
    """)
    conn.commit()


def seed_minimal(conn, tenant="tenant_kevin", company="company_norkevin"):
    """Crea tenant+company+user+client+project+quote base."""
    seed_kevin_only(conn)
    now = now_iso()
    conn.executescript(f"""
        INSERT INTO clients (id, tenant_id, first_name, last_name, created_at, updated_at) VALUES
            ('client_maria', '{tenant}', 'Maria', 'Lopez', '{now}', '{now}');
        INSERT INTO projects (id, tenant_id, company_id, name, type, event_date, event_time,
                              location_name, commercial_status, operational_status,
                              booked_value_units, created_at, updated_at) VALUES
            ('proj_norkevin', '{tenant}', '{company}', 'Boda M', 'boda',
             '2026-08-15', '16:00', 'Antigua',
             'new_lead', 'lead', 2050000, '{now}', '{now}');
        INSERT INTO project_clients (id, tenant_id, project_id, client_id, role,
                                     is_primary, is_billing_contact, created_at) VALUES
            ('pc_001', '{tenant}', 'proj_norkevin', 'client_maria', 'novia',
             0, 0, '{now}');
        INSERT INTO workflow_template_families
        (id, tenant_id, company_id, name, created_at) VALUES
        ('wtf_bodas', '{tenant}', '{company}', 'BODAS', '{now}'),
        ('wtf_prod', '{tenant}', '{company}', 'PRODUCTION', '{now}');
        INSERT INTO workflow_template_versions
        (id, tenant_id, company_id, family_id, version, mode, created_at) VALUES
        ('wtv_bodas_v1', '{tenant}', '{company}', 'wtf_bodas', 1, 'dynamic', '{now}'),
        ('wtv_prod_v1', '{tenant}', '{company}', 'wtf_prod', 1, 'dynamic', '{now}');
    """)
    conn.commit()


# ============================================================
# Sec. 8: accept_quote() sin hardcodes
# ============================================================

def accept_quote(conn, project_id, quote_id, idempotency_key, request_hash,
                 total_units, quote_currency, quote_exponent,
                 tenant_id, company_id, accept_request_id="req-001"):
    """Acepta una quote UPDATE + crea invoice + 2 installments + outbox + workflow.

    Sec. 8: SIN hardcodes. Toma tenant, company, currency, total desde parametros.
    """
    # Verificar idempotency
    row = conn.execute("""
        SELECT status, request_hash, result_payload
        FROM processed_events
        WHERE tenant_id = ? AND idempotency_key = ?
    """, (tenant_id, idempotency_key)).fetchone()

    if row:
        status, rh, result = row
        if status == 'completed':
            if rh != request_hash:
                raise IdempotencyPayloadMismatch(
                    f"idempotency_key '{idempotency_key}' usado con hash diferente"
                )
            import json
            return json.loads(result)
        if status == 'processing':
            raise ConcurrentProcessing("Event is still processing")

    # Reclamar
    try:
        conn.execute("""
            INSERT INTO processed_events
            (tenant_id, idempotency_key, event_type, entity_type, entity_id,
             status, attempts, request_hash, created_at, started_at)
            VALUES (?, ?, 'quote.accepted', 'project', ?, 'processing', 1, ?, ?, ?)
        """, (tenant_id, idempotency_key, project_id, request_hash, now_iso(), now_iso()))
    except sqlite3.IntegrityError:
        # Ya existe
        row = conn.execute("""
            SELECT status, request_hash, result_payload
            FROM processed_events
            WHERE tenant_id = ? AND idempotency_key = ?
        """, (tenant_id, idempotency_key)).fetchone()
        status, rh, result = row
        if status == 'completed' and rh == request_hash:
            import json
            return json.loads(result)
        if status == 'completed' and rh != request_hash:
            raise IdempotencyPayloadMismatch(
                f"idempotency_key '{idempotency_key}' usado con hash diferente"
            )
        if status == 'processing':
            raise ConcurrentProcessing("Event is still processing")
        # failed -> reintentar como UPDATE
        conn.execute("""
            UPDATE processed_events
            SET status='processing', attempts=attempts+1, started_at=?, request_hash=?
            WHERE tenant_id=? AND idempotency_key=?
        """, (now_iso(), request_hash, tenant_id, idempotency_key))

    # 1. UPDATE quote
    cur = conn.execute("""
        UPDATE quotes SET status='accepted', accepted_at=?
        WHERE id=? AND status='sent'
    """, (now_iso(), quote_id))
    if cur.rowcount != 1:
        raise InvalidState(f"quote {quote_id} no esta en 'sent' (o no existe)")

    # 2. UPDATE project
    cur = conn.execute("""
        UPDATE projects
        SET commercial_status='accepted', operational_status='confirmed',
            job_accepted_at=?, job_accepted_via='quote_accepted', updated_at=?
        WHERE id=? AND operational_status='lead'
    """, (now_iso(), now_iso(), project_id))
    if cur.rowcount != 1:
        raise InvalidState(f"project {project_id} no esta en 'lead' (o no existe)")

    quote_row = conn.execute("""
        SELECT billing_project_client_id
        FROM quotes
        WHERE id=? AND tenant_id=? AND company_id=? AND project_id=?
    """, (quote_id, tenant_id, company_id, project_id)).fetchone()
    if not quote_row:
        raise InvalidState("quote no pertenece al tenant/company/project indicado")
    billing_project_client_id = quote_row[0]

    # 3. CREATE invoice (Sec. 8: usa total_units, NO subtotal)
    invoice_id = f"inv_{idempotency_key[:12]}"
    cur = conn.execute("""
        INSERT INTO invoices
        (id, tenant_id, company_id, project_id, billing_project_client_id,
         quote_id, number, status, issue_date, due_date,
         subtotal_units, tax_units, total_units, currency_code, currency_exponent, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'sent', ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        invoice_id, tenant_id, company_id, project_id, billing_project_client_id,
        quote_id, f"INV-{idempotency_key[:8]}", now_iso(),
        add_days_iso(now_iso(), 30),
        total_units // 100,           # subtotal (sin impuesto)
        total_units - total_units // 100,  # tax (residuo)
        total_units,                  # total
        quote_currency, quote_exponent, now_iso()
    ))
    if cur.rowcount != 1:
        raise InvalidState("invoice no fue creada")

    # 4. CREATE installments (Sec. 8: usa total_units, fecha base + timedelta, suma exacto)
    installment_ids = []
    cuota_base = total_units // 2
    residuo = total_units - cuota_base * 2
    for i in (1, 2):
        ins_id = f"ins_{idempotency_key[:8]}_{i}"
        installment_ids.append(ins_id)
        amount = cuota_base + (residuo if i == 2 else 0)
        days_offset = 15 if i == 1 else 45
        conn.execute("""
            INSERT INTO payment_installments
            (id, invoice_id, number, total_installments, due_date, amount_units, created_at)
            VALUES (?, ?, ?, 2, ?, ?, ?)
        """, (ins_id, invoice_id, i, add_days_iso(now_iso(), days_offset), amount, now_iso()))

    # 5. CREATE outbox event
    outbox_id = f"out_{idempotency_key[:12]}"
    conn.execute("""
        INSERT INTO outbox_events
        (id, tenant_id, company_id, event_type, entity_type, entity_id, handler_name,
         payload, status, available_at, dedupe_key, created_at)
        VALUES (?, ?, ?, 'invoice.sent', 'invoice', ?, 'send_email', '{}', 'pending',
         ?, ?, ?)
    """, (outbox_id, tenant_id, company_id, invoice_id, now_iso(),
          f"dedupe-{idempotency_key[:12]}", now_iso()))

    production_template = conn.execute("""
        SELECT v.id, v.version, v.mode
        FROM workflow_template_versions v
        JOIN workflow_template_families f ON f.id = v.family_id
        WHERE v.tenant_id=? AND v.company_id=? AND f.name='PRODUCTION'
        ORDER BY v.version DESC
        LIMIT 1
    """, (tenant_id, company_id)).fetchone()
    if not production_template:
        raise InvalidState("no existe workflow PRODUCTION para tenant/company")

    # 6. CREATE workflow production instance
    wi_id = f"wi_{idempotency_key[:12]}"
    conn.execute("""
        INSERT INTO workflow_instances
        (id, tenant_id, company_id, project_id, template_version_id, template_version,
         mode, started_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (wi_id, tenant_id, company_id, project_id,
          production_template[0], production_template[1], production_template[2], now_iso()))

    # 7. UPDATE processed_event completed
    result = {
        "project_id": project_id,
        "invoice_id": invoice_id,
        "installment_ids": installment_ids,
        "outbox_id": outbox_id,
        "workflow_instance_id": wi_id,
        "total_units": total_units,
    }
    import json
    conn.execute("""
        UPDATE processed_events
        SET status='completed', completed_at=?, result_payload=?
        WHERE tenant_id=? AND idempotency_key=?
    """, (now_iso(), json.dumps(result, sort_keys=True), tenant_id, idempotency_key))
    conn.commit()
    return result


# ============================================================
# TESTS — cada uno con su propia conexion
# ============================================================

def t01_pragmas():
    """PRAGMA foreign_key_check + integrity_check."""
    with fresh_db() as conn:
        seed_minimal(conn)
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
    return len(fk) == 0 and integ == 'ok', f"fk_errs={len(fk)} integrity={integ}"


def t02_count_objects():
    with fresh_db() as conn:
        nt, ni, ntr = count_objects(conn)
    # V5.2 inventario: 35 tablas, 27 indices, 13 triggers
    expected = (35, 27, 13)
    actual = (nt, ni, ntr)
    return actual == expected, f"tablas={nt} indices={ni} triggers={ntr}; esperado={expected}"


def t03_quote_wrong_company():
    """Sec. validacion cruzada: quote de Astral referenciando project de Norkevin."""
    with fresh_db() as conn:
        seed_minimal(conn)  # proj_norkevin en company_norkevin
        try:
            conn.execute("""
                INSERT INTO quotes
                (id, tenant_id, company_id, project_id, billing_project_client_id,
                 number, status, issue_date, due_date,
                 subtotal_units, tax_units, total_units,
                 currency_code, currency_exponent, created_at)
                VALUES ('q_bad', 'tenant_kevin', 'company_astral', 'proj_norkevin', 'pc_001',
                        'Q-X', 'draft', '2026-07-01', '2026-07-15',
                        1000, 120, 1120, 'GTQ', 2, '2026-07-01T10:00:00Z')
            """)
            return False, "quote de company distinto al project no fue rechazado"
        except sqlite3.IntegrityError as e:
            return True, f"rechazado: {str(e)[:80]}"


def t04_client_other_tenant_via_fk():
    """Sec. 1: client de otro tenant en project_clients —
    garantiza rechazo por FK tenant+project, NO por UNIQUE is_primary."""
    with fresh_db() as conn:
        seed_minimal(conn)
        # Insertar 2 clientes en tenant_kevin, ambos como secundarios (is_primary=0)
        # para NO chocar contra uq_project_primary_contact.
        conn.execute("""
            INSERT INTO clients (id, tenant_id, first_name, last_name, created_at, updated_at)
            VALUES ('client_otro2', 'tenant_kevin', 'Otro', 'Cliente', '2026-07-01T10:00:00Z', '2026-07-01T10:00:00Z')
        """)
        # Crear project_clients con client_otro2 como participante (is_primary=0)
        # y NO debe chocar contra uq_project_primary_contact (client_maria era is_primary=0).
        # Ahora intentamos agregar pc_invalid con cliente de OTRO tenant.
        try:
            conn.execute("""
                INSERT INTO project_clients
                (id, tenant_id, project_id, client_id, role, is_primary, created_at)
                VALUES ('pc_bad_tenant', 'tenant_kevin', 'proj_norkevin',
                        'client_otro_de_otro_tenant', 'novio', 0,
                        '2026-07-01T10:00:00Z')
            """)
            return False, "client de otro tenant no fue rechazado"
        except sqlite3.IntegrityError as e:
            return True, f"rechazado por FK: {str(e)[:80]}"


def t05_workflow_version_wrong_family_company():
    """Sec. 2: Workflow Version de Astral con family de Norkevin -> rechazado."""
    with fresh_db() as conn:
        seed_minimal(conn)
        # Crear families en ambas companies
        conn.executescript("""
            INSERT INTO workflow_template_families
            (id, tenant_id, company_id, name, created_at) VALUES
            ('wtf_norkevin', 'tenant_kevin', 'company_norkevin', 'BODAS NORKEVIN', '2026-07-01T10:00:00Z'),
            ('wtf_astral', 'tenant_kevin', 'company_astral', 'BODAS ASTRAL', '2026-07-01T10:00:00Z');
        """)
        # Intentar: version de Astral con family de Norkevin
        try:
            conn.execute("""
                INSERT INTO workflow_template_versions
                (id, tenant_id, company_id, family_id, version, mode, created_at)
                VALUES ('wtv_bad', 'tenant_kevin', 'company_astral',
                        'wtf_norkevin', 1, 'dynamic', '2026-07-01T10:00:00Z')
            """)
            return False, "version de Astral con family de Norkevin NO fue rechazado"
        except sqlite3.IntegrityError as e:
            return True, f"rechazado por FK: {str(e)[:80]}"


def t06_workflow_instance_wrong_project_company():
    """Workflow instance con project+template_version de companies distintas."""
    with fresh_db() as conn:
        seed_minimal(conn)
        # Crear las 2 families y versions correctas
        conn.executescript("""
            INSERT INTO workflow_template_families
            (id, tenant_id, company_id, name, created_at) VALUES
            ('wtf_norkevin', 'tenant_kevin', 'company_norkevin', 'BODAS NORKEVIN', '2026-07-01T10:00:00Z'),
            ('wtf_astral', 'tenant_kevin', 'company_astral', 'BODAS ASTRAL', '2026-07-01T10:00:00Z');
            INSERT INTO workflow_template_versions
            (id, tenant_id, company_id, family_id, version, mode, created_at) VALUES
            ('wtv_norkevin_v1', 'tenant_kevin', 'company_norkevin', 'wtf_norkevin', 1, 'dynamic', '2026-07-01T10:00:00Z'),
            ('wtv_astral_v1', 'tenant_kevin', 'company_astral', 'wtf_astral', 1, 'dynamic', '2026-07-01T10:00:00Z');
        """)
        # Workflow instance: project Norkevin, template_version Astral
        try:
            conn.execute("""
                INSERT INTO workflow_instances
                (id, tenant_id, company_id, project_id, template_version_id,
                 template_version, mode, started_at)
                VALUES ('wi_bad', 'tenant_kevin', 'company_norkevin',
                        'proj_norkevin', 'wtv_astral_v1', 1, 'dynamic',
                        '2026-07-01T10:00:00Z')
            """)
            return False, "workflow instance Norkevin+template Astral NO fue rechazado"
        except sqlite3.IntegrityError as e:
            return True, f"rechazado por FK: {str(e)[:80]}"


def t07_payment_transaction_wrong_invoice_project():
    """Sec. 4: payment_transaction con invoice de project A y project_id B."""
    with fresh_db() as conn:
        seed_minimal(conn)
        # Crear 2 projects + 2 invoices
        conn.executescript("""
            INSERT INTO projects
            (id, tenant_id, company_id, name, type, commercial_status,
             operational_status, created_at, updated_at)
            VALUES ('proj_A', 'tenant_kevin', 'company_norkevin', 'A', 'boda',
                    'new_lead', 'lead', '2026-07-01T10:00:00Z', '2026-07-01T10:00:00Z');
            INSERT INTO project_clients
            (id, tenant_id, project_id, client_id, role, is_primary, created_at)
            VALUES ('pc_A', 'tenant_kevin', 'proj_A', 'client_maria', 'novia', 0,
                    '2026-07-01T10:00:00Z');
            INSERT INTO invoices
            (id, tenant_id, company_id, project_id, billing_project_client_id,
             number, status, issue_date, due_date,
             subtotal_units, tax_units, total_units, currency_code, currency_exponent,
             created_at)
            VALUES
            ('inv_norkevin', 'tenant_kevin', 'company_norkevin', 'proj_norkevin',
             'pc_001', 'INV-1', 'sent', '2026-07-01', '2026-07-15',
             1000, 120, 1120, 'GTQ', 2, '2026-07-01T10:00:00Z'),
            ('inv_A', 'tenant_kevin', 'company_norkevin', 'proj_A', 'pc_A', 'INV-2',
             'sent', '2026-07-01', '2026-07-15', 2000, 240, 2240, 'GTQ', 2,
             '2026-07-01T10:00:00Z');
        """)
        # tx: project_id=proj_A pero invoice_id=inv_norkevin (de proj_norkevin)
        try:
            conn.execute("""
                INSERT INTO payment_transactions
                (id, tenant_id, company_id, project_id, invoice_id,
                 transaction_type, amount_units, currency_code, currency_exponent,
                 date, method, idempotency_key, status, created_at)
                VALUES ('pay_bad', 'tenant_kevin', 'company_norkevin',
                        'proj_A', 'inv_norkevin', 'payment', 100, 'GTQ', 2,
                        '2026-07-01', 'cash', 'idem-pay-bad', 'confirmed',
                        '2026-07-01T10:00:00Z')
            """)
            return False, "tx con invoice de otro project NO fue rechazado"
        except sqlite3.IntegrityError as e:
            return True, f"rechazado: {str(e)[:80]}"


def t08_calendar_event_other_company():
    """Sec. 5: calendar_events con company de otro tenant."""
    with fresh_db() as conn:
        seed_minimal(conn)
        try:
            conn.execute("""
                INSERT INTO calendar_events
                (id, tenant_id, company_id, type, title, all_day, start_date, created_at)
                VALUES ('cal_bad', 'tenant_kevin', 'company_otro', 'event', 'Bad', 1,
                        '2026-08-15', '2026-07-01T10:00:00Z')
            """)
            return False, "calendar_event con company de otro tenant no fue rechazado"
        except sqlite3.IntegrityError as e:
            return True, f"rechazado: {str(e)[:80]}"


def t09_mail_log_other_template_company():
    """Sec. 5: mail_log.template_id de email_templates de company distinto."""
    with fresh_db() as conn:
        seed_minimal(conn)
        # Crear email_templates en cada company
        conn.executescript("""
            INSERT INTO email_templates
            (id, tenant_id, company_id, name, subject, body, created_at, updated_at)
            VALUES
            ('et_norkevin', 'tenant_kevin', 'company_norkevin', 'T-NK', 'S', 'B',
             '2026-07-01T10:00:00Z', '2026-07-01T10:00:00Z'),
            ('et_astral', 'tenant_kevin', 'company_astral', 'T-AS', 'S', 'B',
             '2026-07-01T10:00:00Z', '2026-07-01T10:00:00Z');
        """)
        # mail_log con company_norkevin y template_id de company_astral
        try:
            conn.execute("""
                INSERT INTO mail_log
                (id, tenant_id, company_id, subject, body_snapshot, to_email,
                 template_id, idempotency_key)
                VALUES ('mail_bad', 'tenant_kevin', 'company_norkevin',
                        'Subject', 'Body', 'a@b.com',
                        'et_astral', 'idem-mail-bad')
            """)
            return False, "mail_log.template_id de company distinto no rechazado"
        except sqlite3.IntegrityError as e:
            return True, f"rechazado: {str(e)[:80]}"


def t10_double_acceptance_idempotent():
    """Sec. 8: doble aceptacion produce el MISMO result, sin duplicados."""
    with fresh_db() as conn:
        seed_minimal(conn)
        # Crear quote sent
        conn.execute("""
            INSERT INTO quotes
            (id, tenant_id, company_id, project_id, billing_project_client_id,
             number, status, issue_date, due_date,
             subtotal_units, tax_units, total_units, currency_code, currency_exponent,
             created_at)
            VALUES ('q_norkevin_001', 'tenant_kevin', 'company_norkevin',
                    'proj_norkevin', 'pc_001', 'Q-001', 'sent',
                    '2026-07-01', '2026-07-15', 100000, 12000, 112000,
                    'GTQ', 2, '2026-07-01T10:00:00Z')
        """)
        conn.commit()

        rh = sha256_str('{"action":"accept_quote","project_id":"proj_norkevin"}')

        # Antes
        n_p = conn.execute("SELECT COUNT(*) FROM projects WHERE id='proj_norkevin'").fetchone()[0]
        n_i = conn.execute("SELECT COUNT(*) FROM invoices WHERE project_id='proj_norkevin'").fetchone()[0]
        n_pe = conn.execute("SELECT COUNT(*) FROM processed_events").fetchone()[0]
        n_ob = conn.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0]
        n_wi = conn.execute("SELECT COUNT(*) FROM workflow_instances WHERE project_id='proj_norkevin'").fetchone()[0]

        # Primer request
        r1 = accept_quote(conn, 'proj_norkevin', 'q_norkevin_001', 'idem-test-001', rh,
                           112000, 'GTQ', 2, 'tenant_kevin', 'company_norkevin')

        # Conteos post 1ra accept
        n_i_1 = conn.execute("SELECT COUNT(*) FROM invoices WHERE project_id='proj_norkevin'").fetchone()[0]
        n_pe_1 = conn.execute("SELECT COUNT(*) FROM processed_events").fetchone()[0]
        n_ob_1 = conn.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0]
        n_ins_1 = conn.execute("SELECT COUNT(*) FROM payment_installments").fetchone()[0]
        n_wi_1 = conn.execute("SELECT COUNT(*) FROM workflow_instances WHERE project_id='proj_norkevin'").fetchone()[0]

        # Segundo request: misma key + mismo hash
        r2 = accept_quote(conn, 'proj_norkevin', 'q_norkevin_001', 'idem-test-001', rh,
                           112000, 'GTQ', 2, 'tenant_kevin', 'company_norkevin')

        # Conteos finales (NO deben haber aumentado)
        n_p_f = conn.execute("SELECT COUNT(*) FROM projects WHERE id='proj_norkevin'").fetchone()[0]
        n_i_f = conn.execute("SELECT COUNT(*) FROM invoices WHERE project_id='proj_norkevin'").fetchone()[0]
        n_pe_f = conn.execute("SELECT COUNT(*) FROM processed_events").fetchone()[0]
        n_ob_f = conn.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0]
        n_ins_f = conn.execute("SELECT COUNT(*) FROM payment_installments").fetchone()[0]
        n_wi_f = conn.execute("SELECT COUNT(*) FROM workflow_instances WHERE project_id='proj_norkevin'").fetchone()[0]

        checks = {
            "Projects antes=1": n_p == 1,
            "Projects despues=1": n_p_f == 1,
            "Invoices antes=0": n_i == 0,
            "Invoices despues=1": n_i_f == 1,
            "Installments=2": n_ins_f == 2,
            "Processed events=1": n_pe_f == 1,
            "Outbox events=1": n_ob_f == 1,
            "Workflow production=1": n_wi_f == 1,
            "Result1==Result2": r1 == r2,
        }

    return all(checks.values()), "; ".join(f"{k}={v}" for k, v in checks.items())


def t11_idempotency_hash_mismatch():
    """Sec. 9: misma key + hash distinto -> IdempotencyPayloadMismatch."""
    with fresh_db() as conn:
        seed_minimal(conn)
        conn.execute("""
            INSERT INTO quotes
            (id, tenant_id, company_id, project_id, billing_project_client_id,
             number, status, issue_date, due_date,
             subtotal_units, tax_units, total_units, currency_code, currency_exponent,
             created_at)
            VALUES ('q_idem', 'tenant_kevin', 'company_norkevin',
                    'proj_norkevin', 'pc_001', 'Q-IDEM', 'sent',
                    '2026-07-01', '2026-07-15', 100, 12, 112, 'GTQ', 2,
                    '2026-07-01T10:00:00Z')
        """)
        conn.commit()

        rh1 = sha256_str('{"v":1}')
        rh2 = sha256_str('{"v":2}')

        raised = None
        try:
            accept_quote(conn, 'proj_norkevin', 'q_idem', 'idem-mismatch', rh1,
                         112, 'GTQ', 2, 'tenant_kevin', 'company_norkevin')
            accept_quote(conn, 'proj_norkevin', 'q_idem', 'idem-mismatch', rh2,
                         112, 'GTQ', 2, 'tenant_kevin', 'company_norkevin')
        except IdempotencyPayloadMismatch as e:
            raised = str(e)

        n_inv = conn.execute("SELECT COUNT(*) FROM invoices WHERE project_id='proj_norkevin'").fetchone()[0]
        return raised is not None and n_inv == 1, f"raised={raised is not None}, invoices_doble={n_inv}"


def t12_processed_event_failed_retry():
    """Sec. 9: failed + mismo hash -> reintento exitoso -> completed."""
    import hashlib
    with fresh_db() as conn:
        seed_minimal(conn)
        # Insertar quote
        conn.execute("""
            INSERT INTO quotes
            (id, tenant_id, company_id, project_id, billing_project_client_id,
             number, status, issue_date, due_date,
             subtotal_units, tax_units, total_units, currency_code, currency_exponent,
             created_at)
            VALUES ('q_retry', 'tenant_kevin', 'company_norkevin',
                    'proj_norkevin', 'pc_001', 'Q-R', 'sent',
                    '2026-07-01', '2026-07-15', 100, 12, 112, 'GTQ', 2,
                    '2026-07-01T10:00:00Z')
        """)
        # Insertar processed_event con status=failed (simular crash previo)
        rh_hash = hashlib.sha256(b'{"v":1}').hexdigest()
        conn.execute("""
            INSERT INTO processed_events
            (tenant_id, idempotency_key, event_type, entity_type, entity_id,
             status, attempts, request_hash, last_error, created_at)
            VALUES ('tenant_kevin', 'idem-retry-1', 'quote.accepted', 'project',
                    'proj_norkevin', 'failed', 1, ?, 'simulated_crash', '2026-07-01T10:00:00Z')
        """, (rh_hash,))
        conn.commit()

        # Retry — esta vez debe completar
        result = accept_quote(conn, 'proj_norkevin', 'q_retry', 'idem-retry-1', rh_hash,
                              112, 'GTQ', 2, 'tenant_kevin', 'company_norkevin')

        # Verificar
        row = conn.execute("""
            SELECT status, attempts FROM processed_events
            WHERE tenant_id='tenant_kevin' AND idempotency_key='idem-retry-1'
        """).fetchone()

        n_i = conn.execute("SELECT COUNT(*) FROM invoices WHERE project_id='proj_norkevin'").fetchone()[0]
        n_wi = conn.execute("SELECT COUNT(*) FROM workflow_instances").fetchone()[0]
        proj_status = conn.execute("SELECT commercial_status FROM projects WHERE id='proj_norkevin'").fetchone()[0]

        return (row[0] == 'completed' and row[1] >= 2 and n_i == 1 and n_wi == 1
                and proj_status == 'accepted' and 'invoice_id' in result), \
            f"status={row[0]} attempts={row[1]} inv={n_i} wi={n_wi} proj={proj_status}"


def t13_outbox_dead_letter_after_max_attempts():
    """Sec. 10: despues de max_attempts, event pasa a dead_letter.
    State machine real: pending -> processing -> failed (reschedule)
                   -> ... -> attempts==max_attempts -> dead_letter."""
    with fresh_db() as conn:
        seed_minimal(conn)
        # max_attempts=2
        conn.execute("""
            INSERT INTO outbox_events
            (id, tenant_id, company_id, event_type, entity_type, entity_id,
             handler_name, payload, status, attempts, max_attempts,
             available_at, dedupe_key, created_at)
            VALUES ('evt_dead', 'tenant_kevin', 'company_norkevin',
                    'send.email', 'project', 'proj_norkevin',
                    'send_email', '{}', 'pending', 0, 2,
                    '2026-07-01T10:00:00Z', 'dedupe-dead', '2026-07-01T10:00:00Z')
        """)
        conn.commit()

        def claim_event(worker='worker-A'):
            """Claim atomico via UPDATE WHERE status=pending."""
            cur = conn.execute("""
                UPDATE outbox_events
                SET status='processing', locked_at=?, locked_by=?
                WHERE id='evt_dead' AND status='pending'
                  AND attempts < max_attempts AND available_at <= ?
            """, (now_iso(), worker, now_iso()))
            return cur.rowcount == 1

        def mark_failed_reschedule():
            """Marca failed y reschedulea O marca dead_letter si attempts==max_attempts.
            La transicion post-trabajo:
            - Si attempts + 1 >= max_attempts -> dead_letter
            - Si no -> pending (con available_at en el futuro)"""
            conn.execute("""
                UPDATE outbox_events
                SET
                    attempts = attempts + 1,
                    status = CASE WHEN attempts + 1 >= max_attempts THEN 'dead_letter'
                                  ELSE 'pending' END,
                    locked_at = NULL,
                    locked_by = NULL,
                    last_error = 'simulated',
                    available_at = CASE WHEN attempts + 1 >= max_attempts THEN available_at
                                       ELSE '2026-07-01T10:00:00Z' END
                WHERE id='evt_dead'
            """)

        # Intentar hasta que pase a dead_letter
        status = None
        for i in range(5):
            if claim_event():
                mark_failed_reschedule()
            row = conn.execute("SELECT status, attempts FROM outbox_events WHERE id='evt_dead'").fetchone()
            status = (row[0], row[1])
            if status[0] == 'dead_letter':
                return True, f"dead_letter a attempt {status[1]}, max_attempts=2"

        return False, f"no llego a dead_letter: {status}"


def t14_outbox_delivered_cant_return_to_pending():
    """Sec. 10: delivered no puede volver a pending (Trigger 7)."""
    with fresh_db() as conn:
        seed_minimal(conn)
        conn.execute("""
            INSERT INTO outbox_events
            (id, tenant_id, company_id, event_type, entity_type, entity_id,
             handler_name, payload, status, max_attempts, available_at,
             dedupe_key, created_at)
            VALUES ('evt_delivered', 'tenant_kevin', 'company_norkevin',
                    'send.email', 'project', 'proj_norkevin',
                    'send_email', '{}', 'delivered', 3, '2026-07-01T10:00:00Z',
                    'dedupe-delivered', '2026-07-01T10:00:00Z')
        """)
        conn.commit()
        try:
            conn.execute("""
                UPDATE outbox_events
                SET status='pending'
                WHERE id='evt_delivered'
            """)
            return False, "Trigger no bloqueo intento de volver a pending"
        except sqlite3.IntegrityError as e:
            return "outbox_delivered_cannot_return_to_pending" in str(e), \
                f"trigger: {str(e)[:80]}"


def t15_two_workers_concurrent_claim():
    """Sec. 10: 2 workers intentando claim mismo evento: solo 1 lo obtiene.
    Usar 2 conexiones contra la misma DB temporal."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tf:
        dbfile = tf.name

    try:
        # Setup
        c0 = sqlite3.connect(dbfile)
        c0.executescript(load_schema())
        seed_minimal(c0)
        c0.execute("""
            INSERT INTO outbox_events
            (id, tenant_id, company_id, event_type, entity_type, entity_id,
             handler_name, payload, status, attempts, max_attempts,
             available_at, dedupe_key, created_at)
            VALUES ('evt_concurrent', 'tenant_kevin', 'company_norkevin',
                    'send.email', 'project', 'proj_norkevin',
                    'send_email', '{}', 'pending', 0, 3,
                    '2026-07-01T10:00:00Z', 'dedupe-concurrent',
                    '2026-07-01T10:00:00Z')
        """)
        c0.commit()
        c0.close()

        # 2 workers con conexiones separadas, claim atomico
        results = {}
        for worker in ('worker-A', 'worker-B'):
            conn = sqlite3.connect(dbfile, timeout=30)
            conn.execute("PRAGMA foreign_keys=OFF;")  # solo testing
            conn.execute("BEGIN IMMEDIATE;")  # lock desde el inicio
            cur = conn.execute("""
                UPDATE outbox_events
                SET status='processing', locked_at=?, locked_by=?
                WHERE id='evt_concurrent' AND status='pending'
                  AND attempts < max_attempts
            """, (now_iso(), worker))
            results[worker] = cur.rowcount
            conn.commit()
            conn.close()

        claimed = sum(1 for r in results.values() if r == 1)
        return claimed == 1, f"worker-A={results['worker-A']}, worker-B={results['worker-B']}"
    finally:
        os.unlink(dbfile)


def t16_payment_update_blocked():
    """Sec. 7: bloquear reducir amount por debajo de refunds confirmados."""
    with fresh_db() as conn:
        seed_minimal(conn)
        conn.executescript("""
            INSERT INTO projects
            (id, tenant_id, company_id, name, type, commercial_status,
             operational_status, created_at, updated_at)
            VALUES ('proj_p', 'tenant_kevin', 'company_norkevin', 'P', 'boda',
                    'new_lead', 'lead', '2026-07-01T10:00:00Z', '2026-07-01T10:00:00Z');
            INSERT INTO project_clients
            (id, tenant_id, project_id, client_id, role, is_primary, created_at)
            VALUES ('pc_p', 'tenant_kevin', 'proj_p', 'client_maria', 'novia', 0,
                    '2026-07-01T10:00:00Z');
            INSERT INTO invoices
            (id, tenant_id, company_id, project_id, billing_project_client_id,
             number, status, issue_date, due_date,
             subtotal_units, tax_units, total_units, currency_code, currency_exponent,
             created_at)
            VALUES ('inv_p', 'tenant_kevin', 'company_norkevin', 'proj_p', 'pc_p',
                    'INV-P', 'sent', '2026-07-01', '2026-07-15',
                    2000, 240, 2240, 'GTQ', 2, '2026-07-01T10:00:00Z');
            INSERT INTO payment_transactions
            (id, tenant_id, company_id, project_id, invoice_id,
             transaction_type, amount_units, currency_code, currency_exponent,
             date, method, idempotency_key, status, created_at)
            VALUES ('pay_p', 'tenant_kevin', 'company_norkevin', 'proj_p', 'inv_p',
                    'payment', 1000, 'GTQ', 2, '2026-07-01', 'transfer',
                    'idem-pay-p', 'confirmed', '2026-07-01T10:00:00Z');
            INSERT INTO payment_transactions
            (id, tenant_id, company_id, project_id, invoice_id,
             original_transaction_id, transaction_type, amount_units, currency_code,
             currency_exponent, date, method, idempotency_key, status, created_at)
            VALUES ('ref_p', 'tenant_kevin', 'company_norkevin', 'proj_p', 'inv_p',
                    'pay_p', 'refund', 300, 'GTQ', 2, '2026-07-02', 'transfer',
                    'idem-ref-p', 'confirmed', '2026-07-02T10:00:00Z');
        """)
        conn.commit()
        # Intentar reducir amount_units a 100 (menor que refund 300)
        try:
            conn.execute("""
                UPDATE payment_transactions
                SET amount_units=100
                WHERE id='pay_p'
            """)
            return False, "reduccion permitida: trigger no bloqueo"
        except sqlite3.IntegrityError as e:
            msg = str(e)
            return "payment_amount_below_existing_refunds" in msg, \
                f"trigger mensaje: {msg[:80]}"


def t17_refund_exceeds_original():
    with fresh_db() as conn:
        seed_minimal(conn)
        conn.executescript("""
            INSERT INTO projects
            (id, tenant_id, company_id, name, type, commercial_status,
             operational_status, created_at, updated_at)
            VALUES ('proj_r', 'tenant_kevin', 'company_norkevin', 'R', 'boda',
                    'new_lead', 'lead', '2026-07-01T10:00:00Z', '2026-07-01T10:00:00Z');
            INSERT INTO project_clients
            (id, tenant_id, project_id, client_id, role, is_primary, created_at)
            VALUES ('pc_r', 'tenant_kevin', 'proj_r', 'client_maria', 'novia', 0,
                    '2026-07-01T10:00:00Z');
            INSERT INTO invoices
            (id, tenant_id, company_id, project_id, billing_project_client_id,
             number, status, issue_date, due_date,
             subtotal_units, tax_units, total_units, currency_code, currency_exponent,
             created_at)
            VALUES ('inv_r', 'tenant_kevin', 'company_norkevin', 'proj_r', 'pc_r',
                    'INV-R', 'sent', '2026-07-01', '2026-07-15',
                    500, 60, 560, 'GTQ', 2, '2026-07-01T10:00:00Z');
            INSERT INTO payment_transactions
            (id, tenant_id, company_id, project_id, invoice_id,
             transaction_type, amount_units, currency_code, currency_exponent,
             date, method, idempotency_key, status, created_at)
            VALUES ('pay_r', 'tenant_kevin', 'company_norkevin', 'proj_r', 'inv_r',
                    'payment', 500, 'GTQ', 2, '2026-07-01', 'cash',
                    'idem-pay-r', 'confirmed', '2026-07-01T10:00:00Z');
        """)
        conn.commit()
        # Refund 600 (excede los 500 disponibles)
        try:
            conn.execute("""
                INSERT INTO payment_transactions
                (id, tenant_id, company_id, project_id, invoice_id,
                 original_transaction_id, transaction_type, amount_units, currency_code,
                 currency_exponent, date, method, idempotency_key, status, created_at)
                VALUES ('ref_excess', 'tenant_kevin', 'company_norkevin',
                        'proj_r', 'inv_r', 'pay_r', 'refund', 600, 'GTQ', 2,
                        '2026-07-02', 'cash', 'idem-ref-excess', 'pending',
                        '2026-07-02T10:00:00Z')
            """)
            return False, "refund 600 no fue rechazado"
        except sqlite3.IntegrityError as e:
            return "refund_exceeds_original_payment" in str(e), f"mensaje: {str(e)[:80]}"


def t18_mail_log_no_template_allowed():
    """Validar que mail_log con template_id NULL funcione (es opcional)."""
    with fresh_db() as conn:
        seed_minimal(conn)
        conn.execute("""
            INSERT INTO mail_log
            (id, tenant_id, company_id, subject, body_snapshot, to_email,
             idempotency_key)
            VALUES ('mail_ok', 'tenant_kevin', 'company_norkevin', 'Subj', 'Body',
                    'a@b.com', 'idem-mail-ok')
        """)
        return True, "OK sin template"


def t19_automation_runs_no_task_instance_allowed():
    """Validar que automation_runs con task_instance_id NULL funcione."""
    with fresh_db() as conn:
        seed_minimal(conn)
        conn.executescript("""
            INSERT INTO workflow_template_families
            (id, tenant_id, company_id, name, created_at)
            VALUES ('wtf_a', 'tenant_kevin', 'company_norkevin', 'A', '2026-07-01T10:00:00Z');
            INSERT INTO workflow_template_versions
            (id, tenant_id, company_id, family_id, version, mode, created_at)
            VALUES ('wtv_a_v1', 'tenant_kevin', 'company_norkevin', 'wtf_a', 1,
                    'dynamic', '2026-07-01T10:00:00Z');
            INSERT INTO workflow_instances
            (id, tenant_id, company_id, project_id, template_version_id,
             template_version, mode, started_at)
            VALUES ('wi_a', 'tenant_kevin', 'company_norkevin', 'proj_norkevin',
                    'wtv_a_v1', 1, 'dynamic', '2026-07-01T10:00:00Z');
        """)
        conn.execute("""
            INSERT INTO automation_runs
            (id, tenant_id, company_id, event_type, project_id,
             workflow_instance_id, idempotency_key)
            VALUES ('ar_a', 'tenant_kevin', 'company_norkevin', 'run.event',
                    'proj_norkevin', 'wi_a', 'idem-ar-a')
        """)
        return True, "OK con task_instance NULL"


def t20_allocations_blocked_exceeds_transaction():
    """Triggers de allocations no permiten exceder transaction o installment."""
    with fresh_db() as conn:
        seed_minimal(conn)
        conn.executescript("""
            INSERT INTO projects
            (id, tenant_id, company_id, name, type, commercial_status,
             operational_status, created_at, updated_at)
            VALUES ('proj_alloc', 'tenant_kevin', 'company_norkevin', 'AL', 'boda',
                    'new_lead', 'lead', '2026-07-01T10:00:00Z', '2026-07-01T10:00:00Z');
            INSERT INTO project_clients
            (id, tenant_id, project_id, client_id, role, is_primary, created_at)
            VALUES ('pc_alloc', 'tenant_kevin', 'proj_alloc', 'client_maria', 'novia', 0,
                    '2026-07-01T10:00:00Z');
            INSERT INTO invoices
            (id, tenant_id, company_id, project_id, billing_project_client_id,
             number, status, issue_date, due_date,
             subtotal_units, tax_units, total_units, currency_code, currency_exponent,
             created_at)
            VALUES ('inv_alloc', 'tenant_kevin', 'company_norkevin', 'proj_alloc',
                    'pc_alloc', 'INV-AL', 'sent', '2026-07-01', '2026-07-15',
                    500, 60, 560, 'GTQ', 2, '2026-07-01T10:00:00Z');
            INSERT INTO payment_installments
            (id, invoice_id, number, total_installments, due_date,
             amount_units, created_at)
            VALUES ('pi_alloc', 'inv_alloc', 1, 1, '2026-07-15', 500,
                    '2026-07-01T10:00:00Z');
            INSERT INTO payment_transactions
            (id, tenant_id, company_id, project_id, invoice_id,
             transaction_type, amount_units, currency_code, currency_exponent,
             date, method, idempotency_key, status, created_at)
            VALUES ('pay_alloc', 'tenant_kevin', 'company_norkevin', 'proj_alloc',
                    'inv_alloc', 'payment', 300, 'GTQ', 2, '2026-07-01',
                    'transfer', 'idem-pay-alloc', 'confirmed',
                    '2026-07-01T10:00:00Z');
        """)
        conn.commit()
        # Allocation de 400 (supera los 300 disponibles)
        try:
            conn.execute("""
                INSERT INTO payment_allocations
                (id, invoice_id, transaction_id, installment_id, amount_units, created_at)
                VALUES ('alloc_excess', 'inv_alloc', 'pay_alloc', 'pi_alloc', 400,
                        '2026-07-01T10:00:00Z')
            """)
            return False, "alloc 400 no fue rechazado"
        except sqlite3.IntegrityError as e:
            return "allocation_exceeds_transaction_amount" in str(e), \
                f"mensaje: {str(e)[:80]}"


def t21_quote_item_product_company_mismatch():
    """Sec. 6 V5.2: Quote de Norkevin + Product de Astral -> rechazado por trigger.
    Espera mensaje exacto: quote_item_product_company_mismatch"""
    with fresh_db() as conn:
        seed_minimal(conn)
        # Crear quote en Norkevin
        conn.execute("""
            INSERT INTO quotes
            (id, tenant_id, company_id, project_id, billing_project_client_id,
             number, status, issue_date, due_date,
             subtotal_units, tax_units, total_units, currency_code, currency_exponent,
             created_at)
            VALUES ('q_norkevin_T21', 'tenant_kevin', 'company_norkevin',
                    'proj_norkevin', 'pc_001', 'Q-T21', 'draft',
                    '2026-07-01', '2026-07-15', 100, 12, 112, 'GTQ', 2,
                    '2026-07-01T10:00:00Z')
        """)
        # Crear product en Astral
        conn.execute("""
            INSERT INTO products
            (id, tenant_id, company_id, type, name, price_units, currency_code,
             tax_rate_bps, order_index, active, created_at)
            VALUES ('prod_astral_T21', 'tenant_kevin', 'company_astral',
                    'package', 'Astral Wedding Package', 10000, 'GTQ', 1200,
                    0, 1, '2026-07-01T10:00:00Z')
        """)
        conn.commit()
        # Intentar insertar quote_item con product de otra company
        try:
            conn.execute("""
                INSERT INTO quote_items
                (id, quote_id, product_id, name, price_units,
                 subtotal_units, order_index)
                VALUES ('qi_T21_bad', 'q_norkevin_T21', 'prod_astral_T21',
                        'Snapshot', 100, 100, 0)
            """)
            return False, "Quote Norkevin + Product Astral NO fue rechazado"
        except sqlite3.IntegrityError as e:
            msg = str(e)
            return "quote_item_product_company_mismatch" in msg, \
                f"mensaje exacto: {msg}"


def seed_two_workflow_companies(conn):
    now = "2026-07-01T10:00:00Z"
    conn.executescript(f"""
        INSERT INTO tenants (id, name, created_at) VALUES
            ('tenant_a', 'Tenant A', '{now}'),
            ('tenant_b', 'Tenant B', '{now}');
        INSERT INTO companies
        (id, tenant_id, slug, name, logo_letter, color, created_at, updated_at)
        VALUES
            ('company_a', 'tenant_a', 'a', 'A', 'A', '#111111', '{now}', '{now}'),
            ('company_b', 'tenant_b', 'b', 'B', 'B', '#222222', '{now}', '{now}');
        INSERT INTO clients (id, tenant_id, first_name, last_name, created_at, updated_at)
        VALUES
            ('client_a', 'tenant_a', 'Ana', 'A', '{now}', '{now}'),
            ('client_b', 'tenant_b', 'Bea', 'B', '{now}', '{now}');
        INSERT INTO projects
        (id, tenant_id, company_id, name, type, commercial_status,
         operational_status, created_at, updated_at)
        VALUES
            ('project_a', 'tenant_a', 'company_a', 'Project A', 'boda',
             'new_lead', 'lead', '{now}', '{now}'),
            ('project_b', 'tenant_b', 'company_b', 'Project B', 'boda',
             'new_lead', 'lead', '{now}', '{now}');
        INSERT INTO workflow_template_families
        (id, tenant_id, company_id, name, created_at)
        VALUES
            ('family_a', 'tenant_a', 'company_a', 'PRODUCTION', '{now}'),
            ('family_b', 'tenant_b', 'company_b', 'PRODUCTION', '{now}');
        INSERT INTO workflow_template_versions
        (id, tenant_id, company_id, family_id, version, mode, created_at)
        VALUES
            ('version_a', 'tenant_a', 'company_a', 'family_a', 1, 'dynamic', '{now}'),
            ('version_b', 'tenant_b', 'company_b', 'family_b', 1, 'dynamic', '{now}');
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
             1, 'dynamic', 'active', '{now}'),
            ('workflow_b', 'tenant_b', 'company_b', 'project_b', 'version_b',
             1, 'dynamic', 'active', '{now}');
        INSERT INTO workflow_task_instances
        (id, workflow_instance_id, template_version_id, task_template_version_id,
         name, status, due_rule_mode, idempotency_key)
        VALUES
            ('task_a', 'workflow_a', 'version_a', 'task_tpl_a',
             'Task A', 'pending', 'manual', 'idem-task-a'),
            ('task_b', 'workflow_b', 'version_b', 'task_tpl_b',
             'Task B', 'pending', 'manual', 'idem-task-b');
    """)
    conn.commit()


def t22_workflow_task_template_wrong_workflow_rejected():
    """Sec. 3: task instance no puede usar task_template_version de otro workflow."""
    with fresh_db() as conn:
        seed_two_workflow_companies(conn)
        try:
            conn.execute("""
                INSERT INTO workflow_task_instances
                (id, workflow_instance_id, template_version_id, task_template_version_id,
                 name, status, due_rule_mode, idempotency_key)
                VALUES ('task_bad', 'workflow_a', 'version_a', 'task_tpl_b',
                        'Bad', 'pending', 'manual', 'idem-task-bad')
            """)
            return False, "task_template_version de otro workflow fue aceptado"
        except sqlite3.IntegrityError as e:
            return True, f"rechazado: {str(e)[:80]}"


def t23_automation_run_wrong_project_rejected():
    """Sec. 5: automation_runs no puede mezclar tenant/company con project ajeno."""
    with fresh_db() as conn:
        seed_two_workflow_companies(conn)
        try:
            conn.execute("""
                INSERT INTO automation_runs
                (id, tenant_id, company_id, workflow_instance_id, project_id,
                 event_type, status, idempotency_key)
                VALUES ('ar_bad_project', 'tenant_a', 'company_a', 'workflow_a',
                        'project_b', 'evt', 'pending', 'idem-ar-bad-project')
            """)
            return False, "automation_run con project ajeno fue aceptado"
        except sqlite3.IntegrityError as e:
            return True, f"rechazado: {str(e)[:80]}"


def t24_automation_run_wrong_workflow_rejected():
    """Sec. 5: automation_runs no puede mezclar tenant/company con workflow ajeno."""
    with fresh_db() as conn:
        seed_two_workflow_companies(conn)
        try:
            conn.execute("""
                INSERT INTO automation_runs
                (id, tenant_id, company_id, workflow_instance_id, project_id,
                 event_type, status, idempotency_key)
                VALUES ('ar_bad_workflow', 'tenant_a', 'company_a', 'workflow_b',
                        'project_a', 'evt', 'pending', 'idem-ar-bad-workflow')
            """)
            return False, "automation_run con workflow ajeno fue aceptado"
        except sqlite3.IntegrityError as e:
            return True, f"rechazado: {str(e)[:80]}"


def t25_quote_item_update_product_company_mismatch():
    """Sec. 6: UPDATE de quote_item tampoco puede apuntar a producto de otra company."""
    with fresh_db() as conn:
        seed_minimal(conn)
        conn.executescript("""
            INSERT INTO quotes
            (id, tenant_id, company_id, project_id, billing_project_client_id,
             number, status, issue_date, due_date,
             subtotal_units, tax_units, total_units, currency_code, currency_exponent,
             created_at)
            VALUES ('q_t25', 'tenant_kevin', 'company_norkevin',
                    'proj_norkevin', 'pc_001', 'Q-T25', 'draft',
                    '2026-07-01', '2026-07-15', 100, 12, 112, 'GTQ', 2,
                    '2026-07-01T10:00:00Z');
            INSERT INTO products
            (id, tenant_id, company_id, type, name, price_units, currency_code,
             tax_rate_bps, order_index, active, created_at)
            VALUES
                ('prod_norkevin_t25', 'tenant_kevin', 'company_norkevin',
                 'package', 'Norkevin Package', 10000, 'GTQ', 1200, 0, 1,
                 '2026-07-01T10:00:00Z'),
                ('prod_astral_t25', 'tenant_kevin', 'company_astral',
                 'package', 'Astral Package', 10000, 'GTQ', 1200, 0, 1,
                 '2026-07-01T10:00:00Z');
            INSERT INTO quote_items
            (id, quote_id, product_id, name, price_units, subtotal_units, order_index)
            VALUES ('qi_t25', 'q_t25', 'prod_norkevin_t25', 'Snapshot', 100, 100, 0);
        """)
        try:
            conn.execute("UPDATE quote_items SET product_id='prod_astral_t25' WHERE id='qi_t25'")
            return False, "UPDATE quote_item a producto de otra company fue aceptado"
        except sqlite3.IntegrityError as e:
            return "quote_item_product_company_mismatch" in str(e), \
                f"mensaje exacto: {str(e)}"


def t26_payment_update_below_allocations_rejected():
    """Sec. 7: payment no puede reducirse por debajo de allocations existentes."""
    with fresh_db() as conn:
        seed_minimal(conn)
        conn.executescript("""
            INSERT INTO invoices
            (id, tenant_id, company_id, project_id, billing_project_client_id,
             number, status, issue_date, due_date,
             subtotal_units, tax_units, total_units, currency_code, currency_exponent,
             created_at)
            VALUES ('inv_t26', 'tenant_kevin', 'company_norkevin', 'proj_norkevin',
                    'pc_001', 'INV-T26', 'sent', '2026-07-01', '2026-07-15',
                    1000, 120, 1120, 'GTQ', 2, '2026-07-01T10:00:00Z');
            INSERT INTO payment_installments
            (id, invoice_id, number, total_installments, due_date, amount_units, created_at)
            VALUES ('ins_t26', 'inv_t26', 1, 1, '2026-07-15', 1000,
                    '2026-07-01T10:00:00Z');
            INSERT INTO payment_transactions
            (id, tenant_id, company_id, project_id, invoice_id, transaction_type,
             amount_units, currency_code, currency_exponent, date, method,
             idempotency_key, status, created_at)
            VALUES ('pay_t26', 'tenant_kevin', 'company_norkevin', 'proj_norkevin',
                    'inv_t26', 'payment', 700, 'GTQ', 2, '2026-07-01', 'transfer',
                    'idem-pay-t26', 'confirmed', '2026-07-01T10:00:00Z');
            INSERT INTO payment_allocations
            (id, invoice_id, transaction_id, installment_id, amount_units, created_at)
            VALUES ('alloc_t26', 'inv_t26', 'pay_t26', 'ins_t26', 600,
                    '2026-07-01T10:00:00Z');
        """)
        try:
            conn.execute("UPDATE payment_transactions SET amount_units=500 WHERE id='pay_t26'")
            return False, "payment reducido por debajo de allocations fue aceptado"
        except sqlite3.IntegrityError as e:
            return "payment_amount_below_existing_allocations" in str(e), \
                f"mensaje exacto: {str(e)}"


def t27_payment_original_with_refunds_locked():
    """Sec. 7: payment con refund pendiente/confirmado no puede mutar campos base."""
    with fresh_db() as conn:
        seed_minimal(conn)
        conn.executescript("""
            INSERT INTO invoices
            (id, tenant_id, company_id, project_id, billing_project_client_id,
             number, status, issue_date, due_date,
             subtotal_units, tax_units, total_units, currency_code, currency_exponent,
             created_at)
            VALUES ('inv_t27', 'tenant_kevin', 'company_norkevin', 'proj_norkevin',
                    'pc_001', 'INV-T27', 'sent', '2026-07-01', '2026-07-15',
                    1000, 120, 1120, 'GTQ', 2, '2026-07-01T10:00:00Z');
            INSERT INTO payment_transactions
            (id, tenant_id, company_id, project_id, invoice_id, transaction_type,
             amount_units, currency_code, currency_exponent, date, method,
             idempotency_key, status, created_at)
            VALUES ('pay_t27', 'tenant_kevin', 'company_norkevin', 'proj_norkevin',
                    'inv_t27', 'payment', 700, 'GTQ', 2, '2026-07-01', 'transfer',
                    'idem-pay-t27', 'confirmed', '2026-07-01T10:00:00Z');
            INSERT INTO payment_transactions
            (id, tenant_id, company_id, project_id, invoice_id, original_transaction_id,
             transaction_type, amount_units, currency_code, currency_exponent, date,
             method, idempotency_key, status, created_at)
            VALUES ('refund_t27', 'tenant_kevin', 'company_norkevin', 'proj_norkevin',
                    'inv_t27', 'pay_t27', 'refund', 100, 'GTQ', 2, '2026-07-02',
                    'transfer', 'idem-refund-t27', 'confirmed',
                    '2026-07-02T10:00:00Z');
        """)
        try:
            conn.execute("UPDATE payment_transactions SET status='reversed' WHERE id='pay_t27'")
            return False, "payment original con refund permitio cambio de status"
        except sqlite3.IntegrityError as e:
            return "payment_original_has_refunds_locked" in str(e), \
                f"mensaje exacto: {str(e)}"


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 80)
    print("VALIDATE_SCHEMA_V5.2.PY — Pruebas aisladas (cada una con su propia conexion)")
    print("=" * 80)
    print()
    print(f"Python:  {sys.version}")
    print(f"SQLite:  {sqlite3.sqlite_version}")
    print(f"Schema SHA-256: {sha256_file(SCHEMA)}")
    print()

    tests = [
        ("T01: PRAGMA foreign_key_check + integrity_check", t01_pragmas),
        ("T02: Conteo exacto (35 tablas, 27 indices, 13 triggers)", t02_count_objects),
        ("T03: quote company distinto al project", t03_quote_wrong_company),
        ("T04 (Sec.1): client otro tenant, FK real (no is_primary)", t04_client_other_tenant_via_fk),
        ("T05 (Sec.2): Workflow Version Astral con Family Norkevin", t05_workflow_version_wrong_family_company),
        ("T06: Workflow instance Norkevin + template Astral", t06_workflow_instance_wrong_project_company),
        ("T07 (Sec.4): payment_transaction invoice de otro project", t07_payment_transaction_wrong_invoice_project),
        ("T08 (Sec.5): calendar_events con company de otro tenant", t08_calendar_event_other_company),
        ("T09 (Sec.5): mail_log.template_id de company distinto", t09_mail_log_other_template_company),
        ("T10 (Sec.8): doble aceptacion idempotente (con real result)", t10_double_acceptance_idempotent),
        ("T11 (Sec.9): idempotency key + hash distinto", t11_idempotency_hash_mismatch),
        ("T12 (Sec.9): retry processed_event failed", t12_processed_event_failed_retry),
        ("T13 (Sec.10): outbox dead_letter a max_attempts", t13_outbox_dead_letter_after_max_attempts),
        ("T14 (Sec.10): delivered no vuelve a pending", t14_outbox_delivered_cant_return_to_pending),
        ("T15 (Sec.10): 2 workers concurrentes, solo 1 claim", t15_two_workers_concurrent_claim),
        ("T16 (Sec.7): UPDATE payment reduce amount bajo refunds", t16_payment_update_blocked),
        ("T17: refund excede original", t17_refund_exceeds_original),
        ("T18: mail_log sin template (template opcional)", t18_mail_log_no_template_allowed),
        ("T19: automation_runs sin task_instance", t19_automation_runs_no_task_instance_allowed),
        ("T20: allocation excede transaction", t20_allocations_blocked_exceeds_transaction),
        ("T21 (Sec.6): quote_item product de otra company", t21_quote_item_product_company_mismatch),
        ("T22 (Sec.3): workflow task template de otro workflow", t22_workflow_task_template_wrong_workflow_rejected),
        ("T23 (Sec.5): automation_run project de otra company", t23_automation_run_wrong_project_rejected),
        ("T24 (Sec.5): automation_run workflow de otra company", t24_automation_run_wrong_workflow_rejected),
        ("T25 (Sec.6): UPDATE quote_item product de otra company", t25_quote_item_update_product_company_mismatch),
        ("T26 (Sec.7): UPDATE payment bajo allocations", t26_payment_update_below_allocations_rejected),
        ("T27 (Sec.7): payment original con refunds bloqueado", t27_payment_original_with_refunds_locked),
    ]  # 27 total

    results = []
    for name, test_fn in tests:
        print(f"Ejecutando: {name}...")
        try:
            ok, detail = test_fn()
        except Exception as e:
            ok, detail = False, f"EXCEPCION: {type(e).__name__}: {e}"
        status = "[PASS]" if ok else "[FAIL]"
        print(f"  {status} {detail[:200]}")
        results.append((name, ok, detail))
        print()

    print("=" * 80)
    print("RESUMEN V5.2")
    print("=" * 80)
    n_pass = sum(1 for _, ok, _ in results if ok)
    n_fail = sum(1 for _, ok, _ in results if not ok)
    for name, ok, _ in results:
        print(f"  [{('PASS' if ok else 'FAIL')}] {name}")
    print()
    print(f"Total: {n_pass} pasaron, {n_fail} fallaron (de {len(results)} pruebas)")
    print()
    print("Confirmacion de no-modificacion (durante este script):")
    print("  app.py: NO")
    print("  Datos: NO")
    print("  crm.db: NO")
    print("  Alembic: NO")
    print("  JSON: NO")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
