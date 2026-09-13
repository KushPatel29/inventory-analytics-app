"""
Forecasting: the methods, the metrics, and the selection between them.

The methods are tested on series whose right answer is known by construction -
a constant, a ramp, a repeating season, a run of zeros - because a forecaster
tested only on real data can be wrong in a way that looks like the data being
hard.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from invapp.analytics import demand as dm


class TestMethods:
    def test_every_method_returns_the_horizon_asked_for(self):
        y = np.array([5.0, 7.0, 6.0, 8.0, 0.0, 9.0, 7.0, 6.0, 8.0, 7.0, 5.0, 6.0])
        for name, fn in dm.FORECASTERS.items():
            got = np.asarray(fn(y, 6))
            assert got.shape == (6,), name

    def test_no_method_forecasts_negative_demand(self):
        """A trend fitted to a collapsing series extrapolates below zero."""
        y = np.linspace(200, 1, 40)
        for name, fn in dm.FORECASTERS.items():
            assert np.asarray(fn(y, 8)).min() >= 0, name

    def test_a_constant_series_is_forecast_exactly(self):
        y = np.full(40, 12.0)
        for name in ("naive", "ma4", "ma13", "ses"):
            got = dm.FORECASTERS[name](y, 4)
            assert np.allclose(got, 12.0), name

    def test_holt_follows_a_ramp(self):
        """It climbs, close to where the ramp is, by less than the raw slope.

        A damped model cannot track a linear trend exactly - the damping makes
        the level lag by a constant - so the test asserts the shape rather than
        demanding the arithmetic of an undamped model from a damped one.
        """
        y = np.arange(1.0, 41.0)
        got = dm.f_holt(y, 4)
        assert got[3] > got[0]                      # rising
        assert got[0] == pytest.approx(y[-1], rel=0.05)
        assert got[3] < y[-1] + 4                   # damped: below the raw slope

    def test_seasonal_naive_repeats_last_year(self):
        season = np.tile(np.array([1.0, 5.0, 20.0, 5.0]), 20)[:60]
        got = dm.f_seasonal_naive(season, 4)
        assert got.argmax() == 2

    def test_croston_beats_smoothing_on_intermittent_demand(self):
        """A series of zeros broken by occasional orders.

        Smoothing the raw series drags the forecast toward zero and then no
        buffer is ever held. Croston smooths the sizes and the intervals apart.
        """
        y = np.zeros(60)
        y[::6] = 30.0
        croston = dm.f_croston(y, 4)[0]
        ses = dm.f_ses(y, 4)[0]
        assert croston == pytest.approx(30.0 / 6, rel=0.35)
        assert croston > ses

    def test_croston_falls_back_when_nothing_has_shipped(self):
        assert dm.f_croston(np.zeros(30), 3).tolist() == [0.0, 0.0, 0.0]


class TestMetrics:
    def test_wape_is_error_over_actual(self):
        actual = np.array([100.0, 100.0])
        forecast = np.array([90.0, 120.0])
        assert dm.wape(actual, forecast) == pytest.approx(30 / 200)

    def test_bias_is_signed_and_cancels(self):
        actual = np.array([100.0, 100.0])
        forecast = np.array([90.0, 110.0])
        assert dm.bias(actual, forecast) == pytest.approx(0.0)

    def test_bias_shows_a_consistent_lean(self):
        actual = np.array([100.0, 100.0])
        forecast = np.array([110.0, 120.0])
        assert dm.bias(actual, forecast) == pytest.approx(0.15)

    def test_mase_is_one_when_the_forecast_matches_the_naive_step(self):
        history = np.array([10.0, 12.0, 10.0, 12.0, 10.0, 12.0])
        actual = np.array([10.0, 12.0])
        forecast = np.array([12.0, 10.0])
        assert dm.mase(actual, forecast, history) == pytest.approx(1.0)

    def test_wape_is_undefined_rather_than_zero_on_an_empty_actual(self):
        """Zero actuals divide by zero; reporting 0% error would be a lie."""
        assert np.isnan(dm.wape(np.zeros(3), np.ones(3)))

    def test_tracking_signal_flags_a_one_sided_error(self):
        actual = np.full(8, 100.0)
        assert dm.tracking_signal(actual, np.full(8, 120.0)) == pytest.approx(8.0)
        assert abs(dm.tracking_signal(actual, actual + np.array(
            [10, -10, 10, -10, 10, -10, 10, -10], dtype=float))) < 1e-9


class TestSelection:
    def test_the_backtest_is_out_of_sample(self):
        """Every scored point must come from a model that had not seen it.

        A forecaster given the whole series scores perfectly and says nothing.
        A constant series with one late shock makes that visible: an in-sample
        fit absorbs the shock, an honest backtest does not.
        """
        y = np.full(60, 10.0)
        y[-3:] = 90.0
        scores = dm.backtest_series(y, folds=2, horizon=3, methods=("naive",))
        assert scores["naive"]["wape"] > 0.5

    def test_selection_is_deterministic_on_a_tie(self):
        y = np.full(60, 10.0)
        first = dm.backtest_series(y)
        second = dm.backtest_series(y)
        assert dm._pick(first) == dm._pick(second)

    def test_a_short_series_scores_nothing_rather_than_guessing(self):
        assert dm.backtest_series(np.arange(10.0)) == {}


class TestSeries:
    def test_zero_weeks_are_filled_in(self):
        """A week with no shipment has no row; the mean must still see it.

        This is the single most consequential detail in the module: dividing
        summed units by rows-found instead of weeks-elapsed reports a SKU that
        sold once in thirteen weeks as selling every week, and that number
        flows into safety stock and orders more of it.
        """
        frame = pd.DataFrame({
            "WeekEnding": ["2026-01-03", "2026-01-24"],
            "SKU": ["A", "A"],
            "UnitsShipped": [7.0, 7.0],
        })
        grid = dm.week_grid(frame)
        matrix = dm.weekly_matrix(frame, grid)
        assert matrix.shape == (1, 4)
        assert matrix.to_numpy().tolist() == [[7.0, 0.0, 0.0, 7.0]]
        stats = dm.demand_statistics(matrix)
        assert stats.loc["A", "WeeklyDemand"] == pytest.approx(3.5)

    def test_weeks_since_last_ship_counts_from_the_end(self):
        frame = pd.DataFrame({
            "WeekEnding": ["2026-01-03", "2026-01-10"],
            "SKU": ["A", "B"],
            "UnitsShipped": [1.0, 1.0],
        })
        matrix = dm.weekly_matrix(frame, dm.week_grid(frame))
        stats = dm.demand_statistics(matrix)
        assert stats.loc["A", "WeeksSinceLastShip"] == 1
        assert stats.loc["B", "WeeksSinceLastShip"] == 0

    def test_node_shares_sum_to_one_per_sku(self):
        frame = pd.DataFrame({
            "WeekEnding": ["2026-01-03"] * 3,
            "SKU": ["A", "A", "B"],
            "NodeID": ["N1", "N2", "N1"],
            "UnitsShipped": [30.0, 10.0, 5.0],
        })
        matrix = dm.weekly_matrix(frame, dm.week_grid(frame), keys=["SKU", "NodeID"])
        shares = dm.node_share(matrix)
        assert shares.loc[("A", "N1")] == pytest.approx(0.75)
        assert shares.groupby(level="SKU").sum().round(6).tolist() == [1.0, 1.0]

    def test_standard_deviation_uses_a_sample_denominator(self):
        """ddof=1. With ddof=0 every safety stock in the network is ~4% light."""
        frame = pd.DataFrame({
            "WeekEnding": ["2026-01-03", "2026-01-10", "2026-01-17"],
            "SKU": ["A", "A", "A"],
            "UnitsShipped": [10.0, 20.0, 30.0],
        })
        matrix = dm.weekly_matrix(frame, dm.week_grid(frame))
        stats = dm.demand_statistics(matrix)
        assert stats.loc["A", "WeeklyStdDev"] == pytest.approx(10.0)


class TestOnGeneratedData:
    def test_more_than_one_method_wins_somewhere(self, model):
        """If one method wins everything, the competition is not doing anything."""
        assert model.forecast_summary["Method"].nunique() >= 4

    def test_accuracy_is_plausible_rather_than_perfect(self, model):
        """A forecast scoring 99% on weekly SKU demand is measuring itself."""
        median = model.forecast_summary["ForecastAccuracy"].median()
        assert 0.3 < median < 0.9

    def test_aggregating_reduces_error(self, model):
        """Network-level error must be lower than the median SKU's.

        The risk-pooling result, and a cheap check that the aggregation in the
        API is summing rather than averaging: if it were averaging, the two
        numbers would be the same.
        """
        fit = model.forecast_fit
        weekly = fit.groupby("WeekEnding")[["ActualUnits", "ForecastUnits"]].sum()
        network = dm.wape(weekly["ActualUnits"].to_numpy(), weekly["ForecastUnits"].to_numpy())
        per_sku = 1 - model.forecast_summary["ForecastAccuracy"].median()
        assert network < per_sku
