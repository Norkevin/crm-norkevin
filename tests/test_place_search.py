import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError

import pytest

from src import place_search


@pytest.fixture(autouse=True)
def isolated_search(monkeypatch, tmp_path):
    monkeypatch.setattr(place_search, 'DATA_DIR', str(tmp_path))
    monkeypatch.setenv('FLOW_GOOGLE_PLACES_ENABLED', '1')
    monkeypatch.setenv('GOOGLE_PLACES_API_KEY', 'test-private-key')
    monkeypatch.setenv('GOOGLE_PLACES_DAILY_LIMIT', '100')
    monkeypatch.setattr(place_search, 'urlopen', lambda *a, **kw: pytest.fail('Real Google requests are forbidden in tests'))


def test_google_is_opt_in_and_never_calls_network_when_disabled(monkeypatch):
    monkeypatch.delenv('FLOW_GOOGLE_PLACES_ENABLED')
    assert not place_search.google_places_enabled()
    with pytest.raises(place_search.SearchUnavailable):
        place_search.google_place_suggestions('Jardin Vilaflor')


@pytest.mark.parametrize('query', [None, [], 123, '', 'ab', 'x' * 161, 'cliente@example.com', '+502 1234 5678'])
def test_invalid_or_personal_queries_do_not_reach_google(query):
    with pytest.raises(ValueError):
        place_search.google_place_suggestions(query)


def test_real_google_request_contract_and_safe_projection(monkeypatch):
    calls = []
    class Reply:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, limit):
            return json.dumps({'suggestions': [{'placePrediction': {
                'text': {'text': 'Jardín Vilaflor, Sacatepéquez, Guatemala'},
                'structuredFormat': {'mainText': {'text': 'Jardín Vilaflor'},
                                     'secondaryText': {'text': 'Sacatepéquez, Guatemala'}},
            }}]}).encode()
    def fake(request, timeout):
        calls.append(request)
        assert timeout == 3
        assert request.full_url == 'https://places.googleapis.com/v1/places:autocomplete'
        assert request.get_method() == 'POST'
        assert 'test-private-key' not in request.full_url
        assert request.get_header('X-goog-api-key') == 'test-private-key'
        body = json.loads(request.data)
        assert body['input'] == 'Jardin Vilaflor'
        assert body['languageCode'] == 'es'
        assert body['regionCode'] == 'gt'
        assert body['locationBias']['rectangle']['low']['longitude'] == -92.3
        return Reply()
    monkeypatch.setattr(place_search, 'urlopen', fake)
    expected = [{'name': 'Jardín Vilaflor', 'detail': 'Sacatepéquez, Guatemala',
                 'label': 'Jardín Vilaflor, Sacatepéquez, Guatemala', 'provider': 'google'}]
    assert place_search.google_place_suggestions('Jardin Vilaflor') == expected
    assert place_search.google_place_suggestions('Jardin Vilaflor') == expected
    assert len(calls) == 2, 'Google predictions are not cached'
    with sqlite3.connect(str(place_search.Path(place_search.DATA_DIR) / 'google_places_usage.sqlite3')) as db:
        assert db.execute('SELECT requests FROM usage').fetchone() == (2,)
        assert db.execute('PRAGMA table_info(usage)').fetchall()[0][1] == 'day'


def test_budget_survives_restarts_and_is_atomic_across_threads(monkeypatch):
    monkeypatch.setenv('GOOGLE_PLACES_DAILY_LIMIT', '3')
    def reserve(_):
        try:
            place_search._reserve_lookup()
            return True
        except place_search.SearchLimitReached:
            return False
    with ThreadPoolExecutor(max_workers=6) as executor:
        assert sum(executor.map(reserve, range(9))) == 3
    with pytest.raises(place_search.SearchLimitReached):
        place_search._reserve_lookup()
    monkeypatch.setattr(place_search, '_budget_day', lambda: '2099-01-01')
    place_search._reserve_lookup()


def test_provider_errors_do_not_expose_query_or_credentials(monkeypatch, caplog):
    def fail(*a, **kw):
        raise HTTPError('https://example.invalid/?key=test-private-key', 403, 'Jardin Vilaflor', {}, None)
    monkeypatch.setattr(place_search, 'urlopen', fail)
    with pytest.raises(place_search.SearchUnavailable):
        place_search.google_place_suggestions('Jardin Vilaflor')
    assert 'test-private-key' not in caplog.text
    assert 'Jardin Vilaflor' not in caplog.text


def test_disabled_public_endpoint_is_safe_without_login(client, monkeypatch):
    monkeypatch.delenv('FLOW_GOOGLE_PLACES_ENABLED')
    response = client.post('/api/captacion/places', json={
        'tenant_slug': 'astral-weddings', 'query': 'Jardin Vilaflor',
    })
    assert response.status_code == 503
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'test-private-key' not in response.get_data(as_text=True)


@pytest.mark.parametrize('payload', [[], {'query': None}, {'query': 'ab'},
    {'query': 'Jardin Vilaflor', 'tenant_slug': 'unknown-brand'},
    {'query': 'Jardin Vilaflor', 'tenant_slug': []}])
def test_public_endpoint_validates_before_calling_google(client, payload):
    response = client.post('/api/captacion/places', json=payload)
    assert response.status_code in (400, 404)


def test_public_endpoint_returns_only_places_and_no_store(client, monkeypatch):
    import app as module
    items = [{'name': 'Jardín Vilaflor', 'detail': 'Sacatepéquez, Guatemala',
              'label': 'Jardín Vilaflor, Sacatepéquez, Guatemala', 'provider': 'google'}]
    monkeypatch.setattr(module, 'google_place_suggestions', lambda query: items)
    response = client.post('/api/captacion/places', json={
        'tenant_slug': 'astral-weddings', 'query': 'Jardin Vilaflor',
    })
    assert response.status_code == 200
    assert response.get_json() == {'ok': True, 'items': items}
    assert response.headers['Cache-Control'] == 'no-store'
