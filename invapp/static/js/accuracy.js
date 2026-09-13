import {
  barH, draw, fmt, get, kpis, load, options, palette, status, table, waterfall,
} from './core.js';

const ACTION_TONE = {
  Expedite: 'critical',
  Reorder: 'warning',
  Transfer: 'neutral',
  Investigate: 'warning',
  Reduce: 'neutral',
  Liquidate: 'critical',
};

async function renderKpis() {
  const [summary, overview] = await Promise.all([
    get('/api/accuracy/summary'),
    get('/api/overview'),
  ]);
  const a = summary.rows[0] || {};
  kpis('kpi-accuracy', [
    { label: 'Record accuracy', value: fmt.pct(a.RecordAccuracy),
      note: `${fmt.n(a.AccurateRecords)} of ${fmt.n(a.RecordsCounted)} counts matched exactly`,
      tone: a.RecordAccuracy >= 0.97 ? 'good' : a.RecordAccuracy >= 0.93 ? 'warning' : 'critical' },
    { label: 'Value accuracy', value: fmt.pct(a.ValueAccuracy, 2),
      note: 'one minus absolute variance over value counted',
      tone: a.ValueAccuracy >= 0.99 ? 'good' : 'warning' },
    { label: 'Net shrinkage', value: fmt.usdShort(-(a.NetVarianceValueUSD || 0)),
      note: `${fmt.pct(a.ShrinkagePct, 2)} of counted value`,
      tone: (a.ShrinkagePct || 0) <= 0.005 ? 'good' : 'warning' },
    { label: 'Absolute variance', value: fmt.usdShort(a.AbsVarianceValueUSD),
      note: `${fmt.n(a.AbsVarianceUnits)} units up or down` },
    { label: 'Open actions', value: fmt.n(overview.OpenActions),
      note: `${fmt.usdShort(overview.StockoutExposureUSD)} of contribution at risk` },
    { label: 'Cash to release', value: fmt.usdShort(overview.CashReleaseOpportunityUSD),
      note: 'excess plus dead stock, at cost', tone: 'warning' },
  ]);
}

async function renderWaterfall() {
  const payload = await get('/api/accuracy/waterfall', { weeks: 13 });
  waterfall('waterfall-chart', payload.rows || []);
  const sub = document.getElementById('waterfall-sub');
  if (sub && payload.since) {
    sub.innerHTML = `The bridge from book value on <strong>${payload.since}</strong> to the `
      + `counted value now, one step per cause. Thirteen weeks, not the whole history: `
      + `run over eighteen months the opening balance is two and a half times what the `
      + `business closes at, which is arithmetically correct and reconciles nothing.`;
  }
}

async function renderReasons() {
  const { rows } = await get('/api/accuracy/reasons');
  barH('reason-chart', rows.map((r) => r.ReasonCode),
    rows.map((r) => r.AbsVarianceValueUSD),
    { prefix: '$', leftMargin: 178, layout: { xaxis: { tickprefix: '$' } } });
}

async function renderNodeAccuracy() {
  const { rows } = await get('/api/accuracy/summary', { dim: 'NodeID' });
  const sorted = [...rows].sort((a, b) => a.RecordAccuracy - b.RecordAccuracy);
  const colors = palette();
  draw('node-accuracy-chart', [
    { type: 'bar', name: 'Record accuracy', x: sorted.map((r) => r.NodeID),
      y: sorted.map((r) => r.RecordAccuracy * 100), marker: { color: colors[0] },
      hovertemplate: '%{x}<br>%{y:.1f}% of records matched<extra></extra>' },
    { type: 'bar', name: 'Value accuracy', x: sorted.map((r) => r.NodeID),
      y: sorted.map((r) => r.ValueAccuracy * 100), marker: { color: colors[1] },
      hovertemplate: '%{x}<br>%{y:.2f}% of value matched<extra></extra>' },
  ], {
    barmode: 'group', bargap: 0.3, bargroupgap: 0.06, showlegend: true,
    yaxis: { ticksuffix: '%', range: [80, 101] },
    margin: { t: 34, b: 40, l: 48, r: 16 },
  });
}

async function renderCoverage() {
  const { rows } = await get('/api/accuracy/coverage');
  table('coverage-table', [
    { key: 'NodeID', label: 'Node', cls: 'strong' },
    { key: 'LocationsStocked', label: 'Locations', num: true, fmt: (v) => fmt.n(v) },
    { key: 'LocationsCounted90d', label: 'Counted in 90d', num: true, fmt: (v) => fmt.n(v) },
    { key: 'CoveragePct', label: 'Coverage', num: true, fmt: (v) => fmt.pct(v, 0),
      chip: (v) => (v >= 0.8 ? 'good' : v >= 0.5 ? 'warning' : 'critical') },
    { key: 'MedianDaysSinceCount', label: 'Median age of count', num: true,
      fmt: (v) => fmt.days(v) },
  ], rows);
}

async function renderActionSummary() {
  const { rows } = await get('/api/actions/summary');
  const tone = { Expedite: 'critical', Liquidate: 'critical', Reorder: 'warning',
    Investigate: 'warning' };
  kpis('action-summary', rows.map((r) => ({
    label: r.Action,
    value: fmt.n(r.Items),
    note: `${fmt.usdShort(r.ImpactUSD)} — ${r.ImpactMeaning}`,
    tone: tone[r.Action],
  })));
}

async function renderActions() {
  const params = {
    Action: document.getElementById('a-action').value,
    NodeID: document.getElementById('a-node').value,
    Department: document.getElementById('a-dept').value,
    limit: 200,
  };
  const { rows, total, truncated } = await get('/api/actions/register', params);
  table('action-table', [
    { key: 'Rank', label: '#', num: true, fmt: (v) => fmt.n(v) },
    { key: 'Action', label: 'Do', chip: (v) => ACTION_TONE[v] || 'neutral' },
    { key: 'SKU', label: 'SKU', cls: 'strong' },
    { key: 'ItemDescription', label: 'Item', cls: 'wrap' },
    { key: 'NodeID', label: 'Node' },
    { key: 'Units', label: 'Units', num: true, fmt: (v) => fmt.n(v) },
    { key: 'ImpactUSD', label: 'Impact', num: true, cls: 'strong', fmt: (v) => fmt.usd(v) },
    { key: 'DueInDays', label: 'Within', num: true,
      fmt: (v) => (v === null ? '—' : fmt.days(v)) },
    { key: 'Rationale', label: 'Why', cls: 'wrap' },
  ], rows, {
    empty: 'Nothing to do under these filters.',
    footnote: truncated
      ? `Showing the top 200 of ${fmt.n(total)} by impact. The CSV carries all of them.`
      : `${fmt.n(total)} actions.`,
  });
}

async function fillFilters() {
  const summary = await get('/api/actions/summary');
  options(document.getElementById('a-action'), summary.rows.map((r) => r.Action),
    { all: 'Every verb' });
  const nodes = await get('/api/network/nodes');
  options(document.getElementById('a-node'), nodes.rows.map((r) => r.NodeID));
  const skus = await get('/api/demand/skus', { limit: 5000 });
  options(document.getElementById('a-dept'),
    [...new Set(skus.rows.map((r) => r.Department))].filter(Boolean).sort());
}

export async function render() {
  await fillFilters();
  ['a-action', 'a-node', 'a-dept'].forEach((id) => {
    const el = document.getElementById(id);
    if (el && !el.dataset.wired) {
      el.dataset.wired = '1';
      el.addEventListener('change', renderActions);
    }
  });
  await load([renderKpis, renderWaterfall, renderReasons, renderNodeAccuracy,
    renderCoverage, renderActionSummary, renderActions]);
}

export { status };
