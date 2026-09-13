"""
Where stock is against where demand is, and what to move.

The condition this looks for is a network holding the right total quantity in
the wrong places. A purchase order fixes a shortage in six weeks and costs the
unit price; a transfer fixes it in three days and costs freight. Anywhere both
would work, the transfer wins - so the transfer list has to be produced before
the buy list, or the buy list orders stock the network already owns.

The matching is greedy: for each SKU, sort deficits by how badly they need
stock and surpluses by how much they can spare, then pair them off. Greedy
rather than optimal on purpose. The optimal version is a transportation problem
per SKU, it needs a solver, and it produces a marginally cheaper answer to a
question whose inputs - next week's demand - are themselves an estimate. What
matters is that a move is only recommended when it pays, and that test is
applied per move regardless of how the pairs were chosen.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Freight cost per pound, by how far apart two regions are. A real network
# prices this per lane from a carrier tariff; here it is derived from the
# node dimension's own Region column so the analytics never has to know a
# node ID, and a workbook with different nodes still gets sensible freight.
REGION_ORDER = ("West", "Prairies", "Central", "East", "North", "International")
BASE_LANE_COST_PER_LB = 0.24
LANE_COST_PER_REGION_STEP = 0.21


def lane_cost_table(nodes: pd.DataFrame) -> dict[tuple[str, str], float]:
    """Cost per pound between every pair of nodes."""
    if nodes is None or nodes.empty or "NodeID" not in nodes.columns:
        return {}
    regions = dict(zip(nodes["NodeID"].astype(str), nodes.get("Region", "").astype(str),
                       strict=False))

    def index_of(node: str) -> int:
        region = regions.get(node, "")
        return REGION_ORDER.index(region) if region in REGION_ORDER else 0

    table: dict[tuple[str, str], float] = {}
    ids = [str(n) for n in nodes["NodeID"]]
    for origin in ids:
        for destination in ids:
            if origin == destination:
                table[(origin, destination)] = 0.0
            else:
                gap = abs(index_of(origin) - index_of(destination))
                table[(origin, destination)] = round(
                    BASE_LANE_COST_PER_LB + LANE_COST_PER_REGION_STEP * gap, 3
                )
    return table


# A transfer has to earn its freight by this multiple before it is recommended.
# At 1.0 the report fills with moves worth a dollar each; the threshold is what
# makes the list short enough to action.
MIN_BENEFIT_RATIO = 1.5

# Below this many units a move is not worth a pick, a pack and a manifest line
# whatever the arithmetic says.
MIN_TRANSFER_UNITS = 6


def node_balance(plan: pd.DataFrame) -> pd.DataFrame:
    """Cover and position per SKU and node, with the network for comparison.

    `CoverGapWeeks` is the node's cover minus the network's for that SKU: the
    single number that says "this node holds four months and that one holds
    four days of the same item".
    """
    if plan is None or plan.empty:
        return pd.DataFrame()

    df = plan.copy()
    network = (
        df.groupby("SKU", as_index=False)
        .agg(
            NetworkPositionUnits=("InventoryPosition", "sum"),
            NetworkDailyDemand=("DailyDemand", "sum"),
            NetworkSafetyStock=("SafetyStockUnits", "sum"),
        )
    )
    df = df.merge(network, on="SKU", how="left")
    df["NetworkDaysOfCover"] = np.where(
        df["NetworkDailyDemand"] > 0,
        df["NetworkPositionUnits"] / df["NetworkDailyDemand"],
        np.inf,
    )
    df["CoverGapWeeks"] = (df["DaysOfCover"] - df["NetworkDaysOfCover"]) / 7.0
    df["DeficitUnits"] = (df["ReorderPointUnits"] - df["InventoryPosition"]).clip(lower=0)
    # Surplus is measured against the reorder point plus one order quantity -
    # the most a node should be holding under its own policy. Measuring it
    # against the reorder point alone would call every node that just received
    # a delivery a surplus.
    df["SurplusUnits"] = (
        df["InventoryPosition"] - (df["ReorderPointUnits"] + df["EOQUnits"])
    ).clip(lower=0)
    return df


def recommend_transfers(
    balance: pd.DataFrame,
    params: dict,
    *,
    nodes: pd.DataFrame | None = None,
    min_units: int = MIN_TRANSFER_UNITS,
    min_benefit_ratio: float = MIN_BENEFIT_RATIO,
) -> pd.DataFrame:
    """Pair surplus nodes with deficit nodes, SKU by SKU, and price the move."""
    columns = ["SKU", "ItemDescription", "Department", "ABCClass", "FromNode", "ToNode",
               "TransferUnits", "TransferValueUSD", "FreightCostUSD", "BenefitUSD",
               "BenefitRatio", "FromCoverWeeksBefore", "ToCoverWeeksBefore",
               "ToCoverWeeksAfter", "Rationale"]
    if balance is None or balance.empty:
        return pd.DataFrame(columns=columns)

    penalty = float(params.get("StockoutPenaltyPerUnit", 6.5))
    carrying_rate = float(params.get("CarryingCostRate", 0.24))
    lanes = lane_cost_table(nodes) if nodes is not None else {}
    default_lane = BASE_LANE_COST_PER_LB + LANE_COST_PER_REGION_STEP
    rows: list[dict] = []

    needed = balance[(balance["DeficitUnits"] > 0) | (balance["SurplusUnits"] > 0)]
    for sku, frame in needed.groupby("SKU", sort=True):
        deficits = frame[frame["DeficitUnits"] > 0].sort_values(
            ["StockoutExposureUSD", "NodeID"], ascending=[False, True], kind="stable"
        )
        surpluses = frame[frame["SurplusUnits"] > 0].sort_values(
            ["SurplusUnits", "NodeID"], ascending=[False, True], kind="stable"
        )
        if deficits.empty or surpluses.empty:
            continue

        remaining = {row.NodeID: float(row.SurplusUnits) for row in surpluses.itertuples(index=False)}
        for want in deficits.itertuples(index=False):
            need = float(want.DeficitUnits)
            for give in surpluses.itertuples(index=False):
                if need < min_units:
                    break
                available = remaining.get(give.NodeID, 0.0)
                if available < min_units:
                    continue

                units = float(np.floor(min(need, available)))
                if units < min_units:
                    continue

                weight = float(getattr(want, "UnitWeightLb", 1.0) or 1.0)
                lane = lanes.get((str(give.NodeID), str(want.NodeID)), default_lane)
                freight = lane * weight * units
                # The move is worth the shortfall it prevents at the receiving
                # node, plus the carrying cost released at the sending one for
                # the rest of the year.
                margin = max(float(want.UnitPrice) - float(want.UnitCost), penalty)
                units_short_prevented = min(units, float(want.ExpectedUnitsShort))
                benefit = units_short_prevented * margin
                benefit += units * float(give.UnitCost) * carrying_rate * 0.5
                ratio = benefit / freight if freight > 0 else np.inf

                if ratio < min_benefit_ratio:
                    continue

                daily = float(want.DailyDemand)
                rows.append({
                    "SKU": sku,
                    "ItemDescription": getattr(want, "ItemDescription", ""),
                    "Department": getattr(want, "Department", ""),
                    "ABCClass": getattr(want, "ABCClass", ""),
                    "FromNode": str(give.NodeID),
                    "ToNode": str(want.NodeID),
                    "TransferUnits": units,
                    "TransferValueUSD": units * float(want.UnitCost),
                    "FreightCostUSD": round(freight, 2),
                    "BenefitUSD": round(benefit, 2),
                    "BenefitRatio": round(float(ratio), 2) if np.isfinite(ratio) else None,
                    "FromCoverWeeksBefore": float(give.WeeksOfCover),
                    "ToCoverWeeksBefore": float(want.WeeksOfCover),
                    "ToCoverWeeksAfter": (
                        (float(want.InventoryPosition) + units) / daily / 7.0 if daily > 0 else np.inf
                    ),
                    "Rationale": (
                        f"{give.NodeID} holds {give.WeeksOfCover:.0f} weeks, "
                        f"{want.NodeID} holds {want.WeeksOfCover:.1f}"
                    ),
                })
                remaining[give.NodeID] = available - units
                need -= units

    out = pd.DataFrame(rows, columns=columns)
    if out.empty:
        return out
    return out.sort_values(["BenefitUSD", "SKU"], ascending=[False, True], kind="stable")


def node_summary(balance: pd.DataFrame, nodes: pd.DataFrame) -> pd.DataFrame:
    """One row per node: what it holds, what it owes, how balanced it is."""
    if balance is None or balance.empty:
        return pd.DataFrame()

    df = balance.copy()
    df["InventoryValueUSD"] = df["OnHandUnits"] * df["UnitCost"]
    df["CubeFt"] = df["OnHandUnits"] * df.get("UnitCubeFt", 0.0)
    summary = (
        df.groupby("NodeID", as_index=False)
        .agg(
            SKUCount=("SKU", "nunique"),
            OnHandUnits=("OnHandUnits", "sum"),
            InventoryValueUSD=("InventoryValueUSD", "sum"),
            SafetyStockUnits=("SafetyStockUnits", "sum"),
            BelowReorderSKUs=("Urgency", lambda s: int((s != "Healthy").sum())),
            StockedOutSKUs=("Urgency", lambda s: int((s == "Stocked out").sum())),
            StockoutExposureUSD=("StockoutExposureUSD", "sum"),
            RecommendedOrderValueUSD=("RecommendedOrderValue", "sum"),
            CubeFt=("CubeFt", "sum"),
        )
    )
    summary["StockoutRatePct"] = summary["StockedOutSKUs"] / summary["SKUCount"].replace(0, np.nan)

    if nodes is not None and not nodes.empty:
        cols = [c for c in ("NodeID", "NodeName", "Region", "City", "NodeType",
                            "StorageCapacityCubeFt", "DemandShare") if c in nodes.columns]
        summary = summary.merge(nodes[cols], on="NodeID", how="left")
        if "StorageCapacityCubeFt" in summary.columns:
            summary["CapacityUsedPct"] = summary["CubeFt"] / summary[
                "StorageCapacityCubeFt"
            ].replace(0, np.nan)

    # Value share against demand share: a node holding a quarter of the stock
    # to serve a twentieth of the demand is the finding, and it is invisible in
    # either column on its own.
    total_value = float(summary["InventoryValueUSD"].sum())
    summary["ValueSharePct"] = summary["InventoryValueUSD"] / total_value if total_value else np.nan
    if "DemandShare" in summary.columns:
        summary["ShareGapPct"] = summary["ValueSharePct"] - summary["DemandShare"]
    return summary.sort_values("NodeID", kind="stable")


__all__ = [
    "BASE_LANE_COST_PER_LB",
    "LANE_COST_PER_REGION_STEP",
    "MIN_BENEFIT_RATIO",
    "MIN_TRANSFER_UNITS",
    "lane_cost_table",
    "node_balance",
    "node_summary",
    "recommend_transfers",
]
