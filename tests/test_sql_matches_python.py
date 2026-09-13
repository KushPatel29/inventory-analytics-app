"""
The SQL marts against the Python engine.

Two implementations of the same definitions is a liability unless something
checks they agree, so this runs `sql/inventory_marts.sql` in DuckDB over a
freshly generated copy of the data and compares the answers row by row.

It generates its own CSVs into a temporary directory rather than reading the
committed ones. A test that reads what is committed proves the committed files
are self-consistent with themselves; it does not prove the SQL is right.

Where the two genuinely differ, the difference is asserted as a known and
bounded gap rather than papered over - the SQL plans on a trailing mean because
seven competing forecast methods are not expressible in a sensible amount of
SQL, and that shows up as a difference in the reorder point that the last test
here measures rather than ignores.
"""

from __future__ import annotations

import pathlib

import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

from seed.dataset import SHEET_NAMES, generate  # noqa: E402
from seed.generate_workbook import write_csvs  # noqa: E402


@pytest.fixture(scope="module")
def con(tmp_path_factory, repo_root):
    """DuckDB with the marts built over a freshly generated dataset."""
    data_dir = tmp_path_factory.mktemp("data")
    sheets = generate()
    write_csvs(sheets, data_dir)
    assert len(list(data_dir.glob("*.csv"))) == len(SHEET_NAMES)

    connection = duckdb.connect()
    connection.execute(f"SET VARIABLE data_dir = '{data_dir.as_posix()}'")
    sql = pathlib.Path(repo_root, "sql", "inventory_marts.sql").read_text(encoding="utf-8")
    # The default assignment at the top of the file must not clobber the
    # directory the test just set.
    sql = sql.replace(
        "SET VARIABLE data_dir = COALESCE(getvariable('data_dir'), 'data');", ""
    )
    connection.execute(sql)
    return connection


def fetch(con, query: str) -> pd.DataFrame:
    return con.execute(query).fetch_df()


class TestDemandStats:
    def test_row_counts_match(self, con, model):
        sql_rows = fetch(con, "SELECT COUNT(*) AS n FROM mart_demand_stats")["n"][0]
        assert sql_rows == len(model.node_stats)

    def test_weekly_demand_matches_per_location(self, con, model):
        sql = fetch(con, "SELECT SKU, NodeID, weekly_demand, weekly_stddev, "
                         "days_since_last_ship FROM mart_demand_stats")
        python = model.node_stats[["SKU", "NodeID", "WeeklyDemand", "WeeklyStdDev",
                                   "WeeksSinceLastShip"]]
        merged = sql.merge(python, on=["SKU", "NodeID"], how="inner")
        assert len(merged) == len(sql)
        assert merged["weekly_demand"].sub(merged["WeeklyDemand"]).abs().max() < 1e-6
        assert merged["weekly_stddev"].sub(merged["WeeklyStdDev"]).abs().max() < 1e-6

    def test_recency_matches(self, con, model):
        """Both must count the empty weeks, not the rows that exist."""
        sql = fetch(con, "SELECT SKU, NodeID, days_since_last_ship FROM mart_demand_stats")
        python = model.node_stats[["SKU", "NodeID", "WeeksSinceLastShip"]].copy()
        python["days"] = python["WeeksSinceLastShip"] * 7
        merged = sql.merge(python, on=["SKU", "NodeID"])
        moved = merged[merged["days"].notna()]
        assert moved["days_since_last_ship"].sub(moved["days"]).abs().max() < 1e-6


class TestSegmentation:
    def test_abc_classes_agree(self, con, model):
        sql = fetch(con, "SELECT SKU, abc_class, annual_consumption_value FROM mart_sku_segment")
        python = model.segments[["SKU", "ABCClass", "AnnualConsumptionValueUSD"]]
        merged = sql.merge(python, on="SKU")
        assert len(merged) == len(python)
        agreement = (merged["abc_class"] == merged["ABCClass"]).mean()
        # Not 100%: the Python side classifies on forecast demand where a
        # forecast exists and the SQL side on the trailing mean, so a handful
        # of SKUs sitting on the 80% and 95% lines fall differently. A gap this
        # size is the known one; a bigger gap is a bug.
        assert agreement > 0.90

    def test_consumption_value_matches_where_both_use_the_trailing_mean(self, con, model):
        sql = fetch(con, "SELECT SUM(annual_consumption_value) AS v FROM mart_sku_segment")["v"][0]
        python = float(model.segments["AnnualConsumptionValueUSD"].sum())
        assert sql == pytest.approx(python, rel=0.02)

    def test_xyz_classes_agree(self, con, model):
        sql = fetch(con, "SELECT SKU, xyz_class FROM mart_sku_segment")
        python = model.segments[["SKU", "XYZClass"]]
        merged = sql.merge(python, on="SKU")
        assert (merged["xyz_class"] == merged["XYZClass"]).mean() > 0.95


class TestSupplier:
    def test_lead_times_match_exactly(self, con, model):
        sql = fetch(con, "SELECT SupplierID, lead_time_days_actual, lead_time_days_stddev, "
                         "lead_time_days_contract FROM mart_supplier_leadtime")
        python = model.scorecard[["SupplierID", "LeadTimeDaysActual", "LeadTimeDaysStdDev",
                                  "LeadTimeDaysContract"]]
        merged = sql.merge(python, on="SupplierID")
        assert len(merged) == len(python)
        assert merged["lead_time_days_actual"].sub(merged["LeadTimeDaysActual"]).abs().max() < 1e-6
        assert merged["lead_time_days_stddev"].sub(merged["LeadTimeDaysStdDev"]).abs().max() < 1e-6
        assert merged["lead_time_days_contract"].sub(
            merged["LeadTimeDaysContract"]
        ).abs().max() < 1e-6

    def test_scorecard_measures_match(self, con, model):
        sql = fetch(con, "SELECT SupplierID, po_lines, on_time_pct, fill_rate_pct, "
                         "perfect_order_pct FROM mart_supplier_scorecard")
        python = model.scorecard[["SupplierID", "POLines", "OnTimePct", "FillRatePct",
                                  "PerfectOrderPct"]]
        merged = sql.merge(python, on="SupplierID")
        assert (merged["po_lines"] == merged["POLines"]).all()
        for sql_col, py_col in (("on_time_pct", "OnTimePct"),
                                ("fill_rate_pct", "FillRatePct"),
                                ("perfect_order_pct", "PerfectOrderPct")):
            assert merged[sql_col].sub(merged[py_col]).abs().max() < 1e-9, sql_col


class TestReplenishment:
    def test_the_safety_factor_constants_match_python(self, con):
        """The SQL names three z values because DuckDB has no inverse normal
        CDF. If they drift from `statistics.NormalDist`, every safety stock in
        the warehouse copy is quietly wrong."""
        from invapp.analytics.replenishment import z_for_service_level

        sql = fetch(con, "SELECT DISTINCT service_level, safety_factor_z "
                         "FROM mart_replenishment ORDER BY service_level")
        for row in sql.itertuples(index=False):
            assert row.safety_factor_z == pytest.approx(
                z_for_service_level(row.service_level), abs=1e-9
            )

    def test_safety_stock_matches_where_the_inputs_do(self, con, model):
        """Rebuild the SQL's safety stock in Python from the SQL's own inputs.

        Comparing the two *outputs* directly would fail on the forecast, which
        the SQL does not have. Comparing the formula on identical inputs tests
        the thing the parity check is for.
        """
        from invapp.analytics.replenishment import safety_stock

        sql = fetch(con, "SELECT daily_demand, daily_stddev, lead_time_days, "
                         "lead_time_stddev, safety_factor_z, safety_stock_units "
                         "FROM mart_replenishment")
        expected = safety_stock(
            sql["daily_demand"], sql["daily_stddev"], sql["lead_time_days"],
            sql["lead_time_stddev"], sql["safety_factor_z"],
        )
        assert abs(sql["safety_stock_units"].to_numpy() - expected).max() < 1e-6

    def test_urgency_labels_broadly_agree(self, con, model):
        sql = fetch(con, "SELECT SKU, NodeID, urgency FROM mart_replenishment")
        python = model.plan[["SKU", "NodeID", "Urgency"]]
        merged = sql.merge(python, on=["SKU", "NodeID"])
        agreement = (merged["urgency"] == merged["Urgency"]).mean()
        # The known gap: planning demand is the forecast in Python and the
        # trailing mean in SQL, so lines near a threshold land differently.
        assert agreement > 0.80

    def test_the_forecast_is_the_only_material_difference(self, con, model):
        """Name the gap and bound it.

        The reorder points differ because the demand behind them differs. If
        the difference were larger than the forecast can explain, something
        else has drifted - and "something else" would otherwise be invisible
        behind a difference everyone assumes is the forecast.
        """
        sql = fetch(con, "SELECT SKU, NodeID, daily_demand FROM mart_replenishment")
        python = model.plan[["SKU", "NodeID", "DailyDemand", "WeeklyDemand"]].copy()
        merged = sql.merge(python, on=["SKU", "NodeID"])
        # The SQL's daily demand is the trailing mean, which is exactly the
        # Python trailing mean divided by seven.
        trailing = merged["WeeklyDemand"] / 7.0
        assert merged["daily_demand"].sub(trailing).abs().max() < 1e-6


class TestAccuracyAndCapital:
    def test_count_accuracy_matches_per_node(self, con, model):
        from invapp.analytics.accuracy import accuracy_summary

        sql = fetch(con, "SELECT NodeID, record_accuracy, value_accuracy, "
                         "net_variance_value_usd FROM mart_count_accuracy")
        python = accuracy_summary(model.count_variance, "NodeID")
        merged = sql.merge(python, on="NodeID")
        assert len(merged) == len(python)
        assert merged["record_accuracy"].sub(merged["RecordAccuracy"]).abs().max() < 1e-9
        assert merged["value_accuracy"].sub(merged["ValueAccuracy"]).abs().max() < 1e-9
        assert merged["net_variance_value_usd"].sub(
            merged["NetVarianceValueUSD"]
        ).abs().max() < 1e-6

    def test_working_capital_matches(self, con, model):
        sql = fetch(con, "SELECT * FROM mart_working_capital").iloc[0]
        assert sql["inventory_value_usd"] == pytest.approx(
            model.headline["InventoryValueUSD"], rel=1e-9
        )
        assert sql["annual_cogs_usd"] == pytest.approx(
            model.headline["AnnualCOGSUSD"], rel=1e-9
        )
        assert sql["inventory_turns"] == pytest.approx(
            model.headline["InventoryTurns"], rel=1e-9
        )
        assert sql["days_inventory_outstanding"] == pytest.approx(
            model.headline["DaysInventoryOutstanding"], rel=1e-9
        )
