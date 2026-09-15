"""
The JSON the pages draw from.

Every endpoint reads the model that was computed once at ingest and slices it.
Nothing here recomputes: a route that re-ran a forecast backtest would put
several seconds of CPU behind a chart refresh, and six charts on a page would
do it six times.

Two conventions worth knowing:

* ``NaN`` and ``inf`` are converted to ``null`` on the way out. They are real
  answers here - a SKU with no demand genuinely has infinite cover - but
  ``JSON.parse`` rejects both, and a chart that silently fails to render is
  worse than one showing a gap.
* Every list endpoint returns ``{"rows": [...], "total": n}`` rather than a
  bare array, so a truncated table can say it was truncated instead of quietly
  showing the top 200 as if they were all of them.
"""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
from flask import Blueprint, jsonify, request

from invapp.analytics import (
    accuracy as accuracy_mod,
    actions as actions_mod,
    ageing as ageing_mod,
    segmentation,
    working_capital,
)
from invapp.analytics.policy_simulation import (
    forecast_segment_scorecard,
    policy_scenario_summary,
    service_cost_frontier,
    simulate_network_policy,
)
from invapp.services.bootstrap import is_loading
from invapp.services.ingest import current_model, ingest_sheets
from invapp.services.io_utils import load_workbook_sheets
from invapp.services.state import get_state, set_state
from invapp.services.store import list_runs

bp = Blueprint("api", __name__)

MAX_ROWS = 5000


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _clean(frame: pd.DataFrame) -> pd.DataFrame:
    """Make a frame JSON-safe: no NaN, no inf, no numpy scalars, no Timestamps."""
    out = frame.copy()
    for column in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[column]):
            out[column] = out[column].dt.strftime("%Y-%m-%d")
        elif pd.api.types.is_numeric_dtype(out[column]) and not pd.api.types.is_bool_dtype(
            out[column]
        ):
            out[column] = out[column].astype(float).replace([np.inf, -np.inf], np.nan)
    return out.astype(object).where(pd.notna(out), None)


def rows(frame: pd.DataFrame | None, limit: int | None = None) -> dict:
    if frame is None or frame.empty:
        return {"rows": [], "total": 0, "truncated": False}
    total = int(len(frame))
    limit = min(limit or MAX_ROWS, MAX_ROWS)
    return {
        "rows": _clean(frame.head(limit)).to_dict(orient="records"),
        "total": total,
        "truncated": total > limit,
    }


def scalars(mapping: dict) -> dict:
    """A dict of KPIs, made JSON-safe the same way a frame is."""
    out = {}
    for key, value in mapping.items():
        if isinstance(value, (np.floating, float)):
            out[key] = None if not np.isfinite(float(value)) else float(value)
        elif isinstance(value, (np.integer,)):
            out[key] = int(value)
        elif isinstance(value, (pd.Timestamp,)):
            out[key] = value.date().isoformat()
        else:
            out[key] = value
    return out


def model_or_404():
    """The visitor's model, or the response explaining why there is not one.

    "Still building the sample" is 503 and not 404, because they need
    different behaviour from the caller: one is worth retrying in a second
    and the other never will be. Collapsing them is why a cold container
    used to greet its first visitor with an error that fixed itself on
    reload.
    """
    model = current_model()
    if model is not None and not model.is_empty:
        return model, None
    if is_loading():
        return None, (
            jsonify({"error": "Building the sample dataset.", "loading": True}), 503
        )
    return None, (jsonify({"error": "No workbook has been processed yet."}), 404)


def _filtered(frame: pd.DataFrame, allowed: tuple[str, ...]) -> pd.DataFrame:
    """Apply ?column=value query filters for the columns a route allows."""
    out = frame
    for column in allowed:
        value = request.args.get(column)
        if value:
            out = out[out[column].astype(str) == value]
    return out


def _csv(frame: pd.DataFrame, filename: str):
    return (
        frame.to_csv(index=False, lineterminator="\n"),
        200,
        {
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": f"attachment; filename={filename}",
        },
    )


def _xlsx(frames: dict[str, pd.DataFrame], filename: str):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for sheet, frame in frames.items():
            frame.to_excel(writer, sheet_name=sheet[:31], index=False)
    return (
        buf.getvalue(),
        200,
        {
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": f"attachment; filename={filename}",
        },
    )


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------
@bp.get("/ping")
def ping():
    return jsonify({"message": "pong"})


@bp.post("/workbook/process")
def process_workbook():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file provided."}), 400
    try:
        sheets = load_workbook_sheets(file)
        summary = ingest_sheets(sheets)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:                            # pragma: no cover - defensive
        return jsonify({"error": f"Could not process that workbook: {exc}"}), 400
    return jsonify(scalars(summary))


@bp.get("/runs")
def runs():
    return jsonify(rows(pd.DataFrame(list_runs())))


@bp.get("/parameters")
def parameters_get():
    model = current_model()
    overrides = getattr(get_state(), "param_overrides", {}) or {}
    return jsonify({
        "params": model.params if model else {},
        "overrides": overrides,
        "as_of": model.as_of.date().isoformat() if model else None,
    })


@bp.post("/parameters")
def parameters_set():
    """Change a planning parameter and rebuild the analysis from the same data.

    Rebuilding rather than patching: a service level change moves z, which moves
    safety stock, the reorder point, every order quantity, the excess threshold
    and therefore the action register. Recomputing the whole model is the only
    version of this that stays consistent.
    """
    model, error = model_or_404()
    if error:
        return error

    payload = request.get_json(silent=True) or dict(request.form or {})
    overrides = dict(getattr(get_state(), "param_overrides", {}) or {})
    for key, value in payload.items():
        try:
            overrides[str(key)] = float(value)
        except (TypeError, ValueError):
            return jsonify({"error": f"{key} must be a number."}), 400

    set_state(param_overrides=overrides)
    sheets = {
        "Item Master": model.items,
        "Supplier Master": model.suppliers,
        "Network Nodes": model.nodes,
        "Demand History": model.demand,
        "Inventory Snapshot": model.inventory,
        "Purchase Orders": model.purchase_orders,
        "Cycle Counts": model.count_variance,
        "Inventory Adjustments": model.adjustments,
        "Planning Parameters": pd.DataFrame(
            [{"Parameter": k, "Value": v} for k, v in model.params.items()]
        ),
    }
    summary = ingest_sheets(sheets, persist=False)
    return jsonify(scalars(summary))


# --------------------------------------------------------------------------
# 1. Inventory overview
# --------------------------------------------------------------------------
@bp.get("/overview")
def overview():
    model, error = model_or_404()
    if error:
        return error
    return jsonify(scalars(model.headline))


@bp.get("/overview/value_by")
def overview_value_by():
    model, error = model_or_404()
    if error:
        return error
    dimension = request.args.get("dim", "Department")
    if dimension not in model.ageing.columns:
        return jsonify({"error": f"Unknown dimension {dimension!r}."}), 400
    return jsonify(rows(working_capital.value_by_dimension(
        model.ageing, dimension, params=model.params
    )))


@bp.get("/overview/trend")
def overview_trend():
    model, error = model_or_404()
    if error:
        return error
    return jsonify(rows(model.trend))


@bp.get("/overview/carrying")
def overview_carrying():
    model, error = model_or_404()
    if error:
        return error
    return jsonify(rows(model.carrying))


@bp.get("/overview/abc")
def overview_abc():
    model, error = model_or_404()
    if error:
        return error
    if model.segments.empty:
        return jsonify(rows(None))
    grouped = (
        model.segments.groupby("ABCClass", as_index=False)
        .agg(
            SKUCount=("SKU", "nunique"),
            InventoryValueUSD=("InventoryValueUSD", "sum"),
            AnnualConsumptionValueUSD=("AnnualConsumptionValueUSD", "sum"),
        )
        .sort_values("ABCClass")
    )
    return jsonify(rows(grouped))


# --------------------------------------------------------------------------
# 2. Demand and forecasting
# --------------------------------------------------------------------------
@bp.get("/demand/history")
def demand_history():
    """Actual shipments by week, optionally for one SKU, node or department."""
    model, error = model_or_404()
    if error:
        return error

    frame = model.demand.merge(
        model.items[["SKU", "Department", "Category"]], on="SKU", how="left"
    )
    frame = _filtered(frame, ("SKU", "NodeID", "Department", "Category"))
    grain = request.args.get("grain", "week")
    key = frame["WeekEnding"].dt.to_period("M").dt.to_timestamp() if grain == "month" \
        else frame["WeekEnding"]
    out = (
        frame.assign(Period=key)
        .groupby("Period", as_index=False)
        .agg(
            UnitsShipped=("UnitsShipped", "sum"),
            UnitsRequested=("UnitsRequested", "sum"),
            NetSalesUSD=("NetSalesUSD", "sum"),
        )
        .sort_values("Period")
    )
    out["FillRatePct"] = out["UnitsShipped"] / out["UnitsRequested"].replace(0, np.nan)
    return jsonify(rows(out))


@bp.get("/demand/actual_vs_forecast")
def demand_actual_vs_forecast():
    """Out-of-sample fitted history plus the forward forecast, in one series."""
    model, error = model_or_404()
    if error:
        return error

    sku = request.args.get("SKU") or request.args.get("sku")
    department = request.args.get("Department")

    fit = model.forecast_fit
    forward = model.forecast
    if sku:
        fit = fit[fit["SKU"] == sku]
        forward = forward[forward["SKU"] == sku]
    elif department:
        members = set(model.items.loc[model.items["Department"] == department, "SKU"])
        fit = fit[fit["SKU"].isin(members)]
        forward = forward[forward["SKU"].isin(members)]

    history = (
        fit.groupby("WeekEnding", as_index=False)
        .agg(ActualUnits=("ActualUnits", "sum"), ForecastUnits=("ForecastUnits", "sum"))
        .sort_values("WeekEnding")
    )
    history["Series"] = "Backtest"
    ahead = (
        forward.groupby("WeekEnding", as_index=False)
        .agg(ForecastUnits=("ForecastUnits", "sum"))
        .sort_values("WeekEnding")
    )
    ahead["ActualUnits"] = np.nan
    ahead["Series"] = "Forecast"
    combined = pd.concat([history, ahead], ignore_index=True)

    actual = history["ActualUnits"].to_numpy()
    predicted = history["ForecastUnits"].to_numpy()
    denominator = float(np.abs(actual).sum())
    return jsonify({
        **rows(combined),
        "accuracy": 1.0 - float(np.abs(actual - predicted).sum()) / denominator
        if denominator > 0 else None,
        "bias": float((predicted - actual).sum()) / denominator if denominator > 0 else None,
    })


@bp.get("/demand/skus")
def demand_skus():
    model, error = model_or_404()
    if error:
        return error
    if model.segments.empty:
        return jsonify(rows(None))
    columns = [c for c in ("SKU", "ItemDescription", "Department", "Category", "ABCClass",
                           "XYZClass", "MovementClass", "WeeklyDemand", "ForecastWeekly",
                           "Method", "MethodLabel", "ForecastAccuracy", "Bias",
                           "CoefficientOfVariation") if c in model.segments.columns]
    frame = _filtered(model.segments[columns], ("Department", "ABCClass", "XYZClass", "Method"))
    frame = frame.sort_values(["WeeklyDemand", "SKU"], ascending=[False, True], kind="stable")
    return jsonify(rows(frame, limit=int(request.args.get("limit", 400))))


@bp.get("/demand/method_mix")
def demand_method_mix():
    """Which model won, how often, and how accurate it was.

    The honest version of a forecasting page: if a four-week moving average
    wins on a third of the catalogue, the page should say so rather than
    quietly running something more impressive underneath.
    """
    model, error = model_or_404()
    if error:
        return error
    if model.forecast_summary.empty:
        return jsonify(rows(None))
    mix = (
        model.forecast_summary.groupby(["Method", "MethodLabel"], as_index=False)
        .agg(
            SKUCount=("SKU", "nunique"),
            MedianAccuracy=("ForecastAccuracy", "median"),
            MedianMASE=("MASE", "median"),
            WeeklyUnits=("ForecastWeekly", "sum"),
        )
        .sort_values("SKUCount", ascending=False)
    )
    return jsonify(rows(mix))


@bp.get("/demand/seasonality")
def demand_seasonality():
    """Monthly demand index by department: mean 1.0 within each department."""
    model, error = model_or_404()
    if error:
        return error
    frame = model.demand.merge(model.items[["SKU", "Department"]], on="SKU", how="left")
    frame["Month"] = frame["WeekEnding"].dt.month
    grouped = frame.groupby(["Department", "Month"], as_index=False)["UnitsShipped"].sum()
    totals = grouped.groupby("Department")["UnitsShipped"].transform("mean")
    grouped["SeasonalIndex"] = grouped["UnitsShipped"] / totals.replace(0, np.nan)
    return jsonify(rows(grouped))


@bp.get("/demand/bias")
def demand_bias():
    """Where the forecast leans, and by how much."""
    model, error = model_or_404()
    if error:
        return error
    if model.forecast_summary.empty:
        return jsonify(rows(None))
    frame = model.forecast_summary.merge(
        model.items[["SKU", "ItemDescription", "Department"]], on="SKU", how="left"
    )
    frame = frame[frame["Bias"].notna()]
    frame["BiasDirection"] = np.select(
        [frame["Bias"] > 0.10, frame["Bias"] < -0.10],
        ["Over-forecast", "Under-forecast"],
        default="Within 10%",
    )
    frame["AbsBias"] = frame["Bias"].abs()
    frame = frame.sort_values(["AbsBias", "SKU"], ascending=[False, True], kind="stable")
    return jsonify(rows(frame[[
        "SKU", "ItemDescription", "Department", "Method", "MethodLabel", "Bias",
        "AbsBias", "BiasDirection", "ForecastAccuracy", "TrackingSignal", "ForecastWeekly",
    ]], limit=int(request.args.get("limit", 300))))


@bp.get("/demand/segment_scorecard")
def demand_segment_scorecard():
    """Forecast evidence at the ABC-XYZ policy grain."""
    model, error = model_or_404()
    if error:
        return error
    return jsonify(rows(forecast_segment_scorecard(
        model.forecast_summary, model.segments
    )))


# --------------------------------------------------------------------------
# 3. Replenishment planner
# --------------------------------------------------------------------------
PLAN_COLUMNS = [
    "SKU", "ItemDescription", "Department", "NodeID", "ABCClass", "XYZClass",
    "SupplierID", "OnHandUnits", "ReservedUnits", "InTransitUnits", "InventoryPosition",
    "PlannedWeeklyDemand", "DailyDemand", "DailyStdDev", "LeadTimeDaysActual",
    "LeadTimeDaysStdDev", "LeadTimeDaysContract", "ServiceLevelTarget", "SafetyFactorZ",
    "SafetyStockUnits", "ReorderPointUnits", "ReorderPointOnContract", "EOQUnits",
    "OrderUpToUnits", "RecommendedOrderUnits", "RecommendedOrderValue", "DaysOfCover",
    "WeeksOfCover", "StockoutRisk", "ExpectedUnitsShort", "StockoutExposureUSD",
    "UnitCost", "CasePack", "Urgency",
]


def _plan_frame(model) -> pd.DataFrame:
    frame = model.plan[[c for c in PLAN_COLUMNS if c in model.plan.columns]]
    frame = _filtered(frame, ("NodeID", "ABCClass", "XYZClass", "Department", "Urgency",
                              "SupplierID", "SKU"))
    if request.args.get("due") == "1":
        frame = frame[frame["RecommendedOrderUnits"] > 0]
    return frame


@bp.get("/replenishment/plan")
def replenishment_plan():
    model, error = model_or_404()
    if error:
        return error
    frame = _plan_frame(model).sort_values(
        ["StockoutExposureUSD", "SKU"], ascending=[False, True], kind="stable"
    )
    return jsonify(rows(frame, limit=int(request.args.get("limit", 300))))


@bp.get("/replenishment/summary")
def replenishment_summary():
    model, error = model_or_404()
    if error:
        return error
    plan = model.plan
    due = plan[plan["RecommendedOrderUnits"] > 0]
    return jsonify(scalars({
        "LinesPlanned": int(len(plan)),
        "LinesDue": int(len(due)),
        "OrderValueUSD": float(due["RecommendedOrderValue"].sum()),
        "OrderUnits": float(due["RecommendedOrderUnits"].sum()),
        "SafetyStockUnits": float(plan["SafetyStockUnits"].sum()),
        "SafetyStockValueUSD": float((plan["SafetyStockUnits"] * plan["UnitCost"]).sum()),
        "StockoutExposureUSD": float(plan["StockoutExposureUSD"].sum()),
        "StockedOut": int((plan["Urgency"] == "Stocked out").sum()),
        "BelowSafety": int((plan["Urgency"] == "Below safety stock").sum()),
        "AtReorderPoint": int((plan["Urgency"] == "At reorder point").sum()),
        "Healthy": int((plan["Urgency"] == "Healthy").sum()),
        "MeanLeadTimeDays": float(plan["LeadTimeDaysActual"].mean()),
        # What planning on the contract instead of on receipts would cost: the
        # safety stock the two lead times disagree about, priced.
        "ContractGapUnits": float(
            (plan["ReorderPointUnits"] - plan["ReorderPointOnContract"]).sum()
        ),
        "ContractGapValueUSD": float(
            ((plan["ReorderPointUnits"] - plan["ReorderPointOnContract"]) * plan["UnitCost"]).sum()
        ),
    }))


@bp.get("/replenishment/urgency")
def replenishment_urgency():
    model, error = model_or_404()
    if error:
        return error
    grouped = (
        model.plan.groupby("Urgency", as_index=False)
        .agg(
            Lines=("SKU", "size"),
            OrderValueUSD=("RecommendedOrderValue", "sum"),
            ExposureUSD=("StockoutExposureUSD", "sum"),
        )
    )
    return jsonify(rows(grouped))


@bp.get("/replenishment/eoq")
def replenishment_eoq():
    model, error = model_or_404()
    if error:
        return error
    frame = _filtered(model.eoq, ("Department", "ABCClass", "SKU"))
    return jsonify(rows(frame, limit=int(request.args.get("limit", 200))))


@bp.get("/replenishment/service_curve")
def replenishment_service_curve():
    model, error = model_or_404()
    if error:
        return error
    return jsonify(rows(model.service_curve))


@bp.get("/replenishment/service_cost_frontier")
def replenishment_service_cost_frontier():
    """Annual buffer and shortage trade-off under transparent cost assumptions."""
    model, error = model_or_404()
    if error:
        return error
    try:
        carrying = float(
            request.args.get(
                "carrying_rate", model.params.get("CarryingCostRate", 0.24)
            )
        )
        penalty = float(request.args.get(
            "stockout_penalty", model.params.get("StockoutPenaltyPerUnit", 6.5)
        ))
        frontier = service_cost_frontier(
            model.plan,
            carrying_cost_rate=carrying,
            stockout_penalty_per_unit=penalty,
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(rows(frontier))


@bp.get("/replenishment/policy_scenario")
def replenishment_policy_scenario():
    """Network-wide demand, lead-time, and service policy simulation."""
    model, error = model_or_404()
    if error:
        return error
    try:
        scenario = simulate_network_policy(
            model.plan,
            service_level=float(request.args.get("service_level", 0.95)),
            demand_multiplier=float(request.args.get("demand_multiplier", 1.0)),
            lead_time_multiplier=float(request.args.get("lead_time_multiplier", 1.0)),
            review_period_days=float(model.params.get("ReviewPeriodDays", 7.0)),
            stockout_penalty_per_unit=float(
                model.params.get("StockoutPenaltyPerUnit", 6.5)
            ),
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    detail = scenario[[
        "SKU", "NodeID", "ABCClass", "ScenarioStatus",
        "InventoryPosition", "ScenarioSafetyStockUnits",
        "ScenarioReorderPointUnits", "ScenarioOrderUnits",
        "ScenarioOrderValueUSD", "LeadWindowExpectedUnitsShort",
        "LeadWindowExposureUSD",
    ]]
    return jsonify({
        **rows(detail, limit=int(request.args.get("limit", 100))),
        "summary": scalars(policy_scenario_summary(scenario)),
        "assumptions": {
            "service_level": float(scenario.iloc[0].ScenarioServiceLevel),
            "demand_multiplier": float(scenario.iloc[0].DemandMultiplier),
            "lead_time_multiplier": float(scenario.iloc[0].LeadTimeMultiplier),
            "boundary": (
                "Simulated policy screen; requires planner approval "
                "and local cost validation."
            ),
        },
    })


@bp.get("/download/replenishment.csv")
def download_replenishment_csv():
    model, error = model_or_404()
    if error:
        return error
    return _csv(_plan_frame(model), "replenishment_plan.csv")


@bp.get("/download/replenishment.xlsx")
def download_replenishment_xlsx():
    model, error = model_or_404()
    if error:
        return error
    return _xlsx(
        {
            "Replenishment": _plan_frame(model),
            "EOQ": model.eoq,
            "Service levels": model.service_curve,
        },
        "replenishment_plan.xlsx",
    )


# --------------------------------------------------------------------------
# 4. SKU health
# --------------------------------------------------------------------------
@bp.get("/health/pareto")
def health_pareto():
    model, error = model_or_404()
    if error:
        return error
    curve = segmentation.pareto_curve(model.segments)
    return jsonify(rows(curve, limit=int(request.args.get("limit", 500))))


@bp.get("/health/matrix")
def health_matrix():
    model, error = model_or_404()
    if error:
        return error
    return jsonify(rows(segmentation.abc_xyz_matrix(model.segments)))


@bp.get("/health/ageing")
def health_ageing():
    model, error = model_or_404()
    if error:
        return error
    dimension = request.args.get("dim")
    if dimension and dimension not in model.ageing.columns:
        return jsonify({"error": f"Unknown dimension {dimension!r}."}), 400
    return jsonify(rows(ageing_mod.ageing_by_bucket(model.ageing, dimension)))


@bp.get("/health/dead_stock")
def health_dead_stock():
    model, error = model_or_404()
    if error:
        return error
    frame = _filtered(
        ageing_mod.dead_stock_register(model.ageing),
        ("Department", "NodeID", "ABCClass", "HealthFlag"),
    )
    return jsonify(rows(frame, limit=int(request.args.get("limit", 300))))


@bp.get("/health/summary")
def health_summary():
    model, error = model_or_404()
    if error:
        return error
    aged = model.ageing
    segments = model.segments
    return jsonify(scalars({
        "SKUCount": int(segments["SKU"].nunique()) if not segments.empty else 0,
        "DeadStockSKUs": int(aged.loc[aged["IsDeadStock"], "SKU"].nunique()),
        "DeadStockValueUSD": float(aged.loc[aged["IsDeadStock"], "InventoryValueUSD"].sum()),
        "SlowMovingSKUs": int(aged.loc[aged["IsSlowMoving"], "SKU"].nunique()),
        "SlowMovingValueUSD": float(aged.loc[aged["IsSlowMoving"], "InventoryValueUSD"].sum()),
        "ExcessValueUSD": float(aged["ExcessValueUSD"].sum()),
        "EOReserveUSD": float(aged["EOReserveUSD"].sum()),
        "Over180DaysValueUSD": float(
            aged.loc[aged["AgeDays"] > 180, "InventoryValueUSD"].sum()
        ),
        "CriticalSKUs": int(segments["IsCritical"].sum()) if not segments.empty else 0,
        "AClassShareOfValue": float(
            segments.loc[segments["ABCClass"] == "A", "AnnualConsumptionValueUSD"].sum()
            / max(segments["AnnualConsumptionValueUSD"].sum(), 1.0)
        ) if not segments.empty else None,
        "AClassShareOfSKUs": float(
            (segments["ABCClass"] == "A").mean()
        ) if not segments.empty else None,
    }))


@bp.get("/health/movement")
def health_movement():
    model, error = model_or_404()
    if error:
        return error
    grouped = (
        model.segments.groupby("MovementClass", as_index=False)
        .agg(
            SKUCount=("SKU", "nunique"),
            InventoryValueUSD=("InventoryValueUSD", "sum"),
            AnnualConsumptionValueUSD=("AnnualConsumptionValueUSD", "sum"),
        )
    )
    return jsonify(rows(grouped))


@bp.get("/download/sku_health.csv")
def download_sku_health_csv():
    model, error = model_or_404()
    if error:
        return error
    return _csv(model.segments, "sku_health.csv")


# --------------------------------------------------------------------------
# 5. Network and suppliers
# --------------------------------------------------------------------------
@bp.get("/network/nodes")
def network_nodes():
    model, error = model_or_404()
    if error:
        return error
    return jsonify(rows(model.node_summary))


@bp.get("/network/transfers")
def network_transfers():
    model, error = model_or_404()
    if error:
        return error
    frame = _filtered(model.transfers, ("FromNode", "ToNode", "Department", "ABCClass", "SKU"))
    return jsonify(rows(frame, limit=int(request.args.get("limit", 200))))


@bp.get("/network/balance")
def network_balance():
    """Cover per node for one SKU, or the worst-imbalanced SKUs across the network."""
    model, error = model_or_404()
    if error:
        return error
    sku = request.args.get("SKU") or request.args.get("sku")
    frame = model.balance
    if sku:
        frame = frame[frame["SKU"] == sku]
    else:
        spread = (
            frame.groupby("SKU")["CoverGapWeeks"]
            .agg(lambda s: float(s.max() - s.min()))
            .sort_values(ascending=False)
        )
        frame = frame[frame["SKU"].isin(spread.head(25).index)]
    columns = [c for c in ("SKU", "ItemDescription", "NodeID", "ABCClass", "OnHandUnits",
                           "InventoryPosition", "WeeksOfCover", "NetworkDaysOfCover",
                           "CoverGapWeeks", "DeficitUnits", "SurplusUnits", "Urgency")
               if c in frame.columns]
    return jsonify(rows(frame[columns].sort_values(
        ["SKU", "NodeID"], kind="stable"
    ), limit=int(request.args.get("limit", 400))))


@bp.get("/suppliers/scorecard")
def suppliers_scorecard():
    model, error = model_or_404()
    if error:
        return error
    return jsonify(rows(_filtered(model.scorecard, ("SupplierID", "Grade", "Country"))))


@bp.get("/suppliers/open_pos")
def suppliers_open_pos():
    model, error = model_or_404()
    if error:
        return error
    frame = _filtered(model.open_pos, ("SupplierID", "NodeID", "SKU"))
    if request.args.get("overdue") == "1":
        frame = frame[frame["IsOverdue"]]
    frame = frame.sort_values(["DaysLate", "PONumber"], ascending=[False, True], kind="stable")
    return jsonify(rows(frame, limit=int(request.args.get("limit", 300))))


@bp.get("/suppliers/lead_time_gap")
def suppliers_lead_time_gap():
    """Contracted lead time against delivered, per supplier."""
    model, error = model_or_404()
    if error:
        return error
    if model.scorecard.empty:
        return jsonify(rows(None))
    columns = [c for c in ("SupplierID", "SupplierName", "Country", "LeadTimeDaysContract",
                           "LeadTimeDaysActual", "LeadTimeDaysStdDev", "LeadTimeDaysP95",
                           "LeadTimeGapDays", "OnTimePct", "Grade", "POLines")
               if c in model.scorecard.columns]
    frame = model.scorecard[columns].sort_values(
        ["LeadTimeGapDays", "SupplierID"], ascending=[False, True], kind="stable"
    )
    return jsonify(rows(frame))


@bp.get("/download/transfers.csv")
def download_transfers_csv():
    model, error = model_or_404()
    if error:
        return error
    return _csv(model.transfers, "transfer_plan.csv")


# --------------------------------------------------------------------------
# 6. Accuracy and actions
# --------------------------------------------------------------------------
@bp.get("/accuracy/summary")
def accuracy_summary():
    model, error = model_or_404()
    if error:
        return error
    dimension = request.args.get("dim")
    if dimension and dimension not in model.count_variance.columns:
        return jsonify({"error": f"Unknown dimension {dimension!r}."}), 400
    return jsonify(rows(accuracy_mod.accuracy_summary(model.count_variance, dimension)))


@bp.get("/accuracy/reasons")
def accuracy_reasons():
    model, error = model_or_404()
    if error:
        return error
    return jsonify(rows(accuracy_mod.variance_by_reason(model.count_variance)))


@bp.get("/accuracy/waterfall")
def accuracy_waterfall():
    """Bridge book value to physical value over a window, not over all history.

    The window matters. Run over eighteen months of receipts and shipments the
    bridge opens at two and a half times what the business closes at, which is
    arithmetically correct and useless: nobody reconciles a year and a half in
    one step. Thirteen weeks is a quarter, which is what gets signed off.
    """
    model, error = model_or_404()
    if error:
        return error
    weeks = max(int(request.args.get("weeks", 13)), 1)
    since = model.as_of - pd.Timedelta(weeks=weeks)

    cost = dict(zip(model.items["SKU"], model.items["UnitCost"], strict=True))
    demand = model.demand[model.demand["WeekEnding"] > since]
    cogs = float((demand["UnitsShipped"] * demand["SKU"].map(cost).fillna(0.0)).sum())

    received = model.purchase_orders
    received = received[received["ReceivedDate"] > since] if not received.empty else received
    receipts = float((received["QtyReceived"] * received["UnitCost"]).sum()) if not received.empty \
        else 0.0

    adjustments = model.adjustments
    if not adjustments.empty:
        adjustments = adjustments[adjustments["AdjustmentDate"] > since]

    frame = accuracy_mod.shrinkage_waterfall(
        adjustments,
        float(model.inventory["InventoryValueUSD"].sum()),
        cogs,
        receipts,
    )
    return jsonify({**rows(frame), "weeks": weeks,
                    "since": since.date().isoformat()})


@bp.get("/accuracy/coverage")
def accuracy_coverage():
    model, error = model_or_404()
    if error:
        return error
    return jsonify(rows(accuracy_mod.count_coverage(
        model.inventory, model.count_variance, model.as_of
    )))


@bp.get("/actions/register")
def actions_register():
    model, error = model_or_404()
    if error:
        return error
    frame = _filtered(model.register, ("Action", "NodeID", "Department", "ABCClass", "SKU"))
    return jsonify(rows(frame, limit=int(request.args.get("limit", 200))))


@bp.get("/actions/summary")
def actions_summary():
    model, error = model_or_404()
    if error:
        return error
    return jsonify(rows(actions_mod.register_summary(model.register)))


@bp.get("/download/actions.csv")
def download_actions_csv():
    model, error = model_or_404()
    if error:
        return error
    return _csv(model.register, "action_register.csv")


@bp.get("/download/report.xlsx")
def download_report_xlsx():
    """Everything, one sheet per report - the file that gets emailed."""
    model, error = model_or_404()
    if error:
        return error
    return _xlsx(
        {
            "Actions": model.register,
            "Replenishment": model.plan[[c for c in PLAN_COLUMNS if c in model.plan.columns]],
            "SKU health": model.segments,
            "Ageing": ageing_mod.dead_stock_register(model.ageing),
            "Transfers": model.transfers,
            "Suppliers": model.scorecard,
            "Count variance": model.count_variance,
            "Nodes": model.node_summary,
            "Forecast": model.forecast_summary,
        },
        "inventory_analytics.xlsx",
    )
