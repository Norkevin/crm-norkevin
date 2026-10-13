"""The supplied venue list is evidence to review, never an approval to charge."""
import json
from pathlib import Path

from src.storage import JsonStore
from src.travel import SEED_PATH, TravelService, normalize


def test_user_list_crosswalk_preserves_every_row_and_existing_policy():
    seed = json.loads(SEED_PATH.read_text())
    crosswalk = json.loads((Path(__file__).resolve().parents[1] / 'docs/viaticos-listado-cruce.json').read_text())
    venues = {venue['id']: venue for venue in seed['venues']}
    assert len(venues) == len(seed['venues']) == 166
    assert len(crosswalk['rows']) == 141
    assert len({row['excel_row'] for row in crosswalk['rows']}) == 141
    existing = [row for row in crosswalk['rows'] if row['seed_id']]
    pending = [row for row in crosswalk['rows'] if row['candidate']]
    assert (len(existing), len(pending)) == (23, 118)
    assert sum(row['source']['historical'] for row in pending) == 50
    assert sum(v['verification_status'] == 'verified' for v in venues.values()) == 48
    assert sum(v['approval_status'] == 'approved' for v in venues.values()) == 35
    for row in existing:
        venue = venues[row['seed_id']]
        assert normalize(row['input_name']) in TravelService._names(venue)
        assert all(venue[field] == value for field, value in row['existing_policy'].items())
    for row in pending:
        venue = venues[row['candidate']['id']]
        assert (venue['name'], venue['department']) == (row['input_name'], row['input_department'])
        assert venue['source_url'] == row['source']['url']
        assert f"Fuentes fila {row['source']['excel_row']}" in venue['verification_notes']
        assert row['source']['type'] in venue['verification_notes']
        assert venue['verification_status'] == 'pending' and venue['approval_status'] == 'proposed'
        assert all(venue[field] is None for field in ('municipality', 'address', 'latitude', 'longitude', 'verified_at', 'travel_level'))


def test_every_imported_pending_venue_is_recognized_without_assigning_fees(tmp_path, monkeypatch):
    service = TravelService(JsonStore(str(tmp_path / 'store')))
    tenant = 'tenant-norkevin-photography'
    service.save_settings(tenant, {'enabled': True})
    def deny_lookup(query):
        raise AssertionError('Reading a pending venue must not query external services')
    monkeypatch.setattr(service, '_geocode', deny_lookup)
    pending = [venue for venue in service.catalog(tenant) if venue['verification_status'] == 'pending']
    assert len(pending) == 118
    for venue in pending:
        result = service.resolve({'tenant_id': tenant, 'locacion': venue['name']})
        assert result['venue_id'] == venue['id'], (venue['name'], result)
        assert result['enabled'] and result['requires_review'] and not result['definitive']
        assert result['level'] is None and result['photo_amount'] is None and result['photo_video_amount'] is None
