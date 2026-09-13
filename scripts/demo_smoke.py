#!/usr/bin/env python3
"""
Prove the app works from a clean clone.

Generates a workbook if one is not already present, pushes it through the real
upload endpoint, then asserts every report comes back with data and every page
renders. Exits non-zero on the first problem.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from invapp import create_app  # noqa: E402
from seed.generate_workbook import DEFAULT_OUT, generate  # noqa: E402

import pandas as pd  # noqa: E402

API_ENDPOINTS = [
    "/api/overview", "/api/overview/trend", "/api/overview/carrying",
    "/api/overview/abc", "/api/overview/value_by?dim=Department",
    "/api/demand/history", "/api/demand/actual_vs_forecast",
    "/api/demand/skus?limit=5", "/api/demand/method_mix",
    "/api/demand/seasonality", "/api/demand/bias?limit=5",
    "/api/replenishment/plan?limit=5", "/api/replenishment/summary",
    "/api/replenishment/urgency", "/api/replenishment/eoq?limit=5",
    "/api/replenishment/service_curve", "/api/health/pareto?limit=5",
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


def workbook_bytes() -> bytes:
    if DEFAULT_OUT.exists():
        return DEFAULT_OUT.read_bytes()
    sheets = generate()
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
    return buf.getvalue()


def main() -> int:
    app = create_app()
    client = app.test_client()

    print("\nUpload")
    resp = client.post(
        "/api/workbook/process",
        data={"file": (io.BytesIO(workbook_bytes()), "inventory_workbook.xlsx")},
        content_type="multipart/form-data",
    )
    check(resp.status_code == 200, f"workbook processed -> {resp.status_code}")
    if resp.status_code != 200:
        print(resp.get_data(as_text=True)[:400])
        return 1

    print("\nReports return data")
    for endpoint in API_ENDPOINTS:
        r = client.get(endpoint)
        payload = r.get_json(silent=True)
        size = len(payload.get("rows", [])) if isinstance(payload, dict) and "rows" in payload else len(payload or {})
        check(r.status_code == 200 and size > 0, f"{endpoint} -> {r.status_code}, {size} entries")

    print("\nPages render")
    for page in PAGES:
        r = client.get(page)
        check(r.status_code == 200, f"{page} -> {r.status_code}")

    print("\nTotals are internally consistent")
    overview = client.get("/api/overview").get_json() or {}
    check(overview.get("SKUCount") == 420, "all 420 generated SKUs reached the model")
    check(float(overview.get("InventoryValueUSD") or 0) > 0, "inventory value is positive")
    check(int(overview.get("OpenActions") or 0) > 0, "the decision register contains actions")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        return 1
    print("All demo checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
