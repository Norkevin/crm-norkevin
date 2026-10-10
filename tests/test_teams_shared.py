from copy import deepcopy
from uuid import uuid4

import pytest
from flask import session

from test_flow_teams import web, run, assignment, records, member_client
from src.teams import TeamsError
from src.tenant_brand_map import all_resolved_brands


@pytest.fixture
def shared(web):
    app, owner, crm = web
    store = app.extensions['teams']
    brands = {b.brand_key: b for b in all_resolved_brands() if b.brand_key in ('norkevin', 'astral')}
    for key, b in brands.items():
        crm.upsert('tenants', dict(id=b.internal_tenant_id, login_email=b.sender_email, name=b.display_name, active=True, currency='GTQ'))
        with app.test_request_context('/'):
            session['tenant_id'] = b.internal_tenant_id
            crm.upsert('jobs', dict(id=key+'-job', tenant_id=b.internal_tenant_id, nombre=key+' wedding', price_total=20000,
                boda_date='2026-11-14', location='Antigua', status='En curso'))
    n, a = brands['norkevin'].internal_tenant_id, brands['astral'].internal_tenant_id
    p = run(store, 'member', n, name='Emerson', email='emerson@example.invalid', role='Foto', rate='1500')
    q = run(store, 'member', a, name='Emerson', email='Emerson@example.invalid', role='Foto', rate='1800')
    with owner.session_transaction() as s:
        s.update(tenant_id=n, user_email=brands['norkevin'].sender_email, teams_csrf='csrf')
    return app, owner, crm, store, n, a, p, q


def coverage(store, tenant, person, job, **fields):
    draft = assignment(store, person, job=job, tenant=tenant, **fields)
    return run(store, 'assignment_publish', tenant, id=draft['id'], version=draft['version'])


def test_one_portal_two_brands_independent_records_and_commands(shared):
    app, owner, crm, store, n, a, p, q = shared
    first = coverage(store, n, p, 'norkevin-job')
    second = coverage(store, a, q, 'astral-job')
    # Another member and an unrelated tenant with the same email are never visible.
    other = run(store, 'member', a, name='Other person', email='other@example.invalid', role='Video')
    coverage(store, a, other, 'astral-job', slot='Video')
    run(store, 'member', 'brand-b', name='Unrelated brand', email=p['email'], role='Foto')
    visitor = member_client(app, owner, p)
    left = visitor.get('/teams-portal/summary').get_json()
    right = visitor.get('/teams-portal/summary?brand=astral').get_json()
    assert left['member']['id'] == p['id'] and right['member']['id'] == q['id']
    assert [r['id'] for r in left['assignments']] == [first['id']]
    assert [r['id'] for r in right['assignments']] == [second['id']]
    assert {b['key'] for b in right['brands']} == {'norkevin', 'astral'}
    assert 'Other person' not in str(right) and 'Unrelated brand' not in str(right)
    page = visitor.get('/teams-portal/?brand=astral').get_data(as_text=True)
    assert 'Norkevin Foto' in page and 'Astral Films' in page
    assert 'name="brand" value="astral"' in page
    assert '/teams-portal/command?brand=astral' in page
    assert '/teams-portal/expenses/upload?brand=astral' in page
    assert 'brand=astral' in page.split('Descargar calendario')[0].rsplit('href=', 1)[-1]
    with visitor.session_transaction() as s:
        csrf = s['teams_portal_csrf']
    response = visitor.post('/teams-portal/command?brand=astral', headers={'X-Teams-CSRF': csrf}, json=dict(
        action='response', key=str(uuid4()), id=second['id'], version=second['version'], terms_version=1, status='rechazada'))
    assert response.status_code == 200
    assert records(store, 'assignment', a)[0]['status'] == 'rechazada'
    assert records(store, 'assignment', n)[0]['status'] == 'pendiente'
    assert visitor.post('/teams-portal/command?brand=astral', headers={'X-Teams-CSRF': csrf}, json=dict(
        action='response', key=str(uuid4()), id=first['id'], version=first['version'], terms_version=1, status='aceptada')).status_code == 404
    assert visitor.get('/teams-portal/summary?brand=brand-b').status_code == 403
    assert visitor.get('/teams-portal/?brand=astral&job_id=norkevin-job').status_code == 404
    # CRM summaries remain local; the bridge exposes no foreign costs or clients.
    own = owner.get('/api/teams/summary').get_json()
    assert {m['id'] for m in own['members']} == {p['id']}
    assert {c['job_id'] for c in own['costs']} == {'norkevin-job'}


def test_revocation_and_identity_changes_do_not_reopen_through_other_brand(shared):
    app, owner, crm, store, n, a, p, q = shared
    visitor = member_client(app, owner, p)
    assert visitor.get('/teams-portal/summary?brand=astral').status_code == 200
    q = run(store, 'member_revoke', a, id=q['id'], version=q['version'])
    assert visitor.get('/teams-portal/summary?brand=astral').status_code == 403
    fresh = member_client(app, owner, p)
    assert fresh.get('/teams-portal/summary?brand=astral').status_code == 403
    # Only fresh authentication to the revoked brand reauthorizes its own membership.
    with owner.session_transaction() as s:
        s.update(tenant_id=a, user_email='astralweddingsgt@gmail.com')
    restored = member_client(app, owner, q)
    assert restored.get('/teams-portal/summary?brand=norkevin').status_code == 200
    q = run(store, 'member', a, id=q['id'], version=records(store, 'member', a)[0]['version'],
            name='Emerson', email='changed@example.invalid', role='Foto')
    assert visitor.get('/teams-portal/summary?brand=astral').status_code == 403
    assert restored.get('/teams-portal/summary').status_code == 403


def test_new_membership_appears_in_member_portal_and_owner_preview(shared):
    app, owner, crm, store, n, a, p, q = shared
    with store.transaction() as db:
        db.execute("DELETE FROM entities WHERE kind='member' AND id=?", (q['id'],))
    visitor = member_client(app, owner, p)
    assert len(visitor.get('/teams-portal/summary').get_json()['brands']) == 1
    q = run(store, 'member', a, name='Emerson', email=p['email'], role='Foto')
    assert visitor.get('/teams-portal/summary?brand=astral').status_code == 200
    owner.get('/teams/preview/' + p['id'])
    assert len(owner.get('/teams-portal/summary').get_json()['brands']) == 2
    assert owner.get('/teams-portal/summary?brand=astral').get_json()['member']['id'] == q['id']
    run(store, 'member', a, id=q['id'], version=q['version'], name=q['name'], email=q['email'], role=q['role'], active=False)
    assert visitor.get('/teams-portal/summary?brand=astral').status_code == 403


def test_cross_brand_conflicts_warn_and_block_acceptance_but_never_disclose_money(shared):
    app, owner, crm, store, n, a, p, q = shared
    second = coverage(store, a, q, 'astral-job')
    second = store.command(a, 'Member', dict(action='response', key=str(uuid4()), id=second['id'], version=second['version'],
        terms_version=1, status='aceptada'), lambda i:dict(id=i,boda_date='2026-11-14'), member_id=q['id'])['record']
    result = store.command(n, 'Owner', dict(action='assignment', key=str(uuid4()), member_id=p['id'], job_id='norkevin-job',
        slot='Foto', role='Foto', start='2026-11-14T21:00', end='2026-11-14T23:00', buffer=30, amount='1500'),
        lambda i:dict(id=i,boda_date='2026-11-14'))
    assert 'Astral Weddings' in str(result['warnings'])
    first = result['record']
    with pytest.raises(TeamsError):
        run(store,'assignment_publish',n,id=first['id'],version=first['version'])
    first = run(store,'assignment_publish',n,id=first['id'],version=first['version'],conflict_reason='Revisado con el equipo')
    summary = owner.get('/api/teams/summary').get_json()
    conflicts = summary['assignments'][0]['other_brand_conflicts']
    assert conflicts == [dict(id=second['id'],status='aceptada', start=second['start'],end=second['end'],brand_name='Astral Weddings')]
    assert 'Astral Weddings' in owner.get('/teams/jobs/norkevin-job').get_data(as_text=True)
    with store.transaction() as db:
        first['conflict_override'] = None
        first = store.save(db, n, 'assignment', first)
    with pytest.raises(TeamsError):
        store.command(n, 'Member', dict(action='response', key=str(uuid4()), id=first['id'], version=first['version'],
            terms_version=1,status='aceptada'), lambda i:dict(id=i,boda_date='2026-11-14'), member_id=p['id'])
    with app.test_request_context('/'):
        session['tenant_id'] = a
        job = crm.get('jobs','astral-job')
        crm.upsert('jobs',dict(job,status='Cancelado'))
    with store.transaction() as db:
        assert store.conflicts(db,n,p['id'],first['start'],first['end'],30,first['id']) == []


def test_directory_copy_atomic_idempotent_deduplicated_without_bank_or_access_data(shared):
    app, owner, crm, store, n, a, p, q = shared
    new = run(store,'member',n,name='Alejandro',phone='+502 1234 5678',role='Foto',rate='1500',skills='Foto, Video',instagram='alejandro')
    run(store,'member',n,name='Alejandro',phone='+50212345678',role='Foto')
    with store.transaction() as db:
        store.create(db,n,'member_private',member_id=new['id'],private_profile=True,account_number='SECRET BANK')
    before = deepcopy(records(store,'member',n))
    headers = {'X-Teams-CSRF':'csrf'}
    assert owner.post('/api/teams/members/copy',json={'key':'copy'}).status_code == 403
    result = owner.post('/api/teams/members/copy',json={'key':'copy'},headers=headers).get_json()
    assert result['record'] == dict(created=1,skipped=2)
    assert 'Última copia a Astral Weddings' in owner.get('/teams/members').get_data(as_text=True)
    assert '1 miembro añadido' in str(result['warnings'])
    assert owner.post('/api/teams/members/copy',json={'key':'copy'},headers=headers).get_json() == result
    assert owner.post('/api/teams/members/copy',json={'key':'copy-again'},headers=headers).get_json()['record']['created'] == 0
    assert records(store,'member',n) == before
    assert len(records(store,'member',a)) == 2
    assert records(store,'member_private',a) == [] and records(store,'access',a) == []
    assert 'SECRET BANK' not in str(records(store,'audit',a))
    with owner.session_transaction() as s:
        s.update(tenant_id='brand-a',user_email='owner@example.invalid')
    assert owner.post('/api/teams/members/copy',json={'key':'deny'},headers=headers).status_code == 403


def test_copied_identity_without_email_warns_but_never_grants_shared_portal(shared):
    app, owner, crm, store, n, a, p, q = shared
    person = run(store, 'member', n, name='Sin correo', phone='12345678', role='Foto')
    assert owner.post('/api/teams/members/copy', headers={'X-Teams-CSRF':'csrf'}, json={'key':'copy-no-email'}).status_code == 200
    peer = next(m for m in records(store,'member',a) if m['name'] == 'Sin correo')
    second = coverage(store,a,peer,'astral-job',slot='Otra foto')
    first = assignment(store,person,job='norkevin-job',tenant=n,slot='Otra foto')
    with store.transaction() as db:
        clashes = store.conflicts(db,n,person['id'],first['start'],first['end'],first['buffer'],first['id'])
    assert clashes[0]['id'] == second['id']
    visitor = member_client(app,owner,person)
    assert visitor.get('/teams-portal/summary?brand=astral').status_code == 403


def test_pair_is_disabled_when_brand_ownership_is_unrecognized(shared):
    app, owner, crm, store, n, a, p, q = shared
    crm.upsert('tenants',dict(crm.get('tenants',a),login_email='unrelated-owner@example.invalid'))
    visitor = member_client(app,owner,p)
    assert visitor.get('/teams-portal/summary?brand=astral').status_code == 403
    assert owner.post('/api/teams/members/copy', headers={'X-Teams-CSRF':'csrf'}, json={'key':'copy-deny'}).status_code == 403


@pytest.mark.parametrize('origin', ['norkevin', 'astral'])
def test_owner_preview_shows_both_own_weddings_but_cannot_answer_or_change_owner(origin, shared):
    app, owner, crm, store, n, a, p, q = shared
    first = coverage(store,n,p,'norkevin-job')
    second = coverage(store,a,q,'astral-job')
    foreign = run(store,'member',a,name='Another member',email='another@example.invalid',role='Video')
    coverage(store,a,foreign,'astral-job',slot='Other video')
    tenant, person, email = (n,p,'norkevinfoto@gmail.com') if origin == 'norkevin' else (a,q,'astralweddingsgt@gmail.com')
    with owner.session_transaction() as s:
        s.update(tenant_id=tenant,user_email=email)
    assert owner.get('/teams/preview/'+person['id']).status_code == 302
    for brand, expected_person, expected_coverage in [('norkevin',p,first),('astral',q,second)]:
        summary = owner.get('/teams-portal/summary?brand='+brand).get_json()
        assert summary['preview'] and summary['member']['id'] == expected_person['id']
        assert [r['id'] for r in summary['assignments']] == [expected_coverage['id']]
        page = owner.get('/teams-portal/?brand='+brand).get_data(as_text=True)
        assert 'Norkevin Foto' in page and 'Astral Films' in page
        assert '/teams/members/'+person['id'] in page
        assert 'data-command="response"' not in page
        with owner.session_transaction() as s:
            csrf = s['teams_portal_csrf']
        response = owner.post('/teams-portal/command?brand='+brand,headers={'X-Teams-CSRF':csrf},json=dict(
            action='response',key=str(uuid4()),id=expected_coverage['id'],version=expected_coverage['version'],terms_version=1,status='aceptada'))
        assert response.status_code == 403 and 'vista previa' in response.get_json()['error']
    assert records(store,'assignment',n)[0]['status'] == 'pendiente'
    assert records(store,'assignment',a)[0]['status'] == 'pendiente'
    assert owner.get('/teams-portal/summary?brand=brand-b').status_code == 403
    with owner.session_transaction() as s:
        s.update(tenant_id='brand-b',user_email='other@example.invalid')
    assert owner.get('/teams-portal/summary?brand=astral').status_code == 403
