const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync('static/captacion.js', 'utf8');

async function submit({ brand = 'norkevin-photography', ok = true, tag = 'working' } = {}) {
  const calls = [];
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
  if (tag !== 'blocked') window.gtag = (...args) => {
    if (tag === 'throws') throw new Error('Unavailable measurement');
    calls.push(args);
  };
  vm.runInNewContext(source, {
    window, document: { getElementById: node },
    FormData: class { *[Symbol.iterator]() { yield ['tenant_slug', brand]; } },
    fetch: async () => ({ ok, json: async () => ({ ok, lead_id: 'lead-test' }) }),
    setTimeout, clearTimeout, AbortController, URLSearchParams,
  });
  assert.equal(calls.length, 0, 'visiting does not count as conversion');
  await form.handlers.submit({ preventDefault() {} });
  await form.handlers.submit({ preventDefault() {} });
  return { calls, form, success: node('success-msg'), error: node('form-error') };
}

test('only a successfully saved Norkevin inquiry counts once, without personal fields', async () => {
  const saved = await submit();
  assert.equal(saved.calls.length, 1);
  assert.equal(JSON.stringify(saved.calls[0]), JSON.stringify([
    'event', 'conversion', { send_to: 'AW-10866273491/BtunCMWu5Y4dENPZuL0o', transaction_id: 'lead-test' },
  ]));
  assert.equal((await submit({ ok: false })).calls.length, 0);
  assert.equal((await submit({ brand: 'astral-weddings' })).calls.length, 0);
  for (const tag of ['blocked', 'throws']) {
    const result = await submit({ tag });
    assert.equal(result.success.hidden, false);
    assert.equal(result.form.hidden, true);
    assert.equal(result.error.hidden, true);
  }
});
