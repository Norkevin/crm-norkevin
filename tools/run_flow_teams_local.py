"""Run the existing CRM with a persistent, synthetic local Teams workspace."""
import json
import os
from pathlib import Path
import secrets
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local' / 'flow-teams'
CRM = LOCAL / 'crm'


def prepare():
    # Never follow a local data symlink into an operational CRM directory.
    if any(p.is_symlink() for p in (ROOT / '.local', LOCAL, CRM)):
        raise RuntimeError('El directorio de la propuesta no puede ser un enlace simbólico.')
    CRM.mkdir(parents=True, exist_ok=True)
    marker = LOCAL / 'synthetic-only.txt'
    if not marker.exists() and any(CRM.iterdir()):
        raise RuntimeError('Directorio sin identificación de datos sintéticos. No se inicia la propuesta.')
    marker.write_text('Propuesta local. Datos sintéticos; sin proveedores ni producción.\n')
    secret = LOCAL / 'session-secret'
    if not secret.exists():
        secret.write_text(secrets.token_urlsafe(48))
        secret.chmod(0o600)
    os.environ.update(CRM_DATA_DIR=str(CRM), FLASK_SECRET=secret.read_text().strip(),
                      DEV_LOGIN='1', ENABLE_WORKFLOW_QUEUE='0', ENABLE_REMINDER_SCHEDULER='0',
                      OUTBOUND_EMAIL_ENABLED='0', DISABLE_OUTBOUND_EMAIL='1',
                      ADMIN_ONE_TIME_TOKEN='', NOTION_TOKEN='', RECURRENTE_SECRET_KEY='',
                      RECURRENTE_SECRET_KEY_TEST='')
    sys.path.insert(0, str(ROOT))
    from src.tenant_brand_map import all_resolved_brands
    brands = [b for b in all_resolved_brands() if b.brand_key in ('astral', 'norkevin')]
    tenants, clients, jobs, payments = [], [], [], []
    for b in brands:
        tenants.append(dict(id=b.internal_tenant_id, name=b.display_name, login_email=b.sender_email,
                            slug='astral-weddings' if b.brand_key == 'astral' else 'norkevin-photography',
                            active=True, currency='GTQ', color='#2F7D73', logo_letter=b.display_name[0]))
        for i, (name, event_day, total) in enumerate((('Rosanela López · ejemplo', '2026-11-14', 20000),
                                                    ('Andrea y Pablo · ejemplo', '2026-11-28', 18000),
                                                    ('Sofía y Daniel · sin costos', '2026-12-12', 15000))):
            job_id = f'local-{b.brand_key}-{i + 1}'
            client_id = f'local-client-{b.brand_key}-{i + 1}'
            clients.append(dict(id=client_id, tenant_id=b.internal_tenant_id, first_name=name,
                                last_name='', email=f'pareja-{i + 1}@example.invalid', phone=''))
            jobs.append(dict(id=job_id, tenant_id=b.internal_tenant_id, nombre=name, client_id=client_id,
                             boda_date=event_day, location='Antigua Guatemala · lugar de prueba',
                             status='En curso', type='Boda', price_total=total, currency='GTQ',
                             created_at='2026-10-05T09:00:00', local_synthetic=True))
            payments.extend([
                dict(id=f'{job_id}-paid', job_id=job_id, tenant_id=b.internal_tenant_id,
                     invoice_id=f'{job_id}-invoice', amount=total // 2, original_amount=total // 2,
                     paid_amount=total // 2, status='Pagado', paid_date='2026-10-05', due_date='2026-10-05'),
                dict(id=f'{job_id}-due', job_id=job_id, tenant_id=b.internal_tenant_id,
                     invoice_id=f'{job_id}-invoice', amount=total // 2, original_amount=total // 2,
                     status='Pendiente', due_date=event_day)])
    for name, records in (('tenants', tenants), ('clients', clients), ('jobs', jobs), ('payments', payments)):
        path = CRM / f'{name}.json'
        if not path.exists():
            path.write_text(json.dumps(records, ensure_ascii=False, indent=2))
    return brands


def main():
    brands = prepare()
    # Defense in depth: CRM routes cannot contact any external provider in this process.
    def blocked(*args, **kwargs):
        raise OSError('Conexiones salientes bloqueadas en la propuesta local de Flow Teams.')
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    import app as crm
    from src.teams import register_teams
    crm.app.config['FLOW_TEAMS_LOCAL'] = True
    register_teams(crm.app, crm.store, crm._canonical_jobs, crm._job_payment_summary)
    database = crm.app.extensions['teams']
    for brand in brands:
        with crm.app.test_request_context('/'):
            crm.session.update(logged_in=True, tenant_id=brand.internal_tenant_id, user_email=brand.sender_email)
            def job_reader(identifier):
                job = crm.store.get('jobs', identifier)
                if not job:
                    raise RuntimeError('Job sintético no disponible.')
                return job
            def command(key, **fields):
                return database.command(brand.internal_tenant_id, 'Inicialización sintética',
                                        dict(key='seed-' + key, **fields), job_reader)['record']
            member_ids = []
            for i, (name, role, amount) in enumerate((('Valeria Torres', 'Fotógrafa principal', '1500'),
                                                     ('Diego Méndez', 'Segundo fotógrafo', '1500'),
                                                     ('Camila Reyes', 'Videógrafa', '2000'))):
                member = command(f'member-{i}', action='member', name=name,
                                 email=f'equipo-{i}@example.invalid', role=role, rate=amount)
                member_ids.append(member['id'])
                command(f'assignment-{i}', action='assignment', job_id=f'local-{brand.brand_key}-1',
                        member_id=member['id'], role=role, slot=f'Cobertura {i + 1}',
                        start='2026-11-14T13:00', end='2026-11-14T22:00', buffer=30,
                        amount=amount, due_date='2026-11-15', instructions='Cobertura ilustrativa; no se envía al miembro.')
            for category, amount in (('Transporte', '600'), ('Alimentación', '300'), ('Otros', '100')):
                command('cost-' + category, action='cost', job_id=f'local-{brand.brand_key}-1',
                        category=category, description=category + ' · ejemplo', beneficiary_name='Proveedor de prueba',
                        amount=amount)
    print('Flow Teams local: http://127.0.0.1:5052/dev/login?next=/teams', flush=True)
    crm.app.run(host='127.0.0.1', port=5052, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
