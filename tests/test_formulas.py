"""
The inventory maths, against the formulas rather than against itself.

Every expected value here is computed by hand in the test or is a published
identity, so a test passing means the implementation matches the textbook - not
that it matches whatever it did last week.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from invapp.analytics import replenishment as rp
from invapp.analytics import segmentation as seg


class TestSafetyFactor:
    @pytest.mark.parametrize(
        ("level", "z"),
        [(0.90, 1.2816), (0.95, 1.6449), (0.98, 2.0537), (0.99, 2.3263)],
    )
    def test_matches_published_z_values(self, level, z):
        assert rp.z_for_service_level(level) == pytest.approx(z, abs=1e-4)

    def test_is_monotonic(self):
        levels = [0.80, 0.90, 0.95, 0.99]
        values = [rp.z_for_service_level(x) for x in levels]
        assert values == sorted(values)

    def test_a_certainty_does_not_return_infinity(self):
        """A 100% service level has no finite z; clamping keeps it orderable.

        Left unclamped this returns inf, the safety stock becomes inf, the
        recommended order becomes inf, and the top of the buy list is a SKU
        nobody can order any quantity of.
        """
        assert math.isfinite(rp.z_for_service_level(1.0))
        assert math.isfinite(rp.z_for_service_level(0.0))


class TestNormalLoss:
    def test_g_of_zero_is_the_normal_density(self):
        assert rp.normal_loss(0.0) == pytest.approx(1 / math.sqrt(2 * math.pi), abs=1e-6)

    def test_decreases_with_z(self):
        assert rp.normal_loss(0.0) > rp.normal_loss(1.0) > rp.normal_loss(2.0) > 0

    def test_converts_cycle_service_into_a_higher_fill_rate(self):
        """95% cycle service on a large order is a fill rate well above 99%.

        The two get quoted interchangeably and are not the same number.
        """
        fill = rp.fill_rate_from_cycle_service(
            rp.z_for_service_level(0.95), sigma_lt=120.0, order_quantity=2000.0
        )
        assert 0.99 < fill < 1.0


class TestSafetyStock:
    def test_two_term_formula(self):
        """SS = z sqrt(LT sigma_d^2 + d^2 sigma_LT^2), worked by hand."""
        got = rp.safety_stock([10.0], [3.0], [14.0], [2.0], [1.6449])[0]
        expected = 1.6449 * math.sqrt(14 * 9 + 100 * 4)
        assert got == pytest.approx(expected, rel=1e-6)

    def test_lead_time_variability_dominates_on_a_long_lead(self):
        """The term the one-term formula drops is the larger one here.

        A 40-day import with a week of spread on a fast SKU: dropping the
        lead-time term understates the buffer by more than half.
        """
        both = rp.safety_stock([50.0], [8.0], [40.0], [7.0], [2.05])[0]
        demand_only = 2.05 * math.sqrt(40 * 64)
        assert both > 2 * demand_only

    def test_never_negative(self):
        assert rp.safety_stock([5.0], [-1.0], [-3.0], [-2.0], [1.64])[0] >= 0


class TestReorderPoint:
    def test_is_lead_time_demand_plus_buffer(self):
        """The formula the README quotes: d x LT + SS."""
        safety = rp.safety_stock([10.0], [3.0], [14.0], [2.0], [1.6449])
        got = rp.reorder_point([10.0], [14.0], safety)[0]
        assert got == pytest.approx(10.0 * 14 + safety[0], rel=1e-9)


class TestEOQ:
    def test_square_root_formula(self):
        got = rp.economic_order_quantity([12000.0], [250.0], [8.0], 0.24)[0]
        assert got == pytest.approx(math.sqrt(2 * 12000 * 250 / (8 * 0.24)), rel=1e-9)

    def test_ordering_cost_equals_holding_cost_at_the_optimum(self):
        """The identity that defines EOQ, and the cheapest way to catch a typo."""
        d, s, c, rate = 12000.0, 250.0, 8.0, 0.24
        q = rp.economic_order_quantity([d], [s], [c], rate)[0]
        ordering = d / q * s
        holding = q / 2 * c * rate
        assert ordering == pytest.approx(holding, rel=1e-9)

    def test_total_cost_is_minimised_at_the_optimum(self):
        d, s, c, rate = 12000.0, 250.0, 8.0, 0.24
        q = rp.economic_order_quantity([d], [s], [c], rate)[0]
        at_optimum = rp.total_cost_at_quantity([q], [d], [s], [c], rate)[0]
        for other in (q * 0.5, q * 0.8, q * 1.25, q * 2.0):
            assert rp.total_cost_at_quantity([other], [d], [s], [c], rate)[0] > at_optimum

    def test_a_free_item_has_no_infinite_order_quantity(self):
        """Zero holding cost divides by zero; inf would top the buy list."""
        assert rp.economic_order_quantity([1000.0], [250.0], [0.0], 0.24)[0] == 0.0


class TestStockoutRisk:
    def test_risk_is_a_half_when_position_equals_mean_lead_demand(self):
        risk = rp.stockout_probability([140.0], [10.0], [3.0], [14.0], [0.0])[0]
        assert risk == pytest.approx(0.5, abs=1e-6)

    def test_more_stock_is_less_risk(self):
        low = rp.stockout_probability([100.0], [10.0], [3.0], [14.0], [2.0])[0]
        high = rp.stockout_probability([300.0], [10.0], [3.0], [14.0], [2.0])[0]
        assert low > high

    def test_expected_shortfall_falls_as_cover_rises(self):
        short = rp.expected_units_short(
            [100.0, 300.0], [10.0, 10.0], [3.0, 3.0], [14.0, 14.0], [2.0, 2.0]
        )
        assert short[0] > short[1]

    def test_with_no_variability_shortfall_is_the_plain_gap(self):
        got = rp.expected_units_short([100.0], [10.0], [0.0], [14.0], [0.0])[0]
        assert got == pytest.approx(40.0, rel=1e-9)


class TestRounding:
    def test_rounds_up_to_a_whole_case(self):
        assert list(rp.round_to_pack([1.0, 12.0, 13.0], [12, 12, 12])) == [12.0, 12.0, 24.0]

    def test_a_zero_case_pack_does_not_divide_by_zero(self):
        assert rp.round_to_pack([7.0], [0])[0] == 7.0


class TestABC:
    def test_splits_on_cumulative_share(self):
        import pandas as pd

        frame = pd.DataFrame(
            {"value": [80.0, 15.0, 4.0, 1.0]}, index=["w", "x", "y", "z"]
        )
        out = seg.abc_classify(frame, "value")
        # Cumulative shares are 0.80, 0.95, 0.99, 1.00 and the cuts are
        # inclusive, so the third item is the first past 95%.
        assert list(out["ABCClass"]) == ["A", "B", "C", "C"]

    def test_ties_break_deterministically(self):
        """Two SKUs with the same value must not swap between runs.

        One of them is on the wrong side of the 80% line if they do, and a
        committed mart then differs between a laptop and CI for no visible
        reason.
        """
        import pandas as pd

        frame = pd.DataFrame({"value": [50.0, 50.0, 1.0]}, index=["b", "a", "c"])
        first = list(seg.abc_classify(frame, "value")["ABCClassRank"])
        second = list(seg.abc_classify(frame.iloc[::-1], "value").reindex(frame.index)[
            "ABCClassRank"
        ])
        assert first == second

    def test_everything_is_c_when_there_is_no_value(self):
        import pandas as pd

        frame = pd.DataFrame({"value": [0.0, 0.0]}, index=["a", "b"])
        assert set(seg.abc_classify(frame, "value")["ABCClass"]) == {"C"}


class TestXYZ:
    def test_splits_on_coefficient_of_variation(self):
        import pandas as pd

        frame = pd.DataFrame({"CoefficientOfVariation": [0.2, 0.7, 1.4, np.nan]})
        assert list(seg.xyz_classify(frame)["XYZClass"]) == ["X", "Y", "Z", "Z"]


class TestServiceLevelCurve:
    def test_safety_stock_rises_with_the_target(self, model):
        curve = rp.service_level_curve(model.plan)
        units = list(curve["SafetyStockUnits"])
        assert units == sorted(units)

    def test_the_last_points_cost_more_than_the_first(self, model):
        """z is convex, so 98 -> 99 costs more stock than 85 -> 90."""
        curve = rp.service_level_curve(model.plan, levels=(0.85, 0.90, 0.98, 0.99))
        units = list(curve["SafetyStockUnits"])
        assert (units[3] - units[2]) > (units[1] - units[0])
