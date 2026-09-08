/* Fetch data from API endpoints and render Plotly charts and summary cards */

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return await res.json();
}

function formatNumber(x) {
  return x?.toLocaleString?.(undefined, { maximumFractionDigits: 1 }) ?? `${x}`;
}

function escapeHTML(value) {
  const text = document.createElement('span');
  text.textContent = String(value ?? '');
  return text.innerHTML;
}

async function renderKpis() {
  try {
    const k = await fetchJSON('/api/insights');
    const el = document.getElementById('kpi-cards');
    if (!el || k.error) return;
    el.innerHTML = `
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div class="bg-white rounded p-4 shadow"><div class="text-sm text-gray-500">SKUs in inventory</div><div class="text-2xl font-semibold">${formatNumber(k.total_skus)}</div></div>
        <div class="bg-white rounded p-4 shadow"><div class="text-sm text-gray-500">On-Hand (lb)</div><div class="text-2xl font-semibold">${formatNumber(k.total_weight_lb)}</div></div>
        <div class="bg-white rounded p-4 shadow"><div class="text-sm text-gray-500">On-Hand Cost ($)</div><div class="text-2xl font-semibold">${formatNumber(k.total_cost)}</div></div>
        <div class="bg-white rounded p-4 shadow"><div class="text-sm text-gray-500">Avg WOH (wks)</div><div class="text-2xl font-semibold">${formatNumber(k.avg_woh)}</div></div>
      </div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
        <div class="bg-white rounded p-4 shadow"><div class="text-sm text-gray-500">Median WOH</div><div class="text-xl font-semibold">${formatNumber(k.med_woh)}</div></div>
        <div class="bg-white rounded p-4 shadow"><div class="text-sm text-gray-500">Avg Turns/yr</div><div class="text-xl font-semibold">${formatNumber(k.avg_turns)}</div></div>
        <div class="bg-white rounded p-4 shadow"><div class="text-sm text-gray-500">Median Turns/yr</div><div class="text-xl font-semibold">${formatNumber(k.med_turns)}</div></div>
        <div class="bg-white rounded p-4 shadow"><div class="text-sm text-gray-500">At-Risk SKUs (&lt;1 WOH)</div><div class="text-xl font-semibold">${formatNumber(k.at_risk_skus)}</div></div>
      </div>
    `;
  } catch (e) {
    console.warn('KPIs not available yet:', e);
  }
}

async function renderTopSuppliers() {
  try {
    const data = await fetchJSON('/api/suppliers/top_cost?n=12');
    const x = data.map(d => d.OnHandCostTotal);
    const y = data.map(d => d.Supplier);
    const trace = { x, y, type: 'bar', orientation: 'h', marker: { color: '#16a34a' } };
    Plotly.newPlot('top-suppliers', [trace], { margin: { l: 120, r: 10, t: 10, b: 40 } }, { displayModeBar: false });
  } catch (e) {
    // ignore until data is processed
  }
}

async function renderHoldingCost() {
  try {
    const data = await fetchJSON('/api/holding_cost/top?n=50');
    const x = data.map(d => d.TotalHoldingCost);
    const y = data.map(d => d.SKU_Desc);
    const trace = { x, y, type: 'bar', orientation: 'h', marker: { color: '#f97316' } };
    Plotly.newPlot('holding-cost-top', [trace], { margin: { l: 200, r: 10, t: 10, b: 40 } }, { displayModeBar: false });
  } catch (e) {}
}

async function renderAtRiskDonut() {
  try {
    const k = await fetchJSON('/api/insights');
    if (k.error) return;
    const labels = ['At-Risk', 'Healthy'];
    const values = [k.at_risk_skus, k.healthy_skus];
    Plotly.newPlot('at-risk-donut', [{ labels, values, type: 'pie', hole: 0.5 }], { margin: { t: 10 } }, { displayModeBar: false });
  } catch (e) {}
}

async function renderABC() {
  try {
    const data = await fetchJSON('/api/insights/abc');
    const x = data.map(d => d.ABC);
    const y = data.map(d => d.Value);
    Plotly.newPlot('abc-breakdown', [{ x, y, type: 'bar' }], { margin: { t: 10 }, xaxis: { title: 'Class' }, yaxis: { title: 'Inv Value ($)' } }, { displayModeBar: false });
  } catch (e) {}
}

async function renderCostByProtein() {
  try {
    const data = await fetchJSON('/api/insights/cost_by_protein');
    const x = data.map(d => d.TotalCost);
    const y = data.map(d => d.Protein);
    Plotly.newPlot('cost-by-protein', [{ x, y, type: 'bar', orientation: 'h' }], { margin: { l: 120, t: 10 } }, { displayModeBar: false });
  } catch (e) {}
}

async function renderCostVsWoh() {
  try {
    const pts = await fetchJSON('/api/insights/cost_vs_woh');
    const trace = {
      x: pts.map(p => p.WeeksOnHand),
      y: pts.map(p => p.Cost),
      text: pts.map(p => p.Label),
      mode: 'markers', type: 'scatter', marker: { size: 6, color: '#0ea5e9' }
    };
    Plotly.newPlot('cost-vs-woh', [trace], { xaxis: { title: 'Weeks On Hand' }, yaxis: { title: 'On-Hand Cost ($)' }, margin: { t: 10 } }, { displayModeBar: false });
  } catch (e) {}
}

async function renderQuadrants() {
  try {
    const q = await fetchJSON('/api/quadrants');
    const counts = q.counts || {};
    const labels = Object.keys(counts);
    const values = labels.map(k => counts[k]);
    const trace = { labels, values, type: 'pie', hole: 0.5 };
    Plotly.newPlot('quadrant-pie', [trace], { margin: { t: 10, b: 10 } }, { displayModeBar: false });
  } catch (e) {}
}

async function renderSVSI() {
  try {
    const pts = await fetchJSON('/api/svsi?n=400');
    const trace = {
      x: pts.map(p => p.X),
      y: pts.map(p => p.Y),
      text: pts.map(p => p.Label),
      mode: 'markers', type: 'scatter',
      marker: { size: 6, color: pts.map(p => (p.Protein || '')) }
    };
    Plotly.newPlot('svsi-scatter', [trace], {
      xaxis: { type: 'log', title: 'Total Usage (lb)'},
      yaxis: { type: 'log', title: 'On-Hand (lb)'},
      margin: { t: 10, r: 10, b: 50, l: 60 }
    }, { displayModeBar: false });
  } catch (e) {}
}

async function renderPurchasePlan() {
  try {
    const slider = document.getElementById('woh-slider');
    const woh = slider ? Number(slider.value) : 4;
    const data = await fetchJSON(`/api/purchase_plan?woh=${woh}`);
    const container = document.getElementById('purchase-plan');
    if (!container) return;
    const top = data.slice(0, 20);
    const rows = top.map(r => `<tr>
        <td class="px-2 py-1">${escapeHTML(r.ParentSKU)}</td>
        <td class="px-2 py-1">${escapeHTML(r.SKU_Desc)}</td>
        <td class="px-2 py-1">${escapeHTML(r.Supplier)}</td>
        <td class="px-2 py-1 text-right">${(r.MeanUse||0).toFixed(1)}</td>
        <td class="px-2 py-1 text-right">${(r.PacksToOrder||0)}</td>
        <td class="px-2 py-1 text-right">${(r.OrderWt||0).toFixed(0)}</td>
        <td class="px-2 py-1 text-right">$${(r.EstCost||0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</td>
      </tr>`).join('');
    container.innerHTML = `
      <div class="mb-2 text-sm text-gray-600">Showing top ${top.length} by estimated cost.</div>
      <div class="overflow-x-auto"><table class="min-w-full text-sm">
        <thead><tr class="text-left">
          <th class="px-2 py-1">Parent SKU</th>
          <th class="px-2 py-1">Description</th>
          <th class="px-2 py-1">Supplier</th>
          <th class="px-2 py-1 text-right">Mean Use</th>
          <th class="px-2 py-1 text-right">Packs</th>
          <th class="px-2 py-1 text-right">Order Wt</th>
          <th class="px-2 py-1 text-right">Est Cost</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table></div>`;
  } catch (e) {}
}

export async function initAnalytics() {
  await renderKpis();
  await Promise.all([
    renderWeeklyUsage(),
    renderTurnover(document.getElementById("turnover-group")?.value || "product"),
    renderTopSuppliers(),
    renderHoldingCost(),
    renderAtRiskDonut(),
    renderABC(),
    renderCostByProtein(),
    renderCostVsWoh(),
    renderQuadrants(),
    renderSVSI(),
    renderPurchasePlan(),
  ]);
}


// Turnover with date filters
async function renderTurnover(group) {
  const start = document.getElementById('turnover-start')?.value || '';
  const end = document.getElementById('turnover-end')?.value || '';
  const params = new URLSearchParams({ group });
  if (start) params.set('start', start);
  if (end) params.set('end', end);
  try {
    const res = await fetchJSON(`/api/turnover?${params.toString()}`);
    if (res.error) return;
    const items = res.items || [];
    const y = items.map(i => i.key).slice(0, 30);
    const x = items.map(i => i.AnnualizedTurnover).slice(0, 30);
    Plotly.newPlot('turnover-chart', [{ x, y, type: 'bar', orientation: 'h', marker: { color: '#4338ca' } }], { margin: { l: 180, t: 10 } }, { displayModeBar: false });
  } catch (e) {}
}

window.renderTurnover = renderTurnover;

async function initRuns() {
  try {
    const res = await fetchJSON('/api/runs');
    const sel = document.getElementById('runs-select');
    if (!sel) return;
    sel.innerHTML = '';
    res.forEach(r => {
      const opt = document.createElement('option');
      opt.value = r.id; opt.textContent = `Run #${r.id} • ${r.created_at}`;
      sel.appendChild(opt);
    });
    const btn = document.getElementById('runs-load');
    if (btn) {
      btn.onclick = async () => {
        const id = sel.value;
        if (!id) return;
        const rr = document.getElementById('runs-result');
        try {
          const response = await fetch(`/api/runs/load?run_id=${encodeURIComponent(id)}`, { method: 'POST' });
          if (!response.ok) throw new Error(`Request failed: ${response.status}`);
          rr.textContent = `Loaded run #${id}`;
          await initAnalytics();
        } catch (e) {
          rr.textContent = `Failed to load run #${id}`;
        }
      };
    }
  } catch (e) { /* ignore */ }
}


// Sales History contains shipped pounds and expected dates, not receipt dates.
async function renderWeeklyUsage() {
  const target = document.getElementById('weekly-usage');
  if (!target) return;
  try {
    const rows = await fetchJSON('/api/supplier/trend/usage');
    if (!Array.isArray(rows) || !rows.length) {
      target.textContent = 'No sales history available. Process a workbook to view weekly shipped volume.';
      return;
    }
    target.textContent = '';
    await Plotly.newPlot(target, [{
      x: rows.map(row => row.Week), y: rows.map(row => row.Usage),
      type: 'scatter', mode: 'lines+markers', line: {color: '#10b981'},
      hovertemplate: '%{x}<br>%{y:,.0f} lb<extra></extra>'
    }], {margin: {l: 70, r: 20, t: 10, b: 45},
      xaxis: {title: 'Week (expected date)'}, yaxis: {title: 'Shipped (lb)'}
    }, {displayModeBar: false, responsive: true});
  } catch (e) {
    target.textContent = 'Weekly volume is unavailable. Reload the page or process a workbook to retry.';
  }
}

export async function initDashboard() {
  const slider = document.getElementById('woh-slider');
  if (slider) slider.addEventListener('change', renderPurchasePlan);
  document.body.addEventListener('htmx:afterRequest', async event => {
    if (event.detail.successful && event.detail.elt?.getAttribute('hx-post') === '/api/workbook/process') {
      await Promise.all([initAnalytics(), initRuns()]);
    }
  });
  await Promise.all([initAnalytics(), initRuns()]);
}
