"""One visitor's workbook does not become another visitor's dashboard.

Analysis state was a single module-level object shared by every request. On the
hosted demo that meant an upload replaced what everyone else was reading, and
the holding-cost assumptions with it — two people using the tool at once got
each other's numbers, with no error to say so.

State is now keyed by browser session. These tests drive two independent
clients, which is the only way this can be checked: a single client cannot tell
a private state from a shared one.
"""
from __future__ import annotations

import pandas as pd
import pytest

from invapp import create_app
from invapp.services.state import (
    MAX_SESSIONS,
    get_state,
    reset_state,
    session_count,
    set_state,
)


@pytest.fixture()
def app():
    application = create_app()
    application.config.update(TESTING=True)
    yield application
    reset_state()


def _frame(tag: str) -> pd.DataFrame:
    return pd.DataFrame({"SKU": [tag], "OnHandWeightTotal": [1.0]})


def test_two_clients_do_not_share_an_upload(app):
    alice, bob = app.test_client(), app.test_client()

    with alice:
        alice.get("/healthz")                      # establishes a session
        set_state(sku_stats=_frame("alice"), demo_data=False)
        assert get_state().sku_stats["SKU"].iloc[0] == "alice"

    with bob:
        bob.get("/healthz")
        seen = get_state().sku_stats
        assert seen is None or seen["SKU"].iloc[0] != "alice", (
            "the second visitor is reading the first visitor's workbook"
        )
        set_state(sku_stats=_frame("bob"))

    with alice:
        alice.get("/healthz")
        assert get_state().sku_stats["SKU"].iloc[0] == "alice", (
            "the first visitor's data was overwritten by the second"
        )


def test_holding_cost_assumptions_are_not_global(app):
    alice, bob = app.test_client(), app.test_client()
    with alice:
        alice.get("/healthz")
        set_state(holding_cost_params={"rc": 0.99, "sa": 1.0, "spc": 1.0, "rr": 0.01})
    with bob:
        bob.get("/healthz")
        assert get_state().holding_cost_params["rc"] != 0.99, (
            "one visitor's capital rate is being applied to another's inventory"
        )


def test_a_visitor_who_uploads_nothing_still_sees_the_sample(app):
    """Isolation must not turn the shared demo data into an empty page."""
    reset_state()
    set_state(sku_stats=_frame("generated-sample"), demo_data=True)  # bootstrap, no request
    visitor = app.test_client()
    with visitor:
        visitor.get("/healthz")
        state = get_state()
        assert state.sku_stats is not None, "a fresh visitor sees nothing at all"
        assert state.sku_stats["SKU"].iloc[0] == "generated-sample"
        assert state.demo_data is True


def test_a_visitors_write_does_not_mutate_the_shared_baseline(app):
    reset_state()
    set_state(sku_stats=_frame("generated-sample"), demo_data=True)
    visitor = app.test_client()
    with visitor:
        visitor.get("/healthz")
        set_state(sku_stats=_frame("uploaded"))
    # Back outside any request: the baseline must be untouched.
    assert get_state().sku_stats["SKU"].iloc[0] == "generated-sample", (
        "an upload rewrote the sample every other visitor reads"
    )


def test_sessions_are_capped_so_the_box_cannot_be_exhausted(app):
    reset_state()
    for i in range(MAX_SESSIONS + 5):
        client = app.test_client()
        with client:
            client.get("/healthz")
            set_state(sku_stats=_frame(f"visitor-{i}"))
    assert session_count() <= MAX_SESSIONS, (
        f"{session_count()} sessions held against a cap of {MAX_SESSIONS} — "
        "each one holds DataFrames, so this is a memory leak with a queue"
    )
