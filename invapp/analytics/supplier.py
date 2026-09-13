"""
Supplier performance from purchase-order receipts.

Everything here is measured from what was received, not from what was agreed.
That distinction is the whole module: the contracted lead time is an input to
planning, the delivered lead time is a fact, and the difference between them is
a safety stock that is wrong by a computable number of units.

Four measures, then one score:

* **Lead time** - mean, standard deviation and 95th percentile of received
  minus ordered. The standard deviation is the one that reaches the planning
  maths; the mean only moves the reorder point, while the spread sets the
  buffer.
* **On-time** - received on or before the promised date, plus a grace period
  the parameter table owns. Grace is not generosity: a receipt scanned the
  morning after a Friday-evening arrival is not a late delivery.
* **Fill rate** - received over ordered. A short ship is a stockout the
  supplier caused, and it is invisible in an on-time measure that only looks at
  dates.
* **Quality** - rejected over received.

The composite score weights them and grades the result. Weights are declared
here rather than buried, because "our scorecard says 82" is only a useful
sentence if someone can ask what 82 is made of.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SCORE_WEIGHTS = {
    "OnTimePct": 0.35,
    "FillRatePct": 0.30,
    "QualityPct": 0.20,
    "LeadTimeReliability": 0.15,
}

GRADE_BANDS = ((90, "A"), (80, "B"), (70, "C"), (60, "D"))


def grade(score: float) -> str:
    if not np.isfinite(score):
        return "-"
    for threshold, letter in GRADE_BANDS:
        if score >= threshold:
            return letter
    return "F"


def lead_time_facts(purchase_orders: pd.DataFrame) -> pd.DataFrame:
    """Delivered lead time per supplier, alongside what was contracted.

    Only closed receipts count. An order still in transit has no lead time yet,
    and including it as "zero days so far" is how a supplier scorecard reports
    its worst performer as its best in the week after a big order lands.
    """
    columns = ["SupplierID", "LeadTimeDaysActual", "LeadTimeDaysStdDev",
               "LeadTimeDaysP95", "LeadTimeDaysContract", "LeadTimeGapDays", "ReceiptCount"]
    if purchase_orders is None or purchase_orders.empty:
        return pd.DataFrame(columns=columns)

    po = purchase_orders.copy()
    po["OrderDate"] = pd.to_datetime(po["OrderDate"], errors="coerce")
    po["ReceivedDate"] = pd.to_datetime(po.get("ReceivedDate"), errors="coerce")
    received = po.dropna(subset=["OrderDate", "ReceivedDate"]).copy()
    if received.empty:
        return pd.DataFrame(columns=columns)

    received["ActualLeadDays"] = (received["ReceivedDate"] - received["OrderDate"]).dt.days

    out = (
        received.groupby("SupplierID", as_index=False)
        .agg(
            LeadTimeDaysActual=("ActualLeadDays", "mean"),
            LeadTimeDaysStdDev=("ActualLeadDays", lambda s: float(s.std(ddof=1)) if len(s) > 1 else 0.0),
            LeadTimeDaysP95=("ActualLeadDays", lambda s: float(np.percentile(s, 95))),
            ReceiptCount=("ActualLeadDays", "size"),
        )
    )

    if "LeadTimeDaysContract" in po.columns:
        contract = po.groupby("SupplierID", as_index=False)["LeadTimeDaysContract"].first()
        out = out.merge(contract, on="SupplierID", how="left")
    else:
        promised = po.dropna(subset=["OrderDate"]).copy()
        promised["PromisedDate"] = pd.to_datetime(promised.get("PromisedDate"), errors="coerce")
        promised["ContractDays"] = (promised["PromisedDate"] - promised["OrderDate"]).dt.days
        contract = (
            promised.groupby("SupplierID", as_index=False)["ContractDays"]
            .median()
            .rename(columns={"ContractDays": "LeadTimeDaysContract"})
        )
        out = out.merge(contract, on="SupplierID", how="left")

    out["LeadTimeDaysStdDev"] = out["LeadTimeDaysStdDev"].fillna(0.0)
    out["LeadTimeDaysContract"] = out["LeadTimeDaysContract"].fillna(out["LeadTimeDaysActual"])
    out["LeadTimeGapDays"] = out["LeadTimeDaysActual"] - out["LeadTimeDaysContract"]
    return out[columns]


def build_scorecard(
    purchase_orders: pd.DataFrame,
    suppliers: pd.DataFrame,
    params: dict,
) -> pd.DataFrame:
    """One row per supplier: delivery, completeness, quality and a grade."""
    if purchase_orders is None or purchase_orders.empty:
        return pd.DataFrame()

    grace = float(params.get("OnTimeGraceDays", 2))
    po = purchase_orders.copy()
    for col in ("OrderDate", "PromisedDate", "ReceivedDate"):
        po[col] = pd.to_datetime(po.get(col), errors="coerce")
    for col in ("QtyOrdered", "QtyReceived", "QtyRejected", "UnitCost"):
        po[col] = pd.to_numeric(po.get(col), errors="coerce").fillna(0.0)

    closed = po.dropna(subset=["ReceivedDate"]).copy()
    if closed.empty:
        return pd.DataFrame()

    closed["LateDays"] = (closed["ReceivedDate"] - closed["PromisedDate"]).dt.days
    closed["IsOnTime"] = closed["LateDays"] <= grace
    closed["IsComplete"] = closed["QtyReceived"] >= closed["QtyOrdered"]
    closed["IsClean"] = closed["QtyRejected"] <= 0
    # A perfect order is on time AND complete AND clean. Measured separately
    # each of the three looks fine at 95%; multiplied, the same operation is
    # delivering a perfect order five times in six.
    closed["IsPerfect"] = closed["IsOnTime"] & closed["IsComplete"] & closed["IsClean"]
    closed["SpendUSD"] = closed["QtyReceived"] * closed["UnitCost"]

    card = (
        closed.groupby("SupplierID", as_index=False)
        .agg(
            POLines=("PONumber", "size"),
            UnitsOrdered=("QtyOrdered", "sum"),
            UnitsReceived=("QtyReceived", "sum"),
            UnitsRejected=("QtyRejected", "sum"),
            SpendUSD=("SpendUSD", "sum"),
            OnTimeLines=("IsOnTime", "sum"),
            CompleteLines=("IsComplete", "sum"),
            PerfectLines=("IsPerfect", "sum"),
            AvgLateDays=("LateDays", "mean"),
            WorstLateDays=("LateDays", "max"),
        )
    )
    card["OnTimePct"] = card["OnTimeLines"] / card["POLines"].replace(0, np.nan)
    card["CompletePct"] = card["CompleteLines"] / card["POLines"].replace(0, np.nan)
    card["PerfectOrderPct"] = card["PerfectLines"] / card["POLines"].replace(0, np.nan)
    card["FillRatePct"] = card["UnitsReceived"] / card["UnitsOrdered"].replace(0, np.nan)
    card["DefectRatePct"] = card["UnitsRejected"] / card["UnitsReceived"].replace(0, np.nan)
    card["QualityPct"] = 1.0 - card["DefectRatePct"].fillna(0.0)

    card = card.merge(lead_time_facts(po), on="SupplierID", how="left")
    # Reliability is the coefficient of variation of the lead time, inverted
    # and bounded: a supplier whose spread is a third of their mean scores 0.67.
    card["LeadTimeReliability"] = (
        1.0 - (card["LeadTimeDaysStdDev"] / card["LeadTimeDaysActual"].replace(0, np.nan))
    ).clip(lower=0.0, upper=1.0).fillna(0.0)

    score = np.zeros(len(card))
    for column, weight in SCORE_WEIGHTS.items():
        score += card[column].fillna(0.0).to_numpy() * weight * 100.0
    card["Score"] = np.round(score, 1)
    card["Grade"] = [grade(s) for s in card["Score"]]

    if suppliers is not None and not suppliers.empty:
        cols = [c for c in ("SupplierID", "SupplierName", "Country", "SupplierRegion",
                            "PaymentTerms", "OrderCostUSD") if c in suppliers.columns]
        card = card.merge(suppliers[cols], on="SupplierID", how="left")

    return card.sort_values(["Score", "SupplierID"], ascending=[False, True], kind="stable")


def open_purchase_orders(purchase_orders: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Orders placed and not yet received, with an expected arrival.

    Feeds two things: in-transit quantity for the inventory position, and the
    expedite list - an order already past its promised date on a SKU that is
    below safety stock is the most actionable row in the whole app.
    """
    columns = ["PONumber", "SupplierID", "SKU", "NodeID", "OrderDate", "PromisedDate",
               "QtyOrdered", "UnitCost", "DaysOutstanding", "DaysLate", "IsOverdue"]
    if purchase_orders is None or purchase_orders.empty:
        return pd.DataFrame(columns=columns)

    po = purchase_orders.copy()
    for col in ("OrderDate", "PromisedDate", "ReceivedDate"):
        po[col] = pd.to_datetime(po.get(col), errors="coerce")
    open_lines = po[po["ReceivedDate"].isna()].copy()
    if open_lines.empty:
        return pd.DataFrame(columns=columns)

    open_lines["QtyOrdered"] = pd.to_numeric(open_lines["QtyOrdered"], errors="coerce").fillna(0.0)
    open_lines["UnitCost"] = pd.to_numeric(open_lines["UnitCost"], errors="coerce").fillna(0.0)
    open_lines["DaysOutstanding"] = (as_of - open_lines["OrderDate"]).dt.days
    open_lines["DaysLate"] = (as_of - open_lines["PromisedDate"]).dt.days
    open_lines["IsOverdue"] = open_lines["DaysLate"] > 0
    return open_lines[columns]


def in_transit_by_node(open_pos: pd.DataFrame) -> pd.DataFrame:
    """In-transit units per SKU and node, for the inventory position."""
    if open_pos.empty:
        return pd.DataFrame(columns=["SKU", "NodeID", "InTransitUnits", "OverdueUnits"])
    return (
        open_pos.assign(OverdueUnits=np.where(open_pos["IsOverdue"], open_pos["QtyOrdered"], 0.0))
        .groupby(["SKU", "NodeID"], as_index=False)
        .agg(InTransitUnits=("QtyOrdered", "sum"), OverdueUnits=("OverdueUnits", "sum"))
    )


__all__ = [
    "GRADE_BANDS",
    "SCORE_WEIGHTS",
    "build_scorecard",
    "grade",
    "in_transit_by_node",
    "lead_time_facts",
    "open_purchase_orders",
]
