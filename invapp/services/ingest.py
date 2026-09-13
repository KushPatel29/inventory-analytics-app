"""
Turn a set of workbook sheets into the analysis the app serves.

This logic used to live inside the upload route, which meant the only way to
get data into the app was to POST a file. The hosted demo needs the same result
without a visitor having to find and upload a workbook first, so it is a
function and both paths call it. There is no demo-only branch: the sample
workbook goes through exactly what a real upload goes through.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from invapp.services.pipeline import AnalysisModel, build_model
from invapp.services.state import get_state, set_state
from invapp.services.store import save_run

logger = logging.getLogger(__name__)


def ingest_sheets(sheets: dict[str, pd.DataFrame], *, persist: bool = True) -> dict[str, Any]:
    """Build the model from raw sheets and publish it as this visitor's state."""
    overrides = dict(getattr(get_state(), "param_overrides", {}) or {})
    if overrides:
        # A visitor's parameter changes are applied by appending them to the
        # workbook's own parameter sheet, so they travel through exactly the
        # same reader the sheet does rather than through a second code path.
        params = sheets.get("Planning Parameters")
        extra = pd.DataFrame(
            [{"Parameter": k, "Value": v, "Description": "Set in the app"}
             for k, v in overrides.items()]
        )
        sheets = dict(sheets)
        sheets["Planning Parameters"] = (
            pd.concat([params, extra], ignore_index=True) if params is not None and not params.empty
            else extra
        )

    model = build_model(sheets)
    set_state(model=model, demo_data=False)

    run_id = None
    if persist:
        try:
            run_id = save_run(model)
        except Exception:
            logger.warning("ingest.save_run_failed", exc_info=True)

    summary = dict(model.headline)
    summary["run_id"] = run_id
    summary["sheets"] = [
        {"sheet": name, "rows": int(len(frame))}
        for name, frame in sheets.items()
        if frame is not None
    ]
    return summary


def current_model() -> AnalysisModel | None:
    """The model for this visitor, or None if nothing has been ingested."""
    model = getattr(get_state(), "model", None)
    return model if isinstance(model, AnalysisModel) else None


__all__ = ["current_model", "ingest_sheets"]
