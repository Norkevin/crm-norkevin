const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
test('mobile disclosures collapse initially, preserve user choice until breakpoint changes, and reopen on desktop', () => {
  let onChange;
  const media = { matches: true, addEventListener(event, fn) { assert.equal(event, 'change'); onChange = fn; } };
  const details = [{ open: true }, { open: true }];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/mobile.js'), 'utf8'), {
    window: { matchMedia: () => media }, document: { querySelectorAll: selector => selector === '[data-mobile-fold]' ? details : [] }
  });
  assert.ok(details.every(el => !el.open));
  details[0].open = true;
  assert.equal(details[0].open, true);
  media.matches = false; onChange();
  assert.ok(details.every(el => el.open));
  media.matches = true; onChange();
  assert.ok(details.every(el => !el.open));
});


test('dialogs escape the page scroll container without recreating their contents', () => {
  const button = { onclick() {} };
  const modal = { parentNode: { className: 'main-content' }, children: [button] };
  const body = { appendChild(el) { el.parentNode = this; } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/mobile.js'), 'utf8'), {
    window: { matchMedia: () => ({ matches: true, addEventListener() {} }) },
    document: { body, querySelectorAll: selector => selector === '.main-content .modal-overlay' ? [modal] : [] }
  });
  assert.equal(modal.parentNode, body);
  assert.equal(modal.children[0], button);
  assert.equal(typeof modal.children[0].onclick, 'function');
});
