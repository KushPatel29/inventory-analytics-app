"""
Turn whatever the workbook holds into the columns and types the analytics need.

An export is not a schema. Numbers arrive as text with currency symbols in
them, dates arrive in whatever Excel decided the cell format was, a column that
was empty on the day of the export is missing entirely, and a SKU that is
``B000042`` in one sheet is ``b000042 `` in another. Every one of those is a
silent wrong answer downstream rather than an error - a cost column read as
text sums to zero, and zero is a perfectly plausible-looking number on a page.

So each sheet gets a declared schema: which columns must exist, what type each
one is, and what it defaults to when it does not. Nothing past this module has
to guard a type again.
"""

from __future__ import annotations

import pandas as pd

# column -> ("num" | "int" | "date" | "text", default)
SCHEMAS: dict[str, dict[str, tuple[str, object]]] = {
    "Item Master": {
        "SKU": ("text", ""),
        "ItemDescription": ("text", ""),
        "Department": ("text", "Unclassified"),
        "Category": ("text", "Unclassified"),
        "Brand": ("text", ""),
        "SupplierID": ("text", ""),
        "UnitCost": ("num", 0.0),
        "UnitPrice": ("num", 0.0),
        "CasePack": ("int", 1),
        "MinOrderQty": ("int", 1),
        "UnitWeightLb": ("num", 1.0),
        "UnitCubeFt": ("num", 0.1),
        "TempZone": ("text", "Ambient"),
        "Lifecycle": ("text", "Core"),
        "LaunchDate": ("date", None),
    },
    "Supplier Master": {
        "SupplierID": ("text", ""),
        "SupplierName": ("text", ""),
        "Country": ("text", ""),
        "SupplierRegion": ("text", ""),
        "LeadTimeDays": ("num", 14.0),
        "OrderCostUSD": ("num", 250.0),
        "PaymentTerms": ("text", ""),
    },
    "Network Nodes": {
        "NodeID": ("text", ""),
        "NodeName": ("text", ""),
        "Region": ("text", ""),
        "City": ("text", ""),
        "NodeType": ("text", "Fulfillment Center"),
        "StorageCapacityCubeFt": ("num", 0.0),
        "DemandShare": ("num", 0.0),
    },
    "Demand History": {
        "WeekEnding": ("date", None),
        "SKU": ("text", ""),
        "NodeID": ("text", ""),
        "UnitsRequested": ("num", 0.0),
        "UnitsShipped": ("num", 0.0),
        "OrderLines": ("num", 0.0),
        "NetSalesUSD": ("num", 0.0),
    },
    "Inventory Snapshot": {
        "SnapshotDate": ("date", None),
        "SKU": ("text", ""),
        "NodeID": ("text", ""),
        "OnHandUnits": ("num", 0.0),
        "ReservedUnits": ("num", 0.0),
        "OldestReceiptDate": ("date", None),
        "LastReceiptDate": ("date", None),
        "LastCountDate": ("date", None),
        "UnitCost": ("num", 0.0),
        "StorageType": ("text", ""),
        "BinCount": ("num", 1.0),
    },
    "Purchase Orders": {
        "PONumber": ("text", ""),
        "SupplierID": ("text", ""),
        "SKU": ("text", ""),
        "NodeID": ("text", ""),
        "OrderDate": ("date", None),
        "PromisedDate": ("date", None),
        "ReceivedDate": ("date", None),
        "QtyOrdered": ("num", 0.0),
        "QtyReceived": ("num", 0.0),
        "QtyRejected": ("num", 0.0),
        "UnitCost": ("num", 0.0),
        "Status": ("text", ""),
    },
    "Cycle Counts": {
        "CountDate": ("date", None),
        "SKU": ("text", ""),
        "NodeID": ("text", ""),
        "SystemQty": ("num", 0.0),
        "CountedQty": ("num", 0.0),
        "CountType": ("text", "Cycle"),
        "ReasonCode": ("text", ""),
        "UnitCost": ("num", 0.0),
        "CounterID": ("text", ""),
    },
    "Inventory Adjustments": {
        "AdjustmentDate": ("date", None),
        "SKU": ("text", ""),
        "NodeID": ("text", ""),
        "AdjustmentType": ("text", ""),
        "Units": ("num", 0.0),
        "UnitCost": ("num", 0.0),
        "ReasonCode": ("text", ""),
    },
    "Planning Parameters": {
        "Parameter": ("text", ""),
        "Value": ("num", 0.0),
        "Description": ("text", ""),
    },
}

# Sheets without which the app has nothing to say. Everything else degrades to
# an empty report rather than an exception, because a warehouse that has not
# run a count programme yet should still get a replenishment plan.
REQUIRED_SHEETS = ("Item Master", "Demand History", "Inventory Snapshot")

_CURRENCY = r"[\$,\s]"


def _to_number(series: pd.Series, default: float) -> pd.Series:
    """Numbers that survived a spreadsheet: strip currency, then coerce.

    ``$1,204.50`` and ``1204.5`` have to end up as the same float. A plain
    ``to_numeric`` turns the first into NaN, and NaN filled with the default
    turns a six-figure cost into zero without anything being raised.
    """
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce").fillna(default)
    cleaned = series.astype(str).str.replace(_CURRENCY, "", regex=True)
    cleaned = cleaned.replace({"": None, "nan": None, "None": None, "-": None})
    return pd.to_numeric(cleaned, errors="coerce").fillna(default)


def clean_sheet(frame: pd.DataFrame, sheet: str) -> pd.DataFrame:
    """Coerce one sheet to its declared schema, adding missing columns."""
    schema = SCHEMAS.get(sheet)
    if schema is None:
        return frame.copy()

    df = frame.copy() if frame is not None else pd.DataFrame()
    for column, (kind, default) in schema.items():
        if column not in df.columns:
            df[column] = default if kind != "date" else pd.NaT

        if kind == "num":
            df[column] = _to_number(df[column], float(default))
        elif kind == "int":
            df[column] = _to_number(df[column], float(default)).round().astype("int64")
        elif kind == "date":
            df[column] = pd.to_datetime(df[column], errors="coerce")
        else:
            df[column] = df[column].astype("string").fillna(str(default)).str.strip()

    # Keep any extra columns the export happens to carry - a real WMS adds them
    # without warning and dropping them silently loses information a later
    # version of this app might want.
    ordered = list(schema.keys()) + [c for c in df.columns if c not in schema]
    return df[ordered]


def clean_sheets(sheets: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Clean every known sheet, filling in the ones the workbook omitted."""
    out: dict[str, pd.DataFrame] = {}
    for sheet in SCHEMAS:
        out[sheet] = clean_sheet(sheets.get(sheet, pd.DataFrame()), sheet)
    for name, frame in (sheets or {}).items():
        if name not in out:
            out[name] = frame
    return out


def missing_required(sheets: dict[str, pd.DataFrame]) -> list[str]:
    """Which of the sheets the app cannot work without are empty."""
    return [name for name in REQUIRED_SHEETS if sheets.get(name) is None or sheets[name].empty]


def planning_parameters(frame: pd.DataFrame, defaults: dict) -> dict:
    """Read the parameter sheet into a dict, keeping the defaults it omits.

    Merged over the defaults rather than replacing them: a workbook exported
    before a parameter existed should not blank it out and take the safety
    stock for every SKU to zero.
    """
    params = dict(defaults)
    if frame is None or frame.empty:
        return params
    for row in frame.itertuples(index=False):
        name = str(getattr(row, "Parameter", "")).strip()
        if not name:
            continue
        value = getattr(row, "Value", None)
        try:
            params[name] = float(value)
        except (TypeError, ValueError):
            continue
    return params


def prepare_inventory(
    inventory: pd.DataFrame,
    in_transit: pd.DataFrame | None,
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    """Add the derived columns every downstream report expects.

    ``DaysOnHandAge`` is measured from the *oldest* receipt still on hand, not
    the last one. Measuring from the last receipt makes a pallet that has sat
    since March look three days old because a case arrived on Tuesday, and the
    ageing report then finds nothing.
    """
    if inventory is None or inventory.empty:
        return pd.DataFrame(columns=[
            "SKU", "NodeID", "OnHandUnits", "ReservedUnits", "InTransitUnits",
            "AvailableUnits", "InventoryValueUSD", "DaysOnHandAge", "DaysSinceCount",
        ])

    df = inventory.copy()
    df["OldestReceiptDate"] = pd.to_datetime(df.get("OldestReceiptDate"), errors="coerce")
    df["LastCountDate"] = pd.to_datetime(df.get("LastCountDate"), errors="coerce")
    df["DaysOnHandAge"] = (as_of - df["OldestReceiptDate"]).dt.days.fillna(0).clip(lower=0)
    df["DaysSinceCount"] = (as_of - df["LastCountDate"]).dt.days

    if in_transit is not None and not in_transit.empty:
        df = df.merge(in_transit, on=["SKU", "NodeID"], how="left")
    if "InTransitUnits" not in df.columns:
        df["InTransitUnits"] = 0.0
    df["InTransitUnits"] = pd.to_numeric(df["InTransitUnits"], errors="coerce").fillna(0.0)
    if "OverdueUnits" not in df.columns:
        df["OverdueUnits"] = 0.0
    df["OverdueUnits"] = pd.to_numeric(df["OverdueUnits"], errors="coerce").fillna(0.0)

    df["AvailableUnits"] = (df["OnHandUnits"] - df["ReservedUnits"]).clip(lower=0)
    df["InventoryValueUSD"] = df["OnHandUnits"] * df["UnitCost"]
    return df


def inventory_by_sku(inventory: pd.DataFrame) -> pd.DataFrame:
    """Roll the node-level snapshot up to the SKU."""
    if inventory is None or inventory.empty:
        return pd.DataFrame(columns=["SKU", "OnHandUnits", "ReservedUnits", "InTransitUnits",
                                     "InventoryValueUSD", "NodesStocked"])
    return (
        inventory.groupby("SKU", as_index=False)
        .agg(
            OnHandUnits=("OnHandUnits", "sum"),
            ReservedUnits=("ReservedUnits", "sum"),
            InTransitUnits=("InTransitUnits", "sum"),
            InventoryValueUSD=("InventoryValueUSD", "sum"),
            NodesStocked=("NodeID", "nunique"),
        )
    )


def align_ids(sheets: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Make SKU and NodeID join across sheets.

    Upper-cased and stripped, in every sheet that carries them. Two sheets that
    disagree on the case of an identifier produce a left join full of nulls and
    a report that quietly covers half the catalogue.
    """
    out = {}
    for name, frame in sheets.items():
        df = frame.copy()
        for column in ("SKU", "NodeID", "SupplierID"):
            if column in df.columns:
                df[column] = df[column].astype("string").fillna("").str.strip().str.upper()
        out[name] = df
    return out


def as_of_date(sheets: dict[str, pd.DataFrame]) -> pd.Timestamp:
    """The date the analysis is run against.

    The snapshot date, not today. A workbook exported in August and opened in
    November should report November-in-August, not eighty days of phantom
    ageing on every pallet in the building.
    """
    inventory = sheets.get("Inventory Snapshot")
    if inventory is not None and not inventory.empty and "SnapshotDate" in inventory.columns:
        snapshot = pd.to_datetime(inventory["SnapshotDate"], errors="coerce").dropna()
        if not snapshot.empty:
            return pd.Timestamp(snapshot.max()).normalize()

    demand = sheets.get("Demand History")
    if demand is not None and not demand.empty and "WeekEnding" in demand.columns:
        weeks = pd.to_datetime(demand["WeekEnding"], errors="coerce").dropna()
        if not weeks.empty:
            return pd.Timestamp(weeks.max()).normalize()

    return pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()


__all__ = [
    "REQUIRED_SHEETS",
    "SCHEMAS",
    "align_ids",
    "as_of_date",
    "clean_sheet",
    "clean_sheets",
    "inventory_by_sku",
    "missing_required",
    "planning_parameters",
    "prepare_inventory",
]
