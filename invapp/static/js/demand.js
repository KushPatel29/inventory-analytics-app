import {
  barH, barV, fmt, get, kpis, lines, load, options, palette, status, table,
} from './core.js';

let skuIndex = [];

async function renderKpis() {
  const k = await get('/api/overview');
  kpis('kpi-demand', [
    { label: 'Forecast accuracy', value: fmt.pct(k.ForecastAccuracyPct),
      note: 'volume-weighted, out of sample',
      tone: k.ForecastAccuracyPct >= 0.75 ? 'good' : k.ForecastAccuracyPct >= 0.6 ? 'warning' : 'critical' },
    { label: 'Forecast bias', value: fmt.signedPct(k.ForecastBiasPct),
      note: 'median SKU; negative means under-forecast',
      tone: Math.abs(k.ForecastBiasPct) <= 0.05 ? 'good' : Math.abs(k.ForecastBiasPct) <= 0.15 ? 'warning' : 'critical' },
    { label: 'Units shipped', value: fmt.n(k.UnitsShipped),
      note: `over ${fmt.n(k.WeeksOfHistory)} weeks of history` },
    { label: 'Fill rate', value: fmt.pct(k.FillRatePct),
      note: 'demand met from stock on the week it was asked for' },
    { label: 'Critical items', value: fmt.n(k.CriticalSKUs),
      note: 'A-class value on Y or Z variability' },
  ]);
}

async function renderFit() {
  const sku = document.getElementById('scope-sku').value;
  const department = document.getElementById('scope-dept').value;
  const payload = await get('/api/demand/actual_vs_forecast',
    sku ? { SKU: sku } : { Department: department });
  const rows = payload.rows || [];
  const backtest = rows.filter((r) => r.Series === 'Backtest');
  const forward = rows.filter((r) => r.Series === 'Forecast');
  const colors = palette();

  // The forward forecast is joined to the backtest line at the hand-off week,
  // otherwise the two series read as unrelated and the chart looks like the
  // forecast starts from nowhere.
  const bridge = backtest.length ? [backtest[backtest.length - 1]] : [];
  lines('fit-chart', [
    { name: 'Actual', x: backtest.map((r) => r.WeekEnding), y: backtest.map((r) => r.ActualUnits),
      color: colors[0] },
    { name: 'Forecast (backtest)', x: backtest.map((r) => r.WeekEnding),
      y: backtest.map((r) => r.ForecastUnits), color: colors[1] },
    { name: 'Forecast (next 13 weeks)',
      x: [...bridge.map((r) => r.WeekEnding), ...forward.map((r) => r.WeekEnding)],
      y: [...bridge.map((r) => r.ForecastUnits), ...forward.map((r) => r.ForecastUnits)],
      color: colors[1], dash: 'dot' },
  ], { decimals: 0 });

  const scope = sku ? `SKU ${sku}` : (department || 'the whole network');
  document.getElementById('fit-note').innerHTML = payload.accuracy === null
    ? ''
    : `Over the backtest window, ${scope} was forecast to `
      + `<strong>${fmt.pct(payload.accuracy)}</strong> accuracy with a bias of `
      + `<strong>${fmt.signedPct(payload.bias)}</strong>.`;
}

async function renderMethods() {
  const { rows } = await get('/api/demand/method_mix');
  barH('method-chart', rows.map((r) => r.MethodLabel), rows.map((r) => r.SKUCount),
    { leftMargin: 196, suffix: ' SKUs' });
}

async function renderSegmentScorecard() {
  const { rows } = await get('/api/demand/segment_scorecard');
  table('segment-scorecard', [
    { key: 'ABCClass', label: 'Value class', cls: 'strong' },
    { key: 'XYZClass', label: 'Variability' },
    { key: 'SKUCount', label: 'SKUs', num: true, fmt: (v) => fmt.n(v) },
    { key: 'WeeklyUnits', label: 'Forecast units / wk', num: true, fmt: (v) => fmt.n(v) },
    { key: 'VolumeWeightedAccuracy', label: 'Weighted accuracy', num: true,
      fmt: (v) => fmt.pct(v), chip: (v) => (v >= 0.70 ? 'good' : 'critical') },
    { key: 'MedianMASE', label: 'Median MASE', num: true, fmt: (v) => fmt.n(v, 2) },
    { key: 'MedianBias', label: 'Median bias', num: true, fmt: (v) => fmt.signedPct(v) },
    { key: 'AbsoluteBiasP90', label: 'P90 |bias|', num: true, fmt: (v) => fmt.pct(v) },
    { key: 'ReviewStatus', label: 'Control status', chip: (v) => (
      v === 'WITHIN PORTFOLIO GUARDRAIL' ? 'good' : 'warning'
    ) },
  ], rows, {
    footnote: 'Guardrails are portfolio review thresholds, not approved operating policy. '
      + 'A flagged segment requires diagnosis before forecast overrides or inventory changes.',
  });
}

async function renderBias() {
  // Bias per week across the whole backtest, so a calendar effect is visible as
  // a run of bars rather than hidden inside a per-SKU average.
  const payload = await get('/api/demand/actual_vs_forecast');
  const rows = (payload.rows || []).filter((r) => r.Series === 'Backtest' && r.ActualUnits > 0);
  const bias = rows.map((r) => (r.ForecastUnits - r.ActualUnits) / r.ActualUnits);
  const good = status.good();
  const bad = status.critical();
  const warn = status.serious();
  const colors = bias.map((b) => (Math.abs(b) <= 0.05 ? good : Math.abs(b) <= 0.15 ? warn : bad));

  barV('bias-chart', rows.map((r) => r.WeekEnding), bias.map((b) => b * 100), {
    colors, decimals: 1, suffix: '%',
    layout: { yaxis: { ticksuffix: '%', zeroline: true, zerolinecolor: 'rgba(128,128,128,0.5)' },
      xaxis: { tickangle: -40 }, margin: { b: 74 } },
  });

  const worst = rows
    .map((r, i) => ({ week: r.WeekEnding, bias: bias[i] }))
    .sort((a, b) => Math.abs(b.bias) - Math.abs(a.bias))[0];
  const el = document.getElementById('bias-chart');
  if (worst && el && el.parentElement) {
    let note = el.parentElement.querySelector('.bias-note');
    if (!note) {
      note = document.createElement('div');
      note.className = 'note-inline bias-note';
      el.parentElement.appendChild(note);
    }
    note.innerHTML = `Worst week: <strong>${worst.week}</strong> at `
      + `${fmt.signedPct(worst.bias)}. Runs of same-signed bars on adjacent weeks are a `
      + 'promotional or event calendar the models were never given, not a modelling error.';
  }
}

async function renderSeason() {
  const select = document.getElementById('season-dept');
  const { rows } = await get('/api/demand/seasonality');
  const departments = [...new Set(rows.map((r) => r.Department))].filter(Boolean).sort();
  if (!select.dataset.wired) {
    select.dataset.wired = '1';
    select.innerHTML = departments.map((d) => `<option value="${d}">${d}</option>`).join('');
    select.addEventListener('change', renderSeason);
  }
  const chosen = select.value || departments[0];
  if (!select.value && chosen) select.value = chosen;
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const series = rows.filter((r) => r.Department === chosen).sort((a, b) => a.Month - b.Month);
  const seq = palette();
  barV('season-chart', series.map((r) => months[r.Month - 1]),
    series.map((r) => r.SeasonalIndex), {
      colors: series.map((r) => (r.SeasonalIndex >= 1.15 ? seq[1] : seq[0])),
      decimals: 2,
      layout: { yaxis: { title: { text: '' } } },
    });
}

async function renderSkus() {
  const params = {
    Department: document.getElementById('sku-dept').value,
    ABCClass: document.getElementById('sku-abc').value,
    XYZClass: document.getElementById('sku-xyz').value,
    limit: 250,
  };
  const { rows, total, truncated } = await get('/api/demand/skus', params);
  skuIndex = rows;
  table('sku-table', [
    { key: 'SKU', label: 'SKU', cls: 'strong' },
    { key: 'ItemDescription', label: 'Item', cls: 'wrap' },
    { key: 'ABCClass', label: 'ABC' },
    { key: 'XYZClass', label: 'XYZ' },
    { key: 'WeeklyDemand', label: 'Wk demand', num: true, fmt: (v) => fmt.n(v, 1) },
    { key: 'ForecastWeekly', label: 'Wk forecast', num: true, fmt: (v) => fmt.n(v, 1) },
    { key: 'MethodLabel', label: 'Model' },
    { key: 'ForecastAccuracy', label: 'Accuracy', num: true, fmt: (v) => fmt.pct(v),
      chip: (v) => (v >= 0.75 ? 'good' : v >= 0.55 ? 'warning' : 'critical') },
    { key: 'Bias', label: 'Bias', num: true, fmt: (v) => fmt.signedPct(v) },
  ], rows, {
    footnote: truncated ? `Showing the 250 fastest-moving of ${fmt.n(total)} SKUs.` : '',
  });
}

async function fillFilters() {
  const { rows } = await get('/api/demand/skus', { limit: 5000 });
  const departments = [...new Set(rows.map((r) => r.Department))].filter(Boolean).sort();
  options(document.getElementById('sku-dept'), departments);
  options(document.getElementById('sku-abc'), ['A', 'B', 'C']);
  options(document.getElementById('sku-xyz'), ['X', 'Y', 'Z']);
  options(document.getElementById('scope-dept'), departments, { all: 'Whole network' });

  const skuSelect = document.getElementById('scope-sku');
  const top = rows.slice(0, 120);
  skuSelect.innerHTML = '<option value="">—</option>'
    + top.map((r) => `<option value="${r.SKU}">${r.SKU} · ${r.ItemDescription}</option>`).join('');
  document.getElementById('scope-note').textContent =
    `${fmt.n(rows.length)} SKUs; the picker lists the 120 fastest movers.`;
}

export async function render() {
  await fillFilters();
  ['sku-dept', 'sku-abc', 'sku-xyz'].forEach((id) => {
    const el = document.getElementById(id);
    if (el && !el.dataset.wired) {
      el.dataset.wired = '1';
      el.addEventListener('change', renderSkus);
    }
  });
  ['scope-dept', 'scope-sku'].forEach((id) => {
    const el = document.getElementById(id);
    if (el && !el.dataset.wired) {
      el.dataset.wired = '1';
      el.addEventListener('change', renderFit);
    }
  });
  await load([
    renderKpis, renderFit, renderMethods, renderSegmentScorecard,
    renderBias, renderSeason, renderSkus,
  ]);
}
