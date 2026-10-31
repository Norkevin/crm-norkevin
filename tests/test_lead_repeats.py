"""Repeated inquiries are visible without merging data or exposing another account."""
from types import SimpleNamespace

import pytest
from flask import template_rendered


def inquiry(id, **changes):
    return dict(dict(id=id, tenant_id='tenant-norkevin', nombre='Ana López',
                     email='ana@example.invalid', telefono='', fecha_tentativa='2034-06-17',
                     status='Nuevo', created='2026-10-31'), **changes)


def test_repeat_matching_requires_contact_date_and_account():
    import app as crm
    rows = [
        inquiry('original', telefono='+502 5555-1234'),
        inquiry('email', email=' ANA@EXAMPLE.INVALID ', nombre='Otro nombre'),
        inquiry('phone', email='otra@example.invalid', telefono='5555 1234'),
        inquiry('other-day', fecha_tentativa='2034-06-18'),
        inquiry('other-account', tenant_id='tenant-astral'),
        inquiry('no-date', fecha_tentativa=None),
        inquiry('invalid-date', fecha_tentativa='no es fecha'),
        inquiry('no-account', tenant_id=None),
        inquiry('same-name', email='diferente@example.invalid'),
        inquiry('empty', email='', telefono=''),
        inquiry('empty-too', email='', telefono=''),
        inquiry('invalid-contact', email='-', telefono='123'),
        inquiry('invalid-contact-too', email='-', telefono='123'),
    ]
    matches = crm._lead_repeat_matches(rows)
    assert set(matches['original']) == {'email', 'phone'}
    assert set(matches['email']) == {'original'}  # No transitive identity guesses.
    assert set(matches['phone']) == {'original'}
    assert all(not matches.get(row['id']) for row in rows[3:])
    assert all('repeat_count' not in row for row in rows)


def test_existing_repeats_show_notes_and_do_not_compete_for_the_date(auth_client):
    import app as crm
    rows = [
        inquiry('repeat-old', notes='Primera consulta <script>alert(1)</script>',
                created_time='2026-10-31T08:00:00-06:00'),
        inquiry('repeat-new', notas='Ahora queremos foto y video.\nCeremonia a las 16:00.',
                created_time='2026-10-31T09:30:00-06:00'),
        inquiry('repeat-archived', status='Perdido', notes='Consulta archivada.', created='2026-10-30'),
        inquiry('repeat-foreign', tenant_id='tenant-astral', notes='PRIVATE OTHER ACCOUNT'),
    ]
    for row in rows:
        crm.store.upsert('leads', row)
    contexts = {}

    def capture(sender, template, context, **extra):
        contexts[template.name] = context

    with template_rendered.connected_to(capture, crm.app):
        response = auth_client.get('/leads')
        assert response.status_code == 200
        assert 'Posible repetida · 3 solicitudes' in response.text
        for lead in contexts['leads.html']['leads']:
            if lead['id'].startswith('repeat-'):
                assert lead['repeat_count'] == 3
                assert not lead['other_lead_conflict']
                assert lead['date_available']

        detail = auth_client.get('/leads/repeat-new')
        assert detail.status_code == 200
        assert 'Posible solicitud repetida · 3 solicitudes' in detail.text
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in detail.text
        assert 'Ahora queremos foto y video.' in detail.text
        assert 'Consulta archivada.' in detail.text
        assert 'a las 09:30' in detail.text
        assert 'PRIVATE OTHER ACCOUNT' not in detail.text
        assert '/leads/repeat-foreign' not in detail.text
        assert 'href="/leads/repeat-old"' in detail.text
        assert contexts['lead_detail.html']['repeat_leads'][0]['id'] == 'repeat-new'

        # Legacy submissions only have dates; opening the older one must not reorder them.
        for id in ('repeat-old', 'repeat-new'):
            crm.store.upsert('leads', dict(crm.get_lead(id), created_time=None))
        assert auth_client.get('/leads/repeat-old').status_code == 200
        assert contexts['lead_detail.html']['repeat_leads'][0]['id'] == 'repeat-new'

        crm.store.upsert('leads', inquiry('real-competitor', email='otra-persona@example.invalid'))
        assert auth_client.get('/leads').status_code == 200
        assert all(lead['other_lead_conflict'] for lead in contexts['leads.html']['leads']
                   if lead['id'] in ('repeat-old', 'repeat-new'))

        # Matching is recalculated after edits; no stale stored flags.
        crm.store.upsert('leads', inquiry('repeat-new', fecha_tentativa='2034-06-18'))
        assert 'id="lead-repeats"' not in auth_client.get('/leads/repeat-new').text

    assert crm.get_lead('repeat-old')['notes'] == rows[0]['notes']


@pytest.mark.parametrize('endpoint,note_key', [('/api/captacion', 'notas'), ('/api/leads/nuevo', 'mensaje')])
def test_public_resubmission_preserves_both_requests(auth_client, monkeypatch, endpoint, note_key):
    import app as crm
    monkeypatch.setattr(crm, '_tenant_by_slug', lambda slug: {'id': 'tenant-norkevin'})
    monkeypatch.setattr(crm, '_notify_new_lead', lambda *args: None)
    monkeypatch.setattr(crm, 'trigger_workflow_for_lead', lambda *args, **kwargs: SimpleNamespace(id='test-workflow'))
    payload = dict(nombre='Ana', apellido='López', email='form-repeat@example.invalid',
                   pais='Guatemala', fecha_boda='2034-06-17', fecha_tentativa='2034-06-17')
    ids = []
    for notes in ('Primera consulta', 'Cambio de notas'):
        result = auth_client.post(endpoint, json=dict(payload, **{note_key: notes}))
        assert result.status_code == 200
        ids.append(result.json['lead_id'])
    assert ids[0] != ids[1]
    leads = [crm.get_lead(id) for id in ids]
    assert 'Primera consulta' in (leads[0].get('notas') or leads[0].get('notes'))
    assert 'Cambio de notas' in (leads[1].get('notas') or leads[1].get('notes'))
    assert set(crm._lead_repeat_matches(leads)[ids[0]]) == {ids[1]}
    assert 'Posible repetida · 2 solicitudes' in auth_client.get('/leads').text
