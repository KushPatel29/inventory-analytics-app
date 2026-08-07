from __future__ import annotations

import pandas as pd


def _prepare_df(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    if {"SKU1", "SKU"}.issubset(df.columns):
        df["ProductDesc"] = df["SKU1"].astype(str).str.strip() + " – " + df["SKU"].astype(str).str.strip()
    elif "SKU" in df.columns:
        df["ProductDesc"] = df["SKU"].astype(str).str.strip()
    elif "SKU1" in df.columns:
        df["ProductDesc"] = df["SKU1"].astype(str).str.strip()
    else:
        df["ProductDesc"] = "<unknown>"

    df["ItemCount"] = pd.to_numeric(df.get("ItemCount", 1), errors="coerce").fillna(1).astype(int)
    df["WeightLb"] = pd.to_numeric(df.get("WeightLb", 0), errors="coerce").fillna(0.0)
    df["TotalWeight"] = df["WeightLb"]
    df["CreatedAt"] = pd.to_datetime(df.get("CreatedAt"), errors="coerce")

    if "LastKnownBin" not in df.columns:
        df["LastKnownBin"] = df.get("PalletId1").astype(str)

    df = df[df.get("PackId1").notna() & (df["TotalWeight"] > 0) & df["CreatedAt"].notna()]
    df = df.sort_values("CreatedAt", ascending=False).drop_duplicates(subset="PackId1", keep="first").reset_index(drop=True)
    return df


def prepare_bins_data(sheets: dict[str, pd.DataFrame]) -> pd.DataFrame:
    inv1 = sheets.get("Inventory Detail1", pd.DataFrame())
    # Stock held at a named key account's own facility, tracked separately from
    # the main warehouse because it is consigned rather than owned.
    key_account = sheets.get("Key Account", pd.DataFrame())
    df1 = _prepare_df(inv1)
    if "ProductLocation" not in df1.columns:
        df1["ProductLocation"] = "Main"
    df1 = df1[df1["ProductLocation"].str.lower() != "key account"]

    if not key_account.empty:
        df2 = _prepare_df(key_account)
        df2["ProductLocation"] = "Key Account"
    else:
        df2 = pd.DataFrame(columns=df1.columns)

    df = pd.concat([df1, df2], ignore_index=True)
    df["Source"] = df["ProductLocation"].str.lower().eq("key account").map({True: "Key Account", False: "Main"})
    return df

