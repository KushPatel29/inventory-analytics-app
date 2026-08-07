from __future__ import annotations

import pandas as pd


def calculate_weeks_in_data(df: pd.DataFrame, date_col: str = "DateExpected") -> float:
    dates = pd.to_datetime(df.get(date_col), errors="coerce").dropna()
    if dates.empty:
        return 4.0
    span_days = (dates.max() - dates.min()).days + 1
    return max(span_days / 7.0, 1.0)


def weekly_inventory_trend(
    inv_snap: pd.DataFrame,
    date_col: str = "OriginDate",
    weight_col: str = "OnHandWeightLb",
    freq: str = "W",
) -> pd.DataFrame:
    df = inv_snap.copy()
    df[date_col] = pd.to_datetime(df.get(date_col), errors="coerce")
    ts = (
        df.groupby(pd.Grouper(key=date_col, freq=freq))[weight_col]
        .sum()
        .reset_index()
        .rename(columns={weight_col: "TotalOnHand"})
    )
    return ts

