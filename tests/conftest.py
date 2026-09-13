"""
Shared fixtures.

The model is built once for the whole session. It takes about two seconds -
seven forecasting methods backtested across four hundred SKUs - and rebuilding
it per test would turn a fast suite into a slow one for no extra coverage,
because nothing here mutates it.
"""

from __future__ import annotations

import os

import pandas as pd
import pytest

from invapp.services.pipeline import build_model
from seed.dataset import generate


@pytest.fixture(scope="session")
def sheets() -> dict[str, pd.DataFrame]:
    """The generated workbook, as sheets."""
    return generate()


@pytest.fixture(scope="session")
def model(sheets):
    """The full analysis. Read-only: do not mutate the frames on it."""
    return build_model(sheets)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """A Flask app with the sample loaded and run history in a temp file."""
    monkeypatch.setenv("INVAPP_DB_PATH", str(tmp_path / "runs.db"))
    monkeypatch.setenv("DEMO_AUTOLOAD", "")

    import invapp
    from invapp.services import state, store

    store.DB_PATH = str(tmp_path / "runs.db")
    state.reset_state()

    flask_app = invapp.create_app()
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture()
def loaded_app(app, model):
    """`app`, serving the session-scoped model.

    The model is published directly rather than re-ingested. Running the whole
    pipeline per test would be about two seconds each - four hundred SKUs
    backtested against seven forecasting methods - and with the endpoint list
    parameterised three ways that is several minutes to prove the same model
    over and over. Ingest itself is exercised end to end through the real
    upload route in `test_app.py`.
    """
    from invapp.services.state import set_state

    with app.app_context():
        set_state(model=model)
    return app


@pytest.fixture()
def client(loaded_app):
    return loaded_app.test_client()


@pytest.fixture()
def empty_client(app):
    return app.test_client()


@pytest.fixture(scope="session")
def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
