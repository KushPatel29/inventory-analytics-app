"""
SKU segmentation: ABC by consumption value, XYZ by forecastability, and the
matrix of the two.

ABC on its own answers "where is the money" and stops. It cannot tell an A item
that ships two hundred a week like clockwork from an A item that ships nothing
for a month and then four hundred at once - and those two need opposite
policies. XYZ adds that second axis by classifying on the coefficient of
variation of weekly demand, and the nine cells of the matrix each get a stock
policy rather than a colour.

ABC here is on **annual consumption value** (units shipped x unit cost), not on
inventory value. They are different questions and the difference is the point:
an item with $400k of stock and no demand is a C item sitting on an A item's
worth of cash, which is exactly the thing worth finding.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Cumulative-value cut points. 80/95 is the convention; it is a convention
# rather than a law, and it is here as a constant so it can be argued with.
ABC_CUTS = (0.80, 0.95)

# Coefficient-of-variation cut points for XYZ. Below 0.5 a series is smooth
# enough that a moving average will track it; above 1.0 the standard deviation
# exceeds the mean and no amount of forecasting will fix it - the answer there
# is buffer stock or make-to-order, not a better model.
XYZ_CUTS = (0.5, 1.0)

# What to do about each cell of the matrix. This is the difference between a
# classification and a recommendation.
MATRIX_POLICY: dict[str, str] = {
    "AX": "Tight control, low buffer, weekly review, automate the reorder",
    "AY": "Tight control with a real safety stock; review the forecast monthly",
    "AZ": "High buffer or make-to-order; do not chase the forecast",
    "BX": "Standard reorder point, monthly review",
    "BY": "Standard reorder point with an uplifted buffer",
    "BZ": "Order to demand, keep a token buffer, consider dropping",
    "CX": "Two-bin or bulk buy; the ordering cost outweighs the holding cost",
    "CY": "Bulk buy on a long cycle; review the range annually",
    "CZ": "Candidate for delisting; stock only against a firm order",
}


def abc_classify(
    frame: pd.DataFrame,
    value_col: str,
    *,
    cuts: tuple[float, float] = ABC_CUTS,
    label: str = "ABCClass",
) -> pd.DataFrame:
    """Assign A / B / C on the cumulative share of `value_col`.

    Sorted with a stable tie-break on the index so two runs on two machines
    classify the boundary rows identically. Without that, a pair of SKUs with
    the same consumption value can swap places, one of them crosses the 80%
    line, and a committed mart differs between a laptop and CI for no reason
    anybody can see.
    """
    out = frame.copy()
    if out.empty:
        out[label] = pd.Series(dtype=object)
        out[f"{label}CumulativeShare"] = pd.Series(dtype=float)
        return out

    values = pd.to_numeric(out[value_col], errors="coerce").fillna(0.0)
    total = float(values.sum())
    order = np.lexsort((out.index.to_numpy(dtype=str), -values.to_numpy()))
    ranked = out.iloc[order].copy()
    ranked_values = values.iloc[order]

    if total <= 0:
        ranked[label] = "C"
        ranked[f"{label}CumulativeShare"] = 0.0
    else:
        cumulative = ranked_values.cumsum() / total
        ranked[f"{label}CumulativeShare"] = cumulative.to_numpy()
        ranked[label] = np.select(
            [cumulative <= cuts[0], cumulative <= cuts[1]], ["A", "B"], default="C"
        )
    ranked[f"{label}Rank"] = np.arange(1, len(ranked) + 1)
    return ranked.reindex(frame.index)


def xyz_classify(
    frame: pd.DataFrame,
    cov_col: str = "CoefficientOfVariation",
    *,
    cuts: tuple[float, float] = XYZ_CUTS,
    label: str = "XYZClass",
) -> pd.DataFrame:
    """Assign X / Y / Z on demand variability."""
    out = frame.copy()
    if out.empty:
        out[label] = pd.Series(dtype=object)
        return out
    cov = pd.to_numeric(out.get(cov_col), errors="coerce")
    out[label] = np.select(
        [cov.isna(), cov <= cuts[0], cov <= cuts[1]], ["Z", "X", "Y"], default="Z"
    )
    return out


def movement_class(
    frame: pd.DataFrame,
    *,
    slow_days: float = 90,
    obsolete_days: float = 180,
    weeks_col: str = "WeeksSinceLastShip",
    demand_col: str = "WeeklyDemand",
) -> pd.Series:
    """Fast / Medium / Slow / Dead, on recency first and rate second.

    Recency dominates on purpose. An item that averaged forty a week for a year
    and has shipped nothing since April is dead stock, whatever the average
    says - and a mean taken over the whole history is exactly the statistic
    that would keep calling it a fast mover.
    """
    weeks_since = pd.to_numeric(frame.get(weeks_col), errors="coerce")
    rate = pd.to_numeric(frame.get(demand_col), errors="coerce").fillna(0.0)
    days_since = weeks_since * 7.0

    fast_cut = rate[rate > 0].quantile(0.70) if (rate > 0).any() else 0.0
    medium_cut = rate[rate > 0].quantile(0.30) if (rate > 0).any() else 0.0

    return pd.Series(
        np.select(
            [
                days_since.isna() | (days_since >= obsolete_days),
                days_since >= slow_days,
                rate >= fast_cut,
                rate >= medium_cut,
            ],
            ["Dead", "Slow", "Fast", "Medium"],
            default="Slow",
        ),
        index=frame.index,
        name="MovementClass",
    )


def build_segments(
    stats: pd.DataFrame,
    items: pd.DataFrame,
    inventory_value: pd.DataFrame,
    params: dict,
) -> pd.DataFrame:
    """One row per SKU with every classification the app uses.

    `stats` is the SKU-level demand statistics frame; `inventory_value` carries
    on-hand units and value rolled up to the SKU.
    """
    if stats.empty:
        return pd.DataFrame()

    df = stats.reset_index().merge(
        items[["SKU", "ItemDescription", "Department", "Category", "Brand",
               "SupplierID", "UnitCost", "UnitPrice", "Lifecycle", "TempZone", "LaunchDate"]],
        on="SKU", how="left",
    )
    df = df.merge(inventory_value, on="SKU", how="left")
    for col in ("OnHandUnits", "InventoryValueUSD", "InTransitUnits", "ReservedUnits"):
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    df["UnitCost"] = pd.to_numeric(df["UnitCost"], errors="coerce").fillna(0.0)
    df["UnitPrice"] = pd.to_numeric(df["UnitPrice"], errors="coerce").fillna(0.0)

    df["AnnualConsumptionValueUSD"] = df["AnnualDemand"] * df["UnitCost"]
    df["AnnualRevenueUSD"] = df["AnnualDemand"] * df["UnitPrice"]
    df["GrossMarginUSD"] = df["AnnualDemand"] * (df["UnitPrice"] - df["UnitCost"])

    df = df.set_index("SKU", drop=False)
    df = abc_classify(df, "AnnualConsumptionValueUSD")
    df = xyz_classify(df)
    df["ABCXYZ"] = df["ABCClass"].astype(str) + df["XYZClass"].astype(str)
    df["Policy"] = df["ABCXYZ"].map(MATRIX_POLICY).fillna("Review manually")
    df["MovementClass"] = movement_class(
        df,
        slow_days=float(params.get("SlowMoverDays", 90)),
        obsolete_days=float(params.get("ObsoleteDays", 180)),
    )

    # Turns and GMROI at the SKU. GMROI - gross margin return on inventory
    # investment - is the merchandising counterpart to turns: it says how many
    # dollars of margin each dollar of stock earns in a year, which is the
    # number that decides whether a slow, high-margin item is a problem.
    avg_inventory = df["InventoryValueUSD"].replace(0, np.nan)
    df["AnnualTurns"] = (df["AnnualConsumptionValueUSD"] / avg_inventory).replace(
        [np.inf, -np.inf], np.nan
    )
    df["GMROI"] = (df["GrossMarginUSD"] / avg_inventory).replace([np.inf, -np.inf], np.nan)
    df["DaysInventoryOutstanding"] = np.where(
        df["AnnualConsumptionValueUSD"] > 0,
        df["InventoryValueUSD"] / df["AnnualConsumptionValueUSD"] * 365.0,
        np.nan,
    )

    # A critical item carries A-class value on demand nobody can forecast. That
    # combination is what actually needs a planner rather than a rule: an AX
    # item can be left to its reorder point, and a CZ item is not worth the
    # attention. Defining "critical" as low cover instead would make the flag a
    # restatement of the stockout list, and it would empty out the moment the
    # network was well stocked - which is exactly when these still need
    # watching.
    df["IsCritical"] = (df["ABCClass"] == "A") & df["XYZClass"].isin(("Y", "Z"))

    return df.reset_index(drop=True)


def pareto_curve(segments: pd.DataFrame, value_col: str = "AnnualConsumptionValueUSD") -> pd.DataFrame:
    """SKU rank against cumulative share of value - the Pareto chart's data."""
    if segments.empty:
        return pd.DataFrame(columns=["Rank", "SKU", "ItemDescription", "Value",
                                     "CumulativeValue", "CumulativeShare", "SKUShare", "ABCClass"])
    ordered = segments.sort_values([value_col, "SKU"], ascending=[False, True], kind="stable").copy()
    total = float(ordered[value_col].sum())
    ordered["Rank"] = np.arange(1, len(ordered) + 1)
    ordered["Value"] = ordered[value_col]
    ordered["CumulativeValue"] = ordered[value_col].cumsum()
    ordered["CumulativeShare"] = ordered["CumulativeValue"] / total if total > 0 else 0.0
    ordered["SKUShare"] = ordered["Rank"] / len(ordered)
    return ordered[["Rank", "SKU", "ItemDescription", "Value", "CumulativeValue",
                    "CumulativeShare", "SKUShare", "ABCClass"]]


def abc_xyz_matrix(segments: pd.DataFrame) -> pd.DataFrame:
    """The 3x3 grid: how many SKUs and how much value sit in each cell."""
    if segments.empty:
        return pd.DataFrame(columns=["ABCClass", "XYZClass", "SKUCount", "InventoryValueUSD",
                                     "AnnualConsumptionValueUSD", "Policy"])
    grid = (
        segments.groupby(["ABCClass", "XYZClass"], as_index=False)
        .agg(
            SKUCount=("SKU", "nunique"),
            InventoryValueUSD=("InventoryValueUSD", "sum"),
            AnnualConsumptionValueUSD=("AnnualConsumptionValueUSD", "sum"),
        )
    )
    grid["Policy"] = (grid["ABCClass"] + grid["XYZClass"]).map(MATRIX_POLICY)
    return grid.sort_values(["ABCClass", "XYZClass"], kind="stable")


__all__ = [
    "ABC_CUTS",
    "MATRIX_POLICY",
    "XYZ_CUTS",
    "abc_classify",
    "abc_xyz_matrix",
    "build_segments",
    "movement_class",
    "pareto_curve",
    "xyz_classify",
]
