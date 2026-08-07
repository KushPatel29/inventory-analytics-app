from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
import pandas as pd


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
_state = AnalysisState()


def set_state(**kwargs):
    with _lock:
        for k, v in kwargs.items():
            setattr(_state, k, v)


def get_state() -> AnalysisState:
    with _lock:
        return _state
