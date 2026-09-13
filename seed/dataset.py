"""
Invent a retail fulfillment network and everything its systems would record
about inventory: an item master, seven distribution nodes, eighteen months of
weekly shipment history, on-hand stock, purchase orders, cycle counts and
adjustment postings.

Why generated at all: the app is upload-driven, which is right for the people
it was built for and useless to anyone visiting a link. So the repo ships the
export instead of the warehouse.

Why *this* shape: every report the app draws needs something in the data to
find, and a uniform random dataset has none of it.

* Demand is drawn from a gamma-Poisson, not a normal, with the gamma shape
  varying per SKU. That is what puts spread in the coefficient of variation,
  which is what makes an XYZ classification more than a coin toss.
* Velocity is log-normal, so a fifth of the catalogue carries most of the
  value and the ABC curve bends. A flat catalogue makes Pareto analysis a
  straight line and the whole page pointless.
* Contracted lead times disagree with delivered ones, per supplier and in both
  directions. Safety stock computed from the contract is then wrong by a
  knowable amount, which is the finding the supplier page exists to produce.
* Some items are deliberately overstocked, some starved, some launched
  mid-history and some dying. Without those there is nothing to reorder,
  nothing to transfer and nothing to liquidate.
* Counted quantities disagree with system quantities at a rate that varies by
  node and by class, because that is the pattern real count programmes find:
  accuracy is worst where cycle-count frequency is lowest.

Rows only exist where something happened, exactly as a real export would have
them - there is no row saying a SKU shipped zero units this week. Anything
computing an average over history has to reindex onto the full calendar first,
and :mod:`invapp.analytics.demand` does.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from seed.catalog import (
    ADJUSTMENT_TYPES,
    BRANDS,
    COLD_CATEGORIES,
    COUNT_REASONS,
    DEPARTMENT_SUPPLIERS,
    DEPARTMENTS,
    MODIFIERS,
    NODES,
    PACK_DESCRIPTORS,
    PLANNING_PARAMETERS,
    SUPPLIER_FIELDS,
    SUPPLIERS,
)

# Purchase-order shape. `EXPECTED_PO_LINES` has to agree with the line-count
# draw below or the quantity on each line is wrong by the ratio between them.
EXPECTED_PO_LINES = 12.0
# Centres the buyer's over/under-ordering multiplier on 1.0: a lognormal drawn
# at mu=0 has a *mean* of exp(sigma^2/2), so leaving it there would buy 32% more
# than the business sells and call the surplus a finding.
BUYER_FACTOR_MU = -0.2650
# Receipts are short of orders by whatever is still in transit plus the
# occasional short ship, so the network has to order slightly more than it sells
# to hold its position. Calibrated against the generated history rather than
# guessed; `tests/test_generated_data.py` pins the resulting ratio.
PURCHASE_COVER = 1.090

DEFAULT_SEED = 20260829
DEFAULT_SKUS = 420
DEFAULT_WEEKS = 78
# A Saturday. Retail weeks end on Saturday, and a week-ending date that is not
# a consistent weekday makes every week-over-week comparison a lie.
DEFAULT_END = date(2026, 8, 29)

SHEET_NAMES = (
    "Item Master",
    "Supplier Master",
    "Network Nodes",
    "Demand History",
    "Inventory Snapshot",
    "Purchase Orders",
    "Cycle Counts",
    "Inventory Adjustments",
    "Planning Parameters",
)


# --------------------------------------------------------------------------
# Dimensions
# --------------------------------------------------------------------------
def build_nodes() -> pd.DataFrame:
    return pd.DataFrame(list(NODES))


def build_suppliers() -> pd.DataFrame:
    return pd.DataFrame(list(SUPPLIERS), columns=list(SUPPLIER_FIELDS))


def _department_draw(rng: np.random.Generator, n: int) -> np.ndarray:
    names = np.array([d["Department"] for d in DEPARTMENTS])
    weights = np.array([d["Share"] for d in DEPARTMENTS], dtype=float)
    return rng.choice(names, size=n, p=weights / weights.sum())


def build_items(rng: np.random.Generator, n: int, end: date, weeks: int) -> pd.DataFrame:
    """The item master, plus the hidden per-SKU demand parameters.

    The demand parameters ride along on the same frame rather than living in a
    parallel dict: they are needed by four of the builders below, and a second
    structure keyed by SKU is one more thing to keep in step.
    """
    by_name = {d["Department"]: d for d in DEPARTMENTS}
    departments = _department_draw(rng, n)

    rows = []
    for i, dept_name in enumerate(departments):
        dept = by_name[dept_name]
        category = str(rng.choice(dept["Categories"]))
        brand = str(rng.choice(BRANDS))
        modifier = str(rng.choice(MODIFIERS))
        pack_desc = str(rng.choice(PACK_DESCRIPTORS, p=[0.42, 0.20, 0.15, 0.11, 0.08, 0.04]))

        # Unit cost spans three orders of magnitude across the catalogue, which
        # is what makes ABC-by-value differ from ABC-by-volume. A department
        # anchor keeps a $340 bag of dog food out of the data.
        anchor = {
            "Electronics": 46.0, "Home & Kitchen": 27.0, "Grocery": 6.5,
            "Apparel": 18.0, "Toys & Games": 21.0, "Health & Beauty": 12.0,
            "Sports & Outdoors": 38.0, "Office Products": 9.0,
            "Pet Supplies": 14.0, "Baby": 16.0,
        }[dept_name]
        unit_cost = float(np.clip(rng.lognormal(np.log(anchor), 0.62), 1.2, 900.0))
        margin = float(np.clip(rng.normal(dept["MarginPct"], 0.06), 0.05, 0.68))
        unit_price = unit_cost / (1.0 - margin)

        case_pack = int(rng.choice((1, 4, 6, 8, 12, 24, 48), p=[0.22, 0.18, 0.18, 0.12, 0.16, 0.10, 0.04]))
        weight_lb = float(np.clip(rng.lognormal(np.log(1.9), 0.85), 0.05, 68.0))
        cube_ft = float(np.clip(weight_lb * rng.uniform(0.05, 0.28), 0.01, 22.0))

        temp_zone = COLD_CATEGORIES.get(category, "Ambient")

        # Lifecycle drives what the SKU-health page has to find. Shares are
        # deliberate: a catalogue with no dying items has no dead stock, and one
        # where a third of it is dying is not a business anyone runs.
        life_roll = rng.random()
        if life_roll < 0.11:
            lifecycle = "New"
            launch_week = int(rng.integers(weeks - 42, weeks - 7))
        elif life_roll < 0.21:
            lifecycle = "End of Life"
            launch_week = 0
        elif life_roll < 0.60:
            lifecycle = "Core"
            launch_week = 0
        else:
            lifecycle = "Mature"
            launch_week = 0
        # Spread the decline across the whole window, not just its tail: an
        # item that only starts dying in the last month is never 180 days
        # without a shipment, and 180 days is what "obsolete" means here.
        # Clamped rather than clipped after the fact: a short --weeks run makes
        # `weeks - 62` negative, and a negative index slices from the end of the
        # array instead of failing, so the decay lands on the wrong weeks.
        eol_week = (
            int(rng.integers(max(4, weeks - 62), max(6, weeks - 6)))
            if lifecycle == "End of Life" else weeks + 99
        )

        supplier_id = str(rng.choice(DEPARTMENT_SUPPLIERS[dept_name]))

        # Demand shape. `Dispersion` is the gamma shape parameter: small means
        # lumpy and unforecastable (a Z item), large means smooth (an X item).
        base_weekly = float(np.clip(rng.lognormal(np.log(34.0), 1.35), 0.35, 5200.0))
        dispersion = float(np.clip(rng.gamma(2.6, 2.4), 0.55, 22.0))
        trend = float(np.clip(rng.normal(0.0008, 0.0055), -0.016, 0.017))
        promo_count = int(rng.integers(0, 7))

        rows.append({
            "SKU": f"B0{i:05d}",
            "ItemDescription": f"{brand} {modifier} {category[:-1] if category.endswith('s') and len(category) > 4 else category} {pack_desc}",
            "Department": dept_name,
            "Category": category,
            "Brand": brand,
            "SupplierID": supplier_id,
            "UnitCost": round(unit_cost, 2),
            "UnitPrice": round(unit_price, 2),
            "CasePack": case_pack,
            "MinOrderQty": int(case_pack * rng.choice((1, 2, 4, 8), p=[0.4, 0.3, 0.2, 0.1])),
            "UnitWeightLb": round(weight_lb, 3),
            "UnitCubeFt": round(cube_ft, 4),
            "TempZone": temp_zone,
            "Lifecycle": lifecycle,
            "LaunchDate": (
                end - timedelta(days=7 * (weeks - launch_week))
                if launch_week > 0
                # Everything else predates the window. Spreading it over the
                # previous few years is what makes item age a usable filter
                # rather than one date repeated four hundred times.
                else end - timedelta(days=7 * weeks + int(rng.integers(30, 2200)))
            ).isoformat(),
            # Not written to the workbook - the generator's own parameters.
            "gen_base_weekly": base_weekly,
            "gen_dispersion": dispersion,
            "gen_trend": trend,
            "gen_promo_count": promo_count,
            "gen_launch_week": launch_week,
            "gen_eol_week": eol_week,
        })

    items = pd.DataFrame(rows)
    # Velocity rank decides how wide the assortment is spread across the
    # network: a fast item is stocked everywhere, a slow one sits in one place.
    items["gen_velocity_rank"] = items["gen_base_weekly"].rank(method="first", ascending=False)
    return items


ITEM_MASTER_COLUMNS = [
    "SKU", "ItemDescription", "Department", "Category", "Brand", "SupplierID",
    "UnitCost", "UnitPrice", "CasePack", "MinOrderQty", "UnitWeightLb",
    "UnitCubeFt", "TempZone", "Lifecycle", "LaunchDate",
]


# --------------------------------------------------------------------------
# Demand
# --------------------------------------------------------------------------
def _week_endings(end: date, weeks: int) -> list[date]:
    return [end - timedelta(days=7 * (weeks - 1 - i)) for i in range(weeks)]


def _node_assignment(rng: np.random.Generator, rank: float, n_items: int) -> list[dict]:
    """Which nodes carry this SKU, and each node's share of its demand."""
    pct = rank / max(n_items, 1)
    if pct <= 0.15:
        count = len(NODES)
    elif pct <= 0.45:
        count = 4
    elif pct <= 0.75:
        count = 3
    else:
        count = 2

    shares = np.array([n["DemandShare"] for n in NODES], dtype=float)
    ids = [n["NodeID"] for n in NODES]
    if count >= len(NODES):
        chosen = list(range(len(NODES)))
    else:
        # The largest node stocks the widest assortment, so it is always in.
        biggest = int(np.argmax(shares))
        pool = [i for i in range(len(NODES)) if i != biggest]
        pool_p = shares[pool] / shares[pool].sum()
        picked = rng.choice(pool, size=count - 1, replace=False, p=pool_p)
        chosen = sorted([biggest, *[int(x) for x in picked]])

    weights = shares[chosen] * rng.uniform(0.82, 1.18, size=len(chosen))
    weights = weights / weights.sum()
    return [{"NodeID": ids[c], "Share": float(w)} for c, w in zip(chosen, weights, strict=True)]


def build_demand(
    rng: np.random.Generator,
    items: pd.DataFrame,
    weeks: int,
    end: date,
) -> tuple[pd.DataFrame, dict[str, list[dict]]]:
    """Weekly shipped units by SKU and node, plus the node map for later use."""
    week_ends = _week_endings(end, weeks)
    week_months = np.array([d.month for d in week_ends])
    week_labels = np.array([d.isoformat() for d in week_ends])
    seasonality = {d["Department"]: np.array(d["Seasonality"], dtype=float) for d in DEPARTMENTS}
    t = np.arange(weeks, dtype=float)
    n_items = len(items)

    node_map: dict[str, list[dict]] = {}
    frames: list[pd.DataFrame] = []

    for item in items.itertuples(index=False):
        season = seasonality[item.Department][week_months - 1]
        trend = (1.0 + item.gen_trend) ** t

        lifecycle_mult = np.ones(weeks)
        if item.gen_launch_week > 0:
            lifecycle_mult[: item.gen_launch_week] = 0.0
            ramp = min(6, weeks - item.gen_launch_week)
            if ramp > 0:
                lifecycle_mult[item.gen_launch_week : item.gen_launch_week + ramp] = np.linspace(
                    0.25, 1.0, ramp
                )
        if item.gen_eol_week < weeks:
            # Decay to a hard stop, not to a small number. An item that keeps
            # trickling out three units a week is never more than 7 days since
            # its last shipment, so nothing in the catalogue is ever dead - and
            # the ageing report, the write-down and the liquidate action all
            # have nothing to report on a business that plainly has some.
            tail = weeks - item.gen_eol_week
            stop = min(weeks, item.gen_eol_week + max(2, int(tail * 0.30)))
            span = stop - item.gen_eol_week
            if span > 0:
                lifecycle_mult[item.gen_eol_week : stop] *= np.linspace(0.8, 0.05, span)
            lifecycle_mult[stop:] = 0.0

        promo = np.ones(weeks)
        if item.gen_promo_count > 0:
            promo_weeks = rng.choice(weeks, size=item.gen_promo_count, replace=False)
            promo[promo_weeks] = rng.uniform(1.7, 3.9, size=item.gen_promo_count)

        # Two network-wide events every retailer has: a mid-July sale and the
        # weeks before Christmas. They land on the calendar, not on the SKU, so
        # a forecast that only knows about per-SKU seasonality misses them -
        # which is the honest reason a 4-week moving average sometimes wins.
        event = np.ones(weeks)
        for i, d in enumerate(week_ends):
            if d.month == 7 and 8 <= d.day <= 21:
                event[i] = 2.1
            elif d.month == 11 and d.day >= 22:
                event[i] = 1.55
            elif d.month == 12 and d.day <= 20:
                event[i] = 1.45

        mu_total = item.gen_base_weekly * season * trend * lifecycle_mult * promo * event
        nodes = _node_assignment(rng, item.gen_velocity_rank, n_items)
        node_map[item.SKU] = nodes

        for node in nodes:
            node_life = np.ones(weeks)
            if rng.random() < 0.09:
                # Assortment rationalisation: the item stays live nationally,
                # this node stops carrying it, and whatever was on its shelf
                # stays there. Stranded stock at one node on an otherwise
                # healthy SKU is invisible at SKU level and is most of what a
                # network-wide dead-stock report actually finds.
                node_life[int(rng.integers(int(weeks * 0.35), weeks - 4)) :] = 0.0
            mu = np.maximum(mu_total * node["Share"] * node_life, 1e-6)
            # Gamma-Poisson: the gamma draw is the week's true rate and the
            # Poisson is the counting noise. One draw with the right variance
            # beats a normal with a floor at zero, which produces a spike of
            # exact zeros that no real series has.
            lam = rng.gamma(item.gen_dispersion, mu / item.gen_dispersion)
            units = rng.poisson(lam)
            keep = units > 0
            if not keep.any():
                continue

            requested = units.copy()
            # An availability shortfall is what makes fill rate less than 100%.
            # Tie it loosely to the supplier so the supplier page and the fill
            # rate agree with each other.
            short = rng.random(weeks) < 0.035
            short &= keep
            if short.any():
                shipped = units.astype(float)
                shipped[short] = np.floor(shipped[short] * rng.uniform(0.45, 0.92, size=short.sum()))
                units = np.maximum(shipped.astype(np.int64), 0)
                keep = requested > 0

            frames.append(pd.DataFrame({
                "WeekEnding": week_labels[keep],
                "SKU": item.SKU,
                "NodeID": node["NodeID"],
                "UnitsRequested": requested[keep].astype(np.int64),
                "UnitsShipped": units[keep].astype(np.int64),
                "OrderLines": np.maximum(
                    1, np.round(units[keep] / rng.uniform(1.1, 3.4)).astype(np.int64)
                ),
                "NetSalesUSD": np.round(units[keep] * item.UnitPrice, 2),
            }))

    demand = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["WeekEnding", "SKU", "NodeID", "UnitsRequested", "UnitsShipped",
                 "OrderLines", "NetSalesUSD"]
    )
    demand = demand.sort_values(["WeekEnding", "SKU", "NodeID"], kind="stable").reset_index(drop=True)
    return demand, node_map


# --------------------------------------------------------------------------
# Stock, orders, counts, adjustments
# --------------------------------------------------------------------------
def _recent_rate(demand: pd.DataFrame, weeks_back: int, end: date) -> pd.DataFrame:
    """Mean weekly units per SKU and node over the trailing window.

    Divided by the window length rather than by the number of rows found: rows
    only exist for weeks that moved, so dividing by row count would report a
    SKU that sold once in thirteen weeks as selling every week.
    """
    cutoff = (end - timedelta(days=7 * weeks_back)).isoformat()
    recent = demand[demand["WeekEnding"] > cutoff]
    agg = recent.groupby(["SKU", "NodeID"], as_index=False)["UnitsShipped"].sum()
    agg["RecentWeekly"] = agg["UnitsShipped"] / float(weeks_back)
    return agg[["SKU", "NodeID", "RecentWeekly"]]


def build_inventory(
    rng: np.random.Generator,
    items: pd.DataFrame,
    suppliers: pd.DataFrame,
    demand: pd.DataFrame,
    node_map: dict[str, list[dict]],
    end: date,
) -> pd.DataFrame:
    rate = _recent_rate(demand, 13, end)
    rate_lookup = {(r.SKU, r.NodeID): r.RecentWeekly for r in rate.itertuples(index=False)}
    last_ship = (
        demand.groupby(["SKU", "NodeID"], as_index=False)["WeekEnding"].max()
        .rename(columns={"WeekEnding": "LastShipDate"})
    )
    last_lookup = {(r.SKU, r.NodeID): r.LastShipDate for r in last_ship.itertuples(index=False)}

    lead_weeks = {
        row.SupplierID: max(float(row.ActualLeadTimeMean) / 7.0, 0.7)
        for row in suppliers.itertuples(index=False)
    }

    rows = []
    for item in items.itertuples(index=False):
        # A buyer holds cover in proportion to how long a replacement takes.
        # Drawing cover from one distribution regardless of lead time puts the
        # same six weeks of stock behind a 6-day domestic supplier and a 50-day
        # container, and then half the network sits below its own reorder point
        # for a reason that is an artefact of the generator rather than a
        # finding about the business.
        base_cover = lead_weeks.get(item.SupplierID, 3.0) * 1.35 + 2.0
        for node in node_map.get(item.SKU, []):
            key = (item.SKU, node["NodeID"])
            weekly = rate_lookup.get(key, 0.0)

            # Most stock is roughly right; a tenth is buried and a tenth is
            # starving. That is the dial deciding what these pages find.
            roll = rng.random()
            if roll < 0.10:
                cover = float(base_cover * rng.uniform(2.6, 6.5))   # overstocked
            elif roll < 0.19:
                cover = float(base_cover * rng.uniform(0.05, 0.35))  # at risk
            else:
                cover = float(np.clip(rng.normal(base_cover, base_cover * 0.28),
                                      base_cover * 0.4, base_cover * 2.0))

            if weekly <= 0:
                # Nothing has moved lately. Whatever is on hand is a residue of
                # what was bought before it stopped moving - which is precisely
                # the dead stock the SKU-health page should surface.
                on_hand = int(rng.integers(0, 260)) if rng.random() < 0.72 else 0
                age_days = int(rng.integers(150, 760))
            else:
                on_hand = int(max(0, round(weekly * cover * rng.uniform(0.85, 1.15))))
                age_days = int(np.clip(rng.normal(cover * 7.0 * 1.35, 18.0), 2, 720))

            if on_hand == 0 and rng.random() < 0.55:
                continue

            reserved = int(min(on_hand, round(max(weekly, 0.0) * rng.uniform(0.0, 0.85))))
            last_count_days = int(rng.integers(3, 210))

            rows.append({
                "SnapshotDate": end.isoformat(),
                "SKU": item.SKU,
                "NodeID": node["NodeID"],
                "OnHandUnits": on_hand,
                "ReservedUnits": reserved,
                "OldestReceiptDate": (end - timedelta(days=age_days)).isoformat(),
                "LastReceiptDate": (end - timedelta(days=int(rng.integers(1, max(2, age_days))))).isoformat(),
                "LastCountDate": (end - timedelta(days=last_count_days)).isoformat(),
                "LastShipDate": last_lookup.get(key, ""),
                "UnitCost": item.UnitCost,
                "StorageType": str(rng.choice(("Pallet", "Case", "Each"), p=[0.34, 0.41, 0.25])),
                "BinCount": int(rng.integers(1, 9)),
            })

    inv = pd.DataFrame(rows)
    return inv.sort_values(["SKU", "NodeID"], kind="stable").reset_index(drop=True)


def build_purchase_orders(
    rng: np.random.Generator,
    items: pd.DataFrame,
    suppliers: pd.DataFrame,
    demand: pd.DataFrame,
    node_map: dict[str, list[dict]],
    weeks: int,
    end: date,
) -> pd.DataFrame:
    """Purchase orders, one per supplier per ordering cycle, with many lines.

    A purchase order is raised against a *supplier* and carries a line for every
    SKU being bought from them that week. Generating one PO per SKU instead
    would make the average order carry one line, and the whole economic-order-
    quantity argument - that the fixed cost of raising an order is shared across
    the lines on it - would have nothing to stand on.
    """
    sup = suppliers.set_index("SupplierID")
    total_units = demand.groupby("SKU")["UnitsShipped"].sum().to_dict()
    horizon_days = 7 * weeks
    by_supplier: dict[str, list] = {}
    for item in items.itertuples(index=False):
        by_supplier.setdefault(item.SupplierID, []).append(item)

    rows = []
    po_counter = 480_000
    for supplier_id, catalogue in sorted(by_supplier.items()):
        s_row = sup.loc[supplier_id]
        contracted = int(s_row.LeadTimeDays)
        actual_mean = float(s_row.ActualLeadTimeMean)
        # Spread scales with distance: a container from Shenzhen varies by
        # weeks, a truck from Langley by a day.
        actual_sd = max(1.0, actual_mean * 0.22)
        reliability = float(s_row.OnTimeReliability)
        defect = float(s_row.DefectRate)

        # A buyer orders roughly as often as the lead time lets them.
        orders_per_year = int(np.clip(round(364.0 / max(actual_mean * 0.8, 7.0)), 6, 26))
        po_count = max(2, int(round(orders_per_year * weeks / 52.0)))
        order_days = np.sort(
            rng.choice(np.arange(4, horizon_days), size=min(po_count, horizon_days - 5),
                       replace=False)
        )

        for offset in order_days:
            po_counter += 1
            order_date = end - timedelta(days=int(horizon_days - offset))
            promised = order_date + timedelta(days=contracted)
            lead = float(np.clip(rng.normal(actual_mean, actual_sd), 1.0, actual_mean * 2.6))
            if rng.random() > reliability:
                lead += float(rng.gamma(2.0, max(2.0, actual_mean * 0.16)))
            received_date = order_date + timedelta(days=int(round(lead)))
            in_transit = received_date > end

            # Each order covers a slice of the supplier's range, not all of it.
            line_count = int(np.clip(rng.integers(6, 19), 1, len(catalogue)))  # mean 12
            picked = rng.choice(len(catalogue), size=line_count, replace=False)
            for index in picked:
                item = catalogue[int(index)]
                nodes = [n["NodeID"] for n in node_map.get(item.SKU, [])] or ["YYZ4"]
                node = str(rng.choice(nodes))

                history_units = total_units.get(item.SKU, 0)
                # Divided by the number of orders this SKU is *on*, not by the
                # number the supplier raised. Each order covers a slice of the
                # range, so a SKU appears on roughly lines/range of them - and
                # dividing by the wrong one under-buys by exactly that ratio.
                # It cost 24% of receipts here, which turned a steady-state
                # network into one liquidating itself over eighteen months.
                appearances = max(po_count * EXPECTED_PO_LINES / max(len(catalogue), 1), 1.0)
                per_order = history_units / appearances
                # Buyers do not order the economic quantity. Some ride a
                # container minimum and over-buy; some chase demand weekly and
                # under-buy. The EOQ comparison exists to price that gap, so
                # the gap has to be in the data. The mean of the draw is one, so
                # the gap is in the spread rather than in the level.
                buyer_factor = float(np.clip(rng.lognormal(BUYER_FACTOR_MU, 0.75), 0.25, 4.0))
                raw = max(per_order * buyer_factor * PURCHASE_COVER, item.CasePack)
                qty_ordered = int(
                    max(item.MinOrderQty, round(raw / item.CasePack) * item.CasePack)
                )

                if in_transit:
                    status, qty_received, qty_rejected = "In Transit", 0, 0
                    received_str = ""
                else:
                    status = "Received"
                    short = 0.0 if rng.random() < 0.86 else rng.uniform(0.02, 0.22)
                    qty_received = int(round(qty_ordered * (1.0 - short)))
                    qty_rejected = int(round(qty_received * defect * rng.uniform(0.0, 2.4)))
                    received_str = received_date.isoformat()

                rows.append({
                    "PONumber": f"PO-{po_counter}",
                    "POLine": len(rows) % 99 + 1,
                    "SupplierID": supplier_id,
                    "SKU": item.SKU,
                    "NodeID": node,
                    "OrderDate": order_date.isoformat(),
                    "PromisedDate": promised.isoformat(),
                    "ReceivedDate": received_str,
                    "QtyOrdered": qty_ordered,
                    "QtyReceived": qty_received,
                    "QtyRejected": qty_rejected,
                    "UnitCost": item.UnitCost,
                    "Status": status,
                })

    po = pd.DataFrame(rows)
    return po.sort_values(["OrderDate", "PONumber", "SKU"], kind="stable").reset_index(drop=True)


def build_cycle_counts(
    rng: np.random.Generator,
    items: pd.DataFrame,
    inventory: pd.DataFrame,
    end: date,
    *,
    count_events: int = 2600,
) -> pd.DataFrame:
    if inventory.empty:
        return pd.DataFrame(columns=[
            "CountDate", "SKU", "NodeID", "SystemQty", "CountedQty", "CountType",
            "ReasonCode", "UnitCost", "CounterID",
        ])

    cost = dict(zip(items["SKU"], items["UnitCost"], strict=True))
    # Accuracy is not uniform across the network. Two nodes run a thinner count
    # programme, and that shows up as a lower match rate - which is the finding,
    # not noise to be smoothed away.
    node_error = {
        "YVR2": 0.030, "YYC1": 0.041, "YEG3": 0.052, "YWG2": 0.068,
        "YYZ4": 0.028, "YUL5": 0.037, "YHZ1": 0.075,
    }
    reasons = np.array([r[0] for r in COUNT_REASONS])
    reason_p = np.array([r[1] for r in COUNT_REASONS], dtype=float)
    reason_p = reason_p / reason_p.sum()

    pool = inventory[inventory["OnHandUnits"] > 0].reset_index(drop=True)
    if pool.empty:
        pool = inventory.reset_index(drop=True)
    picks = rng.integers(0, len(pool), size=count_events)

    rows = []
    for idx in picks:
        row = pool.iloc[int(idx)]
        count_date = end - timedelta(days=int(rng.integers(1, 183)))
        system_qty = int(row["OnHandUnits"])
        # Roll the clock back: the count happened before the snapshot, so the
        # quantity on the count sheet is not today's quantity.
        system_qty = max(0, int(round(system_qty * rng.uniform(0.6, 1.5))))

        p_error = node_error.get(str(row["NodeID"]), 0.04)
        if rng.random() < p_error:
            magnitude = max(1, int(round(abs(rng.normal(0, max(system_qty * 0.09, 1.6))))))
            sign = -1 if rng.random() < 0.71 else 1   # variance skews to loss
            counted = max(0, system_qty + sign * magnitude)
            reason = str(rng.choice(reasons, p=reason_p))
        else:
            counted = system_qty
            reason = ""

        rows.append({
            "CountDate": count_date.isoformat(),
            "SKU": str(row["SKU"]),
            "NodeID": str(row["NodeID"]),
            "SystemQty": system_qty,
            "CountedQty": counted,
            "CountType": str(rng.choice(("Cycle", "Cycle", "Cycle", "Exception", "Full"),
                                        p=[0.34, 0.30, 0.20, 0.11, 0.05])),
            "ReasonCode": reason,
            "UnitCost": float(cost.get(str(row["SKU"]), 0.0)),
            "CounterID": f"OPS-{int(rng.integers(100, 460))}",
        })

    counts = pd.DataFrame(rows)
    return counts.sort_values(["CountDate", "SKU", "NodeID"], kind="stable").reset_index(drop=True)


def build_adjustments(
    rng: np.random.Generator,
    items: pd.DataFrame,
    inventory: pd.DataFrame,
    end: date,
    *,
    events: int = 1400,
) -> pd.DataFrame:
    columns = ["AdjustmentDate", "SKU", "NodeID", "AdjustmentType", "Units",
               "UnitCost", "ReasonCode", "ApprovedBy"]
    if inventory.empty:
        return pd.DataFrame(columns=columns)

    cost = dict(zip(items["SKU"], items["UnitCost"], strict=True))
    types = np.array([a[0] for a in ADJUSTMENT_TYPES])
    type_p = np.array([a[1] for a in ADJUSTMENT_TYPES], dtype=float)
    type_p = type_p / type_p.sum()
    sign_by_type = {a[0]: a[2] for a in ADJUSTMENT_TYPES}
    reasons = np.array([r[0] for r in COUNT_REASONS])
    reason_p = np.array([r[1] for r in COUNT_REASONS], dtype=float)
    reason_p = reason_p / reason_p.sum()

    pool = inventory.reset_index(drop=True)
    picks = rng.integers(0, len(pool), size=events)

    rows = []
    for idx in picks:
        row = pool.iloc[int(idx)]
        adj_type = str(rng.choice(types, p=type_p))
        units = int(max(1, round(abs(rng.normal(0, max(float(row["OnHandUnits"]) * 0.05, 2.4))))))
        rows.append({
            "AdjustmentDate": (end - timedelta(days=int(rng.integers(1, 366)))).isoformat(),
            "SKU": str(row["SKU"]),
            "NodeID": str(row["NodeID"]),
            "AdjustmentType": adj_type,
            "Units": units * sign_by_type[adj_type],
            "UnitCost": float(cost.get(str(row["SKU"]), 0.0)),
            "ReasonCode": str(rng.choice(reasons, p=reason_p)),
            "ApprovedBy": f"SUP-OPS-{int(rng.integers(10, 60))}",
        })

    adj = pd.DataFrame(rows, columns=columns)
    return adj.sort_values(["AdjustmentDate", "SKU", "NodeID"], kind="stable").reset_index(drop=True)


def build_parameters() -> pd.DataFrame:
    return pd.DataFrame(
        [{"Parameter": p, "Value": v, "Description": d} for p, v, d in PLANNING_PARAMETERS]
    )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def generate(
    *,
    seed: int = DEFAULT_SEED,
    skus: int = DEFAULT_SKUS,
    weeks: int = DEFAULT_WEEKS,
    end: date | None = None,
) -> dict[str, pd.DataFrame]:
    """Build every sheet. Same seed in, same workbook out, on any machine."""
    rng = np.random.default_rng(seed)
    end = end or DEFAULT_END

    items = build_items(rng, skus, end, weeks)
    suppliers = build_suppliers()
    nodes = build_nodes()
    demand, node_map = build_demand(rng, items, weeks, end)
    inventory = build_inventory(rng, items, suppliers, demand, node_map, end)
    purchase_orders = build_purchase_orders(rng, items, suppliers, demand, node_map, weeks, end)
    counts = build_cycle_counts(rng, items, inventory, end)
    adjustments = build_adjustments(rng, items, inventory, end)

    return {
        "Item Master": items[ITEM_MASTER_COLUMNS].copy(),
        "Supplier Master": suppliers,
        "Network Nodes": nodes,
        "Demand History": demand,
        "Inventory Snapshot": inventory,
        "Purchase Orders": purchase_orders,
        "Cycle Counts": counts,
        "Inventory Adjustments": adjustments,
        "Planning Parameters": build_parameters(),
    }
