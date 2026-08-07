from __future__ import annotations

import numpy as np
import pandas as pd


def _combine_state(df: pd.DataFrame) -> pd.DataFrame:
    # Build per-SKU aggregates across states (usage, supplier, protein, desc)
    usage = (
        df.groupby("SKU", as_index=False)
        .agg(
            # Sales are joined per SKU, so the same AvgWeeklyUsage is copied
            # onto every ProductState row for that SKU. Summing it counted a
            # SKU's demand once per state it is stocked in, which doubled the
            # target stock level and inflated every move recommendation.
            AvgWeeklyUsage=("AvgWeeklyUsage", "max"),
            Supplier=("Supplier", lambda x: x.mode()[0] if not x.mode().empty else x.iloc[0]),
            Protein=("Protein", lambda x: x.mode()[0] if not x.mode().empty else ""),
        )
    )
    # Best description by longest string
    descs = (
        df.groupby("SKU")["SKU_Desc"].apply(lambda s: max(s.dropna(), key=len) if not s.dropna().empty else "")
    )
    usage["SKU_Desc"] = usage["SKU"].map(descs).fillna(usage["SKU"])
    return usage


def _state_onhand(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    if prefix.upper() == "FZ":
        mask = df["ProductState"].astype(str).str.upper().str.startswith("FZ")
        name = "FZ"
    else:
        mask = df["ProductState"].astype(str).str.upper().str.startswith("EXT")
        name = "EXT"
    grp = (
        df.loc[mask]
        .groupby("SKU", as_index=False)
        .agg(
            OnHandWeight=("OnHandWeightTotal", "sum"),
            Packs=("NumPacksOnHand", (lambda s: int(np.nansum(s)) if s.notna().any() else 0)),
        )
    )
    grp = grp.rename(columns={
        "OnHandWeight": f"{name}_OnHandWeight",
        "Packs": f"{name}_PacksOnHand",
    })
    return grp


def compute_fz_to_ext_moves(df: pd.DataFrame, desired_ext_woh: float = 1.0) -> pd.DataFrame:
    base = _combine_state(df)
    fz = _state_onhand(df, "FZ")
    ext = _state_onhand(df, "EXT")
    merged = base.merge(fz, on="SKU", how="left").merge(ext, on="SKU", how="left")
    merged[["FZ_OnHandWeight", "EXT_OnHandWeight", "FZ_PacksOnHand", "EXT_PacksOnHand"]] = (
        merged[["FZ_OnHandWeight", "EXT_OnHandWeight", "FZ_PacksOnHand", "EXT_PacksOnHand"]].fillna(0)
    )
    merged["DesiredEXT_Weight"] = merged["AvgWeeklyUsage"] * float(desired_ext_woh)
    merged["WeightToMove"] = (merged["DesiredEXT_Weight"] - merged["EXT_OnHandWeight"]).clip(lower=0)
    # Cannot move more than available FZ
    merged["WeightToMove"] = np.minimum(merged["WeightToMove"], merged["FZ_OnHandWeight"])
    out = merged[merged["WeightToMove"] > 0].copy()
    out["Total_OnHandWeight"] = out["FZ_OnHandWeight"] + out["EXT_OnHandWeight"]
    out["Total_PacksOnHand"] = out["FZ_PacksOnHand"] + out["EXT_PacksOnHand"]
    cols = [
        "SKU", "SKU_Desc", "Supplier", "Protein",
        "FZ_OnHandWeight", "FZ_PacksOnHand",
        "EXT_OnHandWeight", "EXT_PacksOnHand",
        "Total_OnHandWeight", "Total_PacksOnHand",
        "AvgWeeklyUsage", "DesiredEXT_Weight", "WeightToMove"
    ]
    return out[cols].sort_values("WeightToMove", ascending=False)


def compute_ext_to_fz_moves(df: pd.DataFrame, desired_fz_woh: float = 1.0) -> pd.DataFrame:
    base = _combine_state(df)
    fz = _state_onhand(df, "FZ")
    ext = _state_onhand(df, "EXT")
    merged = base.merge(fz, on="SKU", how="left").merge(ext, on="SKU", how="left")
    merged[["FZ_OnHandWeight", "EXT_OnHandWeight", "FZ_PacksOnHand", "EXT_PacksOnHand"]] = (
        merged[["FZ_OnHandWeight", "EXT_OnHandWeight", "FZ_PacksOnHand", "EXT_PacksOnHand"]].fillna(0)
    )
    merged["DesiredFZ_Weight"] = merged["AvgWeeklyUsage"] * float(desired_fz_woh)
    merged["WeightToReturn"] = (merged["DesiredFZ_Weight"] - merged["FZ_OnHandWeight"]).clip(lower=0)
    # Cannot return more than available EXT
    merged["WeightToReturn"] = np.minimum(merged["WeightToReturn"], merged["EXT_OnHandWeight"])
    out = merged[merged["WeightToReturn"] > 0].copy()
    out["Total_OnHandWeight"] = out["FZ_OnHandWeight"] + out["EXT_OnHandWeight"]
    out["Total_PacksOnHand"] = out["FZ_PacksOnHand"] + out["EXT_PacksOnHand"]
    cols = [
        "SKU", "SKU_Desc", "Supplier", "Protein",
        "EXT_OnHandWeight", "EXT_PacksOnHand",
        "FZ_OnHandWeight", "FZ_PacksOnHand",
        "Total_OnHandWeight", "Total_PacksOnHand",
        "AvgWeeklyUsage", "DesiredFZ_Weight", "WeightToReturn"
    ]
    return out[cols].sort_values("WeightToReturn", ascending=False)

