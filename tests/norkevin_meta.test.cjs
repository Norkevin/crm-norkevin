const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync('static/norkevin-meta.js', 'utf8');

function visit({ search = '', stored = null, blocked = false, hostname = 'norkevinweddings.com', brand = 'norkevin' } = {}) {
  const links = [
    { href: 'https://flowingcrm.com/captacion/norkevin-photography?keep=1' },
    { href: 'https://flowingcrm.com/captacion/astral-weddings' },
    { href: 'https://www.instagram.com/norkevin/' },
  ];
  const inserted = [];
  const context = { URL, URLSearchParams, location: { search, hostname },
    document: {
      createElement: () => ({}),
      getElementsByTagName: () => [{ parentNode: { insertBefore: script => inserted.push(script) } }],
      querySelectorAll: () => links,
    },
    sessionStorage: {
      getItem() { if (blocked) throw Error('blocked'); return stored; },
      setItem(key, value) { if (blocked) throw Error('blocked'); stored = value; },
    },
  };
  context.window = context;
  vm.runInNewContext(brand === 'astral' ? fs.readFileSync('static/astral-meta.js', 'utf8') : source, context);
  return { links, stored, inserted, calls: Array.from(context.fbq.queue, args => Array.from(args)) };
}

test('public pixel uses explicit events and carries only valid Meta ad clicks to Norkevin', () => {
  const organic = visit();
  assert.equal(organic.links[0].href.includes('fbclid'), false);
  assert.equal(organic.inserted[0].src, 'https://connect.facebook.net/en_US/fbevents.js');
  assert.equal(JSON.stringify(organic.calls), JSON.stringify([
    ['set', 'autoConfig', false, '899434420809998'],
    ['init', '899434420809998'],
    ['trackSingle', '899434420809998', 'PageView'],
  ]));
  for (const options of [{ search: '?fbclid=real_Click-123' }, { stored: 'real_Click-123' }, { search: '?fbclid=real_Click-123', blocked: true }]) {
    const result = visit(options);
    assert.equal(result.links[0].href, 'https://flowingcrm.com/captacion/norkevin-photography?keep=1&fbclid=real_Click-123');
    assert.equal(result.links[1].href.includes('fbclid'), false);
    assert.equal(result.links[2].href.includes('fbclid'), false);
  }
  assert.equal(visit({ search: '?fbclid=bad%3Cvalue' }).links[0].href.includes('fbclid'), false);
  assert.equal(visit({ blocked: true }).links[0].href.includes('fbclid'), false);
  assert.equal(visit({ hostname: 'flowingcrm.com', search: '?fbclid=real_Click-123' }).links[0].href.includes('fbclid'), false);
});

test('Astral pixel and ad click stay confined to Astral when sharing FLOW', () => {
  const result = visit({ brand: 'astral', hostname: 'astralfilmsgt.com', search: '?fbclid=actual_astral_Click' });
  assert.equal(JSON.stringify(result.calls), JSON.stringify([
    ['set', 'autoConfig', false, '28915845924706844'],
    ['init', '28915845924706844'],
    ['trackSingle', '28915845924706844', 'PageView'],
  ]));
  assert.equal(result.links[1].href, 'https://flowingcrm.com/captacion/astral-weddings?fbclid=actual_astral_Click');
  assert.equal(result.links[0].href.includes('fbclid'), false);
  assert.equal(result.links[2].href.includes('fbclid'), false);
  assert.equal(visit({ brand: 'astral', hostname: 'astralfilmsgt.com' }).links[1].href.includes('fbclid'), false);
});
