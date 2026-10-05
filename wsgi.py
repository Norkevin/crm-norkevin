"""Entry point para producción con gunicorn."""
import os
from app import app, store, _canonical_jobs, _job_payment_summary, _job_is_active

# No seeds or development login in production. Existing CRM jobs keep their IDs.
if (os.environ.get('FLOW_TEAMS_ENABLED', '1') == '1'
        and app.secret_key != 'norkevin-crm-dev-secret-change-me'
        and len(app.secret_key or '') >= 32):
    from src.teams import register_teams
    app.config.update(FLOW_TEAMS_ENABLED=True, SESSION_COOKIE_SECURE=True,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')
    register_teams(app, store, _canonical_jobs, _job_payment_summary, _job_is_active)

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
