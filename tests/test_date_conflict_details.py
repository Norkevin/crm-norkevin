import json
import copy
import pytest
from html.parser import HTMLParser
from test_lead_package_availability import booking


@pytest.fixture(autouse=True)
def restore_warning_data(booking):
    crm = booking[0]
    snapshots = {table: copy.deepcopy(crm.store._read_raw(table))
                 for table in ('jobs', 'leads', 'calendar', 'pending_emails', 'mail_log')}
    yield
    for table, rows in snapshots.items(): crm.store._save(table, rows)


class WarningButtons(HTMLParser):
    def __init__(self):
        super().__init__(); self.buttons = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'button' and (attrs.get('onclick') or '').startswith('openDateConflict'):
            args = attrs['onclick'][len('openDateConflict(event, '):-1]
            self.buttons.append((attrs, json.loads('[' + args + ']')))


def test_job_warning_names_both_job_and_lead_and_links_to_them(booking):
    crm, client, lead, job, *_ = booking
    other = dict(job, id=job['id'] + '-civil', lead_id=job['lead_id'] + '-civil', nombre='Boda civil coincidente')
    crm.store.upsert('jobs', job); crm.store.upsert('jobs', other)
    parser = WarningButtons(); parser.feed(client.get('/jobs').get_data(as_text=True))
    attrs, (subject, items) = next(row for row in parser.buttons if row[1][0] == job['nombre'])
    assert attrs['type'] == 'button'
    assert {item['name'] for item in items} == {other['nombre'], lead['nombre']}
    assert {item['url'] for item in items} == {'/jobs/' + other['id'], '/leads/' + lead['id']}
    assert all(item['date'] == job['boda_date'] and item['date_label'] for item in items)
    assert all(item['date_label'] == crm._format_date_es(job['boda_date']) for item in items)
    crm.store.delete('jobs', other['id'])


def test_lead_warning_explains_block_and_links_to_calendar(booking):
    crm, client, lead, *_ = booking
    block = client.post('/api/calendar/events', json={'type':'block','date':'2034-05-15','title':'Descanso personal'}).json['event']
    parser = WarningButtons(); parser.feed(client.get('/leads').get_data(as_text=True))
    _, (_, items) = next(row for row in parser.buttons if row[1][0] == lead['nombre'])
    assert any(item['type'] == 'block' and item['name'] == 'Descanso personal' and item['url'] == '/calendar?year=2034&month=5' for item in items)
    crm.store.delete('calendar', block['id'])
