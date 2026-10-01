const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const script = fs.readFileSync('static/workflow-timing.js', 'utf8');
test('countdown displays hours, minutes, days and overdue state without claiming delivery', () => {
  let now = 100000000;
  const offsets = [180, 179, 1, 1440, 1500, 0];
  const elements = offsets.map(minutes => ({dataset:{workflowDue:String(now+minutes*60000)},textContent:''}));
  let tick;
  vm.runInNewContext(script, {document:{querySelectorAll:()=>elements},Date:{now:()=>now},setInterval:fn=>{tick=fn;}});
  assert.deepEqual(elements.map(el=>el.textContent),[
    'Preparación automática en 3 horas.',
    'Preparación automática en 2 horas y 59 minutos.',
    'Preparación automática en 1 minuto.',
    'Preparación automática en 1 día.',
    'Preparación automática en 1 día y 1 hora.',
    'Plazo cumplido. Pendiente de preparación automática.'
  ]);
  now += 60000;
  tick();
  assert.equal(elements[0].textContent, 'Preparación automática en 2 horas y 59 minutos.');
  assert.equal(elements[2].textContent, 'Plazo cumplido. Pendiente de preparación automática.');
});
