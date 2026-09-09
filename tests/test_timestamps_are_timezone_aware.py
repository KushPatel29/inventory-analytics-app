"""Run timestamps carry a timezone, and nothing reaches for the retired call.

`save_run` stamped rows with `datetime.utcnow()`, which returns a naive
datetime that merely happens to be UTC. It is deprecated in 3.12 and slated for
removal, and the value it produced was ambiguous in the run picker: a string
with no offset that a reader would reasonably assume was local time.

The sweep matters more than the one call site. A single fixed line is easy; the
next one someone writes is the problem.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from invapp.services import store

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "node_modules", "__pycache__", ".git"}


def _first_party_sources() -> list[Path]:
    here = Path(__file__).resolve()
    return [
        p for p in ROOT.rglob("*.py")
        if not SKIP_DIRS & set(p.relative_to(ROOT).parts)
        and p.resolve() != here  # this file names the call in order to ban it
    ]


def test_the_sweep_actually_reads_this_package():
    """Guards the guard: a glob that matched nothing would pass silently."""
    found = _first_party_sources()
    assert any(p.name == "store.py" for p in found), "store.py not in the sweep"
    assert len(found) > 5, f"only {len(found)} source files found - glob is wrong"


def test_no_first_party_module_calls_utcnow():
    offenders = [
        str(p.relative_to(ROOT))
        for p in _first_party_sources()
        if "utcnow()" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert not offenders, (
        f"{offenders} call datetime.utcnow(), which is deprecated and returns a "
        "naive datetime - use datetime.now(timezone.utc)"
    )


def test_a_saved_run_is_stamped_with_an_offset(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "runs.db")
    sku = pd.DataFrame({"SKU": ["A"], "OnHandWeightTotal": [1.0]})
    holding = pd.DataFrame({"SKU": ["A"], "HoldingCost": [1.0]})

    run_id = store.save_run(sku, holding, {"horizon": 4})
    stamped = {r["id"]: r["created_at"] for r in store.list_runs()}[run_id]

    parsed = datetime.fromisoformat(stamped)
    assert parsed.tzinfo is not None, (
        f"created_at {stamped!r} has no offset, so a reader cannot tell whether "
        "it is UTC or local"
    )
    assert parsed.utcoffset().total_seconds() == 0
