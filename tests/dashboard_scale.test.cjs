const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const template = fs.readFileSync('templates/dashboard.html', 'utf8');
const ctx = vm.createContext({});
vm.runInContext(template.slice(template.indexOf('function niceCeil('), template.indexOf('function compactAxis')), ctx);

test('A peak near Q50k gets a Q60k ceiling with readable Q15k intervals', () => {
  for (const peak of [50000, 50001, 52000]) assert.equal(ctx.niceCeil(peak), 60000);
  for (const peak of [0.25, 150, 10743, 99999, 1000000]) {
    const ceiling = ctx.niceCeil(peak);
    assert.ok(ceiling >= peak * 1.08 && ceiling <= peak * 1.65);
  }
  assert.equal(ctx.niceCeil(0), 4);
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
        {year: 2025, values: Array(12).fill(200000), color: 'gray'},
      ],
      hiddenRevenueYears: new Set(['2025']), excludedRevenueYears: new Set(),
      MONTH_LABELS: Array(12).fill('Mes'), money: String, compactAxis: f => f,
      drawGridAndAxis: (...args) => { ctx.axisMax = args[7]; return ''; },
      smoothPath: () => 'M 0,0', wireChartTooltips() {}, revenueLegendHtml: () => '',
    });
    vm.runInContext(template.slice(template.indexOf('function drawRevenueComparisonChart('), template.indexOf('function revenueLegendHtml(')), ctx);
    ctx.drawRevenueComparisonChart();
    assert.equal(ctx.axisMax, 60000);
    ctx.hiddenRevenueYears.clear();
    ctx.drawRevenueComparisonChart();
    assert.equal(ctx.axisMax, 240000);
    ctx.excludedRevenueYears.add('2025');
    ctx.drawRevenueComparisonChart();
    assert.equal(ctx.axisMax, 60000);
  }
});
