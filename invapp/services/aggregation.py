from __future__ import annotations

import numpy as np
import pandas as pd

from .metrics import calculate_weeks_in_data
from .logger import logger


def aggregate_sales_history(sales_df: pd.DataFrame) -> pd.DataFrame:
    return (
        sales_df.groupby(["SKU", "Supplier", "Protein", "Description"], as_index=False)
        .agg(
            ShippedLb=("ShippedLb", "sum"),
            QuantityOrdered=("QuantityOrdered", "sum"),
            Cost=("Cost", "sum"),
            Rev=("Rev", "sum"),
        )
    )


def merge_data(
    agg_sales: pd.DataFrame,
    inv_df: pd.DataFrame,
    prod_df: pd.DataFrame,
) -> pd.DataFrame:
    # WeightLb is the weight of a single item, so on-hand weight has to be
    # multiplied by ItemCount - the same definition cleaning.py uses for
    # OnHandWeightLb. Summing WeightLb alone counted one item per row while
    # CostValue already reflected the full quantity, so weight and cost
    # disagreed by a factor of the case count and every turnover and
    # weeks-on-hand figure derived from them was wrong.
    inv_df = inv_df.copy()
    if "ItemCount" in inv_df.columns:
        item_count = pd.to_numeric(inv_df["ItemCount"], errors="coerce").fillna(1)
    else:
        item_count = 1
    inv_df["_OnHandWeightLb"] = pd.to_numeric(inv_df["WeightLb"], errors="coerce").fillna(0.0) * item_count

    inv_agg = (
        inv_df.groupby(["SKU", "ProductState", "ProductName"], as_index=False)
        .agg(OnHandWeightLb=("_OnHandWeightLb", "sum"), OnHandCost=("CostValue", "sum"))
    )

    # Prefer explicit Supplier from production data; fallback to SSN from inventory when available
    supplier_map = (
        prod_df.set_index("SKU")["Supplier"].to_dict() if "Supplier" in prod_df.columns else {}
    )
    if not supplier_map and "Ssn" in inv_df.columns:
        try:
            supplier_map = inv_df.dropna(subset=["Ssn"]).set_index("SKU")["Ssn"].to_dict()
        except Exception:
            supplier_map = {}

    df = inv_agg.merge(agg_sales, on="SKU", how="left")
    df = df.fillna(
        {
            "Supplier": "",
            "Protein": "",
            "Description": "",
            "ShippedLb": 0.0,
            "QuantityOrdered": 0,
            "Cost": 0.0,
            "Rev": 0.0,
        }
    )
    mask_blank = df["Supplier"].astype(str).str.strip() == ""
    df.loc[mask_blank, "Supplier"] = df.loc[mask_blank, "SKU"].map(supplier_map).fillna("")

    if "ProductionShippedLb" not in prod_df.columns:
        prod_df["ProductionShippedLb"] = pd.to_numeric(prod_df.get("WeightLb", 0), errors="coerce").fillna(0.0)

    df = df.merge(prod_df[["SKU", "ProductionShippedLb"]], on="SKU", how="left")
    df["ProductionShippedLb"] = df["ProductionShippedLb"].fillna(0.0)
    return df


def aggregate_final_data(df: pd.DataFrame, sales_df: pd.DataFrame) -> pd.DataFrame:
    try:
        weeks_span = calculate_weeks_in_data(sales_df)
        if weeks_span <= 0:
            raise ValueError
    except Exception:
        logger.warning("calculate_weeks_in_data failed; defaulting to 4 weeks")
        weeks_span = 4

    sku_stats = (
        df.groupby(
            ["SKU", "Supplier", "Protein", "Description", "ProductState", "ProductName"],
            as_index=False,
        )
        .agg(
            OnHandWeightTotal=("OnHandWeightLb", "sum"),
            OnHandCostTotal=("OnHandCost", "sum"),
            TotalShippedLb=("ShippedLb", "sum"),
            TotalProductionLb=("ProductionShippedLb", "sum"),
            TotalRevenue=("Rev", "sum"),
            TotalCost=("Cost", "sum"),
        )
    )

    sku_stats["TotalUsage"] = sku_stats["TotalShippedLb"] + sku_stats["TotalProductionLb"]
    sku_stats["AvgWeeklyUsage"] = sku_stats["TotalUsage"] / weeks_span
    sku_stats["WOH_Flag"] = np.where(sku_stats["TotalUsage"] == 0, "No Usage", "")
    sku_stats["WeeksOnHand"] = (
        sku_stats["OnHandWeightTotal"] / sku_stats["AvgWeeklyUsage"].replace({0: np.nan})
    )
    sku_stats["WeeksOnHand"] = sku_stats["WeeksOnHand"].replace([np.inf, -np.inf], np.nan)
    sku_stats["AnnualTurns"] = (
        sku_stats["AvgWeeklyUsage"] * 52.0
        / sku_stats["OnHandWeightTotal"].replace({0: np.nan})
    )
    sku_stats["AnnualTurns"] = sku_stats["AnnualTurns"].replace([np.inf, -np.inf], 0).fillna(0)

    sku_stats["SKU_Desc"] = (
        sku_stats["SKU"]
        + " – "
        + sku_stats["ProductName"].where(sku_stats["ProductName"].ne(""), sku_stats["Description"])
    )
    logger.info(f"Aggregated final data: {len(sku_stats)} SKUs")
    return sku_stats
