"""Owner Calendar controls; Google consent remains an explicit owner action."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import secrets

from flask import abort, jsonify, redirect, request, session, url_for

from src import google_calendar, gmail_delivery
from src.teams import TeamsError, teams_zone, text, now
from src.teams_calendar import CalendarSync, events, valid_invitation_email
from src.teams_features import VISIBLE_ASSIGNMENTS


def finish_calendar_connection(app,crm_store,redirect_uri):
    pending=session.pop('teams_calendar_oauth',{})
    tenant=session.get('tenant_id');owner=crm_store.get('tenants',tenant) if tenant else None
    if (not owner or not session.get('logged_in') or session.get('user_email')!=owner.get('login_email')
        or pending.get('tenant')!=tenant or pending.get('expires',0)<datetime.now(timezone.utc).timestamp()
        or not secrets.compare_digest(request.args.get('state','').encode(),str(pending.get('state','')).encode())):
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
    def send_invitation_email(tenant, record):
        from src.teams_mail import send_portal_email
        if app.config.get('FLOW_TEAMS_LOCAL'):
            raise TeamsError('El entorno de pruebas no envía correos.')
        with database.transaction() as db:
            assignment=database.get(db,tenant,'assignment',record['identity'].split(':',1)[1])
            member=database.get(db,tenant,'member',assignment['member_id'])
            if assignment['status'] not in VISIBLE_ASSIGNMENTS or not member['active']:
                raise TeamsError('La asignación ya no está vigente. No se envió correo.')
            if member.get('email') not in [a.get('email') for a in record['event'].get('attendees',[])]:
                raise TeamsError('El correo cambió. Revisa la ficha y vuelve a enviar la invitación.')
        return send_portal_email(database,tenant,member,os.environ.get('APP_BASE_URL','https://flowingcrm.com'),
                                 'Invitación de Teams',event=record['event'],calendar_url=record.get('html_url',''),job_id=assignment['job_id'])
    sync.send_invitation_email=send_invitation_email

    def invitation_eligible(tenant, record):
        """Last local check for this bulk action; never send stale or answered terms."""
        with database.transaction() as db:
            a=database.get(db,tenant,'assignment',record['identity'].split(':',1)[1])
            member=database.get(db,tenant,'member',a['member_id'])
            closed={o['job_id'] for o in database.records(db,tenant,'operation') if o.get('closed')}
            apartados={r['job_id'] for r in database.records(db,tenant,'job_classification') if r['state']!='included'}
            clashes=database.conflicts(db,tenant,a['member_id'],a['start'],a['end'],a['buffer'],a['id'],candidate=a)
        if a['status'] not in ('pendiente','reconfirmar'):
            return 'La cobertura ya tiene respuesta o está cerrada.'
        if not member['active'] or not valid_invitation_email(member.get('email','')):
            return 'El miembro está inactivo o necesita revisar su correo.'
        if member.get('shared_portal_blocked'):
            return 'El acceso personal necesita revisión. Usa la invitación individual.'
        if datetime.fromisoformat(a['end'])<=datetime.now(timezone.utc):
            return 'La cobertura ya terminó.'
        if clashes:return 'Hay un cruce de horario. Revisa la invitación individual.'
        jobs=crm_store.list_privileged('jobs',tenant_id=tenant,reason='Teams: validar la cobertura propia antes de una invitación en lote')
        if a['job_id'].startswith('secondary:'):
            from src.linked_coverages import linked_coverages
            calendar=crm_store.list_privileged('calendar',tenant_id=tenant,reason='Teams: validar la cobertura secundaria propia antes de una invitación en lote')
            jobs+=linked_coverages(jobs,calendar)
        job=next((j for j in jobs if j['id']==a['job_id']),None)
        if (not job or not job.get('boda_date') or job.get('status') in ('Cancelado','Archivado') or job.get('boda_date')!=a['job_day']
                or job['id'] in closed or job.get('parent_job_id') in closed
                or job['id'] in apartados or job.get('parent_job_id') in apartados):
            return 'La boda cambió o su operación está cerrada.'
        desired=events(database,tenant,job,os.environ.get('APP_BASE_URL','https://flowingcrm.com').rstrip('/'),
            teams_zone(crm_store,tenant),app.secret_key,eligible_ids=[a['id']],portal_ids={a['id']},
            previous_events={record['identity']:record['event']})
        return True if desired.get(record['identity'])==record['event'] else 'Las condiciones cambiaron. Revisa la invitación individual.'
    sync.invitation_eligible=invitation_eligible
    def reconcile_tenant(tenant):
        # Reuse the CRM's explicit tenant context, also used by workflow workers.
        from app import _workflow_tenant, _calendar_non_job_events
        token=_workflow_tenant.set(tenant)
        try:
            with app.app_context():
                if crm_store.current_tenant_id()!=tenant:
                    raise TeamsError('No se pudo resolver la marca para sincronizar Calendar.')
                sync.reconcile(tenant,canonical_jobs(),os.environ.get('APP_BASE_URL','https://flowingcrm.com').rstrip('/'),
                               teams_zone(crm_store,tenant),app.secret_key,calendar_entries=_calendar_non_job_events())
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
            records=[r for r in database.records(db,tenant,'calendar_sync') if r['identity'].startswith(('job:','block:','event:','lead:'))]
        return dict(crm_calendar_email=email,crm_calendar_failed=sum(r['status']=='failed' for r in records),
                    crm_calendar_errors=sorted({r['error'] for r in records if r['status']=='failed' and r.get('error')}),
                    crm_calendar_synced=sum(r['status']=='synced' and r.get('event') is not None and r['identity'].startswith('job:') for r in records),
                    crm_calendar_other_synced=sum(r['status']=='synced' and r.get('event') is not None and r['identity'].startswith(('event:','lead:')) for r in records),
                    crm_calendar_blocks_synced=sum(r['status']=='synced' and r.get('event') is not None and r['identity'].startswith('block:') for r in records),
                    crm_calendar_pending=sum(r['status']=='pending' for r in records))

    def origin():
        return request.url_root.rstrip('/') if app.config.get('FLOW_TEAMS_LOCAL') else os.environ.get('APP_BASE_URL','https://flowingcrm.com').rstrip('/')

    def enqueue(job,**options):
        try:
            return sync.enqueue(session['tenant_id'],job,origin(),teams_zone(crm_store,session['tenant_id']),app.secret_key,**options)
        except (ValueError,TypeError,KeyError):
            raise TeamsError('Revisa fecha, horarios y correos antes de sincronizar Calendar.')

    def invite_pending(data):
        tenant=session['tenant_id']
        key='calendar-bulk:'+text(data,'key',maximum=100)
        fingerprint=hashlib.sha256(b'calendar-pending-invitations').hexdigest()
        with database.transaction() as db:
            previous=db.execute('SELECT fingerprint,result FROM commands WHERE tenant=? AND key=?',(tenant,key)).fetchone()
            if previous:
                if previous['fingerprint']!=fingerprint:raise TeamsError('La clave del envío ya está en uso.',409)
                return jsonify(json.loads(previous['result']))
            assignments=database.records(db,tenant,'assignment')
            members={m['id']:m for m in database.records(db,tenant,'member')}
            invitations={r['identity']:r for r in database.records(db,tenant,'calendar_sync')}
            closed={o['job_id'] for o in database.records(db,tenant,'operation') if o.get('closed')}
            apartados={r['job_id'] for r in database.records(db,tenant,'job_classification') if r['state']!='included'}
        jobs={j['id']:j for j in canonical_jobs()}
        grouped={};answered=busy=review=0
        for a in assignments:
            job=jobs.get(a['job_id']);member=members.get(a['member_id'])
            if not job or job.get('status') in ('Cancelado','Archivado') or datetime.fromisoformat(a['end'])<=datetime.now(timezone.utc):continue
            if a['status'] not in ('borrador','pendiente','reconfirmar'):
                answered+=a['status'] in ('aceptada','rechazada');continue
            invitation=invitations.get('assignment:'+a['id'],{})
            if invitation.get('response_status') in ('accepted','declined','tentative','cancelled'):
                answered+=1;continue
            if (invitation.get('status')=='pending' or invitation.get('email_status') in ('pending','sending','failed')
                    or invitation.get('not_before')):
                busy+=1;continue
            if (not member or not member['active'] or member.get('shared_portal_blocked')
                    or not valid_invitation_email(member.get('email','')) or not job.get('boda_date')
                    or a['job_day']!=job.get('boda_date') or job['id'] in closed or job.get('parent_job_id') in closed
                    or job['id'] in apartados or job.get('parent_job_id') in apartados):
                review+=1;continue
            grouped.setdefault(job['id'],[]).append(a)
        queued=0
        for job_id,candidates in grouped.items():
            ids=[]
            for a in candidates:
                try:
                    with database.transaction() as db:
                        if database.conflicts(db,tenant,a['member_id'],a['start'],a['end'],a['buffer'],a['id'],candidate=a):
                            review+=1;continue
                    if a['status']=='borrador':
                        database.command(tenant,session['user_email'],dict(action='assignment_publish',
                            key=hashlib.sha256((key+':publish:'+a['id']).encode()).hexdigest(),id=a['id'],version=a['version']),read_job)
                    ids.append(a['id'])
                except TeamsError:
                    review+=1
            if not ids:continue
            try:
                delivery_key=hashlib.sha256((key+':'+job_id).encode()).hexdigest()
                sync.enqueue(tenant,read_job(job_id),origin(),teams_zone(crm_store,tenant),app.secret_key,
                    include_new=True,invite_ids=ids,delivery_key=delivery_key,skip_unchanged=True,
                    background=False,invite_unanswered_only=True)
                queued+=len(ids)
            except (TeamsError,ValueError,TypeError,KeyError):
                review+=len(ids)
        message=(f'{queued} invitaciones pendientes en cola para esta marca. '
            f'{answered} respuestas ya registradas omitidas; {busy} envíos en curso o programados conservados. '
            'Se vuelve a comprobar la respuesta de Google antes de enviar. Revisa el resultado en cada cobertura.')
        if review:message+=f' {review} coberturas necesitan revisión; consulta sus fichas.'
        result=dict(ok=True,record=dict(queued=queued,answered=answered,busy=busy,review=review),warnings=[message])
        with database.transaction() as db:
            db.execute('INSERT OR IGNORE INTO commands VALUES (?,?,?,?)',(tenant,key,fingerprint,json.dumps(result)))
            database.create(db,tenant,'audit',action='calendar_bulk_invite',actor=session['user_email'],created_at=now(),before=None,after=result['record'])
        if queued:sync.wakeup.set()
        return jsonify(result)

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
        if data.get('scope')=='pending_invitations':
            return invite_pending(data)
        if data.get('scope')=='retry_failed':
            count=0
            with database.transaction() as db:
                for record in database.records(db,session['tenant_id'],'calendar_sync'):
                    if record['identity'].startswith(('job:','block:','event:','lead:')) and record['status']=='failed':
                        record.update(status='pending',error='',retry_after=None)
                        database.save(db,session['tenant_id'],'calendar_sync',record);count+=1
            sync.wakeup.set()
            return jsonify(ok=True,record=dict(queued=count),warnings=['Trabajos, leads, eventos y bloqueos pendientes en cola para reintentar. No se envían invitaciones al equipo.'])
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
        publish_draft = data.get('publish') in (True, 'true')
        if publish_draft and invite != 'individual':
            raise TeamsError('Selecciona una persona para publicar y enviar su invitación.')
        ids=[]
        skipped=[]
        if invite!='none':
            with database.transaction() as db:
                eligible=[a for a in database.records(db,session['tenant_id'],'assignment') if a['job_id']==job['id']
                    and (a['status'] in VISIBLE_ASSIGNMENTS or (publish_draft and a['status']=='borrador')) and a['job_day']==job['boda_date']]
                if invite=='individual':eligible=[a for a in eligible if a['id']==data.get('assignment_id')]
                if not eligible:raise TeamsError('Comparte primero una cobertura vigente para los destinatarios.')
                from src.teams_calendar import valid_invitation_email
                valid=[]
                for assignment in eligible:
                    person=database.get(db,session['tenant_id'],'member',assignment['member_id'])
                    if person['active'] and valid_invitation_email(person.get('email','')):valid.append(assignment)
                    else:skipped.append(person['name'])
                eligible=valid
                if not eligible:raise TeamsError('Agrega un correo válido a los trabajadores antes de enviar.')
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
        if publish_draft and eligible[0]['status'] == 'borrador':
            try: version = int(data.get('version', 0))
            except (TypeError, ValueError): raise TeamsError('Recarga la ficha antes de enviar.')
            database.command(session['tenant_id'], session['user_email'], dict(
                action='assignment_publish', key=hashlib.sha256((delivery_key+':publish').encode()).hexdigest(),
                id=eligible[0]['id'], version=version), read_job)
        queued=enqueue(job,include_new=True,invite_ids=ids,send_at=send_at,delivery_key=delivery_key,skip_unchanged=True)
        return jsonify(ok=True,record=dict(queued=queued),warnings=[
            ('Sin cambios por enviar.' if not queued else 'Envío programado. La hora corresponde a esta empresa.' if send_at else 'Sincronización en cola. Google enviará las invitaciones al procesarla.')
            + ' Revisa el estado de Calendar y correo en cada trabajador.'
            + (' Sin enviar por correo pendiente o trabajador inactivo: ' + ', '.join(skipped) + '.' if skipped else '')])

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
            if body.get('action') in ('assignment_publish','assignment_edit','assignment_schedule_pending','job_schedule','assignment_status','document','document_publish','document_withdraw','member_revoke','member'):
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
