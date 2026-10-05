const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('Spanish display keeps the stored day and clock without timezone conversion', () => {
  const context = vm.createContext({});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/date-range.js'), 'utf8'), context);
  assert.equal(context.formatFlowDate('2026-10-05T23:50:00-06:00', true), '5 de octubre de 2026 · 23:50');
  assert.equal(context.formatFlowDate('2024-02-29'), '29 de febrero de 2024');
  assert.equal(context.formatFlowDate('2026-02-29'), '2026-02-29', 'an invalid date must not silently roll into March');
  assert.equal(context.formatFlowDate(''), '');
});

test('shared range follows start, preserves valid manual end and rejects earlier dates', () => {
  const start = { value: '2028-06-03' };
  const end = { value: '', dataset: {} };
  const context = { document: { getElementById: id => id === 'start' ? start : end } };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/date-range.js'), 'utf8'), context);
  const sync = force => context.syncDateRange('start', 'end', force);
  sync();
  assert.equal(end.value, start.value);
  assert.equal(end.min, start.value);
  start.value = '2028-07-03'; sync();
  assert.equal(end.value, start.value);
  end.value = '2028-08-15'; start.value = '2028-07-10'; sync();
  assert.equal(end.value, '2028-08-15');
  start.value = '2028-09-01'; sync();
  assert.equal(end.value, start.value);
  end.value = '2028-10-01'; sync(true);
  assert.equal(end.value, start.value);
  start.value = ''; sync();
  assert.equal(end.min, '');
});
