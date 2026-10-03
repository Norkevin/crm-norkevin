"""Reconcile active jobs from an explicit, tenant-scoped Studio Ninja snapshot.

No workflow actions or document signatures are executed by this importer.
"""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal


def _money(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError('Monto inválido')
    return amount.quantize(Decimal('.01'))


def _day(value):
    if value:
        date.fromisoformat(value)
    return value or ''


def import_active_jobs(crm, entries, tenant_id):
    if not tenant_id:
        raise ValueError('Cuenta requerida')
    planned, seen = [], set()
    # Validate the entire batch before the first write.
    for entry in entries:
        sid = str(entry.get('source_id', ''))
        if not sid.isdigit() or sid in seen:
            raise ValueError('Identificador de Studio Ninja inválido o repetido')
        seen.add(sid)
        jid = entry.get('existing_job_id') or f'boda-sn-active-{sid}'
        job = crm.get_job(jid)
        if entry.get('existing_job_id') and not job:
            raise ValueError('No se encontró el trabajo existente en esta cuenta')
        if job and job.get('tenant_id') != tenant_id:
            raise ValueError('Trabajo de otra cuenta')
        if not entry.get('job_name') or not entry.get('workflow') or not entry.get('clients'):
            raise ValueError('Trabajo, clientes y workflow requeridos')
        _day(entry.get('boda_date')); _day(entry.get('end_date'))
        for event in entry.get('extra_events', []):
            _day(event['start_date']); _day(event['end_date'])
        if not entry.get('source_url', '').startswith('https://app.studioninja.co/'):
            raise ValueError('Enlace de origen inválido')
        for document in entry.get('documents', []):
            if not document.get('url', '').startswith('https://app.studioninja.co/'):
                raise ValueError('Enlace de documento inválido')
        for step in entry['workflow']:
            if not step.get('name') or step.get('status') not in ('done', 'pending'):
                raise ValueError('Etapa inválida')
        for invoice in entry.get('invoices', []):
            total = _money(invoice['total'])
            if sum((_money(p['amount']) for p in invoice['payments']), Decimal(0)) != total:
                raise ValueError('Las cuotas no coinciden con la factura de origen')
            for p in invoice['payments']:
                if p['status'] not in ('Pagado', 'Pendiente', 'Late'):
                    raise ValueError('Estado de pago inválido')
                _day(p['due_date']); _day(p.get('paid_date'))
                if p['status'] == 'Pagado' and not p.get('paid_date'):
                    raise ValueError('Fecha de pago requerida')
        old_payments = [p for p in crm.list_payments(tenant_id) if p.get('job_id') == jid]
        row_count = sum(len(i['payments']) for i in entry.get('invoices', []))
        if len(old_payments) > row_count:
            raise ValueError('Hay más pagos en Flow que en origen; revisar antes de importar')
        planned.append((entry, jid, job, old_payments))
    for table in ('jobs', 'clients', 'leads', 'payments', 'quotes', 'payment_schedules', 'calendar', 'job_clients'):
        crm.store.backup_now(table)
    created, updated, skipped = [], [], []
    now = datetime.now().isoformat()
    for entry, jid, existing, old_payments in planned:
        if existing and existing.get('studio_ninja_active_source_id') == entry['source_id']:
            skipped.append(entry['job_name']); continue
        job = deepcopy(existing or {'id': jid, 'tenant_id': tenant_id})
        if existing:
            job['studio_ninja_before_import'] = {
                'job': deepcopy(existing), 'payments': deepcopy(old_payments),
                'quotes': [deepcopy(q) for q in crm.list_quotes(tenant_id) if q.get('job_id') == jid],
                'schedules': [deepcopy(s) for s in crm.store.list('payment_schedules') if s.get('job_id') == jid],
                'job_clients': deepcopy(crm._job_client_relations(existing)),
                'calendar': [deepcopy(e) for e in crm.store.list('calendar') if e.get('job_id') == jid],
            }
        client_ids = []
        for n, client in enumerate(entry['clients']):
            email = (client.get('email') or '').lower().strip()
            found = next((c for c in crm.list_clients(tenant_id) if email and (c.get('email') or '').lower().strip() == email), None)
            cid = found['id'] if found else f'client-sn-active-{entry["source_id"]}-{n}'
            if not found:
                crm.store.upsert('clients', dict(client, id=cid, tenant_id=tenant_id, estado='Activo', created=entry.get('created') or now[:10]))
            client_ids.append(cid)
        job.update(nombre=entry['job_name'], type=entry.get('type') or 'BODAS',
                   boda_date=entry['boda_date'], end_date=entry.get('end_date') or entry['boda_date'],
                   location=entry.get('location') or '', status='En produccion',
                   client_id=client_ids[0], client_ids=client_ids,
                   secondary_client_id=client_ids[1] if len(client_ids) > 1 else None,
                   lead_source=entry.get('lead_source') or '',
                   studio_ninja_active_source_id=entry['source_id'], studio_ninja_url=entry['source_url'],
                   studio_ninja_workflow=deepcopy(entry['workflow']), studio_ninja_workflow_name='Studio Ninja',
                   studio_ninja_documents=deepcopy(entry.get('documents', [])),
                   studio_ninja_imported_at=now, updated_at=now,
                   price_total=float(sum((_money(i['total']) for i in entry.get('invoices', [])), Decimal(0))))
        job.setdefault('created', entry.get('created') or now[:10])
        note = entry.get('notes', '')
        job['notes'] = ((job.get('notas') or job.get('notes') or '') + '\n\n' + note).strip()
        job['notas'] = job['notes']
        lead = crm.get_lead(job.get('lead_id')) if job.get('lead_id') else None
        if lead:
            lead.update(fuente=job['lead_source'], studio_ninja_source_id=entry['source_id'])
            crm.store.upsert('leads', lead)
        old_payments.sort(key=lambda p: (p.get('cuota') or 0, p.get('due_date') or '', p['id']))
        ordinal = 0
        for inv_no, invoice in enumerate(entry.get('invoices', [])):
            quote_id = old_payments[0].get('quote_id') if inv_no == 0 and old_payments else None
            quote = crm.store.get('quotes', quote_id) if quote_id else None
            if quote:
                quote.update(precio_total=invoice['total'], price_total=invoice['total'], total=invoice['total'])
                crm.store.upsert('quotes', quote)
            ids = []
            for n, row in enumerate(invoice['payments']):
                previous = old_payments[ordinal] if ordinal < len(old_payments) else {}
                pid = previous.get('id') or f'pay-sn-active-{entry["source_id"]}-{inv_no}-{n}'
                ordinal += 1; ids.append(pid)
                paid = row['status'] == 'Pagado'
                crm.store.upsert('payments', dict(previous, id=pid, tenant_id=tenant_id, job_id=jid,
                    client_id=client_ids[0], quote_id=quote_id,
                    invoice_id=invoice['invoice_no'], invoice_group_id=f'sn-active-{entry["source_id"]}-{inv_no}',
                    concepto=invoice.get('title') or f'Factura Studio Ninja {invoice["invoice_no"]}',
                    original_amount=row['amount'], amount=row['amount'], paid_amount=row['amount'] if paid else 0,
                    status=row['status'], due_date=row['due_date'], cuota=n+1,
                    paid_date=row.get('paid_date') or '', fecha_pago=row.get('paid_date') or '',
                    created=invoice.get('issued') or now[:10], studio_ninja_url=invoice['url']))
            schedules = [s for s in crm.store.list('payment_schedules') if s.get('job_id') == jid and s.get('status') == 'active']
            schedule = next((s for s in schedules if s.get('origin') == quote_id), schedules[0] if inv_no == 0 and schedules else None)
            if schedule:
                schedule.update(total_plan=invoice['total'], suma_cuotas=invoice['total'], cuotas=len(ids), payment_ids=ids)
                crm.store.upsert('payment_schedules', schedule)
        # Reuse existing event mirrors, retaining their former values in the audit.
        old_tasks = [t for t in job.get('manual_workflow_tasks', []) if t.get('type') == 'extra-event']
        new_tasks = []
        for n, event in enumerate(entry.get('extra_events', [])):
            old = old_tasks[n] if n < len(old_tasks) else {}
            eid = old.get('calendar_event_id') or f'evt-sn-active-{entry["source_id"]}-{n}'
            task = dict(event, id=old.get('id') or event['step_id'], type='extra-event', calendar_event_id=eid, show_in_portal=True)
            new_tasks.append(task)
            crm.store.upsert('calendar', dict(id=eid, tenant_id=tenant_id, job_id=jid, type='event',
                title=f'{event["name"]} - {entry["job_name"]}', date=event['start_date'], end_date=event['end_date'],
                start_time=event.get('start_time') or '', end_time=event.get('end_time') or '', location=event.get('location') or '',
                notes='Migrado de Studio Ninja', created=now[:10]))
        job['manual_workflow_tasks'] = new_tasks + [t for t in job.get('manual_workflow_tasks', []) if t.get('type') != 'extra-event']
        crm.upsert_job(job)
        relations = [(cid, 'principal' if n == 0 else ('wedding_planner' if 'planner' in (entry['clients'][n].get('last_name') or '').lower() or 'ByExpierence' in entry['clients'][n].get('first_name', '') else 'pareja')) for n, cid in enumerate(client_ids)]
        relations += [(r['client_id'], r['role']) for r in crm._job_client_relations(job) if r['client_id'] not in client_ids]
        crm._set_job_clients(job, relations, tenant_id=tenant_id)
        (updated if existing else created).append(entry['job_name'])
    return {'ok': True, 'created': created, 'updated': updated, 'skipped': skipped,
            'message': f'{len(created)} trabajos creados; {len(updated)} actualizados; {len(skipped)} ya importados'}
