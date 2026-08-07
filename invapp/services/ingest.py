"""
Turn a set of workbook sheets into the analysis state the app serves.

This logic used to live inside the upload route, which meant the only way to
get data into the app was to POST a file. The hosted demo needs the same
result without a visitor having to find and upload a workbook first, so it is
a function now and both paths call it. There is no demo-only branch: the
sample workbook goes through exactly what a real upload goes through.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from invapp.services.aggregation import aggregate_final_data, aggregate_sales_history, merge_data
from invapp.services.cleaning import preprocess_data, process_inventory_snapshot
from invapp.services.costing import compute_holding_cost
from invapp.services.state import get_state, set_state
from invapp.services.store import save_run

logger = logging.getLogger(__name__)


def ingest_sheets(sheets: dict[str, pd.DataFrame], *, persist: bool = True) -> dict[str, Any]:
    """
    Build sku_stats and holding cost from raw sheets and publish them as state.

    Returns a small summary of what landed, for the caller to render.
    """
    sales = sheets.get("Sales History", pd.DataFrame())
    inv = sheets.get("Inventory Detail", pd.DataFrame())
    prod = sheets.get("Production Batch", pd.DataFrame())
    cost_val = sheets.get("Cost Value", pd.DataFrame())
    inv1 = sheets.get("Inventory Detail1", pd.DataFrame())

    sales, inv, prod, cost_val = preprocess_data(sales, inv, prod, cost_val)
    agg_sales = (
        aggregate_sales_history(sales)
        if not sales.empty
        else pd.DataFrame(
            columns=["SKU", "Supplier", "Protein", "Description", "ShippedLb", "QuantityOrdered", "Cost", "Rev"]
        )
    )
    merged = merge_data(agg_sales, inv, prod)
    sku_stats = aggregate_final_data(merged, sales)

    # Pack counts from Inventory Detail1 (distinct PackId1 per SKU + state).
    if not inv1.empty and {"SKU", "ProductState", "PackId1"}.issubset(inv1.columns):
        inv1_cc = inv1.loc[:, ["SKU", "ProductState", "PackId1"]].dropna(subset=["PackId1"]).copy()
        inv1_cc["PackId1"] = inv1_cc["PackId1"].astype(str).str.strip()
        packs = (
            inv1_cc.groupby(["SKU", "ProductState"], as_index=False)["PackId1"]
            .nunique()
            .rename(columns={"PackId1": "NumPacksOnHand"})
        )
        sku_stats = sku_stats.merge(packs, on=["SKU", "ProductState"], how="left")
        sku_stats["NumPacksOnHand"] = sku_stats.get("NumPacksOnHand", 0).fillna(0).astype(int)

    # Holding cost is priced off the item-level snapshot: OnHandCost is derived
    # (Cost_pr x ItemCount) and only exists after process_inventory_snapshot.
    snapshot = process_inventory_snapshot(inv.copy())
    snapshot["OriginDate"] = pd.to_datetime(snapshot.get("OriginDate"), errors="coerce")
    hc_params = get_state().holding_cost_params
    holding_cost = compute_holding_cost(snapshot, params=hc_params)

    set_state(sku_stats=sku_stats, holding_cost=holding_cost, raw_sheets=sheets)

    run_id = None
    if persist:
        try:
            run_id = save_run(sku_stats, holding_cost, hc_params)
        except Exception:
            logger.warning("ingest.save_run_failed", exc_info=True)

    return {
        "run_id": run_id,
        "total_skus": int(sku_stats["SKU"].nunique()) if not sku_stats.empty else 0,
        "total_weight": float(sku_stats.get("OnHandWeightTotal", pd.Series(dtype=float)).sum()),
        "total_cost": float(sku_stats.get("OnHandCostTotal", pd.Series(dtype=float)).sum()),
        "avg_woh": (
            float(sku_stats.get("WeeksOnHand", pd.Series(dtype=float)).mean())
            if not sku_stats.empty
            else 0.0
        ),
    }
