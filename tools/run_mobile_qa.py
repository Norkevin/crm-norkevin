"""Loopback-only HTTPS fixture for Simulator QA; never load operational data.

The private control file contains a synthetic owner's session cookie, not a
production credential. No test login route or bypass is added to the CRM.
"""
import json
import os
import secrets
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def main():
    from cryptography import x509
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import ipaddress

    (ROOT / '.local').mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='iphone-qa-', dir=ROOT / '.local'))
    data_dir = folder / 'data'
    data_dir.mkdir()
    os.environ.update(CRM_DATA_DIR=str(data_dir), FLASK_SECRET=secrets.token_urlsafe(48),
                      FLOW_MOBILE_ENABLED='1', FLOW_MOBILE_PUSH_ENABLED='0',
                      FLOW_MOBILE_ENCRYPTION_KEY=Fernet.generate_key().decode(),
                      FLOW_TEAMS_ENABLED='0', ENABLE_WORKFLOW_QUEUE='0', ENABLE_REMINDER_SCHEDULER='0',
                      OUTBOUND_EMAIL_ENABLED='0', DISABLE_OUTBOUND_EMAIL='1',
                      GOOGLE_CLIENT_ID='', GOOGLE_CLIENT_SECRET='', RECURRENTE_SECRET_KEY='',
                      RECURRENTE_SECRET_KEY_TEST='', ADMIN_ONE_TIME_TOKEN='')
    sys.path.insert(0, str(ROOT))
    import app as crm
    assert Path(crm.store.data_dir).resolve() == data_dir.resolve(), 'Refuse to touch non-QA data'
    crm.app.config.update(SESSION_COOKIE_SECURE=True)
    today = datetime.now(ZoneInfo('America/Guatemala')).date()
    owner = 'qa-owner@example.invalid'
    tenant = 'qa-norkevin'
    crm.store.upsert('tenants', dict(id=tenant, name='Norkevin · Demostración', login_email=owner, active=True))
    crm.store.upsert('tenants', dict(id='qa-astral', name='Astral · Demostración', login_email='qa-astral@example.invalid', active=True))
    samples = [('Andrea y Diego', 7, 'Antigua Guatemala'), ('María y Daniel', 1, 'Ciudad de Guatemala'),
               ('Camila y Mateo', 28, 'Lago de Atitlán'), ('Lucía y Gabriel', 42, 'Antigua Guatemala')]
    for i, (name, offset, location) in enumerate(samples):
        cid, jid = f'qa-client-{i}', f'qa-job-{i}'
        crm.store.upsert('clients', dict(id=cid, tenant_id=tenant, first_name=name,
                                       last_name='', email=f'cliente-{i}@example.invalid', phone='', location=location))
        crm.store.upsert('jobs', dict(id=jid, tenant_id=tenant, client_id=cid, nombre=name,
                                    status='Confirmado', boda_date=(today + timedelta(days=offset)).isoformat(),
                                    location=location, tipo='boda'))
        crm.store.upsert('payments', dict(id=f'qa-payment-{i}', tenant_id=tenant, client_id=cid, job_id=jid,
                                        status='Pagado' if i < 2 else 'Pendiente', amount=3500 + i * 1500,
                                        paid_date=today.isoformat() if i < 2 else '', due_date=(today + timedelta(days=offset)).isoformat()))
    for i, (name, status, source) in enumerate([('Sofía y Luis', 'Nuevo', 'Instagram'),
            ('Valentina y Pablo', 'Contactado', 'Web'), ('Isabel y Andrés', 'Nuevo', 'Recomendación')]):
        crm.store.upsert('leads', dict(id=f'qa-lead-{i}', tenant_id=tenant, nombre=name, status=status,
                                     fuente=source, created=today.isoformat(), created_time='10:00',
                                     fecha_tentativa=(today + timedelta(days=90 + i * 15)).isoformat(),
                                     email=f'lead-{i}@example.invalid'))
    crm.store.upsert('leads', dict(id='qa-foreign', tenant_id='qa-astral', nombre='Registro exclusivo de Astral', status='Nuevo'))
    crm.store.upsert('calendar', dict(id='qa-meeting', tenant_id=tenant, title='Reunión de planificación',
                                    date=today.isoformat(), type='meeting', status='Programada'))
    from src.mobile import journal, source_events
    mobile = journal(crm.app, crm.store)
    mobile.observe(tenant, owner, [])
    mobile.observe(tenant, owner, source_events(crm.store, tenant))

    # A private test cookie exercises the real CSRF/approval route over HTTPS.
    cookie = crm.app.session_interface.get_signing_serializer(crm.app).dumps(
        dict(logged_in=True, tenant_id=tenant, user_email=owner, user_name='Usuario de demostración'))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Flowing Simulator QA')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=2))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),
                x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = folder / 'localhost.pem', folder / 'localhost-key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    key_path.chmod(0o600)
    control = folder / 'control.json'
    control.write_text(json.dumps(dict(url='https://localhost:5443', cookie=cookie,
                                      certificate=str(cert_path), tenant=tenant)))
    control.chmod(0o600)
    print(f'QA_CONTROL={control}', flush=True)
    print('Synthetic HTTPS fixture: https://localhost:5443; push and email disabled', flush=True)
    crm.app.run(host='127.0.0.1', port=5443, debug=False, use_reloader=False,
                ssl_context=(str(cert_path), str(key_path)))


if __name__ == '__main__':
    main()
