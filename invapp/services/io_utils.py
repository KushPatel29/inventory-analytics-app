from __future__ import annotations

import io
import pandas as pd

EXPECTED_SHEETS = [
    "Sales History",
    "Cost Value",
    "Inventory Detail",
    "Production Batch",
    "Inventory Detail1",
    "Key Account",
    "Product Detail",
]


def load_workbook_sheets(uploaded_file) -> dict[str, pd.DataFrame]:
    try:
        uploaded_file.seek(0)
    except Exception:
        pass
    content = uploaded_file.read()
    if not content:
        raise ValueError("Uploaded file is empty.")
    bio = io.BytesIO(content)
    xls = pd.ExcelFile(bio, engine="openpyxl")
    missing = [s for s in EXPECTED_SHEETS if s not in xls.sheet_names]
    if missing:
        # allow partial uploads; only warn
        pass
    dfs: dict[str, pd.DataFrame] = {}
    for name in EXPECTED_SHEETS:
        if name in xls.sheet_names:
            dfs[name] = xls.parse(name, dtype={"SKU": str})
        else:
            dfs[name] = pd.DataFrame()
    return dfs

