async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`Failed ${r.status}`);
  return await r.json();
}

async function loadUsage() {
  const start = document.getElementById('sup-start')?.value || '';
  const end = document.getElementById('sup-end')?.value || '';
  const supplier = document.getElementById('sup-name')?.value || '';
  const q = new URLSearchParams();
  if (start) q.set('start', start);
  if (end) q.set('end', end);
  if (supplier) q.set('supplier', supplier);
  const data = await fetchJSON(`/api/supplier/trend/usage?${q.toString()}`);
  const x = data.map(d => d.Week);
  const y = data.map(d => d.Usage);
  Plotly.newPlot('sup-usage', [{ x, y, type: 'scatter', mode: 'lines+markers' }], { margin: { t: 10 }}, { displayModeBar: false });
}

async function loadWoh() {
  const supplier = document.getElementById('sup-name')?.value || '';
  const q = new URLSearchParams();
  if (supplier) q.set('supplier', supplier);
  const data = await fetchJSON(`/api/supplier/woh_distribution?${q.toString()}`);
  const x = data.map(d => d.WeeksOnHand);
  Plotly.newPlot('sup-woh', [{ x, type: 'histogram', marker: { color: '#059669' } }], { margin: { t: 10 }, xaxis: { title: 'Weeks On Hand' }}, { displayModeBar: false });
}

export function initSuppliers() {
  loadUsage().catch(()=>{});
  loadWoh().catch(()=>{});
  const btn = document.getElementById('sup-apply');
  if (btn) btn.addEventListener('click', () => { loadUsage(); loadWoh(); });
}

