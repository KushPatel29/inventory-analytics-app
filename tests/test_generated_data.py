"""
The generated data: is it reproducible, and is it a business.

The second half matters more than it looks. Synthetic data that is merely
random has nothing for any of these reports to find - a flat ABC curve, no dead
stock, no supplier who is ever late - and a dashboard over it looks finished
while proving nothing. These tests pin the properties the reports depend on.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from seed.catalog import DEPARTMENTS, NODES, PLANNING_PARAMETERS, SUPPLIERS
from seed.dataset import DEFAULT_END, SHEET_NAMES, generate


class TestReproducible:
    def test_the_same_seed_gives_the_same_workbook(self):
        first = generate(skus=40, weeks=30)
        second = generate(skus=40, weeks=30)
        for name in SHEET_NAMES:
            pd.testing.assert_frame_equal(first[name], second[name])

    def test_a_different_seed_gives_different_data(self):
        first = generate(skus=40, weeks=30, seed=1)
        second = generate(skus=40, weeks=30, seed=2)
        assert not first["Demand History"].equals(second["Demand History"])

    def test_the_window_ends_on_a_saturday(self):
        """Retail weeks end on Saturday. A week-ending date that drifts across
        weekdays makes every week-over-week comparison a lie."""
        assert DEFAULT_END.weekday() == 5
        assert DEFAULT_END == date(2026, 8, 29)

    def test_every_week_ending_is_the_same_weekday(self, sheets):
        weeks = pd.to_datetime(sheets["Demand History"]["WeekEnding"])
        assert weeks.dt.weekday.nunique() == 1

    def test_a_short_run_does_not_crash(self):
        """`--weeks 12` used to index an array from the wrong end."""
        small = generate(skus=25, weeks=12)
        assert not small["Demand History"].empty


class TestSchema:
    def test_every_sheet_is_present_and_populated(self, sheets):
        for name in SHEET_NAMES:
            assert name in sheets, name
            assert not sheets[name].empty, name

    def test_ids_join_across_sheets(self, sheets):
        items = set(sheets["Item Master"]["SKU"])
        nodes = set(sheets["Network Nodes"]["NodeID"])
        suppliers = set(sheets["Supplier Master"]["SupplierID"])
        for sheet in ("Demand History", "Inventory Snapshot", "Purchase Orders",
                      "Cycle Counts", "Inventory Adjustments"):
            frame = sheets[sheet]
            assert set(frame["SKU"]) <= items, sheet
            assert set(frame["NodeID"]) <= nodes, sheet
        assert set(sheets["Item Master"]["SupplierID"]) <= suppliers
        assert set(sheets["Purchase Orders"]["SupplierID"]) <= suppliers

    def test_a_sku_only_ships_from_a_node_it_is_stocked_in(self, sheets):
        shipped = set(zip(sheets["Demand History"]["SKU"],
                          sheets["Demand History"]["NodeID"], strict=True))
        stocked = set(zip(sheets["Inventory Snapshot"]["SKU"],
                          sheets["Inventory Snapshot"]["NodeID"], strict=True))
        # Some shipping pairs have run their stock to zero and been dropped from
        # the snapshot; the reverse - stock in a node that never ships - is the
        # stranded inventory the reports look for.
        assert len(shipped - stocked) < 0.2 * len(shipped)

    def test_node_demand_shares_sum_to_one(self):
        assert sum(n["DemandShare"] for n in NODES) == pytest.approx(1.0, abs=1e-9)

    def test_seasonal_indices_average_to_one(self):
        for department in DEPARTMENTS:
            mean = sum(department["Seasonality"]) / 12
            assert mean == pytest.approx(1.0, abs=0.02), department["Department"]

    def test_no_negative_quantities_or_costs(self, sheets):
        assert (sheets["Demand History"]["UnitsShipped"] >= 0).all()
        assert (sheets["Inventory Snapshot"]["OnHandUnits"] >= 0).all()
        assert (sheets["Item Master"]["UnitCost"] > 0).all()
        assert (sheets["Purchase Orders"]["QtyOrdered"] > 0).all()

    def test_price_is_above_cost(self, sheets):
        items = sheets["Item Master"]
        assert (items["UnitPrice"] > items["UnitCost"]).all()

    def test_only_frozen_and_chilled_categories_are_cold(self, sheets):
        cold = sheets["Item Master"]
        cold = cold[cold["TempZone"] != "Ambient"]
        assert set(cold["Category"]) <= {"Dairy & Chilled", "Frozen Meals"}


class TestItIsABusiness:
    def test_receipts_roughly_match_shipments(self, sheets):
        """A network receiving 76% of what it ships is liquidating itself.

        The ratio is a calibration, and the calibration is easy to break: it
        broke once by dividing each order across the wrong denominator, which
        put the implied opening balance at two and a half times the closing one
        and made the inventory trend a slope with no cause.
        """
        cost = sheets["Item Master"].set_index("SKU")["UnitCost"]
        demand = sheets["Demand History"]
        orders = sheets["Purchase Orders"]
        cogs = float((demand["UnitsShipped"] * demand["SKU"].map(cost)).sum())
        receipts = float((orders["QtyReceived"] * orders["UnitCost"]).sum())
        assert 0.95 < receipts / cogs < 1.10

    def test_turns_are_in_the_range_a_retailer_runs_at(self, sheets):
        cost = sheets["Item Master"].set_index("SKU")["UnitCost"]
        demand = sheets["Demand History"]
        weeks = demand["WeekEnding"].nunique()
        annual_cogs = float((demand["UnitsShipped"] * demand["SKU"].map(cost)).sum()) * (
            52.0 / weeks
        )
        inventory = sheets["Inventory Snapshot"]
        value = float((inventory["OnHandUnits"] * inventory["SKU"].map(cost)).sum())
        assert 2.5 < annual_cogs / value < 9.0

    def test_velocity_is_skewed_enough_for_pareto_to_bend(self, sheets):
        """A flat catalogue makes the ABC curve a straight line."""
        cost = sheets["Item Master"].set_index("SKU")["UnitCost"]
        demand = sheets["Demand History"]
        value = (demand["UnitsShipped"] * demand["SKU"].map(cost)).groupby(
            demand["SKU"]
        ).sum().sort_values(ascending=False)
        top_fifth = value.head(len(value) // 5).sum() / value.sum()
        assert top_fifth > 0.6

    def test_demand_variability_spans_the_xyz_bands(self, sheets):
        demand = sheets["Demand History"]
        weekly = demand.pivot_table(index="SKU", columns="WeekEnding",
                                    values="UnitsShipped", aggfunc="sum").fillna(0.0)
        cov = weekly.std(axis=1, ddof=1) / weekly.mean(axis=1).replace(0, pd.NA)
        cov = cov.dropna()
        assert (cov <= 0.5).any() and ((cov > 0.5) & (cov <= 1.0)).any() and (cov > 1.0).any()

    def test_some_items_stop_shipping_entirely(self, sheets):
        """Without a hard stop nothing is ever 180 days without a shipment and
        the ageing report, the write-down and the liquidate action all have
        nothing to report on a business that plainly has some."""
        demand = sheets["Demand History"]
        last = pd.to_datetime(demand.groupby("SKU")["WeekEnding"].max())
        gap = (pd.Timestamp(DEFAULT_END) - last).dt.days
        assert (gap > 180).sum() >= 10

    def test_stock_is_stranded_at_nodes_that_stopped_selling(self, sheets):
        demand = sheets["Demand History"]
        last = pd.to_datetime(demand.groupby(["SKU", "NodeID"])["WeekEnding"].max())
        gap = (pd.Timestamp(DEFAULT_END) - last).dt.days
        assert (gap > 180).sum() >= 30

    def test_suppliers_are_late_and_early(self, sheets):
        orders = sheets["Purchase Orders"]
        received = orders[orders["ReceivedDate"] != ""]
        late = (pd.to_datetime(received["ReceivedDate"])
                - pd.to_datetime(received["PromisedDate"])).dt.days
        assert (late > 2).mean() > 0.02
        assert (late < 0).mean() > 0.20

    def test_contracted_lead_times_disagree_with_delivered_ones_both_ways(self):
        gaps = [row[5] - row[4] for row in SUPPLIERS]
        assert any(gap > 0 for gap in gaps)
        assert any(gap < 0 for gap in gaps)

    def test_purchase_orders_carry_several_lines(self, sheets):
        """One line per order and the whole EOQ argument has nothing to stand
        on - a fixed ordering cost is only shared if there is something to
        share it with."""
        orders = sheets["Purchase Orders"]
        lines_per_po = len(orders) / orders["PONumber"].nunique()
        assert 6 < lines_per_po < 20

    def test_count_accuracy_is_imperfect_and_varies_by_node(self, sheets):
        counts = sheets["Cycle Counts"]
        counts = counts.assign(ok=counts["CountedQty"] == counts["SystemQty"])
        overall = counts["ok"].mean()
        assert 0.90 < overall < 0.99
        by_node = counts.groupby("NodeID")["ok"].mean()
        assert by_node.max() - by_node.min() > 0.02

    def test_variance_skews_towards_loss(self, sheets):
        """Found stock exists; missing stock is more common. A symmetric
        variance nets to zero and the shrink line has nothing on it."""
        counts = sheets["Cycle Counts"]
        variance = counts["CountedQty"] - counts["SystemQty"]
        errors = variance[variance != 0]
        assert (errors < 0).mean() > 0.55

    def test_fill_rate_is_high_but_not_perfect(self, sheets):
        demand = sheets["Demand History"]
        fill = demand["UnitsShipped"].sum() / demand["UnitsRequested"].sum()
        assert 0.95 < fill < 0.999


class TestParameters:
    def test_every_declared_parameter_reaches_the_sheet(self, sheets):
        written = set(sheets["Planning Parameters"]["Parameter"])
        assert {name for name, _, _ in PLANNING_PARAMETERS} == written

    def test_carrying_components_sum_to_the_headline_rate(self):
        values = {name: value for name, value, _ in PLANNING_PARAMETERS}
        parts = (values["CapitalRate"] + values["StorageRate"]
                 + values["ServiceRate"] + values["RiskRate"])
        assert parts == pytest.approx(values["CarryingCostRate"], abs=1e-9)

    def test_service_levels_are_ordered_by_class(self):
        values = {name: value for name, value, _ in PLANNING_PARAMETERS}
        assert values["ServiceLevelA"] > values["ServiceLevelB"] > values["ServiceLevelC"]

    def test_the_workbook_parameters_drive_the_model(self, sheets):
        """A parameter on the sheet must beat the code default, or the sheet is
        decoration."""
        from invapp.services.pipeline import build_model

        changed = dict(sheets)
        params = sheets["Planning Parameters"].copy()
        params.loc[params["Parameter"] == "CarryingCostRate", "Value"] = 0.5
        changed["Planning Parameters"] = params
        model = build_model(changed)
        assert model.params["CarryingCostRate"] == 0.5
