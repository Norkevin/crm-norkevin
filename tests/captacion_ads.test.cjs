const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync('static/captacion.js', 'utf8');
const tracking = fs.readFileSync('static/public-lead-ads.js', 'utf8');

async function submit({ brand = 'norkevin-photography', ok = true, tag = 'working', pixel = 'working' } = {}) {
  const calls = [];
  const metaCalls = [];
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {
      hidden: true, disabled: false, value: '', textContent: '',
      handlers: {}, addEventListener(name, fn) { this.handlers[name] = fn; },
      setAttribute() {}, removeAttribute() {}, focus() {},
    });
    return nodes.get(id);
  };
  const form = node('form-captacion');
  form.hidden = false;
  form.elements = { tenant_slug: { value: brand } };
  form.querySelector = () => node('submit');
  const window = {};
  const configs = {
    'norkevin-photography': { googleConversion: 'AW-10866273491/BtunCMWu5Y4dENPZuL0o', metaPixel: '899434420809998' },
    'astral-weddings': { googleConversion: 'AW-18491511938/Y4ZBCNr-9Y4dEIKpuPFE', metaPixel: '28915845924706844' },
  };
  node('lead-ads-config').dataset = configs[brand];
  const document = { getElementById: id => id === 'lead-ads-config' && !configs[brand] ? null : node(id) };
  if (tag !== 'blocked') window.gtag = (...args) => {
    if (tag === 'throws') throw new Error('Unavailable measurement');
    calls.push(args);
  };
  if (pixel !== 'blocked') window.fbq = (...args) => {
    if (pixel === 'throws') throw new Error('Unavailable pixel');
    metaCalls.push(args);
  };
  vm.runInNewContext(tracking, { window, document });
  vm.runInNewContext(source, {
    window, document,
    FormData: class { *[Symbol.iterator]() { yield ['tenant_slug', brand]; } },
    fetch: async () => ({ ok, json: async () => ({ ok, lead_id: 'lead-test' }) }),
    setTimeout, clearTimeout, AbortController, URLSearchParams,
  });
  assert.equal(metaCalls.length, 0, 'visiting does not count as a Meta lead');
  assert.equal(calls.length, 0, 'visiting does not count as conversion');
  await form.handlers.submit({ preventDefault() {} });
  await form.handlers.submit({ preventDefault() {} });
  return { calls, metaCalls, form, success: node('success-msg'), error: node('form-error') };
}

test('only a successfully saved Norkevin inquiry counts once, without personal fields', async () => {
  const saved = await submit();
  assert.equal(saved.calls.length, 1);
  assert.equal(JSON.stringify(saved.calls[0]), JSON.stringify([
    'event', 'conversion', { send_to: 'AW-10866273491/BtunCMWu5Y4dENPZuL0o', transaction_id: 'lead-test' },
  ]));
  assert.equal((await submit({ ok: false })).calls.length, 0);
  assert.equal((await submit({ brand: 'another-brand' })).calls.length, 0);
  for (const tag of ['blocked', 'throws']) {
    const result = await submit({ tag });
    assert.equal(result.success.hidden, false);
    assert.equal(result.form.hidden, true);
    assert.equal(result.error.hidden, true);
  }
});


test('Meta counts a saved Norkevin lead once, independently of Google and without form data', async () => {
  const saved = await submit();
  assert.equal(JSON.stringify(saved.metaCalls), JSON.stringify([
    ['trackSingle', '899434420809998', 'Lead', {}, { eventID: 'lead-test' }],
  ]));
  assert.equal((await submit({ ok: false })).metaCalls.length, 0);
  assert.equal((await submit({ brand: 'another-brand' })).metaCalls.length, 0);
  for (const state of ['blocked', 'throws']) {
    const result = await submit({ pixel: state });
    assert.equal(result.success.hidden, false);
    assert.equal(result.form.hidden, true);
    assert.equal(result.error.hidden, true);
    assert.equal(result.calls.length, 1);
    assert.equal((await submit({ tag: state })).metaCalls.length, 1);
  }
});

test('Astral saved leads reach only Astral accounts; repeat IDs are deduplicated', async () => {
  const result = await submit({ brand: 'astral-weddings' });
  assert.equal(JSON.stringify(result.calls), JSON.stringify([
    ['event', 'conversion', { send_to: 'AW-18491511938/Y4ZBCNr-9Y4dEIKpuPFE', transaction_id: 'lead-test' }],
  ]));
  assert.equal(JSON.stringify(result.metaCalls), JSON.stringify([
    ['trackSingle', '28915845924706844', 'Lead', {}, { eventID: 'lead-test' }],
  ]));
  assert.equal((await submit({ brand: 'astral-weddings', ok: false })).calls.length, 0);
  assert.equal((await submit({ brand: 'astral-weddings', ok: false })).metaCalls.length, 0);
  const calls = [];
  const window = { gtag: (...args) => calls.push(args) };
  vm.runInNewContext(tracking, { window, document: { getElementById: () => ({ dataset: {
    googleConversion: 'AW-18491511938/Y4ZBCNr-9Y4dEIKpuPFE', metaPixel: '28915845924706844',
  } }) } });
  window.trackSavedLead('lead-same');
  window.trackSavedLead('lead-same');
  window.trackSavedLead(null);
  assert.equal(calls.length, 1);
});
