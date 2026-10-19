"""The compact roster must keep decisions visible and management available."""
from html.parser import HTMLParser

from test_flow_teams import web, member, assignment
from test_teams_shared import shared


class Roster(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.in_person = False
        self.disclosures = []
        self.visible = []
        self.forms = {}
        self.management_open = []
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'article':
            self.in_person = 'ft-crew-record' in attrs.get('class', '').split()
        if tag == 'details':
            self.disclosures.append('open' in attrs)
            if self.in_person and 'ft-crew-manage' in attrs.get('class', '').split():
                self.management_open.append('open' in attrs)
        command = attrs.get('data-command') or attrs.get('data-action')
        if self.in_person and tag in ('form', 'button') and command:
            self.forms[command] = False in self.disclosures

    def handle_endtag(self, tag):
        if tag == 'details':
            self.disclosures.pop()
        if tag == 'article':
            self.in_person = False

    def handle_data(self, text):
        if self.in_person and False not in self.disclosures:
            self.visible.append(text)


def test_roster_keeps_money_and_schedule_visible_with_management_collapsed(web):
    app, owner, _ = web
    store = app.extensions['teams']
    assignment(store, member(store))
    response = owner.get('/teams/jobs/job-1')
    assert response.status_code == 200
    roster = Roster(response.get_data(as_text=True))
    text = ' '.join(roster.visible)
    for value in ('Fotógrafo', '13:00–22:00', 'Honorario', 'Pagado', 'Pendiente', 'Q1,500.00'):
        assert value in text
    assert roster.management_open == [False]
    for command in ('assignment_edit', 'assignment_schedule_pending'):
        assert roster.forms[command], f'{command} must remain inside expandable management'


def test_conflict_warning_remains_visible_without_opening_management(shared):
    _, owner, _, store, first_brand, second_brand, first_person, second_person = shared
    assignment(store, second_person, job='astral-job', tenant=second_brand)
    assignment(store, first_person, job='norkevin-job', tenant=first_brand)
    response = owner.get('/teams/jobs/norkevin-job')
    assert response.status_code == 200
    roster = Roster(response.get_data(as_text=True))
    assert roster.management_open == [False]
    text = ' '.join(roster.visible)
    assert 'Astral Weddings' in text
    assert 'Revisa antes de confirmar' in text
