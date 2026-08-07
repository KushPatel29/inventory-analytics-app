async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`Failed ${r.status}`);
  return await r.json();
}

function tableHTML(rows, fields, headers) {
  const thead = `<thead><tr>${headers.map(h => `<th class="px-2 py-1 text-left">${h}</th>`).join('')}</tr></thead>`;
  const tbody = `<tbody>${rows.map(r => `<tr>${fields.map(f => `<td class="px-2 py-1">${r[f] ?? ''}</td>`).join('')}</tr>`).join('')}</tbody>`;
  return `<div class="overflow-x-auto"><table class="min-w-full text-sm">${thead}${tbody}</table></div>`;
}

async function renderFZ2EXT(thr) {
  document.getElementById('fz2ext-val').textContent = Number(thr).toFixed(2);
  const data = await fetchJSON(`/api/moves/fz_to_ext?threshold=${encodeURIComponent(thr)}`);
  const fields = ['SKU','SKU_Desc','Supplier','FZ_OnHandWeight','EXT_OnHandWeight','AvgWeeklyUsage','DesiredEXT_Weight','WeightToMove'];
  const headers = ['SKU','Description','Supplier','FZ On-Hand','EXT On-Hand','Avg Use','Desired EXT','To Move'];
  document.getElementById('fz2ext-table').innerHTML = tableHTML(data.slice(0, 50), fields, headers);
  document.getElementById('fz2ext-dl').href = `/api/moves/download.xlsx?type=fz_to_ext&threshold=${encodeURIComponent(thr)}`;
}

async function renderEXT2FZ(thr) {
  document.getElementById('ext2fz-val').textContent = Number(thr).toFixed(2);
  const data = await fetchJSON(`/api/moves/ext_to_fz?threshold=${encodeURIComponent(thr)}`);
  const fields = ['SKU','SKU_Desc','Supplier','EXT_OnHandWeight','FZ_OnHandWeight','AvgWeeklyUsage','DesiredFZ_Weight','WeightToReturn'];
  const headers = ['SKU','Description','Supplier','EXT On-Hand','FZ On-Hand','Avg Use','Desired FZ','To Return'];
  document.getElementById('ext2fz-table').innerHTML = tableHTML(data.slice(0, 50), fields, headers);
  document.getElementById('ext2fz-dl').href = `/api/moves/download.xlsx?type=ext_to_fz&threshold=${encodeURIComponent(thr)}`;
}

export function initMoves() {
  const fz = document.getElementById('fz2ext-thr');
  const ex = document.getElementById('ext2fz-thr');
  const update = () => {
    renderFZ2EXT(fz.value).catch(()=>{});
    renderEXT2FZ(ex.value).catch(()=>{});
  };
  if (fz) fz.addEventListener('input', update);
  if (ex) ex.addEventListener('input', update);
  update();
}

