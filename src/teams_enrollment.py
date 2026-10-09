"""Shareable, write-only team registration; private payment data stays in the directory."""
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta
from uuid import uuid4

from flask import Blueprint, abort, jsonify, redirect, render_template, request, session, url_for
from itsdangerous import BadData, URLSafeSerializer

from src.teams import TeamsError, now, text
from src.teams_directory import DIRECTORY

FIELDS = {
    'first_name': ('Nombre', 75, True), 'last_name': ('Apellido', 74, True),
    'email': ('Correo', 200, True), 'phone': ('Teléfono', 80, True),
    'skills': ('Habilidades', 1000, True), 'instagram': ('Instagram', 300, False),
    'bank': ('Banco', 150, False), 'account_type': ('Tipo de cuenta', 150, False),
    'account_number': ('Número de cuenta', 150, False), 'account_holder': ('Titular de la cuenta', 150, False),
}
BANK_FIELDS = ('bank', 'account_type', 'account_number', 'account_holder')


def register_enrollment(app, owner, store, crm_store):
    public = Blueprint('teams_enrollment', __name__)
    signer = URLSafeSerializer(app.secret_key, salt='teams-enrollment')

    @owner.route('/api/teams/enrollment-link', methods=['POST'])
    def enrollment_link():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict) or type(data.get('renew', False)) is not bool:
            raise TeamsError('Solicitud inválida.')
        tenant = session['tenant_id']
        with store.transaction() as db:
            links = store.records(db, tenant, 'enrollment_link')
            link = next((r for r in links if r['active']), None)
            if data.get('renew') or not link:
                for old in links:
                    if old['active']:
                        old['active'] = False
                        store.save(db, tenant, 'enrollment_link', old)
                link = store.create(db, tenant, 'enrollment_link', active=True, created_at=now())
        token = signer.dumps(dict(tenant=tenant, id=link['id']))
        return jsonify(ok=True, url=url_for('teams_enrollment.form', token=token, _external=True))

    @public.before_request
    def enabled():
        local = app.config.get('FLOW_TEAMS_LOCAL')
        if not (local or app.config.get('FLOW_TEAMS_ENABLED')) or (local and request.remote_addr not in ('127.0.0.1', '::1')):
            abort(404)
        if request.content_length and request.content_length > 16384:
            abort(413)

    @public.after_request
    def private(response):
        response.headers.update({'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
                                 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
                                 'Content-Security-Policy': "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
        return response

    @public.route('/teams/join/<token>', methods=['GET', 'POST'])
    def form(token):
        try:
            identity = signer.loads(token)
            tenant, link_id = identity['tenant'], identity['id']
            with store.transaction() as db:
                link = store.get(db, tenant, 'enrollment_link', link_id)
            brand = crm_store.get('tenants', tenant)
            if not link['active'] or not brand or brand.get('active') is False:
                abort(404)
        except (BadData, KeyError, TypeError, TeamsError):
            abort(404)
        session.setdefault('teams_enrollment_csrf', secrets.token_urlsafe(32))
        values, error, status = {}, '', 200
        if request.method == 'POST':
            if not secrets.compare_digest(request.form.get('csrf', ''), session['teams_enrollment_csrf']):
                abort(403)
            try:
                if request.form.get('website'):
                    raise TeamsError('No se pudo registrar. Recarga el formulario e intenta nuevamente.')
                submission = text(request.form, 'submission', maximum=100)
                for key, (label, maximum, required) in FIELDS.items():
                    try:
                        values[key] = text(request.form, key, maximum=maximum, required=required)
                    except TeamsError:
                        raise TeamsError('Revisa el campo ' + label.lower() + '.')
                if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', values['email']):
                    raise TeamsError('Escribe un correo válido.')
                if not any(c.isdigit() for c in values['phone']):
                    raise TeamsError('Escribe un número de teléfono válido.')
                if any(values[k] for k in BANK_FIELDS) and not all(values[k] for k in BANK_FIELDS):
                    raise TeamsError('Para agregar una cuenta, completa banco, tipo, número y titular.')
                if values['account_type'] not in ('', 'Monetaria', 'Ahorro', 'Otra'):
                    raise TeamsError('Selecciona un tipo de cuenta válido.')
                if request.form.get('consent') != 'yes':
                    raise TeamsError('Confirma que podemos guardar tus datos para coordinar el trabajo y los pagos.')
                receipt_id = hashlib.sha256((link_id + ':' + submission).encode()).hexdigest()
                fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
                with store.transaction() as db:
                    # Recheck inside the write transaction so rotation cannot race a submission.
                    if not store.get(db, tenant, 'enrollment_link', link_id)['active']:
                        abort(404)
                    receipts = store.records(db, tenant, 'enrollment_receipt')
                    previous = next((r for r in receipts if r['id'] == receipt_id), None)
                    if previous:
                        if previous['fingerprint'] != fingerprint:
                            raise TeamsError('Este formulario ya se envió. Recarga para comenzar uno nuevo.', 409)
                    else:
                        stamp = now()
                        attempt_id = hashlib.sha256((link_id + ':' + (request.remote_addr or 'unknown')).encode()).hexdigest()
                        attempts = store.records(db, tenant, 'enrollment_attempt')
                        attempt = next((r for r in attempts if r['id'] == attempt_id), dict(id=attempt_id, count=0, until=stamp))
                        if attempt['until'] <= stamp:
                            attempt.update(count=0, until=(datetime.fromisoformat(stamp) + timedelta(hours=1)).isoformat())
                        if attempt['count'] >= 20:
                            raise TeamsError('Se alcanzó el límite de registros. Intenta en una hora.', 429)
                        if any(m.get('email', '').casefold() == values['email'].casefold() for m in store.records(db, tenant, 'member')):
                            raise TeamsError('Este correo ya está registrado. Contacta al responsable para actualizar tus datos.', 409)
                        person = dict(id=str(uuid4()), name=values['first_name'] + ' ' + values['last_name'],
                                      email=values['email'], phone=values['phone'],
                                      skills=list(dict.fromkeys(s.strip() for s in values['skills'].split(',') if s.strip())),
                                      instagram=values['instagram'], role='Por definir', rate=0, rate_missing=True,
                                      active=True, source='enrollment', created_at=stamp)
                        if not person['skills']:
                            raise TeamsError('Escribe al menos una habilidad, por ejemplo fotografía o video.')
                        if any(values[k] for k in BANK_FIELDS):
                            profile = store.create(db, DIRECTORY, 'person_private', private_profile=True,
                                                   updated_at=stamp, **{k: values[k] for k in BANK_FIELDS})
                            person['directory_id'] = profile['id']
                        store.save(db, tenant, 'member', person)
                        store.save(db, tenant, 'enrollment_receipt', dict(id=receipt_id, fingerprint=fingerprint, member_id=person['id']))
                        attempt['count'] += 1
                        store.save(db, tenant, 'enrollment_attempt', attempt)
                        store.create(db, tenant, 'audit', action='member_enrollment', actor='Formulario de registro',
                                     created_at=stamp, before=None, after=dict(id=person['id']))
                return redirect(url_for('teams_enrollment.form', token=token, submitted='1'), code=303)
            except TeamsError as exc:
                error, status = exc.message, exc.status
        return render_template('teams_enrollment.html', brand=brand, error=error, values=values,
                               csrf=session['teams_enrollment_csrf'], submission=str(uuid4()),
                               submitted=request.method == 'GET' and request.args.get('submitted') == '1'), status

    app.register_blueprint(public)
