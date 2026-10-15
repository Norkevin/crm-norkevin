const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync('static/captacion.js', 'utf8');
const seed = JSON.parse(fs.readFileSync('data/seeds/venues_guatemala.default.json', 'utf8'));
const catalog = seed.venues.map(place => ({ name: place.name, aliases: place.aliases || [],
  detail: [...new Set([place.municipality, place.department, 'Guatemala'].filter(Boolean))].join(', ') }));

function setup(fetch = async () => ({ ok: true, json: async () => ({ features: [] }) }), { google = false } = {}) {
  const nodes = new Map();
  const documentHandlers = {};
  function element() {
    return { hidden: false, value: '', textContent: '', children: [], attributes: {}, handlers: {}, dataset: {},
      addEventListener(name, fn) { this.handlers[name] = fn; },
      setAttribute(name, value) { this.attributes[name] = value; },
      removeAttribute(name) { delete this.attributes[name]; },
      replaceChildren() { this.children = []; }, append(child) { this.children.push(child); },
      contains(target) { return target === this || this.children.some(child => child.contains(target)); },
      scrollIntoView() {}, focus() {},
    };
  }
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  };
  node('location-catalog').textContent = JSON.stringify(catalog);
  node('locacion').dataset.googlePlaces = String(google);
  node('form-captacion').elements = { tenant_slug: { value: 'norkevin-photography' } };
  node('form-captacion').querySelector = () => node('submit');
  const timers = new Map();
  let timerId = 0;
  vm.runInNewContext(source, { window: {}, document: { getElementById: node, createElement: element,
    addEventListener(name, fn) { documentHandlers[name] = fn; } },
    fetch, AbortController, URLSearchParams,
    setTimeout(fn, ms) { timers.set(++timerId, { fn, ms }); return timerId; },
    clearTimeout(id) { timers.delete(id); },
  });
  return {
    node,
    input(value) { node('locacion').value = value; node('locacion').handlers.input(); },
    key(key) { node('locacion').handlers.keydown({ key, preventDefault() {} }); },
    pointerDown(target) { documentHandlers.pointerdown({ target }); },
    names() { return node('location-results').children.map(child => child.textContent); },
    scheduledSearches() { return [...timers.values()].filter(timer => timer.ms === 200).length; },
    runSearch() {
      const [id, timer] = [...timers].find(([, timer]) => timer.ms === 200);
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

test('reported Jardin Vilaflor is available locally without enabling paid requests', () => {
  const search = setup();
  for (const query of ['jardin vilaflor', 'Jardín Vilaflor', 'jardin villaflor']) {
    search.input(query);
    assert.equal(search.names()[0], 'Jardín Vilaflor');
  }
});

test('Safari blur keeps a touched suggestion available until its click selects it', () => {
  const search = setup();
  search.input('jardin vilaflor');
  const option = search.node('location-results').children[0];
  search.pointerDown(option.children[0]);
  search.node('locacion').handlers.blur();
  assert.equal(search.node('location-results').hidden, false);
  option.handlers.click();
  assert.equal(search.node('locacion').value, 'Jardín Vilaflor, Sacatepéquez, Guatemala');
  assert.equal(search.node('location-results').hidden, true);
  assert.equal(search.scheduledSearches(), 0);
  assert.match(search.node('location-status').textContent, /Ubicación seleccionada/);
});

test('outside touches and Tab dismiss suggestions without swallowing keyboard focus', () => {
  const search = setup();
  search.input('jardin vilaflor');
  search.pointerDown(search.node('locacion'));
  assert.equal(search.node('location-results').hidden, false);
  search.pointerDown(search.node('notas'));
  assert.equal(search.node('location-results').hidden, true);
  search.input('jardin vilaflor');
  search.node('locacion').handlers.keydown({ key: 'Tab', preventDefault() { throw new Error('Tab must move focus'); } });
  assert.equal(search.node('location-results').hidden, true);
  assert.equal(search.scheduledSearches(), 0);
  assert.equal(search.node('locacion').value, 'jardin vilaflor');
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
  assert.match(search.node('location-status').textContent, /lugar sugerido/);
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

test('repeating an external search shows cached results immediately without another wait or request', async () => {
  let calls = 0;
  const search = setup(async () => {
    calls++;
    return { ok: true, json: async () => ({ features: [{ properties: { name: 'Lugar nuevo', city: 'Cobán' } }] }) };
  });
  search.input('Lugar nuevo');
  await search.runSearch();
  assert.deepEqual(search.names(), ['Lugar nuevo']);
  search.input('LUGAR NUEVO');
  assert.deepEqual(search.names(), ['Lugar nuevo']);
  assert.equal(search.scheduledSearches(), 0);
  assert.equal(calls, 1);
});

test('keeps matching external suggestions visible while refining a query, even if the next request fails', async () => {
  let calls = 0;
  const search = setup(async () => {
    calls++;
    if (calls === 2) throw new TypeError('Slow or offline external service');
    return { ok: true, json: async () => ({ features: calls === 1 ? [{ properties: {
      name: 'Arco de Santa Catalina', city: 'Antigua Guatemala',
    } }] : [] }) };
  });
  search.input('Arco Santa');
  await search.runSearch();
  search.input('Arco Santa Catalina');
  assert.deepEqual(search.names(), ['Arco de Santa Catalina']);
  assert.equal(search.node('location-results').hidden, false);
  await search.runSearch();
  assert.deepEqual(search.names(), ['Arco de Santa Catalina']);
  assert.equal(search.node('location-results').hidden, false);
  search.input('Arco Catalina Antigua');
  await search.runSearch();
  assert.deepEqual(search.names(), ['Arco de Santa Catalina']);
  search.input('Otro destino sin relación');
  assert.equal(search.node('location-results').hidden, true);
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

test('Google results appear and are attributed without waiting for slow Photon', async () => {
  let finishPhoton;
  const search = setup(async (url, options) => {
    if (url.startsWith('https://')) {
      assert.equal(new URL(url).searchParams.get('countrycode'), 'GT');
      return new Promise(resolve => { finishPhoton = resolve; });
    }
    assert.equal(url, '/api/captacion/places');
    assert.equal(JSON.parse(options.body).tenant_slug, 'norkevin-photography');
    return { ok: true, json: async () => ({ ok: true, items: [{
      name: 'Jardín Vilaflor', detail: 'Sacatepéquez, Guatemala',
      label: 'Jardín Vilaflor, Sacatepéquez, Guatemala', provider: 'google',
    }] }) };
  }, { google: true });
  search.input('jardin vilaflor');
  const pending = search.runSearch();
  await new Promise(setImmediate);
  assert.equal(search.names()[0], 'Jardín Vilaflor');
  assert.equal(search.node('location-results').children[0].children[1].textContent, 'Google Maps');
  finishPhoton({ ok: true, json: async () => ({ features: [] }) });
  await pending;
});

test('Google failure keeps Photon and catalog usable; Google predictions never enter the reusable cache', async () => {
  let googleCalls = 0, photonCalls = 0;
  const search = setup(async url => {
    if (url.startsWith('https://')) {
      photonCalls++;
      return { ok: true, json: async () => ({ features: [] }) };
    }
    googleCalls++;
    return googleCalls === 1 ? { ok: true, json: async () => ({ ok: true, items: [{
      name: 'Google-only location', detail: 'Guatemala', label: 'Google-only location, Guatemala', provider: 'google',
    }] }) } : { ok: false };
  }, { google: true });
  search.input('jardin vilaflor');
  await search.runSearch();
  assert.equal(search.names()[0], 'Google-only location');
  search.input('jardin vilaflor');
  assert.ok(!search.names().includes('Google-only location'));
  await search.runSearch();
  assert.equal(search.names()[0], 'Jardín Vilaflor');
  assert.equal(googleCalls, 2);
  assert.equal(photonCalls, 1);
});

test('late Google results cannot reopen a chosen location', async () => {
  let finishGoogle;
  const search = setup(async url => url.startsWith('https://')
    ? { ok: true, json: async () => ({ features: [] }) }
    : new Promise(resolve => { finishGoogle = resolve; }), { google: true });
  search.input('jardin vilaflor');
  const pending = search.runSearch();
  search.input('Finca La Ruca');
  search.key('ArrowDown');
  search.key('Enter');
  finishGoogle({ ok: true, json: async () => ({ ok: true, items: [{
    name: 'Stale Google place', label: 'Stale Google place', provider: 'google',
  }] }) });
  await pending;
  assert.match(search.node('locacion').value, /^Finca La Ruca/);
  assert.equal(search.node('location-results').hidden, true);
});

test('new Google results never discard the active keyboard option', async () => {
  const search = setup(async url => url.startsWith('https://')
    ? { ok: true, json: async () => ({ features: [] }) }
    : { ok: true, json: async () => ({ ok: true, items: Array.from({ length: 5 }, (_, index) => ({
      name: `Google venue ${index}`, label: `Google venue ${index}`, provider: 'google',
    })) }) }, { google: true });
  search.input('Finca');
  search.key('ArrowUp');
  const selected = search.names().at(-1);
  await search.runSearch();
  search.key('Enter');
  assert.ok(search.node('locacion').value.startsWith(selected));
});

test('personal contact details and very long manual addresses are not sent to either search provider', () => {
  const search = setup(() => assert.fail('No provider should be called'), { google: true });
  for (const value of ['cliente@example.com', '+502 1234 5678', 'x'.repeat(161)]) {
    search.input(value);
    assert.equal(search.scheduledSearches(), 0);
    assert.equal(search.node('locacion').value, value);
  }
});
