"""Commercial policy, geographical safeguards and tenant isolation."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from src import travel
from src.storage import JsonStore, TenantMismatchError, MissingTenantContextError


NORKEVIN = 'tenant-norkevin-photography'
ASTRAL = 'tenant-norkevin'


def venue(name, municipality='Antigua Guatemala', department='Sacatepéquez', level=0, **extra):
    return {'name': name, 'municipality': municipality, 'department': department, 'country': 'GT',
            'address': f'Dirección verificada de {name}, {municipality}',
            'source_url': 'https://example.com/ubicacion', 'verification_status': 'verified',
            'approval_status': 'approved', 'travel_level': level, 'aliases': [], **extra}


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(travel, '_GEOCODE_LAST_REQUEST', 0.0)
    seed = tmp_path / 'seed.json'
    seed.write_text(json.dumps({'venues': [
        venue('Finca San Cayetano', 'San Juan Alotenango', 'Sacatepéquez', 0, id='cayetano', aliases=['San Cayetano'], address='Km 89.5 RN-14, San Juan Alotenango'),
        venue('Hotel Atitlán', 'Panajachel', 'Sololá', 1, id='atitlan'),
        venue('La Casa del Mundo', 'Santa Cruz La Laguna', 'Sololá', 1, id='mundo', address='Jaibalito, Santa Cruz La Laguna'),
        venue('IRTRA Retalhuleu', 'San Martín Zapotitlán', 'Retalhuleu', 2, id='irtra'),
        venue('Xejuyup', 'Santa Cruz Muluá', 'Retalhuleu', 2, id='xejuyup'),
    ], 'proposed_destination_rules': [
        {'name': 'Cobán', 'value': 'Cobán', 'scope': 'municipality', 'level': 2, 'department': 'Alta Verapaz', 'approval_status': 'proposed'},
    ]}), encoding='utf-8')
    monkeypatch.setattr(travel, 'SEED_PATH', seed)
    return travel.TravelService(JsonStore(str(tmp_path / 'store')))


def lead(location, **extra):
    return {'id': 'test-lead', 'tenant_id': NORKEVIN, 'locacion': location, **extra}


@pytest.mark.parametrize('location,level,photo,video', [
    ('Finca San Cayetano', 0, 0, 0),
    ('FINCA SAN CAYETANO', 0, 0, 0),
    ('San Cayetano', 0, 0, 0),
    ('Hotel Atitlan', 1, 1500, 2500),
    ('La Casa del Mundo', 1, 1500, 2500),
    ('IRTRA Retalhuleu', 2, 2000, 3500),
    ('Flores, Petén', 3, 2500, 4500),
    ('Tecpán', 0, 0, 0),
    ('Antigua Guatemala', 0, 0, 0),
    ('Ciudad de Guatemala', 0, 0, 0),
    ('San Lucas Sacatepéquez', 0, 0, 0),
    ('Atitlán', 1, 1500, 2500),
])
def test_confirmed_policy(service, location, level, photo, video):
    result = service.resolve(lead(location))
    assert result['confidence'] == 'verified'
    assert result['definitive'] and not result['requires_review']
    assert (result['level'], result['photo_amount'], result['photo_video_amount']) == (level, photo, video)
    assert result['country'] == 'GT'
    assert result['enabled'] is False  # Simulation before controlled activation.


@pytest.mark.parametrize('location', ['Petén', 'Departamento de Petén', 'Depto. de Petén', 'Boda en Petén',
                                      'Boda en departamento de Petén', 'Finca nueva, Departamento de Petén',
                                      'Departamento de Retalhuleu'])
def test_explicit_department_prefix_uses_only_approved_regional_policy(service, location):
    result = service.resolve(lead(location))
    assert result['definitive'] and result['level'] == (2 if 'Retalhuleu' in location else 3)


@pytest.mark.parametrize('location', ['Hotel Petén', 'Finca Retalhuleu', 'Boda en Hotel Petén',
                                      'Departamento de Sacatepéquez', 'Boda en Chimaltenango', 'Depto. de Guatemala'])
def test_department_prefix_does_not_guess_hotel_geography_or_expand_exempt_municipalities(service, location):
    result = service.resolve(lead(location))
    assert result['level'] is None and not result['definitive']


def test_approved_department_rule_is_not_hidden_by_homonymous_proposed_municipality(service):
    municipality = service.save_rule(NORKEVIN, dict(value='Chiquimula', department='Chiquimula', scope='municipality',
                                                  level=1, approval_status='proposed'))
    service.save_rule(NORKEVIN, dict(value='Chiquimula', scope='region', level=2, approval_status='approved'))
    assert service.resolve(lead('Chiquimula'))['level'] == 2
    assert service.resolve(lead('Departamento de Chiquimula'))['level'] == 2
    service.save_rule(NORKEVIN, {'id': municipality['id'], 'approval_status': 'approved'})
    assert service.resolve(lead('Chiquimula', municipality='Chiquimula'))['level'] == 1


def test_pending_venue_uses_only_client_explicit_department_for_approved_region(service):
    pending = service.save_venue(NORKEVIN, venue('Hotel Sin Confirmar', '', 'Petén', None,
                                               verification_status='pending', approval_status='proposed'))
    assert service.resolve(lead(pending['name']))['level'] is None
    assert service.resolve(lead(pending['name'] + ', Departamento de Petén'))['level'] == 3
    assert service.resolve(lead(pending['name'], department='Petén'))['level'] == 3
    conflict = service.resolve(lead(pending['name'], department='Retalhuleu'))
    assert conflict['confidence'] == 'ambiguous' and conflict['level'] is None
    assert service.resolve(lead(pending['name'] + ', Petén, Argentina'))['confidence'] == 'international'
    assert service.resolve(lead('Finca San Cayetano, Departamento de Petén'))['level'] is None


def test_approved_venue_exception_precedes_explicit_department_rule(service):
    service.save_venue(NORKEVIN, venue('Finca Exenta', 'Flores', 'Petén', 0))
    assert service.resolve(lead('Finca Exenta, Departamento de Petén'))['level'] == 0


def test_verified_local_catalog_never_queries_foreign_provider(service, monkeypatch):
    def forbidden(_query):
        raise AssertionError('A verified local venue must not call the provider')
    monkeypatch.setattr(service, '_geocode', forbidden)
    assert service.resolve(lead('Finca San Cayetano'), research=True)['level'] == 0


@pytest.mark.parametrize('extra', [
    {'locacion': 'Finca San Cayetano, Argentina'},
    {'pais': 'Argentina'},
    {'country': 'MX'},
    {'notas': 'La boda será en Argentina'},
    {'locacion': 'Finca San Cayetano, Grecia'},
    {'locacion': 'Finca San Cayetano, France'},
    {'locacion': 'Finca San Cayetano, España'},
])
def test_international_context_precedes_internal_match(service, extra):
    result = service.resolve(lead('Finca San Cayetano', **extra))
    assert result['confidence'] == 'international'
    assert result['level'] is None and result['photo_amount'] is None
    assert result['requires_review']


def test_guest_country_does_not_change_wedding_country(service):
    result = service.resolve(lead('Finca San Cayetano', notas='Tenemos invitados de Argentina'))
    assert result['level'] == 0


@pytest.mark.parametrize('location', ['Hotel Antigua', 'Finca Guatemala', 'Sacatepéquez', 'Chimaltenango', 'Guatemala', 'San Juan Alotenango', 'Lugar inventado'])
def test_unknown_venues_and_departments_never_become_exempt(service, location):
    result = service.resolve(lead(location))
    assert result['requires_review'] and result['level'] is None


def test_notes_about_civil_wedding_do_not_override_main_wedding_location(service):
    result = service.resolve(lead('Xejuyup, Carretera Quetzaltenango - San Sebastián, Santa Cruz Muluá, Retalhuleu, Guatemala',
                                  notas='También boda civil en ciudad de Guatemala en junio 2027'))
    assert result['level'] == 2
    assert result['venue_name'] == 'Xejuyup'


def test_explicit_municipality_works_and_foreign_conflict_does_not(service):
    assert service.resolve(lead('Jardín Nuevo', municipality='Antigua Guatemala', department='Sacatepéquez'))['level'] == 0
    assert service.resolve(lead('Hotel Antigua', municipality='Cobán', department='Alta Verapaz'))['level'] is None
    assert service.resolve(lead('Hotel Atitlán', municipality='Antigua Guatemala'))['confidence'] == 'ambiguous'


@pytest.mark.parametrize('location', ['Finca San Cayetano, Retalhuleu, Guatemala', 'Hotel Atitlán, Antigua Guatemala', 'Finca San Cayetano, Petén', 'Finca San Cayetano, Reykjavik'])
def test_conflicting_address_suffix_cannot_override_geography(service, location):
    result = service.resolve(lead(location))
    assert result['confidence'] == 'ambiguous' and result['level'] is None


def test_canonical_capital_municipality(service):
    saved = service.save_venue(NORKEVIN, venue('Hotel Capital', 'Guatemala', 'Guatemala', None, approval_status='proposed'))
    assert service.resolve(lead(saved['name']))['level'] == 0


def test_exception_overrides_regional_rule(service):
    service.save_venue(NORKEVIN, venue('Finca Exenta', 'Flores', 'Petén', 0))
    assert service.resolve(lead('Finca Exenta'))['level'] == 0


def test_duplicate_names_remain_ambiguous_until_context_resolves(service):
    service.save_venue(NORKEVIN, venue('Jardín Las Flores', 'Antigua Guatemala', 'Sacatepéquez', 0))
    service.save_venue(NORKEVIN, venue('Jardín Las Flores', 'Panajachel', 'Sololá', 1))
    assert service.resolve(lead('Jardín Las Flores'))['confidence'] == 'ambiguous'
    assert service.resolve(lead('Jardín Las Flores', municipality='Panajachel'))['level'] == 1
    assert service.resolve(lead('Jardín Las Flores, Panajachel'))['level'] == 1
    assert service.resolve(lead('Jardín Las Flores', notas='La dirección queda en Panajachel'))['level'] == 1


def test_admin_resolution_remembers_duplicate_context(service):
    service.save_venue(NORKEVIN, venue('Jardín Duplicado'))
    target = service.save_venue(NORKEVIN, venue('Jardín Duplicado', 'Panajachel', 'Sololá', 1))
    assert service.resolve(lead('Jardín Duplicado'))['confidence'] == 'ambiguous'
    review = service.reviews(NORKEVIN)[0]
    service.resolve_review(NORKEVIN, review['id'], target['id'])
    assert service.resolve(lead('Jardín Duplicado'))['level'] == 1
    assert service.resolve(lead('Jardín Duplicado', municipality='Antigua Guatemala'))['level'] == 0


def test_typo_is_only_probable(service):
    result = service.resolve(lead('Finca San Caytano'))
    assert result['confidence'] == 'probable'
    assert result['requires_review'] and result['level'] is None


def test_proposed_classification_is_not_automatically_approved(service):
    row = service.save_venue(NORKEVIN, venue('Nuevo Hotel', 'Panajachel', 'Sololá', 2, approval_status='proposed'))
    result = service.resolve(lead('Nuevo Hotel'))
    assert result['confidence'] == 'verified'
    assert result['level'] is None and result['suggested_level'] == 2
    service.save_venue(NORKEVIN, {'id': row['id'], 'approval_status': 'approved'}, actor='test-admin')
    assert service.resolve(lead('Nuevo Hotel'))['level'] == 2


def test_proposed_destination_requires_approval(service):
    assert service.resolve(lead('Cobán'))['level'] is None
    rule = next(row for row in service.rules(NORKEVIN) if row['value'] == 'Cobán')
    service.save_rule(NORKEVIN, {'id': rule['id'], 'approval_status': 'approved'})
    assert service.resolve(lead('Cobán'))['level'] == 2


def test_flights_in_peten_require_logistics_review(service):
    result = service.resolve(lead('Flores, Petén', notas='Necesitamos vuelo y hospedaje extraordinario'))
    assert result['requires_review'] and not result['definitive']


def test_review_deduplicates_accents_and_case(service):
    service.resolve(lead('Salón Nuevo', id='one'))
    service.resolve(lead('SALON NUEVO', id='two'))
    reviews = service.reviews(NORKEVIN)
    assert len(reviews) == 1 and reviews[0]['lead_ids'] == ['one', 'two']


def test_catalog_seed_is_idempotent_and_manual_changes_survive(service):
    rows = service.catalog(NORKEVIN)
    changed = service.save_venue(NORKEVIN, {'id': rows[0]['id'], 'travel_level': 3})
    service.ensure_catalog(NORKEVIN)
    assert len(service.catalog(NORKEVIN)) == len(rows)
    assert next(row for row in service.catalog(NORKEVIN) if row['id'] == changed['id'])['travel_level'] == 3


def test_seed_metadata_upgrade_is_additive_once_and_preserves_admin_changes(service):
    initial = service.catalog(NORKEVIN)[0]
    service.save_venue(NORKEVIN, {'id': initial['id'], 'travel_level': 3, 'source_url': 'https://example.com/admin',
                                 'address': 'Dirección corregida por administración', 'verification_notes': 'Verificación administrativa'})
    seed = json.loads(travel.SEED_PATH.read_text())
    seed['metadata_revision'] = 'excel-v1'
    seed_row = next(row for row in seed['venues'] if row['id'] == initial['seed_id'])
    seed_row['aliases'].append('Alias adicional Excel')
    seed_row['source_urls'] = ['https://example.com/excel-source']
    seed['venues'].append(venue('Venue añadido después', id='new-seed-venue', verification_status='pending', approval_status='proposed', level=None))
    travel.SEED_PATH.write_text(json.dumps(seed))
    service.ensure_catalog(NORKEVIN)
    upgraded = next(row for row in service.catalog(NORKEVIN) if row['id'] == initial['id'])
    assert upgraded['travel_level'] == 3 and upgraded['source_url'] == 'https://example.com/admin'
    assert upgraded['address'] == 'Dirección corregida por administración'
    assert upgraded['verification_notes'] == 'Verificación administrativa'
    assert 'Alias adicional Excel' in upgraded['aliases'] and 'https://example.com/excel-source' in upgraded['source_urls']
    assert upgraded['seed_metadata_revision'] == 'excel-v1'
    assert any(row['name'] == 'Venue añadido después' for row in service.catalog(NORKEVIN))
    audits = [row for row in service.store.list('travel_audit') if row['action'] == 'seed_metadata']
    service.save_venue(NORKEVIN, {'id': upgraded['id'], 'aliases': initial['aliases'], 'source_urls': []})
    service.ensure_catalog(NORKEVIN)
    edited = next(row for row in service.catalog(NORKEVIN) if row['id'] == initial['id'])
    assert 'Alias adicional Excel' not in edited['aliases'] and edited['source_urls'] == []
    assert len([row for row in service.store.list('travel_audit') if row['action'] == 'seed_metadata']) == len(audits)


def test_spelling_variation_reuses_venue(service):
    initial = service.save_venue(NORKEVIN, venue('Jardín Ámbar'))
    repeated = service.save_venue(NORKEVIN, venue('JARDIN AMBAR'))
    assert repeated['id'] == initial['id']
    assert len([v for v in service.catalog(NORKEVIN) if travel.normalize(v['name']) == 'jardin ambar']) == 1


def test_concurrent_creation_does_not_duplicate_venue(service):
    service.ensure_catalog(NORKEVIN)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: service.save_venue(NORKEVIN, venue('Hacienda Concurrente')), range(8)))
    assert len({row['id'] for row in rows}) == 1


def test_astral_policy_is_never_inherited_from_misleading_tenant_id(service):
    result = service.resolve(lead('Hotel Atitlán', tenant_id=ASTRAL))
    assert result['applicable'] is False and result['enabled'] is False
    assert result['photo_amount'] is None and result['level'] is None
    assert result['confidence'] == 'verified'
    assert service.rules(ASTRAL) == []
    with pytest.raises(ValueError):
        service.save_settings(ASTRAL, {'enabled': True})


def test_package_auto_send_settings_default_preserve_other_field_and_are_audited(service):
    assert service.settings(NORKEVIN)['auto_send_packages'] is False
    automatic = service.save_settings(NORKEVIN, {'auto_send_packages': True}, actor='test-owner')
    assert automatic['auto_send_packages'] is True and automatic['enabled'] is False
    assert service.save_settings(NORKEVIN, {'enabled': True})['auto_send_packages'] is True
    assert service.save_settings(NORKEVIN, {'auto_send_packages': False})['enabled'] is True
    assert service.save_settings(NORKEVIN, {'enabled': False})['auto_send_packages'] is False
    audits = [row for row in service.store.list('travel_audit') if row['action'] == 'settings']
    assert len(audits) == 4 and audits[0]['after']['auto_send_packages'] is True
    assert audits[0]['actor'] == 'test-owner'
    assert service.settings(ASTRAL)['auto_send_packages'] is False
    with pytest.raises(ValueError):
        service.save_settings(ASTRAL, {'auto_send_packages': True})
    service.store.save_tenant_dict('travel_settings', {'enabled': True, 'auto_send_packages': True}, ASTRAL)
    assert service.settings(ASTRAL)['enabled'] is False and service.settings(ASTRAL)['auto_send_packages'] is False


@pytest.mark.parametrize('field,value', [('enabled', 1), ('enabled', 'true'), ('auto_send_packages', 1),
                                       ('auto_send_packages', 'false'), ('auto_send_packages', None)])
def test_settings_require_real_booleans_without_writing(service, field, value):
    with pytest.raises(ValueError):
        service.save_settings(NORKEVIN, {field: value})
    assert service.store.get_tenant_dict('travel_settings', tenant_id=NORKEVIN) == {}


def test_catalog_and_admin_are_tenant_isolated(service):
    norkevin_row = service.catalog(NORKEVIN)[0]
    astral_rows = service.catalog(ASTRAL)
    assert norkevin_row['id'] not in [row['id'] for row in astral_rows]
    with pytest.raises(ValueError):
        service.save_venue(ASTRAL, {'id': norkevin_row['id'], 'travel_level': 1})
    with pytest.raises(TenantMismatchError):
        service.save_venue(ASTRAL, {**norkevin_row})
    service.store.tenant_resolver = lambda: ASTRAL
    with pytest.raises(TenantMismatchError):
        service.catalog(NORKEVIN)
    with pytest.raises(TenantMismatchError):
        service.save_settings(NORKEVIN, {'enabled': True})


def test_missing_or_unsafe_tenant_rejected(service):
    for tenant in (None, '', '../foreign'):
        with pytest.raises(MissingTenantContextError):
            service.settings(tenant)
    service.store.request_context_probe = lambda: True
    with pytest.raises(TenantMismatchError):
        service.resolve(lead('Antigua'))


@pytest.mark.parametrize('change', [
    {'source_url': 'javascript:alert(1)'}, {'source_url': ''},
    {'country': 'Argentina'}, {'municipality': ''}, {'department': ''}, {'address': ''},
    {'travel_level': 1700}, {'travel_level': True}, {'travel_level': 1.8},
    {'latitude': 'nan'}, {'longitude': 200},
])
def test_invalid_verification_and_arbitrary_tariffs_rejected(service, change):
    with pytest.raises(ValueError):
        service.save_venue(NORKEVIN, venue('Invalid Venue', **change))


def test_fingerprint_detects_location_policy_and_activation_changes(service):
    original = service.resolve(lead('Hotel Atitlán'))['fingerprint']
    assert service.resolve(lead('Hotel Atitlán'))['fingerprint'] == original
    assert service.resolve(lead('Tecpán'))['fingerprint'] != original
    service.save_settings(NORKEVIN, {'enabled': True})
    active = service.resolve(lead('Hotel Atitlán'))['fingerprint']
    assert active != original
    row = next(row for row in service.catalog(NORKEVIN) if row['name'] == 'Hotel Atitlán')
    service.save_venue(NORKEVIN, {'id': row['id'], 'travel_level': 2})
    assert service.resolve(lead('Hotel Atitlán'))['fingerprint'] != active


def test_foreign_geocoder_results_are_rejected(service, monkeypatch):
    monkeypatch.setattr(service, '_geocode', lambda query: [{'name': 'Hacienda Desconocida', 'address': {'country_code': 'ar'}}])
    result = service.resolve(lead('Hacienda Desconocida'), research=True)
    assert result['confidence'] == 'unknown' and result['level'] is None
    assert not any(row['name'] == 'Hacienda Desconocida' for row in service.catalog(NORKEVIN))


def test_provider_failure_remains_reviewable(service, monkeypatch):
    def fail(query):
        raise TimeoutError('network timeout')
    monkeypatch.setattr(service, '_geocode', fail)
    result = service.resolve(lead('Destino Inventado'), research=True)
    assert result['requires_review'] and result['level'] is None


def test_external_result_is_proposal_then_admin_approves(service, monkeypatch):
    calls = []
    def geocode(query):
        calls.append(query)
        return [{'name': 'Finca Nueva', 'display_name': 'Finca Nueva, Panajachel, Sololá, Guatemala', 'lat': '14.74', 'lon': '-91.15',
                 'osm_type': 'node', 'osm_id': 123, 'address': {'country_code': 'gt', 'town': 'Panajachel', 'state': 'Sololá'}}]
    monkeypatch.setattr(service, '_geocode', geocode)
    result = service.resolve(lead('Finca Nueva'), research=True)
    assert result['confidence'] == 'probable' and result['level'] is None
    proposed = next(row for row in service.catalog(NORKEVIN) if row['name'] == 'Finca Nueva')
    assert proposed['verification_status'] == 'pending' and proposed['approval_status'] == 'proposed'
    assert proposed['travel_level'] == 1
    service.resolve(lead('Finca Nueva'), research=True)
    assert len(calls) == 1
    service.save_venue(NORKEVIN, {'id': proposed['id'], 'verification_status': 'verified', 'approval_status': 'approved'})
    assert service.resolve(lead('Finca Nueva'))['level'] == 1


def test_pending_import_research_fills_gaps_without_duplicates_or_approval(service, monkeypatch):
    imported = service.save_venue(NORKEVIN, venue(
        'Finca Importada', '', 'Sacatepéquez', None, verification_status='pending', approval_status='proposed',
        address='Dirección escrita por administración', source_url='https://example.com/original',
        source_urls=['https://example.com/historica'], verification_notes='Excel Lugares, fila 17'))
    initial_count, calls = len(service.catalog(NORKEVIN)), []
    def geocode(query):
        calls.append(query)
        return [{'name': 'Finca Importada', 'display_name': 'Otra dirección sugerida', 'lat': '14.6', 'lon': '-90.7',
                 'osm_type': 'node', 'osm_id': 987,
                 'address': {'country_code': 'gt', 'town': 'Antigua Guatemala', 'state': 'Sacatepéquez'}}]
    monkeypatch.setattr(service, '_geocode', geocode)
    result = service.resolve(lead('Finca Importada'), research=True)
    assert calls == ['Finca Importada, Sacatepéquez, Guatemala']
    assert result['requires_review'] and result['level'] is None and result['confidence'] == 'probable'
    assert [item['id'] for item in result['candidates']] == [imported['id']]
    updated = next(v for v in service.catalog(NORKEVIN) if v['id'] == imported['id'])
    assert updated['municipality'] == 'Antigua Guatemala'
    assert updated['address'] == imported['address'] and updated['source_url'] == imported['source_url']
    assert updated['verification_notes'] == imported['verification_notes']
    assert set(updated['source_urls']) == {'https://example.com/historica', 'https://example.com/original', 'https://www.openstreetmap.org/node/987'}
    assert updated['verification_status'] == 'pending' and updated['approval_status'] == 'proposed' and updated['travel_level'] is None
    service.resolve(lead('Finca Importada'), research=True)
    reloaded = service.resolve(lead('Finca Importada'))
    assert len(calls) == 1 and len(service.catalog(NORKEVIN)) == initial_count
    assert reloaded['level'] is None
    assert [item['id'] for item in service.reviews(NORKEVIN)[0]['candidates']] == [imported['id']]


def test_pending_research_alternatives_survive_reload_and_do_not_duplicate(service, monkeypatch):
    imported = service.save_venue(NORKEVIN, venue('Finca Dos Lugares', '', 'Sacatepéquez', None,
                                                verification_status='pending', approval_status='proposed', address=''))
    calls = []
    def geocode(query):
        calls.append(query)
        return [{'name': 'Finca Dos Lugares', 'display_name': municipality + ', Sacatepéquez', 'osm_type': 'node', 'osm_id': index,
                 'address': {'country_code': 'gt', 'town': municipality, 'state': 'Sacatepéquez'}}
                for index, municipality in enumerate(['Antigua Guatemala', 'San Lucas Sacatepéquez'], 111)]
    monkeypatch.setattr(service, '_geocode', geocode)
    result = service.resolve(lead('Finca Dos Lugares'), research=True)
    candidate_ids = {item['id'] for item in result['candidates']}
    assert result['confidence'] == 'ambiguous' and len(candidate_ids) == 3
    assert imported['id'] in candidate_ids and result['level'] is None
    count = len(service.catalog(NORKEVIN))
    service.resolve(lead('Finca Dos Lugares'))
    service.resolve(lead('Finca Dos Lugares'), research=True)
    assert {item['id'] for item in service.reviews(NORKEVIN)[0]['candidates']} == candidate_ids
    assert len(service.catalog(NORKEVIN)) == count and len(calls) == 1
    assert all(v['verification_status'] == 'pending' for v in service.catalog(NORKEVIN) if v['id'] in candidate_ids)


@pytest.mark.parametrize('failure', [False, True])
def test_pending_research_empty_or_failed_provider_keeps_existing_candidate(service, monkeypatch, failure):
    imported = service.save_venue(NORKEVIN, venue('Finca Sin Fuente', '', 'Sacatepéquez', None,
                                                verification_status='pending', approval_status='proposed'))
    def geocode(query):
        if failure:
            raise TimeoutError('Provider unavailable')
        return []
    monkeypatch.setattr(service, '_geocode', geocode)
    result = service.resolve(lead('Finca Sin Fuente'), research=True)
    assert result['level'] is None and result['requires_review']
    assert [item['id'] for item in result['candidates']] == [imported['id']]
    assert next(v for v in service.catalog(NORKEVIN) if v['id'] == imported['id']) == imported


def test_complete_registered_parenthetical_name_is_not_a_geographic_suffix(service):
    service.save_venue(NORKEVIN, venue('Casa de la Ruina (Santa Rosa)', '', 'Sacatepéquez', None,
                                     verification_status='pending', approval_status='proposed'))
    result = service.resolve(lead('Casa de la Ruina (Santa Rosa)'))
    assert result['confidence'] == 'probable' and result['level'] is None
    with_department = service.resolve(lead('Casa de la Ruina (Santa Rosa), Sacatepéquez'))
    assert with_department['confidence'] == 'probable' and with_department['level'] is None
    conflict = service.resolve(lead('Casa de la Ruina (Santa Rosa), Retalhuleu'))
    assert conflict['requires_review'] and conflict['level'] is None


def test_registered_name_with_comma_remains_pending_with_department_suffix(service):
    imported = service.save_venue(NORKEVIN, venue('Hotel Del Ángel, Cobán', '', 'Alta Verapaz', None,
                                                verification_status='pending', approval_status='proposed'))
    result = service.resolve(lead('Hotel Del Ángel, Cobán, Alta Verapaz'))
    assert result['venue_id'] == imported['id'] and result['confidence'] == 'probable'
    assert result['requires_review'] and result['level'] is None
    conflict = service.resolve(lead('Hotel Del Ángel, Cobán, Petén'))
    assert conflict['confidence'] == 'ambiguous' and conflict['level'] is None


@pytest.mark.parametrize('name,location,municipality,department,search_allowed', [
    ('Finca La Ruca Jardín Boutique', 'Finca La Ruca Jardín Boutique', 'Antigua Guatemala', '', True),
    ('Finca La Ruca Jardín Boutique', 'Finca La Ruca Jardín Boutique, Antigua Guatemala', '', '', True),
    ('Casa de la Ruina (Santa Rosa)', 'Casa de la Ruina (Santa Rosa), Antigua Guatemala', '', '', True),
    ('Finca La Ruca Jardín Boutique', 'Finca La Ruca Jardín Boutique', '', 'Retalhuleu', False),
])
def test_unknown_pending_municipality_accepts_hints_but_not_conflicting_department(
        service, monkeypatch, name, location, municipality, department, search_allowed):
    imported = service.save_venue(NORKEVIN, venue(name, '', 'Sacatepéquez', None,
                                                verification_status='pending', approval_status='proposed'))
    calls = []
    monkeypatch.setattr(service, '_geocode', lambda query: calls.append(query) or [])
    result = service.resolve(lead(location, municipality=municipality, department=department), research=True)
    assert bool(calls) is search_allowed
    assert result['requires_review'] and result['level'] is None and result['photo_amount'] is None
    if search_allowed:
        assert result['confidence'] == 'probable' and result['venue_id'] == imported['id']
        assert 'Antigua Guatemala' in calls[0] and 'Sacatepéquez' in calls[0]
    else:
        assert result['confidence'] == 'ambiguous'
    stored = next(v for v in service.catalog(NORKEVIN) if v['id'] == imported['id'])
    assert stored['verification_status'] == 'pending' and stored['municipality'] == ''


def test_external_cache_prevents_repeat_unknown_calls(service, monkeypatch):
    calls = []
    monkeypatch.setattr(service, '_geocode', lambda query: calls.append(query) or [])
    service.resolve(lead('Ningún Lugar'), research=True)
    service.resolve(lead('NINGUN LUGAR'), research=True)
    assert len(calls) == 1


def test_geography_throttle_is_shared_across_tenants(service, monkeypatch):
    calls = []
    monkeypatch.setattr(service, '_geocode', lambda query: calls.append(query) or [])
    service.resolve(lead('Ningún Lugar'), research=True)
    service.resolve(lead('Otro lugar', tenant_id=ASTRAL), research=True)
    assert len(calls) == 1


@pytest.mark.parametrize('location', ['Finca desconocida contacto@example.com', 'Finca tel. +502 5555 1234', 'https://example.com/boda'])
def test_research_never_transmits_contact_data(service, monkeypatch, location):
    monkeypatch.setattr(service, '_geocode', lambda query: pytest.fail('PII must never reach geocoder'))
    assert service.resolve(lead(location), research=True)['requires_review']


def test_failure_cache_expires_after_one_minute(service, monkeypatch):
    calls = []
    monkeypatch.setattr(service, '_geocode', lambda query: calls.append(query) or [])
    now = travel.time.time()
    service.resolve(lead('Ningún Lugar'), research=True)
    monkeypatch.setattr(travel.time, 'time', lambda: now + 61)
    monkeypatch.setattr(travel, '_GEOCODE_LAST_REQUEST', 0.0)
    service.resolve(lead('Ningún Lugar'), research=True)
    assert len(calls) == 2


def test_provider_uses_configurable_https_gt_filter_identification_and_timeout(monkeypatch):
    captured = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, size):
            assert size == 250000
            return b'[]'
    def fake_open(request, timeout):
        captured.append((request, timeout))
        return Response()
    monkeypatch.setattr(travel, 'urlopen', fake_open)
    monkeypatch.setenv('FLOW_GEOCODER_URL', 'https://geo.example.test/search')
    assert travel.TravelService._geocode('Nueva finca Guatemala') == []
    request, timeout = captured[0]
    assert request.full_url.startswith('https://geo.example.test/search?')
    assert 'countrycodes=gt' in request.full_url and 'limit=5' in request.full_url
    assert timeout == 4
    assert 'FlowingCRM' in request.get_header('User-agent')
    monkeypatch.setenv('FLOW_GEOCODER_URL', 'http://geo.example.test/search')
    with pytest.raises(ValueError):
        travel.TravelService._geocode('Nueva finca Guatemala')


def test_full_real_seed_schema_and_required_venues(tmp_path):
    seed_path = travel.Path(__file__).resolve().parents[1] / 'data' / 'seeds' / 'venues_guatemala.default.json'
    if not seed_path.exists():
        pytest.skip('Researched catalogue not yet available')
    seed = json.loads(seed_path.read_text())
    verified = [row for row in seed['venues'] if row['verification_status'] == 'verified']
    pending = [row for row in seed['venues'] if row['verification_status'] == 'pending']
    assert len(verified) == 48
    assert len(pending) == 0 or len(pending) >= 100
    assert all(row['approval_status'] in ('proposed', 'provisional') and row['travel_level'] is None for row in pending)
    validator = travel.TravelService(None)
    for row in seed['venues']:
        validator._validate_venue(row)
    for row in seed['proposed_destination_rules']:
        validator._validate_rule(row)
    names = {travel.normalize(row['name']) for row in seed['venues']}
    assert {'finca san cayetano', 'hotel atitlan', 'la casa del mundo'} <= names
    actual = travel.TravelService(JsonStore(str(tmp_path / 'actual')))
    actual.ensure_catalog(NORKEVIN)
    assert len(actual.catalog(NORKEVIN)) == len(seed['venues'])
    assert len([row for row in actual.rules(NORKEVIN) if row['approval_status'] == 'proposed']) == len(seed['proposed_destination_rules'])
    for name, level in [('Finca San Cayetano', 0), ('Hotel Atitlán', 1), ('La Casa del Mundo', 1), ('IRTRA Retalhuleu', 2), ('Tecpán', 0), ('Flores, Petén', 3)]:
        assert actual.resolve(lead(name))['level'] == level


@pytest.mark.parametrize('location', [
    'Monterrico, Santa Rosa', 'MONTERRICO, STA. ROSA', 'Monterrico Santa Rosa',
    'Monterrico', 'Monterrico, Santa Rosa, Guatemala', 'Hotel nuevo, Monterrico, Santa Rosa',
])
def test_monterrico_custom_destination_rate(service, location):
    result = service.resolve(lead(location))
    assert result['definitive'] and not result['requires_review']
    assert result['level'] is None
    assert (result['photo_amount'], result['photo_video_amount']) == (1000, 1500)
    assert result['normalized_location'] == 'Monterrico, Santa Rosa, Guatemala'


def test_destination_rule_ignores_unverified_geocoder_alias_collisions(service):
    for name in ('Aldea sugerida', 'Playa sugerida'):
        service.save_venue(NORKEVIN, venue(name, 'Taxisco', 'Santa Rosa', None,
            aliases=['Monterrico, Santa Rosa'], verification_status='pending', approval_status='proposed'))
    result = service.resolve(lead('Monterrico, Santa Rosa'))
    assert result['definitive'] and result['photo_amount'] == 1000 and not result['venue_id']


@pytest.mark.parametrize('location,extra', [
    ('Santa Rosa', {}), ('Taxisco, Santa Rosa', {}), ('Hotel Monterrico', {}),
    ('Monterrico, Escuintla', {}), ('Monterrico', {'departamento': 'Escuintla'}),
    ('Monterrico, Santa Rosa', {'pais': 'Argentina'}),
    ('Monterrico, Santa Rosa', {'tenant_id': ASTRAL}),
])
def test_monterrico_rule_does_not_spread_to_other_destinations_or_brands(service, location, extra):
    result = service.resolve(lead(location, **extra))
    assert not result['definitive'] and result['photo_amount'] is None


def test_verified_locality_uses_rate_but_venue_exception_still_wins(service):
    row = service.save_venue(NORKEVIN, venue('Hotel de la Playa', 'Taxisco', 'Santa Rosa', None,
                                           locality='Monterrico', approval_status='proposed'))
    assert service.resolve(lead(row['name']))['photo_amount'] == 1000
    assert service.resolve(lead(row['name'] + ', Monterrico, Santa Rosa'))['photo_amount'] == 1000
    service.save_venue(NORKEVIN, {'id': row['id'], 'travel_level': 0, 'approval_status': 'approved'})
    assert service.resolve(lead(row['name']))['photo_amount'] == 0


def test_custom_rule_edit_survives_seeding_and_can_return_to_level(service):
    rule = next(row for row in service.rules(NORKEVIN) if row['value'] == 'Monterrico')
    service.save_rule(NORKEVIN, {'id': rule['id'], 'photo_video_amount': 1800})
    assert service.resolve(lead('Monterrico'))['photo_video_amount'] == 1800
    service.ensure_catalog(NORKEVIN)
    assert service.resolve(lead('Monterrico'))['photo_video_amount'] == 1800
    service.save_rule(NORKEVIN, {'id': rule['id'], 'photo_amount': None, 'photo_video_amount': None, 'level': 1})
    assert service.resolve(lead('Monterrico'))['photo_video_amount'] == 2500


@pytest.mark.parametrize('amounts', [
    {'photo_amount': 1000}, {'photo_amount': -1, 'photo_video_amount': 1500},
    {'photo_amount': True, 'photo_video_amount': 1500}, {'photo_amount': 'NaN', 'photo_video_amount': 1500},
    {'photo_amount': 1000.50, 'photo_video_amount': 1500}, {'photo_amount': 1000, 'photo_video_amount': 10000000},
])
def test_custom_rule_requires_two_valid_whole_quetzal_amounts(service, amounts):
    with pytest.raises(ValueError, match='ambos importes'):
        service.save_rule(NORKEVIN, dict(value='Destino nuevo', scope='region', approval_status='approved', **amounts))


def test_custom_rules_with_different_amounts_are_ambiguous(service):
    service.save_rule(NORKEVIN, dict(value='Playa alternativa', aliases=['Monterrico'], scope='region',
        department='Santa Rosa', photo_amount=1200, photo_video_amount=1800, approval_status='approved'))
    assert service.resolve(lead('Monterrico'))['confidence'] == 'ambiguous'
