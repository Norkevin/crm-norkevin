"""Kevin: 'llenemos el CRM con toda esta info... tal cual esta' -- exporto su
CRM anterior (Studio Ninja) a un ZIP con 17 jobs reales (clientes, cotizaciones,
facturas con su historial de pagos, contratos). Lei cada factura y contrato a
mano (no un parser automatico, para no arriesgar montos financieros reales) y
armo un JSON con esa forma que Kevin sube el mismo desde Settings.

Ese JSON real (con nombres/correos/telefonos/montos de clientes reales) NUNCA
se guarda en este repo -- es publico en GitHub. El endpoint lo recibe en el
body del POST (payload), no lo lee de un archivo del repo. Estos tests usan
datos sinteticos con la MISMA forma, para probar la logica de import sin tocar
datos reales de nadie."""

SAMPLE_PAYLOAD = {
    "jobs": [
        {
            "slug": "test-fully-paid",
            "job_name": "Boda Test Fully Paid",
            "client": {"first_name": "Ana", "last_name": "Prueba", "email": "ana.prueba@example.com", "phone": "5555-1111"},
            "lead_source": "Instagram",
            "created": "2025-06-10",
            "boda_date": "2025-12-06",
            "location": "Antigua Guatemala, Guatemala",
            "quotes": [
                {
                    "invoice_no": "20250610",
                    "package_name": "Paquete Test Gold",
                    "incluye": ["2 fotografos", "12 horas de cobertura"],
                    "subtotal": 20000.00,
                    "discount": 0,
                    "total": 20000.00,
                    "cuotas": [
                        {"due_date": "2025-06-10", "amount": 10000.00, "status": "Pagado", "paid_date": "2025-06-10"},
                        {"due_date": "2025-12-06", "amount": 10000.00, "status": "Pagado", "paid_date": "2025-11-27"}
                    ]
                }
            ]
        },
        {
            "slug": "test-pending-with-discount",
            "job_name": "Boda Test Pending Discount",
            "client": {"first_name": "Beto", "last_name": "Prueba", "email": "beto.prueba@example.com", "phone": "5555-2222"},
            "lead_source": "Instagram",
            "created": "2026-06-11",
            "boda_date": "2027-04-17",
            "location": "Ciudad de Guatemala, Guatemala",
            "quotes": [
                {
                    "invoice_no": "20260611",
                    "package_name": "Paquete Test Personalizado",
                    "incluye": ["2 fotografos profesionales"],
                    "subtotal": 29000.00,
                    "discount": 3999.10,
                    "total": 25000.90,
                    "cuotas": [
                        {"due_date": "2026-06-12", "amount": 5000.00, "status": "Pendiente"},
                        {"due_date": "2027-05-17", "amount": 20000.90, "status": "Pendiente"}
                    ]
                }
            ],
            "contract": {"signed": False, "photographer_signed": True, "signed_date": "2026-06-12"}
        },
        {
            "slug": "test-two-invoices",
            "job_name": "Boda Test Dos Facturas",
            "client": {"first_name": "Cari", "last_name": "Prueba", "email": "cari.prueba@example.com", "phone": "5555-3333"},
            "lead_source": "Wedding Planner",
            "created": "2025-07-15",
            "boda_date": "2026-11-28",
            "location": "San Cayetano, Guatemala",
            "quotes": [
                {
                    "invoice_no": "20250715",
                    "package_name": "Paquete Principal",
                    "incluye": ["Cobertura completa"],
                    "subtotal": 25125.00,
                    "discount": 0,
                    "total": 25125.00,
                    "cuotas": [
                        {"due_date": "2025-07-22", "amount": 25125.00, "status": "Pagado", "paid_date": "2025-07-23"}
                    ]
                },
                {
                    "invoice_no": "20260625",
                    "package_name": "3 Horas extra",
                    "incluye": ["3 horas extra"],
                    "subtotal": 1500.00,
                    "discount": 0,
                    "total": 1500.00,
                    "cuotas": [
                        {"due_date": "2026-06-25", "amount": 1500.00, "status": "Pagado", "paid_date": "2026-06-25"}
                    ]
                }
            ],
            "contract": {"signed": True, "photographer_signed": True, "signed_date": "2025-07-22"}
        }
    ]
}


def test_import_requires_confirm_keyword(auth_client):
    resp = auth_client.post('/api/admin/import-studio-ninja', json={'payload': SAMPLE_PAYLOAD})
    assert resp.status_code == 400
    assert resp.get_json()['ok'] is False


def test_import_requires_a_payload(auth_client):
    resp = auth_client.post('/api/admin/import-studio-ninja', json={'confirm': 'IMPORTAR'})
    assert resp.status_code == 400
    assert resp.get_json()['ok'] is False


def test_import_never_reads_a_file_from_the_repo(auth_client):
    """El repo es publico -- el endpoint no debe leer ningun archivo local con
    datos de clientes, solo lo que venga en el body del POST."""
    from pathlib import Path
    source = Path('app.py').read_text(encoding='utf-8')
    assert "data/seeds/studio_ninja" not in source
    assert "'seeds', 'studio_ninja_import.json'" not in source
    assert not Path('data/seeds/studio_ninja_import.json').exists(), (
        'nunca debe existir un archivo con datos reales de clientes en el repo'
    )


def test_import_creates_jobs_from_payload(auth_client):
    import app as app_module
    resp = auth_client.post('/api/admin/import-studio-ninja', json={'confirm': 'IMPORTAR', 'payload': SAMPLE_PAYLOAD})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert len(data['created']) == 3
    assert data['skipped'] == []

    jobs = [j for j in app_module.store.list('jobs') if j['id'].startswith('boda-sn-test-')]
    assert len(jobs) == 3
    leads = [l for l in app_module.store.list('leads') if l['id'].startswith('lead-sn-test-')]
    assert all(l['status'] == 'Convertido' for l in leads)


def test_import_is_idempotent_on_second_run(auth_client):
    import app as app_module
    auth_client.post('/api/admin/import-studio-ninja', json={'confirm': 'IMPORTAR', 'payload': SAMPLE_PAYLOAD})
    before = len([j for j in app_module.store.list('jobs') if j['id'].startswith('boda-sn-test-')])

    resp = auth_client.post('/api/admin/import-studio-ninja', json={'confirm': 'IMPORTAR', 'payload': SAMPLE_PAYLOAD})
    data = resp.get_json()
    assert data['created'] == []
    assert len(data['skipped']) == 3

    after = len([j for j in app_module.store.list('jobs') if j['id'].startswith('boda-sn-test-')])
    assert after == before, 'correr el import dos veces no debe duplicar jobs'


def test_import_does_not_trigger_workflow_engine_or_send_mail(auth_client):
    """No debe mandarle correos automaticos a clientes reales por historial viejo --
    la importacion escribe directo al store, sin pasar por el flujo normal de
    conversion de lead a job que dispara el workflow engine."""
    import app as app_module
    before_mail = len(app_module.store.list('mail_log')) + len(app_module.store.list('mail_outbox'))
    before_instances = len(app_module.workflow_engine.instances)

    auth_client.post('/api/admin/import-studio-ninja', json={'confirm': 'IMPORTAR', 'payload': SAMPLE_PAYLOAD})

    after_mail = len(app_module.store.list('mail_log')) + len(app_module.store.list('mail_outbox'))
    after_instances = len(app_module.workflow_engine.instances)
    assert after_mail == before_mail
    assert after_instances == before_instances


def test_fully_paid_job_matches_the_source_invoice_totals(auth_client):
    import app as app_module
    auth_client.post('/api/admin/import-studio-ninja', json={'confirm': 'IMPORTAR', 'payload': SAMPLE_PAYLOAD})

    job = app_module.get_job('boda-sn-test-fully-paid')
    assert job is not None
    assert job['price_total'] == 20000.00

    client = app_module.store.get('clients', 'client-sn-test-fully-paid')
    assert client['email'] == 'ana.prueba@example.com'

    payments = [p for p in app_module.store.list('payments') if p.get('job_id') == 'boda-sn-test-fully-paid']
    assert len(payments) == 2
    assert all(p['status'] == 'Pagado' for p in payments)
    assert sum(p['paid_amount'] for p in payments) == 20000.00
    assert sum(p['original_amount'] for p in payments) == 20000.00


def test_pending_job_with_discount_and_unsigned_contract(auth_client):
    import app as app_module
    auth_client.post('/api/admin/import-studio-ninja', json={'confirm': 'IMPORTAR', 'payload': SAMPLE_PAYLOAD})

    job = app_module.get_job('boda-sn-test-pending-with-discount')
    assert round(job['price_total'], 2) == 25000.90

    payments = [p for p in app_module.store.list('payments') if p.get('job_id') == 'boda-sn-test-pending-with-discount']
    assert len(payments) == 2
    assert all(p['status'] == 'Pendiente' for p in payments)
    assert round(sum(p['amount'] for p in payments), 2) == 25000.90

    contract = app_module.store.get('contracts', 'contract-sn-test-pending-with-discount')
    assert contract['photographer_signed'] is True
    assert contract['signed'] is False


def test_job_with_two_invoices_creates_two_quotes_and_a_signed_contract(auth_client):
    """Un job real (BODA CON GERALDINE) tuvo 2 facturas separadas -- deben
    quedar como 2 cotizaciones distintas del mismo job, no mezcladas ni perdidas."""
    import app as app_module
    auth_client.post('/api/admin/import-studio-ninja', json={'confirm': 'IMPORTAR', 'payload': SAMPLE_PAYLOAD})

    quotes = [q for q in app_module.store.list('quotes') if q.get('job_id') == 'boda-sn-test-two-invoices']
    assert len(quotes) == 2
    totals = sorted(q['precio_total'] for q in quotes)
    assert totals == [1500.00, 25125.00]

    payments = [p for p in app_module.store.list('payments') if p.get('job_id') == 'boda-sn-test-two-invoices']
    assert len(payments) == 2

    contract = app_module.store.get('contracts', 'contract-sn-test-two-invoices')
    assert contract['signed'] is True
    assert contract['photographer_signed'] is True


def test_imported_job_renders_correctly_on_the_job_page(auth_client):
    import app as app_module
    auth_client.post('/api/admin/import-studio-ninja', json={'confirm': 'IMPORTAR', 'payload': SAMPLE_PAYLOAD})

    job = app_module.get_job('boda-sn-test-fully-paid')
    resp = auth_client.get(f"/jobs/{job['id']}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Ana Prueba' in html
    assert 'Q20,000.00' in html
