import {
  barH, draw, fmt, get, kpis, lines, load, options, palette, status, table,
} from './core.js';

const URGENCY_TONE = {
  'Stocked out': 'critical',
  'Below safety stock': 'critical',
  'At reorder point': 'warning',
  Healthy: 'good',
};

function filters() {
  return {
    NodeID: document.getElementById('f-node').value,
    ABCClass: document.getElementById('f-abc').value,
    Department: document.getElementById('f-dept').value,
    Urgency: document.getElementById('f-urgency').value,
    due: document.getElementById('f-due').checked ? '1' : '',
  };
}

async function renderKpis() {
  const s = await get('/api/replenishment/summary');
  kpis('kpi-repl', [
    { label: 'Lines to order', value: fmt.n(s.LinesDue),
      note: `of ${fmt.n(s.LinesPlanned)} stocked locations` },
    { label: 'Order value', value: fmt.usdShort(s.OrderValueUSD),
      note: `${fmt.n(s.OrderUnits)} units at cost` },
    { label: 'Safety stock held', value: fmt.usdShort(s.SafetyStockValueUSD),
      note: `${fmt.n(s.SafetyStockUnits)} units of buffer` },
    { label: 'Contribution at risk', value: fmt.usdShort(s.StockoutExposureUSD),
      note: `${fmt.n(s.StockedOut)} stocked out, ${fmt.n(s.BelowSafety)} below safety stock`,
      tone: s.StockedOut > 0 ? 'critical' : 'good' },
    { label: 'Mean delivered lead time', value: fmt.days(s.MeanLeadTimeDays),
      note: 'from receipts, not from contracts' },
    { label: 'Cost of planning on contract',
      value: fmt.usdShort(Math.abs(s.ContractGapValueUSD)),
      note: s.ContractGapValueUSD >= 0
        ? 'stock the contracted lead times would have missed'
        : 'stock the contracted lead times would have over-ordered',
      tone: 'warning' },
  ]);
}

async function renderUrgency() {
  const { rows } = await get('/api/replenishment/urgency');
  const order = ['Stocked out', 'Below safety stock', 'At reorder point', 'Healthy'];
  const sorted = order
    .map((name) => rows.find((r) => r.Urgency === name))
    .filter(Boolean);
  const colors = sorted.map((r) => ({
    'Stocked out': status.critical(),
    'Below safety stock': status.serious(),
    'At reorder point': status.warning(),
    Healthy: status.good(),
  }[r.Urgency]));
  barH('urgency-chart', sorted.map((r) => r.Urgency), sorted.map((r) => r.Lines),
    { colors, leftMargin: 138, suffix: ' lines' });
}

async function renderService() {
  const { rows } = await get('/api/replenishment/service_curve');
  lines('service-chart', [{
    name: 'Safety stock value',
    x: rows.map((r) => `${(r.ServiceLevel * 100).toFixed(0)}%`),
    y: rows.map((r) => r.SafetyStockValueUSD),
    mode: 'lines+markers',
  }], { prefix: '$', layout: { yaxis: { tickprefix: '$' }, margin: { l: 62, b: 34, t: 10 } } });
}

async function renderServiceFrontier() {
  const { rows } = await get('/api/replenishment/service_cost_frontier');
  const colors = palette();
  draw('frontier-chart', [
    {
      type: 'scatter', mode: 'lines+markers', name: 'Annual buffer cost',
      x: rows.map((r) => `${(r.ServiceLevel * 100).toFixed(0)}%`),
      y: rows.map((r) => r.AnnualBufferCostUSD),
      marker: { color: colors[0], size: 8 }, line: { color: colors[0], width: 3 },
      hovertemplate: '%{x}<br>Buffer cost %{y:$,.0f}<extra></extra>',
    },
    {
      type: 'scatter', mode: 'lines+markers', name: 'Shortage exposure',
      x: rows.map((r) => `${(r.ServiceLevel * 100).toFixed(0)}%`),
      y: rows.map((r) => r.AnnualShortageExposureUSD),
      marker: { color: colors[1], size: 8 }, line: { color: colors[1], width: 3 },
      hovertemplate: '%{x}<br>Exposure %{y:$,.0f}<extra></extra>',
    },
    {
      type: 'scatter', mode: 'lines+markers', name: 'Total modeled cost',
      x: rows.map((r) => `${(r.ServiceLevel * 100).toFixed(0)}%`),
      y: rows.map((r) => r.ModeledAnnualDecisionCostUSD),
      marker: {
        color: rows.map((r) => (r.EconomicScreen.startsWith('LOWEST')
          ? status.good() : colors[3])),
        size: rows.map((r) => (r.EconomicScreen.startsWith('LOWEST') ? 13 : 8)),
      },
      line: { color: colors[3], width: 3, dash: 'dot' },
      hovertemplate: '%{x}<br>Total %{y:$,.0f}<extra></extra>',
    },
  ], {
    showlegend: true,
    margin: { l: 72, r: 18, t: 38, b: 42 },
    yaxis: { tickprefix: '$', rangemode: 'tozero' },
    legend: { orientation: 'h', y: 1.13 },
  });
}

async function renderPolicyScenario(params = {}) {
  const payload = await get('/api/replenishment/policy_scenario', { ...params, limit: 25 });
  const s = payload.summary;
  kpis('scenario-kpis', [
    { label: 'Scenario order value', value: fmt.usdShort(s.OrderValueUSD),
      note: `${fmt.n(s.LinesDue)} of ${fmt.n(s.Lines)} locations due` },
    { label: 'Scenario safety stock', value: fmt.usdShort(s.SafetyStockValueUSD),
      note: `${fmt.n(s.SafetyStockUnits)} buffer units` },
    { label: 'Lead-window exposure', value: fmt.usdShort(s.LeadWindowExposureUSD),
      note: `${fmt.n(s.LeadWindowExpectedUnitsShort)} expected units short`,
      tone: s.LeadWindowExposureUSD > 0 ? 'warning' : 'good' },
    { label: 'Decision status', value: 'Review required',
      note: 'simulation never submits an order', tone: 'warning' },
  ]);
  table('scenario-table', [
    { key: 'SKU', label: 'SKU', cls: 'strong' },
    { key: 'NodeID', label: 'Node' },
    { key: 'ScenarioStatus', label: 'Scenario status', chip: (v) => (
      v === 'Stocked out' ? 'critical'
        : v === 'Covered under scenario' ? 'good' : 'warning'
    ) },
    { key: 'InventoryPosition', label: 'Position', num: true, fmt: (v) => fmt.n(v) },
    { key: 'ScenarioSafetyStockUnits', label: 'Safety', num: true, fmt: (v) => fmt.n(v) },
    { key: 'ScenarioOrderUnits', label: 'Order', num: true, cls: 'strong', fmt: (v) => fmt.n(v) },
    { key: 'LeadWindowExposureUSD', label: 'Exposure', num: true, fmt: (v) => fmt.usd(v) },
  ], payload.rows, {
    footnote: `Showing ${fmt.n(payload.rows.length)} of ${fmt.n(payload.total)} locations. `
      + 'Exposure is an analytical screen, not a realised loss.',
  });
  return payload;
}

function wireScenario() {
  const form = document.getElementById('scenario-form');
  if (!form || form.dataset.wired) return;
  form.dataset.wired = '1';
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const result = document.getElementById('scenario-result');
    result.textContent = 'Running scenario…';
    const params = {};
    new FormData(form).forEach((value, key) => { if (value !== '') params[key] = value; });
    try {
      const payload = await renderPolicyScenario(params);
      result.innerHTML = `<span class="chip good">Scenario complete · ${payload.summary.LinesDue} lines due</span>`;
    } catch (error) {
      result.innerHTML = `<span class="chip critical">${error.message}</span>`;
    }
  });
}

async function renderPlan() {
  const { rows, total, truncated } = await get('/api/replenishment/plan',
    { ...filters(), limit: 250 });
  const query = new URLSearchParams(
    Object.entries(filters()).filter(([, v]) => v)
  ).toString();
  document.getElementById('dl-csv').href = `/api/download/replenishment.csv?${query}`;
  document.getElementById('dl-xlsx').href = `/api/download/replenishment.xlsx?${query}`;

  table('plan-table', [
    { key: 'SKU', label: 'SKU', cls: 'strong' },
    { key: 'ItemDescription', label: 'Item', cls: 'wrap' },
    { key: 'NodeID', label: 'Node' },
    { key: 'ABCClass', label: 'ABC' },
    { key: 'InventoryPosition', label: 'Position', num: true, fmt: (v) => fmt.n(v) },
    { key: 'ReorderPointUnits', label: 'Reorder pt', num: true, fmt: (v) => fmt.n(v) },
    { key: 'SafetyStockUnits', label: 'Safety', num: true, fmt: (v) => fmt.n(v) },
    { key: 'LeadTimeDaysActual', label: 'Lead', num: true, fmt: (v) => fmt.days(v) },
    { key: 'WeeksOfCover', label: 'Cover', num: true,
      fmt: (v) => (v === null ? '∞' : `${fmt.n(v, 1)} wk`) },
    { key: 'RecommendedOrderUnits', label: 'Order', num: true, cls: 'strong', fmt: (v) => fmt.n(v) },
    { key: 'RecommendedOrderValue', label: 'Value', num: true, fmt: (v) => fmt.usd(v) },
    { key: 'StockoutRisk', label: 'Risk', num: true, fmt: (v) => fmt.pct(v, 0) },
    { key: 'Urgency', label: 'Status', chip: (v) => URGENCY_TONE[v] || 'neutral' },
  ], rows, {
    empty: 'Nothing is at its reorder point under these filters.',
    footnote: truncated
      ? `Showing the 250 largest of ${fmt.n(total)} lines by contribution at risk. `
        + 'The CSV and Excel exports carry all of them.'
      : `${fmt.n(total)} lines.`,
  });
}

async function renderEoq() {
  const { rows } = await get('/api/replenishment/eoq', { limit: 40 });
  const top = rows.filter((r) => r.AnnualDemand > 0).slice(0, 14);
  const colors = palette();
  // Two quantities of the same unit on one axis: this is the comparison, and
  // splitting it across two scales would make any pair of bars look equal.
  draw('eoq-chart', [
    { type: 'bar', name: 'Economic quantity', orientation: 'h',
      x: top.map((r) => r.EOQUnits), y: top.map((r) => r.SKU),
      marker: { color: colors[0] },
      hovertemplate: '%{y}<br>EOQ %{x:,.0f} units<extra></extra>' },
    { type: 'bar', name: 'Being ordered', orientation: 'h',
      x: top.map((r) => r.CurrentOrderUnits), y: top.map((r) => r.SKU),
      marker: { color: colors[1] },
      hovertemplate: '%{y}<br>Ordering %{x:,.0f} units<extra></extra>' },
  ], {
    barmode: 'group', bargap: 0.3, bargroupgap: 0.06,
    showlegend: true,
    margin: { l: 92, r: 18, t: 34, b: 38 },
    yaxis: { autorange: 'reversed' },
  });

  table('eoq-table', [
    { key: 'SKU', label: 'SKU', cls: 'strong' },
    { key: 'ItemDescription', label: 'Item', cls: 'wrap' },
    { key: 'EOQUnits', label: 'EOQ', num: true, fmt: (v) => fmt.n(v) },
    { key: 'CurrentOrderUnits', label: 'Ordering', num: true, fmt: (v) => fmt.n(v) },
    { key: 'OrderSizeGapPct', label: 'Gap', num: true, fmt: (v) => fmt.signedPct(v, 0) },
    { key: 'AnnualSavingUSD', label: 'Saving / yr', num: true, cls: 'strong',
      fmt: (v) => fmt.usd(v) },
    { key: 'Insight', label: 'Read as',
      chip: (v) => (v.startsWith('No forward') ? 'critical'
        : v.startsWith('Close') ? 'good' : 'warning') },
  ], rows.slice(0, 25), {
    footnote: 'Saving is the annual ordering plus cycle-stock holding cost avoided by '
      + 'moving to the economic quantity. The total-cost curve is flat near its minimum, '
      + 'so a small gap is genuinely not worth acting on.',
  });
}

async function fillFilters() {
  const nodes = await get('/api/network/nodes');
  options(document.getElementById('f-node'), nodes.rows.map((r) => r.NodeID));
  const skus = await get('/api/demand/skus', { limit: 5000 });
  options(document.getElementById('f-dept'),
    [...new Set(skus.rows.map((r) => r.Department))].filter(Boolean).sort());
  options(document.getElementById('f-abc'), ['A', 'B', 'C']);
  options(document.getElementById('f-urgency'),
    ['Stocked out', 'Below safety stock', 'At reorder point', 'Healthy']);
}

async function fillParams() {
  const { params } = await get('/api/parameters');
  const form = document.getElementById('param-form');
  Object.entries(params || {}).forEach(([key, value]) => {
    const field = form.elements[key];
    if (field) field.value = value;
  });
}

function wireParams() {
  const form = document.getElementById('param-form');
  if (!form || form.dataset.wired) return;
  form.dataset.wired = '1';
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const result = document.getElementById('param-result');
    result.textContent = 'Recomputing…';
    const payload = {};
    new FormData(form).forEach((value, key) => { if (value !== '') payload[key] = value; });
    try {
      const response = await fetch('/api/parameters', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
      result.innerHTML = `<span class="chip good">Rebuilt in ${body.elapsed_ms} ms</span>`;
      await load([renderKpis, renderUrgency, renderService, renderPlan, renderEoq]);
    } catch (error) {
      result.innerHTML = `<span class="chip critical">${error.message}</span>`;
    }
  });
}

export async function render() {
  await fillFilters();
  ['f-node', 'f-abc', 'f-dept', 'f-urgency', 'f-due'].forEach((id) => {
    const el = document.getElementById(id);
    if (el && !el.dataset.wired) {
      el.dataset.wired = '1';
      el.addEventListener('change', renderPlan);
    }
  });
  wireParams();
  wireScenario();
  await load([
    renderKpis, renderPolicyScenario, renderServiceFrontier, renderUrgency,
    renderService, renderPlan, renderEoq, fillParams,
  ]);
}
