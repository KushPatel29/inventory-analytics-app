"""
Regressions for defects that shipped in the original version.

Each of these was reachable from the one action the app exists to perform -
uploading a workbook - and each is pinned here so a refactor cannot quietly
reintroduce it.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

from invapp import create_app
from invapp.services.aggregation import merge_data
from invapp.services.costing import compute_holding_cost
from seed.generate_workbook import generate


@pytest.fixture(scope="module")
def workbook_bytes() -> bytes:
    """A small synthetic workbook, built in memory."""
    sheets = generate(skus=40, weeks=8)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
    return buf.getvalue()


@pytest.fixture(scope="module")
def client_with_data(workbook_bytes):
    app = create_app()
    client = app.test_client()
    resp = client.post(
        "/api/workbook/process",
        data={"file": (io.BytesIO(workbook_bytes), "workbook.xlsx")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)[:400]
    return client


def test_upload_succeeds_end_to_end(client_with_data):
    """
    compute_holding_cost was handed the raw Inventory Detail sheet, which has
    no OnHandCost column. df.get("OnHandCost", 0.0) then returned a float and
    .fillna raised, so every single upload failed with
    "'float' object has no attribute 'fillna'".
    """
    assert client_with_data.get("/api/holding_cost/top").status_code == 200


def test_holding_cost_tolerates_a_missing_onhandcost_column():
    frame = pd.DataFrame(
        [
            {"Cost_pr": 10.0, "ItemCount": 3, "OriginDate": "2026-01-01"},
            {"Cost_pr": 5.0, "ItemCount": 2, "OriginDate": "2026-02-01"},
        ]
    )
    out = compute_holding_cost(frame)
    # Falls back to Cost_pr x ItemCount rather than raising.
    assert out["InventoryValue"].tolist() == [30.0, 10.0]


def test_on_hand_weight_multiplies_by_item_count():
    """
    merge_data summed WeightLb without ItemCount while OnHandCost came from
    CostValue, which already included quantity. Weight and cost therefore
    disagreed by the case count, and every turnover and weeks-on-hand figure
    derived from them was wrong.
    """
    inv = pd.DataFrame(
        [
            {"SKU": "A1", "ProductState": "FZ", "ProductName": "Item A",
             "WeightLb": 10.0, "ItemCount": 4, "CostValue": 200.0},
        ]
    )
    agg_sales = pd.DataFrame(
        [{"SKU": "A1", "Supplier": "S", "Protein": "Beef", "Description": "Item A",
          "ShippedLb": 0.0, "QuantityOrdered": 0, "Cost": 0.0, "Rev": 0.0}]
    )
    prod = pd.DataFrame([{"SKU": "A1", "Supplier": "S", "ProductionShippedLb": 0.0}])

    merged = merge_data(agg_sales, inv, prod)
    assert merged.loc[0, "OnHandWeightLb"] == 40.0, "10 lb x 4 items"


def test_on_hand_value_per_pound_is_plausible(client_with_data):
    """
    A whole-dataset sanity check: the two totals are computed down different
    paths, so if either loses the quantity multiplier again the implied cost
    per pound goes somewhere absurd.
    """
    kpis = client_with_data.get("/api/kpis").get_json()
    per_lb = kpis["total_cost"] / max(kpis["total_weight_lb"], 1)
    assert 1.0 < per_lb < 40.0, f"implied ${per_lb:.2f}/lb is outside any plausible range"


@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/kpis",
        "/api/svsi",
        "/api/inventory/summary",
        "/api/movers/top_usage",
        "/api/movers/top_woh",
        "/api/insights",
        "/api/insights/abc",
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
    ],
)
def test_every_api_returns_data(client_with_data, endpoint):
    resp = client_with_data.get(endpoint)
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload is not None and len(payload) > 0, f"{endpoint} returned nothing"


@pytest.mark.parametrize("page", ["/", "/bins", "/movers", "/moves", "/suppliers", "/healthz"])
def test_pages_render(client_with_data, page):
    assert client_with_data.get(page).status_code == 200


def test_api_module_is_importable():
    """
    invapp/api/__init__.py ended with a stray fragment of a dict literal left
    over from a paste, so the module did not parse at all and the package
    could not be imported.
    """
    import ast
    import pathlib

    source = pathlib.Path("invapp/api/__init__.py").read_text(encoding="utf-8")
    ast.parse(source)
