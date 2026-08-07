from __future__ import annotations

import numpy as np
import pandas as pd


def parent_purchase_plan(
    sku_stats: pd.DataFrame,
    prod_detail: pd.DataFrame | None,
    desired_woh: float = 4.0,
) -> pd.DataFrame:
    df = sku_stats.copy()
    if prod_detail is None:
        prod_detail = pd.DataFrame(columns=["SKU", "ParentSKU", "Description", "SKU_Desc"])

    child_to_parent = dict(zip(prod_detail.get("SKU", []), prod_detail.get("ParentSKU", [])))
    df["ParentSKU"] = df["SKU"].map(child_to_parent).fillna(df["SKU"]).replace({"": np.nan}).fillna(df["SKU"])  # type: ignore

    parent_stats = (
        df.groupby("ParentSKU", as_index=False)
        .agg(
            MeanUse=("AvgWeeklyUsage", "sum"),
            InvWt=("OnHandWeightTotal", "sum"),
            InvCost=("OnHandCostTotal", "sum"),
            Supplier=("Supplier", lambda x: x.mode()[0] if not x.mode().empty else x.iloc[0]),
            Protein=("Protein", lambda x: x.mode()[0] if not x.mode().empty else ""),
        )
    )

    parent_stats["DesiredWt"] = parent_stats["MeanUse"] * desired_woh
    parent_stats["ToBuyWt"] = (parent_stats["DesiredWt"] - parent_stats["InvWt"]).clip(lower=0)

    # Approximate pack size per parent as median child (OnHandWeightTotal / NumPacksOnHand)
    if "NumPacksOnHand" in df.columns:
        per_child_packsize = df.assign(
            PackSize=df["OnHandWeightTotal"] / df["NumPacksOnHand"].replace({0: np.nan})
        )[["SKU", "ParentSKU", "PackSize"]]
        parent_packsize = (
            per_child_packsize.groupby("ParentSKU")["PackSize"].median().replace({np.nan: 1.0}).fillna(1.0)
        )
    else:
        parent_packsize = pd.Series(1.0, index=parent_stats["ParentSKU"]).astype(float)

    parent_stats["PackSize"] = parent_stats["ParentSKU"].map(parent_packsize).fillna(1.0)
    parent_stats["PacksToOrder"] = np.where(
        parent_stats["PackSize"] > 0,
        np.ceil(parent_stats["ToBuyWt"] / parent_stats["PackSize"]),
        0,
    ).astype(int)
    parent_stats["OrderWt"] = parent_stats["PacksToOrder"] * parent_stats["PackSize"]

    parent_stats["CostPerLb"] = np.where(
        parent_stats["InvWt"] > 0, parent_stats["InvCost"] / parent_stats["InvWt"], 0
    )
    parent_stats["EstCost"] = parent_stats["OrderWt"] * parent_stats["CostPerLb"]

    # Best description per parent
    if "SKU_Desc" in df.columns:
        best_desc = (
            df.groupby("ParentSKU")["SKU_Desc"].apply(lambda s: max(s.dropna(), key=len) if not s.dropna().empty else "")
        )
        parent_stats["SKU_Desc"] = parent_stats["ParentSKU"].map(best_desc).fillna("")
    else:
        parent_stats["SKU_Desc"] = parent_stats["ParentSKU"]

    # Only items to order
    return parent_stats.sort_values("EstCost", ascending=False)

