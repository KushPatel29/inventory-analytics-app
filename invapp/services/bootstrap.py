"""
Load the generated sample workbook at startup.

The app is upload-driven, which is right for the people it was built for: they
export a workbook from the warehouse system every week and drop it in. It is
wrong for anyone visiting a hosted link, who has no such file and lands on an
empty dashboard with nothing to look at.

So the demo builds the sample workbook and pushes it through `ingest_sheets` -
the same function the upload route calls - before the first request arrives.
Uploading a real workbook afterwards replaces it exactly as before.

Off unless DEMO_AUTOLOAD is set.
"""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

_started = threading.Event()


def _enabled() -> bool:
    return str(os.getenv("DEMO_AUTOLOAD", "")).strip().lower() in {"1", "true", "yes", "on"}


def is_demo_data_loaded() -> bool:
    """Whether the state currently holds the generated sample, not an upload."""
    try:
        from invapp.services.state import get_state

        return bool(getattr(get_state(), "demo_data", False))
    except Exception:
        return False


def load_sample_data() -> bool:
    """Generate the sample workbook and ingest it. Returns True on success."""
    started = time.perf_counter()
    try:
        from invapp.services.ingest import ingest_sheets
        from invapp.services.state import set_state
        from seed.generate_workbook import generate

        # Built in memory: nothing is written to disk, so a read-only or
        # ephemeral container filesystem is fine.
        sheets = generate(
            skus=int(os.getenv("DEMO_AUTOLOAD_SKUS", "260")),
            weeks=int(os.getenv("DEMO_AUTOLOAD_WEEKS", "26")),
        )
        summary = ingest_sheets(sheets, persist=False)
        set_state(demo_data=True)
        logger.info(
            "bootstrap.sample_loaded",
            extra={
                "skus": summary.get("total_skus"),
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return True
    except Exception:
        logger.warning("bootstrap.sample_load_failed", exc_info=True)
        return False


def start_bootstrap(app) -> None:
    """
    Load the sample in a background thread so startup is not delayed.

    Guarded so it runs once per process even if create_app is called again.
    """
    if not _enabled() or _started.is_set():
        return
    _started.set()

    def _run() -> None:
        with app.app_context():
            load_sample_data()

    threading.Thread(target=_run, name="demo-bootstrap", daemon=True).start()
    logger.info("bootstrap.scheduled")
