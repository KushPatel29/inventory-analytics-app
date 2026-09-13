"""
The action register: every finding in the app, in one list, ranked by money.

This module exists because an analysis that ends in a chart has not finished.
Six pages of correct numbers still leave a planner to work out what to do on
Monday, and the six answers are always one of the same verbs: reorder,
expedite, transfer, reduce, liquidate, investigate.

So every other module contributes rows here in a single shape - verb, what,
where, how much, what it is worth, and one sentence of why - and the register
is sorted by value at risk or released. A hundred and forty rows of "review
this SKU" is not a work list; twelve rows adding to $600k is.

The dollar figures are deliberately not comparable across verbs and the column
says so: a reorder's impact is contribution at risk if it is not placed, a
liquidation's is cash released. Adding them would be nonsense, and ranking
within a verb is the only ordering that means anything.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

ACTIONS = ("Expedite", "Reorder", "Transfer", "Investigate", "Reduce", "Liquidate")

# Ordering when two rows carry the same dollar impact. Time-critical verbs
# first: an expedite that slips a day cannot be recovered, a liquidation that
# slips a week costs a week of carrying cost.
ACTION_PRIORITY = {name: i for i, name in enumerate(ACTIONS)}

IMPACT_MEANING = {
    "Expedite": "Contribution at risk before the late order lands",
    "Reorder": "Contribution at risk over the lead time if the order is not placed",
    "Transfer": "Shortfall prevented plus carrying cost released",
    "Investigate": "Absolute value of the counted variance",
    "Reduce": "Annual carrying cost on stock above target",
    "Liquidate": "Book value of stock that has not moved",
}


def _rows(frame: pd.DataFrame, columns: dict[str, str]) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    for target, source in columns.items():
        out[target] = frame[source] if source in frame.columns else None
    return out


def build_register(
    plan: pd.DataFrame,
    transfers: pd.DataFrame,
    ageing: pd.DataFrame,
    variance: pd.DataFrame,
    open_pos: pd.DataFrame,
    scorecard: pd.DataFrame,
    params: dict,
    *,
    variance_threshold_usd: float = 750.0,
) -> pd.DataFrame:
    """Assemble every recommendation into one ranked list."""
    carrying_rate = float(params.get("CarryingCostRate", 0.24))
    blocks: list[pd.DataFrame] = []

    # --- Reorder ------------------------------------------------------------
    if plan is not None and not plan.empty:
        due = plan[(plan["RecommendedOrderUnits"] > 0) & (plan["Urgency"] != "Healthy")].copy()
        if not due.empty:
            block = _rows(due, {
                "SKU": "SKU", "ItemDescription": "ItemDescription", "NodeID": "NodeID",
                "Department": "Department", "ABCClass": "ABCClass", "Units": "RecommendedOrderUnits",
                "ValueUSD": "RecommendedOrderValue", "ImpactUSD": "StockoutExposureUSD",
                "SupplierID": "SupplierID",
            })
            block["Action"] = "Reorder"
            block["Urgency"] = due["Urgency"].to_numpy()
            block["DueInDays"] = np.where(
                due["DailyDemand"] > 0,
                (due["InventoryPosition"] - due["SafetyStockUnits"]).clip(lower=0)
                / due["DailyDemand"].replace(0, np.nan),
                np.nan,
            )
            block["Rationale"] = [
                f"Position {p:,.0f} is at or below the reorder point of {r:,.0f}; "
                f"{c:.1f} weeks of cover against a {lt:.0f}-day lead time"
                for p, r, c, lt in zip(
                    due["InventoryPosition"], due["ReorderPointUnits"],
                    due["WeeksOfCover"].replace(np.inf, 99), due["LeadTimeDaysActual"], strict=True,
                )
            ]
            blocks.append(block)

    # --- Expedite -----------------------------------------------------------
    # An overdue purchase order matters only where the destination is actually
    # short. Listing every late delivery produces a report the buyer already
    # has; listing the late deliveries that are about to cause a stockout is
    # the one worth their morning.
    if open_pos is not None and not open_pos.empty and plan is not None and not plan.empty:
        overdue = open_pos[open_pos["IsOverdue"]].copy()
        if not overdue.empty:
            # The destinations where the late order is the thing holding the
            # line above its reorder point. Testing the plain urgency instead
            # finds almost nothing, because an in-transit order counts toward
            # the inventory position and therefore reads as covered right up
            # until it fails to arrive.
            column = ("OverdueIsLoadBearing" if "OverdueIsLoadBearing" in plan.columns
                      else "RecommendedOrderUnits")
            risk = plan[plan[column].astype(bool) | (plan["Urgency"] != "Healthy")][
                ["SKU", "NodeID", "ItemDescription", "Department", "ABCClass",
                 "StockoutExposureUSD", "InventoryPosition", "SafetyStockUnits", "UnitCost"]
            ]
            # The open-order frame carries its own UnitCost, so merging both
            # sides suffixes them and `merged["UnitCost"]` stops existing.
            overdue = overdue.drop(columns=["UnitCost"], errors="ignore")
            merged = overdue.merge(risk, on=["SKU", "NodeID"], how="inner")
            if not merged.empty:
                block = _rows(merged, {
                    "SKU": "SKU", "ItemDescription": "ItemDescription", "NodeID": "NodeID",
                    "Department": "Department", "ABCClass": "ABCClass", "Units": "QtyOrdered",
                    "ImpactUSD": "StockoutExposureUSD", "SupplierID": "SupplierID",
                })
                block["Action"] = "Expedite"
                block["ValueUSD"] = (merged["QtyOrdered"] * merged["UnitCost"]).to_numpy()
                block["Urgency"] = "Overdue receipt"
                block["DueInDays"] = 0.0
                block["Rationale"] = [
                    f"{po} is {late:,.0f} days past its promised date; without it {node} "
                    f"is at or below its reorder point"
                    for po, late, node in zip(
                        merged["PONumber"], merged["DaysLate"], merged["NodeID"], strict=True
                    )
                ]
                blocks.append(block)

    # --- Transfer -----------------------------------------------------------
    if transfers is not None and not transfers.empty:
        block = _rows(transfers, {
            "SKU": "SKU", "ItemDescription": "ItemDescription", "Department": "Department",
            "ABCClass": "ABCClass", "Units": "TransferUnits", "ValueUSD": "TransferValueUSD",
            "ImpactUSD": "BenefitUSD",
        })
        block["Action"] = "Transfer"
        block["NodeID"] = transfers["ToNode"].to_numpy()
        block["Urgency"] = "Rebalance"
        block["DueInDays"] = 3.0
        block["SupplierID"] = None
        block["Rationale"] = [
            f"Move {u:,.0f} from {f} to {t}: {r}"
            for u, f, t, r in zip(
                transfers["TransferUnits"], transfers["FromNode"],
                transfers["ToNode"], transfers["Rationale"], strict=True,
            )
        ]
        blocks.append(block)

    # --- Reduce and liquidate ----------------------------------------------
    if ageing is not None and not ageing.empty:
        excess = ageing[(ageing["ExcessValueUSD"] > 0) & (~ageing["IsDeadStock"])].copy()
        if not excess.empty:
            block = _rows(excess, {
                "SKU": "SKU", "ItemDescription": "ItemDescription", "NodeID": "NodeID",
                "Department": "Department", "ABCClass": "ABCClass", "Units": "ExcessUnits",
                "ValueUSD": "ExcessValueUSD",
            })
            block["Action"] = "Reduce"
            block["ImpactUSD"] = (excess["ExcessValueUSD"] * carrying_rate).to_numpy()
            block["Urgency"] = "Stop ordering"
            block["DueInDays"] = 30.0
            block["SupplierID"] = None
            block["Rationale"] = [
                f"{w:.0f} weeks of cover against a target of {t:,.0f} units - suspend replenishment"
                for w, t in zip(
                    excess["WeeksOfCover"].replace(np.inf, 99), excess["TargetUnits"], strict=True
                )
            ]
            blocks.append(block)

        dead = ageing[ageing["IsDeadStock"] & (ageing["InventoryValueUSD"] > 0)].copy()
        if not dead.empty:
            block = _rows(dead, {
                "SKU": "SKU", "ItemDescription": "ItemDescription", "NodeID": "NodeID",
                "Department": "Department", "ABCClass": "ABCClass", "Units": "OnHandUnits",
                "ValueUSD": "InventoryValueUSD", "ImpactUSD": "InventoryValueUSD",
            })
            block["Action"] = "Liquidate"
            block["Urgency"] = "Write down or clear"
            block["DueInDays"] = 60.0
            block["SupplierID"] = None
            block["Rationale"] = [
                f"No shipment in {int(d) if np.isfinite(d) else 999} days; "
                f"aged {int(a)} days, provision at {r:.0%}"
                for d, a, r in zip(
                    dead["DaysSinceLastShip"].fillna(999), dead["AgeDays"],
                    dead["ReserveRate"], strict=True,
                )
            ]
            blocks.append(block)

    # --- Investigate --------------------------------------------------------
    if variance is not None and not variance.empty:
        bad = variance[
            (~variance["IsAccurate"]) & (variance["AbsVarianceValueUSD"] >= variance_threshold_usd)
        ].copy()
        if not bad.empty:
            block = _rows(bad, {
                "SKU": "SKU", "ItemDescription": "ItemDescription", "NodeID": "NodeID",
                "Department": "Department", "Units": "AbsVarianceUnits",
                "ValueUSD": "AbsVarianceValueUSD", "ImpactUSD": "AbsVarianceValueUSD",
            })
            block["Action"] = "Investigate"
            block["ABCClass"] = None
            block["Urgency"] = "Count variance"
            block["DueInDays"] = 7.0
            block["SupplierID"] = None
            block["Rationale"] = [
                f"Counted {c:,.0f} against a system quantity of {s:,.0f} ({reason})"
                for c, s, reason in zip(
                    bad["CountedQty"], bad["SystemQty"], bad["ReasonCode"], strict=True
                )
            ]
            blocks.append(block)

    if scorecard is not None and not scorecard.empty:
        failing = scorecard[scorecard["Grade"].isin(("D", "F"))].copy()
        if not failing.empty:
            block = pd.DataFrame(index=failing.index)
            block["Action"] = "Investigate"
            block["SKU"] = None
            block["ItemDescription"] = failing.get("SupplierName", failing["SupplierID"]).to_numpy()
            block["NodeID"] = None
            block["Department"] = "Supplier management"
            block["ABCClass"] = None
            block["Units"] = failing["POLines"].to_numpy()
            block["ValueUSD"] = failing["SpendUSD"].to_numpy()
            # What the supplier's unreliability costs in safety stock: the extra
            # buffer their lead-time spread forces the network to hold, priced
            # at the carrying rate.
            block["ImpactUSD"] = (failing["SpendUSD"] * (1.0 - failing["OnTimePct"].fillna(0.0))
                                  * carrying_rate).to_numpy()
            block["Urgency"] = "Supplier review"
            block["DueInDays"] = 14.0
            block["SupplierID"] = failing["SupplierID"].to_numpy()
            block["Rationale"] = [
                f"Grade {g} on {n:,.0f} receipts: {ot:.0%} on time, {fr:.0%} fill rate, "
                f"delivering {gap:+.0f} days against contract"
                for g, n, ot, fr, gap in zip(
                    failing["Grade"], failing["POLines"], failing["OnTimePct"].fillna(0.0),
                    failing["FillRatePct"].fillna(0.0), failing["LeadTimeGapDays"].fillna(0.0),
                    strict=True,
                )
            ]
            blocks.append(block)

    if not blocks:
        return pd.DataFrame(columns=[
            "Action", "SKU", "ItemDescription", "NodeID", "Department", "ABCClass",
            "Units", "ValueUSD", "ImpactUSD", "Urgency", "DueInDays", "SupplierID",
            "Rationale", "ImpactMeaning", "Priority",
        ])

    register = pd.concat(blocks, ignore_index=True)
    register["ImpactUSD"] = pd.to_numeric(register["ImpactUSD"], errors="coerce").fillna(0.0)
    register["ValueUSD"] = pd.to_numeric(register["ValueUSD"], errors="coerce").fillna(0.0)
    register["Units"] = pd.to_numeric(register["Units"], errors="coerce").fillna(0.0)
    register["ImpactMeaning"] = register["Action"].map(IMPACT_MEANING)
    register["Priority"] = register["Action"].map(ACTION_PRIORITY)
    register = register.sort_values(
        ["ImpactUSD", "Priority", "SKU"], ascending=[False, True, True], kind="stable"
    ).reset_index(drop=True)
    register.insert(0, "Rank", np.arange(1, len(register) + 1))
    return register


def register_summary(register: pd.DataFrame) -> pd.DataFrame:
    """Counts and dollars per verb - the summary the page leads with."""
    if register is None or register.empty:
        return pd.DataFrame(columns=["Action", "Items", "Units", "ValueUSD", "ImpactUSD",
                                     "ImpactMeaning"])
    out = (
        register.groupby("Action", as_index=False)
        .agg(
            Items=("Action", "size"),
            Units=("Units", "sum"),
            ValueUSD=("ValueUSD", "sum"),
            ImpactUSD=("ImpactUSD", "sum"),
        )
    )
    out["ImpactMeaning"] = out["Action"].map(IMPACT_MEANING)
    out["Priority"] = out["Action"].map(ACTION_PRIORITY)
    return out.sort_values("Priority", kind="stable").drop(columns=["Priority"])


__all__ = ["ACTIONS", "ACTION_PRIORITY", "IMPACT_MEANING", "build_register", "register_summary"]
