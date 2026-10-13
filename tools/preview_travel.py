"""Generate full email previews through the real queue, using only isolated synthetic data.

Run: .venv/bin/python tools/preview_travel.py
No messages or geographic queries can leave this process.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
from html import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    output = ROOT / 'artifacts' / 'viaticos' / 'previews'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='flow-travel-previews-') as isolated:
        for key, value in {
            'CRM_DATA_DIR': isolated, 'ENABLE_WORKFLOW_QUEUE': '0', 'ENABLE_REMINDER_SCHEDULER': '0',
            'OUTBOUND_EMAIL_ENABLED': '0', 'DISABLE_OUTBOUND_EMAIL': '1',
            'FLASK_SECRET': 'synthetic-preview-only-not-production',
        }.items():
            os.environ[key] = value
        import app as crm
        from src.email_delivery import render_email_html
        from src import mail_tracker, travel
        def deny_delivery(*args, **kwargs):
            raise AssertionError('No se permiten envíos en las vistas previas.')
        mail_tracker.send_email = deny_delivery
        crm.travel_service._geocode = lambda query: []
        assert crm.store.data_dir == isolated
        tenant = 'tenant-norkevin-photography'
        for row in crm._MULTI_TENANT_REAL_TENANTS:
            crm.store.upsert('tenants', dict(row))
        fixture = json.loads((ROOT / 'tests/fixtures/travel_email_templates.json').read_text())
        for year in ('2027', '2028'):
            crm.store.upsert('email_templates', dict(id='preview-packages-' + year, tenant_id=tenant,
                name='Paquetes de boda Norkevin' + (' 2028' if year == '2028' else ''), activo=True,
                asunto=fixture['subject'], cuerpo=fixture['body'].replace('PACKAGE_LINK', fixture['links'][year])))
        crm.store.upsert('email_templates', dict(id='preview-unavailable', tenant_id=tenant,
            name='Paquetes de boda Norkevin (FECHA NO DISPONIBLE)', activo=True,
            asunto=fixture['unavailable_subject'], cuerpo=fixture['unavailable_body']))
        workflow = crm.LEAD_WORKFLOW(tenant).to_dict()
        next(step for step in workflow['steps'] if step['id'] == 'envio_paquetes')['email_template_id'] = 'preview-packages-2027'
        crm.store.save_tenant_dict('workflow_templates', {workflow['id']: workflow}, tenant)
        crm.travel_service.save_settings(tenant, {'enabled': True}, actor='Vista previa aislada')
        cases = [
            ('san-cayetano-2027', 'Finca San Cayetano', '2027', False),
            ('atitlan-2028', 'Hotel Atitlán', '2028', False),
            ('casa-del-mundo-2027', 'La Casa del Mundo', '2027', False),
            ('irtra-2028', 'IRTRA Retalhuleu', '2028', False),
            ('antigua-2027', 'Antigua Guatemala', '2027', False),
            ('peten-2028', 'Flores, Petén', '2028', False),
            ('desconocido-2028', 'Venue sin verificar', '2028', False),
            ('internacional-2028', 'Finca San Cayetano, Argentina', '2028', False),
            ('fecha-ocupada-2028', 'IRTRA Retalhuleu', '2028', True),
            ('listado-por-verificar-2028', 'Casa Tecpán La Colina', '2028', False),
        ]
        results = []
        token = crm._workflow_tenant.set(tenant)
        try:
            for index, (slug, place, year, occupied) in enumerate(cases, 1):
                # Independent synthetic scenarios; geographic calls are mocked above.
                travel._GEOCODE_LAST_REQUEST = 0.0
                day = f'{year}-07-{index:02d}'
                if occupied:
                    crm.store.upsert('jobs', dict(id='preview-booked', tenant_id=tenant,
                        nombre='Boda sintética reservada', boda_date=day, status='Confirmado'))
                lead = dict(id='preview-' + slug, tenant_id=tenant, nombre='Ana y Luis (prueba)',
                    email='ana-luis@example.invalid', locacion=place, fecha_tentativa=day,
                    created='2026-10-13', status='Nuevo')
                crm.store.upsert('leads', lead)
                crm.trigger_workflow_for_lead(lead['id'], lead['nombre'], tenant)
                result = crm._complete_lead_workflow_step(lead, 'envio_paquetes')
                mail = crm.store.get('pending_emails', result.get('mail_id')) if result.get('mail_id') else None
                if not mail:
                    mail = next(m for m in crm.store.list('pending_emails') if m.get('lead_id') == lead['id'])
                assert mail['status'] == ('blocked' if slug in ('desconocido-2028', 'internacional-2028', 'listado-por-verificar-2028') else 'pending'), mail
                assert (crm._validate_travel_email(mail) is not None) == (mail['status'] == 'blocked')
                (output / (slug + '.html')).write_text(render_email_html(mail['subject'], mail['body']))
                (output / (slug + '.txt')).write_text(mail['subject'] + '\n\n' + mail['body'])
                results.append(dict(file=slug + '.html', place=place, year=year, status=mail['status'],
                    reason=mail.get('blocked_reason'), level=(mail.get('travel_snapshot') or {}).get('level')))
            assert not crm.store.list('mail_log'), 'Una vista previa intentó enviar un correo.'
        finally:
            crm._workflow_tenant.reset(token)
        (output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
        cards = ''.join('<section><h2>' + escape(row['place']) + ' · ' + row['year'] + '</h2><p>' +
            ('Bloqueado para revisión: ' + escape(row['reason'] or '') if row['status'] == 'blocked' else 'Borrador pendiente de aprobación') +
            '</p><a href="' + row['file'] + '">Abrir correo completo</a><iframe title="' + escape(row['place']) +
            '" src="' + row['file'] + '"></iframe></section>' for row in results)
        (output / 'index.html').write_text('<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Viáticos · Correos de prueba</title><style>body{font:16px/1.6 system-ui;background:#f7f9fc;color:#172b4d;max-width:1000px;margin:auto;padding:24px}section{margin:40px 0}iframe{display:block;width:100%;height:1050px;border:1px solid #e2e8f0;border-radius:12px;margin-top:16px}a{color:#2563eb}</style>'
            '<h1>Correos completos de prueba</h1><p>Plantillas reales verificadas el 13 de octubre de 2026. Datos ficticios. Ningún correo enviado. Enlaces de paquetes conservados según el año.</p>' + cards + '</html>')
        print(json.dumps({'previews': len(results), 'sent': 0, 'output': str(output / 'index.html')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
