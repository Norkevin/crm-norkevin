const {test} = require('node:test');
const assert = require('node:assert/strict');
const {webcrypto} = require('node:crypto');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../static/teams.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function setup({action='member', noUUID=false, blockedStorage=false, hash=''} = {}) {
  const requests=[], events={}, feedback={hidden:true, textContent:'', scrollIntoView() {}}, saved=new Map();
  const button={disabled:false, textContent:'Guardar miembro'};
  const form={dataset:{command:action}, elements:{}, data:{name:'Equipo sintético', role:'Foto', rate:'250'},
    addEventListener(name, handler) {events[name]=handler;}, querySelector:selector=>selector==='button'?button:null,
    querySelectorAll:()=>[]};
  const target={tagName:'DETAILS', open:false, parentElement:null, scrollIntoView(){this.scrolled=true;},
    closest:selector=>selector==='.ft'?{}:selector==='[role="tabpanel"]'?{id:'ft-job-team'}:null};
  const tab={click(){target.tabActivated=true;}};
  const context={URLSearchParams, Uint8Array, location:{pathname:'/teams/members',search:'',hash,reload(){context.reloaded=true;}},
    crypto:noUUID?{getRandomValues:array=>webcrypto.getRandomValues(array)}:webcrypto,
    sessionStorage:{getItem(key){if(blockedStorage)throw Error('SecurityError');return saved.get(key)||null;},
      setItem(key,value){if(blockedStorage)throw Error('SecurityError');saved.set(key,value);},
      removeItem(key){if(blockedStorage)throw Error('SecurityError');saved.delete(key);}},
    FormData:class {constructor(source){this.data=source.data;} [Symbol.iterator](){return Object.entries(this.data)[Symbol.iterator]();}},
    document:{querySelector:selector=>selector==='#ft-feedback'?feedback:selector.includes('aria-controls="ft-job-team"')?tab:null,
      querySelectorAll:selector=>selector==='form[data-command]'?[form]:[], getElementById:()=>target},
    addEventListener(name,handler){events[name]=handler;},
    fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))};
  context.window=context;
  context.flowTeamsCSRF='synthetic-csrf';
  vm.runInNewContext(script,context);
  return {context,requests,form,button,feedback,target,events,
    submit(){events.submit({preventDefault(){},submitter:button});},
    respond(status=200,data={}){requests.at(-1).resolve({ok:status<400,status,json:async()=>data});}};
}

test('saving a member sends its command and immediately shows a busy state',async()=>{
  const s=setup();s.submit();
  assert.equal(s.requests.length,1);
  assert.equal(s.requests[0].url,'/api/teams/command');
  assert.equal(s.requests[0].options.headers['X-Teams-CSRF'],'synthetic-csrf');
  assert.equal(JSON.parse(s.requests[0].options.body).action,'member');
  assert.equal(s.button.disabled,true);
  assert.equal(s.button.textContent,'Guardando…');
  s.respond();await flush();assert.equal(s.context.reloaded,true);
});

test('a browser without randomUUID can save with a random retry key',async()=>{
  const s=setup({noUUID:true});s.submit();
  assert.equal(s.requests.length,1);
  assert.match(JSON.parse(s.requests[0].options.body).key,/^[a-f0-9]{32}$/);
  s.respond();await flush();assert.equal(s.context.reloaded,true);
});

for(const failure of ['connection','server']) test(`a ${failure} failure preserves the exact command for a safe retry`,async()=>{
  const s=setup({action:'payment'});s.submit();
  const body=s.requests[0].options.body;
  if(failure==='connection')s.requests[0].reject(Error('connection lost'));else s.respond(500,{error:'Uncertain result'});
  await flush();
  assert.equal(s.feedback.hidden,false);
  assert.match(s.feedback.textContent,/No se confirmó/);
  assert.equal(s.button.disabled,false);
  assert.equal(s.button.textContent,'Guardar miembro');
  s.submit();assert.equal(s.requests[1].options.body,body);
  s.respond();await flush();
});

test('changed content cannot create a different command after a lost response',async()=>{
  const s=setup();s.submit();s.requests[0].reject(Error('connection lost'));await flush();
  s.form.data.name='Otra persona';s.submit();await flush();
  assert.equal(s.requests.length,1);
  assert.match(s.feedback.textContent,/registro pendiente de comprobar/);
});

test('a validation error permits correcting the form and submitting again',async()=>{
  const s=setup();s.submit();s.respond(400,{error:'Revisa el nombre.'});await flush();
  assert.equal(s.form.dataset.commandKey,undefined);
  assert.equal(s.feedback.textContent,'Revisa el nombre.');
  s.form.data.name='Nombre corregido';s.submit();assert.equal(s.requests.length,2);
  s.respond();await flush();
});

test('denied browser storage does not stop later controls or saving',async()=>{
  const s=setup({blockedStorage:true});
  assert.equal(typeof s.events.hashchange,'function');
  s.submit();s.respond(200,{warnings:['Registro guardado.']});await flush();
  assert.equal(s.context.reloaded,true);
});

test('an editing deep link opens the disclosure in its correct tab',()=>{
  const s=setup({hash:'#ft-add-person'});
  assert.equal(s.target.open,true);
  assert.equal(s.target.tabActivated,true);
  assert.equal(s.target.scrolled,true);
  s.target.open=false;s.events.hashchange();assert.equal(s.target.open,true);
});

test('a malformed hash does not stop control initialization',()=>{
  const s=setup({hash:'#%broken'});
  assert.equal(s.target.open,false);
  assert.equal(typeof s.events.submit,'function');
});
