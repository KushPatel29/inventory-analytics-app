"""
Demand history and forecasting.

Two things here are decisions rather than implementation details.

**Zero weeks are real.** A shipment export has no row for a week a SKU did not
move, so anything averaging over history has to reindex onto the full calendar
first. Dividing summed units by the number of *rows found* reports an item that
sold once in thirteen weeks as selling every week - which then flows into
average demand, into safety stock, into the reorder point, and orders more of
something nobody buys. Every series here is built on the full week grid.

**Forecasting happens at SKU level and is allocated down to nodes.** The
alternative - forecasting each SKU-node series independently - sounds more
precise and is worse: a node series is the SKU series divided by three or four,
so it carries the same signal with several times the noise, and the aggregate
of the node forecasts is no longer the number anybody buys against. Demand
planners forecast at the aggregate and disaggregate on share; so does this.

Seven methods compete per SKU on a rolling-origin backtest and the winner is
chosen on MASE. Croston/SBA is in the set because a third of this catalogue is
intermittent, and a moving average is in it because on real data a moving
average wins often enough that leaving it out would be dishonest.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Weeks of history the demand statistics are computed over. Thirteen weeks is
# the usual planning window: long enough for the variance estimate to mean
# something, short enough to follow a trend.
STAT_WINDOW_WEEKS = 13
FORECAST_HORIZON_WEEKS = 13

# Backtest shape. Four folds of four weeks holds out the last sixteen weeks,
# which leaves enough history for a seasonal method to have something to say.
BACKTEST_FOLDS = 4
BACKTEST_HORIZON = 4

METHODS = ("naive", "ma4", "ma13", "ses", "holt", "seasonal_naive", "croston")


# --------------------------------------------------------------------------
# Series construction
# --------------------------------------------------------------------------
def week_grid(demand: pd.DataFrame, date_col: str = "WeekEnding") -> pd.DatetimeIndex:
    """Every week between the first and last in the data, including empty ones."""
    dates = pd.to_datetime(demand[date_col], errors="coerce").dropna()
    if dates.empty:
        return pd.DatetimeIndex([])
    return pd.date_range(dates.min(), dates.max(), freq="7D")


def weekly_matrix(
    demand: pd.DataFrame,
    grid: pd.DatetimeIndex,
    *,
    keys: list[str] | None = None,
    value: str = "UnitsShipped",
) -> pd.DataFrame:
    """Units by key and week, zero-filled across the whole grid.

    Returns a frame indexed by ``keys`` with one column per week.
    """
    keys = keys or ["SKU"]
    if demand.empty or len(grid) == 0:
        return pd.DataFrame(index=pd.Index([], name=keys[0]), columns=grid, dtype=float)

    d = demand.copy()
    d["WeekEnding"] = pd.to_datetime(d["WeekEnding"], errors="coerce")
    pivot = d.pivot_table(index=keys, columns="WeekEnding", values=value, aggfunc="sum")
    return pivot.reindex(columns=grid).fillna(0.0)


@dataclass(frozen=True)
class DemandStats:
    """Per-key demand statistics, in the units the planning maths wants."""

    frame: pd.DataFrame

    def __getitem__(self, item: str) -> pd.Series:
        return self.frame[item]


def demand_statistics(
    matrix: pd.DataFrame,
    *,
    window: int = STAT_WINDOW_WEEKS,
) -> pd.DataFrame:
    """Mean, variability and recency per key over the trailing window.

    Daily figures come from the weekly ones by dividing the mean by seven and
    the standard deviation by sqrt(7). That is the standard planning conversion
    and it assumes days within a week are independent, which they are not -
    Saturdays outsell Tuesdays. It understates daily variance slightly and is
    stated here rather than hidden, because the alternative is holding daily
    shipment history for four hundred SKUs to gain a second decimal place on a
    safety stock that is already rounded to a case.
    """
    if matrix.empty:
        return pd.DataFrame(
            columns=["WeeklyDemand", "WeeklyStdDev", "CoefficientOfVariation",
                     "DailyDemand", "DailyStdDev", "AnnualDemand", "WeeksSinceLastShip",
                     "ActiveWeeks", "TotalUnits"]
        )

    tail = matrix.iloc[:, -window:] if matrix.shape[1] > window else matrix
    mean_w = tail.mean(axis=1)
    # Sample standard deviation: ddof=1, because thirteen weeks is a sample of
    # the process, not the population. With ddof=0 the safety stock for every
    # SKU is quietly ~4% light.
    std_w = tail.std(axis=1, ddof=1).fillna(0.0)

    values = matrix.to_numpy()
    nonzero = values > 0
    # Weeks since the last shipment, counting back from the end of the grid.
    last_idx = np.where(nonzero.any(axis=1), matrix.shape[1] - 1 - nonzero[:, ::-1].argmax(axis=1), -1)
    weeks_since = np.where(last_idx >= 0, matrix.shape[1] - 1 - last_idx, np.nan)

    out = pd.DataFrame(index=matrix.index)
    out["WeeklyDemand"] = mean_w
    out["WeeklyStdDev"] = std_w
    out["CoefficientOfVariation"] = np.where(mean_w > 0, std_w / mean_w.replace(0, np.nan), np.nan)
    out["DailyDemand"] = mean_w / 7.0
    out["DailyStdDev"] = std_w / np.sqrt(7.0)
    out["AnnualDemand"] = mean_w * 52.0
    out["WeeksSinceLastShip"] = weeks_since
    out["ActiveWeeks"] = nonzero.sum(axis=1)
    out["TotalUnits"] = values.sum(axis=1)
    return out


def node_share(
    node_matrix: pd.DataFrame,
    *,
    window: int = STAT_WINDOW_WEEKS,
) -> pd.Series:
    """Each node's share of its SKU's recent demand.

    The share a SKU-level forecast is split on. A node that has shipped nothing
    lately gets a zero share and therefore no forecast, which is right: an
    allocation that keeps feeding a node no longer selling the item is how
    stock ends up stranded three provinces from the demand.
    """
    if node_matrix.empty:
        return pd.Series(dtype=float)
    tail = node_matrix.iloc[:, -window:] if node_matrix.shape[1] > window else node_matrix
    totals = tail.sum(axis=1)
    by_sku = totals.groupby(level="SKU").transform("sum")
    share = np.where(by_sku > 0, totals / by_sku.replace(0, np.nan), 0.0)
    return pd.Series(share, index=node_matrix.index, name="NodeShare").fillna(0.0)


# --------------------------------------------------------------------------
# Forecast methods
# --------------------------------------------------------------------------
# Each takes a history array and a horizon and returns `horizon` values. They
# are deliberately plain functions over numpy arrays rather than model objects:
# the backtest calls them tens of thousands of times.
def f_naive(y: np.ndarray, h: int) -> np.ndarray:
    return np.repeat(y[-1] if len(y) else 0.0, h)


def _moving_average(y: np.ndarray, h: int, k: int) -> np.ndarray:
    if len(y) == 0:
        return np.zeros(h)
    return np.repeat(float(np.mean(y[-k:])), h)


def f_ma4(y: np.ndarray, h: int) -> np.ndarray:
    return _moving_average(y, h, 4)


def f_ma13(y: np.ndarray, h: int) -> np.ndarray:
    return _moving_average(y, h, 13)


def _ses_level(y: np.ndarray, alpha: float) -> float:
    level = float(y[0])
    for value in y[1:]:
        level = alpha * float(value) + (1 - alpha) * level
    return level


def _ses_sse(y: np.ndarray, alpha: float) -> float:
    level = float(y[0])
    sse = 0.0
    for value in y[1:]:
        sse += (float(value) - level) ** 2
        level = alpha * float(value) + (1 - alpha) * level
    return sse


SES_GRID = (0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5)


def f_ses(y: np.ndarray, h: int) -> np.ndarray:
    """Simple exponential smoothing, alpha fitted on in-sample squared error."""
    if len(y) < 2:
        return f_naive(y, h)
    alpha = min(SES_GRID, key=lambda a: _ses_sse(y, a))
    return np.repeat(max(_ses_level(y, alpha), 0.0), h)


HOLT_GRID = ((0.2, 0.05), (0.3, 0.1), (0.4, 0.05), (0.5, 0.15))
HOLT_DAMPING = 0.90


def _holt_fit(y: np.ndarray, alpha: float, beta: float) -> tuple[float, float, float]:
    level, trend, sse = float(y[0]), float(y[1] - y[0]), 0.0
    for value in y[1:]:
        forecast = level + HOLT_DAMPING * trend
        sse += (float(value) - forecast) ** 2
        prev_level = level
        level = alpha * float(value) + (1 - alpha) * forecast
        trend = beta * (level - prev_level) + (1 - beta) * HOLT_DAMPING * trend
    return level, trend, sse


def f_holt(y: np.ndarray, h: int) -> np.ndarray:
    """Holt's linear trend, damped.

    Damped rather than plain: an undamped trend fitted to eighteen months of
    retail demand extrapolates the Christmas ramp into March.
    """
    if len(y) < 4:
        return f_ses(y, h)
    alpha, beta = min(HOLT_GRID, key=lambda ab: _holt_fit(y, ab[0], ab[1])[2])
    level, trend, _ = _holt_fit(y, alpha, beta)
    steps = np.arange(1, h + 1)
    damping = np.cumsum(HOLT_DAMPING ** steps)
    return np.maximum(level + damping * trend, 0.0)


SEASON_LENGTH = 52


def f_seasonal_naive(y: np.ndarray, h: int) -> np.ndarray:
    """Same week last year, levelled to the recent mean.

    Pure seasonal naive repeats last year's noise as well as last year's shape.
    Scaling last year's window by the ratio of recent means keeps the shape and
    drops the level error, which is what a planner does by hand anyway.
    """
    if len(y) < SEASON_LENGTH + 4:
        return f_ma4(y, h)
    season = y[-SEASON_LENGTH : -SEASON_LENGTH + h] if h <= SEASON_LENGTH else y[-SEASON_LENGTH:]
    if len(season) < h:
        season = np.resize(season, h)
    recent = float(np.mean(y[-13:]))
    year_ago = float(np.mean(y[-SEASON_LENGTH - 6 : -SEASON_LENGTH + 7]))
    scale = recent / year_ago if year_ago > 0 else 1.0
    return np.maximum(season * scale, 0.0)


CROSTON_ALPHA = 0.15


def f_croston(y: np.ndarray, h: int) -> np.ndarray:
    """Croston's method with the Syntetos-Boylan bias correction.

    For intermittent demand - long runs of zeros broken by occasional orders -
    smoothing the raw series pulls the forecast toward zero and then no safety
    stock is ever held. Croston smooths the non-zero *sizes* and the *intervals
    between them* separately and divides; the (1 - alpha/2) factor removes the
    known upward bias in that ratio.
    """
    nz = np.flatnonzero(y)
    if len(nz) < 2:
        return f_ma13(y, h)
    size = float(y[nz[0]])
    interval = float(nz[0] + 1)
    for prev, idx in zip(nz[:-1], nz[1:], strict=True):
        size = CROSTON_ALPHA * float(y[idx]) + (1 - CROSTON_ALPHA) * size
        interval = CROSTON_ALPHA * float(idx - prev) + (1 - CROSTON_ALPHA) * interval
    rate = (size / interval) * (1 - CROSTON_ALPHA / 2.0) if interval > 0 else 0.0
    return np.repeat(max(rate, 0.0), h)


FORECASTERS = {
    "naive": f_naive,
    "ma4": f_ma4,
    "ma13": f_ma13,
    "ses": f_ses,
    "holt": f_holt,
    "seasonal_naive": f_seasonal_naive,
    "croston": f_croston,
}

METHOD_LABELS = {
    "naive": "Naive (last week)",
    "ma4": "4-week moving average",
    "ma13": "13-week moving average",
    "ses": "Exponential smoothing",
    "holt": "Damped trend (Holt)",
    "seasonal_naive": "Seasonal naive (52w)",
    "croston": "Croston / SBA (intermittent)",
}


# --------------------------------------------------------------------------
# Accuracy
# --------------------------------------------------------------------------
def wape(actual: np.ndarray, forecast: np.ndarray) -> float:
    """Weighted absolute percentage error: sum of errors over sum of actuals.

    Used instead of MAPE for reporting because MAPE divides by each actual and
    a week with two units shipped produces a 400% error that swamps the mean.
    """
    denom = np.abs(actual).sum()
    if denom <= 0:
        return float("nan")
    return float(np.abs(actual - forecast).sum() / denom)


def bias(actual: np.ndarray, forecast: np.ndarray) -> float:
    """Signed error as a fraction of actual: positive means over-forecast."""
    denom = np.abs(actual).sum()
    if denom <= 0:
        return float("nan")
    return float((forecast - actual).sum() / denom)


def mase(actual: np.ndarray, forecast: np.ndarray, history: np.ndarray) -> float:
    """Mean absolute scaled error, scaled by the in-sample naive forecast.

    The selection metric. WAPE cannot rank methods on a series that is mostly
    zeros - every method scores near 1.0 - while MASE compares each method to
    "repeat last week" on the same series and stays finite when actuals do not.
    """
    if len(history) < 2:
        return float("nan")
    scale = float(np.mean(np.abs(np.diff(history))))
    if scale <= 0:
        return float("nan")
    return float(np.mean(np.abs(actual - forecast)) / scale)


def tracking_signal(actual: np.ndarray, forecast: np.ndarray) -> float:
    """Cumulative error over mean absolute deviation.

    Outside +/-4 the forecast is not noisy, it is wrong in one direction, and
    the model needs replacing rather than retuning.
    """
    errors = forecast - actual
    mad = float(np.mean(np.abs(errors)))
    if mad <= 0:
        return 0.0
    return float(errors.sum() / mad)


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------
def backtest_series(
    y: np.ndarray,
    *,
    folds: int = BACKTEST_FOLDS,
    horizon: int = BACKTEST_HORIZON,
    methods: tuple[str, ...] = METHODS,
) -> dict[str, dict[str, float]]:
    """Rolling-origin backtest: fit on the past, score the next `horizon` weeks.

    Rolling origin rather than one hold-out because a single split scores one
    accident of the calendar - hold out December and every method looks broken.
    """
    scores: dict[str, dict[str, float]] = {}
    usable = min(folds, max(0, (len(y) - 20) // horizon))
    if usable <= 0:
        return scores

    for name in methods:
        fn = FORECASTERS[name]
        actuals: list[np.ndarray] = []
        preds: list[np.ndarray] = []
        scaled: list[float] = []
        for fold in range(usable, 0, -1):
            cut = len(y) - fold * horizon
            history = y[:cut]
            actual = y[cut : cut + horizon]
            if len(actual) == 0 or len(history) < 8:
                continue
            forecast = np.asarray(fn(history, len(actual)), dtype=float)
            actuals.append(actual)
            preds.append(forecast)
            value = mase(actual, forecast, history)
            if np.isfinite(value):
                scaled.append(value)
        if not actuals:
            continue
        a = np.concatenate(actuals)
        f = np.concatenate(preds)
        scores[name] = {
            "wape": wape(a, f),
            "bias": bias(a, f),
            "mase": float(np.mean(scaled)) if scaled else float("nan"),
            "tracking_signal": tracking_signal(a, f),
        }
    return scores


def _pick(scores: dict[str, dict[str, float]]) -> str:
    """Lowest MASE, falling back to WAPE, with a stable tie-break.

    The tie-break is the order in `METHODS`, not whatever `min` happens to see
    first: two methods scoring identically on a flat series would otherwise
    swap between runs and the committed marts would never be reproducible.
    """
    if not scores:
        return "ma4"
    ranked = sorted(
        scores.items(),
        key=lambda kv: (
            kv[1]["mase"] if np.isfinite(kv[1]["mase"]) else float("inf"),
            kv[1]["wape"] if np.isfinite(kv[1]["wape"]) else float("inf"),
            METHODS.index(kv[0]),
        ),
    )
    return ranked[0][0]


def forecast_skus(
    matrix: pd.DataFrame,
    *,
    horizon: int = FORECAST_HORIZON_WEEKS,
    methods: tuple[str, ...] = METHODS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pick a method per SKU and forecast forward.

    Returns ``(summary, forecast)``: one row per SKU with the chosen method and
    its backtest scores, and one row per SKU per future week.
    """
    if matrix.empty:
        return (
            pd.DataFrame(columns=["SKU", "Method", "MethodLabel", "WAPE", "ForecastAccuracy",
                                  "Bias", "MASE", "TrackingSignal", "ForecastWeekly"]),
            pd.DataFrame(columns=["SKU", "WeekEnding", "ForecastUnits", "Horizon"]),
        )

    weeks = pd.DatetimeIndex(matrix.columns)
    future = pd.date_range(weeks[-1] + pd.Timedelta(days=7), periods=horizon, freq="7D")

    summary_rows = []
    forecast_rows = []
    for key, row in zip(matrix.index, matrix.to_numpy(), strict=True):
        y = np.asarray(row, dtype=float)
        scores = backtest_series(y, methods=methods)
        chosen = _pick(scores)
        stats = scores.get(chosen, {})
        values = np.asarray(FORECASTERS[chosen](y, horizon), dtype=float)
        values = np.maximum(np.round(values, 3), 0.0)

        summary_rows.append({
            "SKU": key,
            "Method": chosen,
            "MethodLabel": METHOD_LABELS[chosen],
            "WAPE": stats.get("wape", float("nan")),
            "ForecastAccuracy": 1.0 - stats["wape"] if np.isfinite(stats.get("wape", np.nan)) else np.nan,
            "Bias": stats.get("bias", float("nan")),
            "MASE": stats.get("mase", float("nan")),
            "TrackingSignal": stats.get("tracking_signal", float("nan")),
            "ForecastWeekly": float(values.mean()),
        })
        for week, value in zip(future, values, strict=True):
            forecast_rows.append({
                "SKU": key,
                "WeekEnding": week,
                "ForecastUnits": float(value),
                "Horizon": int((week - weeks[-1]).days / 7),
            })

    return pd.DataFrame(summary_rows), pd.DataFrame(forecast_rows)


def fitted_history(
    matrix: pd.DataFrame,
    summary: pd.DataFrame,
    *,
    horizon: int = BACKTEST_HORIZON,
    folds: int = BACKTEST_FOLDS,
) -> pd.DataFrame:
    """Actual against what the chosen method would have said, week by week.

    This is the actual-vs-forecast line the report draws. It is out-of-sample:
    every point is produced by a model that had not seen it, which is the only
    version of that chart worth showing.
    """
    if matrix.empty or summary.empty:
        return pd.DataFrame(columns=["SKU", "WeekEnding", "ActualUnits", "ForecastUnits"])

    chosen = dict(zip(summary["SKU"], summary["Method"], strict=True))
    weeks = pd.DatetimeIndex(matrix.columns)
    rows = []
    for key, row in zip(matrix.index, matrix.to_numpy(), strict=True):
        y = np.asarray(row, dtype=float)
        fn = FORECASTERS[chosen.get(key, "ma4")]
        usable = min(folds, max(0, (len(y) - 20) // horizon))
        for fold in range(usable, 0, -1):
            cut = len(y) - fold * horizon
            history = y[:cut]
            actual = y[cut : cut + horizon]
            if len(actual) == 0 or len(history) < 8:
                continue
            forecast = np.asarray(fn(history, len(actual)), dtype=float)
            for offset in range(len(actual)):
                rows.append({
                    "SKU": key,
                    "WeekEnding": weeks[cut + offset],
                    "ActualUnits": float(actual[offset]),
                    "ForecastUnits": float(max(forecast[offset], 0.0)),
                })
    return pd.DataFrame(rows)
