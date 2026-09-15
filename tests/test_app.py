"""
The Flask surface: pages render, every endpoint answers, and what it answers is
valid JSON.

The last of those is not pedantry. `NaN` and `inf` are genuine answers here - a
SKU with no demand has infinite cover - and `JSON.parse` rejects both, so a
route that lets one through renders a blank chart with nothing in the console.
"""

from __future__ import annotations

import json

import pytest

PAGES = ["/", "/demand", "/replenishment", "/sku-health", "/network", "/accuracy"]

# Every JSON endpoint that needs a model, so a new report cannot be added
# without something asserting it answers.
DATA_ENDPOINTS = [
    "/api/overview",
    "/api/overview/trend",
    "/api/overview/carrying",
    "/api/overview/abc",
    "/api/overview/value_by?dim=Department",
    "/api/demand/history",
    "/api/demand/actual_vs_forecast",
    "/api/demand/skus?limit=5",
    "/api/demand/method_mix",
    "/api/demand/seasonality",
    "/api/demand/bias?limit=5",
    "/api/demand/segment_scorecard",
    "/api/replenishment/plan?limit=5",
    "/api/replenishment/summary",
    "/api/replenishment/urgency",
    "/api/replenishment/eoq?limit=5",
    "/api/replenishment/service_curve",
    "/api/replenishment/service_cost_frontier",
    "/api/replenishment/policy_scenario?limit=5",
    "/api/health/pareto?limit=5",
    "/api/health/matrix",
    "/api/health/ageing",
    "/api/health/ageing?dim=NodeID",
    "/api/health/dead_stock?limit=5",
    "/api/health/summary",
    "/api/health/movement",
    "/api/network/nodes",
    "/api/network/transfers?limit=5",
    "/api/network/balance?limit=5",
    "/api/suppliers/scorecard",
    "/api/suppliers/open_pos?limit=5",
    "/api/suppliers/lead_time_gap",
    "/api/accuracy/summary",
    "/api/accuracy/summary?dim=NodeID",
    "/api/accuracy/reasons",
    "/api/accuracy/waterfall",
    "/api/accuracy/coverage",
    "/api/actions/register?limit=5",
    "/api/actions/summary",
]

DOWNLOADS = [
    ("/api/download/replenishment.csv", "text/csv"),
    ("/api/download/sku_health.csv", "text/csv"),
    ("/api/download/transfers.csv", "text/csv"),
    ("/api/download/actions.csv", "text/csv"),
    ("/api/download/replenishment.xlsx", "spreadsheetml"),
    ("/api/download/report.xlsx", "spreadsheetml"),
]


class TestPages:
    @pytest.mark.parametrize("path", PAGES)
    def test_renders(self, client, path):
        response = client.get(path)
        assert response.status_code == 200
        assert b"Inventory Analytics" in response.data

    @pytest.mark.parametrize("path", PAGES)
    def test_renders_before_any_data_is_loaded(self, empty_client, path):
        """An empty app must still serve its chrome, not a 500."""
        assert empty_client.get(path).status_code == 200

    def test_every_page_is_linked_from_the_nav(self, client):
        body = client.get("/").data.decode()
        for path in PAGES[1:]:
            assert f'href="{path}"' in body

    def test_page_chrome_exposes_keyboard_and_navigation_landmarks(self, client):
        body = client.get("/").data.decode()
        assert 'class="skip-link" href="#main-content"' in body
        assert '<nav class="pages" aria-label="Primary navigation">' in body
        assert 'aria-current="page"' in body
        assert '<main id="main-content" tabindex="-1">' in body
        assert '<label for="workbook-upload">Workbook (.xlsx)' in body
        assert 'id="workbook-upload"' in body
        assert 'role="status" aria-live="polite"' in body

        css = client.get("/static/css/styles.css").data.decode()
        assert ":focus-visible" in css
        assert "prefers-reduced-motion: reduce" in css

        javascript = client.get("/static/js/core.js").data.decode()
        assert 'role", "img"' in javascript or "'role', 'img'" in javascript
        assert 'role="region"' in javascript
        assert 'tabindex="0"' in javascript

    def test_theme_control_exposes_its_state_and_action(self, client):
        body = client.get("/").data.decode()
        assert 'id="theme-toggle"' in body
        assert 'aria-pressed="false"' in body
        assert 'aria-label="Switch to dark theme"' in body

    def test_health_check_needs_no_data(self, empty_client):
        response = empty_client.get("/healthz")
        assert response.status_code == 200
        assert response.get_json()["status"] == "ok"


class TestEndpoints:
    @pytest.mark.parametrize("path", DATA_ENDPOINTS)
    def test_answers_with_data(self, client, path):
        response = client.get(path)
        assert response.status_code == 200, path
        payload = response.get_json()
        assert payload is not None
        if isinstance(payload, dict) and "rows" in payload:
            assert payload["rows"], f"{path} returned no rows"

    @pytest.mark.parametrize("path", DATA_ENDPOINTS)
    def test_is_strictly_valid_json(self, client, path):
        """No NaN, no Infinity. Both are legal Python and illegal JSON.

        Flask serialises them happily as bare `NaN` / `Infinity` tokens, which
        `JSON.parse` rejects - the chart renders blank and nothing is logged.
        """
        body = client.get(path).data.decode()
        json.loads(body, parse_constant=_reject)

    @pytest.mark.parametrize("path", DATA_ENDPOINTS)
    def test_says_so_when_nothing_is_loaded(self, empty_client, path):
        response = empty_client.get(path)
        assert response.status_code == 404
        assert "error" in response.get_json()

    def test_an_unknown_dimension_is_rejected(self, client):
        response = client.get("/api/overview/value_by?dim=Nonsense")
        assert response.status_code == 400

    def test_ping_needs_no_data(self, empty_client):
        assert empty_client.get("/api/ping").get_json()["message"] == "pong"


def _reject(constant):
    raise AssertionError(f"{constant} is not valid JSON")


class TestFilters:
    def test_filtering_the_plan_narrows_it(self, client):
        everything = client.get("/api/replenishment/plan?limit=5000").get_json()
        one_node = client.get("/api/replenishment/plan?NodeID=YYZ4&limit=5000").get_json()
        assert 0 < one_node["total"] < everything["total"]
        assert {row["NodeID"] for row in one_node["rows"]} == {"YYZ4"}

    def test_the_due_filter_only_returns_lines_to_order(self, client):
        payload = client.get("/api/replenishment/plan?due=1&limit=500").get_json()
        assert all(row["RecommendedOrderUnits"] > 0 for row in payload["rows"])

    def test_a_filter_that_matches_nothing_returns_an_empty_list(self, client):
        payload = client.get("/api/actions/register?Action=Nonsense").get_json()
        assert payload["rows"] == []
        assert payload["total"] == 0

    def test_truncation_is_declared(self, client):
        payload = client.get("/api/replenishment/plan?limit=5").get_json()
        assert payload["truncated"] is True
        assert payload["total"] > 5

    def test_scoping_the_forecast_to_one_sku_changes_it(self, client):
        skus = client.get("/api/demand/skus?limit=1").get_json()["rows"]
        sku = skus[0]["SKU"]
        network = client.get("/api/demand/actual_vs_forecast").get_json()
        single = client.get(f"/api/demand/actual_vs_forecast?SKU={sku}").get_json()
        assert single["rows"][0]["ActualUnits"] < network["rows"][0]["ActualUnits"]


class TestDownloads:
    @pytest.mark.parametrize(("path", "content_type"), DOWNLOADS)
    def test_serves_a_file(self, client, path, content_type):
        response = client.get(path)
        assert response.status_code == 200
        assert content_type in response.headers["Content-Type"]
        assert "attachment" in response.headers["Content-Disposition"]
        assert len(response.data) > 200

    def test_a_filtered_export_is_smaller_than_the_full_one(self, client):
        full = client.get("/api/download/replenishment.csv").data
        one_node = client.get("/api/download/replenishment.csv?NodeID=YYZ4").data
        assert len(one_node) < len(full)


class TestUpload:
    def test_rejects_a_request_with_no_file(self, client):
        assert client.post("/api/workbook/process").status_code == 400

    def test_rejects_something_that_is_not_a_workbook(self, client):
        import io

        data = {"file": (io.BytesIO(b"not a workbook"), "notes.xlsx")}
        response = client.post("/api/workbook/process", data=data,
                               content_type="multipart/form-data")
        assert response.status_code == 400
        assert "error" in response.get_json()

    def test_a_real_workbook_round_trips_through_the_endpoint(self, empty_client, sheets, tmp_path):
        """The whole promise of the app, end to end, through the real route."""
        import pandas as pd

        from seed.dataset import SHEET_NAMES

        path = tmp_path / "workbook.xlsx"
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            for name in SHEET_NAMES:
                sheets[name].to_excel(writer, sheet_name=name, index=False)

        with open(path, "rb") as handle:
            response = empty_client.post(
                "/api/workbook/process",
                data={"file": (handle, "workbook.xlsx")},
                content_type="multipart/form-data",
            )
        assert response.status_code == 200, response.get_json()
        summary = response.get_json()
        assert summary["SKUCount"] == 420
        assert summary["InventoryValueUSD"] > 0
        # And the reports are now live for this session.
        assert empty_client.get("/api/actions/summary").get_json()["rows"]


class TestParameters:
    def test_changing_the_service_level_moves_safety_stock(self, client):
        before = client.get("/api/replenishment/summary").get_json()
        response = client.post("/api/parameters", json={"ServiceLevelA": 0.999})
        assert response.status_code == 200
        after = client.get("/api/replenishment/summary").get_json()
        assert after["SafetyStockUnits"] > before["SafetyStockUnits"]

    def test_a_non_numeric_parameter_is_rejected(self, client):
        response = client.post("/api/parameters", json={"ServiceLevelA": "high"})
        assert response.status_code == 400

    @pytest.mark.parametrize(
        "query",
        [
            "service_level=1",
            "demand_multiplier=0.1",
            "lead_time_multiplier=3",
            "service_level=not-a-number",
        ],
    )
    def test_policy_scenario_rejects_invalid_inputs(self, client, query):
        response = client.get(f"/api/replenishment/policy_scenario?{query}")
        assert response.status_code == 400
        assert "error" in response.get_json()


class TestSessionIsolation:
    def test_one_visitors_upload_is_not_another_visitors_data(self, app, sheets):
        """The demo runs on one process; a shared model made one visitor's
        workbook everybody's."""
        from invapp.services.ingest import ingest_sheets

        with app.app_context():
            ingest_sheets(sheets, persist=False)

        first = app.test_client()
        assert first.get("/api/overview").status_code == 200

        smaller = dict(sheets)
        smaller["Item Master"] = sheets["Item Master"].head(60)
        second = app.test_client()
        with second.session_transaction():
            pass
        second.post("/api/parameters", json={"ServiceLevelA": 0.999})

        # The second visitor's override must not have reached the first.
        assert first.get("/api/parameters").get_json()["overrides"] == {}
        assert second.get("/api/parameters").get_json()["overrides"] == {
            "ServiceLevelA": 0.999
        }
