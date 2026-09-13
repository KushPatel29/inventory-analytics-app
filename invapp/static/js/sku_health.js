import {
  draw, fmt, get, kpis, load, options, palette, sequential, status, table,
} from './core.js';

const AGE_ORDER = ['0-30 days', '31-60 days', '61-90 days', '91-180 days',
  '181-365 days', '365+ days'];

async function renderKpis() {
  const h = await get('/api/health/summary');
  kpis('kpi-health', [
    { label: 'A-class concentration', value: fmt.pct(h.AClassShareOfValue, 0),
      note: `of consumption value, on ${fmt.pct(h.AClassShareOfSKUs, 0)} of the SKUs` },
    { label: 'Dead stock', value: fmt.usdShort(h.DeadStockValueUSD),
      note: `${fmt.n(h.DeadStockSKUs)} SKUs with nothing shipped in 180 days`,
      tone: h.DeadStockValueUSD > 0 ? 'critical' : 'good' },
    { label: 'Slow moving', value: fmt.usdShort(h.SlowMovingValueUSD),
      note: `${fmt.n(h.SlowMovingSKUs)} SKUs quiet for 90 days`, tone: 'warning' },
    { label: 'Excess above target', value: fmt.usdShort(h.ExcessValueUSD),
      note: 'stock over the reorder point plus one order', tone: 'warning' },
    { label: 'Provision if written down', value: fmt.usdShort(h.EOReserveUSD),
      note: 'on the age ladder: 0 / 25 / 50 / 100%' },
    { label: 'Critical items', value: fmt.n(h.CriticalSKUs),
      note: 'A-class value on Y or Z variability' },
  ]);
}

async function renderPareto() {
  const { rows } = await get('/api/health/pareto', { limit: 500 });
  const colors = palette();
  draw('pareto-chart', [
    { type: 'bar', name: 'Consumption value', x: rows.map((r) => r.Rank),
      y: rows.map((r) => r.Value), marker: { color: colors[0] }, yaxis: 'y',
      hovertemplate: 'Rank %{x}<br>$%{y:,.0f}<extra></extra>' },
  ], {
    margin: { l: 66, r: 16, t: 12, b: 44 },
    xaxis: { title: { text: 'SKU rank by annual consumption value' } },
    yaxis: { tickprefix: '$' },
    shapes: [],
  });

  // The cumulative curve is the point of a Pareto chart, and putting it on a
  // second y-axis is the classic dual-axis mistake: the two scales can be slid
  // against each other until any story appears. It gets its own panel instead,
  // sharing the x axis, with the 80/95 lines marked on it.
  const el = document.getElementById('pareto-chart');
  if (!el || !el.parentElement) return;
  let second = document.getElementById('pareto-cum');
  if (!second) {
    second = document.createElement('div');
    second.id = 'pareto-cum';
    second.className = 'chart short';
    el.parentElement.appendChild(second);
  }
  const cuts = [0.8, 0.95];
  draw('pareto-cum', [
    { type: 'scatter', mode: 'lines', name: 'Cumulative share',
      x: rows.map((r) => r.Rank), y: rows.map((r) => r.CumulativeShare * 100),
      line: { color: colors[1], width: 2 },
      hovertemplate: 'Top %{x} SKUs<br>%{y:.1f}% of value<extra></extra>' },
  ], {
    margin: { l: 66, r: 16, t: 8, b: 40 },
    xaxis: { title: { text: 'SKU rank' } },
    yaxis: { ticksuffix: '%', range: [0, 103] },
    shapes: cuts.map((c) => ({
      type: 'line', x0: 0, x1: rows.length, y0: c * 100, y1: c * 100,
      line: { color: status.warning(), width: 1, dash: 'dot' },
    })),
    annotations: cuts.map((c) => ({
      x: rows.length, y: c * 100, text: `${c * 100}%`, showarrow: false,
      xanchor: 'right', yanchor: 'bottom', font: { size: 11 },
    })),
  });
}

async function renderMatrix() {
  const { rows } = await get('/api/health/matrix');
  const el = document.getElementById('matrix');
  if (!el) return;
  const byKey = Object.fromEntries(rows.map((r) => [`${r.ABCClass}${r.XYZClass}`, r]));
  const max = Math.max(...rows.map((r) => r.InventoryValueUSD), 1);
  const ramp = sequential();

  // Choose the higher-contrast text colour from the rendered shade. The ramp
  // reverses in dark mode, so a fixed threshold by index fails across themes.
  const contrastText = (hex) => {
    const rgb = [1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16) / 255);
    const linear = rgb.map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    const luminance = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
    const whiteContrast = 1.05 / (luminance + 0.05);
    const darkContrast = (luminance + 0.05) / 0.05;
    return whiteContrast >= darkContrast ? '#fff' : '#000';
  };

  // Value is the magnitude here, so it gets a single-hue sequential ramp, not
  // one categorical colour per cell.
  const shade = (value) => {
    const t = Math.sqrt(value / max);
    const index = Math.min(ramp.length - 1, Math.max(0, Math.round(t * (ramp.length - 1))));
    return ramp[index];
  };

  const header = ['<th scope="col"><span class="sr-only">ABC class</span></th>',
    ...['X', 'Y', 'Z'].map((x) => `<th scope="col">${x}</th>`)].join('');
  const body = ['A', 'B', 'C'].map((abc) => {
    const cells = ['X', 'Y', 'Z'].map((xyz) => {
      const cell = byKey[`${abc}${xyz}`];
      if (!cell) return '<td class="empty">—</td>';
      const bg = shade(cell.InventoryValueUSD);
      return `<td style="background:${bg};color:${contrastText(bg)}"
                  title="${cell.Policy}"
                  aria-label="${abc}${xyz}: ${fmt.n(cell.SKUCount)} SKUs, ${fmt.usdShort(cell.InventoryValueUSD)}. ${cell.Policy}">
        <div class="cell-code">${abc}${xyz}</div>
        <div class="cell-n">${fmt.n(cell.SKUCount)} SKUs</div>
        <div class="cell-v">${fmt.usdShort(cell.InventoryValueUSD)}</div>
      </td>`;
    }).join('');
    return `<tr><th scope="row">${abc}</th>${cells}</tr>`;
  }).join('');

  el.innerHTML = `<table class="matrix"><caption class="sr-only">ABC XYZ inventory policy matrix</caption><thead><tr>${header}</tr></thead>`
    + `<tbody>${body}</tbody></table>`
    + '<div class="note-inline">Cell shade is inventory value; hover a cell for its stock '
    + 'policy. X is a coefficient of variation under 0.5, Z over 1.0 &mdash; above that the '
    + 'standard deviation exceeds the mean and no model will fix it.</div>';
}

async function renderAgeing() {
  const dim = document.getElementById('age-dim').value;
  const { rows } = await get('/api/health/ageing', dim ? { dim } : {});
  const ramp = sequential();

  if (!dim) {
    const ordered = AGE_ORDER.map((b) => rows.find((r) => r.AgeBucket === b))
      .filter(Boolean);
    draw('ageing-chart', [{
      type: 'bar', x: ordered.map((r) => r.AgeBucket),
      y: ordered.map((r) => r.InventoryValueUSD),
      marker: { color: ordered.map((_, i) => ramp[Math.min(i, ramp.length - 1)]) },
      hovertemplate: '%{x}<br>$%{y:,.0f}<extra></extra>',
    }], { bargap: 0.3, yaxis: { tickprefix: '$' } });
    return;
  }

  const groups = [...new Set(rows.map((r) => r[dim]))].sort();
  const traces = AGE_ORDER.map((bucket, i) => ({
    type: 'bar', name: bucket,
    x: groups,
    y: groups.map((g) => {
      const hit = rows.find((r) => r[dim] === g && r.AgeBucket === bucket);
      return hit ? hit.InventoryValueUSD : 0;
    }),
    marker: { color: ramp[Math.min(i, ramp.length - 1)],
      line: { color: 'var(--surface)', width: 2 } },
    hovertemplate: `%{x}<br>${bucket}: $%{y:,.0f}<extra></extra>`,
  }));
  draw('ageing-chart', traces, {
    barmode: 'stack', bargap: 0.3, showlegend: true,
    yaxis: { tickprefix: '$' }, margin: { t: 40, b: 60, l: 62, r: 16 },
    xaxis: { tickangle: -25 },
  });
}

async function renderMovement() {
  const { rows } = await get('/api/health/movement');
  const order = ['Fast', 'Medium', 'Slow', 'Dead'];
  const sorted = order.map((m) => rows.find((r) => r.MovementClass === m)).filter(Boolean);
  const tone = { Fast: status.good(), Medium: palette()[0],
    Slow: status.warning(), Dead: status.critical() };
  draw('movement-chart', [
    { type: 'bar', name: 'Inventory value', x: sorted.map((r) => r.MovementClass),
      y: sorted.map((r) => r.InventoryValueUSD),
      marker: { color: sorted.map((r) => tone[r.MovementClass]) },
      text: sorted.map((r) => `${fmt.n(r.SKUCount)} SKUs`),
      textposition: 'outside',
      hovertemplate: '%{x}<br>$%{y:,.0f}<br>%{text}<extra></extra>' },
  ], { bargap: 0.34, yaxis: { tickprefix: '$' }, margin: { t: 26 } });
}

async function renderDead() {
  const params = {
    NodeID: document.getElementById('dead-node').value,
    Department: document.getElementById('dead-dept').value,
    HealthFlag: document.getElementById('dead-flag').value,
    limit: 250,
  };
  const { rows, total, truncated } = await get('/api/health/dead_stock', params);
  table('dead-table', [
    { key: 'SKU', label: 'SKU', cls: 'strong' },
    { key: 'ItemDescription', label: 'Item', cls: 'wrap' },
    { key: 'NodeID', label: 'Node' },
    { key: 'Department', label: 'Department' },
    { key: 'ABCClass', label: 'ABC' },
    { key: 'OnHandUnits', label: 'On hand', num: true, fmt: (v) => fmt.n(v) },
    { key: 'InventoryValueUSD', label: 'Value', num: true, cls: 'strong', fmt: (v) => fmt.usd(v) },
    { key: 'AgeDays', label: 'Age', num: true, fmt: (v) => fmt.days(v) },
    { key: 'DaysSinceLastShip', label: 'Last shipped', num: true,
      fmt: (v) => (v === null ? 'never' : fmt.days(v)) },
    { key: 'ReserveRate', label: 'Provision', num: true, fmt: (v) => fmt.pct(v, 0) },
    { key: 'EOReserveUSD', label: 'Write-down', num: true, fmt: (v) => fmt.usd(v) },
    { key: 'HealthFlag', label: 'Flag',
      chip: (v) => (v === 'Dead stock' ? 'critical' : v === 'Slow moving' ? 'warning' : 'neutral') },
  ], rows, {
    empty: 'Nothing is dead or slow under these filters.',
    footnote: truncated ? `Showing the 250 largest of ${fmt.n(total)} locations by value.` : '',
  });
}

async function fillFilters() {
  const nodes = await get('/api/network/nodes');
  options(document.getElementById('dead-node'), nodes.rows.map((r) => r.NodeID));
  const skus = await get('/api/demand/skus', { limit: 5000 });
  options(document.getElementById('dead-dept'),
    [...new Set(skus.rows.map((r) => r.Department))].filter(Boolean).sort());
  options(document.getElementById('dead-flag'), ['Dead stock', 'Slow moving']);
}

export async function render() {
  await fillFilters();
  const ageDim = document.getElementById('age-dim');
  if (ageDim && !ageDim.dataset.wired) {
    ageDim.dataset.wired = '1';
    ageDim.addEventListener('change', renderAgeing);
  }
  ['dead-node', 'dead-dept', 'dead-flag'].forEach((id) => {
    const el = document.getElementById(id);
    if (el && !el.dataset.wired) {
      el.dataset.wired = '1';
      el.addEventListener('change', renderDead);
    }
  });
  await load([renderKpis, renderPareto, renderMatrix, renderAgeing, renderMovement, renderDead]);
}
