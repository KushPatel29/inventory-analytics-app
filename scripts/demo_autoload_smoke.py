#!/usr/bin/env python3
"""
Prove the hosted demo works with no upload.

DEMO_AUTOLOAD makes the app generate the sample workbook and ingest it at
startup. This checks that a visitor who just opens the link gets a working
dashboard: every report returns data and every page renders, without anyone
POSTing a file.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DEMO_AUTOLOAD", "1")

from invapp import create_app  # noqa: E402

API_ENDPOINTS = [
    "/api/overview", "/api/overview/trend", "/api/overview/carrying",
    "/api/overview/abc", "/api/overview/value_by?dim=Department",
    "/api/demand/history", "/api/demand/actual_vs_forecast",
    "/api/demand/skus?limit=5", "/api/demand/method_mix",
    "/api/demand/seasonality", "/api/demand/bias?limit=5",
    "/api/demand/segment_scorecard",
    "/api/replenishment/plan?limit=5", "/api/replenishment/summary",
    "/api/replenishment/urgency", "/api/replenishment/eoq?limit=5",
    "/api/replenishment/service_curve",
    "/api/replenishment/service_cost_frontier",
    "/api/replenishment/policy_scenario?limit=5",
    "/api/health/pareto?limit=5",
    "/api/health/matrix", "/api/health/ageing",
    "/api/health/ageing?dim=NodeID", "/api/health/dead_stock?limit=5",
    "/api/health/summary", "/api/health/movement", "/api/network/nodes",
    "/api/network/transfers?limit=5", "/api/network/balance?limit=5",
    "/api/suppliers/scorecard", "/api/suppliers/open_pos?limit=5",
    "/api/suppliers/lead_time_gap", "/api/accuracy/summary",
    "/api/accuracy/summary?dim=NodeID", "/api/accuracy/reasons",
    "/api/accuracy/waterfall", "/api/accuracy/coverage",
    "/api/actions/register?limit=5", "/api/actions/summary",
]
PAGES = ["/", "/demand", "/replenishment", "/sku-health", "/network", "/accuracy"]

failures: list[str] = []


def check(ok: bool, message: str) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {message}")
    if not ok:
        failures.append(message)


def main() -> int:
    app = create_app()
    client = app.test_client()

    print("\nWaiting for the background auto-load")
    for _ in range(120):
        if client.get("/api/overview").status_code == 200:
            break
        time.sleep(1)
    check(client.get("/api/overview").status_code == 200, "sample data loaded without an upload")

    print("\nReports return data")
    for endpoint in API_ENDPOINTS:
        resp = client.get(endpoint)
        payload = resp.get_json(silent=True)
        size = (
            len(payload.get("rows", []))
            if isinstance(payload, dict) and "rows" in payload
            else len(payload or {})
        )
        check(
            resp.status_code == 200 and size > 0,
            f"{endpoint} -> {resp.status_code}, {size} entries",
        )

    print("\nPages render")
    for page in PAGES:
        check(client.get(page).status_code == 200, f"{page}")

    print("\nThe demo says its data is generated")
    check("Generated data." in client.get("/").get_data(as_text=True),
          "sample-data banner is shown")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        return 1
    print("Demo auto-load verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
