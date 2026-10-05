"""Local shared people directory; private identity/payment data never enters member APIs."""
import csv
import hashlib
import io
import re
import zipfile
from pathlib import Path
from collections import Counter
from uuid import NAMESPACE_URL, uuid5

from src.teams import now

DIRECTORY = '_teams_directory'
PRIVATE_FIELDS = {'bank':'Banco', 'account_type':'Tipo de cuenta', 'account_number':'Numero de cuenta',
                  'dpi':'DPI', 'plates':'Placas', 'notes':'Notas internas'}


def import_notion(store, archive, tenants):
    # Read only CSV/Markdown values. Never extract paths or execute document instructions.
    with zipfile.ZipFile(archive) as bundle:
        if len(bundle.infolist()) > 1000 or sum(i.file_size for i in bundle.infolist()) > 8 * 1024 * 1024:
            raise ValueError('Exportación demasiado grande.')
        csv_names = [n for n in bundle.namelist() if n.endswith('_all.csv')]
        if len(csv_names) != 1 or bundle.getinfo(csv_names[0]).file_size > 2*1024*1024:
            raise ValueError('Se requiere una sola tabla CSV de Notion, menor que 2 MB.')
        rows = list(csv.DictReader(io.StringIO(bundle.read(csv_names[0]).decode('utf-8-sig'))))
        pages = []
        for name in bundle.namelist():
            match = re.search(r' ([0-9a-f]{32})\.md$', name)
            if match and bundle.getinfo(name).file_size < 65536:
                lines = bundle.read(name).decode('utf-8-sig').splitlines()
                title = next((l[2:].strip() for l in lines if l.startswith('# ')), '')
                properties = dict(l.split(': ',1) for l in lines if ': ' in l and not l.startswith('#'))
                pages.append((title, properties, match[1]))
    if not rows or 'Nombre' not in rows[0]:
        raise ValueError('No se encontró la columna Nombre.')
    duplicate_names = {n for n,c in Counter(r['Nombre'].strip() for r in rows).items() if c > 1}
    created = 0
    with store.transaction() as db:
        for index, row in enumerate(rows):
            name = row['Nombre'].strip()
            candidates = [p for p in pages if p[0] == name and p[1].get('Numero de celular','').strip() == row.get('Numero de celular','').strip()]
            if len(candidates) != 1:
                raise ValueError('No se pudo vincular un registro con su página de origen. Importación cancelada.')
            source = candidates[0][2]
            person_id = 'notion-' + source
            exists = db.execute("SELECT 1 FROM entities WHERE kind='person_private' AND id=?",(person_id,)).fetchone()
            if not exists:
                private = dict(id=person_id, private_profile=True, source_id=source, imported_at=now(),
                               **{k:row.get(v,'').strip() for k,v in PRIVATE_FIELDS.items()})
                store.save(db,DIRECTORY,'person_private',private)
            for tenant in tenants:
                member_id = str(uuid5(NAMESPACE_URL, 'flow-teams:'+tenant+':'+source))
                if db.execute("SELECT 1 FROM entities WHERE kind='member' AND id=?",(member_id,)).fetchone():
                    continue
                skills = [s.strip() for s in row.get('Skills','').split(',') if s.strip()]
                store.save(db,tenant,'member',dict(id=member_id,name=name,email='',phone=row.get('Numero de celular','').strip(),
                    role='Colaborador',rate=0,rate_missing=True,active=row.get('Estado','').strip().casefold() not in ('inactivo','baja'),
                    skills=skills,instagram=row.get('Instagram','').strip(),directory_id=person_id,
                    source_id=source,source='Notion · directorio compartido',access_version=1,review_name=name in duplicate_names))
                created += 1
        digest = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
        for tenant in tenants:
            if not any(a.get('source_digest') == digest for a in store.records(db,tenant,'audit')):
                store.create(db,tenant,'audit',action='directory_import',actor='Importación autorizada',created_at=now(),
                    before=None,after=dict(records=len(rows),duplicate_names=len(duplicate_names)),source_digest=digest)
    return dict(people=len(rows),members_created=created,duplicate_names=len(duplicate_names),missing_email=len(rows))
