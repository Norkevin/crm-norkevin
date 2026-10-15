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
  vm.runInNewContext(script, {document:{querySelectorAll:()=>elements,querySelector:()=>null},Date:{now:()=>now},setInterval:fn=>{tick=fn;}});
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

test('automatic countdown promises scheduled delivery without asking for approval', () => {
  const now = 100000000;
  const elements = [5, 0].map(minutes => ({dataset:{workflowDue:String(now+minutes*60000),workflowAuto:'1'},textContent:''}));
  vm.runInNewContext(script, {document:{querySelectorAll:()=>elements,querySelector:()=>null},Date:{now:()=>now},setInterval:()=>{}});
  assert.deepEqual(elements.map(el=>el.textContent), ['Envío automático en 5 minutos.', 'Plazo cumplido. Esperando el envío automático.']);
});

test('due automatic mail refreshes workflow panels without replacing notes or polling after completion', async () => {
  let tick, calls = 0, replacements = 0;
  let waiting = true;
  const oldDetails = {open:true}, newDetails = {open:false};
  const notes = {value:'Notas sin guardar'};
  const replacement = {querySelectorAll:()=>[newDetails]};
  const panel = {dataset:{workflowLive:'steps'}, contains:()=>false,
    querySelectorAll:()=>[oldDetails], replaceWith(value) {
      assert.equal(value, replacement); waiting = false; replacements++;
    }};
  const document = {hidden:false, activeElement:notes,
    querySelector(selector) { return selector === '[data-workflow-live]' ? panel : null; },
    querySelectorAll(selector) {
      if (selector === '[data-workflow-live]') return [panel];
      return waiting ? [{dataset:{workflowDue:'1',workflowAuto:'1'},textContent:''}] : [];
    }};
  vm.runInNewContext(script, {document, Date:{now:()=>1000}, AbortController, setTimeout, clearTimeout,
    window:{location:{pathname:'/leads/synthetic'}}, setInterval:fn=>{tick=fn;},
    fetch:async (path,options)=>{
      calls++; assert.equal(path,'/leads/synthetic'); assert.equal(options.cache,'no-store');
      return {ok:true,redirected:false,text:async()=>'<updated-workflow>'};
    }, DOMParser:class {parseFromString(text) {
      assert.equal(text,'<updated-workflow>'); return {querySelector:()=>replacement};
    }}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(replacements,1);
  assert.equal(newDetails.open,true);
  assert.equal(notes.value,'Notas sin guardar');
  tick();
  assert.equal(calls,1);
});
