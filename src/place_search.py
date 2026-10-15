"""Opt-in Google Places autocomplete; no credentials or predictions reach storage."""
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
from contextlib import closing
from datetime import datetime
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from src.storage import DATA_DIR

logger = logging.getLogger(__name__)
_SLOTS = threading.BoundedSemaphore(2)


class SearchUnavailable(Exception):
    pass


class SearchLimitReached(Exception):
    pass


def google_places_enabled():
    return os.environ.get('FLOW_GOOGLE_PLACES_ENABLED') == '1' and bool(os.environ.get('GOOGLE_PLACES_API_KEY', '').strip())


def valid_place_query(query):
    return isinstance(query, str) and 3 <= len(query.strip()) <= 160 and not re.search(
        r'@|https?://|(?:\d[\s()-]*){7,}', query, re.I)


def _budget_day():
    return datetime.now(ZoneInfo('America/Guatemala')).date().isoformat()


def _reserve_lookup():
    """Count attempts atomically on the persistent disk, across restarts/workers."""
    limit = int(os.environ.get('GOOGLE_PLACES_DAILY_LIMIT', '100'))
    day = _budget_day()
    with closing(sqlite3.connect(str(Path(DATA_DIR) / 'google_places_usage.sqlite3'), timeout=0.2)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, requests INTEGER NOT NULL)')
        db.execute('INSERT OR IGNORE INTO usage VALUES (?, 0)', (day,))
        changed = db.execute('UPDATE usage SET requests = requests + 1 WHERE day = ? AND requests < ?', (day, limit))
        if changed.rowcount != 1:
            raise SearchLimitReached()


def google_place_suggestions(query):
    if not valid_place_query(query):
        raise ValueError('Invalid place query')
    if not google_places_enabled():
        raise SearchUnavailable()
    # ponytail: cap concurrent lookups so a slow provider cannot occupy all web threads.
    if not _SLOTS.acquire(blocking=False):
        raise SearchLimitReached()
    try:
        _reserve_lookup()
        body = json.dumps({
            'input': query.strip(), 'languageCode': 'es', 'regionCode': 'gt',
            'locationBias': {'rectangle': {
                'low': {'latitude': 13.7, 'longitude': -92.3},
                'high': {'latitude': 17.9, 'longitude': -88.1},
            }},
        }).encode('utf-8')
        request = Request('https://places.googleapis.com/v1/places:autocomplete', data=body, headers={
            'Content-Type': 'application/json',
            'X-Goog-Api-Key': os.environ['GOOGLE_PLACES_API_KEY'].strip(),
            'X-Goog-FieldMask': 'suggestions.placePrediction.text.text,suggestions.placePrediction.structuredFormat',
        }, method='POST')
        with urlopen(request, timeout=3) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise SearchUnavailable()
        data = json.loads(raw)
        items = []
        for entry in data.get('suggestions', [])[:5]:
            prediction = entry.get('placePrediction', {})
            label = prediction.get('text', {}).get('text', '')
            structured = prediction.get('structuredFormat', {})
            name = structured.get('mainText', {}).get('text') or label.split(',')[0]
            detail = structured.get('secondaryText', {}).get('text', '')
            if name and label:
                items.append({'name': name, 'detail': detail, 'label': label, 'provider': 'google'})
        return items
    except SearchLimitReached:
        raise
    except Exception as error:
        # Never log a visitor's query, API credentials or the provider's error body.
        logger.warning('Google Places unavailable (%s)', type(error).__name__)
        raise SearchUnavailable() from None
    finally:
        _SLOTS.release()
