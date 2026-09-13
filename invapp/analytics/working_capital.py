"""
What the inventory is worth, what holding it costs, and how long it sits.

Carrying cost is split four ways rather than quoted as one percentage, because
the four components respond to different levers and a single blended rate hides
which one to pull. Capital cost falls when the cost of capital falls or when
stock falls. Storage cost only falls if the space is actually given back -
halving the stock in a leased building saves nothing until the lease changes.
Service cost tracks value. Risk cost tracks age and obsolescence, and it is the
one that dead stock drives.

Days inventory outstanding is computed on cost of goods sold, not revenue.
Dividing inventory at cost by revenue mixes two bases and understates DIO by
whatever the gross margin is - about a third, here, which turns 72 days into
48 and makes the number look like someone else's business.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CARRYING_COMPONENTS = ("Capital", "Storage", "Service", "Risk")


def carrying_cost(
    inventory_value: float,
    params: dict,
    *,
    risk_weighted_value: float | None = None,
) -> pd.DataFrame:
    """The annual cost of holding this inventory, split by component.

    `risk_weighted_value` lets the risk component be charged against aged and
    slow stock rather than against everything, which is what actually happens:
    a SKU that turns fourteen times a year carries essentially no obsolescence
    risk and charging it the same three percent as a dead one is arithmetic,
    not accounting.
    """
    rates = {
        "Capital": float(params.get("CapitalRate", 0.09)),
        "Storage": float(params.get("StorageRate", 0.08)),
        "Service": float(params.get("ServiceRate", 0.04)),
        "Risk": float(params.get("RiskRate", 0.03)),
    }
    risk_base = inventory_value if risk_weighted_value is None else risk_weighted_value
    rows = []
    for component in CARRYING_COMPONENTS:
        base = risk_base if component == "Risk" else inventory_value
        rows.append({
            "Component": component,
            "Rate": rates[component],
            "BaseValueUSD": base,
            "AnnualCostUSD": base * rates[component],
        })
    frame = pd.DataFrame(rows)
    frame["SharePct"] = frame["AnnualCostUSD"] / frame["AnnualCostUSD"].sum() if frame[
        "AnnualCostUSD"
    ].sum() else np.nan
    return frame


def working_capital_summary(
    inventory_value: float,
    annual_cogs: float,
    annual_revenue: float,
    params: dict,
    *,
    excess_value: float = 0.0,
    dead_value: float = 0.0,
    reserve_value: float = 0.0,
) -> dict:
    """The headline working-capital numbers, in one dict.

    Turns and DIO are reciprocal by construction, and both are reported because
    operations quotes turns and finance quotes days, and a report that only
    speaks one of those dialects gets translated wrong in the meeting.
    """
    carrying_rate = float(params.get("CarryingCostRate", 0.24))
    turns = annual_cogs / inventory_value if inventory_value > 0 else float("nan")
    dio = inventory_value / annual_cogs * 365.0 if annual_cogs > 0 else float("nan")
    gross_margin = annual_revenue - annual_cogs

    return {
        "InventoryValueUSD": inventory_value,
        "AnnualCOGSUSD": annual_cogs,
        "AnnualRevenueUSD": annual_revenue,
        "GrossMarginUSD": gross_margin,
        "GrossMarginPct": gross_margin / annual_revenue if annual_revenue > 0 else float("nan"),
        "InventoryTurns": turns,
        "DaysInventoryOutstanding": dio,
        "CarryingCostUSD": inventory_value * carrying_rate,
        "CarryingCostRate": carrying_rate,
        "GMROI": gross_margin / inventory_value if inventory_value > 0 else float("nan"),
        "ExcessValueUSD": excess_value,
        "DeadStockValueUSD": dead_value,
        "EOReserveUSD": reserve_value,
        # What the excess is costing to hold, per year. The sentence a finance
        # partner acts on: not "you have $1.4m of excess" but "$1.4m of excess
        # is costing $336k a year to keep".
        "ExcessCarryingCostUSD": excess_value * carrying_rate,
        "HealthyValueUSD": max(inventory_value - excess_value - dead_value, 0.0),
        "CashReleaseOpportunityUSD": excess_value + dead_value,
    }


def value_by_dimension(
    ageing: pd.DataFrame,
    dimension: str,
    *,
    params: dict | None = None,
) -> pd.DataFrame:
    """Inventory value, excess and DIO split by any dimension on the frame."""
    if ageing is None or ageing.empty or dimension not in ageing.columns:
        return pd.DataFrame(columns=[dimension, "InventoryValueUSD", "ExcessValueUSD",
                                     "DeadStockValueUSD", "OnHandUnits", "SKUCount"])
    params = params or {}
    out = (
        ageing.assign(
            DeadValueUSD=np.where(ageing["IsDeadStock"], ageing["InventoryValueUSD"], 0.0),
        )
        .groupby(dimension, as_index=False)
        .agg(
            InventoryValueUSD=("InventoryValueUSD", "sum"),
            ExcessValueUSD=("ExcessValueUSD", "sum"),
            DeadStockValueUSD=("DeadValueUSD", "sum"),
            OnHandUnits=("OnHandUnits", "sum"),
            SKUCount=("SKU", "nunique"),
        )
    )
    rate = float(params.get("CarryingCostRate", 0.24))
    out["CarryingCostUSD"] = out["InventoryValueUSD"] * rate
    out["ExcessSharePct"] = out["ExcessValueUSD"] / out["InventoryValueUSD"].replace(0, np.nan)
    return out.sort_values(["InventoryValueUSD", dimension], ascending=[False, True], kind="stable")


def inventory_trend(
    demand: pd.DataFrame,
    items: pd.DataFrame,
    purchase_orders: pd.DataFrame,
    closing_value: float,
    *,
    weeks: int = 26,
) -> pd.DataFrame:
    """Inventory value and days on hand week by week, rolled back from today.

    A warehouse system gives you one snapshot, not a history of them, so the
    series has to be reconstructed. It is reconstructed from *facts*: every
    week's closing balance is the next week's opening balance plus that week's
    shipments minus that week's receipts, and both of those are in the data -
    shipments in the demand history, receipts in the purchase orders on the
    date they were actually received.

    An earlier version assumed receipts matched a trailing average of
    shipments. That is what a system in steady state does, and it produced a
    dead-flat line: the assumption *was* the answer. Real receipts are lumpy,
    so the reconstruction now has the shape that makes it worth plotting.

    It is still a reconstruction, and the only week it is exactly right is the
    last one. What it is not is a straight line through two points called a
    trend.
    """
    columns = ["WeekEnding", "COGSUSD", "ReceiptsUSD", "InventoryValueUSD",
               "DaysInventoryOutstanding", "InventoryTurns"]
    if demand is None or demand.empty:
        return pd.DataFrame(columns=columns)

    cost = dict(zip(items["SKU"], pd.to_numeric(items["UnitCost"], errors="coerce").fillna(0.0),
                    strict=True))
    d = demand.copy()
    d["WeekEnding"] = pd.to_datetime(d["WeekEnding"], errors="coerce")
    d["COGSUSD"] = pd.to_numeric(d["UnitsShipped"], errors="coerce").fillna(0.0) * d["SKU"].map(
        cost
    ).fillna(0.0)
    weekly = d.groupby("WeekEnding", as_index=False)["COGSUSD"].sum().sort_values("WeekEnding")
    weekly = weekly.tail(weeks).reset_index(drop=True)
    if weekly.empty:
        return pd.DataFrame(columns=columns)

    weekly["ReceiptsUSD"] = 0.0
    if purchase_orders is not None and not purchase_orders.empty:
        po = purchase_orders.copy()
        po["ReceivedDate"] = pd.to_datetime(po.get("ReceivedDate"), errors="coerce")
        po = po.dropna(subset=["ReceivedDate"])
        if not po.empty:
            po["ValueUSD"] = (
                pd.to_numeric(po["QtyReceived"], errors="coerce").fillna(0.0)
                * pd.to_numeric(po["UnitCost"], errors="coerce").fillna(0.0)
            )
            # Receipts land on the week that closes on or after the receipt, so
            # they sit in the same bucket as the shipments they are netted
            # against.
            edges = weekly["WeekEnding"]
            # Receipts older than the window are dropped, not bucketed into the
            # first week. searchsorted puts everything below the first edge at
            # index 0, so without this a 26-week window opens with fifteen
            # months of receipts on its first bar and the balance walks
            # backwards to a number nothing supports.
            first_week_opens = edges.iloc[0] - pd.Timedelta(days=7)
            po = po[(po["ReceivedDate"] > first_week_opens) & (po["ReceivedDate"] <= edges.iloc[-1])]
            if po.empty:
                po = po.assign(WeekIndex=pd.Series(dtype=int))
            else:
                index = np.searchsorted(
                    edges.to_numpy(), po["ReceivedDate"].to_numpy(), side="left"
                )
                po = po.assign(WeekIndex=index)
                po = po[(po["WeekIndex"] >= 0) & (po["WeekIndex"] < len(weekly))]
            by_week = po.groupby("WeekIndex")["ValueUSD"].sum()
            weekly.loc[by_week.index, "ReceiptsUSD"] = by_week.to_numpy()

    # Walk backwards: this week's opening is its closing plus what shipped out
    # minus what came in.
    balances = np.empty(len(weekly))
    running = float(closing_value)
    for i in range(len(weekly) - 1, -1, -1):
        balances[i] = running
        running = running + weekly.loc[i, "COGSUSD"] - weekly.loc[i, "ReceiptsUSD"]
    weekly["InventoryValueUSD"] = np.maximum(balances, 0.0)

    # Annualised on a trailing 13-week average rather than on the single week,
    # or one quiet week reports the network as holding a year of stock.
    trailing = weekly["COGSUSD"].rolling(13, min_periods=4).mean() * 52.0
    weekly["InventoryTurns"] = np.where(
        weekly["InventoryValueUSD"] > 0, trailing / weekly["InventoryValueUSD"], np.nan
    )
    weekly["DaysInventoryOutstanding"] = np.where(
        trailing > 0, weekly["InventoryValueUSD"] / trailing * 365.0, np.nan
    )
    return weekly[columns]


__all__ = [
    "CARRYING_COMPONENTS",
    "carrying_cost",
    "inventory_trend",
    "value_by_dimension",
    "working_capital_summary",
]
