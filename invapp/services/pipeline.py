"""
One workbook in, every report out.

This is the only place that knows the order things have to be computed in, and
the order matters more than it looks:

1. Supplier lead times come from receipts, so purchase orders are read before
   anything that plans against a lead time.
2. Open orders give in-transit quantity, which is part of inventory position,
   which is what the reorder decision is made on - so they are read before the
   snapshot is prepared.
3. ABC class sets the service level target, which sets ``z``, which sets safety
   stock - so segmentation runs before replenishment, not after it.
4. The replenishment plan defines each node's target stock, which is what
   "excess" is measured against - so ageing runs after the plan.
5. Transfers have to be produced before the buy list is read, or the buy list
   orders stock the network already owns three provinces away.

Everything is computed once, on ingest, and held as frames. The alternative -
recomputing per request - would put a full forecast backtest behind every page
load, which on this dataset is several seconds of CPU per visitor.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from invapp.analytics import (
    accuracy,
    actions,
    ageing,
    allocation,
    demand as demand_mod,
    replenishment,
    segmentation,
    supplier as supplier_mod,
    working_capital,
)
from invapp.services import cleaning

logger = logging.getLogger(__name__)

# Used when the workbook carries no Planning Parameters sheet. Every one of
# these is a policy the business owns; they are defaults, not constants.
DEFAULT_PARAMS: dict[str, float] = {
    "ReviewPeriodDays": 7,
    "CarryingCostRate": 0.24,
    "CapitalRate": 0.09,
    "StorageRate": 0.08,
    "ServiceRate": 0.04,
    "RiskRate": 0.03,
    "ServiceLevelA": 0.98,
    "ServiceLevelB": 0.95,
    "ServiceLevelC": 0.90,
    "SlowMoverDays": 90,
    "ObsoleteDays": 180,
    "ExcessCoverWeeks": 26,
    "CountToleranceUnits": 0,
    "OnTimeGraceDays": 2,
    "StockoutPenaltyPerUnit": 6.5,
    "OrderLineCostUSD": 45.0,
    "OrderLinesPerPO": 12.0,
    "MinOrderCycleDays": 7,
    "MaxOrderCycleDays": 182,
    "WorkingDaysPerYear": 364,
}


@dataclass
class AnalysisModel:
    """Every frame the app serves, computed once from one workbook."""

    as_of: pd.Timestamp
    params: dict[str, float] = field(default_factory=dict)

    # Dimensions and cleaned facts
    items: pd.DataFrame = field(default_factory=pd.DataFrame)
    suppliers: pd.DataFrame = field(default_factory=pd.DataFrame)
    nodes: pd.DataFrame = field(default_factory=pd.DataFrame)
    demand: pd.DataFrame = field(default_factory=pd.DataFrame)
    inventory: pd.DataFrame = field(default_factory=pd.DataFrame)
    purchase_orders: pd.DataFrame = field(default_factory=pd.DataFrame)
    adjustments: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Derived
    sku_stats: pd.DataFrame = field(default_factory=pd.DataFrame)
    node_stats: pd.DataFrame = field(default_factory=pd.DataFrame)
    forecast_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    forecast: pd.DataFrame = field(default_factory=pd.DataFrame)
    forecast_fit: pd.DataFrame = field(default_factory=pd.DataFrame)
    segments: pd.DataFrame = field(default_factory=pd.DataFrame)
    plan: pd.DataFrame = field(default_factory=pd.DataFrame)
    eoq: pd.DataFrame = field(default_factory=pd.DataFrame)
    service_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    ageing: pd.DataFrame = field(default_factory=pd.DataFrame)
    balance: pd.DataFrame = field(default_factory=pd.DataFrame)
    transfers: pd.DataFrame = field(default_factory=pd.DataFrame)
    node_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    scorecard: pd.DataFrame = field(default_factory=pd.DataFrame)
    open_pos: pd.DataFrame = field(default_factory=pd.DataFrame)
    count_variance: pd.DataFrame = field(default_factory=pd.DataFrame)
    register: pd.DataFrame = field(default_factory=pd.DataFrame)
    carrying: pd.DataFrame = field(default_factory=pd.DataFrame)
    trend: pd.DataFrame = field(default_factory=pd.DataFrame)
    headline: dict[str, Any] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return self.plan is None or self.plan.empty


def _weekly_cogs(demand: pd.DataFrame, items: pd.DataFrame) -> tuple[float, float]:
    """Annualised COGS and revenue from the shipment history."""
    if demand.empty:
        return 0.0, 0.0
    cost = dict(zip(items["SKU"], items["UnitCost"], strict=True))
    price = dict(zip(items["SKU"], items["UnitPrice"], strict=True))
    units = demand["UnitsShipped"]
    weeks = max(demand["WeekEnding"].nunique(), 1)
    cogs = float((units * demand["SKU"].map(cost).fillna(0.0)).sum()) * (52.0 / weeks)
    revenue = float((units * demand["SKU"].map(price).fillna(0.0)).sum()) * (52.0 / weeks)
    return cogs, revenue


def build_model(sheets: dict[str, pd.DataFrame]) -> AnalysisModel:
    """Run the whole analysis. Raises ValueError if a required sheet is empty."""
    started = time.perf_counter()

    clean = cleaning.align_ids(cleaning.clean_sheets(sheets))
    missing = cleaning.missing_required(clean)
    if missing:
        raise ValueError(
            "The workbook is missing data the app cannot work without: "
            + ", ".join(missing)
        )

    as_of = cleaning.as_of_date(clean)
    params = cleaning.planning_parameters(clean["Planning Parameters"], DEFAULT_PARAMS)

    items = clean["Item Master"].drop_duplicates(subset=["SKU"], keep="first")
    suppliers = clean["Supplier Master"].drop_duplicates(subset=["SupplierID"], keep="first")
    nodes = clean["Network Nodes"].drop_duplicates(subset=["NodeID"], keep="first")
    demand = clean["Demand History"]
    purchase_orders = clean["Purchase Orders"]
    adjustments = clean["Inventory Adjustments"]

    # --- 1. Supplier lead times, from receipts ------------------------------
    lead_times = supplier_mod.lead_time_facts(purchase_orders)
    if suppliers is not None and not suppliers.empty:
        contracted = suppliers[["SupplierID", "LeadTimeDays", "OrderCostUSD"]].rename(
            columns={"LeadTimeDays": "ContractSheetDays"}
        )
        lead_times = lead_times.merge(contracted, on="SupplierID", how="outer")
        # The contract sheet wins where it exists: it is what the buyer agreed,
        # and deriving it from promised-minus-ordered dates on the PO is a
        # reconstruction of the same fact with rounding in it.
        lead_times["LeadTimeDaysContract"] = lead_times["ContractSheetDays"].fillna(
            lead_times["LeadTimeDaysContract"]
        )
        lead_times = lead_times.drop(columns=["ContractSheetDays"])
    for column, default in (("LeadTimeDaysActual", 14.0), ("LeadTimeDaysStdDev", 3.0),
                            ("LeadTimeDaysContract", 14.0), ("OrderCostUSD", 250.0)):
        if column not in lead_times.columns:
            lead_times[column] = default
        lead_times[column] = pd.to_numeric(lead_times[column], errors="coerce").fillna(default)

    # --- 2. Open orders and in-transit --------------------------------------
    open_pos = supplier_mod.open_purchase_orders(purchase_orders, as_of)
    in_transit = supplier_mod.in_transit_by_node(open_pos)
    inventory = cleaning.prepare_inventory(clean["Inventory Snapshot"], in_transit, as_of)

    # --- 3. Demand series and statistics ------------------------------------
    grid = demand_mod.week_grid(demand)
    sku_matrix = demand_mod.weekly_matrix(demand, grid, keys=["SKU"])
    node_matrix = demand_mod.weekly_matrix(demand, grid, keys=["SKU", "NodeID"])
    sku_stats = demand_mod.demand_statistics(sku_matrix)
    node_stats = demand_mod.demand_statistics(node_matrix)

    # --- 4. Forecast at SKU level, allocate to nodes ------------------------
    forecast_summary, forecast = demand_mod.forecast_skus(sku_matrix)
    forecast_fit = demand_mod.fitted_history(sku_matrix, forecast_summary)
    shares = demand_mod.node_share(node_matrix)

    # --- 5. Segmentation ----------------------------------------------------
    by_sku = cleaning.inventory_by_sku(inventory)
    segments = segmentation.build_segments(sku_stats, items, by_sku, params)
    if not segments.empty and not forecast_summary.empty:
        segments = segments.merge(
            forecast_summary[["SKU", "Method", "MethodLabel", "ForecastAccuracy", "Bias",
                              "ForecastWeekly"]],
            on="SKU", how="left",
        )

    # --- 6. Replenishment ---------------------------------------------------
    # The node-level forecast is the SKU forecast times the node's recent share,
    # so the node plans add back up to what the buyer is ordering.
    node_stats = node_stats.copy()
    node_stats["NodeShare"] = shares.reindex(node_stats.index).fillna(0.0)
    if not forecast_summary.empty:
        sku_forecast = forecast_summary.set_index("SKU")["ForecastWeekly"]
        node_stats["ForecastWeekly"] = (
            node_stats.index.get_level_values("SKU").map(sku_forecast).to_numpy()
            * node_stats["NodeShare"].to_numpy()
        )
    else:
        node_stats["ForecastWeekly"] = node_stats["WeeklyDemand"]

    # Planning demand is the forecast where there is one, the trailing mean
    # otherwise. Using the trailing mean everywhere would plan the Christmas
    # peak on November's average, which is the single most expensive thing a
    # replenishment system can get wrong in retail.
    planning_weekly = node_stats["ForecastWeekly"].fillna(node_stats["WeeklyDemand"])
    node_stats["PlannedWeeklyDemand"] = planning_weekly
    node_stats["DailyDemand"] = planning_weekly / 7.0
    node_stats["AnnualDemand"] = planning_weekly * 52.0

    plan = replenishment.build_plan(node_stats, items, inventory, lead_times, segments, params)
    eoq = replenishment.eoq_comparison(plan, purchase_orders, params)
    service_curve = replenishment.service_level_curve(plan)

    # --- 7. Ageing and dead stock ------------------------------------------
    # Recency is taken per SKU *and node*. At SKU level almost nothing is ever
    # dead - a national catalogue ships something somewhere most weeks - while
    # the stock actually stranded is stranded in one building, on an item the
    # rest of the network is still selling.
    aged = ageing.build_ageing(
        inventory, segments, plan, params,
        node_recency=node_stats[["WeeksSinceLastShip"]].reset_index(),
    )

    # --- 8. Network balance and transfers -----------------------------------
    balance = allocation.node_balance(plan)
    transfers = allocation.recommend_transfers(balance, params, nodes=nodes)
    node_summary = allocation.node_summary(
        balance.merge(items[["SKU", "UnitCubeFt"]], on="SKU", how="left"), nodes
    )

    # --- 9. Supplier scorecard and count accuracy ---------------------------
    scorecard = supplier_mod.build_scorecard(purchase_orders, suppliers, params)
    variance = accuracy.build_count_variance(clean["Cycle Counts"], items, params)

    # --- 10. Working capital ------------------------------------------------
    inventory_value = float(inventory["InventoryValueUSD"].sum()) if not inventory.empty else 0.0
    annual_cogs, annual_revenue = _weekly_cogs(demand, items)
    excess_value = float(aged["ExcessValueUSD"].sum()) if not aged.empty else 0.0
    dead_value = (
        float(aged.loc[aged["IsDeadStock"], "InventoryValueUSD"].sum()) if not aged.empty else 0.0
    )
    reserve_value = float(aged["EOReserveUSD"].sum()) if not aged.empty else 0.0
    slow_value = (
        float(aged.loc[aged["IsSlowMoving"] | aged["IsDeadStock"], "InventoryValueUSD"].sum())
        if not aged.empty else 0.0
    )

    carrying = working_capital.carrying_cost(
        inventory_value, params, risk_weighted_value=slow_value
    )
    trend = working_capital.inventory_trend(
        demand, items, purchase_orders, inventory_value
    )
    summary = working_capital.working_capital_summary(
        inventory_value, annual_cogs, annual_revenue, params,
        excess_value=excess_value, dead_value=dead_value, reserve_value=reserve_value,
    )

    # --- 11. The action register -------------------------------------------
    register = actions.build_register(
        plan, transfers, aged, variance, open_pos, scorecard, params
    )

    headline = _headline(summary, plan, aged, variance, demand, forecast_summary, segments, register)
    headline["as_of"] = as_of.date().isoformat()
    headline["elapsed_ms"] = int((time.perf_counter() - started) * 1000)

    logger.info("pipeline.built", extra={"skus": len(items), "ms": headline["elapsed_ms"]})

    return AnalysisModel(
        as_of=as_of, params=params,
        items=items, suppliers=suppliers, nodes=nodes, demand=demand,
        inventory=inventory, purchase_orders=purchase_orders, adjustments=adjustments,
        sku_stats=sku_stats.reset_index(), node_stats=node_stats.reset_index(),
        forecast_summary=forecast_summary, forecast=forecast, forecast_fit=forecast_fit,
        segments=segments, plan=plan, eoq=eoq, service_curve=service_curve,
        ageing=aged, balance=balance, transfers=transfers, node_summary=node_summary,
        scorecard=scorecard, open_pos=open_pos, count_variance=variance,
        register=register, carrying=carrying, trend=trend, headline=headline,
    )


def _headline(
    summary: dict,
    plan: pd.DataFrame,
    aged: pd.DataFrame,
    variance: pd.DataFrame,
    demand: pd.DataFrame,
    forecast_summary: pd.DataFrame,
    segments: pd.DataFrame,
    register: pd.DataFrame,
) -> dict[str, Any]:
    """The numbers the overview page leads with."""
    out = dict(summary)

    if not plan.empty:
        out["SKUCount"] = int(plan["SKU"].nunique())
        out["StockedLocations"] = int(len(plan))
        out["StockoutCount"] = int((plan["Urgency"] == "Stocked out").sum())
        out["BelowReorderCount"] = int((plan["Urgency"] != "Healthy").sum())
        out["ReorderValueUSD"] = float(plan["RecommendedOrderValue"].sum())
        out["StockoutExposureUSD"] = float(plan["StockoutExposureUSD"].sum())
        out["SafetyStockValueUSD"] = float(
            (plan["SafetyStockUnits"] * plan["UnitCost"]).sum()
        )
        # The median stocked location, not the mean. Cover has an unbounded
        # right tail - a dead SKU with stock on it has infinite cover - so a
        # mean is dragged wherever the tail goes, and a value-weighted mean is
        # an arithmetic mean of a ratio, which does not reconcile to days
        # inventory outstanding no matter how carefully it is computed. The
        # median says "the typical location"; DIO says "the whole business".
        finite = np.isfinite(plan["DaysOfCover"])
        out["MedianDaysOfCover"] = (
            float(plan.loc[finite, "DaysOfCover"].median()) if finite.any() else float("nan")
        )
    else:
        out.update({"SKUCount": 0, "StockedLocations": 0, "StockoutCount": 0,
                    "BelowReorderCount": 0, "ReorderValueUSD": 0.0,
                    "StockoutExposureUSD": 0.0, "SafetyStockValueUSD": 0.0,
                    "MedianDaysOfCover": float("nan")})

    if not demand.empty:
        requested = float(demand["UnitsRequested"].sum())
        shipped = float(demand["UnitsShipped"].sum())
        out["FillRatePct"] = shipped / requested if requested > 0 else float("nan")
        out["UnitsShipped"] = shipped
        out["WeeksOfHistory"] = int(demand["WeekEnding"].nunique())
    else:
        out.update({"FillRatePct": float("nan"), "UnitsShipped": 0.0, "WeeksOfHistory": 0})

    if not forecast_summary.empty:
        # Accuracy weighted by volume: a 40% error on a SKU shipping three a
        # month should not sit alongside a 4% error on one shipping thousands.
        weights = forecast_summary["ForecastWeekly"].clip(lower=0)
        acc = forecast_summary["ForecastAccuracy"]
        usable = acc.notna() & (weights > 0)
        out["ForecastAccuracyPct"] = (
            float((acc[usable] * weights[usable]).sum() / weights[usable].sum())
            if usable.any() else float("nan")
        )
        out["ForecastBiasPct"] = float(forecast_summary["Bias"].median())
    else:
        out.update({"ForecastAccuracyPct": float("nan"), "ForecastBiasPct": float("nan")})

    if not variance.empty:
        records = len(variance)
        out["RecordAccuracyPct"] = float(variance["IsAccurate"].sum() / records)
        system_value = float(variance["SystemValueUSD"].sum())
        out["ValueAccuracyPct"] = (
            1.0 - float(variance["AbsVarianceValueUSD"].sum()) / system_value
            if system_value else float("nan")
        )
        out["NetShrinkageUSD"] = -float(variance["VarianceValueUSD"].sum())
        out["CountsTaken"] = records
    else:
        out.update({"RecordAccuracyPct": float("nan"), "ValueAccuracyPct": float("nan"),
                    "NetShrinkageUSD": 0.0, "CountsTaken": 0})

    if not aged.empty:
        out["DeadStockSKUs"] = int(aged.loc[aged["IsDeadStock"], "SKU"].nunique())
        out["SlowMovingSKUs"] = int(aged.loc[aged["IsSlowMoving"], "SKU"].nunique())
    else:
        out.update({"DeadStockSKUs": 0, "SlowMovingSKUs": 0})

    if not segments.empty:
        counts = segments["ABCClass"].value_counts()
        out["AClassSKUs"] = int(counts.get("A", 0))
        out["CriticalSKUs"] = int(segments["IsCritical"].sum())
    else:
        out.update({"AClassSKUs": 0, "CriticalSKUs": 0})

    out["OpenActions"] = int(len(register)) if register is not None else 0
    return out


__all__ = ["AnalysisModel", "DEFAULT_PARAMS", "build_model"]
