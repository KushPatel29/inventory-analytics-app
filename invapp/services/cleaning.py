from __future__ import annotations

import pandas as pd


def preprocess_data(
    sales_df: pd.DataFrame,
    inv_df: pd.DataFrame,
    prod_df: pd.DataFrame,
    cost_val_df: pd.DataFrame,
):
    # Normalize SKUs
    for df in (sales_df, inv_df, prod_df, cost_val_df):
        if "SKU" in df.columns:
            df["SKU"] = df["SKU"].astype(str).str.strip()

    # Split inventory SKU to code + name if composed
    if "SKU" in inv_df.columns:
        tmp = inv_df["SKU"].astype(str).str.split(" - ", n=1, expand=True)
        inv_df["SKU"] = tmp[0].fillna("")
        inv_df["ProductName"] = tmp[1].fillna(inv_df["SKU"]) if tmp.shape[1] > 1 else inv_df.get("ProductName", "")

    # Numerics
    inv_df["WeightLb"] = pd.to_numeric(inv_df.get("WeightLb", 0.0), errors="coerce").fillna(0.0)
    inv_df["CostValue"] = (
        inv_df.get("CostValue", 0)
        .astype(str)
        .replace(r"[\$,]", "", regex=True)
        .astype(float)
        .fillna(0.0)
    )

    for col in ("Cost", "Rev", "ShippedLb"):
        if col in sales_df.columns:
            sales_df[col] = (
                sales_df[col]
                .astype(str)
                .replace(r"[\$,]", "", regex=True)
                .astype(float)
                .fillna(0.0)
            )

    if "CostNow" in prod_df.columns:
        prod_df["CostNow"] = (
            prod_df["CostNow"]
            .astype(str)
            .replace(r"[\$,]", "", regex=True)
            .astype(float)
            .fillna(0.0)
        )

    prod_df = prod_df.drop_duplicates(subset=["SKU"], keep="first") if "SKU" in prod_df.columns else prod_df
    cost_val_df = cost_val_df.drop_duplicates(subset=["SKU"], keep="first") if "SKU" in cost_val_df.columns else cost_val_df

    return sales_df, inv_df, prod_df, cost_val_df


def process_inventory_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["SKU"] = df.get("SKU", "").astype(str).str.strip()
    if "ProductName" in df.columns:
        df["ProductName"] = df["ProductName"].astype(str)
    elif "Product" in df.columns:
        df["ProductName"] = df["Product"].astype(str)
    else:
        df["ProductName"] = ""

    df["ItemCount"] = pd.to_numeric(df.get("ItemCount", 1), errors="coerce").fillna(1).astype(int)
    df["WeightLb"] = pd.to_numeric(df.get("WeightLb", 0.0), errors="coerce").fillna(0.0)

    cost_col = "Cost_pr" if "Cost_pr" in df.columns else "CostValue"
    df["Cost_pr"] = pd.to_numeric(df.get(cost_col, 0.0), errors="coerce").fillna(0.0)

    if "OriginDate" in df.columns:
        df["OriginDate"] = pd.to_datetime(df["OriginDate"], errors="coerce")
    elif "CreatedAt" in df.columns:
        df["OriginDate"] = pd.to_datetime(df["CreatedAt"], errors="coerce")
    else:
        df["OriginDate"] = pd.to_datetime("today")

    df["OnHandWeightLb"] = df["WeightLb"] * df["ItemCount"]
    df["OnHandCost"] = df["Cost_pr"] * df["ItemCount"]
    df["SKU_Desc"] = df["SKU"] + " – " + df["ProductName"].fillna("")
    return df


def process_inventory_detail1(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["BinScannedAt"] = pd.to_datetime(df.get("BinScannedAt"), errors="coerce")
    df["CreatedAt"] = pd.to_datetime(df.get("CreatedAt"), errors="coerce")
    df["ProductLocation"] = df.get("ProductLocation", "Unknown").fillna("Unknown")
    df["LastKnownBin"] = df.get("LastKnownBin", "Unknown").fillna("Unknown")
    df["ItemCount"] = pd.to_numeric(df.get("ItemCount", 1), errors="coerce").fillna(1).astype(int)
    df["WeightLb"] = pd.to_numeric(df.get("WeightLb", 0.0), errors="coerce").fillna(0.0)
    return df

