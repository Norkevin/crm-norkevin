#!/usr/bin/env python3
"""
migrate_json_to_v5_shadow.py

Fase de estabilizacion (agosto 2026), punto 5: migracion V5.2 en MODO SOMBRA.
Fase de EJECUCION (agosto 2026, continuacion): ahora soporta --source y
--db-path para poder correr dos escenarios completamente independientes
sin pisarse:

    CLEAN_STATE:  --source data                         --db-path data/crm_v5_shadow_clean.db
    LEGACY fixture: --source artifacts/fixtures/legacy_20260712 --db-path artifacts/crm_v5_shadow_legacy_20260712.db

Que hace:
  1. Crea el archivo .db indicado por --db-path (NUEVO -- nunca toca
     data/crm.db ni ningun data/*.json de produccion, sin importar que
     --source se use).
  2. Aplica schema_v5.2.sql + migrations/idempotency_patch_v5.2.sql
     completos sobre ese archivo nuevo.
  3. Migra tenants/companies (via canonical brand map, NO por nombre de
     id), clients, projects (fusion de leads+jobs, arquitectura V5.2),
     project_clients (fix del placeholder anterior), quotes, invoices +
     payment_installments + payment_transactions, workflow_template
     placeholders legados + workflow_instances (fix del segundo bloqueo
     anterior).
  4. Aplica las cuarentenas de Camila Rios / Daniel Dubuc SOLO si ya
     corriste quarantine_camila_daniel.py contra el MISMO --source antes
     (lee su reporte JSON desde <source>/quarantine_review o
     <out>/../quarantine_review; si no existe, migra esos registros igual
     pero marcados 'review_needed' en legacy_record_map en vez de asumir
     un estado). Los datos problematicos de Camila/Daniel NO se limpian
     antes de migrar -- el migrador debe poder verlos tal como existian.
  5. Llena legacy_record_map para CADA registro leido de cada JSON,
     incluyendo los que no se pudieron mapear (status='skipped' o
     'review_needed', nunca se descartan en silencio).
  6. Escribe <out>/migration_reconciliation_report.json y .md con counts,
     huerfanos, duplicados, conflictos y CUALQUIER registro no mapeado.

Que NO hace (a proposito):
  - No toca data/crm.db ni ningun data/*.json de produccion.
  - No reconcilia dinero ni contratos automaticamente -- eso lo hace
    quarantine_camila_daniel.py como PROPUESTA, y esta ligado a decisiones
    de Kevin, no a este script.
  - No hace cutover. La shadow db no se conecta a app.py en ningun lado.

Entidades de JSON que esta version del script AUN NO mapea a tablas V5.2
(se listan explicitamente en el reporte bajo 'unmapped_entities', no se
adivina una tabla para ellas): contracts.json, team.json, calendar.json,
email_templates.json, packages.json, settings.json, mail_log.json,
mail_outbox.json. V5.2 no tiene una tabla 'contracts' equivalente en
schema_v5.2.sql -- se reporta como discrepancia en vez de inventar un
mapeo.

Uso:
    python migrate_json_to_v5_shadow.py --source data --db-path data/crm_v5_shadow_clean.db
    python migrate_json_to_v5_shadow.py --source artifacts/fixtures/legacy_20260712 \
        --db-path artifacts/crm_v5_shadow_legacy_20260712.db \
        --quarantine-report artifacts/quarantine_legacy_20260712/camila_daniel_report.json
"""
import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(ROOT, 'schema_v5.2.sql')
IDEMPOTENCY_PATCH_PATH = os.path.join(ROOT, 'migrations', 'idempotency_patch_v5.2.sql')

sys.path.insert(0, ROOT)
from src.tenant_brand_map import all_resolved_brands, resolve_brand, UnresolvedBrandError  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()

UNMAPPED_ENTITIES = [
    'contracts.json', 'team.json', 'calendar.json', 'email_templates.json',
    'packages.json', 'settings.json', 'mail_log.json', 'mail_outbox.json',
]

# legacy_record_map.tenant_id es NOT NULL con FK a tenants(id) -- un
# registro cuyo tenant_id legado NO se pudo resolver via tenant_brand_map
# igual necesita quedar trazado (review_needed), asi que se usa este tenant
# centinela en vez de un string 'UNKNOWN' que rompería la FK. Nunca se usa
# para clients/projects/quotes reales -- solo para dejar constancia en
# legacy_record_map de que algo no se pudo mapear.
UNMAPPED_TENANT_ID = 'tenant-unmapped-legacy'


def _load(name, source_dir):
    path = os.path.join(source_dir, f'{name}.json')
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def _units(amount):
    """Q1,234.56 -> 123456 centavos, entero. Nunca float en la base."""
    try:
        return int(round(float(amount or 0) * 100))
    except (TypeError, ValueError):
        return 0


class Migrator:
    def __init__(self, conn, source_dir, db_path, quarantine_report_path=None):
        self.conn = conn
        self.source_dir = source_dir
        self.report = {
            'generated_at': NOW,
            'source_dir': source_dir,
            'shadow_db_path': db_path,
            'cutover_performed': False,
            'json_files_modified': False,
            'sqlite_crm_db_modified': False,
            'counts': {'json_source': {}, 'sqlite_imported': {}},
            'rows_imported': {},
            'rows_quarantined': {},
            'broken_relationships': [],
            'orphan_references': [],
            'duplicates_detected': [],
            'tenant_brand_conflicts': [],
            'financial_conflicts': [],
            'contractual_conflicts': [],
            'discarded_records': [],  # deberia quedar vacio siempre
            'unmapped_entities': UNMAPPED_ENTITIES,
            'legacy_record_map_entries': 0,
            'quarantine_report_used': quarantine_report_path,
        }
        self._client_id_by_legacy = {}
        self._project_id_by_legacy_lead_or_job = {}
        self._project_client_by_project_id = {}
        self._workflow_template_version_by_workflow_id = {}
        self._quarantine = self._load_quarantine_report(quarantine_report_path)

    def _load(self, name):
        return _load(name, self.source_dir)

    def _load_quarantine_report(self, path):
        if not path or not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)

    # ------------------------------------------------------------ helpers

    def _map_legacy(self, tenant_id, source_file, legacy_id, entity_type,
                     new_entity_id, status='imported', notes=None):
        self.conn.execute(
            """INSERT INTO legacy_record_map
               (id, tenant_id, source_file, legacy_id, entity_type,
                new_entity_id, migration_status, notes, migrated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f'lrm-{uuid.uuid4().hex[:12]}', tenant_id, source_file, legacy_id,
             entity_type, new_entity_id, status, notes, NOW),
        )
        self.report['legacy_record_map_entries'] += 1

    def _resolve_tenant_for_legacy_tenant_id(self, legacy_tenant_id):
        """legacy_tenant_id ('tenant-norkevin', etc) -> (tenant_id, company_id)
        canonicos en el schema V5.2, vía tenant_brand_map. Nunca por nombre."""
        try:
            brand = resolve_brand(legacy_tenant_id)
        except UnresolvedBrandError as exc:
            self.report['tenant_brand_conflicts'].append({
                'legacy_tenant_id': legacy_tenant_id, 'error': str(exc),
            })
            return None, None
        return brand.internal_tenant_id, f'company-{brand.brand_key}'

    # ------------------------------------------------------------ tenants

    def migrate_tenants_and_companies(self):
        self.conn.execute(
            """INSERT OR IGNORE INTO tenants (id, name, timezone, language, created_at)
               VALUES (?, 'UNMAPPED LEGACY (tenant no resoluble)', 'America/Guatemala', 'es', ?)""",
            (UNMAPPED_TENANT_ID, NOW),
        )
        for brand in all_resolved_brands():
            self.conn.execute(
                """INSERT OR IGNORE INTO tenants (id, name, timezone, language, created_at)
                   VALUES (?, ?, 'America/Guatemala', 'es', ?)""",
                (brand.internal_tenant_id, brand.display_name, NOW),
            )
            self.conn.execute(
                """INSERT OR IGNORE INTO companies
                   (id, tenant_id, slug, name, logo_letter, color, email,
                    currency_code, active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'GTQ', 1, ?, ?)""",
                (f'company-{brand.brand_key}', brand.internal_tenant_id,
                 brand.brand_key, brand.display_name, brand.display_name[:1].upper(),
                 '#000000', brand.sender_email, NOW, NOW),
            )
        self.conn.commit()

    # ------------------------------------------------------------ clients

    def migrate_clients(self):
        clients = self._load('clients')
        self.report['counts']['json_source']['clients'] = len(clients)
        imported = 0
        for c in clients:
            legacy_tenant = c.get('tenant_id')
            tenant_id, company_id = self._resolve_tenant_for_legacy_tenant_id(legacy_tenant)
            if not tenant_id:
                self._map_legacy(legacy_tenant or UNMAPPED_TENANT_ID, 'clients.json',
                                 c['id'], 'client', None, status='review_needed',
                                 notes='tenant_id legado sin marca canonica confirmada')
                continue
            new_id = f"client-{c['id']}"
            self.conn.execute(
                """INSERT OR IGNORE INTO clients
                   (id, tenant_id, first_name, last_name, source, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (new_id, tenant_id, c.get('first_name') or '', c.get('last_name') or '',
                 c.get('source') or c.get('fuente'), c.get('created') or NOW, NOW),
            )
            self._client_id_by_legacy[c['id']] = (new_id, tenant_id, company_id)
            self._map_legacy(tenant_id, 'clients.json', c['id'], 'client', new_id)
            imported += 1
        self.report['rows_imported']['clients'] = imported
        self.conn.commit()

    # ------------------------------------------------------------ projects (lead+job fusion)

    def migrate_projects(self):
        """V5.2 fusiona lead+job en 'projects' (decision de arquitectura ya
        aceptada en MODELO_DE_DATOS_CRM_V5.md, Opcion A). Un job.json es
        siempre un project con operational_status avanzado; un lead.json
        sin job asociado es un project con operational_status='lead'."""
        leads = self._load('leads')
        jobs = self._load('jobs')
        self.report['counts']['json_source']['leads'] = len(leads)
        self.report['counts']['json_source']['jobs'] = len(jobs)

        jobs_by_lead_id = {}
        for j in jobs:
            if j.get('lead_id'):
                jobs_by_lead_id.setdefault(j['lead_id'], []).append(j)

        imported_projects = 0

        def import_job_as_project(j, from_lead=None, client_legacy_id=None):
            nonlocal imported_projects
            legacy_tenant = j.get('tenant_id')
            tenant_id, company_id = self._resolve_tenant_for_legacy_tenant_id(legacy_tenant)
            if not tenant_id:
                self._map_legacy(legacy_tenant or UNMAPPED_TENANT_ID, 'jobs.json', j['id'],
                                 'project', None, status='review_needed',
                                 notes='tenant_id legado sin marca canonica confirmada')
                return
            quarantine_info = None
            if self._quarantine:
                quarantine_info = (self._quarantine.get('camila_rios', {})
                                    .get('jobs', {}).get(j['id']))
            is_quarantined = bool(quarantine_info and
                                   quarantine_info.get('proposed_status') == 'quarantined_superseded')
            new_id = f"project-{j['id']}"
            op_status = 'cancelled' if is_quarantined else self._map_job_status(j.get('status'))
            self.conn.execute(
                """INSERT OR IGNORE INTO projects
                   (id, tenant_id, company_id, name, type, event_date,
                    location_name, commercial_status, operational_status,
                    job_accepted_at, job_accepted_via, booked_value_units,
                    package_name_snapshot, cancellation_reason,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (new_id, tenant_id, company_id, j.get('nombre') or 'Sin nombre',
                 j.get('type') or 'boda', j.get('boda_date'), j.get('location') or '',
                 'accepted' if j.get('accepted_quote_id') else 'new_lead',
                 op_status, j.get('created'),
                 'quote_accepted' if j.get('accepted_quote_id') else None,
                 _units(j.get('price_total')), j.get('package'),
                 (f"QUARANTINED: superseded_by={quarantine_info.get('superseded_by')}"
                  if is_quarantined else None),
                 j.get('created') or NOW, NOW),
            )
            self._project_id_by_legacy_lead_or_job[j['id']] = (new_id, tenant_id, company_id)
            if from_lead:
                self._project_id_by_legacy_lead_or_job[from_lead] = (new_id, tenant_id, company_id)
            status = 'archived' if is_quarantined else 'imported'
            notes = ('quarantined_superseded, ver camila_daniel_report.json'
                     if is_quarantined else None)
            self._map_legacy(tenant_id, 'jobs.json', j['id'], 'project', new_id,
                             status=status, notes=notes)
            if is_quarantined:
                self.report['rows_quarantined'].setdefault('projects', []).append(j['id'])
            imported_projects += 1
            self._link_project_client(new_id, tenant_id, company_id, j.get('client_id'))

        for lead in leads:
            related_jobs = jobs_by_lead_id.get(lead['id'], [])
            if not related_jobs:
                legacy_tenant = lead.get('tenant_id')
                tenant_id, company_id = self._resolve_tenant_for_legacy_tenant_id(legacy_tenant)
                if not tenant_id:
                    self._map_legacy(legacy_tenant or UNMAPPED_TENANT_ID, 'leads.json', lead['id'],
                                     'project', None, status='review_needed',
                                     notes='tenant_id legado sin marca canonica confirmada')
                    continue
                new_id = f"project-{lead['id']}"
                self.conn.execute(
                    """INSERT OR IGNORE INTO projects
                       (id, tenant_id, company_id, name, type, event_date,
                        location_name, commercial_status, operational_status,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'boda', ?, ?, 'new_lead', 'lead', ?, ?)""",
                    (new_id, tenant_id, company_id, lead.get('nombre') or 'Sin nombre',
                     lead.get('fecha_tentativa'), lead.get('locacion') or '',
                     lead.get('created') or NOW, NOW),
                )
                self._project_id_by_legacy_lead_or_job[lead['id']] = (new_id, tenant_id, company_id)
                self._map_legacy(tenant_id, 'leads.json', lead['id'], 'project', new_id)
                imported_projects += 1
                self._link_project_client(new_id, tenant_id, company_id, lead.get('client_id'))
            else:
                if len(related_jobs) > 1:
                    self.report['duplicates_detected'].append({
                        'entity': 'jobs_for_same_lead', 'lead_id': lead['id'],
                        'job_ids': [j['id'] for j in related_jobs],
                        'note': ('Bug de idempotencia de /api/jobs/new -- ver '
                                 'STABILIZATION_PHASE_REPORT.md. Se importan '
                                 'TODOS como projects; los que aparezcan en '
                                 'quarantine_camila_daniel.py se marcan '
                                 'operational_status=cancelled.'),
                    })
                for j in related_jobs:
                    import_job_as_project(j, from_lead=lead['id'])
                # El lead en si tambien necesita su propia fila en
                # legacy_record_map -- si no, un lead con jobs asociados
                # desaparecia de la trazabilidad (bug encontrado por
                # compute_silently_dropped(): lead-camila-rios no tenia
                # entrada propia, solo sus jobs la tenian). Apunta al
                # project del primer job relacionado (mismo lead = mismo
                # project fusionado en V5.2).
                first_job_mapping = self._project_id_by_legacy_lead_or_job.get(lead['id'])
                if first_job_mapping:
                    self._map_legacy(first_job_mapping[1], 'leads.json', lead['id'],
                                     'project', first_job_mapping[0], status='merged',
                                     notes='lead fusionado con job(s) relacionados, ver entity_type=project')
                else:
                    self._map_legacy(UNMAPPED_TENANT_ID, 'leads.json', lead['id'],
                                     'project', None, status='review_needed',
                                     notes='lead con jobs relacionados pero ninguno se pudo migrar')

        # Jobs sin lead_id (o cuyo lead_id no aparece en leads.json).
        lead_ids_seen = {l['id'] for l in leads}
        for j in jobs:
            if j.get('lead_id') and j['lead_id'] in lead_ids_seen:
                continue  # ya se importo arriba
            if j['id'] in self._project_id_by_legacy_lead_or_job:
                continue
            if j.get('lead_id'):
                self.report['orphan_references'].append({
                    'entity': 'job', 'id': j['id'],
                    'missing_reference': f"lead_id={j['lead_id']} (no existe en leads.json)",
                })
            import_job_as_project(j)

        self.report['rows_imported']['projects'] = imported_projects
        self.conn.commit()

    def _link_project_client(self, project_id, tenant_id, company_id, client_legacy_id):
        """Fix del bloqueo anterior: antes quotes/invoices usaban un
        billing_project_client_id PLACEHOLDER que nunca existia en
        project_clients, y el INSERT hubiera fallado el FOREIGN KEY. Ahora
        se crea una fila real en project_clients (is_primary=1,
        is_billing_contact=1 -- el unico contacto conocido en los JSON
        legados, no hay dato de contactos secundarios/planner en esta
        version del JSON) ANTES de migrar quotes/invoices, y ese id real se
        usa como billing_project_client_id."""
        client_info = self._client_id_by_legacy.get(client_legacy_id)
        if not client_info:
            self.report['orphan_references'].append({
                'entity': 'project_client', 'id': project_id,
                'missing_reference': f'client_id={client_legacy_id} no resuelve a ningun client migrado',
            })
            return
        new_client_id, client_tenant_id, _client_company_id = client_info
        if client_tenant_id != tenant_id:
            self.report['tenant_brand_conflicts'].append({
                'issue': 'project_client tenant mismatch',
                'project_id': project_id, 'client_id': new_client_id,
                'project_tenant': tenant_id, 'client_tenant': client_tenant_id,
            })
            return
        pc_id = f"pc-{project_id}-{new_client_id}"
        self.conn.execute(
            """INSERT OR IGNORE INTO project_clients
               (id, tenant_id, project_id, client_id, role, is_primary,
                is_billing_contact, is_portal_contact, created_at)
               VALUES (?, ?, ?, ?, 'primary', 1, 1, 1, ?)""",
            (pc_id, tenant_id, project_id, new_client_id, NOW),
        )
        self._project_client_by_project_id[project_id] = pc_id

    @staticmethod
    def _map_job_status(legacy_status):
        return {
            'Confirmado': 'confirmed', 'Cotizando': 'lead', 'Archivado': 'completed',
            'Listo': 'completed', 'Cancelado': 'cancelled',
        }.get(legacy_status, 'confirmed')

    # ------------------------------------------------------------ quotes + invoices + payments

    def migrate_quotes_and_payments(self):
        quotes = self._load('quotes')
        payments = self._load('payments')
        self.report['counts']['json_source']['quotes'] = len(quotes)
        self.report['counts']['json_source']['payments'] = len(payments)

        imported_quotes = 0
        for q in quotes:
            proj = (self._project_id_by_legacy_lead_or_job.get(q.get('job_id'))
                    or self._project_id_by_legacy_lead_or_job.get(q.get('lead_id')))
            if not proj:
                self.report['orphan_references'].append({
                    'entity': 'quote', 'id': q['id'],
                    'missing_reference': f"job_id={q.get('job_id')} lead_id={q.get('lead_id')} "
                                          'no resuelven a ningun project migrado',
                })
                self._map_legacy(q.get('tenant_id') or UNMAPPED_TENANT_ID, 'quotes.json', q['id'],
                                 'quote', None, status='review_needed',
                                 notes='no se pudo resolver el project de origen')
                continue
            new_project_id, tenant_id, company_id = proj
            billing_pc_id = self._project_client_by_project_id.get(new_project_id)
            if not billing_pc_id:
                self.report['orphan_references'].append({
                    'entity': 'quote', 'id': q['id'],
                    'missing_reference': f'project {new_project_id} no tiene project_clients (billing contact desconocido)',
                })
                self._map_legacy(tenant_id, 'quotes.json', q['id'], 'quote', None,
                                 status='review_needed', notes='sin billing_project_client_id resoluble')
                continue
            new_id = f"quote-{q['id']}"
            total_units = _units(q.get('precio_total'))
            self.conn.execute(
                """INSERT OR IGNORE INTO quotes
                   (id, tenant_id, company_id, project_id, billing_project_client_id,
                    number, type, status, subtotal_units, total_units,
                    currency_code, currency_exponent, sent_at, accepted_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'GTQ', 2, ?, ?, ?)""",
                (new_id, tenant_id, company_id, new_project_id, billing_pc_id,
                 q['id'], ('pick_choose' if q.get('tipo_cotizacion') == 'pick_and_choose' else 'fixed'),
                 self._map_quote_status(q.get('status')), total_units, total_units,
                 q.get('sent_at'), q.get('aceptada_en'), q.get('created') or NOW),
            )
            self._map_legacy(tenant_id, 'quotes.json', q['id'], 'quote', new_id)
            imported_quotes += 1
        self.report['rows_imported']['quotes'] = imported_quotes

        # Pagos: se agrupan por (job_id, quote_id) como un invoice, y cada
        # fila de payments.json se importa como payment_installments +
        # (si status=='Pagado') payment_transactions.
        groups = {}
        for p in payments:
            if p.get('tipo') == 'team_payment':
                continue
            key = (p.get('job_id'), p.get('quote_id'))
            groups.setdefault(key, []).append(p)

        imported_invoices = 0
        imported_installments = 0
        imported_transactions = 0
        for (job_id, quote_id), rows in groups.items():
            proj = self._project_id_by_legacy_lead_or_job.get(job_id)
            if not proj:
                for p in rows:
                    self.report['orphan_references'].append({
                        'entity': 'payment', 'id': p['id'],
                        'missing_reference': f'job_id={job_id} no resuelve a ningun project migrado',
                    })
                    self._map_legacy(p.get('tenant_id') or UNMAPPED_TENANT_ID, 'payments.json',
                                     p['id'], 'payment_installment', None,
                                     status='review_needed', notes='job_id huerfano')
                continue
            new_project_id, tenant_id, company_id = proj
            billing_pc_id = self._project_client_by_project_id.get(new_project_id)
            if not billing_pc_id:
                for p in rows:
                    self.report['orphan_references'].append({
                        'entity': 'payment', 'id': p['id'],
                        'missing_reference': f'project {new_project_id} sin billing_project_client_id resoluble',
                    })
                    self._map_legacy(tenant_id, 'payments.json', p['id'],
                                     'payment_installment', None, status='review_needed')
                continue
            invoice_id = f"invoice-{job_id}-{quote_id or 'legacy'}"
            total_units = sum(_units(p.get('amount')) for p in rows)
            is_flagged = self._payment_group_is_flagged(job_id, quote_id, rows)
            self.conn.execute(
                """INSERT OR IGNORE INTO invoices
                   (id, tenant_id, company_id, project_id, billing_project_client_id,
                    quote_id, number, status, subtotal_units, total_units,
                    currency_code, currency_exponent, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'GTQ', 2, ?)""",
                (invoice_id, tenant_id, company_id, new_project_id, billing_pc_id,
                 (f'quote-{quote_id}' if quote_id else None), invoice_id,
                 'partially_paid' if is_flagged else 'issued',
                 total_units, total_units, NOW),
            )
            imported_invoices += 1
            for i, p in enumerate(rows, start=1):
                installment_id = f"installment-{p['id']}"
                self.conn.execute(
                    """INSERT OR IGNORE INTO payment_installments
                       (id, invoice_id, number, total_installments, due_date,
                        amount_units, notes, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (installment_id, invoice_id, i, len(rows), p.get('due_date') or NOW[:10],
                     _units(p.get('amount')),
                     ('QUARANTINE: requires_manual_financial_reconciliation'
                      if is_flagged else None), NOW),
                )
                imported_installments += 1
                if p.get('status') == 'Pagado':
                    idem_key = f"legacy-payment:{p['id']}"
                    tx_id = f"tx-{p['id']}"
                    self.conn.execute(
                        """INSERT OR IGNORE INTO payment_transactions
                           (id, tenant_id, company_id, project_id, invoice_id,
                            transaction_type, amount_units, currency_code,
                            currency_exponent, date, method, idempotency_key,
                            status, notes, created_at)
                           VALUES (?, ?, ?, ?, ?, 'payment', ?, 'GTQ', 2, ?, ?, ?, 'completed', ?, ?)""",
                        (tx_id, tenant_id, company_id, new_project_id, invoice_id,
                         _units(p.get('amount')), p.get('paid_date') or p.get('due_date') or NOW[:10],
                         'legacy_unspecified', idem_key,
                         ('QUARANTINE: requires_manual_financial_reconciliation'
                          if is_flagged else None), NOW),
                    )
                    imported_transactions += 1
                if is_flagged:
                    self.report['financial_conflicts'].append({
                        'legacy_payment_id': p['id'], 'job_id': job_id,
                        'quote_id': quote_id, 'amount': p.get('amount'),
                        'status': p.get('status'),
                        'reason': 'requires_manual_financial_reconciliation (ver quarantine report)',
                    })
                # El registro SI se importa (installment_id existe) aunque este
                # flagged -- 'review_needed' en legacy_record_map exige
                # new_entity_id NULL (CHECK constraint), y aca siempre hay uno.
                # La cuarentena financiera va en las notas, no en el status.
                self._map_legacy(tenant_id, 'payments.json', p['id'],
                                 'payment_installment', installment_id,
                                 status='imported',
                                 notes=('requires_manual_financial_reconciliation, '
                                        'ver financial_conflicts' if is_flagged else None))

        self.report['rows_imported']['invoices'] = imported_invoices
        self.report['rows_imported']['payment_installments'] = imported_installments
        self.report['rows_imported']['payment_transactions'] = imported_transactions
        self.conn.commit()

    def _payment_group_is_flagged(self, job_id, quote_id, rows):
        if not self._quarantine:
            return False
        camila = self._quarantine.get('camila_rios', {})
        daniel = self._quarantine.get('daniel_dubuc', {})
        flagged_ids = set()
        for section in (camila.get('payments', {}).get('detail', {}),):
            for grp in ('quote_a_la_que_esta_asociado_actualmente', 'accepted_quote'):
                for p in (section.get(grp, {}) or {}).get('payments', []) or []:
                    if p:
                        flagged_ids.add(p.get('id'))
        for grp in ('quote_a_la_que_esta_asociado_actualmente', 'accepted_quote'):
            for p in (daniel.get(grp, {}) or {}).get('payments', []) or []:
                if p:
                    flagged_ids.add(p.get('id'))
        return any(p.get('id') in flagged_ids for p in rows)

    @staticmethod
    def _map_quote_status(legacy_status):
        return {
            'Enviada': 'sent', 'Aceptada': 'accepted', 'Superada': 'superseded',
        }.get(legacy_status, 'draft')

    # ------------------------------------------------------------ workflow templates (legacy placeholder)

    def migrate_legacy_workflow_template_versions(self):
        """Fix del segundo bloqueo anterior: workflow_instances.template_version_id
        es NOT NULL y los JSON legados no traen ese id (el motor de workflows
        legado no versiona plantillas). En vez de inventar contenido de
        workflow (pasos, stage, etc.) para ese template -- que si seria
        adivinar datos -- se crea UNA familia+version 'legacy import' por
        cada (tenant, workflow_id legado) distinto, sin tareas asociadas,
        marcada explicitamente como placeholder de trazabilidad. Esto deja
        el registro migrado y consultable (no lo descarta), y el hueco real
        (que pasos tenia cada workflow_id legado) queda visible en
        contractual_conflicts para que un humano decida si vale la pena
        reconstruirlo desde workflow_history.json."""
        raw = self._load('workflow_instances')
        items = list(raw.values()) if isinstance(raw, dict) else list(raw)
        seen = set()
        for wi in items:
            subject_id = wi.get('subject_id')
            proj = self._project_id_by_legacy_lead_or_job.get(subject_id)
            if not proj:
                continue
            _new_project_id, tenant_id, company_id = proj
            workflow_id = wi.get('workflow_id') or 'unknown-legacy-workflow'
            key = (tenant_id, workflow_id)
            if key in seen:
                continue
            seen.add(key)
            family_id = f"wtf-legacy-{tenant_id}-{workflow_id}"
            version_id = f"wtv-legacy-{tenant_id}-{workflow_id}"
            self.conn.execute(
                """INSERT OR IGNORE INTO workflow_template_families
                   (id, tenant_id, company_id, name, description, active, created_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?)""",
                (family_id, tenant_id, company_id,
                 f'[LEGACY IMPORT] {workflow_id}',
                 'Placeholder de trazabilidad creado por migrate_json_to_v5_shadow.py '
                 'para poder migrar workflow_instances legados sin template_version_id. '
                 'No representa los pasos reales del workflow legado -- esos siguen '
                 'solo en workflow_history.json / workflow_instances.json originales.',
                 NOW),
            )
            self.conn.execute(
                """INSERT OR IGNORE INTO workflow_template_versions
                   (id, tenant_id, company_id, family_id, version, mode, notes, created_at)
                   VALUES (?, ?, ?, ?, 1, 'frozen', ?, ?)""",
                (version_id, tenant_id, company_id, family_id,
                 'LEGACY IMPORT PLACEHOLDER -- sin workflow_task_template_versions.', NOW),
            )
            self._workflow_template_version_by_workflow_id[key] = version_id
            self.report['contractual_conflicts'].append({
                'issue': 'workflow_template_version legacy placeholder (sin pasos reales)',
                'tenant_id': tenant_id, 'legacy_workflow_id': workflow_id,
                'placeholder_template_version_id': version_id,
                'note': 'Se creo una version vacia solo para satisfacer la FK NOT NULL '
                        'de workflow_instances. Reconstruir los pasos reales (si se '
                        'necesitan) es una decision humana separada -- no se adivinaron.',
            })
        self.conn.commit()

    # ------------------------------------------------------------ workflow_instances

    def migrate_workflow_instances(self):
        raw = self._load('workflow_instances')
        items = raw.values() if isinstance(raw, dict) else raw
        items = list(items)
        self.report['counts']['json_source']['workflow_instances'] = len(items)
        imported = 0
        for wi in items:
            subject_id = wi.get('subject_id')
            proj = self._project_id_by_legacy_lead_or_job.get(subject_id)
            if not proj:
                self.report['orphan_references'].append({
                    'entity': 'workflow_instance', 'id': wi.get('id'),
                    'missing_reference': f'subject_id={subject_id} no resuelve a ningun project migrado',
                })
                self._map_legacy(UNMAPPED_TENANT_ID, 'workflow_instances.json', wi.get('id'),
                                 'workflow_instance', None, status='review_needed')
                continue
            new_project_id, tenant_id, company_id = proj
            workflow_id = wi.get('workflow_id') or 'unknown-legacy-workflow'
            template_version_id = self._workflow_template_version_by_workflow_id.get((tenant_id, workflow_id))
            if not template_version_id:
                self.report['orphan_references'].append({
                    'entity': 'workflow_instance', 'id': wi.get('id'),
                    'missing_reference': f'no se pudo crear/resolver template_version_id legacy para workflow_id={workflow_id}',
                })
                self._map_legacy(tenant_id, 'workflow_instances.json', wi.get('id'),
                                 'workflow_instance', None, status='review_needed')
                continue
            is_quarantined = False
            if self._quarantine:
                for job_id, info in self._quarantine.get('camila_rios', {}).get('jobs', {}).items():
                    if any(w['id'] == wi.get('id') for w in info.get('workflow_instances_found', [])):
                        is_quarantined = info.get('proposed_status') == 'quarantined_superseded'
            new_id = f"wi-{wi.get('id')}"
            status = 'cancelled' if is_quarantined else self._map_wi_status(wi.get('status'))
            try:
                self.conn.execute(
                    """INSERT OR IGNORE INTO workflow_instances
                       (id, tenant_id, company_id, project_id, template_version_id,
                        template_version, mode, status, started_at, completed_at)
                       VALUES (?, ?, ?, ?, ?, 1, 'frozen', ?, ?, ?)""",
                    (new_id, tenant_id, company_id, new_project_id, template_version_id,
                     status, wi.get('created') or wi.get('trigger_at') or NOW,
                     wi.get('completed_at')),
                )
                imported += 1
                status_map = 'archived' if is_quarantined else 'imported'
                notes = ('quarantined_superseded (workflow de job duplicado)'
                         if is_quarantined else
                         'template_version_id es un placeholder legacy sin pasos reales, ver contractual_conflicts')
                self._map_legacy(tenant_id, 'workflow_instances.json', wi.get('id'),
                                 'workflow_instance', new_id, status=status_map, notes=notes)
            except sqlite3.IntegrityError as exc:
                # uq_project_active_workflow: solo puede haber 1 workflow activo/paused
                # por project. Con jobs duplicados (Camila) puede haber >1 workflow
                # 'active' legado apuntando al mismo project canonico -- eso es
                # exactamente el tipo de conflicto que este proyecto quiere DETECTAR,
                # no ocultar forzando el insert.
                self.report['duplicates_detected'].append({
                    'entity': 'workflow_instance_active_conflict',
                    'legacy_id': wi.get('id'), 'project_id': new_project_id,
                    'error': str(exc),
                    'note': 'Mas de un workflow activo/paused legado resuelve al mismo '
                            'project canonico -- constraint uq_project_active_workflow '
                            'lo rechazo. No se fuerza el insert.',
                })
                self._map_legacy(tenant_id, 'workflow_instances.json', wi.get('id'),
                                 'workflow_instance', None, status='review_needed',
                                 notes=f'IntegrityError: {exc}')
        self.report['rows_imported']['workflow_instances'] = imported
        self.conn.commit()

    @staticmethod
    def _map_wi_status(legacy_status):
        return {
            'active': 'active', 'paused': 'paused', 'completed': 'completed',
            'cancelled': 'cancelled', 'cancelled_duplicate': 'cancelled',
        }.get(legacy_status, 'completed')

    # ------------------------------------------------------------ reconciliation report

    def finalize_counts(self):
        cur = self.conn.cursor()
        for table in ('tenants', 'companies', 'clients', 'projects', 'project_clients',
                      'quotes', 'invoices', 'payment_installments', 'payment_transactions',
                      'workflow_template_families', 'workflow_template_versions',
                      'workflow_instances', 'legacy_record_map'):
            try:
                cur.execute(f'SELECT COUNT(*) FROM {table}')
                self.report['counts']['sqlite_imported'][table] = cur.fetchone()[0]
            except sqlite3.OperationalError as exc:
                self.report['counts']['sqlite_imported'][table] = f'ERROR: {exc}'

        cur.execute("PRAGMA foreign_key_check")
        fk_violations = cur.fetchall()
        self.report['foreign_key_check_violations'] = len(fk_violations)
        if fk_violations:
            self.report['foreign_key_check_detail'] = [
                {'table': row[0], 'rowid': row[1], 'refers_to': row[2]}
                for row in fk_violations[:50]
            ]

        cur.execute("PRAGMA integrity_check")
        integrity = cur.fetchall()
        self.report['integrity_check'] = [row[0] for row in integrity]

    def compute_silently_dropped(self):
        """silently_dropped_records: un legacy_id de una entidad migrable
        (client/project/quote/payment_installment/workflow_instance) que NO
        tiene NINGUNA fila en legacy_record_map -- ni siquiera 'review_needed'.
        Si tiene fila con status review_needed/skipped, esta quarantined/
        pendiente, no perdido en silencio."""
        cur = self.conn.cursor()
        cur.execute("SELECT source_file, legacy_id FROM legacy_record_map")
        mapped = {(row[0], row[1]) for row in cur.fetchall()}
        dropped = []
        checks = [
            ('clients.json', 'clients'), ('leads.json', 'leads'), ('jobs.json', 'jobs'),
            ('quotes.json', 'quotes'), ('payments.json', 'payments'),
            ('workflow_instances.json', 'workflow_instances'),
        ]
        for source_file, table in checks:
            records = self._load(table)
            items = list(records.values()) if isinstance(records, dict) else records
            for r in items:
                legacy_id = r.get('id')
                if not legacy_id:
                    continue
                if (source_file, legacy_id) not in mapped:
                    dropped.append({'source_file': source_file, 'legacy_id': legacy_id})
        self.report['silently_dropped_records'] = len(dropped)
        self.report['silently_dropped_detail'] = dropped[:100]
        return dropped

    def write_report(self, out_dir):
        os.makedirs(out_dir, exist_ok=True)
        json_path = os.path.join(out_dir, 'migration_reconciliation_report.json')
        with open(json_path, 'w', encoding='utf-8') as fh:
            json.dump(self.report, fh, indent=2, ensure_ascii=False)

        md_path = os.path.join(out_dir, 'migration_reconciliation_report.md')
        lines = ['# Reconciliation report — migracion shadow V5.2', '',
                  f'Generado: {self.report["generated_at"]}',
                  f'Fuente: `{self.report["source_dir"]}`',
                  f'Shadow DB: `{self.report["shadow_db_path"]}`', '',
                  '**Shadow only.** No se toco data/crm.db ni ningun data/*.json de produccion.', '',
                  '## Counts JSON vs SQLite', '',
                  '| Entidad | JSON | SQLite importado |', '|---|---|---|']
        for k, v in self.report['counts']['json_source'].items():
            lines.append(f"| {k} | {v} | {self.report['rows_imported'].get(k, '?')} |")
        lines.append('')
        lines.append(f"## integrity_check: {self.report.get('integrity_check')}")
        lines.append(f"## FK check violations: {self.report.get('foreign_key_check_violations', '?')}")
        lines.append('')
        lines.append(f"## silently_dropped_records: {self.report.get('silently_dropped_records', '?')}")
        lines.append('')
        lines.append(f"## Referencias huerfanas: {len(self.report['orphan_references'])}")
        for o in self.report['orphan_references'][:30]:
            lines.append(f"- `{o['entity']}` `{o['id']}`: {o['missing_reference']}")
        lines.append('')
        lines.append(f"## Duplicados detectados: {len(self.report['duplicates_detected'])}")
        for d in self.report['duplicates_detected']:
            lines.append(f"- {d['entity']}: {d}")
        lines.append('')
        lines.append(f"## Conflictos financieros: {len(self.report['financial_conflicts'])}")
        lines.append(f"## Conflictos contractuales: {len(self.report['contractual_conflicts'])}")
        lines.append(f"## Conflictos tenant/brand: {len(self.report['tenant_brand_conflicts'])}")
        lines.append(f"## Registros descartados (debe ser 0): {len(self.report['discarded_records'])}")
        lines.append('')
        lines.append(f"## Entidades JSON aun sin mapear a V5.2: {', '.join(UNMAPPED_ENTITIES)}")
        lines.append('')
        lines.append(f"## legacy_record_map: {self.report['legacy_record_map_entries']} filas escritas")
        with open(md_path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines))
        return json_path, md_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True,
                         help='Directorio con los *.json de origen (data/ para clean state, '
                              'o un fixture aislado para el escenario legacy).')
    parser.add_argument('--db-path', required=True,
                         help='Ruta del archivo .db shadow a crear (se borra y recrea entero '
                              'en cada corrida). NUNCA debe ser data/crm.db.')
    parser.add_argument('--out', default=None,
                         help='Directorio de salida del reporte (por defecto junto al --db-path).')
    parser.add_argument('--quarantine-report', default=None,
                         help='Ruta a camila_daniel_report.json generado por quarantine_camila_daniel.py '
                              'contra el MISMO --source. Opcional.')
    args = parser.parse_args()

    source_dir = os.path.abspath(args.source)
    db_path = os.path.abspath(args.db_path)
    out_dir = os.path.abspath(args.out) if args.out else os.path.dirname(db_path)

    if os.path.abspath(db_path) in (
        os.path.abspath(os.path.join(ROOT, 'data', 'crm.db')),
    ):
        print('ABORTADO: --db-path no puede ser data/crm.db (produccion).')
        return 1

    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA foreign_keys = ON')
    with open(SCHEMA_PATH, 'r', encoding='utf-8') as fh:
        conn.executescript(fh.read())
    if os.path.exists(IDEMPOTENCY_PATCH_PATH):
        with open(IDEMPOTENCY_PATCH_PATH, 'r', encoding='utf-8') as fh:
            conn.executescript(fh.read())

    migrator = Migrator(conn, source_dir, db_path, quarantine_report_path=args.quarantine_report)
    migrator.migrate_tenants_and_companies()
    migrator.migrate_clients()
    migrator.migrate_projects()
    migrator.migrate_quotes_and_payments()
    migrator.migrate_legacy_workflow_template_versions()
    migrator.migrate_workflow_instances()
    migrator.finalize_counts()
    migrator.compute_silently_dropped()
    json_path, md_path = migrator.write_report(out_dir)
    conn.close()

    print('OK -- shadow db:', db_path)
    print('Reporte:', json_path)
    print('Reporte (md):', md_path)
    print('data/crm.db y data/*.json de produccion NO fueron modificados.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
