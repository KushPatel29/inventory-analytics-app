"""Per-visitor analysis state.

This was a single module-level `AnalysisState`, shared by every request from
every visitor. On the hosted demo that meant one person's uploaded workbook —
and their holding-cost assumptions — became everyone's: the next visitor's
dashboard silently showed someone else's numbers, and either of them could
overwrite the other mid-read. The README said so, which is better than hiding
it, but a disclosure is not isolation.

State is now keyed by a browser session. The API is unchanged — `get_state()`
and `set_state()` still take no session argument — because resolving the key
belongs here rather than at forty-two call sites.

Three things worth knowing about the design:

* **Outside a request there is no session**, so bootstrap at start-up, the CLI
  and the tests share one baseline state. That is deliberate: the generated
  sample is public synthetic data, and a visitor who has uploaded nothing should
  see it rather than an empty page.
* **A visitor's first write forks them off that baseline**, so uploading never
  mutates what everyone else is reading.
* **Sessions are capped and evicted least-recently-used.** The demo runs on a
  512 MB box and each state can hold several DataFrames; unbounded per-session
  storage would be a memory leak with a queue of visitors attached.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from threading import RLock
from uuid import uuid4

import pandas as pd

# Each state holds the parsed workbook and its derived frames. Twelve concurrent
# visitors is generous for a portfolio demo and bounded enough for a small box.
MAX_SESSIONS = int(os.environ.get("INVAPP_MAX_SESSIONS", "12"))

SESSION_KEY = "invapp_sid"


@dataclass
class AnalysisState:
    sku_stats: pd.DataFrame | None = None
    holding_cost: pd.DataFrame | None = None
    raw_sheets: dict[str, pd.DataFrame] = field(default_factory=dict)
    # True while the app is serving the generated sample rather than an upload,
    # so the UI can say so instead of passing invented numbers off as real.
    demo_data: bool = False
    holding_cost_params: dict = field(default_factory=lambda: {
        "rc": 0.05,   # capital rate
        "sa": 102055.0,  # service cost pool
        "spc": (71466*0.4 + 107128*0.7 + 48280*0.7 + 453626 + 544699*0.5),  # storage pool
        "rr": 0.03,   # risk rate
    })


_lock = RLock()

# The state used when there is no request in flight, and the one a visitor
# inherits before their first write: the generated sample.
_baseline = AnalysisState()

# session id -> state, most-recently-used last.
_sessions: "OrderedDict[str, AnalysisState]" = OrderedDict()


def _session_id() -> str | None:
    """The current browser's session id, or None outside a request.

    Imported lazily and guarded, so this module stays usable from a script or a
    test with no Flask application context.
    """
    try:
        from flask import has_request_context, session
    except Exception:            # pragma: no cover - Flask always present in the app
        return None
    if not has_request_context():
        return None
    try:
        sid = session.get(SESSION_KEY)
        if not sid:
            sid = uuid4().hex
            session[SESSION_KEY] = sid
            session.permanent = False
        return sid
    except Exception:
        # No secret key configured, or the cookie is unreadable. Falling back to
        # the shared baseline keeps the demo working; it does not pretend to
        # isolate.
        return None


def ensure_session() -> str | None:
    """Create the visitor's session id early in the request.

    Called from a before_request hook so the Set-Cookie rides out on the
    response. Without it the id is minted on the first state write, after the
    response exists, and never reaches the browser.
    """
    return _session_id()


def _fork_baseline() -> AnalysisState:
    """A private copy of the baseline for a visitor about to write."""
    return replace(_baseline, raw_sheets=dict(_baseline.raw_sheets),
                   holding_cost_params=dict(_baseline.holding_cost_params))


def set_state(**kwargs):
    with _lock:
        sid = _session_id()
        if sid is None:
            target = _baseline
        else:
            target = _sessions.get(sid)
            if target is None:
                target = _fork_baseline()
                _sessions[sid] = target
            _sessions.move_to_end(sid)
            while len(_sessions) > MAX_SESSIONS:
                _sessions.popitem(last=False)
        for k, v in kwargs.items():
            setattr(target, k, v)


def get_state() -> AnalysisState:
    with _lock:
        sid = _session_id()
        if sid is not None and sid in _sessions:
            _sessions.move_to_end(sid)
            return _sessions[sid]
        return _baseline


def reset_state() -> None:
    """Drop every visitor's state. For tests and for the CLI."""
    global _baseline
    with _lock:
        _baseline = AnalysisState()
        _sessions.clear()


def session_count() -> int:
    """How many visitors currently hold their own state."""
    with _lock:
        return len(_sessions)
