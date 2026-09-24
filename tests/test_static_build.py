"""
The static copy shows what the app shows.

`build_static.py` saves each page after the browser has drawn it, plus every
JSON payload the page asked for. A reviewer lands on that copy, not on the
Flask app, so a figure that drifted between the two would be read as the
app's answer. Three layers of check:

* The key and HTML-rewriting helpers, which always run.
* Every stat tile on every static page against the same figure recomputed here
  from the app's own API, and every saved payload against the live answer.
  These need a build, so they run when ``STATIC_DIST`` points at one (the
  static-site workflow sets it) and skip otherwise.
* A browser pass over the built site: hover works, nothing is fetched from
  another host, a saved filter works and an unsaved one says so. Needs a build
  and Playwright.
"""

from __future__ import annotations

import functools
import html
import json
import os
import re
import threading
from decimal import ROUND_HALF_UP, Decimal
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import build_static
from build_static import PAGES, Page, canonical_key, key_file, page_root, rewrite_html

DIST = Path(os.environ["STATIC_DIST"]).resolve() if os.environ.get("STATIC_DIST") else None


# --------------------------------------------------------------------------
# Helpers that always run
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("url", "key"),
    [
        ("/api/overview", "/api/overview"),
        ("/api/demand/skus?limit=5000", "/api/demand/skus?limit=5000"),
        # Sorted, empties dropped: the JS drops empty filters before asking.
        (
            "/api/replenishment/plan?due=1&NodeID=&ABCClass=A",
            "/api/replenishment/plan?ABCClass=A&due=1",
        ),
        # Typed numbers: "1.00" from a form input must find the answer saved as "1".
        (
            "/api/replenishment/policy_scenario?service_level=0.950&demand_multiplier=1.00&limit=25",
            "/api/replenishment/policy_scenario?demand_multiplier=1&limit=25&service_level=0.95",
        ),
        # Encoded as encodeURIComponent would, so "&" inside a value stays a value.
        (
            "/api/actions/register?Department=Home+%26+Kitchen&limit=200",
            "/api/actions/register?Department=Home%20%26%20Kitchen&limit=200",
        ),
    ],
)
def test_canonical_key(url, key):
    assert canonical_key(url) == key


def test_key_files_are_stable_and_downloads_keep_their_name():
    assert key_file("/api/overview") == key_file("/api/overview")
    assert key_file("/api/overview").startswith("api/")
    assert key_file("/api/download/actions.csv").endswith("-actions.csv")


def test_page_root():
    assert page_root("index.html") == "./"
    assert page_root("demand/index.html") == "../"


def test_pages_cover_every_dashboard_route():
    from invapp import app

    build_static.check_pages_cover_app(app)
    rules = {r.rule for r in app.url_map.iter_rules() if r.endpoint.startswith("dashboard.")}
    assert rules == {p.route for p in PAGES}


def test_rewrite_makes_every_link_local():
    page = Page("dashboard.demand", "/demand", "demand/index.html")
    source = (
        '<html><head><meta charset="utf-8">'
        '<link rel="stylesheet" href="/static/css/styles.css">'
        '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>'
        '</head><body><a href="/">Overview</a><a href="/sku-health">SKU</a>'
        '<a class="btn" href="/api/download/sku_health.csv">CSV</a>'
        '<main id="main-content"><div class="banner">Generated data. Upload your own workbook'
        " on the Overview page to replace it.</div>"
        '<form id="upload-form"><input></form></main>'
        '<script type="module">import { get } from "/static/js/core.js";</script></body></html>'
    )
    api = {"/api/download/sku_health.csv": "downloads/abc-sku_health.csv"}
    out = rewrite_html(source, page, api, live="https://live.example")

    assert 'href="../static/css/styles.css"' in out
    assert 'href="../"' in out and 'href="../sku-health/"' in out
    assert 'href="../downloads/abc-sku_health.csv"' in out
    assert 'from "../static/js/core.js"' in out
    assert "cdn.plot.ly" not in out
    assert f'<script defer src="../{build_static.PLOTLY_FILE}">' in out
    assert "Static copy." in out and 'href="https://live.example/demand"' in out
    assert "Uploads need the full app." in out
    assert "Upload your own workbook" not in out
    assert not re.search(r'(href|src)="/(static|api)/', out)


# --------------------------------------------------------------------------
# The built site against the app
# --------------------------------------------------------------------------
needs_build = pytest.mark.skipif(DIST is None, reason="set STATIC_DIST to a build_static.py output")


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    """The app's answer to any GET, with the sample loaded exactly as the demo does."""
    if DIST is None:
        pytest.skip("set STATIC_DIST to a build_static.py output")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DEMO_AUTOLOAD", "")
        import invapp
        from invapp.services import state, store
        from invapp.services.bootstrap import load_sample_data

        mp.setattr(store, "DB_PATH", str(tmp_path_factory.mktemp("runs") / "runs.db"))
        state.reset_state()
        app = invapp.create_app()
        app.config.update(TESTING=True)
        with app.app_context():
            assert load_sample_data()
        client = app.test_client()

        @functools.cache
        def get(url: str):
            response = client.get(url)
            assert response.status_code == 200, url
            return response.get_json() if response.is_json else response.get_data()

        yield get
        state.reset_state()


# Number formatting, as `fmt` in invapp/static/js/core.js does it. Intl rounds
# the exact value of the double half away from zero, which is what Decimal's
# ROUND_HALF_UP does to Decimal(float).
def _num(value, digits=0):
    if value is None:
        return "—"
    rounded = Decimal(value).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    return f"{rounded:,.{digits}f}"


def _usd_short(value):
    if value is None:
        return "—"
    size = abs(value)
    if size >= 1e9:
        return f"${_num(value / 1e9, 2)}B"
    if size >= 1e6:
        return f"${_num(value / 1e6, 2)}M"
    if size >= 1e4:
        return f"${_num(value / 1e3, 0)}k"
    return f"${_num(value, 0)}"


def _pct(value, digits=1):
    return "—" if value is None else f"{_num(value * 100, digits)}%"


def _signed_pct(value, digits=1):
    if value is None:
        return "—"
    return ("+" if value > 0 else "") + _pct(value, digits)


def _days(value):
    return "—" if value is None else f"{_num(value, 0)} d"


def _overview(get):
    k = get("/api/overview")
    return {
        "Inventory at cost": _usd_short(k["InventoryValueUSD"]),
        "Inventory turns": _num(k["InventoryTurns"], 2),
        "Days inventory outstanding": _num(k["DaysInventoryOutstanding"]),
        "Fill rate": _pct(k["FillRatePct"]),
        "Stocked out now": _num(k["StockoutCount"]),
        "Carrying cost": _usd_short(k["CarryingCostUSD"]),
        "Excess and dead": _usd_short(k["CashReleaseOpportunityUSD"]),
        "GMROI": _num(k["GMROI"], 2),
        "Forecast accuracy": _pct(k["ForecastAccuracyPct"]),
        "Record accuracy": _pct(k["RecordAccuracyPct"]),
        "Open actions": _num(k["OpenActions"]),
    }


def _demand(get):
    k = get("/api/overview")
    return {
        "Forecast accuracy": _pct(k["ForecastAccuracyPct"]),
        "Forecast bias": _signed_pct(k["ForecastBiasPct"]),
        "Units shipped": _num(k["UnitsShipped"]),
        "Fill rate": _pct(k["FillRatePct"]),
        "Critical items": _num(k["CriticalSKUs"]),
    }


def _replenishment(get):
    s = get("/api/replenishment/summary")
    scenario = get("/api/replenishment/policy_scenario?limit=25")["summary"]
    return {
        "Lines to order": _num(s["LinesDue"]),
        "Order value": _usd_short(s["OrderValueUSD"]),
        "Safety stock held": _usd_short(s["SafetyStockValueUSD"]),
        "Contribution at risk": _usd_short(s["StockoutExposureUSD"]),
        "Mean delivered lead time": _days(s["MeanLeadTimeDays"]),
        "Cost of planning on contract": _usd_short(abs(s["ContractGapValueUSD"])),
        "Scenario order value": _usd_short(scenario["OrderValueUSD"]),
        "Scenario safety stock": _usd_short(scenario["SafetyStockValueUSD"]),
        "Lead-window exposure": _usd_short(scenario["LeadWindowExposureUSD"]),
        "Decision status": "Review required",
    }


def _sku_health(get):
    h = get("/api/health/summary")
    return {
        "A-class concentration": _pct(h["AClassShareOfValue"], 0),
        "Dead stock": _usd_short(h["DeadStockValueUSD"]),
        "Slow moving": _usd_short(h["SlowMovingValueUSD"]),
        "Excess above target": _usd_short(h["ExcessValueUSD"]),
        "Provision if written down": _usd_short(h["EOReserveUSD"]),
        "Critical items": _num(h["CriticalSKUs"]),
    }


def _network(get):
    nodes = get("/api/network/nodes")["rows"]
    transfers = get("/api/network/transfers?limit=5000")
    suppliers = get("/api/suppliers/scorecard")["rows"]
    worst = sorted(nodes, key=lambda r: -(r.get("ShareGapPct") or 0))[0]
    lines = sum(r["POLines"] for r in suppliers)
    on_time = sum((r.get("OnTimePct") or 0) * r["POLines"] for r in suppliers) / lines
    perfect = sum((r.get("PerfectOrderPct") or 0) * r["POLines"] for r in suppliers) / lines
    failing = [r for r in suppliers if r["Grade"] in {"D", "F"}]
    return {
        "Nodes": _num(len(nodes)),
        "Most over-weighted node": worst["NodeID"],
        "Transfers worth making": _num(transfers["total"]),
        "On-time delivery": _pct(on_time),
        "Perfect order rate": _pct(perfect),
        "Suppliers below grade C": _num(len(failing)),
    }


def _accuracy(get):
    a = get("/api/accuracy/summary")["rows"][0]
    k = get("/api/overview")
    verbs = {r["Action"]: _num(r["Items"]) for r in get("/api/actions/summary")["rows"]}
    return verbs | {
        "Record accuracy": _pct(a["RecordAccuracy"]),
        "Value accuracy": _pct(a["ValueAccuracy"], 2),
        "Net shrinkage": _usd_short(-(a["NetVarianceValueUSD"] or 0)),
        "Absolute variance": _usd_short(a["AbsVarianceValueUSD"]),
        "Open actions": _num(k["OpenActions"]),
        "Cash to release": _usd_short(k["CashReleaseOpportunityUSD"]),
    }


HEADLINES = {
    "index.html": _overview,
    "demand/index.html": _demand,
    "replenishment/index.html": _replenishment,
    "sku-health/index.html": _sku_health,
    "network/index.html": _network,
    "accuracy/index.html": _accuracy,
}

TILE_RE = re.compile(
    r'<div class="kpi[^"]*">\s*<span class="label">(.*?)</span>\s*<span class="value">(.*?)</span>',
    re.S,
)


def _tiles(out: str) -> dict[str, str]:
    text = (DIST / out).read_text(encoding="utf-8")
    return {
        html.unescape(label).strip(): html.unescape(value).strip()
        for label, value in TILE_RE.findall(text)
    }


def test_every_page_has_a_headline_check():
    assert set(HEADLINES) == {p.out for p in PAGES}


@needs_build
@pytest.mark.parametrize("out", list(HEADLINES))
def test_static_tiles_match_the_app(out, api):
    # Every tile on the page, with the same label and the same figure. A tile
    # added to the app without a line here fails too.
    assert _tiles(out) == HEADLINES[out](api)


@needs_build
def test_every_saved_payload_matches_the_app(api):
    index = json.loads((DIST / "api" / "index.json").read_text())
    assert len(index) > 100
    # Run history lives on the server's disk, not in the seeded snapshot.
    for key, file in index.items():
        if key.startswith("/api/runs") or key.split("?")[0].endswith(".xlsx"):
            continue
        saved = (DIST / file).read_bytes()
        live = api(key)
        if isinstance(live, bytes):
            assert saved == live, key
        else:
            assert _without_timing(json.loads(saved)) == _without_timing(live), key


def _without_timing(payload):
    """How long the build took to compute a payload is not part of the answer."""
    if isinstance(payload, dict):
        return {k: v for k, v in payload.items() if k != "elapsed_ms"}
    return payload


@needs_build
def test_built_pages_are_self_contained():
    for page in PAGES:
        text = (DIST / page.out).read_text(encoding="utf-8")
        assert not re.search(r'(href|src)="/(static|api)/', text), page.out
        assert "cdn.plot.ly" not in text, page.out
        assert text.count("data-frozen") >= 2, page.out
        assert "data-wired" not in text, page.out
    assert (DIST / build_static.PLOTLY_FILE).stat().st_size > 500_000


# --------------------------------------------------------------------------
# The built site in a browser
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """The build served under a sub-path, the way GitHub Pages serves it."""
    if DIST is None:
        pytest.skip("set STATIC_DIST to a build_static.py output")
    pytest.importorskip("playwright")
    root = tmp_path_factory.mktemp("pages")
    (root / "inventory-analytics-app").symlink_to(DIST, target_is_directory=True)
    handler = functools.partial(_QuietHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/inventory-analytics-app/"
    server.shutdown()


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def browser(site):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


def _open(browser, url, width=1280):
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    seen = {"external": [], "errors": []}
    origin = url.split("/inventory-analytics-app/")[0]
    page.on("request", lambda r: r.url.startswith(origin) or seen["external"].append(r.url))
    page.on("pageerror", lambda e: seen["errors"].append(str(e)))
    page.goto(url)
    # Redrawn: the page's own scripts have replaced every frozen chart.
    page.wait_for_function(
        "() => !document.querySelector('[data-frozen]')"
        " && document.querySelectorAll('.js-plotly-plot .main-svg').length > 0",
        timeout=30000,
    )
    return context, page, seen


@needs_build
@pytest.mark.parametrize("path", [p.out.removesuffix("index.html") for p in PAGES])
@pytest.mark.parametrize("width", [1280, 390])
def test_pages_redraw_locally_and_fit_the_screen(browser, site, path, width):
    context, page, seen = _open(browser, site + path, width)
    assert seen["external"] == []
    assert seen["errors"] == []
    assert page.query_selector("#static-miss") is None
    assert page.evaluate("() => document.documentElement.scrollWidth") <= width
    context.close()


@needs_build
def test_chart_hover_works(browser, site):
    context, page, _ = _open(browser, site + "network/")
    bar = page.query_selector(".js-plotly-plot .point path")
    bar.scroll_into_view_if_needed()
    box = bar.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_selector(".hoverlayer .hovertext", timeout=5000)
    context.close()


@needs_build
def test_saved_filter_works_and_unsaved_combination_says_so(browser, site):
    context, page, _ = _open(browser, site + "replenishment/")
    node = page.eval_on_selector("#f-node", "el => el.options[1].value")
    page.select_option("#f-node", node)
    page.wait_for_timeout(500)
    assert page.query_selector("#static-miss") is None
    assert node in page.inner_text("#plan-table")

    page.select_option("#f-abc", "A")
    page.wait_for_selector("#static-miss", timeout=5000)
    assert "Open the full app" in page.inner_text("#static-miss")
    context.close()


@needs_build
def test_policy_lab_grid_is_saved(browser, site):
    context, page, _ = _open(browser, site + "replenishment/")
    page.fill("input[name=service_level]", "0.98")
    page.fill("input[name=demand_multiplier]", "1.10")
    page.fill("input[name=lead_time_multiplier]", "1.2")
    page.click("#scenario-form button[type=submit]")
    result = "() => document.querySelector('#scenario-result').textContent"
    page.wait_for_function(f"() => /Scenario complete/.test(({result})())")

    page.fill("input[name=service_level]", "0.96")
    page.click("#scenario-form button[type=submit]")
    page.wait_for_function(f"() => /Not saved/.test(({result})())")
    context.close()
