"""Link an operational Notion snapshot to existing, tenant-scoped Teams jobs.

Source payment states remain references: a checkbox or 'half paid' cannot
supply the missing payment amount, coverage hours, or member acceptance.
"""
from collections import defaultdict
from datetime import date
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlparse
from uuid import uuid4

from src.teams import TeamsError, now
from src.tenant_brand_map import resolve_brand, UnresolvedBrandError

ROLES = [('Primera Camara','Primera cámara','Confirmado (1)'),
         ('Segunda Camara','Segunda cámara','Confirmado'),
         ('Videografo 1','Primer videógrafo','Confirmado video'),
         ('Videografo 2','Segundo videógrafo','Confirmado video 2'),
         ('Asistencia','Asistente',None)]


def plain(value, limit=12000):
    if value is None:
        return ''
    if not isinstance(value,str) or len(value)>limit:
        raise TeamsError('Revisa los campos de la información de Notion.')
    return value.strip()


def tokens(value):
    value = plain(value).split('(FORMULARIO')[0]
    value = ''.join(c for c in unicodedata.normalize('NFKD',value.casefold()) if not unicodedata.combining(c))
    return set(re.findall(r'[a-z0-9]+',value)) - {'boda','civil','religiosa','con','y','de','el','la','los','las','formulario','contacto','norkevin'}


def day(value):
    try:
        return date.fromisoformat(plain(value)[:10]).isoformat()
    except ValueError:
        return ''


def source_url(value):
    value=plain(value,500);url=urlparse(value)
    if url.scheme!='https' or url.hostname not in ('app.notion.com','www.notion.so','notion.so') or url.username or url.password:
        raise TeamsError('El registro debe incluir su enlace privado de Notion.')
    if not re.fullmatch(r'/p/[a-f0-9]{32}',url.path) and not re.fullmatch(r'/[a-f0-9-]{32,36}',url.path):
        raise TeamsError('El identificador de origen de Notion no es válido.')
    return value


def match_job(row, jobs, title_key):
    event_day=day(row.get('date:Fecha del evento:start'))
    name=tokens(row.get(title_key))
    if not event_day or not name:
        return None
    same_day=[j for j in jobs if day(j.get('boda_date'))==event_day]
    exact=[j for j in same_day if tokens(j.get('nombre'))==name]
    if len(exact)==1:
        return exact[0]
    # ponytail: conservative token containment, explicit links if names become ambiguous.
    close=[j for j in same_day if len(name & tokens(j.get('nombre'))) >= 2
           and (name <= tokens(j.get('nombre')) or tokens(j.get('nombre')) <= name)]
    return close[0] if len(close)==1 else None


def import_snapshot(store,tenant,jobs,payload,actor):
    if not isinstance(payload,dict) or any(not isinstance(payload.get(k),list) or len(payload[k])>2000 for k in ('jobs','payments')):
        raise TeamsError('Selecciona una captura válida de las dos bases de Notion.')
    try:
        expected={'norkevin':'NORKEVIN','astral':'ASTRAL FILMS'}.get(resolve_brand(tenant).brand_key)
    except UnresolvedBrandError:
        expected=None
    if not expected:
        raise TeamsError('Esta empresa no corresponde a las bases compartidas.',403)
    grouped=defaultdict(lambda:dict(crew=[],sources=[],notes=[],event_details=[],payments=[]))
    report=dict(updated=0,unchanged=0,unmatched_jobs=[],unmatched_payments=[],ignored_other_brand=0)
    for kind,title in [('jobs','BODA'),('payments','Evento específico')]:
        seen=set()
        for row in payload[kind]:
            if not isinstance(row,dict):
                raise TeamsError('Hay un registro de origen inválido.')
            if plain(row.get('EMPRESA'),100)!=expected:
                report['ignored_other_brand']+=1;continue
            url=source_url(row.get('url'))
            if url in seen:
                continue
            seen.add(url)
            job=match_job(row,jobs,title)
            if not job:
                report['unmatched_'+kind].append(dict(name=plain(row.get(title),500),date=day(row.get('date:Fecha del evento:start')),url=url))
                continue
            context=grouped[job['id']]
            context['sources'].append(url)
            if kind=='jobs':
                details={key:plain(row.get(key),2000) for key in ('Lugar de evento','Wedding Planner','CRONOGRAMA','Tipo de evento')}
                if any(details.values()) and details not in context['event_details']:
                    context['event_details'].append(details)
                for key,role,confirmation in ROLES:
                    person=plain(row.get(key),150)
                    if person and person.upper()!='NO APLICA':
                        item=dict(person=person,role=role,confirmed=row.get(confirmation)=='__YES__' if confirmation else None)
                        if item not in context['crew']:
                            context['crew'].append(item)
                for key in ('NOTAS','Notas de producción'):
                    note=plain(row.get(key)).replace('<br>','\n')
                    if note and note not in context['notes']:
                        context['notes'].append(note)
            else:
                amount=row.get('Monto acordado')
                if amount is not None and (type(amount) not in (int,float) or not 0<=amount<=100000000):
                    raise TeamsError('Hay un monto de Notion inválido.')
                status=plain(row.get('Estado de pago'),100)
                if status not in ('Pendiente','Mitad pagado','En proceso','Pagado',''):
                    raise TeamsError('Hay un estado de pago de Notion desconocido.')
                context['payments'].append(dict(person=plain(row.get('Persona'),150),role=plain(row.get('Servicio'),150),
                    amount=amount,status=status,date=day(row.get('date:Fecha de pago:start')),receipt=plain(row.get('Comprobante'),2000),url=url))
    with store.transaction() as db:
        existing={r['job_id']:r for r in store.records(db,tenant,'job_source')}
        for job_id,context in grouped.items():
            fingerprint=hashlib.sha256(json.dumps(context,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            previous=existing.get(job_id)
            if previous and previous['fingerprint']==fingerprint:
                report['unchanged']+=1;continue
            record=dict(previous) if previous else dict(id=str(uuid4()),job_id=job_id)
            record.update(context=context,fingerprint=fingerprint,synced_at=now(),source='Notion')
            store.save(db,tenant,'job_source',record)
            store.create(db,tenant,'audit',action='notion_link',actor=actor,created_at=now(),
                before=dict(job_id=job_id,version=previous['version']) if previous else None,
                after=dict(job_id=job_id,version=record['version']))
            report['updated']+=1
        previous_report=next(iter(store.records(db,tenant,'notion_report')),None)
        store.save(db,tenant,'notion_report',dict(id=previous_report['id'] if previous_report else str(uuid4()),
            version=previous_report['version'] if previous_report else 0,report=report,synced_at=now()))
    return dict(ok=True,record=report,warnings=[f"Información de Notion vinculada: {report['updated']} bodas actualizadas; {report['unchanged']} sin cambios. {len(report['unmatched_jobs'])} bodas y {len(report['unmatched_payments'])} pagos requieren revisión. Los saldos y coberturas de Flow se conservan."])
