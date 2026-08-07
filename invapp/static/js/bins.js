async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`Failed ${r.status}`);
  return await r.json();
}

export async function initBins() {
  try {
    const kpis = await fetchJSON('/api/bins/summary');
    const el = document.getElementById('bins-kpis');
    if (el && !kpis.error) {
      el.innerHTML = `Total packs: <b>${kpis.total_packs}</b> • Total weight: <b>${(kpis.total_weight||0).toLocaleString()}</b> lb • Products: <b>${kpis.products}</b> • Bins: <b>${kpis.bins}</b> • Locations: <b>${kpis.locations}</b>`;
    }
  } catch {}

  try {
    const p = await fetchJSON('/api/bins/weight_by_protein');
    const x = p.map(d => d.TotalWeight);
    const y = p.map(d => d.Protein);
    Plotly.newPlot('bins-by-protein', [{ x, y, type: 'bar', orientation: 'h'}], { margin: { l: 120, t: 10 } }, { displayModeBar: false });
  } catch {}

  try {
    const p = await fetchJSON('/api/bins/weight_by_location');
    const x = p.map(d => d.TotalWeight);
    const y = p.map(d => d.ProductLocation);
    Plotly.newPlot('bins-by-location', [{ x, y, type: 'bar', orientation: 'h'}], { margin: { l: 120, t: 10 } }, { displayModeBar: false });
  } catch {}

  const btn = document.getElementById('bins-download');
  if (btn) {
    btn.addEventListener('click', () => {
      const loc = document.getElementById('bins-location')?.value || '';
      const prot = document.getElementById('bins-protein')?.value || '';
      const url = `/api/bins/download.csv?location=${encodeURIComponent(loc)}&protein=${encodeURIComponent(prot)}`;
      window.location = url;
    });
  }
}

