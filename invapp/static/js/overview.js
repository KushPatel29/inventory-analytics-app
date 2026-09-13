import {
  barH, donut, fmt, get, kpis, lines, load, palette, prepareChart, status, table,
} from './core.js';

/**
 * Colour a KPI by where the number sits, not by whether it is big.
 *
 * `invert` is for metrics where lower is better - days of inventory, stockout
 * counts. `good` and `bad` always name the value at each end regardless, so a
 * caller never has to work out which way round to pass them.
 */
const tone = (value, { good, bad, invert = false } = {}) => {
  if (value === null || value === undefined || Number.isNaN(value)) return undefined;
  if (invert) {
    if (value <= good) return 'good';
    if (value >= bad) return 'critical';
  } else {
    if (value >= good) return 'good';
    if (value <= bad) return 'critical';
  }
  return 'warning';
};

async function renderKpis() {
  const k = await get('/api/overview');

  kpis('kpi-primary', [
    { label: 'Inventory at cost', value: fmt.usdShort(k.InventoryValueUSD),
      note: `${fmt.n(k.SKUCount)} SKUs across ${fmt.n(k.StockedLocations)} stocked locations` },
    { label: 'Inventory turns', value: fmt.n(k.InventoryTurns, 2),
      note: `on ${fmt.usdShort(k.AnnualCOGSUSD)} annualised COGS`,
      tone: tone(k.InventoryTurns, { good: 6, bad: 3 }) },
    { label: 'Days inventory outstanding', value: fmt.n(k.DaysInventoryOutstanding, 0),
      note: `median location holds ${fmt.n(k.MedianDaysOfCover, 0)} days`,
      tone: tone(k.DaysInventoryOutstanding, { good: 60, bad: 110, invert: true }) },
    { label: 'Fill rate', value: fmt.pct(k.FillRatePct),
      note: 'units shipped over units requested',
      tone: tone(k.FillRatePct, { good: 0.98, bad: 0.95 }) },
    { label: 'Stocked out now', value: fmt.n(k.StockoutCount),
      note: `${fmt.n(k.BelowReorderCount)} lines at or below the reorder point`,
      tone: tone(k.StockoutCount, { good: 10, bad: 40, invert: true }) },
  ]);

  kpis('kpi-secondary', [
    { label: 'Carrying cost', value: fmt.usdShort(k.CarryingCostUSD),
      note: `${fmt.pct(k.CarryingCostRate, 0)} of value, per year` },
    { label: 'Excess and dead', value: fmt.usdShort(k.CashReleaseOpportunityUSD),
      note: `${fmt.usdShort(k.ExcessCarryingCostUSD)} a year to keep holding it`,
      tone: tone(k.CashReleaseOpportunityUSD / (k.InventoryValueUSD || 1),
        { good: 0.10, bad: 0.25, invert: true }) },
    { label: 'GMROI', value: fmt.n(k.GMROI, 2),
      note: 'margin dollars per dollar of stock',
      tone: tone(k.GMROI, { good: 2.5, bad: 1.5 }) },
    { label: 'Forecast accuracy', value: fmt.pct(k.ForecastAccuracyPct),
      note: `bias ${fmt.signedPct(k.ForecastBiasPct)} on the median SKU`,
      tone: tone(k.ForecastAccuracyPct, { good: 0.75, bad: 0.6 }) },
    { label: 'Record accuracy', value: fmt.pct(k.RecordAccuracyPct),
      note: `${fmt.n(k.CountsTaken)} counts, ${fmt.usdShort(k.NetShrinkageUSD)} net shrink`,
      tone: tone(k.RecordAccuracyPct, { good: 0.97, bad: 0.93 }) },
    { label: 'Open actions', value: fmt.n(k.OpenActions),
      note: `${fmt.usdShort(k.StockoutExposureUSD)} of contribution at risk` },
  ]);
}

async function renderTrend() {
  const { rows } = await get('/api/overview/trend');
  lines('trend-chart', [
    { name: 'Inventory value', x: rows.map((r) => r.WeekEnding),
      y: rows.map((r) => r.InventoryValueUSD),
      fill: 'tozeroy', fillcolor: 'rgba(42,120,214,0.10)' },
  ], { prefix: '$', decimals: 0, layout: { yaxis: { tickprefix: '$' } } });

  // Days of inventory gets its own panel rather than a second y-axis. Two
  // scales on one frame can be slid against each other until any story appears,
  // and dollars and days have no honest common scale.
  const el = document.getElementById('trend-chart');
  if (!el || !el.parentElement) return;
  let second = document.getElementById('dio-chart');
  if (!second) {
    second = document.createElement('div');
    second.id = 'dio-chart';
    second.className = 'chart short';
    el.parentElement.appendChild(second);
  }
  lines('dio-chart', [
    { name: 'Days inventory outstanding', x: rows.map((r) => r.WeekEnding),
      y: rows.map((r) => r.DaysInventoryOutstanding), color: palette()[1] },
  ], { suffix: ' d', layout: { yaxis: { ticksuffix: ' d' }, margin: { t: 8, b: 34 } } });
}

async function renderCarrying() {
  const { rows } = await get('/api/overview/carrying');
  donut('carrying-chart', rows.map((r) => r.Component), rows.map((r) => r.AnnualCostUSD),
    { prefix: '$' });
}

async function renderValueBy(dim) {
  const { rows } = await get('/api/overview/value_by', { dim });
  const top = rows.slice(0, 12);
  barH('value-chart', top.map((r) => r[dim]), top.map((r) => r.InventoryValueUSD),
    { prefix: '$', leftMargin: 168, layout: { xaxis: { tickprefix: '$' } } });
}

async function renderAbc() {
  const { rows } = await get('/api/overview/abc');
  const skus = rows.reduce((a, r) => a + r.SKUCount, 0) || 1;
  const value = rows.reduce((a, r) => a + r.AnnualConsumptionValueUSD, 0) || 1;
  const colors = palette();
  // Two shares against one axis, not two axes: both are percentages of their
  // own whole, so they belong on the same scale and the comparison is the point.
  prepareChart('abc-chart', 'ABC concentration: share of SKUs compared with share of annual consumption value');
  window.Plotly.newPlot('abc-chart', [
    { type: 'bar', name: 'Share of SKUs', x: rows.map((r) => r.ABCClass),
      y: rows.map((r) => (r.SKUCount / skus) * 100), marker: { color: colors[0] },
      hovertemplate: 'Class %{x}<br>%{y:.1f}% of SKUs<extra></extra>' },
    { type: 'bar', name: 'Share of consumption value', x: rows.map((r) => r.ABCClass),
      y: rows.map((r) => (r.AnnualConsumptionValueUSD / value) * 100), marker: { color: colors[1] },
      hovertemplate: 'Class %{x}<br>%{y:.1f}% of value<extra></extra>' },
  ], {
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
    barmode: 'group', bargap: 0.36, bargroupgap: 0.08,
    margin: { l: 48, r: 16, t: 30, b: 36 },
    font: { family: 'system-ui, sans-serif', size: 12,
      color: getComputedStyle(document.documentElement).getPropertyValue('--ink-muted') },
    yaxis: { ticksuffix: '%',
      gridcolor: getComputedStyle(document.documentElement).getPropertyValue('--grid') },
    xaxis: { title: { text: '' } },
    showlegend: true,
    legend: { orientation: 'h', y: 1.2, x: 0 },
  }, { displayModeBar: false, responsive: true });
}

async function renderRuns() {
  const { rows } = await get('/api/runs');
  table('runs-table', [
    { key: 'as_of', label: 'Snapshot' },
    { key: 'created_at', label: 'Processed', fmt: (v) => (v ? v.slice(0, 16).replace('T', ' ') : '—') },
    { key: 'InventoryValueUSD', label: 'Value', num: true, fmt: (v) => fmt.usdShort(v) },
    { key: 'InventoryTurns', label: 'Turns', num: true, fmt: (v) => fmt.n(v, 2) },
    { key: 'FillRatePct', label: 'Fill rate', num: true, fmt: (v) => fmt.pct(v) },
    { key: 'StockoutCount', label: 'Stockouts', num: true, fmt: (v) => fmt.n(v) },
    { key: 'ForecastAccuracyPct', label: 'Forecast acc.', num: true, fmt: (v) => fmt.pct(v) },
    { key: 'OpenActions', label: 'Actions', num: true, fmt: (v) => fmt.n(v) },
  ], rows.slice(0, 10), {
    empty: 'No runs recorded yet. Each workbook you process adds a row, so a weekly '
      + 'planning cycle builds its own trend.',
    footnote: 'Run history is stored on local disk. The hosted demo has an ephemeral '
      + 'filesystem, so history there lasts as long as the process.',
  });
}

export function wireUpload() {
  const form = document.getElementById('upload-form');
  if (!form) return;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const result = document.getElementById('upload-result');
    result.textContent = 'Processing…';
    try {
      const response = await fetch('/api/workbook/process', {
        method: 'POST', body: new FormData(form),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      result.innerHTML = `<span class="chip good">Loaded</span> `
        + `${fmt.n(payload.SKUCount)} SKUs, ${fmt.usdShort(payload.InventoryValueUSD)} at cost, `
        + `${fmt.n(payload.OpenActions)} actions &mdash; computed in ${payload.elapsed_ms} ms. `
        + 'Reloading…';
      setTimeout(() => window.location.reload(), 1200);
    } catch (error) {
      result.innerHTML = `<span class="chip critical">${error.message}</span>`;
    }
  });
}

export async function render() {
  const dim = document.getElementById('value-dim');
  if (dim && !dim.dataset.wired) {
    dim.dataset.wired = '1';
    dim.addEventListener('change', () => renderValueBy(dim.value));
  }
  await load([
    renderKpis,
    renderTrend,
    renderCarrying,
    () => renderValueBy(dim ? dim.value : 'Department'),
    renderAbc,
    renderRuns,
  ]);
  // Every tile is a status word away from being meaningless; if the whole
  // payload failed the page should say so rather than showing six dashes.
  const primary = document.getElementById('kpi-primary');
  if (primary && primary.querySelector('.loading')) {
    primary.innerHTML = '<div class="banner error">Nothing has been processed yet. '
      + 'Upload a workbook below.</div>';
  }
}

export { status };
