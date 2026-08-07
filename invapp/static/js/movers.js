async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`Failed ${r.status}`);
  return await r.json();
}

async function loadTop(q) {
  const params = new URLSearchParams({ q: q.toString() });
  const topU = await fetchJSON(`/api/movers/top_usage?${params.toString()}`);
  const topW = await fetchJSON(`/api/movers/top_woh?${params.toString()}`);
  Plotly.newPlot('mv-top-usage', [{ x: topU.map(d => d.AvgWeeklyUsage), y: topU.map(d => d.SKU_Desc), type: 'bar', orientation: 'h'}], { margin: { l: 180, t: 10 } }, { displayModeBar: false });
  Plotly.newPlot('mv-top-woh', [{ x: topW.map(d => d.WeeksOnHand), y: topW.map(d => d.SKU_Desc), type: 'bar', orientation: 'h'}], { margin: { l: 180, t: 10 } }, { displayModeBar: false });
}

async function loadHeat(q) {
  const params = new URLSearchParams({ q: q.toString() });
  const data = await fetchJSON(`/api/movers/heatmap?${params.toString()}`);
  const suppliers = [...new Set(data.map(d => d.Supplier))];
  const classes = [...new Set(data.map(d => d.MovementClass))];
  const z = classes.map(cls => suppliers.map(sup => {
    const found = data.find(d => d.Supplier === sup && d.MovementClass === cls);
    return found ? found.Count : 0;
  }));
  const trace = { z, x: suppliers, y: classes, type: 'heatmap', colorscale: 'Blues' };
  Plotly.newPlot('mv-heatmap', [trace], { margin: { t: 10 } }, { displayModeBar: false });
}

export function initMovers() {
  const apply = () => {
    const q = Number(document.getElementById('mv-q').value || 0.5);
    loadTop(q).catch(()=>{});
    loadHeat(q).catch(()=>{});
  };
  apply();
  const btn = document.getElementById('mv-apply');
  if (btn) btn.addEventListener('click', apply);
}

