"""
Inventory optimisation: safety stock, reorder point, EOQ and what to order.

The formulas are the standard ones. Three choices in how they are applied are
worth stating, because they are where most implementations of this go wrong.

**Safety stock covers lead-time variability, not just demand variability.**
The one-term version, ``z * sigma_d * sqrt(LT)``, assumes the supplier is
never late. On this network the container suppliers vary by a week either side
of a forty-day lead time, and that variance is multiplied by average demand -
for a fast SKU it dwarfs the demand term. The two-term formula is used
throughout::

    SS = z * sqrt(LT * sigma_d^2 + d^2 * sigma_LT^2)

**Lead time comes from receipts, not from the contract.** The contracted lead
time is what the supplier agreed to; the observed one is what they do. Planning
on the contract is the single most common way a service level target is missed
by a system that is working exactly as designed.

**The reorder decision is made on inventory position, not on-hand.** Position
is on-hand plus what is already on a truck minus what is already promised to a
customer. A planner who reorders on on-hand alone orders the same shortage
twice - once when it appears and again next week before the first order lands.
"""

from __future__ import annotations

from statistics import NormalDist

import numpy as np
import pandas as pd

_NORMAL = NormalDist()

# Service level by ABC class. A items get the expensive target because a
# stockout on an A item costs more than the stock does; C items get the cheap
# one because the reverse is true.
DEFAULT_SERVICE_LEVELS = {"A": 0.98, "B": 0.95, "C": 0.90}


def z_for_service_level(service_level: float) -> float:
    """The safety factor for a cycle service level.

    ``statistics.NormalDist`` rather than scipy: this is the only place the
    project would need scipy, and a 40 MB dependency for one inverse CDF is a
    bad trade in a container that also has to hold pandas.
    """
    p = float(min(max(service_level, 0.500001), 0.999999))
    return float(_NORMAL.inv_cdf(p))


def normal_loss(z: float) -> float:
    """The standard normal loss function G(z) = phi(z) - z * (1 - Phi(z)).

    Expected units short per replenishment cycle, in units of sigma. It is what
    turns a cycle service level (how often you avoid a stockout) into a fill
    rate (what fraction of demand you actually ship) - two numbers that are
    routinely quoted as if they were the same and are not: 95% cycle service on
    a fast mover is a fill rate well above 99%.
    """
    return float(_NORMAL.pdf(z) - z * (1.0 - _NORMAL.cdf(z)))


def safety_stock(
    daily_demand: np.ndarray | pd.Series,
    daily_sigma: np.ndarray | pd.Series,
    lead_days: np.ndarray | pd.Series,
    lead_sigma: np.ndarray | pd.Series,
    z: np.ndarray | pd.Series,
) -> np.ndarray:
    """Buffer covering demand and lead-time variability over the lead time."""
    d = np.asarray(daily_demand, dtype=float)
    sd = np.asarray(daily_sigma, dtype=float)
    lt = np.maximum(np.asarray(lead_days, dtype=float), 0.0)
    slt = np.maximum(np.asarray(lead_sigma, dtype=float), 0.0)
    zz = np.asarray(z, dtype=float)
    variance = lt * np.square(sd) + np.square(d) * np.square(slt)
    return np.maximum(zz * np.sqrt(np.maximum(variance, 0.0)), 0.0)


def reorder_point(
    daily_demand: np.ndarray | pd.Series,
    lead_days: np.ndarray | pd.Series,
    safety: np.ndarray | pd.Series,
) -> np.ndarray:
    """Average demand over the lead time, plus the buffer."""
    d = np.asarray(daily_demand, dtype=float)
    lt = np.maximum(np.asarray(lead_days, dtype=float), 0.0)
    return np.maximum(d * lt + np.asarray(safety, dtype=float), 0.0)


def economic_order_quantity(
    annual_demand: np.ndarray | pd.Series,
    order_cost: np.ndarray | pd.Series,
    unit_cost: np.ndarray | pd.Series,
    carrying_rate: float,
) -> np.ndarray:
    """sqrt(2 D S / H), the quantity where ordering cost meets holding cost.

    Returns zero where the holding cost is zero rather than infinity: a free
    item has no economic order quantity, and letting that propagate as ``inf``
    puts an unorderable recommendation at the top of the plan.
    """
    d = np.maximum(np.asarray(annual_demand, dtype=float), 0.0)
    s = np.maximum(np.asarray(order_cost, dtype=float), 0.0)
    holding = np.maximum(np.asarray(unit_cost, dtype=float) * float(carrying_rate), 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        q = np.sqrt(2.0 * d * s / np.where(holding > 0, holding, np.nan))
    return np.nan_to_num(q, nan=0.0, posinf=0.0)


def total_cost_at_quantity(
    order_quantity: np.ndarray | pd.Series,
    annual_demand: np.ndarray | pd.Series,
    order_cost: np.ndarray | pd.Series,
    unit_cost: np.ndarray | pd.Series,
    carrying_rate: float,
) -> np.ndarray:
    """Annual ordering plus cycle-stock holding cost for a given order size.

    The comparison behind "EOQ says 480 and you are buying 1,200": the answer
    is only interesting alongside what the difference costs, and for a flat
    total-cost curve the honest answer is often "nothing much, leave it".
    """
    q = np.asarray(order_quantity, dtype=float)
    d = np.maximum(np.asarray(annual_demand, dtype=float), 0.0)
    s = np.maximum(np.asarray(order_cost, dtype=float), 0.0)
    holding = np.maximum(np.asarray(unit_cost, dtype=float) * float(carrying_rate), 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        ordering = np.where(q > 0, d / q * s, 0.0)
    return np.nan_to_num(ordering, nan=0.0, posinf=0.0) + q / 2.0 * holding


def round_to_pack(quantity: np.ndarray | pd.Series, case_pack: np.ndarray | pd.Series) -> np.ndarray:
    """Round up to a whole case. Warehouses do not receive two-thirds of a case."""
    q = np.maximum(np.asarray(quantity, dtype=float), 0.0)
    pack = np.maximum(np.asarray(case_pack, dtype=float), 1.0)
    return np.ceil(q / pack) * pack


def stockout_probability(
    inventory_position: np.ndarray | pd.Series,
    daily_demand: np.ndarray | pd.Series,
    daily_sigma: np.ndarray | pd.Series,
    lead_days: np.ndarray | pd.Series,
    lead_sigma: np.ndarray | pd.Series,
) -> np.ndarray:
    """P(demand over the lead time exceeds what is available to meet it).

    Normal approximation. It is wrong in the tail for a lumpy C item, where the
    true distribution is closer to a compound Poisson - so the number is
    reported as a band on the page rather than to two decimal places.
    """
    ip = np.asarray(inventory_position, dtype=float)
    d = np.asarray(daily_demand, dtype=float)
    sd = np.asarray(daily_sigma, dtype=float)
    lt = np.maximum(np.asarray(lead_days, dtype=float), 0.0)
    slt = np.maximum(np.asarray(lead_sigma, dtype=float), 0.0)

    mu = d * lt
    sigma = np.sqrt(np.maximum(lt * np.square(sd) + np.square(d) * np.square(slt), 0.0))
    out = np.empty_like(mu, dtype=float)
    for i in range(len(out)):
        if sigma[i] <= 0:
            out[i] = 1.0 if ip[i] < mu[i] else 0.0
        else:
            out[i] = 1.0 - _NORMAL.cdf(float((ip[i] - mu[i]) / sigma[i]))
    return np.clip(out, 0.0, 1.0)


def expected_units_short(
    inventory_position: np.ndarray | pd.Series,
    daily_demand: np.ndarray | pd.Series,
    daily_sigma: np.ndarray | pd.Series,
    lead_days: np.ndarray | pd.Series,
    lead_sigma: np.ndarray | pd.Series,
) -> np.ndarray:
    """Expected shortfall over the lead time, in units.

    Probability of a stockout says how often; this says how much, which is the
    one that converts to dollars and therefore the one that ranks the actions.
    """
    ip = np.asarray(inventory_position, dtype=float)
    d = np.asarray(daily_demand, dtype=float)
    sd = np.asarray(daily_sigma, dtype=float)
    lt = np.maximum(np.asarray(lead_days, dtype=float), 0.0)
    slt = np.maximum(np.asarray(lead_sigma, dtype=float), 0.0)

    mu = d * lt
    sigma = np.sqrt(np.maximum(lt * np.square(sd) + np.square(d) * np.square(slt), 0.0))
    out = np.zeros_like(mu, dtype=float)
    for i in range(len(out)):
        if sigma[i] <= 0:
            out[i] = max(mu[i] - ip[i], 0.0)
        else:
            out[i] = sigma[i] * normal_loss(float((ip[i] - mu[i]) / sigma[i]))
    return np.maximum(out, 0.0)


def build_plan(
    stats: pd.DataFrame,
    items: pd.DataFrame,
    inventory: pd.DataFrame,
    lead_times: pd.DataFrame,
    classes: pd.DataFrame,
    params: dict,
) -> pd.DataFrame:
    """One replenishment recommendation per SKU and node.

    `stats` is indexed by (SKU, NodeID) and carries DailyDemand / DailyStdDev.
    Everything else is joined on.
    """
    if stats.empty:
        return pd.DataFrame()

    df = stats.reset_index()
    df = df.merge(
        items[["SKU", "ItemDescription", "Department", "Category", "SupplierID",
               "UnitCost", "UnitPrice", "CasePack", "MinOrderQty", "UnitWeightLb"]],
        on="SKU", how="left",
    )
    df = df.merge(
        inventory[["SKU", "NodeID", "OnHandUnits", "ReservedUnits", "InTransitUnits",
                   "OverdueUnits", "OldestReceiptDate", "DaysOnHandAge"]],
        on=["SKU", "NodeID"], how="left",
    )
    df = df.merge(lead_times, on="SupplierID", how="left")
    df = df.merge(classes[["SKU", "ABCClass", "XYZClass"]], on="SKU", how="left")

    for col, default in (
        ("OnHandUnits", 0.0), ("ReservedUnits", 0.0), ("InTransitUnits", 0.0),
        ("OverdueUnits", 0.0),
        ("UnitCost", 0.0), ("UnitPrice", 0.0), ("CasePack", 1.0), ("MinOrderQty", 1.0),
        ("LeadTimeDaysActual", 14.0), ("LeadTimeDaysStdDev", 3.0),
        ("LeadTimeDaysContract", 14.0), ("OrderCostUSD", 250.0), ("UnitWeightLb", 1.0),
    ):
        if col not in df.columns:
            df[col] = default
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(default)

    df["ABCClass"] = df["ABCClass"].fillna("C")
    service_levels = {
        "A": float(params.get("ServiceLevelA", DEFAULT_SERVICE_LEVELS["A"])),
        "B": float(params.get("ServiceLevelB", DEFAULT_SERVICE_LEVELS["B"])),
        "C": float(params.get("ServiceLevelC", DEFAULT_SERVICE_LEVELS["C"])),
    }
    df["ServiceLevelTarget"] = df["ABCClass"].map(service_levels).fillna(service_levels["C"])
    df["SafetyFactorZ"] = df["ServiceLevelTarget"].map(z_for_service_level)

    carrying_rate = float(params.get("CarryingCostRate", 0.24))
    review_days = float(params.get("ReviewPeriodDays", 7))
    line_cost = float(params.get("OrderLineCostUSD", 45.0))
    lines_per_po = max(float(params.get("OrderLinesPerPO", 12.0)), 1.0)
    # The order cost that belongs to one SKU is the marginal cost of adding a
    # line - receiving, put-away, an invoice line to match - plus its share of
    # the fixed cost of raising the purchase order. Charging every SKU the full
    # per-PO cost, which is what a naive EOQ does, inflates the square root by
    # sqrt(lines per PO): here that turned a 20-day order quantity into a
    # 190-day one and asked for more stock than the business owns.
    df["EffectiveOrderCostUSD"] = line_cost + df["OrderCostUSD"] / lines_per_po

    df["SafetyStockUnits"] = safety_stock(
        df["DailyDemand"], df["DailyStdDev"],
        df["LeadTimeDaysActual"], df["LeadTimeDaysStdDev"], df["SafetyFactorZ"],
    )
    df["ReorderPointUnits"] = reorder_point(
        df["DailyDemand"], df["LeadTimeDaysActual"], df["SafetyStockUnits"]
    )
    # What the reorder point would have been on the contracted lead time. The
    # gap is the supplier page's finding expressed in units on a shelf.
    df["ReorderPointOnContract"] = reorder_point(
        df["DailyDemand"], df["LeadTimeDaysContract"],
        safety_stock(df["DailyDemand"], df["DailyStdDev"], df["LeadTimeDaysContract"],
                     df["LeadTimeDaysStdDev"], df["SafetyFactorZ"]),
    )

    # EOQ is decided at the SKU, because that is where purchasing happens: a
    # buyer places one order with a supplier and the receipt is split across
    # nodes afterwards. Computing it per node instead pays the same fixed
    # ordering cost once per node and produces five sub-economic quantities.
    sku_demand = df.groupby("SKU")["AnnualDemand"].transform("sum")
    network_eoq = economic_order_quantity(
        sku_demand, df["EffectiveOrderCostUSD"], df["UnitCost"], carrying_rate
    )

    # Bound the resulting order cycle to something a buyer can actually run.
    #
    # Textbook EOQ has no idea what a purchase order costs to live with. On a
    # fast-moving expensive SKU it happily returns a quantity that means
    # ordering fifty-eight times a year from a supplier with a forty-seven day
    # lead time - arithmetically correct, operationally impossible, and it
    # inflates the "saving" against what the buyer really does into six figures
    # of advice nobody can take. The floor is the review period, because you
    # cannot order more often than you look; the ceiling is a maximum cycle,
    # because past it the holding cost is real even where the curve is flat.

    sku_daily = df.groupby("SKU")["DailyDemand"].transform("sum").to_numpy()
    min_cycle = float(params.get("MinOrderCycleDays", review_days))
    max_cycle = float(params.get("MaxOrderCycleDays", 182.0))
    floor_units = sku_daily * min_cycle
    ceiling_units = np.where(sku_daily > 0, sku_daily * max_cycle, network_eoq)
    df["EOQUnconstrained"] = network_eoq
    network_eoq = np.clip(network_eoq, floor_units, np.maximum(ceiling_units, floor_units))
    with np.errstate(divide="ignore", invalid="ignore"):
        df["EOQCycleDays"] = np.where(
            sku_daily > 0, network_eoq / np.where(sku_daily > 0, sku_daily, np.nan), np.nan
        )

    node_share = np.where(sku_demand > 0, df["AnnualDemand"] / sku_demand, 0.0)
    df["EOQUnits"] = np.maximum(
        round_to_pack(network_eoq * node_share, df["CasePack"]), df["CasePack"]
    )
    df["EOQUnitsNetwork"] = np.maximum(
        round_to_pack(network_eoq, df["CasePack"]), df["MinOrderQty"]
    )

    df["InventoryPosition"] = (
        df["OnHandUnits"] + df["InTransitUnits"] - df["ReservedUnits"]
    ).clip(lower=0)
    df["AvailableUnits"] = (df["OnHandUnits"] - df["ReservedUnits"]).clip(lower=0)

    # Position without the receipts that are already late.
    #
    # An overdue purchase order is still in transit and still counts toward the
    # inventory position, which is right for the reorder decision - you do not
    # order it twice - and wrong for deciding whether to chase it. Counting it
    # keeps the position above the reorder point, the line reads Healthy, and
    # the expedite list is empty on a network with late orders sitting against
    # short nodes. Discounting it is what turns "this PO is late" into "this PO
    # is late and it is the only thing standing between this node and a
    # stockout".
    df["PositionExcludingOverdue"] = (
        df["InventoryPosition"] - df["OverdueUnits"]
    ).clip(lower=0)
    df["OverdueIsLoadBearing"] = (
        (df["OverdueUnits"] > 0)
        & (df["PositionExcludingOverdue"] <= df["ReorderPointUnits"])
    )

    # A node is replenished on periodic review, not on EOQ: the target covers
    # the lead time, the buffer and one review period, because the next chance
    # to order is a week away. Pushing a full EOQ into each node instead would
    # multiply the network cycle stock by the number of nodes.
    df["OrderUpToUnits"] = df["ReorderPointUnits"] + df["DailyDemand"] * review_days
    raw_order = np.where(
        df["InventoryPosition"] <= df["ReorderPointUnits"],
        df["OrderUpToUnits"] - df["InventoryPosition"],
        0.0,
    )
    df["RecommendedOrderUnits"] = np.where(
        raw_order > 0, round_to_pack(raw_order, df["CasePack"]), 0.0
    )
    df["RecommendedOrderValue"] = df["RecommendedOrderUnits"] * df["UnitCost"]

    df["DaysOfCover"] = np.where(
        df["DailyDemand"] > 0, df["InventoryPosition"] / df["DailyDemand"], np.inf
    )
    df["WeeksOfCover"] = df["DaysOfCover"] / 7.0
    df["StockoutRisk"] = stockout_probability(
        df["InventoryPosition"], df["DailyDemand"], df["DailyStdDev"],
        df["LeadTimeDaysActual"], df["LeadTimeDaysStdDev"],
    )
    df["ExpectedUnitsShort"] = expected_units_short(
        df["InventoryPosition"], df["DailyDemand"], df["DailyStdDev"],
        df["LeadTimeDaysActual"], df["LeadTimeDaysStdDev"],
    )
    penalty = float(params.get("StockoutPenaltyPerUnit", 6.5))
    # Lost contribution, floored at the assumed penalty: an item whose margin is
    # thin still costs a customer when it is not there.
    df["StockoutExposureUSD"] = df["ExpectedUnitsShort"] * np.maximum(
        df["UnitPrice"] - df["UnitCost"], penalty
    )

    df["Urgency"] = np.select(
        [
            (df["InventoryPosition"] <= 0) & (df["DailyDemand"] > 0),
            df["InventoryPosition"] < df["SafetyStockUnits"],
            df["InventoryPosition"] <= df["ReorderPointUnits"],
        ],
        ["Stocked out", "Below safety stock", "At reorder point"],
        default="Healthy",
    )
    return df


def eoq_comparison(plan: pd.DataFrame, purchase_orders: pd.DataFrame, params: dict) -> pd.DataFrame:
    """EOQ against the quantity actually being ordered, priced.

    Aggregated to the SKU because purchasing happens at the SKU: a buyer places
    one order with a supplier and splits the receipt across nodes afterwards.
    """
    if plan.empty:
        return pd.DataFrame()

    carrying_rate = float(params.get("CarryingCostRate", 0.24))
    by_sku = (
        plan.groupby("SKU", as_index=False)
        .agg(
            AnnualDemand=("AnnualDemand", "sum"),
            EOQUnits=("EOQUnitsNetwork", "max"),
            EOQUnconstrained=("EOQUnconstrained", "max"),
            EOQCycleDays=("EOQCycleDays", "max"),
            UnitCost=("UnitCost", "first"),
            OrderCostUSD=("EffectiveOrderCostUSD", "first"),
            ItemDescription=("ItemDescription", "first"),
            Department=("Department", "first"),
            ABCClass=("ABCClass", "first"),
            CasePack=("CasePack", "first"),
        )
    )

    if purchase_orders is not None and not purchase_orders.empty:
        # The quantity a buyer orders is the quantity on the purchase order, and
        # a purchase order can carry more than one line for the same SKU going
        # to different nodes. Averaging the *lines* would report a buy of 1,200
        # split three ways as an order quantity of 400 and then tell the buyer
        # they are under-ordering against EOQ by exactly the number of nodes.
        per_po = (
            purchase_orders.groupby(["SKU", "PONumber"], as_index=False)["QtyOrdered"].sum()
        )
        actual = (
            per_po.groupby("SKU", as_index=False)
            .agg(CurrentOrderUnits=("QtyOrdered", "mean"), OrdersPlaced=("QtyOrdered", "size"))
        )
        by_sku = by_sku.merge(actual, on="SKU", how="left")
    else:
        by_sku["CurrentOrderUnits"] = np.nan
        by_sku["OrdersPlaced"] = 0

    by_sku["CurrentOrderUnits"] = by_sku["CurrentOrderUnits"].fillna(by_sku["EOQUnits"])
    by_sku["CostAtEOQ"] = total_cost_at_quantity(
        by_sku["EOQUnits"], by_sku["AnnualDemand"], by_sku["OrderCostUSD"],
        by_sku["UnitCost"], carrying_rate,
    )
    by_sku["CostAtCurrent"] = total_cost_at_quantity(
        by_sku["CurrentOrderUnits"], by_sku["AnnualDemand"], by_sku["OrderCostUSD"],
        by_sku["UnitCost"], carrying_rate,
    )
    by_sku["AnnualSavingUSD"] = (by_sku["CostAtCurrent"] - by_sku["CostAtEOQ"]).clip(lower=0)
    by_sku["OrderSizeGapPct"] = np.where(
        by_sku["EOQUnits"] > 0,
        (by_sku["CurrentOrderUnits"] - by_sku["EOQUnits"]) / by_sku["EOQUnits"],
        np.nan,
    )
    # An item with no forward demand still being bought is not an order-size
    # problem and telling a buyer to "order 2 instead of 2,000" is not the
    # advice. Say the actual thing.
    by_sku["CurrentCycleDays"] = np.where(
        by_sku["AnnualDemand"] > 0,
        by_sku["CurrentOrderUnits"] / (by_sku["AnnualDemand"] / 365.0),
        np.nan,
    )
    # Whether the bound is doing the work matters to the reader: "order less at
    # a time" reads differently when the target is itself a floor rather than
    # the unconstrained optimum.
    by_sku["EOQIsBounded"] = ~np.isclose(
        by_sku["EOQUnits"], by_sku["EOQUnconstrained"], rtol=0.02
    )
    by_sku["Insight"] = np.select(
        [
            by_sku["AnnualDemand"] <= 0,
            by_sku["OrderSizeGapPct"] > 0.5,
            by_sku["OrderSizeGapPct"] < -0.5,
        ],
        [
            "No forward demand - stop ordering",
            "Over-ordering: order less at a time, more often",
            "Under-ordering: order more at a time, less often",
        ],
        default="Close to economic quantity",
    )
    return by_sku.sort_values(
        ["AnnualSavingUSD", "SKU"], ascending=[False, True], kind="stable"
    )


def service_level_curve(plan: pd.DataFrame, levels: tuple[float, ...] = (0.85, 0.90, 0.95, 0.98, 0.99)) -> pd.DataFrame:
    """What each service level target would cost in safety stock.

    The trade-off nobody sees until it is drawn: the last three points of
    service cost more than the first eighty-five, because z climbs steeply as
    the target approaches one.
    """
    if plan.empty:
        return pd.DataFrame(columns=["ServiceLevel", "SafetyFactorZ", "SafetyStockUnits",
                                     "SafetyStockValueUSD"])
    rows = []
    for level in levels:
        z = z_for_service_level(level)
        units = safety_stock(
            plan["DailyDemand"], plan["DailyStdDev"],
            plan["LeadTimeDaysActual"], plan["LeadTimeDaysStdDev"],
            np.full(len(plan), z),
        )
        rows.append({
            "ServiceLevel": level,
            "SafetyFactorZ": round(z, 4),
            "SafetyStockUnits": float(units.sum()),
            "SafetyStockValueUSD": float((units * plan["UnitCost"].to_numpy()).sum()),
        })
    return pd.DataFrame(rows)


def fill_rate_from_cycle_service(z: float, sigma_lt: float, order_quantity: float) -> float:
    """Convert a cycle service level into an expected fill rate.

    ``1 - sigma * G(z) / Q``. Included because the two get quoted
    interchangeably and they are not the same number - a 90% cycle service
    level on a SKU ordered in large batches is a fill rate of 99-point-
    something, and reporting the smaller number understates the operation.
    """
    if order_quantity <= 0:
        return float("nan")
    return float(1.0 - (sigma_lt * normal_loss(z)) / order_quantity)


__all__ = [
    "DEFAULT_SERVICE_LEVELS",
    "build_plan",
    "economic_order_quantity",
    "eoq_comparison",
    "expected_units_short",
    "fill_rate_from_cycle_service",
    "normal_loss",
    "reorder_point",
    "round_to_pack",
    "safety_stock",
    "service_level_curve",
    "stockout_probability",
    "total_cost_at_quantity",
    "z_for_service_level",
]