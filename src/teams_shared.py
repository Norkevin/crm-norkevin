"""Explicit Teams-only bridge for Kevin's two canonical brands."""
from datetime import timedelta
import hashlib
import json

from src.tenant_brand_map import all_resolved_brands


def shared_brands(crm_store):
    # Never infer brand identity from the legacy tenant IDs.
    brands = {}
    for brand in all_resolved_brands():
        if brand.brand_key not in ('norkevin', 'astral'):
            continue
        tenant = crm_store.get('tenants', brand.internal_tenant_id)
        if (tenant and tenant.get('active', True)
                and tenant.get('login_email', '').casefold() == brand.sender_email.casefold()):
            brands[brand.internal_tenant_id] = brand
    return brands if len(brands) == 2 else {}


def peers(store, db, tenant, member, *, portal=False):
    crm_store = getattr(store, 'crm_store', None)
    brands = shared_brands(crm_store) if crm_store else {}
    email = member.get('email', '').strip().casefold()
    if tenant not in brands or (portal and not email) or not member.get('active'):
        return []
    result = []
    for other, brand in brands.items():
        if other == tenant:
            continue
        matches = [m for m in store.records(db, other, 'member')
                   if (email and m.get('email', '').strip().casefold() == email)
                   or (not portal and ((m.get('copied_from_tenant'), m.get('copied_from_member')) == (tenant, member['id'])
                       or (member.get('copied_from_tenant'), member.get('copied_from_member')) == (other, m['id'])))]
        if len(matches) == 1 and matches[0].get('active'):
            person = matches[0]
            if not portal or not person.get('shared_portal_blocked'):
                result.append((other, brand, person))
    return result


def cross_conflicts(store, db, tenant, member_id, start, end):
    from src.teams import assignment_trip, availability_window
    from src.linked_coverages import linked_coverages
    member = store.get(db, tenant, 'member', member_id)
    result = []
    for other, brand, person in peers(store, db, tenant, member):
        # Only this explicitly paired brand is read. No client/financial fields leave this function.
        jobs = store.crm_store.list_privileged('jobs', tenant_id=other,
                    reason='Teams: comprobar disponibilidad del mismo colaborador entre marcas vinculadas')
        calendar = store.crm_store.list_privileged('calendar', tenant_id=other,
                    reason='Teams: comprobar coberturas secundarias del mismo colaborador')
        jobs = {j['id']: j for j in jobs + linked_coverages(jobs, calendar)}
        plans = store.records(db, other, 'travel')
        for a in store.records(db, other, 'assignment'):
            if a['member_id'] != person['id'] or a['status'] in ('cancelada', 'rechazada'):
                continue
            job = jobs.get(a['job_id'])
            if not job or job.get('status') in ('Cancelado', 'Archivado') or job.get('boda_date') != a['job_day']:
                continue
            other_start, other_end = availability_window(a, assignment_trip(a, plans))
            margin = timedelta(minutes=a['buffer'])
            if start < other_end + margin and other_start - margin < end:
                result.append({k: a[k] for k in ('id', 'status', 'start', 'end')})
                result[-1]['brand_name'] = brand.display_name
    return result


def register_shared(owner_blueprint, store, crm_store):
    from flask import jsonify, request, session
    from src.teams import TeamsError, now, text

    @owner_blueprint.app_context_processor
    def shared_copy_target():
        brands = shared_brands(crm_store)
        tenant = session.get('tenant_id')
        other = next((b for t, b in brands.items() if t != tenant), None) if tenant in brands else None
        return dict(teams_copy_target=other.display_name if other else None)

    @owner_blueprint.route('/api/teams/members/copy', methods=['POST'])
    def copy_members():
        tenant = session['tenant_id']
        brands = shared_brands(crm_store)
        if tenant not in brands:
            raise TeamsError('Esta cuenta no tiene una marca vinculada para Teams.', 403)
        target = next(t for t in brands if t != tenant)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise TeamsError('Selecciona la copia de miembros.')
        key = 'copy-members:' + text(data, 'key', maximum=100)
        fingerprint = hashlib.sha256((tenant + ':' + target).encode()).hexdigest()
        with store.transaction() as db:
            previous = db.execute('SELECT result FROM commands WHERE tenant=? AND key=?', (tenant, key)).fetchone()
            if previous:
                return jsonify(json.loads(previous['result']))
            existing = store.records(db, target, 'member')
            created, skipped = 0, 0
            for m in store.records(db, tenant, 'member'):
                email = m.get('email', '').strip().casefold()
                phone = ''.join(c for c in m.get('phone', '') if c.isdigit())
                duplicate = any((p.get('copied_from_tenant'), p.get('copied_from_member')) == (tenant, m['id'])
                    or (email and email == p.get('email', '').strip().casefold())
                    or (phone and m['name'].strip().casefold() == p['name'].strip().casefold()
                        and phone == ''.join(c for c in p.get('phone', '') if c.isdigit())) for p in existing)
                if duplicate:
                    skipped += 1
                    continue
                # Deliberate directory-only copy: never bank data, access tokens, assignments or balances.
                fields = {k: m[k] for k in ('name', 'email', 'phone', 'skills', 'instagram', 'role', 'rate', 'active', 'rate_missing') if k in m}
                fields['rate_missing'] = m.get('rate_missing', bool(m.get('source') and not m['rate']))
                fields.update(copied_from_tenant=tenant, copied_from_member=m['id'])
                person = store.create(db, target, 'member', **fields)
                existing.append(person)
                store.create(db, target, 'audit', action='member_copy', actor=session['user_email'], created_at=now(),
                             before=None, after=dict(person), source_brand=brands[tenant].display_name)
                created += 1
            result = dict(ok=True, record=dict(created=created, skipped=skipped),
                warnings=[f"{created} miembros añadidos a {brands[target].display_name}. {skipped} fichas ya existentes o repetidas omitidas."])
            db.execute('INSERT INTO commands VALUES (?,?,?,?)', (tenant, key, fingerprint, json.dumps(result)))
        return jsonify(result)
