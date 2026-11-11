const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../static/teams.js'), 'utf8');

function setup(referrer, saved = new Map()) {
  const events = {}, clicks = {}, frames = [];
  const main = {scrollTop: 0, scrollHeight: 1500, clientHeight: 700};
  const back = {href: '/teams/jobs', addEventListener: (key, callback) => clicks[key] = callback};
  const context = {
    URL, URLSearchParams,
    location: {origin: 'https://flow.example', pathname: '/teams/members', search: '', hash: ''},
    document: {referrer, scrollingElement: {scrollTop: 0}, querySelectorAll: () => [], getElementById: () => null,
      querySelector: selector => ({'[data-teams-owner]': {}, '[data-teams-back]': back, main})[selector] || null},
    sessionStorage: {getItem: key => saved.get(key), setItem: (key, value) => saved.set(key, value), removeItem: key => saved.delete(key)},
    history: {length: 3, back() { context.wentBack = true; }},
    addEventListener: (key, callback) => events[key] = callback,
    requestAnimationFrame: callback => frames.push(callback)
  };
  context.window = context;
  vm.runInNewContext(script, context);
  return {context, back, main, events, clicks, frames, saved};
}

test('back preserves the previous Teams query and uses real history', () => {
  const s = setup('https://flow.example/teams/jobs?view=past&list_year=2026#crew');
  assert.equal(s.back.href, '/teams/jobs?view=past&list_year=2026#crew');
  s.clicks.click({preventDefault() {}});
  assert.equal(s.context.wentBack, true);
  assert.equal(s.saved.get('flow-teams-restore-scroll'), '/teams/jobs?view=past&list_year=2026');
  s.main.scrollTop = 820;
  s.events.pagehide();
  assert.equal(s.saved.get('flow-teams-scroll:/teams/members'), '820');
});

test('return restores the main scrolling container after page initialization', () => {
  const s = setup('', new Map([
    ['flow-teams-restore-scroll', '/teams/members'],
    ['flow-teams-scroll:/teams/members', '520']
  ]));
  s.frames.forEach(callback => callback());
  assert.equal(s.main.scrollTop, 520);
});

test('desktop navigation remembers document scrolling when main does not scroll', () => {
  const s = setup('https://flow.example/teams/payments');
  s.main.clientHeight = s.main.scrollHeight;
  s.context.document.scrollingElement.scrollTop = 620;
  s.events.pagehide();
  assert.equal(s.saved.get('flow-teams-scroll:/teams/members'), '620');
});

for (const url of ['', 'https://external.example/teams/payments', 'https://flow.example/teams-portal']) {
  test(`direct or unrelated visit keeps a local fallback: ${url}`, () => {
    const s = setup(url);
    s.clicks.click({preventDefault() {}});
    assert.equal(s.back.href, '/teams/jobs');
    assert.equal(s.context.wentBack, undefined);
  });
}
