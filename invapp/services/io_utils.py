"""Read an uploaded workbook into DataFrames."""

from __future__ import annotations

import io

import pandas as pd

EXPECTED_SHEETS = [
    "Item Master",
    "Supplier Master",
    "Network Nodes",
    "Demand History",
    "Inventory Snapshot",
    "Purchase Orders",
    "Cycle Counts",
    "Inventory Adjustments",
    "Planning Parameters",
]

# Identifiers that must not be read as numbers. Left to pandas, ``B000042``
# stays text but a workbook whose SKUs happen to be all digits becomes int64,
# then ``42`` will not join to ``"42"`` in any other sheet - and the failure is
# an empty report rather than an error.
ID_COLUMNS = ("SKU", "NodeID", "SupplierID", "PONumber", "CounterID")

MAX_UPLOAD_BYTES = 60 * 1024 * 1024


def load_workbook_sheets(uploaded_file) -> dict[str, pd.DataFrame]:
    """Parse every sheet the app knows about; absent ones come back empty."""
    try:
        uploaded_file.seek(0)
    except Exception:
        pass
    content = uploaded_file.read()
    if not content:
        raise ValueError("The uploaded file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"That file is {len(content) / 1e6:.0f} MB; the limit is "
            f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB."
        )

    try:
        xls = pd.ExcelFile(io.BytesIO(content), engine="openpyxl")
    except Exception as exc:
        raise ValueError(f"That does not look like an .xlsx workbook ({exc}).") from exc

    dtype = dict.fromkeys(ID_COLUMNS, str)
    sheets: dict[str, pd.DataFrame] = {}
    for name in EXPECTED_SHEETS:
        if name in xls.sheet_names:
            sheets[name] = xls.parse(name, dtype=dtype)
        else:
            sheets[name] = pd.DataFrame()
    return sheets


def describe_workbook(sheets: dict[str, pd.DataFrame]) -> list[dict]:
    """Row and column counts per sheet, for the upload confirmation."""
    return [
        {"sheet": name, "rows": int(len(frame)), "columns": int(frame.shape[1])}
        for name, frame in sheets.items()
    ]
