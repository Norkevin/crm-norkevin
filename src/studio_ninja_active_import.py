"""Reconcile active jobs from an explicit, tenant-scoped Studio Ninja snapshot.

No workflow actions or document signatures are executed by this importer.
"""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re


def recover_service_documents(crm, entries, tenant_id):
    """Restore source descriptions only; payment and booking state stay intact."""
    planned, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):raise ValueError('Registro de origen inválido')
        jid, sid = entry.get('existing_job_id'), str(entry.get('source_id') or '')
        job = crm.get_job(jid)
        payments = [p for p in crm.list_payments(tenant_id) if p.get('job_id') == jid]
        if (not job or job.get('tenant_id') != tenant_id or jid in seen or not sid.isdigit()
                or str(job.get('studio_ninja_active_source_id')) != sid
                or not str(job.get('boda_date', '')).startswith('2026-') or not crm._job_is_active(job, payments)):
            raise ValueError('Selecciona trabajos activos de 2026 con el mismo origen y marca')
        seen.add(jid)
        invoice_urls = {p.get('studio_ninja_url') for p in payments}
        invoice_urls.update(i.get('url') for i in job.get('studio_ninja_payment_invoices', []))
        documents, urls = [], set()
        if not isinstance(entry.get('documents'), list):raise ValueError('Documentos de origen requeridos')
        for doc in entry['documents']:
            if not isinstance(doc, dict):raise ValueError('Documento de origen inválido')
            kind, url = doc.get('kind'), doc.get('url', '')
            pattern = (rf'https://app\.studioninja\.co/jobs/{sid}/quotes/(?:fixed|pick-and-choose)/\d+'
                       if kind == 'quote' else r'https://app\.studioninja\.co/invoices/\d+' if kind == 'invoice' else '')
            if not pattern or not re.fullmatch(pattern, url) or url in urls:
                raise ValueError('Documento original inválido o repetido')
            if kind == 'invoice' and url not in invoice_urls:
                raise ValueError('La factura original no pertenece a esta boda')
            urls.add(url)
            items = []
            if not isinstance(doc.get('items'), list) or len(doc['items']) > 100:raise ValueError('Lista de servicios inválida')
            for item in doc['items']:
                if (not isinstance(item, dict) or not isinstance(item.get('name'), str) or not item['name'].strip()
                        or len(item['name']) > 300 or not isinstance(item.get('description'), str) or len(item['description']) > 30000):
                    raise ValueError('El servicio necesita su nombre y descripción originales')
                items.append({k: str(item.get(k) or '') for k in ('name','description','quantity','unit_price','amount')})
                if any(len(items[-1][k]) > 100 for k in ('quantity','unit_price','amount')):
                    raise ValueError('Cantidad o precio de origen inválido')
            text = doc.get('source_text', '')
            number = doc.get('number')
            if not items or not isinstance(text, str) or len(text) > 100000 or not isinstance(number, str) or not number or len(number) > 100:
                raise ValueError('Conserva el número y el desglose del documento original')
            try:total=float(_money(doc.get('total')))
            except (InvalidOperation, TypeError):raise ValueError('Total del documento inválido')
            documents.append(dict(id=kind+'-'+url.rsplit('/',1)[1], kind=kind, url=url, number=number,
                                  total=total, items=items, source_text=text))
        if not documents:raise ValueError('No hay servicios originales para recuperar')
        planned.append((deepcopy(job), documents))
    crm.store.backup_now('jobs')
    updated, skipped = [], []
    for job, documents in planned:
        existing = {d['id']: d for d in job.get('studio_ninja_service_documents', [])}
        merged = dict(existing)
        merged.update({d['id']: d for d in documents})
        if merged == existing:skipped.append(job['nombre']); continue
        job['studio_ninja_service_documents'] = list(merged.values())
        job['studio_ninja_services_recovered_at'] = datetime.now().isoformat()
        crm.upsert_job(job)
        updated.append(job['nombre'])
    return dict(ok=True, updated=updated, created=[], skipped=skipped,
                message=f'{len(updated)} bodas con cotizaciones y servicios recuperados; pagos conservados')


def source_invoice(job, payment):
    documents = (job or {}).get('studio_ninja_service_documents', [])
    return next((d for d in documents if d['kind'] == 'invoice' and
                 (d['url'] == payment.get('studio_ninja_url') if payment.get('studio_ninja_url')
                  else d['number'] == str(payment.get('invoice_id') or ''))), None)


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


def import_document_copies(crm, entries, tenant_id):
    """Save readable source copies as files; never manufacture signed contracts."""
    from hashlib import sha256
    from html import escape
    from pathlib import Path
    planned = []
    for entry in entries:
        job = crm.get_job(entry.get('existing_job_id'))
        if not job or job.get('tenant_id') != tenant_id or job.get('studio_ninja_active_source_id') != entry.get('source_id'):
            raise ValueError('Documento sin trabajo importado en esta cuenta')
        for doc in entry.get('documents', []):
            url, text = doc.get('url', ''), doc.get('text', '')
            if not url.startswith('https://app.studioninja.co/') or not text or len(text) > 1000000:
                raise ValueError('Copia documental inválida')
            planned.append((job, doc))
    crm.store.backup_now('files')
    created, skipped = [], []
    Path(crm.UPLOADS_DIR).mkdir(parents=True, exist_ok=True)
    for job, doc in planned:
        fid = 'file-sn-' + sha256((tenant_id + job['id'] + doc['url']).encode()).hexdigest()[:20]
        if crm.store.get('files', fid):
            skipped.append(doc['type']); continue
        name = f'{doc["type"]} Studio Ninja - copia textual.html'
        stored = fid + '__copia-studio-ninja.html'
        body = ('<!doctype html><html lang="es"><meta charset="utf-8"><title>' + escape(name) + '</title>'
                '<style>body{font:16px/1.6 system-ui;max-width:900px;margin:40px auto;padding:24px}pre{white-space:pre-wrap;font:inherit}</style>'
                '<h1>' + escape(doc['type']) + ' — Studio Ninja</h1><p>Copia textual de los datos visibles del documento original. '
                'Conserva el contenido y los estados históricos; las imágenes de firmas y el diseño están en el original.</p>'
                '<p><a href="' + escape(doc['url'], quote=True) + '">Abrir documento original</a></p><hr><pre>' + escape(doc['text']) + '</pre></html>')
        Path(crm.UPLOADS_DIR, stored).write_text(body, encoding='utf-8')
        crm.store.upsert('files', {'id': fid, 'tenant_id': tenant_id, 'job_id': job['id'],
            'client_id': job.get('client_id'), 'lead_id': job.get('lead_id') or '',
            'name': name, 'stored': stored, 'size': f'{len(body.encode()) / 1048576:.2f} MB',
            'status': 'Uploaded', 'created': datetime.now().date().isoformat(), 'source_url': doc['url']})
        created.append(name)
    return {'ok': True, 'created': created, 'skipped': skipped, 'message': f'{len(created)} copias documentales guardadas'}


def reconcile_payments(crm, entries, tenant_id, dry_run=False):
    """Replace invoice summaries with source installments, preserving record IDs.

    Write-offs remain invoice metadata, never cash or a collectible payment.
    All validation precedes writes; backups and per-job snapshots permit recovery.
    """
    if not tenant_id or not entries:
        raise ValueError('Cuenta y trabajos requeridos')
    planned, seen_jobs, seen_invoices = [], set(), set()
    all_payments = crm.list_payments(tenant_id)
    for entry in entries:
        jid, sid = entry.get('existing_job_id'), str(entry.get('source_id') or '')
        job = crm.get_job(jid)
        if not job or job.get('tenant_id') != tenant_id or jid in seen_jobs:
            raise ValueError('Trabajo inexistente, repetido o de otra cuenta')
        if not sid.isdigit() or entry.get('source_url') != f'https://app.studioninja.co/jobs/view/{sid}':
            raise ValueError('Origen del trabajo inválido')
        if job.get('studio_ninja_active_source_id') and str(job['studio_ninja_active_source_id']) != sid:
            raise ValueError('El origen no coincide con el trabajo activo')
        seen_jobs.add(jid)
        invoices = entry.get('invoices') or []
        if not invoices:
            raise ValueError('Facturas requeridas')
        new_rows = []
        for invoice in invoices:
            url = invoice.get('url', '')
            if not url.startswith('https://app.studioninja.co/invoices/') or not url.rsplit('/', 1)[-1].isdigit() or url in seen_invoices:
                raise ValueError('Factura de origen inválida o repetida')
            seen_invoices.add(url)
            total = _money(invoice['total'])
            writeoffs = sum((_money(w['amount']) for w in invoice.get('writeoffs', [])), Decimal(0))
            for w in invoice.get('writeoffs', []):
                if not w.get('date') or not w.get('due_date'):
                    raise ValueError('Fechas de condonación requeridas')
                _day(w['date']); _day(w['due_date'])
            if sum((_money(p['amount']) for p in invoice['payments']), Decimal(0)) + writeoffs != total:
                raise ValueError('Cobros, saldo y condonaciones no coinciden con la factura')
            if not invoice.get('invoice_no'):
                raise ValueError('Número de factura requerido')
            for n, row in enumerate(invoice['payments']):
                if row.get('status') not in ('Pagado', 'Pendiente', 'Late') or not row.get('due_date'):
                    raise ValueError('Estado o vencimiento inválido')
                _day(row['due_date']); _day(row.get('paid_date'))
                if row['status'] == 'Pagado' and not row.get('paid_date'):
                    raise ValueError('Fecha de cobro requerida')
                new_rows.append((invoice, n, row))
        old_rows = [p for p in all_payments if p.get('job_id') == jid and p.get('tipo') != 'team_payment']
        if len(old_rows) > len(new_rows):
            raise ValueError(f'Hay más pagos existentes que cuotas de origen en {jid}; revisar antes de reemplazar')
        planned.append((entry, deepcopy(job), old_rows, new_rows))
    result = {'ok': True, 'skipped': [], 'jobs': len(planned), 'payments': sum(len(p[3]) for p in planned),
              'paid': float(sum((_money(r['amount']) for p in planned for _, _, r in p[3] if r['status'] == 'Pagado'), Decimal(0))),
              'pending': float(sum((_money(r['amount']) for p in planned for _, _, r in p[3] if r['status'] != 'Pagado'), Decimal(0)))}
    if dry_run:
        return dict(result, dry_run=True, message=f'{len(planned)} trabajos validados; sin cambios')
    for table in ('jobs', 'payments', 'quotes', 'payment_schedules'):
        crm.store.backup_now(table)
    now = datetime.now().isoformat()
    for entry, job, old_rows, new_rows in planned:
        jid = job['id']
        schedules = [s for s in crm.store.list('payment_schedules') if s.get('job_id') == jid and s.get('status') == 'active']
        quotes = [q for q in crm.list_quotes(tenant_id) if q.get('job_id') == jid]
        job.setdefault('studio_ninja_payment_reconciliation_before', {
            'job': deepcopy(job), 'payments': deepcopy(old_rows),
            'quotes': deepcopy(quotes), 'schedules': deepcopy(schedules)})
        available = sorted(old_rows, key=lambda p: (str(p.get('invoice_id') or ''), str(p.get('cuota') or ''), p['id']))
        assigned = []
        # Match unchanged installments first so native payment links survive.
        for invoice, n, row in new_rows:
            match = next((p for p in available if p.get('invoice_id') == invoice['invoice_no']
                          and p.get('due_date') == row['due_date'] and _money(p.get('amount') or 0) == _money(row['amount'])
                          and p.get('status') == row['status'] and (p.get('paid_date') or '') == (row.get('paid_date') or '')), None)
            if match:
                available.remove(match)
            assigned.append(match)
        ids_by_invoice = {}
        for (invoice, n, row), matched in zip(new_rows, assigned):
            previous = matched or (available.pop(0) if available else {})
            pid = previous.get('id') or f'pay-sn-reconcile-{entry["source_id"]}-{invoice["url"].rsplit("/", 1)[-1]}-{n}'
            paid = row['status'] == 'Pagado'
            crm.store.upsert('payments', dict(previous, id=pid, tenant_id=tenant_id, job_id=jid,
                client_id=job.get('client_id'), quote_id=None,
                invoice_id=invoice['invoice_no'], invoice_group_id='sn-invoice-' + invoice['url'].rsplit('/', 1)[-1],
                concepto=f'Factura Studio Ninja {invoice["invoice_no"]}', amount=row['amount'],
                original_amount=row['amount'], paid_amount=row['amount'] if paid else 0,
                status=row['status'], due_date=row['due_date'], paid_date=row.get('paid_date') or '',
                fecha_pago=row.get('paid_date') or '', cuota=n + 1,
                studio_ninja_url=invoice['url'], studio_ninja_reconciled_at=now))
            ids_by_invoice.setdefault(invoice['invoice_no'], []).append(pid)
        job.update(price_total=float(sum((_money(i['total']) for i in entry['invoices']), Decimal(0))),
                   studio_ninja_payment_invoices=deepcopy(entry['invoices']),
                   studio_ninja_payment_reconciled_at=now)
        writeoff = float(sum((_money(w['amount']) for i in entry['invoices'] for w in i.get('writeoffs', [])), Decimal(0)))
        job['studio_ninja_payment_writeoff'] = writeoff
        for quote in quotes:
            if quote.get('id') == job.get('accepted_quote_id'):
                quote.update(precio_total=job['price_total'], price_total=job['price_total'], total=job['price_total'])
                crm.store.upsert('quotes', quote)
        for schedule in schedules:
            schedule.update(total_plan=job['price_total'] - writeoff, suma_cuotas=job['price_total'] - writeoff,
                            cuotas=len(new_rows), payment_ids=[pid for ids in ids_by_invoice.values() for pid in ids])
            crm.store.upsert('payment_schedules', schedule)
        crm.upsert_job(job)
    result['message'] = f'{result["jobs"]} trabajos conciliados; {result["payments"]} cuotas fieles al origen'
    return result
