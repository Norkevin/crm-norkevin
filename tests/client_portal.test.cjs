const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const template = fs.readFileSync(path.join(__dirname, '../templates/client_portal.html'), 'utf8');
const script = template.slice(template.indexOf('var PORTAL_TABS'), template.indexOf('let currentContractId'));

function element(initial = []) {
  const classes = new Set(initial);
  return {
    attributes: {}, classes, scrolled: false,
    classList: { toggle(name, force) {
      const enabled = force === undefined ? !classes.has(name) : force;
      if (enabled) classes.add(name); else classes.delete(name);
      return enabled;
    } },
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; },
    scrollIntoView() { this.scrolled = true; },
  };
}
function setup() {
  const nodes = Object.fromEntries(['quotes', 'contracts', 'invoices', 'questionnaires', 'galeria', 'portal-tabs'].map(id => [id, element()]));
  const pills = Object.fromEntries(Object.keys(nodes).map(id => [id, element()]));
  const context = { document: {
    getElementById: id => nodes[id],
    querySelector: selector => pills[selector.match(/data-tab="([^"]+)"/)[1]],
    addEventListener() {},
  }, history: { replaceState() {} } };
  vm.createContext(context);
  vm.runInContext(script, context);
  return { nodes, pills, context };
}

test('next-step navigation reveals one panel and scrolls to documents; tabs keep their position', () => {
  const { nodes, pills, context } = setup();
  context.showPortalTab('contracts', { preventDefault() {}, currentTarget: { closest: () => null } });
  assert.equal(nodes.contracts.classes.has('active'), true);
  assert.equal(nodes.quotes.classes.has('active'), false);
  assert.equal(pills.contracts.attributes['aria-current'], 'page');
  assert.equal(nodes['portal-tabs'].scrolled, true);
  nodes['portal-tabs'].scrolled = false;
  context.showPortalTab('invoices', { preventDefault() {}, currentTarget: { closest: () => nodes['portal-tabs'] } });
  assert.equal(nodes.contracts.classes.has('active'), false);
  assert.equal(nodes.invoices.classes.has('active'), true);
  assert.equal(nodes['portal-tabs'].scrolled, false);
});

test('invoice details and the accessible expanded state open and close together', () => {
  const { nodes, context } = setup();
  nodes['invoice-breakdown-test'] = element(['hidden']);
  const button = element();
  context.toggleInvoiceGroup('test', button);
  assert.equal(nodes['invoice-breakdown-test'].classes.has('hidden'), false);
  assert.equal(button.attributes['aria-expanded'], 'true');
  context.toggleInvoiceGroup('test', button);
  assert.equal(nodes['invoice-breakdown-test'].classes.has('hidden'), true);
  assert.equal(button.attributes['aria-expanded'], 'false');
});
