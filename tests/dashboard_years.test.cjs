const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const template = fs.readFileSync('templates/dashboard.html','utf8');
function state(saved) {
  const store = new Map(saved ? [['flow-dashboard-years:test-brand',JSON.stringify(saved)]] : []);
  const ctx = {CURRENT_YEAR:2026,REVENUE_SERIES:[{year:2023,total:10,color:'red'},{year:2024,total:20,color:'green'},{year:2026,total:30,color:'blue'}],
    localStorage:{getItem:key=>store.get(key),setItem:(key,value)=>store.set(key,value)}, updateDashboard:()=>{},money:n=>`Q${n}`};
  vm.createContext(ctx);
  const pref = template.slice(template.indexOf('var excludedRevenueYears'),template.indexOf('function toggleRevenueYear'))
    .replace('{{ current_tenant.id|tojson }}',JSON.stringify('test-brand'));
  vm.runInContext(pref,ctx);
  vm.runInContext(template.slice(template.indexOf('function revenueLegendHtml'),template.indexOf('function updateDashboard')),ctx);
  return {ctx,store};
}
test('Removing an old year removes the complete legend item and total; preference survives a reload',()=>{
  const {ctx,store}=state(); assert.match(ctx.revenueLegendHtml(),/2024/);
  ctx.setRevenueYearIncluded(2024,false);
  assert.doesNotMatch(ctx.revenueLegendHtml(),/2024|Q20/); assert.match(ctx.revenueLegendHtml(),/2026/);
  const restored=state(JSON.parse(store.get('flow-dashboard-years:test-brand')));
  assert.doesNotMatch(restored.ctx.revenueLegendHtml(),/2024|Q20/);
  restored.ctx.setRevenueYearIncluded(2024,true);
  assert.match(restored.ctx.revenueLegendHtml(),/2024/);assert.equal(restored.ctx.hiddenRevenueYears.has('2024'),false);
});
test('Series renderer applies both line visibility and complete year exclusion',()=>{
  assert.match(template,/!hiddenRevenueYears.has\(String\(s.year\)\) && !excludedRevenueYears.has\(String\(s.year\)\)/);
  assert.match(template,/Desmarca un año para quitar su línea, cifra y etiqueta/);
});
