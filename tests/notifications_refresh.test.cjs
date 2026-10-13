const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('templates/base.html', 'utf8');
const source = html.slice(html.indexOf('var MAIL_ICON_SVG'), html.indexOf('function toggleAccountMenu'))
  .replace('{{ recent_notifications | tojson }}', '[]')
  .replace('{{ unread_notifications_count | default(0) }}', '0');
function page(history = false, push = true) {
  const events = {}, windows = {}, workers = {}, calls = [], timers = [];
  const context = {console, AbortController, encodeURIComponent,
    document: {hidden: false, addEventListener: (key, fn) => events[key] = fn,
      getElementById: id => history && id === 'notif-history-list' ? {innerHTML: '', querySelectorAll: () => []} : null,
      querySelectorAll: () => []},
    navigator: {serviceWorker: {addEventListener: (key, fn) => workers[key] = fn}},
    setTimeout: () => 1, clearTimeout() {}, setInterval: fn => timers.push(fn),
    addEventListener: (key, fn) => windows[key] = fn,
    fetch(url, options) { return new Promise(resolve => calls.push({url, options, resolve})); },
    flashToast() {}, formatFlowDate: value => value};
  if (!push) context.navigator = {};
  context.window = context;
  vm.createContext(context); vm.runInContext(source, context);
  const resolve = (index, data = {ok:true, notifications:[], count:0}) => calls[index].resolve({ok:true, json:async()=>data});
  return {context, events, windows, workers, calls, timers, resolve};
}
test('server snapshot avoids a duplicate load; returning/online/push refresh and coalesce', async()=>{
  const p = page(); p.events.DOMContentLoaded(); assert.equal(p.calls.length, 0);
  p.windows.focus(); p.events.visibilitychange();
  assert.equal(p.calls.length, 1); assert.match(p.calls[0].url, /limit=5/);
  assert.equal(p.calls[0].options.cache, 'no-store');
  const pending = p.context.notificationRequest; p.resolve(0); await pending;
  p.context.document.hidden = true; p.timers[0](); assert.equal(p.calls.length, 1);
  p.context.document.hidden = false; p.windows.online();
  const online = p.context.notificationRequest; p.resolve(1); await online;
  p.windows.pageshow({persisted:true});
  const restored = p.context.notificationRequest; p.resolve(2); await restored;
  p.workers.message({data:{type:'FLOW_NOTIFICATIONS_CHANGED'}});
  const pushed = p.context.notificationRequest; p.resolve(3); await pushed;
  assert.equal(p.calls.length, 4);
});
test('history has no five-item limit and late responses cannot undo a read', async()=>{
  const p = page(true);
  const request = p.context.refreshNotifications(); assert.doesNotMatch(p.calls[0].url, /limit=/);
  p.context.notificationVersion++;
  p.resolve(0,{ok:true,notifications:[{id:'old',read:false}],count:1}); await request;
  assert.equal(p.context.notificationUnreadCount,0);
});
test('failed request clears the flight for retry', async()=>{
  const p = page(); const request = p.context.refreshNotifications();
  p.calls[0].resolve({ok:false,json:async()=>({ok:false})}); await request;
  assert.equal(p.context.notificationRequest,null);
  const retried = p.context.refreshNotifications(); p.resolve(1); await retried;
  assert.equal(p.calls.length,2);
});

test('push arriving during a request schedules a fresh snapshot after it', async()=>{
  const p = page(); const current = p.context.refreshNotifications();
  p.workers.message({data:{type:'FLOW_NOTIFICATIONS_CHANGED'}});
  p.workers.message({data:{type:'FLOW_NOTIFICATIONS_CHANGED'}});
  assert.equal(p.calls.length,1);
  p.resolve(0); await current; await Promise.resolve();
  assert.equal(p.calls.length,2);
  const refreshed = p.context.notificationRequest; p.resolve(1); await refreshed;
});

test('without service workers the visible page still updates by polling and focus', async()=>{
  const p = page(false, false); p.events.DOMContentLoaded();
  p.timers[0](); const first = p.context.notificationRequest; p.resolve(0); await first;
  p.windows.focus(); const next = p.context.notificationRequest; p.resolve(1); await next;
  assert.equal(p.calls.length,2);
});
