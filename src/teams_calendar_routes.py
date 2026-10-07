"""Owner Calendar controls; Google consent remains an explicit owner action."""
from datetime import datetime, timedelta, timezone
import os
import secrets

from flask import abort, jsonify, redirect, request, session, url_for

from src import google_calendar, gmail_delivery
from src.teams import TeamsError, teams_zone
from src.teams_calendar import CalendarSync
from src.teams_features import VISIBLE_ASSIGNMENTS


def finish_calendar_connection(app,crm_store,redirect_uri):
    pending=session.pop('teams_calendar_oauth',{})
    tenant=session.get('tenant_id');owner=crm_store.get('tenants',tenant) if tenant else None
    if (not owner or not session.get('logged_in') or session.get('user_email')!=owner.get('login_email')
        or pending.get('tenant')!=tenant or pending.get('expires',0)<datetime.now(timezone.utc).timestamp()
        or not secrets.compare_digest(request.args.get('state',''),pending.get('state',''))):
        abort(403)
    try:
        if request.args.get('error') or not request.args.get('code'):raise ValueError('Conexión cancelada.')
        google_calendar.exchange_code(tenant,request.args['code'],redirect_uri)
        app.extensions['teams_calendar'].wakeup.set()
        session['teams_calendar_message']='Google Calendar conectado. Las bodas se sincronizan automáticamente desde Flow; la primera sincronización puede tardar un minuto.'
    except Exception:
        session['teams_calendar_message']='No se conectó Calendar. Usa la cuenta de esta marca y concede el permiso de Calendar. Si Google bloquea el acceso, revisa la configuración OAuth y Calendar API.'
    return redirect('/teams/settings')


def register_calendar(app,blueprint,crm_store,database,read_job,canonical_jobs):
    sync=CalendarSync(database);app.extensions['teams_calendar']=sync
    def reconcile_tenant(tenant):
        # Reuse the CRM's explicit tenant context, also used by workflow workers.
        from app import _workflow_tenant
        token=_workflow_tenant.set(tenant)
        try:
            with app.app_context():
                if crm_store.current_tenant_id()!=tenant:
                    raise TeamsError('No se pudo resolver la marca para sincronizar Calendar.')
                sync.reconcile(tenant,canonical_jobs(),os.environ.get('APP_BASE_URL','https://flowingcrm.com').rstrip('/'),
                               teams_zone(crm_store,tenant),app.secret_key)
        finally:_workflow_tenant.reset(token)
    sync.reconcile_tenant=reconcile_tenant
    if app.config.get('FLOW_TEAMS_ENABLED') and not app.config.get('TESTING') and not app.config.get('FLOW_TEAMS_LOCAL'):sync.start()

    @app.context_processor
    def calendar_connection_status():
        if request.endpoint not in ('calendar_view','settings'):return {}
        tenant=session.get('tenant_id')
        owner=crm_store.get('tenants',tenant) if tenant else None
        if not owner or not session.get('logged_in') or session.get('user_email')!=owner.get('login_email'):return {}
        email=google_calendar.connected_email(tenant)
        with database.transaction() as db:
            records=[r for r in database.records(db,tenant,'calendar_sync') if r['identity'].startswith('job:')]
        return dict(crm_calendar_email=email,crm_calendar_failed=sum(r['status']=='failed' for r in records),
                    crm_calendar_errors=sorted({r['error'] for r in records if r['status']=='failed' and r.get('error')}),
                    crm_calendar_synced=sum(r['status']=='synced' and r.get('event') is not None for r in records),
                    crm_calendar_pending=sum(r['status']=='pending' for r in records))

    def origin():
        return request.url_root.rstrip('/') if app.config.get('FLOW_TEAMS_LOCAL') else os.environ.get('APP_BASE_URL','https://flowingcrm.com').rstrip('/')

    def enqueue(job,**options):
        try:
            return sync.enqueue(session['tenant_id'],job,origin(),teams_zone(crm_store,session['tenant_id']),app.secret_key,**options)
        except (ValueError,TypeError,KeyError):
            raise TeamsError('Revisa fecha, horarios y correos antes de sincronizar Calendar.')

    @blueprint.route('/teams/calendar/connect')
    def calendar_connect():
        if app.config.get('FLOW_TEAMS_LOCAL'):raise TeamsError('Las conexiones externas están bloqueadas en la propuesta local.')
        if not gmail_delivery.is_configured():raise TeamsError('Falta configurar el cliente OAuth de Google en el servidor.')
        state='calendar.'+secrets.token_urlsafe(32)
        session['teams_calendar_oauth']=dict(state=state,tenant=session['tenant_id'],expires=(datetime.now(timezone.utc)+timedelta(minutes=10)).timestamp())
        from app import _google_redirect_uri
        return redirect(google_calendar.authorization_url(_google_redirect_uri(),state,session['tenant_id']))

    @blueprint.route('/api/teams/calendar/sync',methods=['POST'])
    def calendar_sync():
        data=request.get_json(silent=True)
        if not isinstance(data,dict):raise TeamsError('Revisa la invitación.')
        if app.config.get('FLOW_TEAMS_LOCAL') or not google_calendar.connected_email(session['tenant_id']):
            raise TeamsError('Conecta Google Calendar en Configuración para esta marca.')
        if data.get('scope')=='weddings':
            from src.teams import job_phase
            today=datetime.now(teams_zone(crm_store,session['tenant_id'])).date()
            count=0
            for wedding in canonical_jobs():
                if wedding.get('status') not in ('Cancelado','Archivado') and job_phase(wedding,[],today)=='upcoming':
                    count+=enqueue(wedding,invite_ids=[])
            return jsonify(ok=True,record=dict(queued=count),warnings=['Próximas bodas en cola para el calendario de esta marca. Esta acción no invita colaboradores.'])
        job=read_job(data.get('job_id'))
        if not job.get('boda_date') or job.get('status') in ('Cancelado','Archivado'):
            raise TeamsError('La boda necesita una fecha y estar activa en el CRM.')
        invite=data.get('invite','none')
        if invite not in ('none','all','individual'):raise TeamsError('Revisa los destinatarios.')
        ids=[]
        if invite!='none':
            with database.transaction() as db:
                eligible=[a for a in database.records(db,session['tenant_id'],'assignment') if a['job_id']==job['id']
                    and a['status'] in VISIBLE_ASSIGNMENTS and a['job_day']==job['boda_date']]
                if invite=='individual':eligible=[a for a in eligible if a['id']==data.get('assignment_id')]
                if not eligible:raise TeamsError('Comparte primero una cobertura vigente para los destinatarios.')
                ids=[a['id'] for a in eligible]
        send_at=None
        if data.get('send_at'):
            try:
                when=datetime.fromisoformat(data['send_at'])
                if when.tzinfo is not None:raise ValueError
                when=when.replace(tzinfo=teams_zone(crm_store,session['tenant_id']))
                if when<=datetime.now(when.tzinfo) or when>datetime.now(when.tzinfo)+timedelta(days=366):raise ValueError
                if ids and any(when>=datetime.fromisoformat(a['end']) for a in eligible):raise ValueError
                send_at=when.astimezone(timezone.utc).isoformat()
            except (ValueError,TypeError):raise TeamsError('Elige una fecha y hora futuras, dentro del próximo año y antes de terminar las coberturas.')
        from src.teams import text
        delivery_key=text(data,'key',maximum=100)
        queued=enqueue(job,include_new=True,invite_ids=ids,send_at=send_at,delivery_key=delivery_key)
        return jsonify(ok=True,record=dict(queued=queued),warnings=[
            ('Sin cambios por enviar.' if not queued else 'Envío programado. La hora corresponde a esta empresa.' if send_at else 'Sincronización en cola. Google enviará las invitaciones al procesarla.')
            + ' Revisa el estado de cada cobertura; Calendar conectado no significa correo entregado.'])

    @blueprint.route('/api/teams/calendar/cancel-scheduled',methods=['POST'])
    def calendar_cancel_scheduled():
        data=request.get_json(silent=True)
        if not isinstance(data,dict):raise TeamsError('Selecciona el envío programado.')
        with database.transaction() as db:
            record=database.get(db,session['tenant_id'],'calendar_sync',data.get('id'))
            database.check_version(record,data)
            if record['status']!='pending' or not record.get('not_before') or datetime.fromisoformat(record['not_before'])<=datetime.now(timezone.utc):
                raise TeamsError('El envío ya se procesó o no está programado.',409)
            record.update(status='paused',not_before=None)
            database.save(db,session['tenant_id'],'calendar_sync',record)
        return jsonify(ok=True,record=record,warnings=['Envío programado cancelado.'])

    @app.after_request
    def update_linked_events(response):
        if request.method!='POST' or not 200<=response.status_code<300 or not session.get('logged_in'):return response
        tenant=session.get('tenant_id')
        owner=crm_store.get('tenants',tenant) if tenant else None
        if (not owner or session.get('user_email')!=owner.get('login_email')
            or app.config.get('FLOW_TEAMS_LOCAL') or not google_calendar.connected_email(tenant)):return response
        job_id=None
        if request.endpoint=='teams.command':
            body=request.get_json(silent=True) or {}
            if body.get('action') in ('assignment_publish','assignment_edit','assignment_status','document','document_publish','document_withdraw','member_revoke','member'):
                result=response.get_json(silent=True) or {};record=result.get('record') or {};job_id=record.get('job_id')
                if not job_id and body.get('action') in ('member','member_revoke'):
                    with database.transaction() as db:
                        job_ids={a['job_id'] for a in database.records(db,tenant,'assignment') if a['member_id']==record.get('id')}
                    for identifier in job_ids:
                        try:enqueue(read_job(identifier))
                        except Exception:app.logger.warning('Calendar update not queued; business change remains saved')
        elif request.endpoint=='api_job_new':
            result=response.get_json(silent=True) or {};job_id=result.get('job_id') or (result.get('job') or {}).get('id')
        elif request.endpoint in ('api_job_update','api_job_status'):
            job_id=(request.view_args or {}).get('job_id')
        if job_id:
            try:enqueue(read_job(job_id))
            except Exception:app.logger.warning('Calendar update not queued; business change remains saved')
        return response

    return google_calendar.connected_email
