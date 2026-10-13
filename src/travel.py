"""Tenant-isolated venue recognition and approved commercial travel policy.

Resolution is deterministic. External geography is opt-in research and always
creates a proposal; it cannot approve a venue or override the local catalogue.
"""
import hashlib
import json
import math
import os
import re
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from src.storage import MissingTenantContextError, TenantMismatchError
from src.tenant_brand_map import UnresolvedBrandError, resolve_brand


TRAVEL_LEVELS = {
    0: {'name': 'Exento', 'photo_amount': 0, 'photo_video_amount': 0},
    1: {'name': 'Cercano', 'photo_amount': 1500, 'photo_video_amount': 2500},
    2: {'name': 'Intermedio', 'photo_amount': 2000, 'photo_video_amount': 3500},
    3: {'name': 'Lejano', 'photo_amount': 2500, 'photo_video_amount': 4500},
}
SEED_PATH = Path(__file__).resolve().parents[1] / 'data' / 'seeds' / 'venues_guatemala.default.json'
_GEOCODE_LOCK = threading.Lock()
_GEOCODE_LAST_REQUEST = 0.0
_FOREIGN_COUNTRIES = (
    'argentina', 'mexico', 'estados unidos', 'united states', 'usa', 'espana',
    'spain', 'costa rica', 'el salvador', 'honduras', 'nicaragua', 'panama',
    'belice', 'belize', 'colombia', 'peru', 'chile', 'ecuador', 'brasil',
    'brazil', 'canada', 'italia', 'francia', 'alemania', 'republica dominicana',
    'puerto rico', 'venezuela', 'uruguay', 'paraguay', 'bolivia', 'portugal',
    'reino unido', 'inglaterra', 'japon', 'australia', 'suiza', 'cuba',
    'france', 'italy', 'germany', 'switzerland', 'japan', 'china', 'india',
    'paises bajos', 'holanda', 'netherlands', 'belgica', 'belgium', 'austria',
    'grecia', 'greece', 'turquia', 'turkey', 'noruega', 'norway', 'suecia',
    'sweden', 'dinamarca', 'denmark', 'finlandia', 'finland', 'islandia',
    'iceland', 'irlanda', 'ireland', 'polonia', 'poland', 'rumania', 'romania',
    'rusia', 'russia', 'ucrania', 'ukraine', 'croacia', 'croatia', 'serbia',
    'egipto', 'egypt', 'marruecos', 'morocco', 'sudafrica', 'south africa',
    'indonesia', 'tailandia', 'thailand', 'vietnam', 'filipinas', 'philippines',
    'corea del sur', 'south korea', 'nueva zelanda', 'new zealand', 'israel',
    'emiratos arabes unidos', 'united arab emirates', 'qatar', 'maldivas',
    'maldives', 'jamaica', 'haiti', 'bahamas', 'barbados', 'trinidad y tobago',
)
_DEPARTMENTS = {normalize_name for normalize_name in (
    'alta verapaz', 'baja verapaz', 'chimaltenango', 'chiquimula', 'el progreso',
    'escuintla', 'guatemala', 'huehuetenango', 'izabal', 'jalapa', 'jutiapa',
    'peten', 'quetzaltenango', 'quiche', 'retalhuleu', 'sacatepequez',
    'san marcos', 'santa rosa', 'solola', 'suchitepequez', 'totonicapan', 'zacapa',
)}


def normalize(value):
    text = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode().lower()
    text = re.sub(r'\b(?:sta)\.?\s', 'santa ', text)
    text = re.sub(r'\b(?:sto)\.?\s', 'santo ', text)
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _country(value):
    return 'GT' if normalize(value) in ('gt', 'gtm', 'guatemala', 'republica de guatemala') else str(value or '').strip()


def _url(value):
    value = str(value or '').strip()
    parsed = urlparse(value)
    if value and (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password):
        raise ValueError('La fuente debe ser una URL http(s) válida, sin credenciales.')
    return value


def _level(value, optional=False):
    if optional and value in ('', None):
        return None
    if isinstance(value, bool) or str(value) not in ('0', '1', '2', '3'):
        raise ValueError('El nivel debe ser 0, 1, 2 o 3; las tarifas son fijas.')
    return int(value)


def _aliases(value):
    if isinstance(value, str):
        value = re.split(r'[\n;|]', value)
    if not isinstance(value or [], list):
        raise ValueError('Los alias deben ser una lista.')
    return list(dict.fromkeys(str(item).strip() for item in (value or []) if str(item).strip()))[:60]


class TravelService:
    def __init__(self, store):
        self.store = store

    def _tenant(self, tenant_id):
        if not tenant_id or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', str(tenant_id)):
            raise MissingTenantContextError('Se requiere una empresa válida para viáticos.')
        active = self.store.current_tenant_id()
        in_request = self.store.request_context_probe and self.store.request_context_probe()
        if (active and active != tenant_id) or (in_request and not active):
            raise TenantMismatchError('Los viáticos pertenecen a otra empresa o falta la empresa activa.')
        return tenant_id

    def _norkevin(self, tenant_id):
        try:
            return resolve_brand(tenant_id).brand_key == 'norkevin'
        except UnresolvedBrandError:
            return False

    def _rows(self, table, tenant_id):
        self._tenant(tenant_id)
        return [row for row in self.store.list_strict(table) if row.get('tenant_id') == tenant_id]

    def _id(self, tenant_id, kind, key):
        return f'travel-{kind}-' + _hash([tenant_id, key])[:24]

    def _audit(self, tenant_id, action, before, after, actor=''):
        self.store.upsert('travel_audit', {
            'id': str(uuid.uuid4()), 'tenant_id': tenant_id, 'action': action,
            'actor': str(actor)[:200], 'at': _now(), 'before': before, 'after': after,
        })

    def settings(self, tenant_id):
        self._tenant(tenant_id)
        saved = self.store.get_tenant_dict('travel_settings', tenant_id=tenant_id)
        confirmed = self._norkevin(tenant_id)
        return {**saved, 'enabled': bool(saved.get('enabled')) and confirmed,
                'auto_send_packages': bool(saved.get('auto_send_packages')) and confirmed,
                'policy_confirmed': confirmed}

    def save_settings(self, tenant_id, data, actor=''):
        self._tenant(tenant_id)
        fields = ('enabled', 'auto_send_packages')
        if not any(field in data for field in fields):
            raise ValueError('Indica enabled o auto_send_packages.')
        for field in fields:
            if field in data and not isinstance(data[field], bool):
                raise ValueError(field + ' debe ser verdadero o falso.')
        if any(data.get(field) for field in fields) and not self._norkevin(tenant_id):
            raise ValueError('Esta marca todavía no tiene una política de viáticos confirmada.')
        table = f'travel_settings_{tenant_id}'
        # The settings document uses the existing tenant-specific dictionary storage.
        # Audit locking serializes this small admin operation within the current process.
        from src.storage import _lock_for_path
        with _lock_for_path(self.store._path(table)):
            before = self.settings(tenant_id)
            after = {**{field: data.get(field, before[field]) for field in fields},
                     'updated_at': _now(), 'updated_by': str(actor)[:200]}
            self.store.save_tenant_dict('travel_settings', after, tenant_id=tenant_id)
            self._audit(tenant_id, 'settings', before, after, actor)
        return self.settings(tenant_id)

    def ensure_catalog(self, tenant_id):
        self._tenant(tenant_id)
        seed = json.loads(SEED_PATH.read_text(encoding='utf-8')) if SEED_PATH.exists() else {'venues': []}
        metadata_revision = str(seed.get('metadata_revision') or '')
        with self.store.locked_tables('travel_venues', 'travel_rules', 'travel_audit'):
            existing_venues = self._rows('travel_venues', tenant_id)
            existing_ids = {row.get('seed_id'): row for row in existing_venues}
            for venue in seed.get('venues', []):
                seed_id = venue.get('id') or normalize(venue.get('name'))
                if seed_id in existing_ids:
                    current = existing_ids[seed_id]
                    if metadata_revision and current.get('seed_metadata_revision') != metadata_revision:
                        aliases = {normalize(alias): alias for alias in current.get('aliases', [])}
                        for alias in _aliases(venue.get('aliases')):
                            aliases.setdefault(normalize(alias), alias)
                        sources = list(dict.fromkeys(current.get('source_urls', []) +
                                                     [_url(url) for url in _aliases(venue.get('source_urls'))]))
                        updated = {**current, 'aliases': list(aliases.values()), 'source_urls': sources,
                                   'seed_metadata_revision': metadata_revision}
                        self.store.upsert('travel_venues', updated)
                        self._audit(tenant_id, 'seed_metadata', current, updated, 'catalog-seed')
                    continue
                record = self._validate_venue({**venue, 'seed_id': seed_id})
                record.update(id=self._id(tenant_id, 'venue', seed_id), tenant_id=tenant_id, seeded_at=_now())
                if metadata_revision:
                    record['seed_metadata_revision'] = metadata_revision
                if not self._norkevin(tenant_id):
                    record.update(approval_status='proposed', travel_level=None)
                # Seeding never overwrites a manually created/approved location.
                duplicate = next((row for row in existing_venues if self._same_venue(row, record)), None)
                if duplicate:
                    self.store.upsert('travel_venues', {'id': duplicate['id'], 'tenant_id': tenant_id, 'seed_id': seed_id})
                else:
                    self.store.upsert('travel_venues', record)
                    existing_venues.append(record)
            if self._norkevin(tenant_id):
                existing_rules = {row.get('seed_id') for row in self._rows('travel_rules', tenant_id)}
                for rule in self._initial_rules() + seed.get('proposed_destination_rules', []):
                    seed_id = rule.get('id') or normalize(rule.get('value') or rule.get('name') or rule.get('municipality') or rule.get('department'))
                    if seed_id in existing_rules:
                        continue
                    record = self._validate_rule(rule)
                    record.update(id=self._id(tenant_id, 'rule', seed_id), tenant_id=tenant_id, seed_id=seed_id, seeded_at=_now())
                    self.store.upsert('travel_rules', record)
                    existing_rules.add(seed_id)
            return {'venues': len(self._rows('travel_venues', tenant_id)), 'rules': len(self._rows('travel_rules', tenant_id))}

    @staticmethod
    def _initial_rules():
        municipalities = [
            ('Ciudad de Guatemala', 'Guatemala', 0, ['Guatemala', 'Guatemala City', 'Ciudad Guatemala']),
            ('Antigua Guatemala', 'Sacatepéquez', 0, ['Antigua']),
            ('San Lucas Sacatepéquez', 'Sacatepéquez', 0, []),
            ('Tecpán Guatemala', 'Chimaltenango', 0, ['Tecpán']),
            ('Panajachel', 'Sololá', 1, []),
            ('Santa Cruz La Laguna', 'Sololá', 1, ['Jaibalito']),
            ('San Lucas Tolimán', 'Sololá', 1, []),
            ('San Pedro La Laguna', 'Sololá', 1, []),
            ('San Juan La Laguna', 'Sololá', 1, []),
            ('San Marcos La Laguna', 'Sololá', 1, []),
            ('Santiago Atitlán', 'Sololá', 1, []),
            ('Santa Catarina Palopó', 'Sololá', 1, []),
            ('San Antonio Palopó', 'Sololá', 1, []),
        ]
        rules = [dict(id='municipality-' + normalize(name), name=name, value=name, department=department,
                      aliases=aliases, scope='municipality', level=level, approval_status='approved',
                      reason='Política comercial confirmada por el usuario; municipio/zona, no todo el departamento.')
                 for name, department, level, aliases in municipalities]
        rules += [dict(id='region-' + normalize(name), name=name, value=name, aliases=aliases, scope='region',
                       level=level, approval_status='approved', reason='Destino confirmado por el usuario; traslado habitual.')
                  for name, level, aliases in [('Lago de Atitlán', 1, ['Atitlán', 'Lago Atitlán']),
                                                ('Retalhuleu', 2, ['Reu', 'IRTRA Retalhuleu', 'IRTRA de Retalhuleu']),
                                                ('Petén', 3, ['Flores, Petén', 'Flores Petén'])]]
        return rules

    def catalog(self, tenant_id):
        self.ensure_catalog(tenant_id)
        return sorted(self._rows('travel_venues', tenant_id), key=lambda row: normalize(row.get('name')))

    def rules(self, tenant_id):
        self.ensure_catalog(tenant_id)
        return self._rows('travel_rules', tenant_id)

    def reviews(self, tenant_id):
        return sorted(self._rows('travel_reviews', tenant_id), key=lambda row: row.get('updated_at', ''), reverse=True)

    def _validate_venue(self, data):
        name = str(data.get('name') or '').strip()[:250]
        if not name:
            raise ValueError('El venue necesita un nombre.')
        record = {key: str(data.get(key) or '').strip()[:1000] for key in
                  ('municipality', 'department', 'locality', 'address', 'verified_at', 'classification_reason', 'verification_notes')}
        record.update(name=name, aliases=_aliases(data.get('aliases')), country=_country(data.get('country') or 'GT'),
                      travel_level=_level(data.get('travel_level'), optional=True),
                      source_url=_url(data.get('source_url')),
                      source_urls=[_url(url) for url in _aliases(data.get('source_urls'))],
                      verification_status=data.get('verification_status') or 'pending',
                      approval_status='proposed' if data.get('approval_status') == 'provisional' else data.get('approval_status') or 'proposed')
        if record['approval_status'] not in ('approved', 'proposed', 'rejected'):
            raise ValueError('Estado de aprobación inválido.')
        if record['verification_status'] not in ('verified', 'pending'):
            raise ValueError('Estado de verificación inválido.')
        if record['verification_status'] == 'verified':
            if record['country'] != 'GT' or not all(record[key] for key in ('source_url', 'municipality', 'department', 'address')):
                raise ValueError('Verificar requiere país GT, fuente, dirección, municipio y departamento.')
            record['verified_at'] = record['verified_at'] or _now()
        if record['approval_status'] == 'approved' and record['travel_level'] is None:
            raise ValueError('Aprobar una tarifa requiere uno de los cuatro niveles.')
        for key, maximum in (('latitude', 90), ('longitude', 180)):
            value = data.get(key)
            try:
                value = None if value in (None, '') else float(value)
            except (TypeError, ValueError):
                raise ValueError('Coordenadas inválidas.') from None
            if value is not None and (not math.isfinite(value) or abs(value) > maximum):
                raise ValueError('Coordenadas inválidas.')
            record[key] = value
        if data.get('seed_id'):
            record['seed_id'] = str(data['seed_id'])[:250]
        return record

    @staticmethod
    def _names(venue):
        return {normalize(name) for name in [venue.get('name'), *venue.get('aliases', [])] if normalize(name)}

    def _same_venue(self, a, b):
        if any(normalize(a.get(key)) != normalize(b.get(key)) for key in ('municipality', 'department', 'country')):
            return False
        names_a, names_b = self._names(a), self._names(b)
        return bool(names_a & names_b) or any(SequenceMatcher(None, x, y).ratio() >= .97 for x in names_a for y in names_b)

    def save_venue(self, tenant_id, data, actor=''):
        self._tenant(tenant_id)
        if data.get('tenant_id') and data['tenant_id'] != tenant_id:
            raise TenantMismatchError('El venue pertenece a otra empresa.')
        self.ensure_catalog(tenant_id)
        with self.store.locked_tables('travel_venues', 'travel_audit'):
            rows = self._rows('travel_venues', tenant_id)
            before = next((row for row in rows if row['id'] == data.get('id')), None)
            if data.get('id') and not before:
                raise ValueError('Venue inexistente en esta empresa.')
            record = self._validate_venue({**(before or {}), **data})
            duplicate = next((row for row in rows if row['id'] != data.get('id') and self._same_venue(row, record)), None)
            if duplicate:
                if before:
                    raise ValueError('Ya existe un venue equivalente en este municipio.')
                before = duplicate
                record['aliases'] = sorted(set(record['aliases'] + before.get('aliases', []) + [before['name']]))
                # A new spelling must not downgrade an approved venue implicitly.
                record = self._validate_venue({**before, **data, 'aliases': record['aliases']})
            if not self._norkevin(tenant_id) and record['approval_status'] == 'approved':
                raise ValueError('Esta marca no tiene una política comercial confirmada.')
            record.update(id=(before or {}).get('id') or self._id(tenant_id, 'venue', str(uuid.uuid4())),
                          tenant_id=tenant_id, updated_at=_now(), updated_by=str(actor)[:200])
            self.store.upsert('travel_venues', record)
            self._audit(tenant_id, 'venue', before, record, actor)
            return record

    def _validate_rule(self, data):
        scope = data.get('scope') or ('municipality' if data.get('municipality') else 'region')
        if scope not in ('municipality', 'region'):
            raise ValueError('La regla debe ser de municipio o región.')
        value = str(data.get('value') or data.get('name') or data.get('municipality') or data.get('department') or '').strip()[:250]
        status = 'proposed' if data.get('approval_status') == 'provisional' else data.get('approval_status') or 'proposed'
        if not value or status not in ('approved', 'proposed', 'rejected'):
            raise ValueError('Destino o estado de regla inválido.')
        return {'name': str(data.get('name') or value)[:250], 'value': value, 'scope': scope,
                'aliases': _aliases(data.get('aliases')), 'department': str(data.get('department') or '')[:150],
                'level': _level(data.get('level', data.get('travel_level'))), 'approval_status': status,
                'reason': str(data.get('reason') or data.get('classification_reason') or '')[:1500],
                'source_url': _url(data.get('source_url')), 'verified_at': str(data.get('verified_at') or '')[:100]}

    def save_rule(self, tenant_id, data, actor=''):
        self._tenant(tenant_id)
        if data.get('tenant_id') and data['tenant_id'] != tenant_id:
            raise TenantMismatchError('La regla pertenece a otra empresa.')
        self.ensure_catalog(tenant_id)
        with self.store.locked_tables('travel_rules', 'travel_audit'):
            rows = self._rows('travel_rules', tenant_id)
            before = next((row for row in rows if row['id'] == data.get('id')), None)
            if data.get('id') and not before:
                raise ValueError('Regla inexistente en esta empresa.')
            record = self._validate_rule({**(before or {}), **data})
            if not self._norkevin(tenant_id) and record['approval_status'] == 'approved':
                raise ValueError('Esta marca no tiene una política comercial confirmada.')
            duplicate = next((row for row in rows if row['id'] != data.get('id') and row['scope'] == record['scope']
                              and normalize(row['value']) == normalize(record['value'])
                              and normalize(row.get('department')) == normalize(record['department'])), None)
            if duplicate:
                if before:
                    raise ValueError('Ya existe una regla para ese destino.')
                before = duplicate
            record.update(id=(before or {}).get('id') or self._id(tenant_id, 'rule', str(uuid.uuid4())),
                          tenant_id=tenant_id, updated_at=_now(), updated_by=str(actor)[:200])
            self.store.upsert('travel_rules', record)
            self._audit(tenant_id, 'rule', before, record, actor)
            return record

    def resolve_review(self, tenant_id, review_id, venue_id, actor=''):
        self._tenant(tenant_id)
        with self.store.locked_tables('travel_reviews', 'travel_audit'):
            review = next((row for row in self._rows('travel_reviews', tenant_id) if row['id'] == review_id), None)
            venue = next((row for row in self._rows('travel_venues', tenant_id) if row['id'] == venue_id), None)
            if not review or not venue or venue['verification_status'] != 'verified' or venue['approval_status'] != 'approved':
                raise ValueError('La revisión requiere un venue verificado y aprobado de esta empresa.')
            after = {**review, 'status': 'resolved', 'venue_id': venue_id, 'updated_at': _now()}
            self.store.upsert('travel_reviews', after)
            self._audit(tenant_id, 'review', review, after, actor)
            return after

    @staticmethod
    def _context(lead):
        return {
            'location': str(next((lead.get(key) for key in ('locacion', 'ubicacion', 'location', 'lugar', 'venue') if lead.get(key)), '')).strip(),
            'municipality': str(lead.get('municipality') or lead.get('municipio') or '').strip(),
            'department': str(lead.get('department') or lead.get('departamento') or '').strip(),
            'country': str(lead.get('country') or lead.get('pais') or '').strip(),
            'notes': str(lead.get('notes') or lead.get('notas') or '').strip(),
        }

    def _international(self, context):
        country = context['country']
        if country and _country(country) != 'GT':
            return country
        location, notes = normalize(context['location']), normalize(context['notes'])
        for country in _FOREIGN_COUNTRIES:
            if re.search(r'\b' + re.escape(country) + r'\b', location):
                return country
            if re.search(r'\b(?:boda|evento|casamos|casaremos|destino|ceremonia)\b.{0,45}\ben\s+' + re.escape(country) + r'\b', notes):
                return country
        return ''

    @staticmethod
    def _segments(context):
        # Only explicit address components are geographical evidence. A hotel
        # containing "Antigua" in its name is not evidence of its municipality.
        segments = []
        for part in re.split(r'[,;/()\n]+', context['location']):
            name = normalize(part)
            department = re.sub(r'^(?:boda en )?(?:(?:departamento|depto) de )?', '', name)
            if department in _DEPARTMENTS:
                name = department
            if name:
                segments.append(name)
        return segments

    def _match_venues(self, venues, context):
        location = normalize(context['location'])
        segments = self._segments(context)
        # A registered name may contain parentheses or commas. Only consider
        # complete address components, never unrestricted substring prefixes.
        address_parts = re.split(r'[,;/\n]+', context['location'])
        name_segments = segments + [normalize(','.join(address_parts[:end])) for end in range(1, len(address_parts) + 1)]
        matches = [venue for venue in venues if location in self._names(venue) or any(part in self._names(venue) for part in name_segments)]
        if not matches:
            return []
        for field in ('municipality', 'department'):
            explicit = normalize(context[field])
            if explicit:
                matches = [venue for venue in matches if normalize(venue.get(field)) == explicit
                           or (venue['verification_status'] == 'pending' and not normalize(venue.get(field)))]
        if len(matches) > 1:
            geo = ' '.join(segments[1:])
            narrowed = [v for v in matches if any(normalize(v.get(field)) and normalize(v[field]) in geo for field in ('municipality', 'department'))]
            if narrowed:
                matches = narrowed
        if len(matches) > 1:
            notes = normalize(context['notes'])
            narrowed = [v for v in matches if any(normalize(v.get(field)) and re.search(r'\b' + re.escape(normalize(v[field])) + r'\b', notes)
                                                 for field in ('municipality', 'department'))]
            if narrowed:
                matches = narrowed
        return matches

    def _match_rules(self, rules, context, venue=None):
        municipality = normalize((venue or {}).get('municipality') or context['municipality'])
        department = normalize((venue or {}).get('department') or context['department'])
        segments = self._segments(context)
        if venue:
            segments += [normalize(venue.get('municipality')), normalize(venue.get('department'))]
        # Ignore the trailing country Guatemala; otherwise every national address
        # would accidentally become Ciudad de Guatemala and therefore exempt.
        if len(segments) > 1 and segments[-1] in ('guatemala', 'gt'):
            segments.pop()
        elif segments == ['guatemala'] and not municipality and not venue:
            segments = []
        candidates = []
        for rule in rules:
            if rule.get('approval_status') == 'rejected':
                continue
            names = {normalize(rule['value']), *(normalize(alias) for alias in rule.get('aliases', []))}
            if rule['scope'] == 'municipality':
                if department and rule.get('department') and normalize(rule['department']) != department:
                    continue
                if municipality:
                    matched = municipality in names
                else:
                    matched = any(part in names for part in segments)
            else:
                matched = department in names or any(part in names for part in segments)
            if matched:
                candidates.append(rule)
        municipal = [rule for rule in candidates if rule['scope'] == 'municipality']
        regional = [rule for rule in candidates if rule['scope'] == 'region']
        approved_regions = {normalize(rule['value']) for rule in regional if rule['approval_status'] == 'approved'}
        municipal = [rule for rule in municipal if rule['approval_status'] == 'approved'
                     or normalize(rule['value']) not in approved_regions]
        return municipal or regional

    def _venue_conflict(self, venue, context, venues, rules):
        if normalize(context['location']) in self._names(venue):
            return False
        address_parts = re.split(r'[,;/\n]+', context['location'])
        name_end = max((end for end in range(1, len(address_parts) + 1)
                        if normalize(','.join(address_parts[:end])) in self._names(venue)), default=0)
        address_parts = [part for part in address_parts[name_end:] if normalize(part) not in self._names(venue)]
        segments = self._segments({**context, 'location': ', '.join(address_parts)})
        if segments and segments[-1] in ('guatemala', 'gt'):
            segments.pop()
        municipality, department = normalize(venue['municipality']), normalize(venue['department'])
        municipalities = {normalize(v['municipality']) for v in venues}
        compatible_hints = set()
        if venue['verification_status'] == 'pending' and not municipality:
            compatible_hints.update(normalize(v['municipality']) for v in venues
                                    if not department or normalize(v['department']) == department)
            for rule in rules:
                if rule['scope'] == 'municipality' and (not department or normalize(rule.get('department')) == department):
                    compatible_hints.update({normalize(rule['value']), *(normalize(alias) for alias in rule.get('aliases', []))})
        geographical_names = _DEPARTMENTS | municipalities | {normalize(venue.get('locality'))}
        for rule in rules:
            geographical_names.update({normalize(rule['value']), *(normalize(alias) for alias in rule.get('aliases', []))})
        address_words = set(normalize(venue.get('address')).split())
        for part in segments:
            if part in self._names(venue):
                continue
            if part in _DEPARTMENTS and department and part != department:
                return True
            if part in compatible_hints:
                continue
            if part in municipalities and part not in (municipality, department):
                return True
            for rule in rules:
                names = {normalize(rule['value']), *(normalize(alias) for alias in rule.get('aliases', []))}
                if rule['scope'] == 'municipality' and part in names and municipality not in names and part != department:
                    return True
                if rule['scope'] == 'region' and part in names and part != department:
                    local_rule = self._match_rules([rule], {'location': '', 'municipality': '', 'department': department}, venue)
                    lake = normalize(rule['value']) == 'lago de atitlan' and department == 'solola'
                    lake_municipalities = [r for r in self._initial_rules() if r['scope'] == 'municipality' and r['level'] == 1]
                    local_lake = lake and self._match_rules(lake_municipalities, {'location': '', 'municipality': '', 'department': department}, venue)
                    if not local_rule and not local_lake:
                        return True
            if part not in geographical_names and part not in ('guatemala', 'gt', 'centroamerica', 'c a'):
                # An unexplained suffix may be a foreign country/city. Keep the
                # match reviewable instead of guessing a national destination.
                known_address = set(part.split()) <= address_words
                route = re.match(r'^(km|kilometro|rn|ca|ruta|carretera|calle|avenida|zona)\b', part)
                if not known_address and not route:
                    return True
        return False

    def resolve(self, lead, research=False):
        tenant_id = self._tenant(lead.get('tenant_id'))
        self.ensure_catalog(tenant_id)
        settings = self.settings(tenant_id)
        context = self._context(lead)
        result = {
            'original_location': context['location'], 'venue_id': None, 'venue_name': '',
            'normalized_location': '', 'level': None, 'photo_amount': None, 'photo_video_amount': None,
            'confidence': 'unknown', 'reason': 'Ubicación pendiente de verificación.',
            'requires_review': True, 'applicable': settings['policy_confirmed'], 'enabled': settings['enabled'],
            'definitive': False, 'country': '', 'rule_id': None,
        }
        international = self._international(context)
        if international:
            result.update(confidence='international', country=international,
                          reason='Destino internacional indicado expresamente; logística por confirmar, sin tarifa nacional.')
        else:
            venues = self._rows('travel_venues', tenant_id)
            rules = self._rows('travel_rules', tenant_id)
            matches = self._match_venues(venues, context)
            review_key = {key: normalize(value) for key, value in context.items() if key != 'notes'}
            reviewed = next((row for row in self._rows('travel_reviews', tenant_id)
                             if row['id'] == self._id(tenant_id, 'review', review_key) and row.get('status') == 'resolved'), None)
            if reviewed:
                approved_venue = next((v for v in venues if v['id'] == reviewed.get('venue_id')
                                       and v['verification_status'] == 'verified' and v['approval_status'] == 'approved'), None)
                if approved_venue:
                    matches = [approved_venue]
            raw_matches = self._match_venues(venues, {**context, 'municipality': '', 'department': '', 'notes': ''})
            if raw_matches and not matches:
                result.update(confidence='ambiguous', reason='El municipio o departamento indicado contradice el venue reconocido; confirmar ubicación.')
            elif len(matches) == 1 and self._venue_conflict(matches[0], context, venues, rules):
                result.update(confidence='ambiguous', reason='La dirección escrita contradice la ubicación verificada del venue; confirmar municipio y departamento.')
            elif len(matches) > 1:
                result.update(confidence='ambiguous', reason='Varios venues coinciden; confirmar municipio o dirección.',
                              candidates=[{'id': v['id'], 'name': v['name'], 'municipality': v['municipality']} for v in matches])
            elif matches:
                venue = matches[0]
                result.update(venue_id=venue['id'], venue_name=venue['name'], country=venue['country'],
                              normalized_location=', '.join(dict.fromkeys(filter(None, [venue['name'], venue.get('locality'), venue['municipality'], venue['department'], 'Guatemala']))),
                              source_url=venue.get('source_url'), verification_status=venue['verification_status'],
                              approval_status=venue['approval_status'],
                              candidates=[{'id': venue['id'], 'name': venue['name'], 'municipality': venue['municipality'], 'source_url': venue.get('source_url')}])
                if venue['verification_status'] != 'verified':
                    result.update(confidence='probable', reason='Venue identificado, ubicación todavía pendiente de verificar.')
                    # A department written by the client can use its approved
                    # regional policy without trusting a pending venue's metadata.
                    segments = self._segments(context)
                    if len(segments) > 1 and segments[-1] in ('guatemala', 'gt'):
                        segments.pop()
                    departments = ({normalize(context['department'])} if context['department'] else
                                   {part for part in segments if part in _DEPARTMENTS})
                    if len(departments) == 1:
                        regional = [rule for rule in rules if rule['scope'] == 'region' and rule['approval_status'] == 'approved']
                        explicit = {'location': '', 'municipality': '', 'department': next(iter(departments))}
                        self._resolve_rules(result, self._match_rules(regional, explicit))
                    if research and not result['definitive']:
                        search_context = {**context, 'department': context['department'] or venue.get('department', '')}
                        suggestions, error = self._research(tenant_id, search_context, pending_venue_id=venue['id'])
                        candidates = {item['id']: item for item in result['candidates'] + suggestions}
                        result.update(candidates=list(candidates.values()),
                                      confidence='ambiguous' if len(candidates) > 1 else 'probable',
                                      reason=error or 'Fuentes geográficas consultadas; el venue requiere verificación y aprobación comercial.')
                elif venue['approval_status'] == 'approved' and venue.get('travel_level') is not None:
                    self._apply(result, venue['travel_level'], 'Excepción/clasificación específica aprobada del venue. ' + venue.get('classification_reason', ''))
                elif venue['approval_status'] == 'rejected':
                    result.update(confidence='verified', reason='La ubicación está verificada; su clasificación comercial está rechazada.')
                else:
                    # Explicit provisional venue classifications must be approved,
                    # even if a broader geographical rule happens to match.
                    if venue.get('travel_level') is not None:
                        result.update(confidence='verified', suggested_level=venue['travel_level'], reason='Ubicación verificada; clasificación comercial pendiente de aprobación.')
                    else:
                        result.update(confidence='verified', reason='Ubicación verificada; falta una regla comercial aprobada.')
                        self._resolve_rules(result, self._match_rules(rules, context, venue))
            else:
                matched_rules = self._match_rules(rules, context)
                if matched_rules:
                    self._resolve_rules(result, matched_rules)
                else:
                    location = normalize(context['location'])
                    probable = [v for v in venues if location and max((SequenceMatcher(None, location, name).ratio() for name in self._names(v)), default=0) >= .85]
                    if probable:
                        result.update(confidence='probable', reason='Nombre parecido a un venue; confirmar antes de aplicar viáticos.',
                                      candidates=[{'id': v['id'], 'name': v['name'], 'municipality': v['municipality']} for v in probable[:5]])
                    elif research and location:
                        suggestions, error = self._research(tenant_id, context)
                        result.update(confidence='ambiguous' if len(suggestions) > 1 else 'probable' if suggestions else 'unknown',
                                      candidates=suggestions, reason=error or 'Ubicación geográfica propuesta; requiere verificación y aprobación comercial.')
            if result['level'] == 3 and re.search(r'\b(vuelo|vuelos|avion|aereo|helicoptero|hospedaje extraordinario|logistica especial)\b', normalize(context['location'] + ' ' + context['notes'])):
                result.update(requires_review=True, definitive=False, reason='Petén: vuelo, hospedaje extraordinario o logística especial por confirmar.')
            if not settings['policy_confirmed']:
                result.update(level=None, photo_amount=None, photo_video_amount=None, definitive=False, requires_review=True,
                              reason=result['reason'] + ' Esta marca no tiene una política comercial confirmada.')
        if result['requires_review']:
            self._record_review(tenant_id, lead, context, result)
        result['fingerprint'] = _hash({'context': context, 'result': result})
        return result

    def _apply(self, result, level, reason):
        result.update(level=level, **{key: value for key, value in TRAVEL_LEVELS[level].items() if key != 'name'},
                      confidence='verified', country='GT', requires_review=False, definitive=True, reason=reason)

    def _resolve_rules(self, result, rules):
        approved = [rule for rule in rules if rule['approval_status'] == 'approved']
        if len({rule['level'] for rule in rules}) > 1:
            result.update(confidence='ambiguous', reason='Reglas de destino contradictorias; confirmar la ubicación antes de cotizar.')
        elif approved:
            rule = approved[0]
            result.update(rule_id=rule['id'], normalized_location=result['normalized_location'] or f"{rule['value']}, Guatemala", approval_status='approved')
            self._apply(result, rule['level'], 'Regla aprobada de ' + ('municipio: ' if rule['scope'] == 'municipality' else 'región: ') + rule['value'] + '. ' + rule.get('reason', ''))
        elif rules:
            result.update(confidence='probable', suggested_level=rules[0]['level'], reason='Destino con nivel propuesto; pendiente de aprobación comercial.')

    def _record_review(self, tenant_id, lead, context, result):
        key = {key: normalize(value) for key, value in context.items() if key != 'notes'}
        review_id = self._id(tenant_id, 'review', key)
        with self.store.locked_tables('travel_reviews'):
            before = next((row for row in self._rows('travel_reviews', tenant_id) if row['id'] == review_id), {})
            lead_ids = list(dict.fromkeys(before.get('lead_ids', []) + ([str(lead['id'])] if lead.get('id') else [])))
            # Lead reloads resolve without research. Retain the research choices
            # so the admin can still inspect them after that ordinary page read.
            candidates = {item['id']: item for item in before.get('candidates', []) + result.get('candidates', [])}
            record = {**before, 'id': review_id, 'tenant_id': tenant_id, 'original_location': context['location'],
                      'context': {key: value for key, value in context.items() if key != 'notes'},
                      'confidence': result['confidence'], 'reason': result['reason'], 'candidates': list(candidates.values()),
                      'lead_ids': lead_ids, 'status': 'pending', 'created_at': before.get('created_at') or _now()}
            if any(record.get(key) != before.get(key) for key in record):
                record['updated_at'] = _now()
                self.store.upsert('travel_reviews', record)

    def _enrich_pending(self, tenant_id, venue_id, proposal):
        """A single compatible suggestion may fill gaps, never approve or replace evidence."""
        if not venue_id:
            return None
        with self.store.locked_tables('travel_venues', 'travel_audit'):
            current = next((v for v in self._rows('travel_venues', tenant_id) if v['id'] == venue_id), None)
            if not current or current['verification_status'] != 'pending' or current['approval_status'] != 'proposed':
                return None
            if normalize(proposal['name']) not in self._names(current):
                return None
            if any(current.get(key) and normalize(current[key]) != normalize(proposal.get(key))
                   for key in ('country', 'department', 'municipality')):
                return None
            updates = {key: proposal[key] for key in ('municipality', 'department', 'address', 'latitude', 'longitude', 'source_url')
                       if current.get(key) in (None, '') and proposal.get(key) not in (None, '')}
            sources = list(dict.fromkeys(filter(None, current.get('source_urls', []) + [current.get('source_url'), proposal.get('source_url')])))
            if sources != current.get('source_urls', []):
                updates['source_urls'] = sources
            return self.save_venue(tenant_id, {'id': venue_id, **updates}, actor='geography-research') if updates else current

    def _research(self, tenant_id, context, pending_venue_id=None):
        global _GEOCODE_LAST_REQUEST
        query = ', '.join(filter(None, [context['location'], context['municipality'], context['department'], 'Guatemala']))[:500]
        if re.search(r'@|https?://|www\.|(?:\+?\d[\s().-]*){7,}', query, re.I):
            return [], 'La ubicación contiene datos de contacto o enlaces; corregir el lugar antes de investigar.'
        cache_id = self._id(tenant_id, 'cache', normalize(query))
        with self.store.locked_tables('travel_cache'):
            cached = next((row for row in self._rows('travel_cache', tenant_id) if row['id'] == cache_id), None)
            ttl = 60 if cached and cached.get('error') else 86400 * 30
            if cached and time.time() - cached.get('cached_at', 0) < ttl:
                return cached['candidates'], cached.get('error', '')
            # ponytail: process-wide throttle matches the single Gunicorn worker;
            # use a shared limiter before scaling to multiple server processes.
            with _GEOCODE_LOCK:
                if time.monotonic() - _GEOCODE_LAST_REQUEST < 15:
                    return [], 'Consulta geográfica aplazada; vuelva a investigar en unos segundos.'
                _GEOCODE_LAST_REQUEST = time.monotonic()
            error, suggestions = '', []
            try:
                rows = self._geocode(query)
                if not isinstance(rows, list):
                    raise ValueError('Respuesta geográfica inválida')
                rows = [row for row in rows[:5] if isinstance(row, dict) and isinstance(row.get('address'), dict)
                        and normalize(row['address'].get('country_code')) == 'gt']
                for row in rows:
                    address = row['address']
                    name = str(row.get('name') or context['location'])[:250]
                    source = 'https://www.openstreetmap.org/'
                    if row.get('osm_type') in ('node', 'way', 'relation') and str(row.get('osm_id', '')).isdigit():
                        source += f"{row['osm_type']}/{row['osm_id']}"
                    proposal = {'name': name, 'aliases': [context['location']], 'country': 'GT',
                                'municipality': address.get('city') or address.get('town') or address.get('municipality') or address.get('village') or '',
                                'department': address.get('state') or '', 'address': str(row.get('display_name') or '')[:1000],
                                'source_url': source, 'latitude': row.get('lat'), 'longitude': row.get('lon'),
                                'verification_status': 'pending', 'approval_status': 'proposed',
                                'classification_reason': 'Sugerencia geográfica externa; pendiente de verificación humana.'}
                    matched = self._match_rules(self._rows('travel_rules', tenant_id), context, proposal)
                    proposal['travel_level'] = matched[0]['level'] if matched and len({rule['level'] for rule in matched}) == 1 else None
                    # Do not save an externally suggested duplicate over local evidence.
                    enriched = self._enrich_pending(tenant_id, pending_venue_id, proposal) if len(rows) == 1 else None
                    known = enriched or next((venue for venue in self._rows('travel_venues', tenant_id) if self._same_venue(venue, proposal)), None)
                    if known:
                        saved = known
                    else:
                        saved = self.save_venue(tenant_id, proposal, actor='geography-research')
                    suggestions.append({'id': saved['id'], 'name': saved['name'], 'municipality': saved['municipality'], 'source_url': saved['source_url']})
                if not suggestions:
                    error = 'No se encontró una coincidencia confiable dentro de Guatemala; ubicación pendiente.'
            except (OSError, ValueError, TypeError, KeyError):
                error = 'El servicio geográfico no pudo verificar el destino; ubicación pendiente.'
            self.store.upsert('travel_cache', {'id': cache_id, 'tenant_id': tenant_id, 'cached_at': time.time(),
                                               'candidates': suggestions, 'error': error})
            return suggestions, error

    @staticmethod
    def _geocode(query):
        endpoint = os.environ.get('FLOW_GEOCODER_URL', 'https://nominatim.openstreetmap.org/search').strip()
        parsed = urlparse(_url(endpoint))
        if parsed.scheme != 'https':
            raise ValueError('El proveedor geográfico debe usar HTTPS.')
        url = endpoint + ('&' if parsed.query else '?') + urlencode({
            'q': query, 'format': 'jsonv2', 'addressdetails': 1, 'countrycodes': 'gt', 'limit': 5,
        })
        user_agent = os.environ.get('FLOW_GEOCODER_USER_AGENT', 'FlowingCRM/1.0 (https://flowingcrm.com; venue verification)')
        request = Request(url, headers={'User-Agent': user_agent, 'Accept': 'application/json'})
        with urlopen(request, timeout=4) as response:
            return json.loads(response.read(250000).decode('utf-8'))
