"""
Stock ageing, dead stock and excess - and what writing it off would cost.

Ageing and dead stock are different questions that get conflated. Ageing is
about the *stock*: how long has this particular quantity been sitting here.
Dead is about the *item*: has anything shipped at all. A fast-moving SKU can
hold aged stock in one node while turning over weekly in another, and an item
received last week can already be dead.

Excess is a third thing again, and the only one that is defined against a
target rather than a clock: stock above what the reorder point plus one order
quantity justifies. It is the number that converts to cash, so it is the one
the working-capital page carries.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

AGE_BUCKETS = (
    (0, 30, "0-30 days"),
    (30, 60, "31-60 days"),
    (60, 90, "61-90 days"),
    (90, 180, "91-180 days"),
    (180, 365, "181-365 days"),
    (365, np.inf, "365+ days"),
)
AGE_BUCKET_ORDER = [label for _, _, label in AGE_BUCKETS]

# Excess-and-obsolete reserve ladder. A provision policy, not a physics
# constant: the ladder is what a controller signs off, and it is here as data
# so the number on the page can be traced to a rule rather than a guess.
EO_RESERVE_LADDER = (
    (90, 0.00),
    (180, 0.25),
    (365, 0.50),
    (np.inf, 1.00),
)


def age_bucket(days: pd.Series) -> pd.Series:
    """Label each row's age. Right-open bins, so 30 days is "31-60"."""
    values = pd.to_numeric(days, errors="coerce")
    conditions = [values < high for _, high, _ in AGE_BUCKETS]
    return pd.Series(
        np.select(conditions, AGE_BUCKET_ORDER, default=AGE_BUCKET_ORDER[-1]),
        index=days.index,
        name="AgeBucket",
    )


def reserve_rate(days: pd.Series) -> pd.Series:
    """The provision rate for each row's age, from the ladder above."""
    values = pd.to_numeric(days, errors="coerce").fillna(0.0)
    conditions = [values < threshold for threshold, _ in EO_RESERVE_LADDER]
    rates = [rate for _, rate in EO_RESERVE_LADDER]
    return pd.Series(np.select(conditions, rates, default=1.0), index=days.index)


def build_ageing(
    inventory: pd.DataFrame,
    segments: pd.DataFrame,
    plan: pd.DataFrame,
    params: dict,
    *,
    node_recency: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One row per SKU and node with age, movement, excess and provision."""
    if inventory.empty:
        return pd.DataFrame()

    df = inventory.copy()
    if "DaysOnHandAge" not in df.columns:
        df["DaysOnHandAge"] = 0.0
    df["AgeDays"] = pd.to_numeric(df["DaysOnHandAge"], errors="coerce").fillna(0.0)
    df["AgeBucket"] = age_bucket(df["AgeDays"])

    keep = ["SKU", "ItemDescription", "Department", "Category", "ABCClass", "XYZClass",
            "MovementClass", "WeeksSinceLastShip", "WeeklyDemand", "UnitCost", "UnitPrice",
            "Lifecycle"]
    # Columns the snapshot already carries are taken from the snapshot. Merging
    # both sides would suffix them to UnitCost_x / UnitCost_y, `df["UnitCost"]`
    # would then not exist, and `pd.to_numeric(df.get("UnitCost"))` returns a
    # bare NaN scalar rather than raising - so every value on this page would
    # quietly become zero.
    available = [c for c in keep if c in segments.columns and (c == "SKU" or c not in df.columns)]
    df = df.merge(segments[available], on="SKU", how="left")

    for column, default in (("UnitCost", 0.0), ("UnitPrice", 0.0), ("OnHandUnits", 0.0)):
        if column not in df.columns:
            df[column] = default
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(default)
    df["InventoryValueUSD"] = df["OnHandUnits"] * df["UnitCost"]

    obsolete_days = float(params.get("ObsoleteDays", 180))
    slow_days = float(params.get("SlowMoverDays", 90))
    excess_weeks = float(params.get("ExcessCoverWeeks", 26))

    if "WeeksSinceLastShip" not in df.columns:
        df["WeeksSinceLastShip"] = np.nan
    if node_recency is not None and not node_recency.empty:
        # Node-level recency wins where it exists: dead stock is a property
        # of a quantity in a building, not of an item in a catalogue.
        df = df.merge(
            node_recency.rename(columns={"WeeksSinceLastShip": "NodeWeeksSinceLastShip"}),
            on=["SKU", "NodeID"], how="left",
        )
        df["WeeksSinceLastShip"] = df["NodeWeeksSinceLastShip"].fillna(
            df["WeeksSinceLastShip"]
        )
    days_since_ship = pd.to_numeric(df["WeeksSinceLastShip"], errors="coerce") * 7.0
    df["DaysSinceLastShip"] = days_since_ship
    df["IsDeadStock"] = (
        (df["OnHandUnits"] > 0)
        & (days_since_ship.isna() | (days_since_ship >= obsolete_days))
    )
    df["IsSlowMoving"] = (
        (df["OnHandUnits"] > 0)
        & (~df["IsDeadStock"])
        & (days_since_ship >= slow_days)
    )

    # Excess against the plan's own target, where there is one. Falling back to
    # a fixed cover threshold matters: a SKU with no demand has no reorder point,
    # and "excess = on-hand above the reorder point" would then call all of it
    # excess and all of it dead, double-counting the same stock in two reports.
    if plan is not None and not plan.empty:
        target = plan[["SKU", "NodeID", "ReorderPointUnits", "EOQUnits", "DailyDemand"]]
        df = df.merge(target, on=["SKU", "NodeID"], how="left")
    else:
        df["ReorderPointUnits"] = np.nan
        df["EOQUnits"] = np.nan
        df["DailyDemand"] = np.nan

    if "WeeklyDemand" not in df.columns:
        df["WeeklyDemand"] = 0.0
    weekly = pd.to_numeric(df["WeeklyDemand"], errors="coerce").fillna(0.0)
    fallback_target = weekly * excess_weeks
    plan_target = pd.to_numeric(df["ReorderPointUnits"], errors="coerce").fillna(0.0) + pd.to_numeric(
        df["EOQUnits"], errors="coerce"
    ).fillna(0.0)
    df["TargetUnits"] = np.where(plan_target > 0, plan_target, fallback_target)
    df["ExcessUnits"] = (df["OnHandUnits"] - df["TargetUnits"]).clip(lower=0)
    df["ExcessValueUSD"] = df["ExcessUnits"] * df["UnitCost"]
    df["WeeksOfCover"] = np.where(weekly > 0, df["OnHandUnits"] / weekly, np.inf)

    df["ReserveRate"] = reserve_rate(df["AgeDays"])
    # Only stock that is not moving carries a provision. Aged stock on a fast
    # mover is a receiving-date artefact - FIFO is not perfectly enforced in any
    # warehouse - and reserving against it would overstate the write-down.
    df["ReserveRate"] = np.where(df["IsDeadStock"] | df["IsSlowMoving"], df["ReserveRate"], 0.0)
    df["EOReserveUSD"] = df["InventoryValueUSD"] * df["ReserveRate"]

    df["HealthFlag"] = np.select(
        [
            df["IsDeadStock"],
            df["IsSlowMoving"],
            df["ExcessValueUSD"] > 0,
            df["OnHandUnits"] <= 0,
        ],
        ["Dead stock", "Slow moving", "Excess", "Out of stock"],
        default="Healthy",
    )
    return df


def ageing_by_bucket(ageing: pd.DataFrame, group: str | None = None) -> pd.DataFrame:
    """Value and unit counts per age bucket, optionally split by a dimension."""
    if ageing.empty:
        return pd.DataFrame(columns=["AgeBucket", "InventoryValueUSD", "OnHandUnits", "SKUCount"])
    keys = ["AgeBucket"] + ([group] if group else [])
    out = (
        ageing.groupby(keys, as_index=False)
        .agg(
            InventoryValueUSD=("InventoryValueUSD", "sum"),
            OnHandUnits=("OnHandUnits", "sum"),
            SKUCount=("SKU", "nunique"),
        )
    )
    out["AgeBucket"] = pd.Categorical(out["AgeBucket"], categories=AGE_BUCKET_ORDER, ordered=True)
    return out.sort_values(keys, kind="stable")


def dead_stock_register(ageing: pd.DataFrame, top: int | None = None) -> pd.DataFrame:
    """Dead and slow stock, biggest liability first."""
    if ageing.empty:
        return pd.DataFrame()
    dead = ageing[ageing["IsDeadStock"] | ageing["IsSlowMoving"]].copy()
    dead = dead.sort_values(["InventoryValueUSD", "SKU"], ascending=[False, True], kind="stable")
    columns = [c for c in (
        "SKU", "ItemDescription", "Department", "NodeID", "ABCClass", "MovementClass",
        "OnHandUnits", "UnitCost", "InventoryValueUSD", "AgeDays", "AgeBucket",
        "DaysSinceLastShip", "ReserveRate", "EOReserveUSD", "HealthFlag", "Lifecycle",
    ) if c in dead.columns]
    return dead[columns].head(top) if top else dead[columns]


__all__ = [
    "AGE_BUCKETS",
    "AGE_BUCKET_ORDER",
    "EO_RESERVE_LADDER",
    "age_bucket",
    "ageing_by_bucket",
    "build_ageing",
    "dead_stock_register",
    "reserve_rate",
]
