"""Exercise native pairing and reads over verified loopback HTTPS, with QA data."""
import argparse
import json
import re
import ssl
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('control', type=Path)
    parser.add_argument('--approve-code', help='Approve only the code displayed by the native QA app.')
    args = parser.parse_args()
    control = json.loads(args.control.read_text())
    base = control['url']
    url = urlsplit(base)
    assert url.scheme == 'https' and url.netloc == 'localhost:5443' and not url.query
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context(cafile=control['certificate'])))

    def send(path, method='GET', value=None, cookie=None, token=None, form=False):
        headers = {'Accept': 'application/json'}
        body = None
        if value is not None:
            body = urlencode(value).encode() if form else json.dumps(value).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        if cookie:
            headers['Cookie'] = cookie
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if method == 'POST':
            headers['Origin'] = base
        with opener.open(Request(base + path, data=body, method=method, headers=headers), timeout=10) as response:
            return response.read(), response.headers

    pair = None if args.approve_code else json.loads(send('/api/mobile/pair/start', 'POST', {})[0])
    page, headers = send('/mobile/connect', cookie='session=' + control['cookie'])
    csrf = re.search(rb'name="csrf" value="([^"]+)"', page).group(1).decode()
    cookie = headers['Set-Cookie'].split(';', 1)[0]
    send('/mobile/connect', 'POST', dict(code=args.approve_code or pair['code'], csrf=csrf), cookie=cookie, form=True)
    if args.approve_code:
        print('Native QA code approved through the real owner/CSRF route.')
        return
    token = json.loads(send('/api/mobile/pair/poll', 'POST', dict(device_secret=pair['device_secret']))[0])['token']
    overview = json.loads(send('/api/mobile/overview', token=token)[0])
    leads = json.loads(send('/api/mobile/records/leads', token=token)[0])['records']
    assert len(leads) == overview['lead_count'] == 3
    assert 'qa-foreign' not in {lead['id'] for lead in leads}
    assert len(overview['monthly_paid']) == 12 and sum(overview['monthly_paid']) == overview['paid']
    for kind in ('clients', 'jobs', 'calendar'):
        assert json.loads(send('/api/mobile/records/' + kind, token=token)[0])['records']
    month = (datetime.now(ZoneInfo('America/Guatemala')) + timedelta(days=3)).strftime('%Y-%m')
    calendar = json.loads(send('/api/mobile/calendar?month=' + month, token=token)[0])
    assert calendar['month'] == month
    block = next(record for record in calendar['records'] if record['id'] == 'qa-block')
    assert block['event_type'] == 'block' and block['date'] < block['end_date']
    assert all(record['id'] != 'qa-released' for record in calendar['records'])
    assert json.loads(send('/api/mobile/payments', token=token)[0])['summary']['paid'] == overview['paid']
    teams = json.loads(send('/api/mobile/teams', token=token)[0])
    assert teams['enabled'] and len(teams['members']) == 2
    assert all('Astral' not in member['name'] for member in teams['members'])
    job = next(job for job in teams['jobs'] if job['id'] == 'qa-job-0')
    assert len(job['assignments']) == 2 and job['pending'] == 250000
    inbox = json.loads(send('/api/mobile/notifications', token=token)[0])
    assert inbox['count'] > 0
    # Read an unread item: a repeated QA run may have read the first row already.
    nid = next(item['id'] for item in inbox['notifications'] if not item['read'])
    assert json.loads(send(f'/api/mobile/notifications/{nid}/target', token=token)[0])['record'] is not None
    send('/api/mobile/notifications/read', 'POST', {'ids': [nid]}, token=token)
    assert json.loads(send('/api/mobile/notifications', token=token)[0])['count'] == inbox['count'] - 1
    send('/api/mobile/logout', 'POST', {}, token=token)
    print('Verified HTTPS QA: pairing, CSRF approval, native reads, cash chart, isolation, notification target/read and logout passed.')


if __name__ == '__main__':
    main()
