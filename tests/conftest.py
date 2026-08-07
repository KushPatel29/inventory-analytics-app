"""
Shared fixtures.

test_app.py defined `app` and `client` locally, but test_api.py and
test_moves_api.py request them too and errored at collection because pytest
only shares fixtures through a conftest. Hoisting them here is what makes
those two files runnable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def app():
    from invapp import create_app

    application = create_app()
    application.config.update({"TESTING": True})
    return application


@pytest.fixture
def client(app):
    return app.test_client()
