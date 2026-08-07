from __future__ import annotations

import numpy as np
import pandas as pd


def compute_aging_buckets(df: pd.DataFrame, days_col: str = "DaysInStorage") -> pd.DataFrame:
    bins = [0, 30, 60, 90, 180, 365, np.inf]
    labels = ["0–30", "31–60", "61–90", "91–180", "181–365", "365+"]
    df["AgeBucket"] = pd.cut(df.get(days_col, 0), bins=bins, labels=labels, right=False)
    return df


def abc_classification(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().sort_values("InventoryValue", ascending=False)
    total = df["InventoryValue"].sum()
    if total <= 0:
        df["ABC"] = "C"
        return df
    df["CumPerc"] = df["InventoryValue"].cumsum() / total
    df["ABC"] = pd.cut(
        df["CumPerc"], bins=[0, 0.8, 0.95, 1.0], labels=["A", "B", "C"], include_lowest=True
    )
    return df.drop(columns=["CumPerc"])


def compute_holding_cost(df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    today = pd.Timestamp("today").normalize()
    df = df.copy()
    df["OriginDate"] = pd.to_datetime(df.get("OriginDate"), errors="coerce").fillna(today)
    df["DaysInStorage"] = (today - df["OriginDate"]).dt.days.clip(lower=0)
    df["FractionOfYear"] = np.minimum(df["DaysInStorage"] / 365.0, 1.0)
    # df.get(col, default) returns the scalar default when the column is
    # absent, and a scalar has no .fillna - so guard the column instead of
    # relying on the default.
    if "OnHandCost" in df.columns:
        df["InventoryValue"] = pd.to_numeric(df["OnHandCost"], errors="coerce").fillna(0.0)
    elif {"Cost_pr", "ItemCount"}.issubset(df.columns):
        df["InventoryValue"] = (
            pd.to_numeric(df["Cost_pr"], errors="coerce").fillna(0.0)
            * pd.to_numeric(df["ItemCount"], errors="coerce").fillna(0.0)
        )
    else:
        df["InventoryValue"] = 0.0

    total_val = df["InventoryValue"].sum()
    df["ValueFraction"] = np.where(total_val > 0, df["InventoryValue"] / total_val, 0.0)

    # Tunable parameters
    params = params or {}
    rc = float(params.get("rc", 0.05))
    sa = float(params.get("sa", 102055.0))
    spc = float(params.get("spc", (71466 * 0.4 + 107128 * 0.7 + 48280 * 0.7 + 453626 + 544699 * 0.5)))
    rr = float(params.get("rr", 0.03))

    df["CapitalCost"] = df["InventoryValue"] * rc * df["FractionOfYear"]
    df["ServiceCost"] = df["ValueFraction"] * sa * df["FractionOfYear"]
    df["StorageCost"] = df["ValueFraction"] * spc * df["FractionOfYear"]
    df["RiskCost"] = df["InventoryValue"] * rr * df["FractionOfYear"]

    for c in ["CapitalCost", "ServiceCost", "StorageCost", "RiskCost"]:
        df[c] = df[c].fillna(0.0)

    df["TotalHoldingCost"] = df[["CapitalCost", "ServiceCost", "StorageCost", "RiskCost"]].sum(axis=1)
    df["HoldingCostPercent"] = (
        df["TotalHoldingCost"] / df["InventoryValue"].replace({0: np.nan})
    ).fillna(0.0) * 100

    df = compute_aging_buckets(df)
    df = abc_classification(df)
    return df
