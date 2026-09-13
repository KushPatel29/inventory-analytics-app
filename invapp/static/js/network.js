import { draw, fmt, get, kpis, load, options, palette, status, table } from './core.js';

const GRADE_TONE = { A: 'good', B: 'good', C: 'warning', D: 'critical', F: 'critical' };

async function renderKpis() {
  const [nodes, transfers, suppliers] = await Promise.all([
    get('/api/network/nodes'),
    get('/api/network/transfers', { limit: 5000 }),
    get('/api/suppliers/scorecard'),
  ]);
  const worst = [...nodes.rows].sort(
    (a, b) => (b.ShareGapPct ?? 0) - (a.ShareGapPct ?? 0)
  )[0];
  const benefit = transfers.rows.reduce((a, r) => a + (r.BenefitUSD || 0), 0);
  const freight = transfers.rows.reduce((a, r) => a + (r.FreightCostUSD || 0), 0);
  const failing = suppliers.rows.filter((r) => ['D', 'F'].includes(r.Grade));
  const onTime = suppliers.rows.length
    ? suppliers.rows.reduce((a, r) => a + (r.OnTimePct || 0) * r.POLines, 0)
      / suppliers.rows.reduce((a, r) => a + r.POLines, 0)
    : null;
  const perfect = suppliers.rows.length
    ? suppliers.rows.reduce((a, r) => a + (r.PerfectOrderPct || 0) * r.POLines, 0)
      / suppliers.rows.reduce((a, r) => a + r.POLines, 0)
    : null;

  kpis('kpi-network', [
    { label: 'Nodes', value: fmt.n(nodes.rows.length),
      note: `${fmt.n(nodes.rows.reduce((a, r) => a + r.SKUCount, 0))} stocked locations` },
    { label: 'Most over-weighted node', value: worst ? worst.NodeID : '—',
      note: worst ? `holds ${fmt.pct(worst.ValueSharePct, 0)} of value for `
        + `${fmt.pct(worst.DemandShare, 0)} of demand` : '',
      tone: worst && worst.ShareGapPct > 0.04 ? 'warning' : undefined },
    { label: 'Transfers worth making', value: fmt.n(transfers.total),
      note: `${fmt.usdShort(benefit)} of benefit for ${fmt.usdShort(freight)} of freight` },
    { label: 'On-time delivery', value: fmt.pct(onTime),
      note: 'weighted by receipt lines, with a 2-day grace',
      tone: onTime >= 0.9 ? 'good' : onTime >= 0.8 ? 'warning' : 'critical' },
    { label: 'Perfect order rate', value: fmt.pct(perfect),
      note: 'on time and complete and no rejects',
      tone: perfect >= 0.85 ? 'good' : perfect >= 0.7 ? 'warning' : 'critical' },
    { label: 'Suppliers below grade C', value: fmt.n(failing.length),
      note: failing.length ? failing.map((r) => r.SupplierID).join(', ') : 'none',
      tone: failing.length ? 'critical' : 'good' },
  ]);
}

async function renderShare() {
  const { rows } = await get('/api/network/nodes');
  const sorted = [...rows].sort((a, b) => b.InventoryValueUSD - a.InventoryValueUSD);
  const colors = palette();
  draw('share-chart', [
    { type: 'bar', name: 'Share of inventory value', x: sorted.map((r) => r.NodeID),
      y: sorted.map((r) => (r.ValueSharePct || 0) * 100), marker: { color: colors[0] },
      hovertemplate: '%{x}<br>%{y:.1f}% of stock value<extra></extra>' },
    { type: 'bar', name: 'Share of demand', x: sorted.map((r) => r.NodeID),
      y: sorted.map((r) => (r.DemandShare || 0) * 100), marker: { color: colors[1] },
      hovertemplate: '%{x}<br>%{y:.1f}% of demand<extra></extra>' },
  ], {
    barmode: 'group', bargap: 0.32, bargroupgap: 0.06, showlegend: true,
    yaxis: { ticksuffix: '%' }, margin: { t: 34, b: 40, l: 48, r: 16 },
  });
}

async function renderNodes() {
  const { rows } = await get('/api/network/nodes');
  table('node-table', [
    { key: 'NodeID', label: 'Node', cls: 'strong' },
    { key: 'NodeName', label: 'Site' },
    { key: 'Region', label: 'Region' },
    { key: 'SKUCount', label: 'SKUs', num: true, fmt: (v) => fmt.n(v) },
    { key: 'InventoryValueUSD', label: 'Value', num: true, fmt: (v) => fmt.usdShort(v) },
    { key: 'ShareGapPct', label: 'Stock vs demand', num: true,
      fmt: (v) => fmt.signedPct(v, 1) },
    { key: 'StockedOutSKUs', label: 'Stocked out', num: true, fmt: (v) => fmt.n(v) },
    { key: 'BelowReorderSKUs', label: 'Below ROP', num: true, fmt: (v) => fmt.n(v) },
    { key: 'StockoutExposureUSD', label: 'At risk', num: true, fmt: (v) => fmt.usdShort(v) },
  ], rows, {
    footnote: 'A positive stock-versus-demand gap means the node holds more of the '
      + 'network&rsquo;s stock than it ships of its demand.',
  });
}

async function renderTransfers() {
  const params = {
    FromNode: document.getElementById('t-from').value,
    ToNode: document.getElementById('t-to').value,
    limit: 200,
  };
  const { rows, total, truncated } = await get('/api/network/transfers', params);
  table('transfer-table', [
    { key: 'SKU', label: 'SKU', cls: 'strong' },
    { key: 'ItemDescription', label: 'Item', cls: 'wrap' },
    { key: 'FromNode', label: 'From' },
    { key: 'ToNode', label: 'To' },
    { key: 'TransferUnits', label: 'Units', num: true, fmt: (v) => fmt.n(v) },
    { key: 'TransferValueUSD', label: 'Stock value', num: true, fmt: (v) => fmt.usd(v) },
    { key: 'FreightCostUSD', label: 'Freight', num: true, fmt: (v) => fmt.usd(v) },
    { key: 'BenefitUSD', label: 'Benefit', num: true, cls: 'strong', fmt: (v) => fmt.usd(v) },
    { key: 'BenefitRatio', label: 'x freight', num: true, fmt: (v) => `${fmt.n(v, 1)}×` },
    { key: 'FromCoverWeeksBefore', label: 'From cover', num: true,
      fmt: (v) => (v === null ? '∞' : `${fmt.n(v, 0)} wk`) },
    { key: 'ToCoverWeeksBefore', label: 'To cover', num: true,
      fmt: (v) => (v === null ? '∞' : `${fmt.n(v, 1)} wk`) },
    { key: 'ToCoverWeeksAfter', label: 'To cover after', num: true,
      fmt: (v) => (v === null ? '∞' : `${fmt.n(v, 1)} wk`) },
  ], rows, {
    empty: 'No move on this lane clears its own freight.',
    footnote: truncated ? `Showing the 200 largest of ${fmt.n(total)} moves by benefit.` : '',
  });
}

async function renderSuppliers() {
  const { rows } = await get('/api/suppliers/scorecard');
  table('supplier-table', [
    { key: 'SupplierName', label: 'Supplier', cls: 'strong' },
    { key: 'Country', label: 'Country' },
    { key: 'POLines', label: 'Lines', num: true, fmt: (v) => fmt.n(v) },
    { key: 'SpendUSD', label: 'Spend', num: true, fmt: (v) => fmt.usdShort(v) },
    { key: 'OnTimePct', label: 'On time', num: true, fmt: (v) => fmt.pct(v, 0) },
    { key: 'FillRatePct', label: 'Fill rate', num: true, fmt: (v) => fmt.pct(v, 0) },
    { key: 'DefectRatePct', label: 'Defects', num: true, fmt: (v) => fmt.pct(v, 1) },
    { key: 'PerfectOrderPct', label: 'Perfect orders', num: true, fmt: (v) => fmt.pct(v, 0) },
    { key: 'LeadTimeDaysActual', label: 'Lead', num: true, fmt: (v) => fmt.days(v) },
    { key: 'LeadTimeGapDays', label: 'vs contract', num: true,
      fmt: (v) => `${v > 0 ? '+' : ''}${fmt.n(v, 1)} d` },
    { key: 'Score', label: 'Score', num: true, fmt: (v) => fmt.n(v, 1) },
    { key: 'Grade', label: 'Grade', chip: (v) => GRADE_TONE[v] || 'neutral' },
  ], rows);
}

async function renderLead() {
  const { rows } = await get('/api/suppliers/lead_time_gap');
  const colors = palette();
  const sorted = [...rows].sort((a, b) => b.LeadTimeDaysActual - a.LeadTimeDaysActual);
  draw('lead-chart', [
    { type: 'bar', orientation: 'h', name: 'Contracted',
      x: sorted.map((r) => r.LeadTimeDaysContract), y: sorted.map((r) => r.SupplierID),
      marker: { color: colors[0] },
      hovertemplate: '%{y}<br>contracted %{x:.0f} days<extra></extra>' },
    { type: 'bar', orientation: 'h', name: 'Delivered',
      x: sorted.map((r) => r.LeadTimeDaysActual), y: sorted.map((r) => r.SupplierID),
      marker: { color: colors[1] },
      hovertemplate: '%{y}<br>delivered %{x:.0f} days<extra></extra>' },
  ], {
    barmode: 'group', bargap: 0.24, bargroupgap: 0.05, showlegend: true,
    margin: { l: 84, r: 18, t: 34, b: 40 },
    yaxis: { autorange: 'reversed' },
    xaxis: { ticksuffix: ' d' },
  });
}

async function renderPos() {
  const params = {
    SupplierID: document.getElementById('po-supplier').value,
    overdue: document.getElementById('po-overdue').checked ? '1' : '',
    limit: 200,
  };
  const { rows, total, truncated } = await get('/api/suppliers/open_pos', params);
  table('po-table', [
    { key: 'PONumber', label: 'PO', cls: 'strong' },
    { key: 'SupplierID', label: 'Supplier' },
    { key: 'SKU', label: 'SKU' },
    { key: 'NodeID', label: 'To' },
    { key: 'OrderDate', label: 'Ordered' },
    { key: 'PromisedDate', label: 'Promised' },
    { key: 'QtyOrdered', label: 'Units', num: true, fmt: (v) => fmt.n(v) },
    { key: 'DaysOutstanding', label: 'Outstanding', num: true, fmt: (v) => fmt.days(v) },
    { key: 'DaysLate', label: 'Late by', num: true, fmt: (v) => fmt.days(v),
      chip: (v) => (v > 14 ? 'critical' : v > 0 ? 'warning' : 'good') },
  ], rows, {
    empty: 'Nothing outstanding under these filters.',
    footnote: truncated ? `Showing 200 of ${fmt.n(total)} open lines, latest first.` : '',
  });
}

async function fillFilters() {
  const nodes = await get('/api/network/nodes');
  options(document.getElementById('t-from'), nodes.rows.map((r) => r.NodeID));
  options(document.getElementById('t-to'), nodes.rows.map((r) => r.NodeID));
  const suppliers = await get('/api/suppliers/scorecard');
  options(document.getElementById('po-supplier'), suppliers.rows.map((r) => r.SupplierID));
}

export async function render() {
  await fillFilters();
  ['t-from', 't-to'].forEach((id) => {
    const el = document.getElementById(id);
    if (el && !el.dataset.wired) {
      el.dataset.wired = '1';
      el.addEventListener('change', renderTransfers);
    }
  });
  ['po-supplier', 'po-overdue'].forEach((id) => {
    const el = document.getElementById(id);
    if (el && !el.dataset.wired) {
      el.dataset.wired = '1';
      el.addEventListener('change', renderPos);
    }
  });
  await load([renderKpis, renderShare, renderNodes, renderTransfers,
    renderSuppliers, renderLead, renderPos]);
}

export { status };
