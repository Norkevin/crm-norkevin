"""
migrate_json_to_v5.py
======================

Migra data/*.json (produccion actual, formato legacy) hacia
data/crm_v5_shadow.db (schema_v5.2.sql), en modo sombra.

NO toca: app.py, data/*.json originales, data/crm.db.
Escribe SOLO en data/crm_v5_shadow.db.

Decisiones de diseno importantes (ver AUDITORIA / reporte final para detalle):
  - schema_v5.2 no tiene tabla `leads` separada: cada lead se migra como
    `projects` con operational_status='lead'. Cada job se migra como
    `projects` con operational_status='confirmed'.
  - schema_v5.2 NO tiene tabla `contracts` ni tabla de staff/equipo interno.
    contracts.json y team.json se mapean SOLO a legacy_record_map
    (migration_status='review_needed'), no se inventa una tabla.
  - IDs legacy se reusan tal cual como primary key TEXT en la tabla nueva
    cuando es posible (mismo texto en legacy_id y new_entity_id dentro de
    legacy_record_map) para mantener trazabilidad legible.
  - Dinero: todo se castea a centavos (INTEGER) con round(x*100).
  - Caso especial Camila Rios (4 jobs, 1 solo real) instruido explicitamente
    por Kevin. Se detecto el MISMO patron para Valeria Mora y Sofia Castillo
    (jobs huerfanos con cliente real pero sin dato en jobs.json) y se les dio
    el mismo tratamiento por consistencia -- marcado en el reporte para que
    Kevin lo confirme.
  - Workflow instances que referencian subject_id sin cliente/lead real
    ("Prueba Workflow Aceptado") se dejan como SKIPPED en legacy_record_map,
    no se crea projects/workflow_instances para no ensuciar datos de negocio
    con artefactos de prueba del engine.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
DB_PATH = os.path.join(DATA, "crm_v5_shadow.db")

TENANT_NORKEVIN = "tenant-norkevin"
TENANT_ASTRAL = "tenant-astral"
COMPANY_NORKEVIN = "company-norkevin"
COMPANY_ASTRAL = "company-astral"

NOW = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

REPORT = {
    "counts": {},
    "legacy_mapped": 0,
    "legacy_unmapped": [],
    "special_cases": [],
    "overlapping_payment_calendars": [],
    "pending_decisions": [],
    "errors": [],
}

_lrm_counter = 0


# ============================================================
# Helpers
# ============================================================

def load(name):
    path = os.path.join(DATA, f"{name}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def to_cents(x):
    if x is None:
        return 0
    return int(round(float(x) * 100))


def to_iso(d, with_time=None):
    if d is None:
        return None
    if "T" in d:
        return d
    if with_time:
        return f"{d}T{with_time}"
    return f"{d}T00:00:00"


def next_lrm_id():
    global _lrm_counter
    _lrm_counter += 1
    return f"lrm-{_lrm_counter:06d}"


def map_legacy(cur, tenant_id, source_file, legacy_id, entity_type,
               new_entity_id, status="imported", notes=None):
    """Registra SIEMPRE un mapeo legacy -> nuevo (o skip/review) en legacy_record_map."""
    cur.execute(
        """INSERT INTO legacy_record_map
           (id, tenant_id, source_file, legacy_id, entity_type, new_entity_id,
            migration_status, notes, migrated_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (next_lrm_id(), tenant_id, source_file, legacy_id, entity_type,
         new_entity_id, status, notes, NOW),
    )
    if status in ("imported", "merged", "archived"):
        REPORT["legacy_mapped"] += 1
    else:
        REPORT["legacy_unmapped"].append(
            {"source_file": source_file, "legacy_id": legacy_id,
             "entity_type": entity_type, "status": status, "notes": notes}
        )


# ============================================================
# FASE 1: tenants + companies
# ============================================================

def migrate_tenants_companies(cur):
    tenants = load("tenants")
    settings = load("settings")

    for t in tenants:
        cur.execute(
            """INSERT INTO tenants (id, name, owner_user_id, timezone, language,
                                     created_at, archived_at)
               VALUES (?,?,?,?,?,?,NULL)""",
            (t["id"], t["name"], None, "America/Guatemala", t.get("language", "es"),
             to_iso(t["created"])),
        )
        map_legacy(cur, t["id"], "tenants.json", t["id"], "tenant", t["id"])

        company_id = COMPANY_NORKEVIN if t["id"] == TENANT_NORKEVIN else COMPANY_ASTRAL
        email = None
        phone = None
        legal_name = None
        if t["id"] == TENANT_NORKEVIN:
            # Caso C (Kevin): identidad ASTRAL WEDDINGS / info@astralweddings.com
            # esta puesta hoy en el tenant "norkevin". Se migra TAL CUAL, sin
            # inventar un valor nuevo. Queda pendiente de decision -- ver reporte.
            email = settings.get("company", {}).get("email")
            phone = settings.get("company", {}).get("phone")
            legal_name = settings.get("company", {}).get("name")
            REPORT["pending_decisions"].append(
                "CASO C: tenant 'norkevin' / company 'company-norkevin' tiene "
                "identidad 'ASTRAL WEDDINGS' y email 'info@astralweddings.com' "
                "(viene de tenants.json + settings.json TAL CUAL). Confirmar con "
                "Kevin antes de usar esta fila para enviar correos reales o "
                "mostrarla en produccion: puede que el nombre/tenant real deba "
                "ser 'norkevin' y 'astral weddings' sea una marca/tenant distinta."
            )
        cur.execute(
            """INSERT INTO companies
               (id, tenant_id, slug, name, legal_name, logo_letter, color, email,
                phone, currency_code, currency_exponent, tax_rate_bps,
                invoice_prefix, quote_prefix, active, archived_at, created_at,
                updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (company_id, t["id"], t["slug"], t["name"], legal_name,
             t["logo_letter"], t["color"], email, phone,
             t.get("currency", "GTQ"), 2, 1200, "INV", "Q",
             1 if t.get("active", True) else 0, None,
             to_iso(t["created"]), NOW),
        )
        map_legacy(cur, t["id"], "tenants.json", f"{t['id']}::company",
                   "company", company_id,
                   notes="Sintetizada: schema_v5.2 separa tenant/company pero "
                         "tenants.json legacy traia ambos conceptos mezclados "
                         "(slug/logo/color son de company, no de tenant).")

    REPORT["counts"]["tenants"] = len(tenants)
    REPORT["counts"]["companies"] = len(tenants)

    # settings.json -> settings table (company-norkevin)
    n = 0
    flat = {
        "company_name": settings.get("company", {}).get("name"),
        "company_currency": settings.get("company", {}).get("currency"),
        "company_timezone": settings.get("company", {}).get("timezone"),
        "company_email": settings.get("company", {}).get("email"),
        "company_phone": settings.get("company", {}).get("phone"),
        "workflow_templates": json.dumps(settings.get("workflow_templates", [])),
        "default_template": settings.get("default_template"),
    }
    for k, v in flat.items():
        if v is None:
            continue
        cur.execute(
            """INSERT INTO settings (tenant_id, company_id, key, value, updated_at)
               VALUES (?,?,?,?,?)""",
            (TENANT_NORKEVIN, COMPANY_NORKEVIN, k, str(v), NOW),
        )
        n += 1
    map_legacy(cur, TENANT_NORKEVIN, "settings.json", "settings.json", "settings",
               f"{COMPANY_NORKEVIN}::settings",
               notes="Aplanado a filas key/value en settings. Incluye "
                     "company_email='info@astralweddings.com' -- ver CASO C.")
    REPORT["counts"]["settings"] = n


# ============================================================
# FASE 2: clients
# ============================================================

def migrate_clients(cur):
    clients = load("clients")
    n_c = n_e = n_p = n_a = 0
    for c in clients:
        cur.execute(
            """INSERT INTO clients
               (id, tenant_id, first_name, last_name, source, consent_marketing,
                consent_signed_at, notes, archived_at, created_at, updated_at)
               VALUES (?,?,?,?,?,0,NULL,?,NULL,?,?)""",
            (c["id"], c.get("tenant_id", TENANT_NORKEVIN), c["first_name"],
             c["last_name"], None, None, to_iso(c["created"]), NOW),
        )
        n_c += 1
        if c.get("email"):
            cur.execute(
                """INSERT INTO client_emails
                   (id, client_id, value_raw, value_normalized, is_primary,
                    verified_at, archived_at, created_at)
                   VALUES (?,?,?,?,1,NULL,NULL,?)""",
                (f"cemail-{c['id']}", c["id"], c["email"],
                 c["email"].strip().lower(), to_iso(c["created"])),
            )
            n_e += 1
        if c.get("phone"):
            cur.execute(
                """INSERT INTO client_phones
                   (id, client_id, value_raw, value_normalized, is_primary,
                    verified_at, archived_at, created_at)
                   VALUES (?,?,?,?,1,NULL,NULL,?)""",
                (f"cphone-{c['id']}", c["id"], c["phone"],
                 "".join(ch for ch in c["phone"] if ch.isdigit()),
                 to_iso(c["created"])),
            )
            n_p += 1
        if c.get("address"):
            cur.execute(
                """INSERT INTO client_addresses
                   (id, client_id, type, is_primary, line1, line2, city, state,
                    postal_code, country, lat, lng, archived_at, created_at)
                   VALUES (?,?,?,1,?,NULL,NULL,NULL,NULL,'Guatemala',NULL,NULL,NULL,?)""",
                (f"caddr-{c['id']}", c["id"], "event", c["address"],
                 to_iso(c["created"])),
            )
            n_a += 1
        map_legacy(cur, c.get("tenant_id", TENANT_NORKEVIN), "clients.json",
                   c["id"], "client", c["id"])
    REPORT["counts"]["clients"] = n_c
    REPORT["counts"]["client_emails"] = n_e
    REPORT["counts"]["client_phones"] = n_p
    REPORT["counts"]["client_addresses"] = n_a
    return {c["id"]: c for c in clients}


# ============================================================
# FASE 3: packages.json -> products
# ============================================================

def migrate_products(cur):
    packages = load("packages")
    n = 0
    for p in packages:
        cur.execute(
            """INSERT INTO products
               (id, tenant_id, company_id, type, name, description, price_units,
                currency_code, tax_rate_bps, duration_hours, includes, category,
                order_index, active, archived_at, created_at)
               VALUES (?,?,?,'package',?,?,?,?,1200,?,?,?,0,1,NULL,?)""",
            (p["id"], TENANT_NORKEVIN, COMPANY_NORKEVIN, p["name"],
             p.get("description"), to_cents(p["price"]), "GTQ",
             p.get("duration_hours"), json.dumps(p.get("includes", [])),
             p.get("category"), NOW),
        )
        map_legacy(cur, TENANT_NORKEVIN, "packages.json", p["id"], "product", p["id"])
        n += 1
    REPORT["counts"]["products"] = n
    return {p["id"] for p in packages}


# ============================================================
# FASE 4: email_templates.json
# ============================================================

def migrate_email_templates(cur):
    templates = load("email_templates")
    n = 0
    for t in templates:
        cur.execute(
            """INSERT INTO email_templates
               (id, tenant_id, company_id, name, subject, body, variables_used,
                active, created_at, updated_at, archived_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,NULL)""",
            (t["id"], TENANT_NORKEVIN, COMPANY_NORKEVIN, t["name"], t["asunto"],
             t["cuerpo"], json.dumps(t.get("adjuntos", [])),
             1 if t.get("activo", True) else 0, to_iso(t["created"]), NOW),
        )
        map_legacy(cur, TENANT_NORKEVIN, "email_templates.json", t["id"],
                   "email_template", t["id"])
        n += 1
    REPORT["counts"]["email_templates"] = n


# ============================================================
# FASE 5: projects (leads.json + jobs.json + huerfanos de workflow_instances)
# ============================================================

STATUS_LEAD_COMMERCIAL = {
    "Nuevo": "new_lead",
    "Contactado": "contacted",
    "Consulta": "follow_up",
    "Convertido": "accepted",
}

DUPLICADO_JOBS = {
    # job_id_huerfano: (client_id, workflow_instance_id, subject_name)
    "boda-69f508a1": ("client-camila-rios", "wi_c8aea974", "Boda Camila Rios"),
    "boda-1d62d5e2": ("client-camila-rios", "wi_1f814700", "Boda Camila Rios"),
    "boda-35bd38a1": ("client-camila-rios", "wi_49fb933d", "Boda Camila Rios"),
    # Mismo patron detectado en Valeria y Sofia (no mencionado explicitamente
    # por Kevin pero identico: cliente real, job huerfano en workflow_instances,
    # ausente en jobs.json). Tratado igual, marcado para confirmacion.
    "boda-423e71c5": ("client-valeria-mora", "wi_4a9f9143", "Boda Valeria Mora"),
    "boda-d8a57784": ("client-sofia-castillo", "wi_88b52afd", "Boda Sofia Castillo"),
}

# Subject ids referenciados por workflow_instances.json SIN cliente/lead real:
# artefactos de prueba del workflow engine ("Prueba Workflow Aceptado").
ORPHAN_TEST_SUBJECTS = {
    "lead-5ffc10d6": "wi_6ecee1d2",
    "boda-ac297991": "wi_71eecdbc",
    "lead-f26d314c": "wi_d70d4c63",
    "boda-ac9a338f": "wi_14e0c297",
}

REAL_JOB_IDS_IN_JOBS_JSON = {
    "job-maria-carlos", "job-karen-diego", "job-daniel-paola", "boda-e8b7e2a7",
}


def create_project_clients(cur, project_id, client_id, created_at):
    pc_id = f"pc-{project_id}"
    cur.execute(
        """INSERT INTO project_clients
           (id, tenant_id, project_id, client_id, role, is_primary,
            is_billing_contact, is_portal_contact, created_at, archived_at)
           VALUES (?,?,?,?,?,1,1,1,?,NULL)""",
        (pc_id, TENANT_NORKEVIN, project_id, client_id, "participant", created_at),
    )
    return pc_id


def migrate_projects(cur, clients_by_id):
    leads = load("leads")
    jobs = load("jobs")
    workflow_instances = load("workflow_instances")

    project_clients_by_project = {}
    n_projects = 0

    # --- leads.json -> projects (operational_status='lead') ---
    for l in leads:
        pid = l["id"]
        created_at = to_iso(l["created"])
        commercial = STATUS_LEAD_COMMERCIAL.get(l["status"], "new_lead")
        cur.execute(
            """INSERT INTO projects
               (id, tenant_id, company_id, name, type, source, form_id,
                event_date, event_time, event_end_date, event_end_time,
                location_name, location_address, location_lat, location_lng,
                commercial_status, operational_status, job_accepted_at,
                job_accepted_via, booked_value_units, package_id,
                package_name_snapshot, completed_at, cancelled_at,
                cancellation_reason, archived_at, created_at, updated_at)
               VALUES (?,?,?,?,'boda',?,NULL,?,NULL,NULL,NULL,?,NULL,NULL,NULL,
                       ?,'lead',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,?,?)""",
            (pid, l.get("tenant_id", TENANT_NORKEVIN), COMPANY_NORKEVIN,
             l["nombre"], l.get("fuente"), l.get("fecha_tentativa"),
             l.get("locacion"), commercial, created_at, NOW),
        )
        n_projects += 1
        client_id = l.get("client_id")
        if client_id and client_id in clients_by_id:
            project_clients_by_project[pid] = create_project_clients(
                cur, pid, client_id, created_at)
        map_legacy(cur, l.get("tenant_id", TENANT_NORKEVIN), "leads.json", pid,
                   "project", pid,
                   notes=(f"lead -> project operational_status='lead'. "
                          f"converted_to_job={l.get('converted_to_job')}"
                          if l.get("converted_to_job") else None))

    # --- jobs.json -> projects (operational_status='confirmed') ---
    for j in jobs:
        pid = j["id"]
        created_at = to_iso(j["created"])
        cur.execute(
            """INSERT INTO projects
               (id, tenant_id, company_id, name, type, source, form_id,
                event_date, event_time, event_end_date, event_end_time,
                location_name, location_address, location_lat, location_lng,
                commercial_status, operational_status, job_accepted_at,
                job_accepted_via, booked_value_units, package_id,
                package_name_snapshot, completed_at, cancelled_at,
                cancellation_reason, archived_at, created_at, updated_at)
               VALUES (?,?,?,?,'boda',NULL,NULL,?,NULL,NULL,NULL,?,NULL,NULL,NULL,
                       'accepted','confirmed',?,'quote_accepted',?,NULL,?,
                       NULL,NULL,NULL,NULL,?,?)""",
            (pid, j.get("tenant_id", TENANT_NORKEVIN), COMPANY_NORKEVIN,
             j["nombre"], j.get("boda_date"), j.get("location"), created_at,
             to_cents(j.get("price_total")), j.get("package"), created_at, NOW),
        )
        n_projects += 1
        client_id = j.get("client_id")
        if client_id and client_id in clients_by_id:
            project_clients_by_project[pid] = create_project_clients(
                cur, pid, client_id, created_at)
        map_legacy(cur, j.get("tenant_id", TENANT_NORKEVIN), "jobs.json", pid,
                   "project", pid)

    # --- CASO A (+ extension Valeria/Sofia): jobs duplicados/huerfanos ---
    for boda_id, (client_id, wi_id, subject_name) in DUPLICADO_JOBS.items():
        if boda_id in REAL_JOB_IDS_IN_JOBS_JSON:
            continue  # ya migrado arriba (boda-e8b7e2a7)
        wi = workflow_instances.get(wi_id, {})
        created_at = to_iso(wi.get("trigger_at", NOW)[:10]) if wi.get("trigger_at") else NOW
        note = (
            f"MIGRACION shadow: job duplicado/huerfano de {subject_name} "
            f"(cliente real {client_id}). Existe en workflow_instances.json "
            f"(instance {wi_id}) pero NO en jobs.json actual -- probablemente "
            f"reemplazado por una version posterior del mismo lead. "
            f"NO se borra: se migra con operational_status='cancelled' y "
            f"commercial_status='accepted' (fue aceptado en su momento). "
            f"Contratos que referencian este job_id en contracts.json quedan "
            f"apuntando a este project via legacy_record_map (ver notes de esos "
            f"contratos)."
        )
        cur.execute(
            """INSERT INTO projects
               (id, tenant_id, company_id, name, type, source, form_id,
                event_date, event_time, event_end_date, event_end_time,
                location_name, location_address, location_lat, location_lng,
                commercial_status, operational_status, job_accepted_at,
                job_accepted_via, booked_value_units, package_id,
                package_name_snapshot, completed_at, cancelled_at,
                cancellation_reason, archived_at, created_at, updated_at)
               VALUES (?,?,?,?,'boda',NULL,NULL,NULL,NULL,NULL,NULL,
                       NULL,NULL,NULL,NULL,
                       'accepted','cancelled',?,'quote_accepted',NULL,NULL,NULL,
                       NULL,?,?,NULL,?,?)""",
            (boda_id, TENANT_NORKEVIN, COMPANY_NORKEVIN, subject_name,
             created_at, created_at, note, created_at, NOW),
        )
        n_projects += 1
        if client_id in clients_by_id:
            project_clients_by_project[boda_id] = create_project_clients(
                cur, boda_id, client_id, created_at)
        map_legacy(cur, TENANT_NORKEVIN, "workflow_instances.json", boda_id,
                   "project", boda_id, status="imported", notes=note)
        REPORT["special_cases"].append(
            f"Project '{boda_id}' ({subject_name}) migrado como "
            f"operational_status='cancelled' -- job duplicado/huerfano, ver notes."
        )

    # --- artefactos de prueba sin cliente real: SKIPPED, no se crea project ---
    for subj_id, wi_id in ORPHAN_TEST_SUBJECTS.items():
        map_legacy(cur, TENANT_NORKEVIN, "workflow_instances.json", subj_id,
                   "project", None, status="skipped",
                   notes=(f"Subject '{subj_id}' referenciado por workflow_instances "
                          f"'{wi_id}' ('Prueba Workflow Aceptado') sin cliente/lead "
                          f"real correspondiente en clients.json/leads.json. No se "
                          f"crea project para no mezclar datos de negocio con "
                          f"artefactos de prueba del workflow engine. Requiere "
                          f"decision de Kevin: purgar de workflow_instances.json o "
                          f"confirmar que es basura de QA."))
        REPORT["special_cases"].append(
            f"SKIPPED (sin project ni workflow_instance): subject '{subj_id}' "
            f"/ instance '{wi_id}' -- artefacto de prueba, no tiene cliente real."
        )

    REPORT["counts"]["projects"] = n_projects
    return project_clients_by_project


# ============================================================
# FASE 6: quotes + quote_items
# ============================================================

STATUS_QUOTE = {
    "Borrador": "draft",
    "Enviada": "sent",
    "Aceptada": "accepted",
    "Rechazada": "declined",
}


def migrate_quotes(cur, project_clients_by_project, product_ids):
    quotes = load("quotes")
    n_q = n_i = 0
    quote_project = {}
    for q in quotes:
        pid = q.get("job_id")
        if not pid or pid not in project_clients_by_project:
            map_legacy(cur, TENANT_NORKEVIN, "quotes.json", q["id"], "quote", None,
                       status="review_needed",
                       notes=f"job_id='{pid}' no resuelve a un project migrado "
                             f"con contacto de facturacion. Revisar manualmente.")
            continue
        billing_pc = project_clients_by_project[pid]
        status = STATUS_QUOTE.get(q.get("status"), "sent")
        qtype = "pick_choose" if q.get("tipo_cotizacion") == "pick_and_choose" else "fixed"
        total = to_cents(q["precio_total"])
        created_at = to_iso(q["created"])
        cur.execute(
            """INSERT INTO quotes
               (id, tenant_id, company_id, project_id, billing_project_client_id,
                template_id, number, type, status, issue_date, due_date,
                subtotal_units, discount_units, tax_units, total_units,
                currency_code, currency_exponent, sent_at, viewed_at, accepted_at,
                accepted_by_user_id, acceptance_ip, sent_snapshot,
                accepted_snapshot, snapshot_hash, pdf_url, created_at, archived_at)
               VALUES (?,?,?,?,?,NULL,?,?,?,?,NULL,?,0,0,?,'GTQ',2,?,NULL,?,
                       NULL,NULL,NULL,NULL,NULL,NULL,?,NULL)""",
            (q["id"], TENANT_NORKEVIN, COMPANY_NORKEVIN, pid, billing_pc,
             f"Q-{q['id']}", qtype, status, q["created"], total, total,
             to_iso(q["sent_at"]) if q.get("sent_at") else None,
             to_iso(q["aceptada_en"]) if q.get("aceptada_en") else None,
             created_at),
        )
        n_q += 1
        quote_project[q["id"]] = pid

        items = q.get("items")
        if items:
            for idx, item in enumerate(items):
                prod_id = item.get("id") if item.get("id") in product_ids else None
                price = to_cents(item["price"])
                cur.execute(
                    """INSERT INTO quote_items
                       (id, quote_id, product_id, name, description, price_units,
                        quantity, subtotal_units, discount_units, tax_units,
                        order_index)
                       VALUES (?,?,?,?,?,?,1,?,0,0,?)""",
                    (f"qi-{q['id']}-{idx}", q["id"], prod_id, item["name"],
                     item.get("description"), price, price, idx),
                )
                n_i += 1
        else:
            cur.execute(
                """INSERT INTO quote_items
                   (id, quote_id, product_id, name, description, price_units,
                    quantity, subtotal_units, discount_units, tax_units,
                    order_index)
                   VALUES (?,?,NULL,?,?,?,1,?,0,0,0)""",
                (f"qi-{q['id']}-0", q["id"], q["paquete_nombre"],
                 q.get("notas"), total, total),
            )
            n_i += 1

        map_legacy(cur, TENANT_NORKEVIN, "quotes.json", q["id"], "quote", q["id"])

    REPORT["counts"]["quotes"] = n_q
    REPORT["counts"]["quote_items"] = n_i
    return quote_project


# ============================================================
# FASE 7: payments.json -> invoices + installments + transactions + allocations
# ============================================================

def migrate_payments(cur, project_clients_by_project):
    payments = load("payments")
    n_inv = n_it = n_tx = n_alloc = 0

    # Detectar calendarios de pago superpuestos (Caso B): mismo project_id con
    # (a) mas de un quote_id distinto entre sus pagos, o (b) una mezcla de
    # pagos CON quote_id y pagos SIN quote_id (schedule viejo pre-quotes +
    # schedule nuevo del quote aceptado). Daniel Dubuc cae en (b): sus 3 pagos
    # originales (pay-daniel-1/2/3) no tienen quote_id, pero sus 2 pagos
    # nuevos (pay-efe93655/pay-27f94291) si (quote-8efbddb9).
    quote_by_project = {}
    no_quote_by_project = set()
    for p in payments:
        pid = p.get("job_id")
        if not pid:
            continue
        qid = p.get("quote_id")
        if qid:
            quote_by_project.setdefault(pid, set()).add(qid)
        else:
            no_quote_by_project.add(pid)
    overlapping_projects = {
        pid: qs for pid, qs in quote_by_project.items()
        if len(qs) > 1 or pid in no_quote_by_project
    }

    for p in payments:
        pid = p.get("job_id")
        if not pid or pid not in project_clients_by_project:
            map_legacy(cur, TENANT_NORKEVIN, "payments.json", p["id"], "payment", None,
                       status="review_needed",
                       notes=f"job_id='{pid}' no resuelve a project migrado.")
            continue
        billing_pc = project_clients_by_project[pid]
        invoice_id = p["invoice_id"]
        amount = to_cents(p["amount"])
        status_json = p.get("status")
        inv_status = "paid" if status_json == "Pagado" else "issued"
        created_created = p.get("due_date")
        quote_id = p.get("quote_id")

        cur.execute(
            """INSERT INTO invoices
               (id, tenant_id, company_id, project_id, billing_project_client_id,
                quote_id, payment_schedule_id, number, status, issue_date,
                due_date, subtotal_units, discount_units, tax_units, total_units,
                currency_code, currency_exponent, sent_at, viewed_at,
                snapshot_hash, pdf_url, created_at, archived_at)
               VALUES (?,?,?,?,?,?,NULL,?,?,?,?,?,0,0,?,'GTQ',2,NULL,NULL,
                       NULL,NULL,?,NULL)""",
            (invoice_id, TENANT_NORKEVIN, COMPANY_NORKEVIN, pid, billing_pc,
             quote_id, invoice_id, inv_status, p.get("due_date"), p["due_date"],
             amount, amount, to_iso(p["due_date"])),
        )
        n_inv += 1

        cur.execute(
            """INSERT INTO invoice_items
               (id, invoice_id, name, description, quantity, unit_price_units,
                subtotal_units, discount_units, tax_units, order_index)
               VALUES (?,?,?,NULL,1,?,?,0,0,0)""",
            (f"ii-{invoice_id}", invoice_id, p.get("concepto", invoice_id),
             amount, amount),
        )
        n_it += 1

        cur.execute(
            """INSERT INTO payment_installments
               (id, invoice_id, number, total_installments, due_date,
                amount_units, late_since, notes, created_at, archived_at)
               VALUES (?,?,1,1,?,?,NULL,?,?,NULL)""",
            (f"pi-{invoice_id}", invoice_id, p["due_date"], amount,
             p.get("cuota"), to_iso(p["due_date"])),
        )

        if status_json == "Pagado":
            tx_id = f"tx-{p['id']}"
            paid_date = p.get("paid_date") or p.get("fecha_pago") or p["due_date"]
            cur.execute(
                """INSERT INTO payment_transactions
                   (id, tenant_id, company_id, project_id, invoice_id,
                    original_transaction_id, transaction_type, amount_units,
                    currency_code, currency_exponent, date, method,
                    external_reference, provider, provider_transaction_id,
                    idempotency_key, status, receipt_url, notes,
                    created_by_user_id, created_at, archived_at)
                   VALUES (?,?,?,?,?,NULL,'payment',?,'GTQ',2,?,?,NULL,NULL,NULL,
                           ?,'confirmed',NULL,?,NULL,?,NULL)""",
                (tx_id, TENANT_NORKEVIN, COMPANY_NORKEVIN, pid, invoice_id,
                 amount, paid_date, "no_especificado_legacy",
                 f"legacy-payment-{p['id']}", p.get("last_action"),
                 to_iso(paid_date)),
            )
            n_tx += 1
            cur.execute(
                """INSERT INTO payment_allocations
                   (id, invoice_id, transaction_id, installment_id, amount_units,
                    created_at)
                   VALUES (?,?,?,?,?,?)""",
                (f"alloc-{p['id']}", invoice_id, tx_id, f"pi-{invoice_id}", amount,
                 to_iso(paid_date)),
            )
            n_alloc += 1

        map_legacy(cur, TENANT_NORKEVIN, "payments.json", p["id"], "payment",
                   invoice_id,
                   notes=("Caso B: calendario de pago superpuesto con otro "
                          "quote sobre el mismo project -- revisar cual invoice "
                          "cancelar." if pid in overlapping_projects else None))

    for pid, qs in overlapping_projects.items():
        REPORT["overlapping_payment_calendars"].append(
            {"project_id": pid, "quotes_con_quote_id": sorted(qs),
             "tiene_pagos_sin_quote_id": pid in no_quote_by_project,
             "nota": "Dos calendarios de pago activos sobre el mismo project "
                     "(cotizacion vieja / pagos sin quote_id + cotizacion "
                     "pick-and-choose aceptada). Migrados ambos tal cual, sin "
                     "fusionar ni borrar. Kevin debe decidir cual invoice/quote "
                     "cancelar."}
        )

    REPORT["counts"]["invoices"] = n_inv
    REPORT["counts"]["invoice_items"] = n_it
    REPORT["counts"]["payment_installments"] = n_inv
    REPORT["counts"]["payment_transactions"] = n_tx
    REPORT["counts"]["payment_allocations"] = n_alloc


# ============================================================
# FASE 8: contracts.json -> legacy_record_map (sin tabla destino)
# ============================================================

def migrate_contracts(cur):
    contracts = load("contracts")
    for c in contracts:
        job_id = c.get("job_id")
        target_note = (
            f"contract '{c['id']}' (status={c.get('status')}, signed={c.get('signed')}) "
            f"vincula a project '{job_id}' (client_id={c.get('client_id')}, "
            f"lead_id={c.get('lead_id')}). schema_v5.2 NO tiene tabla `contracts` "
            f"(solo workflow_task_template_versions.contract_template_id como "
            f"referencia de plantilla). Este registro NO se puede insertar como "
            f"entidad propia sin agregar una tabla al schema -- pendiente de "
            f"decision de Kevin. El project destino sigue existiendo "
            f"(ver legacy_record_map entity_type='project')."
        )
        map_legacy(cur, TENANT_NORKEVIN, "contracts.json", c["id"], "contract",
                   None, status="review_needed", notes=target_note)
    REPORT["pending_decisions"].append(
        "schema_v5.2.sql no define una tabla `contracts`. Los 6 registros de "
        "contracts.json quedaron mapeados solo en legacy_record_map "
        "(review_needed), sin insertar en ninguna tabla de negocio. Si se "
        "necesita, hay que agregar una tabla `contracts` al schema v5.3."
    )
    REPORT["counts"]["contracts_reviewed"] = len(contracts)


# ============================================================
# FASE 9: team.json -> legacy_record_map (sin tabla destino)
# ============================================================

def migrate_team(cur):
    team = load("team")
    for m in team:
        map_legacy(cur, TENANT_NORKEVIN, "team.json", m["id"], "staff_member",
                   None, status="review_needed",
                   notes=(f"'{m['first_name']} {m['last_name']}' ({m.get('rol')}) "
                          f"-- schema_v5.2 no tiene tabla de staff/equipo interno "
                          f"(la tabla `users` es para login de la app, con role "
                          f"owner/admin/editor/viewer, no aplica a fotografos/"
                          f"editores). Pendiente de decision de Kevin."))
    REPORT["pending_decisions"].append(
        "schema_v5.2.sql no tiene tabla para staff interno (fotografos, editores "
        "de video). Los 3 registros de team.json quedaron solo en "
        "legacy_record_map (review_needed)."
    )
    REPORT["counts"]["team_reviewed"] = len(team)


# ============================================================
# FASE 10: calendar.json -> calendar_events
# ============================================================

def migrate_calendar(cur, known_project_ids):
    events = load("calendar")
    n = 0
    for e in events:
        pid = e.get("job_id") or e.get("lead_id")
        if pid not in known_project_ids:
            pid = None
        cur.execute(
            """INSERT INTO calendar_events
               (id, tenant_id, company_id, project_id, type, title, start_date,
                start_at, end_date, end_at, all_day, timezone, location, notes,
                external_calendar_event_id, created_by_user_id, created_at)
               VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,1,NULL,NULL,NULL,NULL,NULL,?)""",
            (e["id"], e.get("tenant_id", TENANT_NORKEVIN), COMPANY_NORKEVIN, pid,
             "event", e["title"], e["date"], NOW),
        )
        n += 1
        map_legacy(cur, e.get("tenant_id", TENANT_NORKEVIN), "calendar.json",
                   e["id"], "calendar_event", e["id"])
    REPORT["counts"]["calendar_events"] = n


# ============================================================
# FASE 11: workflow templates (families/versions/task templates)
#          -- reconstruidas desde src/workflow/templates.py (codigo vivo) +
#          variantes legacy detectadas en workflow_instances.json
# ============================================================

def _due(mode, amount=0, unit="days", anchor=None, direction=None):
    return dict(mode=mode, amount=amount, unit=unit, anchor=anchor, direction=direction)


TASK_SPECS = {
    "wtv-lead-v1": [
        ("lead_created", "lead", "Lead creado", "noop", None, _due("manual")),
        ("envio_paquetes", "lead", "Envio de paquetes", "send_email", "tpl-paquetes",
         _due("after_creation", 3, "hours", "lead_created", "after")),
        ("seguimiento_cliente", "lead", "Seguimiento cliente", "send_email",
         "tpl-seguimiento", _due("after_creation", 7, "days", "lead_created", "after")),
        ("levanta_muertos", "lead", "Levanta muertos", "send_email",
         "tpl-levanta-muertos", _due("after_creation", 30, "days", "lead_created", "after")),
        ("seguimiento_final", "lead", "Seguimiento final", "send_email",
         "tpl-levanta-muertos", _due("after_creation", 90, "days", "lead_created", "after")),
    ],
    "wtv-lead-v2": [
        ("envio_paquetes", "lead", "Envio de paquetes", "send_email", "tpl-paquetes",
         _due("after_creation", 3, "hours", "lead_created", "after")),
        ("seguimiento_cliente", "lead", "Seguimiento cliente", "send_email",
         "tpl-seguimiento", _due("after_creation", 7, "days", "lead_created", "after")),
        ("levanta_muertos", "lead", "Levanta muertos", "send_email",
         "tpl-levanta-muertos", _due("after_creation", 30, "days", "lead_created", "after")),
        ("seguimiento_final", "lead", "Seguimiento final", "send_email",
         "tpl-levanta-muertos", _due("after_creation", 90, "days", "lead_created", "after")),
    ],
    "wtv-prod-v1": [
        ("job_accepted", "production", "Job accepted", "noop", None, _due("manual")),
        ("reserva_confirmada", "production", "Reserva confirmada", "send_email",
         "tpl-reserva", _due("after_creation", 1, "days", "job_created", "after")),
        ("firma_contrato", "production", "Firma de contrato", "send_contract",
         "tpl-contrato", _due("after_creation", 3, "days", "job_created", "after")),
        ("pago_reserva", "production", "Pago de reserva", "notify_owner", None,
         _due("manual")),
        ("cuestionario_cliente", "production", "Cuestionario cliente", "send_email",
         "tpl-cuestionario", _due("after_event", 1, "months", "before_boda", "before")),
        ("boda", "production", "Dia de la boda", "noop", None, _due("manual")),
        ("google_comments", "post_production", "Pedir review Google", "send_email",
         "tpl-review", _due("after_event", 1, "months", "after_boda", "after")),
        ("job_complete", "post_production", "Job complete", "change_status", None,
         _due("manual")),
    ],
    "wtv-prod-v2": [
        ("job_accepted", "production", "Job accepted", "noop", None, _due("manual")),
        ("reserva_confirmada", "production", "Reserva confirmada", "send_email",
         "tpl-reserva", _due("after_creation", 1, "days", "job_created", "after")),
        ("firma_contrato", "production", "Firma de contrato", "send_contract",
         "tpl-contrato", _due("after_creation", 3, "days", "job_created", "after")),
        ("cuestionario_cliente", "production", "Cuestionario cliente", "send_email",
         "tpl-cuestionario", _due("after_event", 1, "months", "before_boda", "before")),
        ("envio_galeria", "post_production", "Envio de galeria", "send_gallery",
         "tpl-galeria", _due("after_event", 2, "months", "after_boda", "after")),
        ("pedir_review", "post_production", "Pedir review Google", "send_email",
         "tpl-review", _due("after_event", 1, "months", "after_boda", "after")),
        ("job_complete", "post_production", "Job complete", "change_status", None,
         _due("manual")),
    ],
}

VERSION_META = {
    "wtv-lead-v1": ("wtf-lead-workflow", "lead_workflow_v1", 1, "frozen"),
    "wtv-lead-v2": ("wtf-lead-workflow", "lead_workflow_v1", 2, "dynamic"),
    "wtv-prod-v1": ("wtf-production-workflow", "production_workflow_v1", 1, "frozen"),
    "wtv-prod-v2": ("wtf-production-workflow", "production_workflow_v1", 2, "dynamic"),
}

FAMILY_META = {
    "wtf-lead-workflow": "Lead Follow-up",
    "wtf-production-workflow": "Production",
}


def migrate_workflow_templates(cur):
    for family_id, name in FAMILY_META.items():
        cur.execute(
            """INSERT INTO workflow_template_families
               (id, tenant_id, company_id, name, description, active, created_at)
               VALUES (?,?,?,?,?,1,?)""",
            (family_id, TENANT_NORKEVIN, COMPANY_NORKEVIN, name,
             "Reconstruido desde src/workflow/templates.py para la migracion", NOW),
        )

    task_template_ids = {}  # (version_id, step_key) -> task_template_version id
    for version_id, (family_id, _wf_id, version, mode) in VERSION_META.items():
        cur.execute(
            """INSERT INTO workflow_template_versions
               (id, tenant_id, company_id, family_id, version, mode, notes,
                published_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (version_id, TENANT_NORKEVIN, COMPANY_NORKEVIN, family_id, version,
             mode, "Reconstruido durante migracion shadow desde workflow_instances.json"
             if mode == "frozen" else "Coincide con src/workflow/templates.py actual",
             NOW, NOW),
        )
        for idx, (step_key, stage, tname, action_type, email_tpl, due) in \
                enumerate(TASK_SPECS[version_id]):
            ttv_id = f"ttv-{version_id}-{step_key}"
            cur.execute(
                """INSERT INTO workflow_task_template_versions
                   (id, template_version_id, stage, order_index, name, description,
                    action_type, action_config_json, email_template_id,
                    contract_template_id, questionnaire_template_id,
                    due_rule_mode, due_rule_anchor, due_rule_amount, due_rule_unit,
                    due_rule_direction, active)
                   VALUES (?,?,?,?,?,NULL,?,'{}',?,NULL,NULL,?,?,?,?,?,1)""",
                (ttv_id, version_id, stage, idx, tname, action_type, email_tpl,
                 due["mode"], due["anchor"], due["amount"] or None, due["unit"],
                 due["direction"]),
            )
            task_template_ids[(version_id, step_key)] = ttv_id

    REPORT["counts"]["workflow_template_families"] = len(FAMILY_META)
    REPORT["counts"]["workflow_template_versions"] = len(VERSION_META)
    REPORT["counts"]["workflow_task_template_versions"] = sum(
        len(v) for v in TASK_SPECS.values())
    return task_template_ids


# ============================================================
# FASE 12: workflow_instances.json -> workflow_instances + workflow_task_instances
# ============================================================

def _detect_version(step_states):
    keys = set(step_states.keys())
    if keys == {"lead_created", "envio_paquetes", "seguimiento_cliente",
                "levanta_muertos", "seguimiento_final"}:
        return "wtv-lead-v1"
    if keys == {"envio_paquetes", "seguimiento_cliente", "levanta_muertos",
                "seguimiento_final"}:
        return "wtv-lead-v2"
    if keys == {"job_accepted", "reserva_confirmada", "firma_contrato",
                "pago_reserva", "cuestionario_cliente", "boda",
                "google_comments", "job_complete"}:
        return "wtv-prod-v1"
    if keys == {"job_accepted", "reserva_confirmada", "firma_contrato",
                "cuestionario_cliente", "envio_galeria", "pedir_review",
                "job_complete"}:
        return "wtv-prod-v2"
    return None


def migrate_workflow_instances(cur, known_project_ids, task_template_ids):
    instances = load("workflow_instances")
    n_wi = n_wti = 0

    for inst_id, inst in instances.items():
        if inst_id in ORPHAN_TEST_SUBJECTS.values():
            continue  # ya registrado como skipped en migrate_projects()

        subject_id = inst["subject_id"]
        if subject_id not in known_project_ids:
            map_legacy(cur, TENANT_NORKEVIN, "workflow_instances.json", inst_id,
                       "workflow_instance", None, status="review_needed",
                       notes=f"subject_id '{subject_id}' no resuelve a un project "
                             f"migrado. No se pudo insertar (FK NOT NULL).")
            continue

        version_id = _detect_version(inst["step_states"])
        if not version_id:
            map_legacy(cur, TENANT_NORKEVIN, "workflow_instances.json", inst_id,
                       "workflow_instance", None, status="review_needed",
                       notes=f"step_states no coincide con ninguna version "
                             f"reconstruida: {sorted(inst['step_states'].keys())}")
            continue

        _family_id, _wf_id, version_num, mode = VERSION_META[version_id]
        started_at = to_iso(inst["trigger_at"])
        completed_at = started_at if inst["status"] == "completed" else None

        cur.execute(
            """INSERT INTO workflow_instances
               (id, tenant_id, company_id, project_id, template_version_id,
                template_version, mode, status, started_at, completed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (inst_id, TENANT_NORKEVIN, COMPANY_NORKEVIN, subject_id, version_id,
             version_num, mode, inst["status"], started_at, completed_at),
        )
        n_wi += 1

        for step_key, step_status in inst["step_states"].items():
            ttv_id = task_template_ids.get((version_id, step_key))
            if not ttv_id:
                continue
            spec = next(s for s in TASK_SPECS[version_id] if s[0] == step_key)
            _, _, tname, _, _, due = spec
            result = inst.get("step_results", {}).get(step_key)
            executed_at = started_at if step_status == "done" else None
            cur.execute(
                """INSERT INTO workflow_task_instances
                   (id, workflow_instance_id, template_version_id,
                    task_template_version_id, name, status, due_rule_mode,
                    due_rule_anchor, due_rule_amount, due_rule_unit,
                    due_rule_direction, scheduled_at, executed_at,
                    completed_by_user_id, result, error, retry_count,
                    skip_reason, idempotency_key)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,?,NULL,?,NULL,0,NULL,?)""",
                (f"wti-{inst_id}-{step_key}", inst_id, version_id, ttv_id, tname,
                 step_status, due["mode"], due["anchor"], due["amount"] or None,
                 due["unit"], due["direction"], executed_at, result,
                 f"legacy-wti-{inst_id}-{step_key}"),
            )
            n_wti += 1

        map_legacy(cur, TENANT_NORKEVIN, "workflow_instances.json", inst_id,
                   "workflow_instance", inst_id)

    REPORT["counts"]["workflow_instances"] = n_wi
    REPORT["counts"]["workflow_task_instances"] = n_wti


# ============================================================
# FASE 13: workflow_history.json -> activity_log
# ============================================================

def migrate_workflow_history(cur, migrated_instance_ids):
    history = load("workflow_history")
    n = 0
    for idx, h in enumerate(history.get("history", [])):
        inst_id = h.get("instance_id")
        cur.execute(
            """INSERT INTO activity_log
               (id, tenant_id, company_id, project_id, client_id, actor_type,
                actor_id, event_type, entity_type, entity_id, summary,
                before_data, after_data, source, ip, user_agent, created_at,
                idempotency_key)
               VALUES (?,?,?,NULL,NULL,'system','workflow_engine',?,
                       'workflow_instance',?,?,NULL,NULL,'workflow_history.json',
                       NULL,NULL,?,?)""",
            (f"al-wh-{idx:05d}", TENANT_NORKEVIN, COMPANY_NORKEVIN, h.get("event"),
             inst_id, h.get("message"), to_iso(h["timestamp"]),
             f"legacy-wh-{idx:05d}"),
        )
        n += 1
    map_legacy(cur, TENANT_NORKEVIN, "workflow_history.json", "workflow_history.json",
               "activity_log_batch", f"{n} filas",
               notes="Cada entrada de history[] se migro 1:1 a activity_log.")
    REPORT["counts"]["activity_log"] = n


# ============================================================
# FASE 14: mail_log.json + mail_outbox.json -> mail_log
# ============================================================

MAIL_STATUS_MAP = {
    "pending": "pending", "sent": "sent", "delivered": "delivered",
    "opened": "opened", "clicked": "clicked", "bounced": "bounced",
    "failed": "failed",
}


def migrate_mail(cur, known_project_ids, client_by_project):
    n = 0
    mail_log = load("mail_log")
    for m in mail_log:
        pid = m.get("lead_id") or m.get("job_id")
        if pid not in known_project_ids:
            pid = None
        client_id = client_by_project.get(pid)
        status = MAIL_STATUS_MAP.get(m.get("status"), "sent")
        cur.execute(
            """INSERT INTO mail_log
               (id, tenant_id, company_id, project_id, client_id, template_id,
                subject, body_snapshot, from_email, to_email, cc_emails, status,
                external_message_id, sent_at, delivered_at, opened_at,
                clicked_at, replied_at, error, idempotency_key)
               VALUES (?,?,?,?,?,?,?,?,NULL,?,NULL,?,NULL,?,NULL,?,?,NULL,NULL,?)""",
            (m["id"], TENANT_NORKEVIN, COMPANY_NORKEVIN, pid, client_id,
             m.get("template_id"), m["subject"], m.get("body_preview", ""),
             m["to"], status, to_iso(m["sent_at"]) if m.get("sent_at") else None,
             to_iso(m["opened_at"]) if m.get("opened_at") else None,
             to_iso(m["clicked_at"]) if m.get("clicked_at") else None,
             f"legacy-maillog-{m['id']}"),
        )
        n += 1
        map_legacy(cur, TENANT_NORKEVIN, "mail_log.json", m["id"], "mail_log",
                   m["id"],
                   notes=(None if pid else
                          f"lead_id/job_id original ('{m.get('lead_id') or m.get('job_id')}') "
                          f"no existe en leads.json/jobs.json actuales -- project_id "
                          f"quedo NULL, el registro de correo se conservo igual."))

    outbox = load("mail_outbox")
    for o in outbox:
        meta = o.get("metadata", {})
        pid = meta.get("job_id") or meta.get("lead_id")
        if pid not in known_project_ids:
            pid = None
        client_id = client_by_project.get(pid)
        cur.execute(
            """INSERT INTO mail_log
               (id, tenant_id, company_id, project_id, client_id, template_id,
                subject, body_snapshot, from_email, to_email, cc_emails, status,
                external_message_id, sent_at, delivered_at, opened_at,
                clicked_at, replied_at, error, idempotency_key)
               VALUES (?,?,?,?,?,?,?,?,?,?,NULL,'sent',NULL,?,NULL,NULL,NULL,
                       NULL,NULL,?)""",
            (o["id"], TENANT_NORKEVIN, COMPANY_NORKEVIN, pid, client_id,
             meta.get("template_id"), o["subject"], o["body"], o.get("from"),
             o["to"], to_iso(o["created_at"]), f"legacy-outbox-{o['id']}"),
        )
        n += 1
        map_legacy(cur, TENANT_NORKEVIN, "mail_outbox.json", o["id"], "mail_log",
                   o["id"])

    REPORT["counts"]["mail_log"] = n


# ============================================================
# MAIN
# ============================================================

def main():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: {DB_PATH} no existe. Correr primero la creacion del "
              f"schema (Paso 2).", file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    cur = con.cursor()

    try:
        cur.execute("BEGIN")

        migrate_tenants_companies(cur)
        clients_by_id = migrate_clients(cur)
        product_ids = migrate_products(cur)
        migrate_email_templates(cur)
        project_clients_by_project = migrate_projects(cur, clients_by_id)
        known_project_ids = set(project_clients_by_project.keys()) | {
            pid for pid in REAL_JOB_IDS_IN_JOBS_JSON
        }
        # known_project_ids debe incluir TODOS los projects creados, no solo
        # los que tienen project_clients (por si alguno quedo sin cliente).
        cur.execute("SELECT id FROM projects")
        known_project_ids = {r[0] for r in cur.fetchall()}

        client_by_project = {}
        cur.execute("SELECT project_id, client_id FROM project_clients")
        for pid, cid in cur.fetchall():
            client_by_project[pid] = cid

        migrate_quotes(cur, project_clients_by_project, product_ids)
        migrate_payments(cur, project_clients_by_project)
        migrate_contracts(cur)
        migrate_team(cur)
        migrate_calendar(cur, known_project_ids)
        task_template_ids = migrate_workflow_templates(cur)
        migrate_workflow_instances(cur, known_project_ids, task_template_ids)
        migrate_workflow_history(cur, known_project_ids)
        migrate_mail(cur, known_project_ids, client_by_project)

        con.commit()
        print("MIGRACION COMMIT OK")
    except Exception as exc:
        con.rollback()
        REPORT["errors"].append(str(exc))
        print(f"MIGRACION FALLO, ROLLBACK: {exc}", file=sys.stderr)
        raise
    finally:
        con.close()

    report_path = os.path.join(BASE, "MIGRATION_REPORT_v5_shadow.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(REPORT, f, indent=2, ensure_ascii=False)
    print(f"Reporte escrito en {report_path}")


if __name__ == "__main__":
    main()
