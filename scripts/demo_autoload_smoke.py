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
    "/api/kpis", "/api/svsi", "/api/insights", "/api/insights/abc",
    "/api/insights/cost_by_protein", "/api/quadrants", "/api/purchase_plan",
    "/api/holding_cost/top", "/api/bins/summary", "/api/bins/weight_by_protein",
    "/api/bins/weight_by_location", "/api/suppliers/top_cost", "/api/turnover",
    "/api/moves/fz_to_ext", "/api/moves/ext_to_fz", "/api/movers/top_usage",
    "/api/movers/top_woh", "/api/supplier/woh_distribution",
]
PAGES = ["/", "/bins", "/movers", "/moves", "/suppliers", "/healthz"]

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
        if client.get("/api/kpis").status_code == 200:
            break
        time.sleep(1)
    check(client.get("/api/kpis").status_code == 200, "sample data loaded without an upload")

    print("\nReports return data")
    for endpoint in API_ENDPOINTS:
        resp = client.get(endpoint)
        payload = resp.get_json(silent=True)
        size = len(payload) if isinstance(payload, (list, dict)) else 0
        check(resp.status_code == 200 and size > 0, f"{endpoint} -> {resp.status_code}, {size} entries")

    print("\nPages render")
    for page in PAGES:
        check(client.get(page).status_code == 200, f"{page}")

    print("\nThe demo says its data is generated")
    check("Sample data." in client.get("/").get_data(as_text=True),
          "sample-data banner is shown")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        return 1
    print("Demo auto-load verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
