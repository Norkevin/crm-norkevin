#!/usr/bin/env python3
"""
quarantine_camila_daniel.py

Fase de estabilizacion (agosto 2026), punto 4: identificar y marcar
explicitamente los registros huerfanos de Camila Rios y las duplicaciones
de pago de Daniel Dubuc encontradas durante la auditoria de datos.

REGLAS NO NEGOCIABLES (confirmadas por Kevin):
  - NO borra nada.
  - NO reasigna contratos automaticamente a un job "canonico".
  - NO mueve, fusiona ni "aplica" dinero ya cobrado a otra factura.
  - NO escribe sobre data/*.json directamente. Siempre produce un reporte
    (JSON + markdown) y un archivo de PATCH PROPUESTO por separado. Aplicar
    el patch es una accion humana explicita, en otro paso, con su propio
    backup -- este script no lo hace por si solo ni con --apply.

Uso:
    python quarantine_camila_daniel.py
        Solo lee data/*.json (en su ubicacion real, sin copiarlos) y escribe:
            data/quarantine_review/camila_daniel_report.json
            data/quarantine_review/camila_daniel_report.md
            data/quarantine_review/camila_daniel_proposed_patch.json

El patch propuesto es una lista de operaciones "quarantine" (agregar campos
de estado a un registro EXISTENTE, nunca crear/borrar/mezclar). Se decide
manualmente si se aplica, y con que herramienta (revision de Kevin +
Codex), no aca.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(REPO_ROOT, 'data')
OUT_DIR = os.path.join(DATA_DIR, 'quarantine_review')

# IDs conocidos por la auditoria de la fase de estabilizacion. Se dejan
# explicitos (no "todo lead cuyo nombre contenga Camila") para que el
# script sea auditable linea por linea, no una heuristica que podria
# atrapar un registro distinto por error de nombre.
CAMILA_LEAD_ID = 'lead-camila-rios'
CAMILA_CLIENT_ID = 'client-camila-rios'
CAMILA_CANONICAL_JOB_ID = 'boda-e8b7e2a7'  # aceptado PROVISIONALMENTE por Kevin
CAMILA_ORPHAN_JOB_IDS = ['boda-69f508a1', 'boda-1d62d5e2', 'boda-35bd38a1']
CAMILA_CONTRACTS_REQUIRING_REVIEW = ['contract-c1cfd9e3', 'contract-39404f47']
CAMILA_CANONICAL_CONTRACT_ID = 'contract-f2b491e4'
CAMILA_OLD_QUOTE_ID = 'quote-camila-rios'
CAMILA_ACCEPTED_QUOTE_ID = 'quote-47238c5c'
CAMILA_OLD_PAYMENT_IDS = ['pay-da08e486', 'pay-916cbc01']
CAMILA_NEW_PAYMENT_IDS = ['pay-0a7eebd9', 'pay-84f7d152']

DANIEL_JOB_ID = 'job-daniel-paola'
DANIEL_ACCEPTED_QUOTE_ID = 'quote-8efbddb9'
DANIEL_LEGACY_PAYMENT_IDS = ['pay-daniel-1', 'pay-daniel-2', 'pay-daniel-3']
DANIEL_NEW_PAYMENT_IDS = ['pay-efe93655', 'pay-27f94291']

NOW = datetime.now(timezone.utc).isoformat()
REVIEWER_NOTE_PREFIX = 'stabilization_phase_2026_08'


def _load(name, source_dir=None):
    path = os.path.join(source_dir or DATA_DIR, f'{name}.json')
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def _by_id(records, id_field='id'):
    return {r.get(id_field): r for r in records if r.get(id_field)}


def build_report(source_dir=None):
    jobs = _by_id(_load('jobs', source_dir))
    workflow_instances = _load('workflow_instances', source_dir)
    if isinstance(workflow_instances, dict):
        wi_by_subject = {}
        for wi_id, wi in workflow_instances.items():
            wi = dict(wi, id=wi.get('id', wi_id))
            wi_by_subject.setdefault(wi.get('subject_id'), []).append(wi)
    else:
        wi_by_subject = {}
        for wi in workflow_instances:
            wi_by_subject.setdefault(wi.get('subject_id'), []).append(wi)
    contracts = _by_id(_load('contracts', source_dir))
    payments = _by_id(_load('payments', source_dir))
    quotes = _by_id(_load('quotes', source_dir))

    report = {
        'generated_at': NOW,
        'read_only': True,
        'no_data_modified': True,
        'source_dir': source_dir or DATA_DIR,
        'camila_rios': _camila_section(jobs, wi_by_subject, contracts, payments, quotes),
        'daniel_dubuc': _daniel_section(jobs, payments, quotes),
    }
    return report


def _camila_section(jobs, wi_by_subject, contracts, payments, quotes):
    section = {
        'lead_id': CAMILA_LEAD_ID,
        'client_id': CAMILA_CLIENT_ID,
        'canonical_job_id_provisional': CAMILA_CANONICAL_JOB_ID,
        'canonical_reason': (
            'Unico de los 4 job_id encontrados en workflow_instances que '
            'sigue presente en jobs.json hoy, y el unico con accepted_quote_id '
            'poblado. Aceptado por Kevin como PROVISIONAL -- no implica que '
            'los contratos huerfanos deban reapuntarse automaticamente a el.'
        ),
        'jobs': {},
        'contracts': {},
        'payments': {'requires_manual_financial_reconciliation': True, 'detail': {}},
    }

    all_camila_job_ids = [CAMILA_CANONICAL_JOB_ID] + CAMILA_ORPHAN_JOB_IDS
    for job_id in all_camila_job_ids:
        job = jobs.get(job_id)
        instances = wi_by_subject.get(job_id, [])
        is_canonical = job_id == CAMILA_CANONICAL_JOB_ID
        section['jobs'][job_id] = {
            'exists_in_jobs_json': job is not None,
            'job_record': job,
            'workflow_instances_found': [
                {'id': wi.get('id'), 'status': wi.get('status'),
                 'trigger_at': wi.get('trigger_at'), 'workflow_id': wi.get('workflow_id')}
                for wi in instances
            ],
            'proposed_status': (
                'canonical_active' if is_canonical else 'quarantined_superseded'
            ),
            'proposed_workflow_instance_status': (
                None if is_canonical else 'cancelled_duplicate'
            ),
            'superseded_by': None if is_canonical else CAMILA_CANONICAL_JOB_ID,
        }

    for contract_id in CAMILA_CONTRACTS_REQUIRING_REVIEW + [CAMILA_CANONICAL_CONTRACT_ID]:
        contract = contracts.get(contract_id)
        section['contracts'][contract_id] = {
            'exists': contract is not None,
            'contract_record': contract,
            'job_id_referenced': (contract or {}).get('job_id'),
            'job_id_referenced_exists_in_jobs_json': (
                (contract or {}).get('job_id') in jobs
            ),
            'proposed_status': (
                'canonical_active' if contract_id == CAMILA_CANONICAL_CONTRACT_ID
                else 'requires_manual_contract_reconciliation'
            ),
            'reconciliation_questions': (
                [] if contract_id == CAMILA_CANONICAL_CONTRACT_ID else [
                    'Que quote/paquete representa este contrato -- coincide '
                    f'con {CAMILA_OLD_QUOTE_ID} o con {CAMILA_ACCEPTED_QUOTE_ID}?',
                    'Se envio de verdad (revisar sent_at y mail_log)?',
                    'A que destinatario se envio?',
                    'Hay evidencia de firma o aceptacion del cliente?',
                    'El contenido representa informacion CONTRACTUAL '
                    'distinta al contrato canonico (otro paquete, otro '
                    'precio) o es un duplicado exacto?',
                ]
            ),
        }

    old_quote = quotes.get(CAMILA_OLD_QUOTE_ID)
    new_quote = quotes.get(CAMILA_ACCEPTED_QUOTE_ID)
    old_payments = [payments.get(pid) for pid in CAMILA_OLD_PAYMENT_IDS]
    new_payments = [payments.get(pid) for pid in CAMILA_NEW_PAYMENT_IDS]
    monto_cobrado_conocido = sum(
        float(p.get('amount') or 0) for p in old_payments
        if p and p.get('status') == 'Pagado'
    )
    canonical_job = jobs.get(CAMILA_CANONICAL_JOB_ID) or {}
    section['payments']['detail'] = {
        'monto_cobrado_conocido': monto_cobrado_conocido,
        'quote_a_la_que_esta_asociado_actualmente': {
            'quote_id': CAMILA_OLD_QUOTE_ID,
            'paquete': (old_quote or {}).get('paquete_nombre'),
            'precio_total': (old_quote or {}).get('precio_total'),
            'status': (old_quote or {}).get('status'),
            'payments': old_payments,
        },
        'accepted_quote': {
            'quote_id': CAMILA_ACCEPTED_QUOTE_ID,
            'paquete': (new_quote or {}).get('paquete_nombre'),
            'precio_total': (new_quote or {}).get('precio_total'),
            'status': (new_quote or {}).get('status'),
            'payments': new_payments,
        },
        'job_price_total': canonical_job.get('price_total'),
        'job_price_paid_field': canonical_job.get('price_paid'),
        'diferencia_pendiente_potencial': (
            float((new_quote or {}).get('precio_total') or 0) - monto_cobrado_conocido
        ),
        'accion_automatica_tomada': 'NINGUNA -- requiere decision de Kevin',
    }
    return section


def _daniel_section(jobs, payments, quotes):
    job = jobs.get(DANIEL_JOB_ID)
    quote = quotes.get(DANIEL_ACCEPTED_QUOTE_ID)
    legacy_payments = [payments.get(pid) for pid in DANIEL_LEGACY_PAYMENT_IDS]
    new_payments = [payments.get(pid) for pid in DANIEL_NEW_PAYMENT_IDS]
    monto_cobrado_conocido = sum(
        float(p.get('amount') or 0) for p in legacy_payments
        if p and p.get('status') == 'Pagado'
    )
    return {
        'job_id': DANIEL_JOB_ID,
        'job_record': job,
        'accepted_quote_id': DANIEL_ACCEPTED_QUOTE_ID,
        'accepted_quote_reason': (
            'Coincide con accepted_quote_id del job. No hay duplicacion de '
            'JOB en este caso -- unicamente de PAGOS: dos calendarios '
            'distintos conviven sobre el mismo job.'
        ),
        'requires_manual_financial_reconciliation': True,
        'monto_cobrado_conocido': monto_cobrado_conocido,
        'quote_a_la_que_esta_asociado_actualmente': {
            'nota': 'Los pagos legacy no tienen quote_id -- son anteriores al modelo de invoices-por-quote.',
            'payments': legacy_payments,
        },
        'accepted_quote': {
            'quote_id': DANIEL_ACCEPTED_QUOTE_ID,
            'paquete': (quote or {}).get('paquete_nombre'),
            'precio_total': (quote or {}).get('precio_total'),
            'status': (quote or {}).get('status'),
            'payments': new_payments,
        },
        'job_price_total': (job or {}).get('price_total'),
        'job_price_paid_field': (job or {}).get('price_paid'),
        'diferencia_pendiente_potencial': (
            float((quote or {}).get('precio_total') or 0) - monto_cobrado_conocido
        ),
        'sobrefacturacion_potencial_si_no_se_reconcilia': (
            sum(float(p.get('amount') or 0) for p in legacy_payments if p)
            + sum(float(p.get('amount') or 0) for p in new_payments if p)
            - float((job or {}).get('price_total') or 0)
        ),
        'accion_automatica_tomada': 'NINGUNA -- requiere decision de Kevin',
    }


def build_proposed_patch(report):
    """Lista de operaciones QUARANTINE propuestas, NO aplicadas por este
    script. Cada operacion agrega campos a un registro existente; ninguna
    borra, fusiona ni mueve dinero."""
    ops = []
    for job_id, info in report['camila_rios']['jobs'].items():
        if info['proposed_status'] == 'quarantined_superseded' and info['exists_in_jobs_json']:
            ops.append({
                'op': 'add_fields', 'table': 'jobs', 'id': job_id,
                'fields': {
                    'status': 'quarantined_superseded',
                    'superseded_by_job_id': info['superseded_by'],
                    'quarantine_reason': (
                        f'{REVIEWER_NOTE_PREFIX}: job duplicado detectado para '
                        f'{CAMILA_LEAD_ID}, ver camila_daniel_report.json'
                    ),
                    'quarantined_at': NOW,
                },
            })
        for wi in info['workflow_instances_found']:
            if info['proposed_workflow_instance_status']:
                ops.append({
                    'op': 'add_fields', 'table': 'workflow_instances', 'id': wi['id'],
                    'fields': {
                        'status': 'cancelled_duplicate',
                        'quarantine_reason': (
                            f'{REVIEWER_NOTE_PREFIX}: workflow de job duplicado '
                            f'{job_id}, superseded_by={info["superseded_by"]}'
                        ),
                        'quarantined_at': NOW,
                    },
                })
    for contract_id, info in report['camila_rios']['contracts'].items():
        if info['proposed_status'] == 'requires_manual_contract_reconciliation' and info['exists']:
            ops.append({
                'op': 'add_fields', 'table': 'contracts', 'id': contract_id,
                'fields': {
                    'review_status': 'requires_manual_contract_reconciliation',
                    'reconciliation_questions': info['reconciliation_questions'],
                    'flagged_at': NOW,
                },
            })
    for pid in CAMILA_OLD_PAYMENT_IDS + CAMILA_NEW_PAYMENT_IDS:
        ops.append({
            'op': 'add_fields', 'table': 'payments', 'id': pid,
            'fields': {
                'review_status': 'requires_manual_financial_reconciliation',
                'flagged_at': NOW,
            },
        })
    for pid in DANIEL_LEGACY_PAYMENT_IDS + DANIEL_NEW_PAYMENT_IDS:
        ops.append({
            'op': 'add_fields', 'table': 'payments', 'id': pid,
            'fields': {
                'review_status': 'requires_manual_financial_reconciliation',
                'flagged_at': NOW,
            },
        })
    return {
        'generated_at': NOW,
        'applied': False,
        'apply_instructions': (
            'Este archivo es una PROPUESTA. Para aplicarlo: revisar cada '
            'operacion a mano, hacer backup de data/*.json, y escribir un '
            'script separado y explicito que las aplique (o aplicarlas a '
            'mano). Este script (quarantine_camila_daniel.py) NUNCA escribe '
            'sobre data/jobs.json, data/contracts.json ni data/payments.json.'
        ),
        'operations': ops,
    }


def render_markdown(report):
    lines = ['# Quarantine report: Camila Rios y Daniel Dubuc', '',
             f'Generado: {report["generated_at"]}', '',
             '**Read-only. Ningun archivo de data/ fue modificado por este script.**', '']

    lines.append('## Camila Rios\n')
    lines.append(f'Job canonico provisional: `{report["camila_rios"]["canonical_job_id_provisional"]}`\n')
    lines.append('| Job ID | Existe en jobs.json | Workflow instances | Propuesta |')
    lines.append('|---|---|---|---|')
    for job_id, info in report['camila_rios']['jobs'].items():
        wi_txt = ', '.join(w['id'] for w in info['workflow_instances_found']) or '(ninguno)'
        lines.append(f"| `{job_id}` | {info['exists_in_jobs_json']} | {wi_txt} | {info['proposed_status']} |")
    lines.append('')
    lines.append('| Contrato | Existe | job_id referenciado | ese job existe hoy | Propuesta |')
    lines.append('|---|---|---|---|---|')
    for cid, info in report['camila_rios']['contracts'].items():
        lines.append(f"| `{cid}` | {info['exists']} | `{info['job_id_referenced']}` | "
                      f"{info['job_id_referenced_exists_in_jobs_json']} | {info['proposed_status']} |")
    lines.append('')
    pay = report['camila_rios']['payments']['detail']
    lines.append('### Reconciliacion financiera de Camila (NADA aplicado automaticamente)\n')
    lines.append(f"- Monto cobrado conocido: Q{pay['monto_cobrado_conocido']:,.2f}")
    lines.append(f"- Asociado actualmente a: `{pay['quote_a_la_que_esta_asociado_actualmente']['quote_id']}` "
                  f"({pay['quote_a_la_que_esta_asociado_actualmente']['paquete']})")
    lines.append(f"- Quote aceptada: `{pay['accepted_quote']['quote_id']}` "
                  f"({pay['accepted_quote']['paquete']})")
    lines.append(f"- Diferencia pendiente potencial: Q{pay['diferencia_pendiente_potencial']:,.2f}")
    lines.append('')

    lines.append('## Daniel Dubuc\n')
    d = report['daniel_dubuc']
    lines.append(f"- Job: `{d['job_id']}` (sin duplicado de job -- solo de pagos)")
    lines.append(f"- Monto cobrado conocido (schedule legacy): Q{d['monto_cobrado_conocido']:,.2f}")
    lines.append(f"- Quote aceptada: `{d['accepted_quote_id']}`")
    lines.append(f"- Diferencia pendiente potencial: Q{d['diferencia_pendiente_potencial']:,.2f}")
    lines.append(f"- Sobrefacturacion potencial si NO se reconcilia: Q{d['sobrefacturacion_potencial_si_no_se_reconcilia']:,.2f}")
    lines.append('')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--source', default=None,
        help=(
            'Directorio con jobs.json/contracts.json/payments.json/quotes.json/'
            'workflow_instances.json a analizar. NUNCA asume data/ por defecto '
            'si se pasa explicitamente -- por defecto usa data/ (produccion) '
            'solo si no se pasa nada, para no romper compatibilidad con '
            'invocaciones previas.'
        ),
    )
    parser.add_argument(
        '--out', default=None,
        help='Directorio de salida para los reportes (por defecto <source>/quarantine_review).',
    )
    args = parser.parse_args()

    source_dir = os.path.abspath(args.source) if args.source else DATA_DIR
    out_dir = os.path.abspath(args.out) if args.out else os.path.join(source_dir, 'quarantine_review')

    os.makedirs(out_dir, exist_ok=True)
    report = build_report(source_dir)
    patch = build_proposed_patch(report)
    md = render_markdown(report)

    with open(os.path.join(out_dir, 'camila_daniel_report.json'), 'w', encoding='utf-8') as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(out_dir, 'camila_daniel_proposed_patch.json'), 'w', encoding='utf-8') as fh:
        json.dump(patch, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(out_dir, 'camila_daniel_report.md'), 'w', encoding='utf-8') as fh:
        fh.write(md)

    print('OK -- fuente:', source_dir)
    print('OK -- reporte generado en', out_dir)
    print('Ningun archivo de', source_dir, 'fue modificado (solo lectura).')
    print('Operaciones propuestas (no aplicadas):', len(patch['operations']))


if __name__ == '__main__':
    sys.exit(main())
