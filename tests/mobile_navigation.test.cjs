const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function shell({ path = '/leads', stored = 320, reduced = false, mobile = true, blockedStorage = false } = {}) {
  const handlers = {};
  const sheetHandlers = {};
  let sheetOpen = true;
  const positions = new Map([['flowcrm-tab-scroll:' + path, String(stored)]]);
  const links = [];
  const main = { scrollTop: 0, scrollTo(options) { this.scrollTop = options.top; this.scrollOptions = options; } };
  const nav = { addEventListener(type, fn) { handlers[type] = fn; }, querySelectorAll() { return links.filter(l => l.pending); } };
  const sessionStorage = {
    getItem(key) { if (blockedStorage) throw Error('blocked'); return positions.get(key); },
    setItem(key, value) { if (blockedStorage) throw Error('blocked'); positions.set(key, value); },
    removeItem(key) { if (blockedStorage) throw Error('blocked'); positions.delete(key); },
  };
  const location = new URL('https://flow.test' + path);
  vm.runInNewContext(fs.readFileSync('static/mobile-navigation.js', 'utf8'), {
    URL, sessionStorage,
    window: { location, closeMoreSheet() { sheetOpen = false; }, matchMedia: q => ({ matches: q.includes('reduced') ? reduced : mobile }), addEventListener(type, fn) { handlers[type] = fn; } },
    document: { querySelector: s => s === '.main-content' ? main : s === '.bottom-nav' ? nav : { addEventListener(type, fn) { sheetHandlers[type] = fn; } } },
  });
  function click(pathname, extra = {}) {
    const link = { href: new URL(pathname, location).href, pending: false,
      classList: { add() { link.pending = true; }, remove() { link.pending = false; } },
      setAttribute(_, value) { link.busy = value; }, removeAttribute() { delete link.busy; } };
    links.push(link);
    const event = { target: { closest: () => link }, button: 0, preventDefault() { this.prevented = true; }, ...extra };
    handlers.click(event);
    return { link, event };
  }
  return { main, handlers, positions, click, sheetHandlers, get sheetOpen() { return sheetOpen; } };
}

test('More menu closes immediately on navigation and when returning from browser history', () => {
  const s = shell();
  let prevented = false;
  s.sheetHandlers.click({ target: { closest: () => ({ href: '/emails' }) }, button: 0,
    preventDefault() { prevented = true; } });
  assert.equal(s.sheetOpen, false);
  assert.equal(prevented, false, 'menu links retain native navigation');
  const restored = shell();
  restored.handlers.pageshow();
  assert.equal(restored.sheetOpen, false);
});

test('tab navigation restores its position, saves the outgoing position, and clears pending state on browser back', () => {
  const s = shell();
  assert.equal(s.main.scrollTop, 320);
  s.main.scrollTop = 580;
  const { link, event } = s.click('/clients');
  assert.equal(event.prevented, undefined, 'normal links keep native document navigation');
  assert.equal(s.positions.get('flowcrm-tab-scroll:/leads'), '580');
  assert.equal(link.pending, true);
  assert.equal(link.busy, 'true');
  s.handlers.pageshow();
  assert.equal(link.pending, false);
  assert.equal(link.busy, undefined);
});

test('reselecting a tab returns to its top without a reload and respects reduced motion', () => {
  for (const reduced of [false, true]) {
    const s = shell({ reduced });
    assert.equal(s.click('/leads').event.prevented, true);
    assert.equal(s.main.scrollTop, 0);
    assert.equal(s.main.scrollOptions.behavior, reduced ? 'instant' : 'smooth');
    assert.equal(s.positions.has('flowcrm-tab-scroll:/leads'), false);
  }
});

test('modified clicks, external links, and desktop navigation retain browser behavior', () => {
  for (const extra of [{ metaKey: true }, { ctrlKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }, { defaultPrevented: true }]) {
    assert.equal(shell().click('/clients', extra).link.pending, false);
  }
  assert.equal(shell().click('https://other.test/clients').link.pending, false);
  assert.equal(shell({ mobile: false }).click('/clients').link.pending, false);
});

test('detail pages and disabled storage do not block navigation', () => {
  const detail = shell({ path: '/leads/example' });
  assert.equal(detail.main.scrollTop, 0);
  assert.equal(detail.click('/leads').event.prevented, undefined);
  const blocked = shell({ blockedStorage: true });
  assert.equal(blocked.click('/clients').link.pending, true);
  assert.doesNotThrow(() => blocked.handlers.pagehide());
});
