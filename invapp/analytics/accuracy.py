"""
Inventory record accuracy, shrinkage and the bridge between them.

Three numbers get called "inventory accuracy" and they are not the same, so
all three are computed and labelled:

* **Record accuracy** - the share of counted records whose counted quantity
  matches the system quantity, within tolerance. This is the operational one,
  the one a WMS reports, and the one a warehouse manager is measured on. It
  weights a bin of screws the same as a bin of tablets.
* **Value accuracy** - one minus the absolute variance in dollars over the
  system value counted. This is the one finance cares about, and it is always
  worse than record accuracy looks when the errors are on expensive items.
* **Net shrinkage** - the signed variance. It nets a found pallet against a
  missing one, so it is the smallest of the three and the only one that goes
  on a P&L.

The waterfall bridges opening book value to closing physical value through
receipts, shipments and each class of adjustment - the one presentation that
makes a shrink number explainable rather than merely reportable.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def build_count_variance(
    counts: pd.DataFrame,
    items: pd.DataFrame,
    params: dict,
) -> pd.DataFrame:
    """Per-count variance in units and dollars."""
    if counts is None or counts.empty:
        return pd.DataFrame()

    tolerance = float(params.get("CountToleranceUnits", 0))
    df = counts.copy()
    df["CountDate"] = pd.to_datetime(df.get("CountDate"), errors="coerce")
    for col in ("SystemQty", "CountedQty", "UnitCost"):
        df[col] = pd.to_numeric(df.get(col), errors="coerce").fillna(0.0)

    if items is not None and not items.empty:
        cols = [c for c in ("SKU", "ItemDescription", "Department", "Category") if c in items.columns]
        df = df.merge(items[cols], on="SKU", how="left")

    df["VarianceUnits"] = df["CountedQty"] - df["SystemQty"]
    df["AbsVarianceUnits"] = df["VarianceUnits"].abs()
    df["VarianceValueUSD"] = df["VarianceUnits"] * df["UnitCost"]
    df["AbsVarianceValueUSD"] = df["AbsVarianceUnits"] * df["UnitCost"]
    df["SystemValueUSD"] = df["SystemQty"] * df["UnitCost"]
    df["IsAccurate"] = df["AbsVarianceUnits"] <= tolerance
    df["ReasonCode"] = df.get("ReasonCode", "").fillna("").replace("", "No variance")
    return df


def accuracy_summary(variance: pd.DataFrame, group: str | None = None) -> pd.DataFrame:
    """Accuracy measures, network-wide or split by a dimension."""
    columns = ["RecordsCounted", "AccurateRecords", "RecordAccuracy", "AbsVarianceUnits",
               "AbsVarianceValueUSD", "NetVarianceValueUSD", "SystemValueUSD",
               "ValueAccuracy", "ShrinkagePct"]
    if variance is None or variance.empty:
        return pd.DataFrame(columns=([group] if group else []) + columns)

    def _agg(frame: pd.DataFrame) -> dict:
        system_value = float(frame["SystemValueUSD"].sum())
        abs_value = float(frame["AbsVarianceValueUSD"].sum())
        net_value = float(frame["VarianceValueUSD"].sum())
        records = int(len(frame))
        accurate = int(frame["IsAccurate"].sum())
        return {
            "RecordsCounted": records,
            "AccurateRecords": accurate,
            "RecordAccuracy": accurate / records if records else float("nan"),
            "AbsVarianceUnits": float(frame["AbsVarianceUnits"].sum()),
            "AbsVarianceValueUSD": abs_value,
            "NetVarianceValueUSD": net_value,
            "SystemValueUSD": system_value,
            "ValueAccuracy": 1.0 - abs_value / system_value if system_value else float("nan"),
            # Negative net variance is a loss, so shrinkage is reported positive
            # when stock is missing - the sign convention every stock-loss report
            # uses, and the opposite of the raw arithmetic.
            "ShrinkagePct": -net_value / system_value if system_value else float("nan"),
        }

    if group is None:
        return pd.DataFrame([_agg(variance)])
    rows = []
    for key, frame in variance.groupby(group, sort=True):
        row = {group: key}
        row.update(_agg(frame))
        rows.append(row)
    return pd.DataFrame(rows)


def variance_by_reason(variance: pd.DataFrame) -> pd.DataFrame:
    """Root cause: where the variance comes from, in dollars."""
    if variance is None or variance.empty:
        return pd.DataFrame(columns=["ReasonCode", "Records", "AbsVarianceValueUSD",
                                     "NetVarianceValueUSD"])
    errors = variance[~variance["IsAccurate"]]
    if errors.empty:
        return pd.DataFrame(columns=["ReasonCode", "Records", "AbsVarianceValueUSD",
                                     "NetVarianceValueUSD"])
    out = (
        errors.groupby("ReasonCode", as_index=False)
        .agg(
            Records=("ReasonCode", "size"),
            AbsVarianceValueUSD=("AbsVarianceValueUSD", "sum"),
            NetVarianceValueUSD=("VarianceValueUSD", "sum"),
        )
    )
    return out.sort_values(["AbsVarianceValueUSD", "ReasonCode"],
                           ascending=[False, True], kind="stable")


def shrinkage_waterfall(
    adjustments: pd.DataFrame,
    inventory_value: float,
    demand_cogs: float,
    receipt_value: float,
) -> pd.DataFrame:
    """Opening book value to closing physical value, one step per cause.

    The steps are signed and ordered so the running total is meaningful at every
    point. Opening is derived by working backwards from the closing physical
    value rather than stated: the snapshot is the fact, and an opening balance
    that does not reconcile to it is a bridge to nowhere.
    """
    if adjustments is None or adjustments.empty:
        adjust_by_type = pd.DataFrame(columns=["AdjustmentType", "ValueUSD"])
    else:
        adj = adjustments.copy()
        adj["Units"] = pd.to_numeric(adj.get("Units"), errors="coerce").fillna(0.0)
        adj["UnitCost"] = pd.to_numeric(adj.get("UnitCost"), errors="coerce").fillna(0.0)
        adj["ValueUSD"] = adj["Units"] * adj["UnitCost"]
        adjust_by_type = (
            adj.groupby("AdjustmentType", as_index=False)["ValueUSD"].sum()
            .sort_values(["ValueUSD", "AdjustmentType"], ascending=[True, True], kind="stable")
        )

    adjustment_total = float(adjust_by_type["ValueUSD"].sum()) if not adjust_by_type.empty else 0.0
    opening = inventory_value - receipt_value + demand_cogs - adjustment_total

    steps = [
        {"Step": "Opening book value", "ValueUSD": opening, "Kind": "Total"},
        {"Step": "Receipts", "ValueUSD": receipt_value, "Kind": "Increase"},
        {"Step": "Shipments (COGS)", "ValueUSD": -demand_cogs, "Kind": "Decrease"},
    ]
    for row in adjust_by_type.itertuples(index=False):
        steps.append({
            "Step": str(row.AdjustmentType),
            "ValueUSD": float(row.ValueUSD),
            "Kind": "Increase" if row.ValueUSD >= 0 else "Decrease",
        })
    steps.append({"Step": "Closing physical value", "ValueUSD": inventory_value, "Kind": "Total"})

    frame = pd.DataFrame(steps)
    running = 0.0
    starts, ends = [], []
    for row in frame.itertuples(index=False):
        if row.Kind == "Total":
            starts.append(0.0)
            ends.append(row.ValueUSD)
            running = row.ValueUSD
        else:
            starts.append(running)
            running += row.ValueUSD
            ends.append(running)
    frame["RunningStart"] = starts
    frame["RunningEnd"] = ends
    frame["StepOrder"] = np.arange(len(frame))
    return frame


def count_coverage(inventory: pd.DataFrame, counts: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """How much of the network has been counted lately, by node.

    Coverage is the leading indicator that accuracy is the lagging one for. A
    node reporting 99% accuracy on eleven counts is not accurate; it is
    unmeasured.
    """
    if inventory is None or inventory.empty:
        return pd.DataFrame(columns=["NodeID", "LocationsStocked", "LocationsCounted90d",
                                     "CoveragePct", "MedianDaysSinceCount"])
    inv = inventory.copy()
    inv["LastCountDate"] = pd.to_datetime(inv.get("LastCountDate"), errors="coerce")
    inv["DaysSinceCount"] = (as_of - inv["LastCountDate"]).dt.days

    counted = counts.copy() if counts is not None and not counts.empty else pd.DataFrame()
    if not counted.empty:
        counted["CountDate"] = pd.to_datetime(counted["CountDate"], errors="coerce")
        recent = counted[counted["CountDate"] >= as_of - pd.Timedelta(days=90)]
        recent_pairs = recent.groupby("NodeID")["SKU"].nunique()
    else:
        recent_pairs = pd.Series(dtype=int)

    out = (
        inv.groupby("NodeID", as_index=False)
        .agg(
            LocationsStocked=("SKU", "nunique"),
            MedianDaysSinceCount=("DaysSinceCount", "median"),
        )
    )
    out["LocationsCounted90d"] = out["NodeID"].map(recent_pairs).fillna(0).astype(int)
    out["CoveragePct"] = out["LocationsCounted90d"] / out["LocationsStocked"].replace(0, np.nan)
    return out.sort_values("NodeID", kind="stable")


__all__ = [
    "accuracy_summary",
    "build_count_variance",
    "count_coverage",
    "shrinkage_waterfall",
    "variance_by_reason",
]
