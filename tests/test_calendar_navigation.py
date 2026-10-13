"""Monthly fragments keep the full calendar's permissions and dates."""
from flask import template_rendered
from test_flow_teams import web, member, assignment, publish, member_client


def test_owner_calendar_has_six_weeks_and_a_small_authorized_fragment(auth_client):
    import app as crm
    contexts = []
    def capture(sender, template, context, **extra):
        contexts.append(context)
    for month in ('2026-11', '2026-12', '2027-02'):
        with template_rendered.connected_to(capture, crm.app):
            full = auth_client.get('/calendar?month=' + month)
        assert full.status_code == 200
        assert len(contexts[-1]['calendar_grid']) == 6
        assert sum(len(week) for week in contexts[-1]['calendar_grid']) == 42
        fragment = auth_client.get('/calendar?month=' + month, headers={'X-Calendar-Fragment':'1'})
        assert fragment.status_code == 200
        html = fragment.get_data(as_text=True)
        assert 'data-calendar-root="owner"' in html and 'data-calendar-content' in html
        assert '<script' not in html and '<!DOCTYPE' not in html
        assert len(fragment.data) < len(full.data)
    for query in ('month=2026-13', 'year=2026&month=0', 'month=invalid'):
        assert auth_client.get('/calendar?' + query, headers={'X-Calendar-Fragment':'1'}).status_code == 400
    denied = crm.app.test_client().get('/calendar?month=2026-11', headers={'X-Calendar-Fragment':'1'})
    assert denied.status_code != 200 and 'data-calendar-root' not in denied.get_data(as_text=True)


def test_member_calendar_fragment_has_six_weeks_and_excludes_payments_and_other_members(web):
    app, owner, _ = web
    store = app.extensions['teams']
    person = member(store)
    publish(store, assignment(store, person))
    worker = member_client(app, owner, person)
    for month in ('2026-11', '2026-12', '2027-02'):
        url = '/teams-portal/?view=calendar&month=' + month
        full = worker.get(url)
        fragment = worker.get(url, headers={'X-Calendar-Fragment':'1'})
        html = fragment.get_data(as_text=True)
        assert fragment.status_code == 200
        assert html.count('class="ft-month-day ') == 42
        assert 'data-calendar-root="portal"' in html and 'data-calendar-link' in html
        assert 'Mis pagos' not in html and 'Q1,500' not in html and '<script' not in html
        assert len(fragment.data) < len(full.data)
    assert worker.get('/teams-portal/?view=calendar&job_id=not-mine', headers={'X-Calendar-Fragment':'1'}).status_code == 404
    assert app.test_client().get('/teams-portal/?view=calendar', headers={'X-Calendar-Fragment':'1'}).status_code == 302


def test_portal_view_fragments_keep_filters_and_authorization(web):
    from html import unescape
    app, owner, _ = web
    store = app.extensions['teams']
    person = member(store)
    publish(store, assignment(store, person))
    worker = member_client(app, owner, person)
    for view in ('list', 'calendar'):
        url = f'/teams-portal/?view={view}&month=2026-11&day=2026-11-14&q=no-match&period=upcoming'
        fragment = worker.get(url, headers={'X-Calendar-Fragment':'1'})
        assert fragment.status_code == 200
        html = unescape(fragment.get_data(as_text=True))
        assert f'data-calendar-view="{view}"' in html
        assert html.count('data-calendar-root="portal"') == 1
        assert 'data-calendar-toolbar' in html and 'data-calendar-content' in html
        assert html.count('data-calendar-view-link') == 2
        assert 'month=2026-11' in html and 'day=2026-11-14' in html
        assert 'q=no-match' in html and 'period=upcoming' in html
        assert 'Mis pagos' not in html and '<script' not in html and 'Q1,500' not in html
        assert 'no-store' in fragment.headers['Cache-Control']
        assert worker.get(url + '&job_id=not-mine', headers={'X-Calendar-Fragment':'1'}).status_code == 404
        assert app.test_client().get(url, headers={'X-Calendar-Fragment':'1'}).status_code == 302
    full = worker.get('/teams-portal/').get_data(as_text=True)
    assert 'data-calendar-root="portal" data-calendar-view="list"' in full
