from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Tuple


def compute_threshold_move(ext_df: pd.DataFrame, hc_df: pd.DataFrame | None) -> float:
    common = ext_df if hc_df is None else ext_df[ext_df["SKU"].isin(hc_df.get("SKU", []))]
    if common.empty:
        return 1.0
    return float(pd.to_numeric(common.get("WeeksOnHand", 0), errors="coerce").median())


def classify_movement(df: pd.DataFrame, quantile: float = 0.5) -> pd.DataFrame:
    tmp = df.copy()
    qv = pd.to_numeric(tmp.get("AvgWeeklyUsage", 0), errors="coerce").quantile(quantile)
    tmp["MovementClass"] = np.where(tmp["AvgWeeklyUsage"] >= qv, "High", "Slow")
    return tmp


def quadrantify(
    df: pd.DataFrame, xcol: str, ycol: str, method: str = "median"
) -> Tuple[pd.DataFrame, float, float]:
    df_q = df.dropna(subset=[xcol, ycol]).copy()
    if method == "median":
        xm, ym = df_q[xcol].median(), df_q[ycol].median()
    elif method == "mean":
        xm, ym = df_q[xcol].mean(), df_q[ycol].mean()
    else:
        raise ValueError("method must be 'median' or 'mean'")

    hi_x = df_q[xcol] >= xm
    hi_y = df_q[ycol] >= ym
    df_q["Quadrant"] = hi_x.map({True: "High", False: "Low"}) + "-" + hi_y.map({True: "High", False: "Low"})
    df_q["Quadrant"] = pd.Categorical(
        df_q["Quadrant"], categories=["High-High", "High-Low", "Low-High", "Low-Low"], ordered=True
    )
    return df_q, float(xm), float(ym)


def top_n_by_metric(df: pd.DataFrame, group: str, metric: str, n: int = 10, asc: bool = False) -> pd.DataFrame:
    agg = df.groupby(group, as_index=False)[metric].sum()
    return agg.sort_values(metric, ascending=asc).head(n)

