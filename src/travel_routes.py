"""Tenant-scoped venue administration using the CRM's existing session guards."""
from flask import abort, jsonify, render_template, request, session

from src.storage import TenantMismatchError
from src.travel import TRAVEL_LEVELS


def register_travel_routes(app, store, service, get_lead):
    def tenant_id():
        tenant = store.current_tenant_id()
        if not tenant:
            abort(401)
        return tenant

    def payload():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('Envía un objeto JSON válido.')
        if data.get('tenant_id') and data['tenant_id'] != tenant_id():
            abort(404)
        return data

    def actor():
        return session.get('user_email') or 'Usuario de Flow'

    @app.get('/settings/travel')
    def settings_travel():
        tenant = tenant_id()
        service.ensure_catalog(tenant)
        venues = service.catalog(tenant)
        by_id = {venue['id']: venue for venue in venues}
        reviews = [dict(review, candidate_venues=[by_id[item['id']] for item in review.get('candidates', [])
                                                  if item.get('id') in by_id])
                   for review in service.reviews(tenant) if review.get('status') != 'resolved']
        return render_template(
            'settings_travel.html', venues=venues,
            travel_rules=service.rules(tenant), reviews=reviews,
            travel_settings=service.settings(tenant), travel_levels=TRAVEL_LEVELS,
        )

    @app.post('/api/travel/venues')
    def api_travel_venue_save():
        tenant = tenant_id()
        try:
            data = payload()
            if data.get('id') and not any(v['id'] == data['id'] for v in service.catalog(tenant)):
                abort(404)
            review_id = data.pop('review_id', None)
            if review_id and not any(r.get('id') == review_id for r in service.reviews(tenant)):
                abort(404)
            venue = service.save_venue(tenant, data, actor=actor())
            if (review_id and venue.get('verification_status') == 'verified'
                    and venue.get('approval_status') == 'approved'):
                service.resolve_review(tenant, review_id, venue['id'], actor=actor())
            return jsonify(ok=True, venue=venue)
        except TenantMismatchError:
            abort(404)
        except (ValueError, TypeError) as error:
            return jsonify(ok=False, error=str(error)), 400

    @app.post('/api/travel/rules')
    def api_travel_rule_save():
        try:
            tenant, data = tenant_id(), payload()
            if data.get('id') and not any(r['id'] == data['id'] for r in service.rules(tenant)):
                abort(404)
            rule = service.save_rule(tenant, data, actor=actor())
            return jsonify(ok=True, rule=rule)
        except TenantMismatchError:
            abort(404)
        except (ValueError, TypeError) as error:
            return jsonify(ok=False, error=str(error)), 400

    @app.post('/api/travel/settings')
    def api_travel_settings_save():
        try:
            settings = service.save_settings(tenant_id(), payload(), actor=actor())
            return jsonify(ok=True, settings=settings)
        except TenantMismatchError:
            abort(404)
        except (ValueError, TypeError) as error:
            return jsonify(ok=False, error=str(error)), 400

    @app.post('/api/leads/<lead_id>/travel/research')
    def api_lead_travel_research(lead_id):
        tenant = tenant_id()
        lead = get_lead(lead_id)
        if not lead or lead.get('tenant_id') != tenant:
            abort(404)
        try:
            return jsonify(ok=True, travel=service.resolve(lead, research=True))
        except TenantMismatchError:
            abort(404)
        except (ValueError, TypeError) as error:
            return jsonify(ok=False, error=str(error)), 400
