"""
The pipeline: does each report agree with the ones it is derived from.

Most of these are invariants rather than expected values - the kind of check
that catches a merge collision, a double count or a units mix-up, which are the
failure modes that produce plausible numbers rather than exceptions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from invapp.analytics import accuracy as acc
from invapp.analytics import actions as act
from invapp.analytics import ageing as ag
from invapp.analytics import allocation as alloc
from invapp.analytics import supplier as sup
from invapp.services import cleaning
from invapp.services.pipeline import build_model


class TestShape:
    def test_every_report_has_rows(self, model):
        """A report that silently comes back empty is the failure to catch."""
        for name in ("items", "suppliers", "nodes", "demand", "inventory",
                     "purchase_orders", "adjustments", "sku_stats", "node_stats",
                     "forecast_summary", "forecast", "forecast_fit", "segments",
                     "plan", "eoq", "service_curve", "ageing", "balance",
                     "transfers", "node_summary", "scorecard", "open_pos",
                     "count_variance", "register", "carrying", "trend"):
            frame = getattr(model, name)
            assert isinstance(frame, pd.DataFrame), name
            assert not frame.empty, f"{name} is empty"

    def test_the_plan_covers_every_stocked_location(self, model):
        planned = set(zip(model.plan["SKU"], model.plan["NodeID"], strict=True))
        stocked = set(zip(model.inventory["SKU"], model.inventory["NodeID"], strict=True))
        # The plan is built from demand, so a location with stock and no demand
        # history at all is legitimately absent - but it must be a handful, not
        # a third of the network.
        assert len(stocked - planned) < 0.05 * len(stocked)

    def test_one_row_per_sku_and_node(self, model):
        assert not model.plan.duplicated(subset=["SKU", "NodeID"]).any()
        assert not model.segments.duplicated(subset=["SKU"]).any()


class TestCostAndValue:
    def test_inventory_value_reconciles_to_units_times_cost(self, model):
        expected = float(
            (model.inventory["OnHandUnits"] * model.inventory["UnitCost"]).sum()
        )
        assert model.headline["InventoryValueUSD"] == pytest.approx(expected, rel=1e-9)

    def test_ageing_value_matches_the_snapshot(self, model):
        """The two are computed in different modules from the same facts.

        A merge collision on UnitCost would leave one of them at zero, and the
        page would show a plausible-looking $0 rather than raising.
        """
        assert model.ageing["InventoryValueUSD"].sum() == pytest.approx(
            model.headline["InventoryValueUSD"], rel=1e-6
        )

    def test_turns_and_days_are_reciprocal(self, model):
        turns = model.headline["InventoryTurns"]
        days = model.headline["DaysInventoryOutstanding"]
        assert turns * days == pytest.approx(365.0, rel=1e-6)

    def test_implied_cost_per_unit_is_plausible(self, model):
        """A quantity multiplier going missing shows up here first.

        The cheapest possible guard against value and units drifting apart by a
        factor of the case pack - which is the shape of bug that makes every
        turnover figure in the app wrong while every page still renders.
        """
        units = model.inventory["OnHandUnits"].sum()
        value = model.headline["InventoryValueUSD"]
        assert 5.0 < value / units < 200.0

    def test_carrying_cost_charges_risk_against_slow_stock_only(self, model):
        risk = model.carrying.set_index("Component").loc["Risk", "BaseValueUSD"]
        capital = model.carrying.set_index("Component").loc["Capital", "BaseValueUSD"]
        assert risk < capital


class TestReplenishment:
    def test_reorder_point_exceeds_safety_stock_where_there_is_demand(self, model):
        moving = model.plan[model.plan["DailyDemand"] > 0]
        assert (moving["ReorderPointUnits"] >= moving["SafetyStockUnits"]).all()

    def test_orders_are_only_raised_below_the_reorder_point(self, model):
        ordering = model.plan[model.plan["RecommendedOrderUnits"] > 0]
        assert (ordering["InventoryPosition"] <= ordering["ReorderPointUnits"]).all()

    def test_order_quantities_are_whole_cases(self, model):
        ordering = model.plan[model.plan["RecommendedOrderUnits"] > 0]
        remainder = ordering["RecommendedOrderUnits"] % ordering["CasePack"]
        assert (remainder.abs() < 1e-9).all()

    def test_a_class_gets_a_higher_service_target_than_c(self, model):
        by_class = model.plan.groupby("ABCClass")["ServiceLevelTarget"].max()
        assert by_class["A"] > by_class["C"]

    def test_planning_on_the_contract_gives_a_different_answer(self, model):
        """The gap is the supplier finding, in units on a shelf.

        If these were equal, either the lead times agree - which they do not in
        this data - or the contract column is being used for both.
        """
        gap = (model.plan["ReorderPointUnits"] - model.plan["ReorderPointOnContract"]).abs()
        assert gap.sum() > 0

    def test_eoq_is_an_orderable_cycle(self, model):
        """Bounded to something a buyer can run, not the raw square root.

        Unbounded EOQ happily asks for fifty-eight orders a year from a
        forty-seven day supplier.
        """
        cycles = model.eoq["EOQCycleDays"].dropna()
        assert cycles.min() >= 6.9
        assert cycles.max() <= 183

    def test_inventory_position_is_not_just_on_hand(self, model):
        assert not np.allclose(model.plan["InventoryPosition"], model.plan["OnHandUnits"])


class TestSegmentation:
    def test_a_class_is_a_minority_of_skus_and_most_of_the_value(self, model):
        segments = model.segments
        share_of_skus = (segments["ABCClass"] == "A").mean()
        value = segments.groupby("ABCClass")["AnnualConsumptionValueUSD"].sum()
        share_of_value = value.get("A", 0) / value.sum()
        assert share_of_skus < 0.35
        assert share_of_value > 0.70

    def test_every_matrix_cell_carries_a_policy(self, model):
        from invapp.analytics.segmentation import abc_xyz_matrix

        grid = abc_xyz_matrix(model.segments)
        assert grid["Policy"].notna().all()
        assert len(grid) >= 6

    def test_the_pareto_curve_ends_at_one(self, model):
        from invapp.analytics.segmentation import pareto_curve

        curve = pareto_curve(model.segments)
        assert curve["CumulativeShare"].iloc[-1] == pytest.approx(1.0, abs=1e-9)
        assert curve["CumulativeShare"].is_monotonic_increasing


class TestAgeingAndDeadStock:
    def test_dead_and_slow_are_mutually_exclusive(self, model):
        overlap = model.ageing["IsDeadStock"] & model.ageing["IsSlowMoving"]
        assert not overlap.any()

    def test_the_network_has_dead_stock_to_find(self, model):
        """A catalogue with nothing dead has no ageing report worth drawing."""
        assert model.headline["DeadStockSKUs"] > 5

    def test_dead_stock_is_judged_per_node(self, model):
        """Some dead locations must sit on SKUs that are alive elsewhere.

        Judged at catalogue level almost nothing is ever dead - a national
        range ships something somewhere most weeks - so a report that found
        only whole-catalogue deaths would be measuring the wrong grain.
        """
        dead = model.ageing[model.ageing["IsDeadStock"]]
        live_skus = set(
            model.segments.loc[model.segments["WeeksSinceLastShip"] < 4, "SKU"]
        )
        assert len(set(dead["SKU"]) & live_skus) > 0

    def test_provision_only_applies_to_stock_that_is_not_moving(self, model):
        healthy = model.ageing[~(model.ageing["IsDeadStock"] | model.ageing["IsSlowMoving"])]
        assert (healthy["EOReserveUSD"] == 0).all()

    def test_excess_never_exceeds_what_is_on_hand(self, model):
        assert (model.ageing["ExcessUnits"] <= model.ageing["OnHandUnits"] + 1e-9).all()


class TestAllocation:
    def test_a_transfer_never_moves_more_than_the_sender_can_spare(self, model):
        surplus = model.balance.set_index(["SKU", "NodeID"])["SurplusUnits"]
        for row in model.transfers.itertuples(index=False):
            available = surplus.get((row.SKU, row.FromNode), 0.0)
            assert row.TransferUnits <= available + 1e-6

    def test_every_recommended_move_pays_for_its_freight(self, model):
        assert (model.transfers["BenefitUSD"]
                >= model.transfers["FreightCostUSD"] * alloc.MIN_BENEFIT_RATIO - 1e-6).all()

    def test_no_transfer_to_the_node_it_came_from(self, model):
        assert (model.transfers["FromNode"] != model.transfers["ToNode"]).all()

    def test_transfers_improve_the_receiving_node(self, model):
        assert (model.transfers["ToCoverWeeksAfter"]
                >= model.transfers["ToCoverWeeksBefore"]).all()

    def test_node_value_shares_sum_to_one(self, model):
        assert model.node_summary["ValueSharePct"].sum() == pytest.approx(1.0, abs=1e-6)


class TestSupplier:
    def test_lead_time_is_measured_from_receipts_only(self, model):
        """An in-transit order has no lead time; counting it as zero would
        report the worst supplier as the best in the week after a big order."""
        po = model.purchase_orders
        received = po[po["ReceivedDate"].notna()]
        lead = sup.lead_time_facts(po)
        assert lead["ReceiptCount"].sum() == len(received)

    def test_contracted_and_delivered_disagree_in_both_directions(self, model):
        gaps = model.scorecard["LeadTimeGapDays"]
        assert (gaps > 0).any() and (gaps < 0).any()

    def test_scores_spread_across_grades(self, model):
        """One grade for everybody is a scorecard measuring nothing."""
        assert model.scorecard["Grade"].nunique() >= 3

    def test_perfect_order_rate_is_below_each_component(self, model):
        card = model.scorecard
        assert (card["PerfectOrderPct"] <= card["OnTimePct"] + 1e-9).all()
        assert (card["PerfectOrderPct"] <= card["CompletePct"] + 1e-9).all()


class TestAccuracy:
    def test_record_accuracy_is_a_share_of_records(self, model):
        summary = acc.accuracy_summary(model.count_variance).iloc[0]
        assert summary["AccurateRecords"] <= summary["RecordsCounted"]
        assert 0 < summary["RecordAccuracy"] <= 1

    def test_value_accuracy_and_record_accuracy_differ(self, model):
        """They answer different questions and are quoted interchangeably."""
        summary = acc.accuracy_summary(model.count_variance).iloc[0]
        assert summary["ValueAccuracy"] != summary["RecordAccuracy"]

    def test_accuracy_varies_by_node(self, model):
        by_node = acc.accuracy_summary(model.count_variance, "NodeID")
        assert by_node["RecordAccuracy"].std() > 0.005

    def test_the_waterfall_closes_on_the_snapshot(self, model):
        """A bridge whose last bar is not the physical value bridges nothing."""
        frame = acc.shrinkage_waterfall(model.adjustments, 1_000_000.0, 400_000.0, 380_000.0)
        assert frame.iloc[-1]["Step"] == "Closing physical value"
        assert frame.iloc[-1]["RunningEnd"] == pytest.approx(1_000_000.0)
        # And the running total must actually arrive there through the steps.
        assert frame.iloc[-2]["RunningEnd"] == pytest.approx(1_000_000.0, rel=1e-6)


class TestActionRegister:
    def test_every_verb_is_one_of_the_six(self, model):
        assert set(model.register["Action"]) <= set(act.ACTIONS)

    def test_all_six_verbs_actually_fire(self, model):
        """A verb that never appears is a feature that does not work.

        Expedite did not, for a reason that read as correct: an in-transit
        order counts toward the inventory position, so a node kept afloat by an
        order that is already three weeks late reported as healthy. Nothing
        raised; the register was simply one verb short.
        """
        assert set(model.register["Action"]) == set(act.ACTIONS)

    def test_an_overdue_receipt_is_discounted_from_the_position(self, model):
        """Stock you cannot promise is not stock. The reorder decision counts
        it - you must not order it twice - and the expedite decision must not."""
        overdue = model.plan[model.plan["OverdueUnits"] > 0]
        assert not overdue.empty
        assert (overdue["PositionExcludingOverdue"] <= overdue["InventoryPosition"]).all()
        assert model.plan["OverdueIsLoadBearing"].any()

    def test_the_register_is_ranked_by_impact(self, model):
        impacts = model.register["ImpactUSD"].tolist()
        assert impacts == sorted(impacts, reverse=True)

    def test_every_row_says_why(self, model):
        assert model.register["Rationale"].str.len().min() > 10

    def test_the_impact_column_declares_what_it_means(self, model):
        """The dollars are not comparable across verbs, so each row says so."""
        assert model.register["ImpactMeaning"].notna().all()

    def test_reorders_in_the_register_match_the_plan(self, model):
        reorders = model.register[model.register["Action"] == "Reorder"]
        due = model.plan[(model.plan["RecommendedOrderUnits"] > 0)
                         & (model.plan["Urgency"] != "Healthy")]
        assert len(reorders) == len(due)


class TestDegradedInputs:
    def test_a_missing_optional_sheet_still_produces_a_plan(self, sheets):
        """A warehouse that has not run a count programme still needs to buy."""
        partial = dict(sheets)
        partial["Cycle Counts"] = pd.DataFrame()
        partial["Inventory Adjustments"] = pd.DataFrame()
        model = build_model(partial)
        assert not model.plan.empty
        assert model.count_variance.empty

    def test_a_missing_required_sheet_is_an_error_not_an_empty_page(self, sheets):
        partial = dict(sheets)
        partial["Demand History"] = pd.DataFrame()
        with pytest.raises(ValueError, match="cannot work without"):
            build_model(partial)

    def test_currency_text_is_read_as_a_number(self):
        frame = pd.DataFrame({"SKU": ["A"], "UnitCost": ["$1,204.50"]})
        cleaned = cleaning.clean_sheet(frame, "Item Master")
        assert cleaned["UnitCost"].iloc[0] == pytest.approx(1204.50)

    def test_ids_are_aligned_across_sheets(self):
        sheets = {
            "Item Master": pd.DataFrame({"SKU": [" b0001 "]}),
            "Demand History": pd.DataFrame({"SKU": ["B0001"]}),
        }
        aligned = cleaning.align_ids(sheets)
        assert aligned["Item Master"]["SKU"].iloc[0] == "B0001"

    def test_as_of_is_the_snapshot_date_not_today(self, model):
        """A workbook exported in August and opened in November must not age
        every pallet in the building by eighty days."""
        assert str(model.as_of.date()) == "2026-08-29"


class TestNoLeakage:
    def test_no_infinities_reach_a_report(self, model):
        """`inf` is a real answer for cover on a dead SKU and invalid JSON."""
        for name in ("segments", "eoq", "scorecard", "node_summary", "register"):
            frame = getattr(model, name)
            numeric = frame.select_dtypes(include="number")
            assert not np.isinf(numeric.to_numpy(dtype=float, na_value=0.0)).any(), name

    def test_ageing_and_the_plan_agree_on_units(self, model):
        merged = model.ageing.merge(
            model.plan[["SKU", "NodeID", "OnHandUnits"]],
            on=["SKU", "NodeID"], how="inner", suffixes=("_age", "_plan"),
        )
        assert (merged["OnHandUnits_age"] == merged["OnHandUnits_plan"]).all()
