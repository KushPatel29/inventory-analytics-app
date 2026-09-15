"""Governed inventory-policy simulation and segment-level model evidence.

These functions are interface independent so policy assumptions can be tested,
replayed and reused by another client.  They support planning discussion; they
do not place orders, reserve inventory or claim realised service outcomes.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from invapp.analytics.replenishment import (
    expected_units_short,
    normal_loss,
    reorder_point,
    round_to_pack,
    safety_stock,
    z_for_service_level,
)

PLAN_REQUIRED = {
    "SKU",
    "NodeID",
    "ABCClass",
    "DailyDemand",
    "DailyStdDev",
    "AnnualDemand",
    "LeadTimeDaysActual",
    "LeadTimeDaysStdDev",
    "InventoryPosition",
    "UnitCost",
    "UnitPrice",
    "CasePack",
    "EOQUnits",
}


def _require(frame: pd.DataFrame, columns: set[str], label: str) -> None:
    missing = columns - set(frame.columns)
    if missing:
        raise ValueError(f"{label} is missing required columns: {sorted(missing)}")


def _bounded(value: float, low: float, high: float, label: str) -> float:
    number = float(value)
    if not low <= number <= high:
        raise ValueError(f"{label} must be between {low} and {high}")
    return number


def simulate_network_policy(
    plan: pd.DataFrame,
    *,
    service_level: float = 0.95,
    demand_multiplier: float = 1.0,
    lead_time_multiplier: float = 1.0,
    review_period_days: float = 7.0,
    stockout_penalty_per_unit: float = 6.5,
) -> pd.DataFrame:
    """Reprice one network-wide service, demand and lead-time scenario.

    The simulation holds the current inventory position fixed, then recomputes
    safety stock, reorder point, order-up-to quantity and lead-window exposure.
    This is a comparative policy screen, not an order proposal or forecast.
    """

    _require(plan, PLAN_REQUIRED, "replenishment plan")
    service = _bounded(service_level, 0.50, 0.999, "service_level")
    demand = _bounded(demand_multiplier, 0.50, 2.00, "demand_multiplier")
    lead = _bounded(lead_time_multiplier, 0.50, 2.00, "lead_time_multiplier")
    review = _bounded(review_period_days, 1.0, 90.0, "review_period_days")
    penalty = _bounded(stockout_penalty_per_unit, 0.0, 1000.0, "stockout_penalty_per_unit")
    if plan.empty:
        return pd.DataFrame()

    result = plan[[
        "SKU", "NodeID", "ABCClass", "InventoryPosition", "UnitCost",
        "UnitPrice", "CasePack", "DailyDemand", "DailyStdDev",
        "LeadTimeDaysActual", "LeadTimeDaysStdDev",
    ]].copy()
    result["ScenarioServiceLevel"] = service
    result["DemandMultiplier"] = demand
    result["LeadTimeMultiplier"] = lead
    result["ScenarioDailyDemand"] = result["DailyDemand"] * demand
    result["ScenarioDailyStdDev"] = result["DailyStdDev"] * demand
    result["ScenarioLeadTimeDays"] = result["LeadTimeDaysActual"] * lead
    result["ScenarioLeadTimeStdDev"] = result["LeadTimeDaysStdDev"] * lead
    z = np.full(len(result), z_for_service_level(service))
    result["ScenarioSafetyStockUnits"] = safety_stock(
        result["ScenarioDailyDemand"],
        result["ScenarioDailyStdDev"],
        result["ScenarioLeadTimeDays"],
        result["ScenarioLeadTimeStdDev"],
        z,
    )
    result["ScenarioReorderPointUnits"] = reorder_point(
        result["ScenarioDailyDemand"],
        result["ScenarioLeadTimeDays"],
        result["ScenarioSafetyStockUnits"],
    )
    result["ScenarioOrderUpToUnits"] = (
        result["ScenarioReorderPointUnits"]
        + result["ScenarioDailyDemand"] * review
    )
    raw_order = np.where(
        result["InventoryPosition"] <= result["ScenarioReorderPointUnits"],
        result["ScenarioOrderUpToUnits"] - result["InventoryPosition"],
        0.0,
    )
    result["ScenarioOrderUnits"] = np.where(
        raw_order > 0,
        round_to_pack(raw_order, result["CasePack"]),
        0.0,
    )
    result["ScenarioOrderValueUSD"] = (
        result["ScenarioOrderUnits"] * result["UnitCost"]
    )
    result["LeadWindowExpectedUnitsShort"] = expected_units_short(
        result["InventoryPosition"],
        result["ScenarioDailyDemand"],
        result["ScenarioDailyStdDev"],
        result["ScenarioLeadTimeDays"],
        result["ScenarioLeadTimeStdDev"],
    )
    unit_consequence = np.maximum(
        result["UnitPrice"] - result["UnitCost"], penalty
    )
    result["LeadWindowExposureUSD"] = (
        result["LeadWindowExpectedUnitsShort"] * unit_consequence
    )
    result["ScenarioStatus"] = np.select(
        [
            (result["InventoryPosition"] <= 0) & (result["ScenarioDailyDemand"] > 0),
            result["InventoryPosition"] < result["ScenarioSafetyStockUnits"],
            result["InventoryPosition"] <= result["ScenarioReorderPointUnits"],
        ],
        ["Stocked out", "Below scenario safety stock", "Scenario order due"],
        default="Covered under scenario",
    )
    return result.sort_values(
        ["LeadWindowExposureUSD", "SKU", "NodeID"],
        ascending=[False, True, True],
        kind="stable",
    ).reset_index(drop=True)


def policy_scenario_summary(scenario: pd.DataFrame) -> dict[str, float | int | str]:
    """Return the executive totals for one simulated network policy."""

    if scenario.empty:
        return {
            "Lines": 0,
            "LinesDue": 0,
            "StockedOut": 0,
            "SafetyStockUnits": 0.0,
            "SafetyStockValueUSD": 0.0,
            "OrderUnits": 0.0,
            "OrderValueUSD": 0.0,
            "LeadWindowExpectedUnitsShort": 0.0,
            "LeadWindowExposureUSD": 0.0,
            "DecisionStatus": "NO DATA",
        }
    due = scenario["ScenarioOrderUnits"].gt(0)
    return {
        "Lines": int(len(scenario)),
        "LinesDue": int(due.sum()),
        "StockedOut": int(scenario["ScenarioStatus"].eq("Stocked out").sum()),
        "SafetyStockUnits": float(scenario["ScenarioSafetyStockUnits"].sum()),
        "SafetyStockValueUSD": float(
            (scenario["ScenarioSafetyStockUnits"] * scenario["UnitCost"]).sum()
        ),
        "OrderUnits": float(scenario.loc[due, "ScenarioOrderUnits"].sum()),
        "OrderValueUSD": float(scenario.loc[due, "ScenarioOrderValueUSD"].sum()),
        "LeadWindowExpectedUnitsShort": float(
            scenario["LeadWindowExpectedUnitsShort"].sum()
        ),
        "LeadWindowExposureUSD": float(scenario["LeadWindowExposureUSD"].sum()),
        "DecisionStatus": "SIMULATED — REQUIRES PLANNER APPROVAL",
    }


def service_cost_frontier(
    plan: pd.DataFrame,
    *,
    levels: Iterable[float] = (0.85, 0.90, 0.93, 0.95, 0.97, 0.98, 0.99),
    carrying_cost_rate: float = 0.24,
    stockout_penalty_per_unit: float = 6.5,
) -> pd.DataFrame:
    """Estimate annual buffer-versus-shortage cost across service targets.

    The lowest modeled total cost is labelled as the economic screen, not as
    an approved service target.  Revenue, customer tiering and contractual
    penalties would still need to be validated by the business.
    """

    _require(plan, PLAN_REQUIRED, "replenishment plan")
    carrying = _bounded(carrying_cost_rate, 0.0, 1.0, "carrying_cost_rate")
    penalty = _bounded(stockout_penalty_per_unit, 0.0, 1000.0, "stockout_penalty_per_unit")
    if plan.empty:
        return pd.DataFrame()
    parsed_levels = tuple(float(level) for level in levels)
    if not parsed_levels or len(set(parsed_levels)) != len(parsed_levels):
        raise ValueError("levels must contain unique service targets")
    for level in parsed_levels:
        _bounded(level, 0.50, 0.999, "service level")

    daily_sigma = plan["DailyStdDev"].to_numpy(dtype=float)
    daily_demand = plan["DailyDemand"].to_numpy(dtype=float)
    lead_days = plan["LeadTimeDaysActual"].to_numpy(dtype=float)
    lead_sigma = plan["LeadTimeDaysStdDev"].to_numpy(dtype=float)
    sigma_lt = np.sqrt(
        np.maximum(
            lead_days * np.square(daily_sigma)
            + np.square(daily_demand) * np.square(lead_sigma),
            0.0,
        )
    )
    annual_demand = plan["AnnualDemand"].to_numpy(dtype=float)
    order_quantity = np.maximum(plan["EOQUnits"].to_numpy(dtype=float), 1.0)
    cycles = np.divide(
        annual_demand,
        order_quantity,
        out=np.zeros_like(annual_demand),
        where=order_quantity > 0,
    )
    unit_cost = plan["UnitCost"].to_numpy(dtype=float)
    consequence = np.maximum(
        plan["UnitPrice"].to_numpy(dtype=float) - unit_cost,
        penalty,
    )
    rows = []
    for level in parsed_levels:
        z = z_for_service_level(level)
        buffer_units = safety_stock(
            daily_demand, daily_sigma, lead_days, lead_sigma, np.full(len(plan), z)
        )
        annual_short_units = sigma_lt * normal_loss(z) * cycles
        annual_demand_total = float(annual_demand.sum())
        expected_fill = (
            1.0 - float(annual_short_units.sum()) / annual_demand_total
            if annual_demand_total > 0
            else float("nan")
        )
        buffer_value = float((buffer_units * unit_cost).sum())
        carrying_cost = buffer_value * carrying
        shortage_exposure = float((annual_short_units * consequence).sum())
        rows.append(
            {
                "ServiceLevel": level,
                "SafetyStockUnits": float(buffer_units.sum()),
                "SafetyStockValueUSD": buffer_value,
                "AnnualBufferCostUSD": carrying_cost,
                "ExpectedAnnualUnitsShort": float(annual_short_units.sum()),
                "ExpectedFillRate": max(0.0, min(1.0, expected_fill)),
                "AnnualShortageExposureUSD": shortage_exposure,
                "ModeledAnnualDecisionCostUSD": carrying_cost + shortage_exposure,
            }
        )
    frontier = pd.DataFrame(rows).sort_values("ServiceLevel").reset_index(drop=True)
    minimum = frontier["ModeledAnnualDecisionCostUSD"].idxmin()
    frontier["EconomicScreen"] = "Compare"
    frontier.loc[minimum, "EconomicScreen"] = "LOWEST MODELED COST — NOT APPROVED"
    return frontier


def forecast_segment_scorecard(
    forecast_summary: pd.DataFrame,
    segments: pd.DataFrame,
) -> pd.DataFrame:
    """Aggregate forecast evidence by the ABC-XYZ policy segments it informs."""

    _require(
        forecast_summary,
        {"SKU", "ForecastAccuracy", "Bias", "MASE", "ForecastWeekly"},
        "forecast summary",
    )
    _require(segments, {"SKU", "ABCClass", "XYZClass"}, "segment catalogue")
    if forecast_summary.empty:
        return pd.DataFrame()
    joined = forecast_summary.merge(
        segments[["SKU", "ABCClass", "XYZClass"]].drop_duplicates("SKU"),
        on="SKU",
        how="left",
        validate="one_to_one",
    )
    if joined[["ABCClass", "XYZClass"]].isna().any().any():
        raise ValueError("every forecast SKU must have an ABC-XYZ segment")

    def summarize(group: pd.DataFrame) -> pd.Series:
        weights = group["ForecastWeekly"].clip(lower=0).fillna(0)
        usable = group["ForecastAccuracy"].notna() & weights.gt(0)
        weighted_accuracy = (
            float(np.average(group.loc[usable, "ForecastAccuracy"], weights=weights[usable]))
            if usable.any()
            else float("nan")
        )
        return pd.Series(
            {
                "SKUCount": int(group.SKU.nunique()),
                "WeeklyUnits": float(weights.sum()),
                "VolumeWeightedAccuracy": weighted_accuracy,
                "MedianMASE": float(group.MASE.median()),
                "MedianBias": float(group.Bias.median()),
                "AbsoluteBiasP90": float(group.Bias.abs().quantile(0.90)),
            }
        )

    rows = []
    for (abc_class, xyz_class), group in joined.groupby(
        ["ABCClass", "XYZClass"], observed=True
    ):
        row = summarize(group).to_dict()
        row.update({"ABCClass": abc_class, "XYZClass": xyz_class})
        rows.append(row)
    scorecard = pd.DataFrame(rows)
    scorecard["ReviewStatus"] = np.select(
        [
            scorecard["VolumeWeightedAccuracy"].lt(0.70),
            scorecard["AbsoluteBiasP90"].gt(0.50),
        ],
        ["REVIEW ACCURACY", "REVIEW BIAS"],
        default="WITHIN PORTFOLIO GUARDRAIL",
    )
    return scorecard.sort_values(
        ["ABCClass", "XYZClass"], kind="stable"
    ).reset_index(drop=True)


__all__ = [
    "forecast_segment_scorecard",
    "policy_scenario_summary",
    "service_cost_frontier",
    "simulate_network_policy",
]
