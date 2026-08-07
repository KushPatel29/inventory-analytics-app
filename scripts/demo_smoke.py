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
    "/api/kpis",
    "/api/svsi",
    "/api/inventory/summary",
    "/api/movers/top_usage",
    "/api/movers/top_woh",
    "/api/insights",
    "/api/insights/abc",
    "/api/insights/cost_by_protein",
    "/api/quadrants",
    "/api/purchase_plan",
    "/api/holding_cost/top",
    "/api/bins/summary",
    "/api/bins/weight_by_protein",
    "/api/bins/weight_by_location",
    "/api/suppliers/top_cost",
    "/api/turnover",
    "/api/moves/fz_to_ext",
    "/api/moves/ext_to_fz",
    "/api/supplier/woh_distribution",
]

PAGES = ["/", "/bins", "/movers", "/moves", "/suppliers", "/healthz"]

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
        size = len(payload) if isinstance(payload, (list, dict)) else 0
        check(r.status_code == 200 and size > 0, f"{endpoint} -> {r.status_code}, {size} entries")

    print("\nPages render")
    for page in PAGES:
        r = client.get(page)
        check(r.status_code == 200, f"{page} -> {r.status_code}")

    print("\nTotals are internally consistent")
    kpis = client.get("/api/kpis").get_json() or {}
    weight = float(kpis.get("total_weight_lb") or 0)
    cost = float(kpis.get("total_cost") or 0)
    per_lb = cost / weight if weight else 0
    # On-hand weight and on-hand cost are computed down separate paths; if
    # either drops the quantity multiplier the implied price per pound goes
    # somewhere impossible.
    check(1.0 < per_lb < 40.0, f"implied ${per_lb:.2f}/lb on hand is plausible")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        return 1
    print("All demo checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
