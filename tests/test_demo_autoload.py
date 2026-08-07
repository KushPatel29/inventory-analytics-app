"""
The hosted demo has to work without anyone uploading anything.

The app is upload-driven, which is right for its users and wrong for a visitor
following a link: they have no workbook, so every panel is empty. The demo
build generates the sample and pushes it through the same ingest the upload
route uses, before the first request arrives.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

from invapp.services import bootstrap
from invapp.services.ingest import ingest_sheets
from invapp.services.state import get_state, set_state
from seed.generate_workbook import generate


@pytest.fixture
def clean_state():
    set_state(sku_stats=None, holding_cost=None, raw_sheets={}, demo_data=False)
    yield
    set_state(sku_stats=None, holding_cost=None, raw_sheets={}, demo_data=False)


class TestAutoloadFlag:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("DEMO_AUTOLOAD", raising=False)
        assert bootstrap._enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "ON"])
    def test_on_when_asked(self, monkeypatch, value):
        monkeypatch.setenv("DEMO_AUTOLOAD", value)
        assert bootstrap._enabled() is True


class TestSampleLoad:
    def test_load_populates_the_analysis_state(self, clean_state, monkeypatch):
        monkeypatch.setenv("DEMO_AUTOLOAD_SKUS", "40")
        monkeypatch.setenv("DEMO_AUTOLOAD_WEEKS", "8")
        assert bootstrap.load_sample_data() is True

        state = get_state()
        assert state.sku_stats is not None and not state.sku_stats.empty
        assert state.holding_cost is not None and not state.holding_cost.empty
        assert state.raw_sheets

    def test_load_marks_the_data_as_generated(self, clean_state, monkeypatch):
        monkeypatch.setenv("DEMO_AUTOLOAD_SKUS", "40")
        monkeypatch.setenv("DEMO_AUTOLOAD_WEEKS", "8")
        bootstrap.load_sample_data()
        assert bootstrap.is_demo_data_loaded() is True, (
            "the UI banner depends on this; without it the demo passes invented "
            "numbers off as real"
        )

    def test_nothing_is_written_to_disk(self, clean_state, monkeypatch, tmp_path):
        """
        The sample is built in memory so a read-only or ephemeral container
        filesystem is fine.
        """
        monkeypatch.setenv("DEMO_AUTOLOAD_SKUS", "40")
        monkeypatch.setenv("DEMO_AUTOLOAD_WEEKS", "8")
        monkeypatch.chdir(tmp_path)
        bootstrap.load_sample_data()
        assert not list(tmp_path.rglob("*.xlsx"))


class TestSharedIngestPath:
    def test_upload_and_autoload_use_the_same_function(self):
        """
        The upload route used to carry the pipeline inline. If it drifts back,
        the demo and a real upload can start producing different results from
        the same sheets.
        """
        import inspect

        from invapp.api import process_workbook

        source = inspect.getsource(process_workbook)
        assert "ingest_sheets" in source
        assert "aggregate_final_data" not in source, "pipeline leaked back into the route"

    def test_autoload_result_matches_an_upload_of_the_same_workbook(self, clean_state):
        sheets = generate(skus=40, weeks=8)

        from_direct = ingest_sheets(sheets, persist=False)
        direct_state = get_state().sku_stats.copy()

        # Same workbook, but through the HTTP upload route.
        set_state(sku_stats=None, holding_cost=None, raw_sheets={}, demo_data=False)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            for name, frame in sheets.items():
                frame.to_excel(writer, sheet_name=name, index=False)

        from invapp import create_app

        client = create_app().test_client()
        resp = client.post(
            "/api/workbook/process",
            data={"file": (io.BytesIO(buf.getvalue()), "workbook.xlsx")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        uploaded_state = get_state().sku_stats.copy()

        assert from_direct["total_skus"] == int(uploaded_state["SKU"].nunique())
        assert len(direct_state) == len(uploaded_state)


class TestDemoBanner:
    def test_banner_appears_only_while_showing_generated_data(self, clean_state, monkeypatch):
        from invapp import create_app

        client = create_app().test_client()

        body = client.get("/").get_data(as_text=True)
        assert "Sample data." not in body

        monkeypatch.setenv("DEMO_AUTOLOAD_SKUS", "40")
        monkeypatch.setenv("DEMO_AUTOLOAD_WEEKS", "8")
        bootstrap.load_sample_data()

        body = client.get("/").get_data(as_text=True)
        assert "Sample data." in body
        assert "generate_workbook.py" in body
