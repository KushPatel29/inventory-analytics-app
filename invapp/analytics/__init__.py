"""
The inventory analytics engine.

Pure functions over DataFrames - no Flask, no session, no I/O. Everything the
app shows is computed here, which is what makes it testable against the
formulas rather than against a rendered page, and what lets the same code
produce the Power BI marts and the SQL parity check.

Modules map to the questions a planner asks:

* :mod:`demand` - what will we ship, and how well do we know
* :mod:`replenishment` - what to buy, how much, and when
* :mod:`segmentation` - which SKUs deserve which policy
* :mod:`ageing` - what is not moving and what that is worth
* :mod:`supplier` - who delivers what they promised
* :mod:`accuracy` - does the system quantity match the shelf
* :mod:`allocation` - is the stock in the right building
* :mod:`working_capital` - what it all costs to hold
* :mod:`actions` - and therefore, what to do
"""

from __future__ import annotations

from invapp.analytics import (
    accuracy,
    actions,
    ageing,
    allocation,
    demand,
    replenishment,
    segmentation,
    supplier,
    working_capital,
)

__all__ = [
    "accuracy",
    "actions",
    "ageing",
    "allocation",
    "demand",
    "replenishment",
    "segmentation",
    "supplier",
    "working_capital",
]
