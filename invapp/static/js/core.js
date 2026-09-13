/*
 * Shared front-end: fetching, formatting, tables and the chart theme.
 *
 * The chart colours are read from the stylesheet's custom properties rather
 * than hard-coded here. That is not tidiness - it is the only way the dark
 * theme can be a *selected* palette rather than an inverted one, because the
 * dark steps are different hex values chosen for the dark surface, and a
 * second copy of them in JavaScript would drift from the first within a week.
 */

const css = (name, fallback) => {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
};

export const palette = () => [
  css('--series-1', '#2a78d6'), css('--series-2', '#eb6834'),
  css('--series-3', '#1baf7a'), css('--series-4', '#eda100'),
  css('--series-5', '#e87ba4'), css('--series-6', '#008300'),
  css('--series-7', '#4a3aa7'), css('--series-8', '#e34948'),
];

export const sequential = () => [
  css('--seq-100', '#cde2fb'), css('--seq-250', '#86b6ef'), css('--seq-400', '#3987e5'),
  css('--seq-550', '#1c5cab'), css('--seq-700', '#0d366b'),
];

export const status = {
  good: () => css('--good', '#0ca30c'),
  warning: () => css('--warning', '#fab219'),
  serious: () => css('--serious', '#ec835a'),
  critical: () => css('--critical', '#d03b3b'),
};

// ------------------------------------------------------------------ format
const nf = (digits) => new Intl.NumberFormat('en-CA', {
  minimumFractionDigits: digits, maximumFractionDigits: digits,
});

export const fmt = {
  n: (v, d = 0) => (v === null || v === undefined || Number.isNaN(v) ? '—' : nf(d).format(v)),
  usd: (v, d = 0) => (v === null || v === undefined || Number.isNaN(v) ? '—' : `$${nf(d).format(v)}`),
  // Money on a stat tile, where four significant figures is plenty and the
  // extra six digits only make the number harder to read across a row.
  usdShort: (v) => {
    if (v === null || v === undefined || Number.isNaN(v)) return '—';
    const abs = Math.abs(v);
    if (abs >= 1e9) return `$${nf(2).format(v / 1e9)}B`;
    if (abs >= 1e6) return `$${nf(2).format(v / 1e6)}M`;
    if (abs >= 1e4) return `$${nf(0).format(v / 1e3)}k`;
    return `$${nf(0).format(v)}`;
  },
  pct: (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? '—' : `${nf(d).format(v * 100)}%`),
  signedPct: (v, d = 1) => {
    if (v === null || v === undefined || Number.isNaN(v)) return '—';
    const s = v > 0 ? '+' : '';
    return `${s}${nf(d).format(v * 100)}%`;
  },
  days: (v) => (v === null || v === undefined || Number.isNaN(v) ? '—' : `${nf(0).format(v)} d`),
  text: (v) => (v === null || v === undefined || v === '' ? '—' : String(v)),
};

// ------------------------------------------------------------------- fetch
const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

/**
 * Fetch JSON, waiting out a cold start.
 *
 * The demo builds its sample in a background thread, so the first visitor to
 * a cold container can arrive mid-build. The API answers 503 for that and 404
 * for a genuinely empty state; only the first is worth retrying, and retrying
 * it here means the page fills in by itself instead of showing an error that
 * goes away if you happen to reload.
 */
export async function get(path, params, { retries = 12, wait = 900 } = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
  });
  for (let attempt = 0; ; attempt += 1) {
    const response = await fetch(url, { headers: { Accept: 'application/json' } });
    if (response.ok) return response.json();
    let body = {};
    try { body = await response.json(); } catch { /* not JSON */ }
    if (response.status === 503 && body.loading && attempt < retries) {
      await sleep(wait);
      continue;
    }
    throw new Error(body.error || `${response.status}`);
  }
}

/** Run several loaders; a failure in one leaves the others alone. */
export function load(tasks) {
  return Promise.all(tasks.map((task) => Promise.resolve().then(task).catch((error) => {
    console.error(error);
    return null;
  })));
}

export function fail(elementId, message) {
  const el = document.getElementById(elementId);
  if (el) el.innerHTML = `<div class="chart-empty">${message}</div>`;
}

// ------------------------------------------------------------------ charts
function layout(extra = {}) {
  const ink = css('--ink', '#0b0b0b');
  const muted = css('--ink-muted', '#898781');
  const grid = css('--grid', '#e1e0d9');
  const axis = css('--axis', '#c3c2b7');
  const base = {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', size: 12, color: muted },
    margin: { l: 56, r: 16, t: 12, b: 40 },
    hoverlabel: {
      bgcolor: css('--surface-raised', '#fff'),
      bordercolor: axis,
      font: { color: ink, size: 12.5 },
    },
    // One crosshair for the whole x position, not one tooltip per trace: a
    // reader comparing two lines wants both numbers at the week they hovered.
    hovermode: 'closest',
    showlegend: false,
    xaxis: {
      gridcolor: 'rgba(0,0,0,0)', linecolor: axis, zeroline: false,
      tickfont: { color: muted, size: 11.5 }, automargin: true,
    },
    yaxis: {
      gridcolor: grid, linecolor: 'rgba(0,0,0,0)', zeroline: false,
      tickfont: { color: muted, size: 11.5 }, automargin: true,
    },
    legend: {
      orientation: 'h', y: 1.14, x: 0, font: { color: css('--ink-secondary', '#52514e'), size: 12 },
      bgcolor: 'rgba(0,0,0,0)',
    },
  };
  return deepMerge(base, extra);
}

function deepMerge(a, b) {
  const out = { ...a };
  Object.entries(b || {}).forEach(([key, value]) => {
    out[key] = value && typeof value === 'object' && !Array.isArray(value)
      ? deepMerge(a[key] || {}, value)
      : value;
  });
  return out;
}

const CONFIG = { displayModeBar: false, responsive: true };

/** Give a Plotly surface a useful non-visual name before the SVG is drawn. */
export function prepareChart(elementId, label = '') {
  const el = document.getElementById(elementId);
  if (!el) return null;
  const card = el.closest('.card');
  const heading = card?.querySelector('h2')?.textContent.trim();
  const description = card?.querySelector('.sub')?.textContent.trim();
  const fallback = elementId.replace(/[-_]+/g, ' ');
  const name = [label || heading || fallback, description]
    .filter(Boolean).join('. ').replace(/\s+/g, ' ');
  el.setAttribute('role', 'img');
  el.setAttribute('aria-label', `${name}. Interactive chart; the surrounding text explains the measure.`);
  return el;
}

export function draw(elementId, traces, extra) {
  const el = prepareChart(elementId);
  if (!el) return;
  if (!traces || !traces.length) {
    el.innerHTML = '<div class="chart-empty">No data for this selection.</div>';
    return;
  }
  el.innerHTML = '';
  window.Plotly.newPlot(el, traces, layout(extra), CONFIG);
}

/** Horizontal bars, biggest at the top - the default for ranked categories. */
export function barH(elementId, labels, values, opts = {}) {
  const colors = opts.colors || palette()[0];
  draw(elementId, [{
    type: 'bar', orientation: 'h',
    x: values, y: labels,
    marker: { color: colors, line: { width: 0 } },
    hovertemplate: `%{y}<br>${opts.prefix || ''}%{x:,.${opts.decimals ?? 0}f}${opts.suffix || ''}<extra></extra>`,
  }], deepMerge({
    margin: { l: opts.leftMargin || 150, r: 22, t: 8, b: 34 },
    yaxis: { autorange: 'reversed', ticksuffix: '  ' },
    bargap: 0.34,
  }, opts.layout || {}));
}

export function barV(elementId, labels, values, opts = {}) {
  draw(elementId, [{
    type: 'bar',
    x: labels, y: values,
    marker: { color: opts.colors || palette()[0], line: { width: 0 } },
    hovertemplate: `%{x}<br>${opts.prefix || ''}%{y:,.${opts.decimals ?? 0}f}${opts.suffix || ''}<extra></extra>`,
  }], deepMerge({ bargap: 0.32 }, opts.layout || {}));
}

export function lines(elementId, series, opts = {}) {
  const colors = palette();
  const traces = series.map((s, i) => ({
    type: 'scatter',
    mode: s.mode || 'lines',
    name: s.name,
    x: s.x,
    y: s.y,
    line: { color: s.color || colors[i % colors.length], width: 2, dash: s.dash || 'solid',
            shape: s.shape || 'linear' },
    marker: { size: 8 },
    connectgaps: false,
    fill: s.fill,
    fillcolor: s.fillcolor,
    hovertemplate: `%{x}<br>${s.name}: ${opts.prefix || ''}%{y:,.${opts.decimals ?? 0}f}${opts.suffix || ''}<extra></extra>`,
  }));
  draw(elementId, traces, deepMerge({
    showlegend: series.length > 1,
    hovermode: 'x unified',
  }, opts.layout || {}));
}

export function donut(elementId, labels, values, opts = {}) {
  const colors = opts.colors || palette();
  draw(elementId, [{
    type: 'pie', hole: 0.62,
    labels, values,
    marker: { colors, line: { color: css('--surface', '#fff'), width: 2 } },
    textinfo: 'label+percent',
    textposition: 'outside',
    automargin: true,
    hovertemplate: `%{label}<br>${opts.prefix || ''}%{value:,.0f}${opts.suffix || ''} (%{percent})<extra></extra>`,
  }], deepMerge({ margin: { l: 10, r: 10, t: 18, b: 18 } }, opts.layout || {}));
}

/**
 * A waterfall built from bar segments rather than Plotly's waterfall trace.
 *
 * The built-in trace decides the sign of each step from its own running total,
 * which is right until a step is a restated total rather than a movement - and
 * an inventory bridge has two of those, one at each end.
 */
export function waterfall(elementId, steps, opts = {}) {
  const good = status.good();
  const bad = status.critical();
  const neutral = palette()[0];
  const base = steps.map((s) => (s.Kind === 'Total' ? 0 : Math.min(s.RunningStart, s.RunningEnd)));
  const size = steps.map((s) => (s.Kind === 'Total'
    ? s.ValueUSD
    : Math.abs(s.RunningEnd - s.RunningStart)));
  const colors = steps.map((s) => (s.Kind === 'Total' ? neutral : (s.ValueUSD >= 0 ? good : bad)));

  draw(elementId, [
    { type: 'bar', x: steps.map((s) => s.Step), y: base, marker: { color: 'rgba(0,0,0,0)' },
      hoverinfo: 'skip', showlegend: false },
    { type: 'bar', x: steps.map((s) => s.Step), y: size,
      marker: { color: colors, line: { width: 0 } },
      customdata: steps.map((s) => s.ValueUSD),
      hovertemplate: '%{x}<br>$%{customdata:,.0f}<extra></extra>' },
  ], deepMerge({
    barmode: 'stack',
    bargap: 0.4,
    xaxis: { tickangle: -32 },
    margin: { l: 66, r: 16, t: 12, b: 96 },
  }, opts.layout || {}));
}

// ------------------------------------------------------------------ tables
/**
 * Render a table from a column spec.
 *
 * Columns are `{key, label, fmt, cls, chip}`; `chip` maps a value to a status
 * class so state is carried by a labelled pill rather than by a row colour,
 * which is invisible to a third of readers and to a printer.
 */
export function table(elementId, columns, records, opts = {}) {
  const el = document.getElementById(elementId);
  if (!el) return;
  if (!records || !records.length) {
    el.innerHTML = `<div class="chart-empty">${opts.empty || 'Nothing to show.'}</div>`;
    return;
  }
  const head = columns.map((c) => `<th scope="col" class="${c.num ? 'num' : ''}">${c.label}</th>`).join('');
  const body = records.map((row) => {
    const cells = columns.map((c) => {
      const raw = row[c.key];
      const text = c.fmt ? c.fmt(raw, row) : fmt.text(raw);
      if (c.chip) {
        const kind = c.chip(raw, row);
        return `<td><span class="chip ${kind}">${text}</span></td>`;
      }
      return `<td class="${c.num ? 'num' : ''} ${c.cls || ''}">${text}</td>`;
    }).join('');
    return `<tr>${cells}</tr>`;
  }).join('');
  const cardHeading = el.closest('.card')?.querySelector('h2')?.textContent.trim();
  const regionLabel = `${cardHeading || 'Results'} table; scroll for additional columns`;
  el.innerHTML = `<div class="table-wrap" tabindex="0" role="region" aria-label="${regionLabel}"><table class="data"><thead><tr>${head}</tr></thead>`
    + `<tbody>${body}</tbody></table></div>`
    + (opts.footnote ? `<div class="note-inline">${opts.footnote}</div>` : '');
}

export function kpis(elementId, tiles) {
  const el = document.getElementById(elementId);
  if (!el) return;
  el.innerHTML = tiles.map((t) => `
    <div class="kpi ${t.tone ? `is-${t.tone}` : ''}">
      <span class="label">${t.label}</span>
      <span class="value">${t.value}</span>
      ${t.note ? `<span class="note">${t.note}</span>` : ''}
    </div>`).join('');
}

export function options(select, values, { all = 'All', selected = '' } = {}) {
  if (!select) return;
  const current = selected || select.value;
  select.innerHTML = `<option value="">${all}</option>`
    + values.map((v) => `<option value="${v}">${v}</option>`).join('');
  if (current) select.value = current;
}

// ------------------------------------------------------------------- theme
export function initTheme() {
  const stored = localStorage.getItem('invapp-theme');
  if (stored) document.documentElement.setAttribute('data-theme', stored);
  const button = document.getElementById('theme-toggle');
  if (!button) return;
  const paint = () => {
    const dark = document.documentElement.getAttribute('data-theme') === 'dark'
      || (!document.documentElement.getAttribute('data-theme')
          && window.matchMedia('(prefers-color-scheme: dark)').matches);
    button.textContent = dark ? 'Light' : 'Dark';
    button.setAttribute('aria-pressed', String(dark));
    button.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
  };
  paint();
  button.addEventListener('click', () => {
    const dark = document.documentElement.getAttribute('data-theme') === 'dark'
      || (!document.documentElement.getAttribute('data-theme')
          && window.matchMedia('(prefers-color-scheme: dark)').matches);
    const next = dark ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('invapp-theme', next); } catch { /* private mode */ }
    paint();
    // Charts bake their colours in at draw time, so a theme change has to
    // redraw them. Reloading is blunt but correct; re-theming every open plot
    // in place is a lot of surface area for a button nobody presses twice.
    window.dispatchEvent(new CustomEvent('invapp:theme'));
  });
}

/**
 * Wire a page up: theme, first render, and a redraw when the theme changes.
 *
 * Re-running the whole render on a theme change re-fetches the JSON as well as
 * repainting. That is a few kilobytes for a button nobody presses twice, and
 * the alternative - keeping every page's last payload and re-theming each open
 * plot in place - is a cache to invalidate on six pages.
 */
export function boot(render) {
  initTheme();
  const run = () => Promise.resolve().then(render).catch((error) => console.error(error));
  run();
  window.addEventListener('invapp:theme', run);
}
