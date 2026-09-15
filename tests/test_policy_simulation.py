"""Decision contracts for network inventory-policy simulation."""

from __future__ import annotations

import pandas as pd
import pytest

from invapp.analytics.policy_simulation import (
    forecast_segment_scorecard,
    policy_scenario_summary,
    service_cost_frontier,
    simulate_network_policy,
)


def test_baseline_policy_scenario_preserves_network_grain_and_boundaries(model):
    scenario = simulate_network_policy(model.plan)
    assert len(scenario) == len(model.plan)
    assert not scenario.duplicated(["SKU", "NodeID"]).any()
    assert scenario.ScenarioServiceLevel.eq(0.95).all()
    assert scenario.DemandMultiplier.eq(1.0).all()
    assert scenario.LeadTimeMultiplier.eq(1.0).all()
    assert scenario.ScenarioStatus.isin({
        "Stocked out",
        "Below scenario safety stock",
        "Scenario order due",
        "Covered under scenario",
    }).all()


def test_more_demand_increases_network_order_and_exposure(model):
    baseline = policy_scenario_summary(simulate_network_policy(model.plan))
    pressure = policy_scenario_summary(
        simulate_network_policy(model.plan, demand_multiplier=1.25)
    )
    assert pressure["SafetyStockUnits"] > baseline["SafetyStockUnits"]
    assert pressure["OrderValueUSD"] > baseline["OrderValueUSD"]
    assert pressure["LeadWindowExposureUSD"] > baseline["LeadWindowExposureUSD"]


def test_longer_lead_time_increases_buffer_and_exposure(model):
    baseline = policy_scenario_summary(simulate_network_policy(model.plan))
    pressure = policy_scenario_summary(
        simulate_network_policy(model.plan, lead_time_multiplier=1.30)
    )
    assert pressure["SafetyStockUnits"] > baseline["SafetyStockUnits"]
    assert pressure["LeadWindowExpectedUnitsShort"] > baseline["LeadWindowExpectedUnitsShort"]


def test_higher_service_target_increases_the_buffer(model):
    lower = policy_scenario_summary(
        simulate_network_policy(model.plan, service_level=0.90)
    )
    higher = policy_scenario_summary(
        simulate_network_policy(model.plan, service_level=0.98)
    )
    assert higher["SafetyStockUnits"] > lower["SafetyStockUnits"]
    assert higher["OrderValueUSD"] >= lower["OrderValueUSD"]


@pytest.mark.parametrize(
    "options,message",
    [
        ({"service_level": 1.0}, "service_level"),
        ({"demand_multiplier": 0.49}, "demand_multiplier"),
        ({"lead_time_multiplier": 2.01}, "lead_time_multiplier"),
        ({"review_period_days": 0}, "review_period_days"),
        ({"stockout_penalty_per_unit": -1}, "stockout_penalty_per_unit"),
    ],
)
def test_policy_scenario_rejects_unsafe_parameter_ranges(model, options, message):
    with pytest.raises(ValueError, match=message):
        simulate_network_policy(model.plan, **options)


def test_service_cost_frontier_makes_the_economic_tradeoff_explicit(model):
    frontier = service_cost_frontier(model.plan)
    assert frontier.ServiceLevel.tolist() == [0.85, 0.90, 0.93, 0.95, 0.97, 0.98, 0.99]
    assert frontier.SafetyStockValueUSD.is_monotonic_increasing
    assert frontier.AnnualShortageExposureUSD.is_monotonic_decreasing
    assert frontier.ExpectedFillRate.is_monotonic_increasing
    assert frontier.EconomicScreen.str.startswith("LOWEST").sum() == 1
    assert frontier.ModeledAnnualDecisionCostUSD.equals(
        frontier.AnnualBufferCostUSD + frontier.AnnualShortageExposureUSD
    )


def test_frontier_refuses_duplicate_levels(model):
    with pytest.raises(ValueError, match="unique service targets"):
        service_cost_frontier(model.plan, levels=(0.90, 0.90))


def test_forecast_scorecard_reconciles_every_sku_to_one_policy_cell(model):
    scorecard = forecast_segment_scorecard(model.forecast_summary, model.segments)
    assert scorecard.SKUCount.sum() == model.forecast_summary.SKU.nunique() == 420
    assert not scorecard.duplicated(["ABCClass", "XYZClass"]).any()
    assert scorecard.VolumeWeightedAccuracy.between(0, 1).all()
    assert scorecard.MedianMASE.ge(0).all()
    assert scorecard.ReviewStatus.isin({
        "REVIEW ACCURACY", "REVIEW BIAS", "WITHIN PORTFOLIO GUARDRAIL"
    }).all()


def test_forecast_scorecard_fails_when_a_sku_has_no_segment(model):
    incomplete = model.segments.iloc[1:].copy()
    with pytest.raises(ValueError, match="every forecast SKU"):
        forecast_segment_scorecard(model.forecast_summary, incomplete)


def test_summary_is_safe_for_an_empty_scenario():
    summary = policy_scenario_summary(pd.DataFrame())
    assert summary["Lines"] == 0
    assert summary["DecisionStatus"] == "NO DATA"
