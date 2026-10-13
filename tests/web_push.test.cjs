const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createHash, webcrypto } = require('node:crypto');
const { TextEncoder } = require('node:util');

const root = path.resolve(__dirname, '..');
const pushSource = fs.readFileSync(path.join(root, 'static/flow-web-push.js'), 'utf8');
const workerSource = fs.readFileSync(path.join(root, 'service-worker.js'), 'utf8');
const endpoint = 'https://push.example.test/subscriptions/this-browser';
const endpointID = value => createHash('sha256').update(value).digest('hex');
const trusted = { isTrusted: true };

function browser(options = {}) {
  const calls = [], order = [], documentEvents = new Map(), elements = new Map();
  for (const id of ['status', 'enable', 'disable', 'test', 'feedback']) {
    elements.set(id, { hidden: false, disabled: false, textContent: '', listeners: new Map(),
      addEventListener(type, listener) { this.listeners.set(type, listener); } });
  }
  let local = null;
  function subscription(value = endpoint) {
    return {
      endpoint: value,
      toJSON: () => ({ endpoint: value, expirationTime: null, keys: { p256dh: 'public-key', auth: 'auth-key' } }),
      async unsubscribe() {
        order.push('browser:unsubscribe');
        if (options.unsubscribeError) throw Error(options.unsubscribeError);
        if (options.unsubscribeResult === false) return false;
        local = null;
        return true;
      },
    };
  }
  if (options.local) local = subscription();
  const server = {
    available: true, vapid_public_key: 'AQID', csrf: 'csrf-this-account',
    has_subscription: false, subscription_ids: [], ...options.status,
  };
  const notification = {
    permission: options.permission || 'default',
    requestPermission() {
      order.push('browser:permission');
      if (options.permissionError) throw Error(options.permissionError);
      notification.permission = options.permissionResult || 'granted';
      return Promise.resolve(notification.permission);
    },
  };
  const registration = {
    waiting: options.waiting || null,
    pushManager: {
      getSubscription: async () => local,
      async subscribe(value) {
        calls.push({ action: 'browser:subscribe', value });
        order.push('browser:subscribe');
        local = subscription();
        return local;
      },
    },
  };
  const navigator = {
    userAgent: options.iphone ? 'Mozilla/5.0 (iPhone)' : 'Desktop test',
    platform: 'test', maxTouchPoints: 0, standalone: options.standalone || false,
    userActivation: { isActive: options.userActive !== false },
  };
  const context = {
    navigator, crypto: webcrypto, TextEncoder,
    atob: value => Buffer.from(value, 'base64').toString('binary'),
    document: {
      getElementById: id => elements.get(id.replace(/^web-push-/, '')) || null,
      addEventListener: (type, listener) => documentEvents.set(type, listener),
    },
    isSecureContext: options.secure !== false,
    matchMedia: () => ({ matches: options.standalone || false }),
    Notification: notification,
    async fetch(url, value) {
      const route = url.split('/').pop();
      calls.push({ action: route, url, options: value });
      order.push('server:' + route);
      if (options.responses && options.responses[route]) return options.responses[route](value);
      const data = route === 'status' ? server : ['subscribe', 'bind'].includes(route) ? { ok: true, id: endpointID(endpoint) } : { ok: true };
      return { ok: true, json: async () => data };
    },
  };
  context.window = context;
  if (!options.unsupported) {
    context.PushManager = function () {};
    navigator.serviceWorker = {
      getRegistration: async () => options.noRegistration ? null : registration,
      async register(url) { calls.push({ action: 'browser:register', url }); return registration; },
      ready: Promise.resolve(registration),
    };
  }
  vm.runInNewContext(pushSource, context, { filename: 'flow-web-push.js' });
  return {
    api: context.FlowWebPush, calls, order, server, navigator, notification, elements,
    status: () => elements.get('status').textContent,
    async init() { documentEvents.get('DOMContentLoaded')(); await context.FlowWebPush.refresh(); },
    post: route => calls.find(call => call.action === route),
  };
}

function response(data, ok = true) { return { ok, json: async () => data }; }

function worker(options = {}) {
  const listeners = new Map(), notices = [], opened = [], cacheCalls = [], network = [];
  const cache = {
    async addAll(urls) { cacheCalls.push({ action: 'addAll', urls }); },
    async put(request, value) { cacheCalls.push({ action: 'put', request, value }); },
  };
  const self = {
    location: { origin: 'https://flowing.test' },
    addEventListener: (type, listener) => listeners.set(type, listener),
    registration: { async showNotification(title, value) { notices.push({ title, ...value }); } },
    clients: {
      matchAll: async () => options.clients || [],
      async openWindow(url) { opened.push(url); },
      async claim() {},
    },
    skipWaiting() {},
  };
  vm.runInNewContext(workerSource, {
    self, URL,
    caches: {
      async open(name) { cacheCalls.push({ action: 'open', name }); return cache; },
      async match(request) { cacheCalls.push({ action: 'match', request }); return options.cached; },
      keys: async () => [], delete: async () => true,
    },
    async fetch(request) {
      network.push(request);
      if (options.offline) throw Error('offline');
      return { ok: true, clone: () => ({ copied: true }) };
    },
  }, { filename: 'service-worker.js' });
  async function dispatch(type, value = {}) {
    const pending = [];
    let intercepted = false, result;
    listeners.get(type)({ ...value,
      waitUntil: promise => pending.push(promise),
      respondWith(promise) { intercepted = true; result = promise; },
    });
    await Promise.all(pending);
    const resolved = await result;
    await Promise.resolve();
    return { intercepted, result: resolved };
  }
  return {
    notices, opened, cacheCalls, network, dispatch,
    push: payload => dispatch('push', { data: { json: () => payload } }),
    async click(url) {
      let closed = false;
      await dispatch('notificationclick', { notification: { data: { url }, close() { closed = true; } } });
      assert.equal(closed, true);
    },
  };
}

test('initialization binds controls and checks registration without asking for permission', async () => {
  const b = browser({ noRegistration: true });
  await b.init();
  assert.equal(b.order.includes('browser:permission'), false);
  assert.equal(b.api.isRegistered(), false);
  assert.equal(b.calls.filter(call => call.action === 'status').length, 1);
  assert.equal(b.post('status').options.cache, 'no-store');
  assert.equal(b.post('status').options.credentials, 'same-origin');
  assert.equal(b.post('browser:register').url, '/service-worker.js');
  for (const id of ['enable', 'disable', 'test']) assert.equal(typeof b.elements.get(id).listeners.get('click'), 'function');
});

test('iPhone requires installation before it can request notification permission', async () => {
  const b = browser({ iphone: true });
  await b.init();
  await b.api.activate(trusted);
  assert.match(b.status(), /pantalla de inicio/);
  assert.equal(b.elements.get('enable').disabled, true);
  assert.equal(b.order.includes('browser:permission'), false);
  assert.equal(b.post('subscribe'), undefined);
});

test('unsupported, insecure, denied, unavailable, and waiting states explain why activation is disabled', async () => {
  for (const [options, text] of [
    [{ unsupported: true }, /no permite alertas/],
    [{ secure: false }, /no permite alertas/],
    [{ permission: 'denied' }, /Alertas bloqueadas/],
    [{ status: { available: false } }, /servidor.*no.*listo/],
    [{ waiting: {} }, /versión nueva/],
  ]) {
    const b = browser(options);
    await b.init();
    await b.api.activate(trusted);
    assert.match(b.status(), text);
    assert.equal(b.elements.get('enable').disabled, true);
    assert.equal(b.api.isRegistered(), false);
    assert.equal(b.order.includes('browser:permission'), false);
    assert.equal(b.post('subscribe'), undefined);
  }
});

test('permission requests need a trusted active gesture and happen before awaiting network', async () => {
  const b = browser({ iphone: true, standalone: true });
  await b.init();
  await b.api.activate();
  await b.api.activate({ isTrusted: false });
  b.navigator.userActivation.isActive = false;
  await b.api.activate(trusted);
  assert.equal(b.order.includes('browser:permission'), false);
  b.navigator.userActivation.isActive = true;
  const activation = b.elements.get('enable').listeners.get('click')(trusted);
  assert.equal(b.order.at(-1), 'browser:permission', 'permission starts in the click handler');
  await activation;
  assert.equal(b.api.isRegistered(), true);
});

test('granted permission can create a subscription with CSRF protection and private details by default', async () => {
  const b = browser({ permission: 'granted' });
  await b.init();
  await b.api.activate(trusted);
  const request = b.post('subscribe');
  assert.equal(request.options.method, 'POST');
  assert.equal(request.options.headers['X-CSRF-Token'], 'csrf-this-account');
  assert.equal(request.options.headers['Content-Type'], 'application/json');
  assert.deepEqual(JSON.parse(request.options.body), {
    subscription: { endpoint, expirationTime: null, keys: { p256dh: 'public-key', auth: 'auth-key' } }, hide_details: true,
  });
  const subscribe = b.post('browser:subscribe').value;
  assert.equal(subscribe.userVisibleOnly, true);
  assert.deepEqual(Array.from(subscribe.applicationServerKey), [1, 2, 3]);
  assert.equal(b.order.includes('browser:permission'), false);
  assert.equal(b.api.isRegistered(), true);
  assert.equal(b.elements.get('test').hidden, false);
});

test('a local subscription or another server subscription does not falsely register this browser', async () => {
  const b = browser({ permission: 'granted', local: true,
    status: { has_subscription: true, subscription_ids: [endpointID('https://push.example.test/another-browser')] } });
  await b.init();
  assert.equal(b.api.isRegistered(), false);
  assert.equal(b.post('bind'), undefined);
  assert.equal(b.elements.get('test').hidden, true);
  assert.match(b.status(), /necesita una suscripción registrada/);
  await b.api.activate(trusted);
  assert.equal(b.order.indexOf('browser:unsubscribe') < b.order.indexOf('browser:subscribe'), true);
  assert.equal(b.api.isRegistered(), true);
});

test('a new web session binds an existing owned browser subscription without permission or subscribe', async () => {
  const b = browser({ permission: 'granted', local: true, status: { subscription_ids: [endpointID(endpoint)] } });
  await b.init();
  const bind = b.post('bind');
  assert.deepEqual(JSON.parse(bind.options.body), { endpoint });
  assert.equal(bind.options.method, 'POST');
  assert.equal(bind.options.headers['X-CSRF-Token'], 'csrf-this-account');
  assert.equal(bind.options.credentials, 'same-origin');
  assert.equal(b.api.isRegistered(), true);
  assert.equal(b.order.includes('browser:permission'), false);
  assert.equal(b.order.includes('browser:unsubscribe'), false);
  assert.equal(b.post('subscribe'), undefined);
});

test('foreign or inactive bind failure stays unconfirmed and retry never reactivates a subscription', async () => {
  for (const error of ['Foreign subscription', 'Inactive subscription']) {
    let active = false;
    const b = browser({ permission: 'granted', local: true, status: { subscription_ids: [endpointID(endpoint)] },
      responses: { bind: () => active ? response({ ok: true, id: endpointID(endpoint) }) : response({ ok: false, error }, false) } });
    await b.init();
    assert.equal(b.api.isRegistered(), false);
    assert.match(b.status(), /No se pudo comprobar/);
    assert.equal(b.elements.get('enable').textContent, 'Reintentar comprobación');
    assert.equal(b.elements.get('enable').disabled, false);
    await b.api.activate(trusted);
    assert.equal(b.api.isRegistered(), false);
    assert.equal(b.post('subscribe'), undefined);
    assert.equal(b.order.includes('browser:permission'), false);
    assert.equal(b.order.includes('browser:unsubscribe'), false);
    active = true;
    await b.api.activate(trusted);
    assert.equal(b.api.isRegistered(), true);
    assert.equal(b.post('subscribe'), undefined);
  }
});

test('bind requires an explicit matching server confirmation before showing registered', async () => {
  for (const data of [{}, { ok: true, id: 'another-browser' }]) {
    const b = browser({ permission: 'granted', local: true, status: { subscription_ids: [endpointID(endpoint)] },
      responses: { bind: () => response(data) } });
    await b.init();
    assert.equal(b.api.isRegistered(), false);
    assert.equal(b.elements.get('test').hidden, true);
    assert.equal(b.post('subscribe'), undefined);
    assert.equal(b.order.includes('browser:permission'), false);
  }
});

test('a stopped worker can restore logout binding but cannot promise alerts or queue a test', async () => {
  const b = browser({ permission: 'granted', local: true,
    status: { enabled: true, worker_ready: false, available: false, subscription_ids: [endpointID(endpoint)] } });
  await b.init();
  assert.ok(b.post('bind'));
  assert.equal(b.api.isRegistered(), false);
  assert.match(b.status(), /servidor.*no.*listo/);
  assert.equal(b.elements.get('test').disabled, true);
  await b.api.test(trusted);
  await b.api.activate(trusted);
  assert.equal(b.post('test'), undefined);
  assert.equal(b.post('subscribe'), undefined);
  assert.equal(b.order.includes('browser:permission'), false);
});

test('a missing or incorrect server confirmation leaves activation unconfirmed', async () => {
  for (const data of [{}, { ok: true, id: 'another-device' }, { ok: false, error: 'Subscription refused' }]) {
    const b = browser({ permission: 'granted', responses: { subscribe: () => response(data) } });
    await b.init();
    await b.api.activate(trusted);
    assert.equal(b.api.isRegistered(), false);
    assert.equal(b.elements.get('test').hidden, true);
    assert.match(b.status(), /no confirmó|Subscription refused/);
    assert.equal(b.elements.get('enable').disabled, false);
  }
});

test('permission dismissed in the browser does not create or confirm a subscription', async () => {
  const b = browser({ permissionResult: 'default' });
  await b.init();
  await b.api.activate(trusted);
  assert.equal(b.post('browser:subscribe'), undefined);
  assert.equal(b.post('subscribe'), undefined);
  assert.equal(b.api.isRegistered(), false);
  assert.equal(b.elements.get('enable').disabled, false);
});

test('a synchronous permission error clears busy state and reports an actionable failure', async () => {
  const b = browser({ permissionError: 'Browser permission API failed' });
  await b.init();
  await b.api.activate(trusted);
  assert.equal(b.status(), 'Browser permission API failed');
  assert.equal(b.api.isRegistered(), false);
  assert.equal(b.elements.get('enable').disabled, false);
  assert.equal(b.post('subscribe'), undefined);
});

test('revoked browser permission cannot appear registered or send a test', async () => {
  const b = browser({ permission: 'granted', local: true, status: { subscription_ids: [endpointID(endpoint)] } });
  await b.init();
  assert.equal(b.api.isRegistered(), true);
  b.notification.permission = 'denied';
  await b.api.refresh();
  await b.api.test(trusted);
  assert.equal(b.api.isRegistered(), false);
  assert.equal(b.post('test'), undefined);
  assert.match(b.status(), /Alertas bloqueadas/);
});

test('deactivation removes the server endpoint before cancelling this browser subscription', async () => {
  const b = browser({ permission: 'granted', local: true, status: { subscription_ids: [endpointID(endpoint)] } });
  await b.init();
  assert.equal(b.api.isRegistered(), true);
  await b.api.deactivate({ isTrusted: false });
  assert.equal(b.post('unsubscribe'), undefined);
  await b.api.deactivate(trusted);
  assert.equal(b.order.indexOf('server:unsubscribe') < b.order.indexOf('browser:unsubscribe'), true);
  assert.deepEqual(JSON.parse(b.post('unsubscribe').options.body), { endpoint });
  assert.equal(b.post('unsubscribe').options.headers['X-CSRF-Token'], 'csrf-this-account');
  assert.equal(b.api.isRegistered(), false);
  assert.equal(b.elements.get('disable').hidden, true);
  assert.match(b.status(), /desactivadas.*otros dispositivos/);
});

test('a server unsubscribe error leaves the local endpoint registered and reports the error', async () => {
  const b = browser({ permission: 'granted', local: true, status: { subscription_ids: [endpointID(endpoint)] },
    responses: { unsubscribe: () => response({ ok: false, error: 'Server unsubscribe failed' }, false) } });
  await b.init();
  await b.api.deactivate(trusted);
  assert.equal(b.order.includes('browser:unsubscribe'), false);
  assert.equal(b.api.isRegistered(), true);
  assert.equal(b.elements.get('disable').hidden, false);
  assert.equal(b.status(), 'Server unsubscribe failed');
});

test('a failed browser unsubscribe reports partial cancellation without a false success', async () => {
  const b = browser({ permission: 'granted', local: true, unsubscribeResult: false,
    status: { subscription_ids: [endpointID(endpoint)] } });
  await b.init();
  await b.api.deactivate(trusted);
  assert.equal(b.api.isRegistered(), false);
  assert.equal(b.elements.get('disable').hidden, false);
  assert.match(b.status(), /ya dejó de enviar.*no terminó de cancelar/);
  assert.doesNotMatch(b.status(), /Alertas desactivadas/);
});

test('the test alert targets this endpoint and confirms queuing rather than delivery', async () => {
  const b = browser({ permission: 'granted', local: true, status: { subscription_ids: [endpointID(endpoint)] } });
  await b.init();
  await b.api.test({ isTrusted: false });
  assert.equal(b.post('test'), undefined);
  await b.api.test(trusted);
  assert.deepEqual(JSON.parse(b.post('test').options.body), { endpoint });
  assert.equal(b.post('test').options.headers['X-CSRF-Token'], 'csrf-this-account');
  assert.match(b.status(), /en cola.*no confirma su recepción/);
});

test('failed test delivery and status fetch show errors instead of claiming success', async () => {
  const b = browser({ permission: 'granted', local: true, status: { subscription_ids: [endpointID(endpoint)] },
    responses: { test: () => response({ ok: false, error: 'Could not queue test' }, false) } });
  await b.init();
  await b.api.test(trusted);
  assert.equal(b.status(), 'Could not queue test');
  const unavailable = browser({ responses: { status: () => { throw Error('offline'); } } });
  await unavailable.init();
  assert.match(unavailable.status(), /No se pudo comprobar.*bandeja sigue disponible/);
  assert.equal(unavailable.api.isRegistered(), false);
});

test('push events always show a generic visible alert when payload is absent, malformed, or private', async () => {
  for (const payload of [undefined, null, 'not an object', [], {},
    { body: 'Private client and payment details', url: '/payments/invoice_1' },
    { hide_details: true, body: 'Private client and payment details', url: '/payments/invoice_1' },
    { hide_details: 0, body: 'Private client and payment details', url: '/leads/client_1' }]) {
    const sw = worker();
    await sw.push(payload);
    assert.equal(sw.notices.length, 1);
    assert.equal(sw.notices[0].title, 'Flowing');
    assert.equal(sw.notices[0].body, 'Tienes actividad nueva. Abre Flowing para verla.');
    assert.equal(sw.notices[0].data.url, '/notifications');
  }
  const sw = worker();
  await sw.dispatch('push', { data: { json() { throw SyntaxError('invalid JSON'); } } });
  assert.equal(sw.notices.length, 1);
  assert.equal(sw.notices[0].data.url, '/notifications');
  assert.equal(sw.notices[0].body, 'Tienes actividad nueva. Abre Flowing para verla.');
});

test('explicitly visible details allow an internal route and sanitize notification text', async () => {
  const sw = worker();
  await sw.push({ hide_details: false, body: 'New\nactivity\u0000' + 'x'.repeat(300),
    url: '/jobs/job_42', notification_id: 'a'.repeat(64) });
  const notice = sw.notices[0];
  assert.equal(notice.body.length, 240);
  assert.equal(/[\u0000-\u001f]/.test(notice.body), false);
  assert.equal(notice.data.url, '/jobs/job_42');
  assert.equal(notice.tag, 'flowing-' + 'a'.repeat(32));
});

test('unsafe payload routes fall back to the inbox, including traversal before URL normalization', async () => {
  for (const url of ['https://evil.test/leads', '//evil.test/leads', 'javascript:alert(1)',
    'https://user:password@flowing.test/leads', '/leads\\evil', '/leads\n/private',
    '/notifications/../leads', '/notifications/%2e%2e/leads', '/leads/%2e%2e%2fjobs',
    '/leads/a%2Fb', '/leads/../jobs/id', '/jobs/id?token=x', '/jobs/id#section', '/settings']) {
    const sw = worker();
    await sw.push({ hide_details: false, body: 'Activity', url });
    assert.equal(sw.notices[0].data.url, '/notifications', url);
  }
});

test('notification clicks never open an external or disallowed URL', async () => {
  for (const url of ['https://evil.test/leads', '//evil.test/leads', '/notifications/../leads', '/settings', undefined]) {
    const sw = worker();
    await sw.click(url);
    assert.deepEqual(sw.opened, ['https://flowing.test/notifications']);
  }
  const sw = worker();
  await sw.click('/leads/lead_1');
  assert.deepEqual(sw.opened, ['https://flowing.test/leads/lead_1']);
});

test('notification clicks reuse an app window and ignore windows on other origins', async () => {
  const visited = [], focused = [];
  const external = { url: 'https://evil.test/leads', async navigate(url) { assert.fail('external window must not navigate: ' + url); } };
  const app = { url: 'https://flowing.test/calendar', async navigate(url) { visited.push(url); return this; },
    async focus() { focused.push(this.url); return this; } };
  const sw = worker({ clients: [external, app] });
  await sw.click('/teams/jobs/secondary:cal-id');
  assert.deepEqual(visited, ['https://flowing.test/teams/jobs/secondary:cal-id']);
  assert.equal(focused.length, 1);
  assert.deepEqual(sw.opened, []);
});

test('private API, authenticated pages, other origins, and mutations never write to the cache', async () => {
  for (const request of [
    { url: 'https://flowing.test/api/web-push/status', method: 'GET', mode: 'cors' },
    { url: 'https://flowing.test/api/clients', method: 'GET', mode: 'cors' },
    { url: 'https://flowing.test/payments', method: 'GET', mode: 'cors' },
    { url: 'https://evil.test/static/public.js', method: 'GET', mode: 'cors' },
    { url: 'https://flowing.test/api/web-push/subscribe', method: 'POST', mode: 'cors' },
  ]) {
    const sw = worker();
    assert.equal((await sw.dispatch('fetch', { request })).intercepted, false);
    assert.deepEqual(sw.cacheCalls, []);
  }
  const sw = worker();
  const request = { url: 'https://flowing.test/clients', method: 'GET', mode: 'navigate' };
  assert.equal((await sw.dispatch('fetch', { request })).intercepted, true);
  assert.deepEqual(sw.network, [request]);
  assert.deepEqual(sw.cacheCalls, []);
});

test('only static assets and manifest are cached; offline navigation uses the public fallback', async () => {
  for (const url of ['https://flowing.test/static/app.js?v=2', 'https://flowing.test/manifest.webmanifest']) {
    const sw = worker();
    const request = { url, method: 'GET', mode: 'cors' };
    assert.equal((await sw.dispatch('fetch', { request })).intercepted, true);
    assert.deepEqual(sw.network, [request]);
    assert.equal(sw.cacheCalls.filter(call => call.action === 'put').length, 1);
    assert.equal(sw.cacheCalls.find(call => call.action === 'put').request, request);
  }
  const fallback = { publicOfflinePage: true };
  const sw = worker({ offline: true, cached: fallback });
  const result = await sw.dispatch('fetch', { request: { url: 'https://flowing.test/payments', method: 'GET', mode: 'navigate' } });
  assert.equal(result.result, fallback);
  assert.equal(sw.cacheCalls[0].request, '/offline.html');
  assert.equal(sw.cacheCalls.some(call => call.action === 'put'), false);
});

test('push refreshes existing windows without leaking private payloads', async () => {
  const messages = [];
  const sw = worker({ clients: [{postMessage: message => messages.push(message)}] });
  await sw.push({hide_details: true, body: 'Private client', notification_id: 'a'.repeat(64)});
  assert.equal(sw.notices.length, 1);
  assert.equal(JSON.stringify(messages), JSON.stringify([{type:'FLOW_NOTIFICATIONS_CHANGED'}]));
});
