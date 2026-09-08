"""A SKU's demand counts once, however many states it is stocked in.

`sku_stats` is grouped by SKU *and ProductState*, so a SKU held frozen and
external arrives as two rows — and sales are joined per SKU, so the same
AvgWeeklyUsage is copied onto both. The parent purchase plan summed that column,
which counted the demand once per state and inflated the target stock level and
every order quantity beneath it.

moves.py had already found this and takes the max across states, with a comment
saying summing "doubled the target stock level and inflated every move
recommendation". planning.py was still summing.

Inventory weight and cost are a different matter: there really is stock in both
states, so those stay additive. That asymmetry is the whole point of the fix and
is asserted here too.
"""
from __future__ import annotations

import pandas as pd

from invapp.services.planning import parent_purchase_plan


def _sku_stats(rows):
    return pd.DataFrame(rows)


def test_demand_is_not_multiplied_by_the_number_of_states():
    """One SKU, 5 lb/week, stocked in two states, nothing on hand.

    Four weeks of desired cover is 20 lb. Summing the copied usage asks for 40.
    """
    stats = _sku_stats([
        {"SKU": "A", "Supplier": "S", "Protein": "Beef", "ProductState": "FZ",
         "AvgWeeklyUsage": 5.0, "OnHandWeightTotal": 0.0, "OnHandCostTotal": 0.0,
         "NumPacksOnHand": 0.0, "SKU_Desc": "Item A"},
        {"SKU": "A", "Supplier": "S", "Protein": "Beef", "ProductState": "EXT",
         "AvgWeeklyUsage": 5.0, "OnHandWeightTotal": 0.0, "OnHandCostTotal": 0.0,
         "NumPacksOnHand": 0.0, "SKU_Desc": "Item A"},
    ])
    plan = parent_purchase_plan(stats, prod_detail=None, desired_woh=4.0)
    row = plan.loc[plan["ParentSKU"] == "A"].iloc[0]
    assert row["MeanUse"] == 5.0, (
        f"weekly demand read as {row['MeanUse']} for a SKU that uses 5 lb/week — "
        "it was counted once per state it is stocked in"
    )
    assert row["DesiredWt"] == 20.0
    assert row["ToBuyWt"] == 20.0


def test_three_states_do_not_triple_the_order():
    """The reviewer's case: 60 lb ordered where 20 is right."""
    stats = _sku_stats([
        {"SKU": "A", "Supplier": "S", "Protein": "Beef", "ProductState": state,
         "AvgWeeklyUsage": 5.0, "OnHandWeightTotal": 0.0, "OnHandCostTotal": 0.0,
         "NumPacksOnHand": 0.0, "SKU_Desc": "Item A"}
        for state in ("FZ", "EXT", "FRESH")
    ])
    plan = parent_purchase_plan(stats, prod_detail=None, desired_woh=4.0)
    row = plan.loc[plan["ParentSKU"] == "A"].iloc[0]
    assert row["ToBuyWt"] == 20.0, f"ordered {row['ToBuyWt']} lb where 20 is right"


def test_on_hand_stock_still_adds_across_states():
    """Demand collapses; inventory does not. Stock in two places is real stock."""
    stats = _sku_stats([
        {"SKU": "A", "Supplier": "S", "Protein": "Beef", "ProductState": "FZ",
         "AvgWeeklyUsage": 5.0, "OnHandWeightTotal": 6.0, "OnHandCostTotal": 12.0,
         "NumPacksOnHand": 1.0, "SKU_Desc": "Item A"},
        {"SKU": "A", "Supplier": "S", "Protein": "Beef", "ProductState": "EXT",
         "AvgWeeklyUsage": 5.0, "OnHandWeightTotal": 4.0, "OnHandCostTotal": 8.0,
         "NumPacksOnHand": 1.0, "SKU_Desc": "Item A"},
    ])
    plan = parent_purchase_plan(stats, prod_detail=None, desired_woh=4.0)
    row = plan.loc[plan["ParentSKU"] == "A"].iloc[0]
    assert row["InvWt"] == 10.0, "on-hand weight must still sum across states"
    assert row["InvCost"] == 20.0
    assert row["ToBuyWt"] == 10.0, "20 lb of cover less 10 lb on hand"


def test_distinct_children_of_a_parent_still_sum():
    """Collapsing per SKU must not collapse a real parent roll-up."""
    detail = pd.DataFrame([
        {"SKU": "A", "ParentSKU": "P", "Description": "d", "SKU_Desc": "Item A"},
        {"SKU": "B", "ParentSKU": "P", "Description": "d", "SKU_Desc": "Item B"},
    ])
    stats = _sku_stats([
        {"SKU": "A", "Supplier": "S", "Protein": "Beef", "ProductState": "FZ",
         "AvgWeeklyUsage": 5.0, "OnHandWeightTotal": 0.0, "OnHandCostTotal": 0.0,
         "NumPacksOnHand": 0.0, "SKU_Desc": "Item A"},
        {"SKU": "B", "Supplier": "S", "Protein": "Beef", "ProductState": "FZ",
         "AvgWeeklyUsage": 3.0, "OnHandWeightTotal": 0.0, "OnHandCostTotal": 0.0,
         "NumPacksOnHand": 0.0, "SKU_Desc": "Item B"},
    ])
    plan = parent_purchase_plan(stats, prod_detail=detail, desired_woh=4.0)
    row = plan.loc[plan["ParentSKU"] == "P"].iloc[0]
    assert row["MeanUse"] == 8.0, "two different SKUs under one parent must add"
    assert row["ToBuyWt"] == 32.0
