const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const source = readFileSync(require('node:path').join(__dirname, '../static/invoice-email.js'), 'utf8');

function composer(deliveryStatus = 'pending') {
  const calls = [];
  const fields = new Map();
  function element(id) {
    if (!fields.has(id)) {
      const listeners = {};
      fields.set(id, {value: '', disabled: false, open: false,
        addEventListener: (name, action) => { listeners[name] = action; },
        fire: async name => listeners[name]?.({preventDefault() {}})});
    }
    return fields.get(id);
  }
  const field = name => element('invoice-email-' + name);
  const dialog = field('dialog');
  dialog.showModal = () => { dialog.open = true; };
  dialog.close = () => { dialog.open = false; dialog.fire('close'); };
  dialog.querySelector = () => element('editor');
  field('form').reset = () => {};
  field('form').reportValidity = () => true;
  const context = {document: {getElementById: element}, window: {},
    crypto: {randomUUID: () => '11111111-1111-4111-8111-111111111111'},
    fetch: async url => {
      calls.push(['GET', url]);
      return {ok: true, json: async () => ({ok: true, to_email: 'client@example.invalid',
        subject: 'Recordatorio', body: 'Saldo Q1,900\n[[INVOICE_LINK]]',
        preview_url: '/invoices/test/documento', company: 'Estudio', from_email: ''})};
    },
    apiPost: async (url, body) => {
      calls.push(['POST', url, body]);
      if (url === '/api/email-preview') return {html: body.body, unresolved: []};
      if (url.startsWith('/api/payments/')) return {mail_id: 'queued', delivery_status: deliveryStatus};
      return {ok: true};
    },
    flashToast: (...args) => calls.push(['toast', ...args]),
    location: {reload: () => calls.push(['reload'])}};
  vm.runInNewContext(source, context);
  return {calls, field, open: () => context.window.sendInvoice('test')};
}

test('opening and cancelling invoice review never queues or sends mail', async () => {
  const ui = composer();
  await ui.open();
  assert.equal(ui.field('dialog').open, true);
  assert.equal(ui.field('send').disabled, false);
  await ui.field('cancel').fire('click');
  assert.equal(ui.field('dialog').open, false);
  assert.equal(ui.calls.filter(([method, url]) => method === 'POST' && url !== '/api/email-preview').length, 0);
});

test('edited content must be previewed, and confirmation sends the reviewed content once', async () => {
  const ui = composer();
  await ui.open();
  ui.field('body').value = 'Mensaje editado\n/invoices/test/documento';
  await ui.field('body').fire('input');
  await ui.field('form').fire('submit');
  assert.equal(ui.field('send').disabled, true);
  assert.equal(ui.calls.filter(([, url]) => url === '/api/payments/test/send').length, 0);
  await ui.field('preview').fire('click');
  await Promise.all([ui.field('form').fire('submit'), ui.field('form').fire('submit')]);
  const sends = ui.calls.filter(([, url]) => url === '/api/payments/test/send');
  assert.equal(sends.length, 1);
  assert.equal(sends[0][2].body, 'Mensaje editado\n[[INVOICE_LINK]]');
  assert.equal(ui.calls.filter(([, url]) => url === '/api/pending-emails/queued/send').length, 1);
});

test('an already delivered confirmation is not approved or sent a second time', async () => {
  const ui = composer('sent');
  await ui.open();
  await ui.field('form').fire('submit');
  assert.equal(ui.calls.filter(([, url]) => url === '/api/pending-emails/queued/send').length, 0);
  assert.equal(ui.calls.filter(([kind]) => kind === 'reload').length, 1);
});
