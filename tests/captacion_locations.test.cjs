const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync('static/captacion.js', 'utf8');
const seed = JSON.parse(fs.readFileSync('data/seeds/venues_guatemala.default.json', 'utf8'));
const catalog = seed.venues.map(place => ({ name: place.name, aliases: place.aliases || [],
  detail: [...new Set([place.municipality, place.department, 'Guatemala'].filter(Boolean))].join(', ') }));

function setup(fetch = async () => ({ ok: true, json: async () => ({ features: [] }) })) {
  const nodes = new Map();
  function element() {
    return { hidden: false, value: '', textContent: '', children: [], attributes: {}, handlers: {},
      addEventListener(name, fn) { this.handlers[name] = fn; },
      setAttribute(name, value) { this.attributes[name] = value; },
      removeAttribute(name) { delete this.attributes[name]; },
      replaceChildren() { this.children = []; }, append(child) { this.children.push(child); },
      scrollIntoView() {}, focus() {},
    };
  }
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  };
  node('location-catalog').textContent = JSON.stringify(catalog);
  node('form-captacion').querySelector = () => node('submit');
  const timers = new Map();
  let timerId = 0;
  vm.runInNewContext(source, { window: {}, document: { getElementById: node, createElement: element },
    fetch, AbortController, URLSearchParams,
    setTimeout(fn, ms) { timers.set(++timerId, { fn, ms }); return timerId; },
    clearTimeout(id) { timers.delete(id); },
  });
  return {
    node,
    input(value) { node('locacion').value = value; node('locacion').handlers.input(); },
    key(key) { node('locacion').handlers.keydown({ key, preventDefault() {} }); },
    names() { return node('location-results').children.map(child => child.textContent); },
    runSearch() {
      const [id, timer] = [...timers].find(([, timer]) => timer.ms === 450);
      timers.delete(id);
      return timer.fn();
    },
  };
}

test('real catalog finds La Ruca immediately, including connectors and a small typo', () => {
  const search = setup(() => { throw new Error('Network is not needed for local matches'); });
  for (const query of ['Finca La Ruca', 'Finca de la Ruca', 'La Ruca', 'Finca La Ruka']) {
    search.input(query);
    assert.equal(search.names()[0], 'Finca La Ruca Jardín Boutique', query);
    assert.equal(search.node('locacion').value, query, 'typing never replaces free text');
  }
});

test('matches aliases, accents, partial names and municipality, without unrelated suggestions', () => {
  const search = setup();
  for (const [query, expected] of [['Hotel Pensativo', 'Pensativo House Hotel'],
    ['Santo Domingo Antigua', 'Casa Santo Domingo'], ['Jardin La Ruca', 'Finca La Ruca Jardín Boutique']]) {
    search.input(query);
    assert.ok(search.names().includes(expected), query);
  }
  search.input('zxqv neverland');
  assert.equal(search.names().length, 0);
  search.input('ru');
  assert.equal(search.node('location-results').hidden, true);
});

test('keeps local results and manual input usable when the external service fails', async () => {
  const search = setup(async () => { throw new TypeError('Offline'); });
  search.input('Finca de la Ruca');
  await search.runSearch();
  assert.equal(search.node('location-results').hidden, false);
  assert.match(search.node('location-status').textContent, /catálogo/);
  search.node('location-results').children[0].handlers.click();
  assert.match(search.node('locacion').value, /^Finca La Ruca Jardín Boutique, Sacatepéquez, Guatemala$/);
  assert.equal(search.node('location-results').hidden, true);
  search.input('Nuestra casa particular');
  await search.runSearch();
  assert.equal(search.node('locacion').value, 'Nuestra casa particular');
  assert.match(search.node('location-status').textContent, /escribir el lugar/);
});

test('merges external places without duplicates or moving the active keyboard selection', async () => {
  let resolve;
  const search = setup(() => new Promise(done => { resolve = done; }));
  search.input('Finca La Ruca');
  const pending = search.runSearch();
  search.key('ArrowDown');
  resolve({ ok: true, json: async () => ({ features: [
    { properties: { name: 'Finca La Ruca Jardín Boutique', state: 'Sacatepéquez', country: 'Guatemala' } },
    { properties: { name: 'Otro lugar encontrado', city: 'San Lucas' } },
  ] }) });
  await pending;
  assert.deepEqual(search.names(), ['Finca La Ruca Jardín Boutique', 'Otro lugar encontrado']);
  assert.equal(search.node('locacion').attributes['aria-activedescendant'], 'location-option-0');
  assert.equal(search.node('location-results').children[0].attributes['aria-selected'], 'true');
  search.key('Enter');
  assert.match(search.node('locacion').value, /^Finca La Ruca/);
  assert.equal(search.node('location-results').hidden, true);
});

test('a late external response cannot overwrite a new search, a selection or a closed menu', async () => {
  let resolve;
  const search = setup(() => new Promise(done => { resolve = done; }));
  search.input('Santo Domingo');
  const oldRequest = search.runSearch();
  search.input('Finca La Ruca');
  resolve({ ok: true, json: async () => ({ features: [{ properties: { name: 'Stale place' } }] }) });
  await oldRequest;
  assert.equal(search.names()[0], 'Finca La Ruca Jardín Boutique');
  const nextRequest = search.runSearch();
  search.key('Escape');
  resolve({ ok: true, json: async () => ({ features: [{ properties: { name: 'Another stale place' } }] }) });
  await nextRequest;
  assert.equal(search.node('location-results').hidden, true);
});

test('external-only results still work and are cached', async () => {
  let calls = 0;
  const search = setup(async () => {
    calls++;
    return { ok: true, json: async () => ({ features: [{ properties: { name: 'Lugar nuevo', city: 'Cobán' } }] }) };
  });
  search.input('Lugar nuevo');
  await search.runSearch();
  assert.deepEqual(search.names(), ['Lugar nuevo']);
  search.input('Lugar nuevo');
  await search.runSearch();
  assert.equal(calls, 1);
});

test('missing event date blocks submission before any lead or conversion request', async () => {
  let calls = 0;
  const form = setup(async () => { calls++; });
  await form.node('form-captacion').handlers.submit({ preventDefault() {} });
  assert.equal(calls, 0);
  assert.equal(form.node('form-error').hidden, false);
  assert.match(form.node('form-error').textContent, /Selecciona la fecha del evento/);
  assert.notEqual(form.node('submit').disabled, true);
});
