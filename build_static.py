#!/usr/bin/env python3
"""Render the demo to a static site that needs no server.

The hosted app runs on a free Render instance that spins down when idle, and a
spun-down container takes about a minute to wake before any of our code runs.
That delay cannot be optimised away, only avoided. The demo shows one fixed,
seeded snapshot, so every number a page can display is known before anyone
visits. This build walks the real Flask app once in a real browser, saves each
page after its JavaScript has drawn it, and saves every JSON payload the page
asked for. The result is a `dist/` tree that GitHub Pages can serve.

What a visitor gets:

* The first paint already has the numbers, tables and charts in it, because
  the HTML is the page as the browser left it.
* Hover still works. The page's own scripts run again, read the saved JSON
  through a small `fetch` shim, and let Plotly redraw each chart over its
  frozen copy.
* Each filter works one change at a time from the default view, and the
  policy lab has a grid of saved scenarios. Anything else (combined filters,
  uploads, parameter edits) says so and links to the full app.

    python build_static.py --out dist
"""

from __future__ import annotations

import argparse
import hashlib
import io
import itertools
import json
import logging
import os
import re
import shutil
import sys
import tarfile
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

LIVE_URL = os.environ.get("STATIC_LIVE_URL", "https://inventory-analytics-app.onrender.com")

# The app loads the full Plotly bundle from cdn.plot.ly. The static copy uses
# the basic bundle instead: a third of the size, and it holds every trace type
# these pages draw (bar, scatter and pie). The build renders with the same
# bundle it ships, and fails if a chart asks for any other trace type, so a new
# heatmap cannot quietly publish as an empty box.
PLOTLY_VERSION = "2.35.2"
PLOTLY_TARBALL = (
    "https://registry.npmjs.org/plotly.js-basic-dist-min/-/"
    f"plotly.js-basic-dist-min-{PLOTLY_VERSION}.tgz"
)
PLOTLY_SHA256 = "138c2e81014b979dc00867a93da55b7605a17495ee78dd7afb433b7f021dfcfa"
PLOTLY_FILE = f"static/vendor/plotly-basic-{PLOTLY_VERSION}.min.js"
BASIC_TRACE_TYPES = {"bar", "scatter", "pie"}
PLOTLY_CDN_RE = re.compile(r'<script src="https://cdn\.plot\.ly/[^"]+"[^>]*></script>')


@dataclass(frozen=True)
class Page:
    endpoint: str
    route: str
    out: str


# Every dashboard route. `check_pages_cover_app` fails the build if the app
# gains a page that is not listed here, so a new page cannot go unbuilt.
PAGES: tuple[Page, ...] = (
    Page("dashboard.overview", "/", "index.html"),
    Page("dashboard.demand", "/demand", "demand/index.html"),
    Page("dashboard.replenishment", "/replenishment", "replenishment/index.html"),
    Page("dashboard.sku_health", "/sku-health", "sku-health/index.html"),
    Page("dashboard.network", "/network", "network/index.html"),
    Page("dashboard.accuracy", "/accuracy", "accuracy/index.html"),
)

# A select with more options than this is not walked. The demand page's SKU
# picker has 420; saving a forecast for each would triple the site for a
# control most visitors never open.
MAX_OPTIONS = 40

# The policy lab takes free-form numbers. This grid covers the moves a
# reviewer is likely to try: the seven service targets on the frontier chart,
# demand and lead time in steps of 10%, and the 25% demand and 30% lead-time
# shocks that UAT scenario 11 in the interview guide asks for.
SCENARIO_GRID = {
    "service_level": ("0.85", "0.9", "0.93", "0.95", "0.97", "0.98", "0.99"),
    "demand_multiplier": ("0.8", "0.9", "1", "1.1", "1.2", "1.25"),
    "lead_time_multiplier": ("1", "1.1", "1.2", "1.3"),
}

NUMBER_RE = re.compile(r"^-?\d*\.?\d+$")


# ─────────────────────────────────────────────────────────────────────────────
# Keys: one string per request, computed the same way here and in the shim
# ─────────────────────────────────────────────────────────────────────────────
def _canonical_value(value: str) -> str:
    """Write a number the way JavaScript's `String(Number(v))` would.

    Form inputs send what the visitor typed, so "1.00", "1.0" and "1" must all
    find the same saved answer.
    """
    if not NUMBER_RE.match(value):
        return value
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return repr(number)


def canonical_key(url: str) -> str:
    """`/api/path?a=1&b=2`: empty values dropped, numbers normalised, pairs sorted."""
    parts = urllib.parse.urlsplit(url)
    pairs = sorted(
        (k, _canonical_value(v))
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if v != ""
    )
    query = "&".join(f"{_quote(k)}={_quote(v)}" for k, v in pairs)
    return parts.path + (f"?{query}" if query else "")


def _quote(value: str) -> str:
    """Percent-encode exactly as JavaScript's `encodeURIComponent` does.

    Encoding keeps the key a valid URL, so a value holding "&" (the
    "Health & Beauty" department) cannot be mistaken for two parameters.
    """
    return urllib.parse.quote(value, safe="!'()*")


def key_file(key: str, suffix: str = ".json") -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    if key.startswith("/api/download/"):
        name = key.split("?", 1)[0].rsplit("/", 1)[-1]
        return f"downloads/{digest}-{name}"
    return f"api/{digest}{suffix}"


# ─────────────────────────────────────────────────────────────────────────────
# The runtime that ships in every page
# ─────────────────────────────────────────────────────────────────────────────
# Runs before anything else in <head>. `fetch` is the only way the pages talk
# to the server (see `get` in core.js), so replacing it is enough to make them
# read saved files. The key rule matches `canonical_key` above.
SHIM_JS = r"""
(function () {
  var S = window.__INV_STATIC__;
  var realFetch = window.fetch.bind(window);
  var num = /^-?\d*\.?\d+$/;
  function key(input) {
    var url = new URL(input, location.href);
    var at = url.pathname.indexOf('/api/');
    if (at < 0) return null;
    var pairs = [];
    url.searchParams.forEach(function (v, k) {
      if (v !== '') pairs.push([k, num.test(v) ? String(Number(v)) : v]);
    });
    pairs.sort(function (a, b) {
      return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0);
    });
    var q = pairs.map(function (p) {
      return encodeURIComponent(p[0]) + '=' + encodeURIComponent(p[1]);
    }).join('&');
    return url.pathname.slice(at) + (q ? '?' + q : '');
  }
  function json(status, body) {
    return new Response(JSON.stringify(body), {
      status: status, headers: { 'Content-Type': 'application/json' },
    });
  }
  function notice() {
    var el = document.getElementById('static-miss');
    if (!el) {
      el = document.createElement('div');
      el.id = 'static-miss';
      el.className = 'static-toast';
      el.setAttribute('role', 'status');
      document.body.appendChild(el);
    }
    el.innerHTML = 'This static copy saves each filter one at a time, so this '
      + 'combination was not saved. Part of the page still shows the previous view. '
      + '<a href="' + S.live + S.route + '" target="_blank" rel="noopener">Open the full app</a>'
      + ' <button type="button" aria-label="Dismiss">&times;</button>';
    el.querySelector('button').onclick = function () { el.remove(); };
  }
  window.fetch = function (input, init) {
    var url = input && input.url ? input.url : String(input);
    var k = key(url);
    if (k === null) return realFetch(input, init);
    var method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    if (method !== 'GET') {
      return Promise.resolve(json(405, {
        error: 'The static copy cannot change the data. Use the full app to upload a '
          + 'workbook or edit parameters.',
      }));
    }
    var file = S.api[k];
    if (!file) {
      notice();
      return Promise.resolve(json(404, {
        error: 'Not saved in the static copy. Try the full app.',
      }));
    }
    return realFetch(S.root + file);
  };
  // Download links are plain anchors, and replenishment.js rewrites its own
  // to carry the filters, so they are resolved when clicked.
  document.addEventListener('click', function (event) {
    var a = event.target.closest && event.target.closest('a[href*="/api/download/"]');
    if (!a) return;
    var k = key(a.getAttribute('href'));
    event.preventDefault();
    if (S.api[k]) location.href = S.root + S.api[k];
    else window.open(S.live + k, '_blank', 'noopener');
  });
})();
""".strip()

# Plotly draws into the chart's own element, and these elements arrive holding
# the frozen SVG. Most redraws clear the element first (`draw` in core.js); the
# ABC chart calls `Plotly.newPlot` directly, so clearing is done here for all.
UNFREEZE_JS = """
if (window.Plotly) {
  const newPlot = window.Plotly.newPlot;
  window.Plotly.newPlot = function (target, ...rest) {
    const el = typeof target === 'string' ? document.getElementById(target) : target;
    if (el && el.hasAttribute('data-frozen')) {
      el.removeAttribute('data-frozen');
      el.innerHTML = '';
    }
    return newPlot.call(this, el, ...rest);
  };
}
""".strip()

STATIC_CSS = """
.chart[data-frozen]{overflow:hidden}
.static-toast{position:fixed;left:16px;right:16px;bottom:16px;z-index:50;max-width:36rem;
margin:0 auto;padding:10px 14px;border:1px solid var(--border);
border-left:3px solid var(--series-4);border-radius:var(--radius-sm);
background:var(--surface-raised);color:var(--ink-secondary);font-size:13px;
box-shadow:0 6px 24px rgba(0,0,0,.18)}
.static-toast button{margin-left:8px;border:0;background:none;color:inherit;
font-size:16px;cursor:pointer}
.static-note{font-size:12px;color:var(--ink-secondary);margin:4px 0 0}
""".strip()

STATIC_BANNER = (
    '<div class="banner" role="note"><strong>Static copy.</strong> '
    "This is the app with its data saved at build time, so it opens at once. "
    "Charts keep their hover, and each filter works one change at a time. "
    "Uploads and parameter edits need the "
    '<a href="{live}{route}" target="_blank" rel="noopener">full app</a>, '
    "which can take about a minute to wake.</div>"
)

UPLOAD_HINT_RE = re.compile(r"\s*Upload\s+your\s+own\s+workbook\b.*?</div>", re.S)

# Controls the static copy cannot fully serve. Each gets one plain sentence
# under it, so a visitor knows before pressing the button.
FORM_NOTES = {
    "upload-form": "Uploads need the full app. The static copy shows the generated sample.",
    "param-form": "Parameter edits need the full app. The values shown are the ones in use.",
    "scenario-form": (
        "Saved in the static copy: the seven service targets on the frontier chart; "
        "demand multipliers 0.8, 0.9, 1.0, 1.1, 1.2 and 1.25; lead-time multipliers "
        "1.0, 1.1, 1.2 and 1.3. Other values need the full app."
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# HTML rewriting (pure functions, tested in tests/test_static_build.py)
# ─────────────────────────────────────────────────────────────────────────────
def page_root(out: str) -> str:
    """The relative path from a page back to the site root."""
    depth = out.count("/")
    return "../" * depth if depth else "./"


def rewrite_html(html: str, page: Page, api: dict[str, str], live: str = LIVE_URL) -> str:
    root = page_root(page.out)
    routes = {p.route: p.out.removesuffix("index.html") for p in PAGES}

    def local(match: re.Match) -> str:
        attr, path = match.group(1), match.group(2)
        if path.startswith("/api/download/"):
            key = canonical_key(path)
            target = root + api[key] if key in api else live + path
            return f'{attr}="{target}"'
        if path.startswith("/static/"):
            return f'{attr}="{root}{path[1:]}"'
        if path in routes:
            return f'{attr}="{root}{routes[path]}"'
        return match.group(0)

    html = re.sub(r'(href|src)="(/[^"]*)"', local, html)
    html = re.sub(r'from "/static/', f'from "{root}static/', html)

    settings = json.dumps(
        {"root": root, "live": live, "route": page.route, "api": api}, separators=(",", ":")
    ).replace("</", "<\\/")
    head = (
        f"<script>window.__INV_STATIC__={settings};</script>\n"
        f"<script>{SHIM_JS}</script>\n<style>{STATIC_CSS}</style>"
    )
    html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n' + head, 1)

    plotly = (
        f'<script defer src="{root}{PLOTLY_FILE}"></script>\n'
        f'<script type="module">{UNFREEZE_JS}</script>'
    )
    html, count = PLOTLY_CDN_RE.subn(plotly, html)
    if count != 1:
        raise RuntimeError(f"{page.route}: expected one Plotly script tag, found {count}")

    # The generated-data banner tells visitors to upload a workbook, which this
    # copy cannot take. The static banner above it says where uploads go.
    html, count = UPLOAD_HINT_RE.subn("</div>", html)
    if count != 1:
        raise RuntimeError(f"{page.route}: expected the upload hint once, found {count}")

    banner = STATIC_BANNER.format(live=live, route=page.route)
    html = re.sub(r"(<main[^>]*>)", r"\1" + banner.replace("\\", "\\\\"), html, count=1)

    for form_id, text in FORM_NOTES.items():
        html = re.sub(
            rf'(<form id="{form_id}"[^>]*>.*?</form>)',
            lambda m, t=text: f'{m.group(1)}<p class="static-note">{t}</p>',
            html,
            count=1,
            flags=re.S,
        )
    return html


# ─────────────────────────────────────────────────────────────────────────────
# Build
# ─────────────────────────────────────────────────────────────────────────────
def fetch_plotly(cache: Path) -> bytes:
    """The pinned basic bundle, from the npm registry, checked by hash."""
    cached = cache / f"plotly-basic-{PLOTLY_VERSION}.min.js"
    if cached.exists():
        data = cached.read_bytes()
    else:
        with urllib.request.urlopen(PLOTLY_TARBALL, timeout=60) as response:
            tarball = response.read()
        with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
            data = tar.extractfile("package/plotly-basic.min.js").read()
        cache.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    if digest != PLOTLY_SHA256:
        raise RuntimeError(f"plotly-basic hash mismatch: {digest}")
    return data


def check_pages_cover_app(app) -> None:
    endpoints = {
        r.endpoint for r in app.url_map.iter_rules() if r.endpoint.startswith("dashboard.")
    }
    missing = endpoints - {p.endpoint for p in PAGES}
    if missing:
        raise RuntimeError(f"dashboard pages not in PAGES: {sorted(missing)}")


def start_server(app):
    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def wait_for_data(base: str, timeout: float = 120) -> None:
    """The sample loads in a background thread; the API says 503 until then."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/api/overview", timeout=10) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("the demo data did not load")


class Recorder:
    """Every GET under /api/ a page makes, keyed as the shim will look it up."""

    def __init__(self, base: str):
        self.base = base
        self.bodies: dict[str, bytes] = {}
        self.inflight = 0
        self.pending: list = []

    def attach(self, page) -> None:
        def is_api(request) -> bool:
            return request.method == "GET" and "/api/" in request.url

        def started(request):
            if is_api(request):
                self.inflight += 1

        def ended(request):
            if is_api(request):
                self.inflight -= 1

        def responded(response):
            if is_api(response.request) and response.status == 200:
                self.pending.append(response)

        page.on("request", started)
        page.on("requestfinished", ended)
        page.on("requestfailed", ended)
        page.on("response", responded)

    def settle(self, page, quiet_ms: int = 400, timeout_ms: int = 30000) -> None:
        waited = quiet = 0
        while waited < timeout_ms:
            page.wait_for_timeout(100)
            waited += 100
            quiet = quiet + 100 if self.inflight == 0 else 0
            if quiet >= quiet_ms:
                break
        for response in self.pending:
            key = canonical_key(response.url.split(self.base, 1)[-1])
            self.bodies.setdefault(key, response.body())
        self.pending.clear()

    def get(self, key: str) -> None:
        """Save a URL the browser did not ask for (scenario grid, downloads)."""
        if key in self.bodies:
            return
        with urllib.request.urlopen(self.base + key, timeout=120) as response:
            self.bodies[key] = response.read()


def wait_for_charts(page) -> None:
    """Until every chart slot holds a drawn plot or an empty-state message."""
    page.wait_for_function(
        """() => [...document.querySelectorAll('.chart')].every(el =>
             el.querySelector('.main-svg') || el.querySelector('.chart-empty'))
           && !document.querySelector('.loading')""",
        timeout=60000,
    )


def freeze(page) -> tuple[str, list[str]]:
    """The drawn page as HTML, and the trace types its charts used."""
    return page.evaluate(
        """() => {
          const types = [];
          document.querySelectorAll('.js-plotly-plot').forEach(el => {
            (el.data || []).forEach(t => types.push(t.type || 'scatter'));
            el.setAttribute('data-frozen', '');
          });
          /* The page scripts mark a control `data-wired` once its listener is
             attached, so a re-render does not attach a second one. Saved into
             the HTML, the mark tells the scripts on the static page that the
             work is done, and every filter and form is left dead. */
          const wired = [...document.querySelectorAll('[data-wired]')];
          wired.forEach(el => el.removeAttribute('data-wired'));
          const html = '<!doctype html>\\n' + document.documentElement.outerHTML;
          wired.forEach(el => el.setAttribute('data-wired', '1'));
          document.querySelectorAll('.js-plotly-plot')
            .forEach(el => el.removeAttribute('data-frozen'));
          return [html, types];
        }"""
    )


def walk_controls(page, recorder: Recorder) -> int:
    """Change each select and checkbox once from the default, and put it back."""
    controls = page.evaluate(
        f"""() => [...document.querySelectorAll('main select, main input[type=checkbox]')]
             .filter(el => el.id && !el.closest('form'))
             .filter(el => el.tagName !== 'SELECT' || el.options.length <= {MAX_OPTIONS})
             .map(el => ({{id: el.id, tag: el.tagName,
                          values: el.tagName === 'SELECT' ? [...el.options].map(o => o.value) : [],
                          current: el.tagName === 'SELECT' ? el.value : el.checked}}))"""
    )
    changes = 0
    for control in controls:
        selector = f"#{control['id']}"
        if control["tag"] == "SELECT":
            for value in control["values"]:
                if value == control["current"]:
                    continue
                page.select_option(selector, value)
                recorder.settle(page)
                changes += 1
            page.select_option(selector, control["current"])
        else:
            page.click(selector)
            recorder.settle(page)
            page.click(selector)
            changes += 1
        recorder.settle(page)
    return changes


def download_links(page) -> list[str]:
    return page.evaluate(
        """() => [...document.querySelectorAll('a[href*="/api/download/"]')]
             .map(a => new URL(a.href).pathname + new URL(a.href).search)"""
    )


def build(out: Path, cache: Path) -> dict:
    os.environ.setdefault("DEMO_AUTOLOAD", "1")
    # Run history is whatever this machine has processed before. It is not part
    # of the seeded snapshot, so the build starts from an empty history.
    os.environ["INVAPP_DB_PATH"] = str(cache / "build-runs.db")
    (cache / "build-runs.db").unlink(missing_ok=True)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    from playwright.sync_api import sync_playwright

    from invapp import app

    check_pages_cover_app(app)
    cache.mkdir(parents=True, exist_ok=True)
    plotly = fetch_plotly(cache)
    server, base = start_server(app)
    wait_for_data(base)

    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "invapp" / "static", out / "static")
    (out / PLOTLY_FILE).parent.mkdir(parents=True, exist_ok=True)
    (out / PLOTLY_FILE).write_bytes(plotly)

    stats = {"pages": 0, "payloads": 0, "downloads": 0, "control_changes": 0, "charts": 0}
    frozen: dict[str, tuple[str, set[str]]] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for spec in PAGES:
            context = browser.new_context(
                viewport={"width": 1280, "height": 900}, color_scheme="light"
            )
            page = context.new_page()
            # Render with the bundle that ships, not the CDN's full one.
            page.route(
                "https://cdn.plot.ly/**",
                lambda route: route.fulfill(body=plotly, content_type="application/javascript"),
            )
            recorder = Recorder(base)
            recorder.attach(page)
            page.goto(base + spec.route, wait_until="load")
            wait_for_charts(page)
            recorder.settle(page)

            html, types = freeze(page)
            unsupported = set(types) - BASIC_TRACE_TYPES
            if unsupported:
                raise RuntimeError(f"{spec.route}: trace types not in plotly-basic: {unsupported}")
            stats["charts"] += html.count('data-frozen=""')

            links = set(download_links(page))
            stats["control_changes"] += walk_controls(page, recorder)
            links |= set(download_links(page))
            if spec.route == "/replenishment":
                for values in itertools.product(*SCENARIO_GRID.values()):
                    params = dict(zip(SCENARIO_GRID, values), limit="25")
                    recorder.get(
                        canonical_key(
                            "/api/replenishment/policy_scenario?" + urllib.parse.urlencode(params)
                        )
                    )
            for link in links:
                recorder.get(canonical_key(link))

            api = {}
            for key, body in recorder.bodies.items():
                api[key] = key_file(key)
                target = out / api[key]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
            frozen[spec.out] = (html, api)
            context.close()
            print(f"  {spec.route:<16} {len(api):>4} saved responses", flush=True)
        browser.close()
    server.shutdown()

    index: dict[str, str] = {}
    for spec in PAGES:
        html, api = frozen[spec.out]
        target = out / spec.out
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rewrite_html(html, spec, api), encoding="utf-8")
        index.update(api)
        stats["pages"] += 1
    stats["payloads"] = sum(1 for k in index if not k.startswith("/api/download/"))
    stats["downloads"] = sum(1 for k in index if k.startswith("/api/download/"))
    # The test suite reads this to compare every saved payload with the app.
    (out / "api" / "index.json").write_text(json.dumps(index, indent=1, sort_keys=True))
    (out / ".nojekyll").write_text("")
    (out / "build-info.json").write_text(
        json.dumps(
            {"commit": os.environ.get("GITHUB_SHA", ""), "live": LIVE_URL, **stats}, indent=1
        )
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default="dist", type=Path)
    parser.add_argument("--cache", default=".cache", type=Path)
    args = parser.parse_args()
    started = time.perf_counter()
    stats = build(args.out.resolve(), args.cache.resolve())
    print(
        f"built {stats['pages']} pages, {stats['charts']} charts, {stats['payloads']} "
        f"payloads, {stats['downloads']} downloads, {stats['control_changes']} control "
        f"changes in {time.perf_counter() - started:.0f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
