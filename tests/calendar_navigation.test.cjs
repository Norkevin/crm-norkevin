const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../static/calendar-navigation.js'), 'utf8');
const url = month => `https://example.test/calendar?year=2026&month=${month}`;
const flush = () => new Promise(resolve => setImmediate(resolve));

function calendar(month) {
  const links = ['previous','next','today'].map((key,i) => ({
    href:url(i === 0 ? month-1 : i === 1 ? month+1 : 10), dataset:{calendarControl:key},
    getAttribute() {return this.href;}, closest() {return this;}, hasAttribute:()=>false, removeAttribute() {}
  }));
  const label = {textContent:`Month ${month}`};
  const content = {innerHTML:`dates-${month}`, style:{}, contains:()=>false,
    getBoundingClientRect:()=>({height:500}), animate() {}};
  const status = {textContent:'', classList:{add(){},remove(){}}};
  return {dataset:{calendarRoot:'owner'}, isConnected:true, links, label, content, status,
    parentElement:{scrollTop:240,scrollLeft:0,parentElement:null}, attributes:{},
    setAttribute(name,value) {this.attributes[name]=value;},
    cloneNode() {return calendar(month);}, contains:node=>links.includes(node),
    querySelector(selector) {
      if (selector.includes('data-calendar-content')) return content;
      if (selector.includes('data-calendar-status')) return status;
      if (selector.includes('data-calendar-label')) return label;
      return links.find(link=>selector.includes(`"${link.dataset.calendarControl}"`)) || null;
    },
    querySelectorAll:()=>links
  };
}

const portalURL = view => `https://example.test/teams-portal/?view=${view}&month=2026-11&q=Ana&period=upcoming`;
function portal(view) {
  const root=calendar(11), originalSelector=root.querySelector;
  root.dataset={calendarRoot:'portal',calendarView:view};
  root.toolbar={innerHTML:`toolbar-${view}`,contains:()=>false};
  root.content.innerHTML=`weddings-${view}`;
  root.content.getBoundingClientRect=()=>({height:1400,top:400});
  root.links=['list','calendar'].map(key=>({href:portalURL(key),dataset:{calendarControl:key},attributes:key===view?{'aria-current':'page'}:{},
    closest(){return this;},getAttribute(name){return name==='href'?this.href:this.attributes[name];},
    hasAttribute(name){return name in this.attributes;},setAttribute(name,value){this.attributes[name]=value;},removeAttribute(name){delete this.attributes[name];}}));
  root.contains=node=>root.links.includes(node);
  root.querySelector=selector=>{
    if(selector.includes('data-calendar-toolbar'))return root.toolbar;
    if(selector.includes('data-calendar-label'))return root.dataset.calendarView==='calendar'?root.label:null;
    if(selector.includes('data-calendar-control'))return root.links.find(link=>selector.includes(`"${link.dataset.calendarControl}"`));
    return originalSelector(selector);
  };
  root.querySelectorAll=selector=>selector==='[data-calendar-control]'?root.links:[];
  root.cloneNode=()=>portal(view);
  return root;
}

function setup(portalView=false) {
  const root=portalView?portal('list'):calendar(10), events={}, windowEvents={}, requests=[], pushes=[], assigned=[];
  const location={href:portalView?portalURL('list'):url(10),origin:'https://example.test',pathname:portalView?'/teams-portal/':'/calendar',assign:value=>assigned.push(value)};
  const history={state:{},replaceState(state){this.state=state;},pushState(state,unused,value){this.state=state;location.href=value;pushes.push(value);}};
  const context={URL,URLSearchParams,AbortController,Date,location,history,
    setTimeout:()=>1,clearTimeout(){},matchMedia:()=>({matches:false}),filterCalendar(){},
    document:{querySelector:()=>root,activeElement:root.links[1],documentElement:{clientHeight:900},addEventListener:(key,fn)=>events[key]=fn},
    addEventListener:(key,fn)=>windowEvents[key]=fn,
    fetch:(value,options)=>new Promise(resolve=>requests.push({value,options,resolve})),
    DOMParser:class {parseFromString(value){return {querySelector:()=>portalView?portal(value):calendar(Number(value))};}}
  };
  context.window=context;
  vm.runInNewContext(script,context);
  const click=index=>events.click({target:root.links[index],button:0,preventDefault(){}});
  const respond=(index,month,status=200,redirect='')=>requests[index].resolve({ok:status===200,status,redirected:Boolean(redirect),
    url:redirect||requests[index].value,text:async()=>String(month)});
  return {root,context,requests,pushes,assigned,location,history,windowEvents,click,respond};
}

test('month navigation preserves toolbar identity, focus and scroll; returning uses the memory cache',async()=>{
  const s=setup(), next=s.root.links[1];
  s.click(1);
  assert.equal(s.requests[0].options.headers['X-Calendar-Fragment'],'1');
  assert.equal(s.requests[0].options.cache,'no-store');
  s.respond(0,11); await flush();
  assert.equal(s.root.label.textContent,'Month 11');
  assert.equal(s.root.links[1],next);
  assert.equal(s.context.document.activeElement,next);
  assert.equal(s.root.parentElement.scrollTop,240);
  assert.equal(s.root.content.style.minHeight,'500px');
  s.click(0); await flush();
  assert.equal(s.root.label.textContent,'Month 10');
  assert.equal(s.requests.length,1);
  assert.equal(s.pushes.length,2);
});

test('an older slow response cannot overwrite the last requested month',async()=>{
  const s=setup(); s.click(1); s.click(0);
  s.respond(1,9); await flush(); s.respond(0,11); await flush();
  assert.equal(s.root.content.innerHTML,'dates-9');
  assert.equal(s.root.attributes['aria-busy'],'false');
  assert.deepEqual(s.pushes,[url(9)]);
});

test('a failure keeps the calendar visible and allows a fresh retry',async()=>{
  const s=setup(); s.click(1); s.respond(0,11,500); await flush();
  assert.equal(s.root.content.innerHTML,'dates-10');
  assert.match(s.root.status.textContent,/No se pudo cargar/);
  assert.equal(s.pushes.length,0);
  s.click(1); assert.equal(s.requests.length,2);
  s.respond(1,11); await flush(); assert.equal(s.root.status.textContent,'');
});

test('browser Back updates the month without adding another history entry',async()=>{
  const s=setup(); s.click(1); s.respond(0,11); await flush();
  s.location.href=url(10); s.windowEvents.popstate(); await flush();
  assert.equal(s.root.label.textContent,'Month 10');
  assert.equal(s.pushes.length,1);
});

test('expired authentication follows the login redirect without displaying a fragment',async()=>{
  const s=setup(); s.click(1); s.respond(0,11,200,'https://example.test/login'); await flush();
  assert.equal(s.root.content.innerHTML,'dates-10');
  assert.deepEqual(s.assigned,['https://example.test/login']);
});

test('switching List and Calendar preserves the view buttons, selected state, focus and scroll',async()=>{
  const s=setup(true), buttons=[...s.root.links];
  s.click(1); s.respond(0,'calendar'); await flush();
  assert.equal(s.root.dataset.calendarView,'calendar');
  assert.equal(s.root.content.innerHTML,'weddings-calendar');
  assert.equal(s.root.toolbar.innerHTML,'toolbar-calendar');
  assert.equal(s.root.links[1].getAttribute('aria-current'),'page');
  assert.equal(s.root.links[0].hasAttribute('aria-current'),false);
  assert.equal(s.root.content.style.minHeight,'500px');
  assert.equal(s.root.parentElement.scrollTop,240);
  assert.equal(s.context.document.activeElement,buttons[1]);
  s.click(0); await flush();
  assert.deepEqual(s.root.links,buttons);
  assert.equal(s.root.dataset.calendarView,'list');
  assert.equal(s.root.toolbar.innerHTML,'toolbar-list');
  assert.equal(s.root.links[0].getAttribute('aria-current'),'page');
  assert.equal(s.requests.length,1);
  assert.equal(s.assigned.length,0);
  assert.deepEqual(s.pushes,[portalURL('calendar'),portalURL('list')]);
});

test('Back restores the list view and a delayed view response cannot replace it',async()=>{
  const s=setup(true); s.click(1); s.respond(0,'calendar'); await flush();
  s.location.href=portalURL('list'); s.windowEvents.popstate(); await flush();
  assert.equal(s.root.dataset.calendarView,'list');
  assert.equal(s.pushes.length,1);
  const slow=setup(true); slow.click(1); slow.click(0); await flush();
  slow.respond(0,'calendar'); await flush();
  assert.equal(slow.root.dataset.calendarView,'list');
  assert.equal(slow.root.content.innerHTML,'weddings-list');
});

test('a failed view change retains the list and its selected button',async()=>{
  const s=setup(true); s.click(1); s.respond(0,'calendar',500); await flush();
  assert.equal(s.root.content.innerHTML,'weddings-list');
  assert.equal(s.root.toolbar.innerHTML,'toolbar-list');
  assert.equal(s.root.links[0].getAttribute('aria-current'),'page');
  assert.equal(s.pushes.length,0);
});
