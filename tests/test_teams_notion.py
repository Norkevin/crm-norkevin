from copy import deepcopy
from uuid import uuid4

import pytest

from src.teams import TeamsError, TeamsStore
from src.teams_notion import import_snapshot, match_job

TENANT = 'tenant-norkevin-photography'
JOBS = [dict(id='existing', nombre='Elisa & Juan', boda_date='2026-11-14')]


def row(**fields):
    return {'url':'https://app.notion.com/p/' + uuid4().hex, 'EMPRESA':'NORKEVIN',
            'BODA':'Boda Elisa & Juan', 'Evento específico':'Elisa y Juan',
            'date:Fecha del evento:start':'2026-11-14', **fields}


def data():
    return dict(jobs=[row(**{'Primera Camara':'Kevin','Confirmado (1)':'__YES__'})],
                payments=[row(**{'Persona':'Kevin','Servicio':'Foto','Monto acordado':1500,
                                'Estado de pago':'Mitad pagado'})])


def records(store, kind, tenant=TENANT):
    with store.transaction() as db:
        return store.records(db,tenant,kind)


def test_links_existing_job_without_creating_payment_or_acceptance(tmp_path):
    store=TeamsStore(tmp_path/'teams.sqlite3');payload=data()
    with store.transaction() as db:
        store.create(db,TENANT,'assignment',job_id='existing',status='pendiente')
        store.create(db,TENANT,'cost',job_id='existing',estimate=150000)
        store.create(db,TENANT,'payment',amount=50000)
    before={k:records(store,k) for k in ('assignment','cost','payment')}
    original=deepcopy(JOBS)
    result=import_snapshot(store,TENANT,JOBS,payload,'owner')
    assert result['record']['updated']==1
    source=records(store,'job_source')[0]
    assert source['job_id']=='existing' and source['context']['crew'][0]['confirmed'] is True
    assert source['context']['payments'][0]['status']=='Mitad pagado'
    assert 'paid' not in source['context']['payments'][0]
    assert {k:records(store,k) for k in before}==before
    assert JOBS==original
    repeat=import_snapshot(store,TENANT,JOBS,payload,'owner')['record']
    assert repeat['updated']==0 and repeat['unchanged']==1
    assert len(records(store,'job_source'))==1 and len(records(store,'audit'))==1
    reopened=TeamsStore(store.path)
    assert records(reopened,'job_source')==records(store,'job_source')


def test_brand_scope_and_same_job_id_cannot_overwrite_other_company(tmp_path):
    store=TeamsStore(tmp_path/'teams.sqlite3');payload=data()
    import_snapshot(store,TENANT,JOBS,payload,'owner')
    payload['jobs'][0]['EMPRESA']='ASTRAL FILMS';payload['payments'][0]['EMPRESA']='ASTRAL FILMS'
    import_snapshot(store,'tenant-norkevin',JOBS,payload,'other owner')
    first=records(store,'job_source')[0];second=records(store,'job_source','tenant-norkevin')[0]
    assert first['id']!=second['id']
    assert import_snapshot(store,TENANT,JOBS,payload,'owner')['record']['ignored_other_brand']==2
    assert records(store,'job_source')[0]==first
    with pytest.raises(TeamsError):
        import_snapshot(store,'unknown',JOBS,payload,'owner')


def test_matching_date_and_name_are_required_and_ambiguous_results_not_guessed():
    source=row();assert match_job(source,JOBS,'BODA')==JOBS[0]
    assert match_job(source,JOBS+[dict(JOBS[0],id='duplicate')],'BODA') is None
    assert match_job(row(BODA='Otra persona'),JOBS,'BODA') is None
    assert match_job(row(**{'date:Fecha del evento:start':'2025-11-14'}),JOBS,'BODA') is None
    assert match_job(row(**{'date:Fecha del evento:start':None}),JOBS,'BODA') is None


def test_duplicates_are_deduplicated_unmatched_report_and_no_partial_writes(tmp_path):
    store=TeamsStore(tmp_path/'teams.sqlite3');payload=data()
    payload['jobs'] += [deepcopy(payload['jobs'][0]),row(BODA='Otra boda')]
    result=import_snapshot(store,TENANT,JOBS,payload,'owner')['record']
    assert result['updated']==1 and len(result['unmatched_jobs'])==1
    assert len(records(store,'job_source')[0]['context']['crew'])==1
    for bad in (dict(payload,jobs=[row(url='https://evil.invalid/private')]),
                dict(payload,payments=[row(**{'Monto acordado':float('nan')})]),
                dict(payload,payments=[row(**{'Estado de pago':'Inventado'})])):
        before=records(store,'job_source')
        with pytest.raises(TeamsError):import_snapshot(store,TENANT,JOBS,bad,'owner')
        assert records(store,'job_source')==before
