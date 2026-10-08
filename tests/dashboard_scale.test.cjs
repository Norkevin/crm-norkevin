const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const template = fs.readFileSync('templates/dashboard.html', 'utf8');
const ctx = vm.createContext({});
vm.runInContext(template.slice(template.indexOf('function niceCeil('), template.indexOf('function compactAxis')), ctx);

test('Money ceilings have Q10k headroom and round Q10k boundaries', () => {
  for (const peak of [0, 0.25, 150, 10743, 20000, 50000, 50001, 52000, 99999, 1000000]) {
    assert.equal(ctx.niceCeil(peak), (Math.ceil(peak / 10000) + 1) * 10000);
  }
});

test('Lead and event counts have distinct integer ticks, including small counts', () => {
  for (const peak of [0, 1, 2, 3, 7, 51]) {
    const ceiling = ctx.niceCeil(peak, true);
    assert.ok(Number.isInteger(ceiling / 4));
    assert.ok(ceiling >= Math.max(4, peak));
  }
});

test('Comparison rescales to visible years on desktop and mobile', () => {
  for (const width of [430, 1200]) {
    const svg = {setAttribute() {}, innerHTML: ''};
    const legend = {innerHTML: ''};
    Object.assign(ctx, {
      document: {getElementById: id => id === 'dashboard-chart' ? svg : legend},
      chartPixelWidth: () => width, chartPixelHeight: () => 300,
      REVENUE_SERIES: [
        {year: 2026, values: Array(12).fill(52000), color: 'blue'},
        {year: 2027, values: Array(12).fill(20000), color: 'green'},
        {year: 2025, values: Array(12).fill(200000), color: 'gray'},
      ],
      hiddenRevenueYears: new Set(['2025', '2027']), excludedRevenueYears: new Set(),
      MONTH_LABELS: Array(12).fill('Mes'), money: String, compactAxis: f => f,
      drawGridAndAxis: (...args) => { ctx.axisMax = args[7]; return ''; },
      smoothPath: () => 'M 0,0', wireChartTooltips() {}, revenueLegendHtml: () => '',
    });
    vm.runInContext(template.slice(template.indexOf('function drawRevenueComparisonChart('), template.indexOf('function revenueLegendHtml(')), ctx);
    ctx.drawRevenueComparisonChart();
    assert.equal(ctx.axisMax, 70000);
    ctx.hiddenRevenueYears.clear();
    ctx.drawRevenueComparisonChart();
    assert.equal(ctx.axisMax, 210000);
    ctx.excludedRevenueYears.add('2025');
    ctx.drawRevenueComparisonChart();
    assert.equal(ctx.axisMax, 70000);
    ctx.hiddenRevenueYears.add('2026');
    ctx.drawRevenueComparisonChart();
    assert.equal(ctx.axisMax, 30000);
  }
});


test('Financial axis labels use round tens of thousands rather than quarters', () => {
  vm.runInContext(template.slice(template.indexOf('function drawGridAndAxis('), template.indexOf('function wireChartTooltips(')), ctx);
  for (const max of [30000, 70000, 210000]) {
    const ticks = [];
    ctx.drawGridAndAxis(1200, 300, 88, 24, 42, 1024, 234, max, n => { ticks.push(n); return n; }, true);
    assert.equal(ticks[0], max);
    assert.equal(ticks.at(-1), 0);
    assert.ok(ticks.every(n => n % 10000 === 0));
    assert.ok(ticks.length <= 9);
    if (max === 70000) assert.deepEqual(ticks, [70000, 60000, 50000, 40000, 30000, 20000, 10000, 0]);
  }
});
