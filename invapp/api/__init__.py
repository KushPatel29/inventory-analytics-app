from flask import Blueprint, jsonify, request, render_template_string
import pandas as pd

from invapp.services.io_utils import load_workbook_sheets
from invapp.services.cleaning import preprocess_data, process_inventory_snapshot
from invapp.services.aggregation import (
    aggregate_sales_history,
    merge_data,
    aggregate_final_data,
)
from invapp.services.costing import compute_holding_cost
from invapp.services.classification import quadrantify, top_n_by_metric
from invapp.services.classification import classify_movement
from invapp.services.state import set_state, get_state
from invapp.services.planning import parent_purchase_plan
from invapp.services.bins import prepare_bins_data
from invapp.services.store import save_run, list_runs, load_run
from invapp.services.moves import compute_fz_to_ext_moves, compute_ext_to_fz_moves


bp = Blueprint("api", __name__)


@bp.get("/ping")
def ping():
    return jsonify({"message": "pong"})


@bp.get("/inventory/summary")
def inventory_summary():
    # Example static payload for now
    data = {
        "total_items": 1234,
        "low_stock": 17,
        "out_of_stock": 5,
        "last_updated": "2025-01-01T12:00:00Z",
    }
    return jsonify(data)


@bp.post("/workbook/process")
def process_workbook():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file provided"}), 400

    try:
        sheets = load_workbook_sheets(file)
        sales = sheets.get("Sales History", pd.DataFrame())
        inv = sheets.get("Inventory Detail", pd.DataFrame())
        prod = sheets.get("Production Batch", pd.DataFrame())
        cost_val = sheets.get("Cost Value", pd.DataFrame())
        inv1 = sheets.get("Inventory Detail1", pd.DataFrame())

        sales, inv, prod, cost_val = preprocess_data(sales, inv, prod, cost_val)
        agg_sales = aggregate_sales_history(sales) if not sales.empty else pd.DataFrame(
            columns=["SKU", "Supplier", "Protein", "Description", "ShippedLb", "QuantityOrdered", "Cost", "Rev"]
        )
        merged = merge_data(agg_sales, inv, prod)
        sku_stats = aggregate_final_data(merged, sales)

        # Compute pack counts from Inventory Detail1 (distinct PackId1 per SKU+state)
        if not inv1.empty and {"SKU", "ProductState", "PackId1"}.issubset(inv1.columns):
            inv1_cc = inv1.loc[:, ["SKU", "ProductState", "PackId1"]].dropna(subset=["PackId1"]).copy()
            inv1_cc["PackId1"] = inv1_cc["PackId1"].astype(str).str.strip()
            packs = (
                inv1_cc.groupby(["SKU", "ProductState"], as_index=False)["PackId1"].nunique()
                .rename(columns={"PackId1": "NumPacksOnHand"})
            )
            sku_stats = sku_stats.merge(packs, on=["SKU", "ProductState"], how="left")
            sku_stats["NumPacksOnHand"] = sku_stats.get("NumPacksOnHand", 0).fillna(0).astype(int)

        # Holding cost is priced off the item-level snapshot: OnHandCost is a
        # derived column (Cost_pr x ItemCount) that only exists after
        # process_inventory_snapshot. Passing the raw sheet here meant
        # compute_holding_cost fell back to a scalar default and raised
        # "'float' object has no attribute 'fillna'" on every upload.
        snapshot = process_inventory_snapshot(inv.copy())
        snapshot["OriginDate"] = pd.to_datetime(snapshot.get("OriginDate"), errors="coerce")
        hc_params = get_state().holding_cost_params
        holding_cost = compute_holding_cost(snapshot, params=hc_params)

        set_state(sku_stats=sku_stats, holding_cost=holding_cost, raw_sheets=sheets)

        # Persist this run to SQLite for future reloads
        try:
            run_id = save_run(sku_stats, holding_cost, hc_params)
        except Exception:
            run_id = None

        # Build a small HTML snippet for HTMX target
        total_skus = int(sku_stats["SKU"].nunique()) if not sku_stats.empty else 0
        total_weight = float(sku_stats.get("OnHandWeightTotal", pd.Series(dtype=float)).sum())
        total_cost = float(sku_stats.get("OnHandCostTotal", pd.Series(dtype=float)).sum())
        avg_woh = float(sku_stats.get("WeeksOnHand", pd.Series(dtype=float)).mean()) if not sku_stats.empty else 0.0

        html = render_template_string(
            """
            <div class="grid grid-cols-2 gap-4">
              <div class="bg-green-50 border border-green-200 p-4 rounded">Processed <b>{{ total_skus }}</b> SKUs.</div>
              <div class="bg-blue-50 border border-blue-200 p-4 rounded">On-Hand: <b>{{ total_weight | round(0) }} lb</b></div>
              <div class="bg-amber-50 border border-amber-200 p-4 rounded">Cost: <b>${{ '{:,.0f}'.format(total_cost) }}</b></div>
              <div class="bg-indigo-50 border border-indigo-200 p-4 rounded">Avg WOH: <b>{{ '{:.1f}'.format(avg_woh) }} wks</b></div>
            </div>
            {% if run_id %}
            <div class="mt-3 text-sm text-gray-600">Saved as run ID <b>{{ run_id }}</b>.</div>
            {% endif %}
            """,
            total_skus=total_skus,
            total_weight=total_weight,
            total_cost=total_cost,
            avg_woh=avg_woh,
            run_id=run_id,
        )
        return html
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.get("/kpis")
def kpis():
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty:
        return jsonify({"error": "No data processed yet"}), 404
    result = {
        "total_skus": int(df["SKU"].nunique()),
        "total_weight_lb": float(df.get("OnHandWeightTotal", pd.Series(dtype=float)).sum()),
        "total_cost": float(df.get("OnHandCostTotal", pd.Series(dtype=float)).sum()),
        "avg_weeks_on_hand": float(df.get("WeeksOnHand", pd.Series(dtype=float)).mean()),
        "avg_turns": float(df.get("AnnualTurns", pd.Series(dtype=float)).mean()),
    }
    return jsonify(result)


@bp.get("/suppliers/top_cost")
def suppliers_top_cost():
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty:
        return jsonify([])
    top = top_n_by_metric(df, "Supplier", "OnHandCostTotal", n=int(request.args.get("n", 10)))
    return jsonify(top.to_dict(orient="records"))


@bp.get("/holding_cost/top")
def holding_cost_top():
    st = get_state()
    hc = st.holding_cost
    if hc is None or hc.empty:
        return jsonify([])
    n = int(request.args.get("n", 10))
    top = hc.groupby("SKU_Desc", as_index=False)["TotalHoldingCost"].sum().nlargest(n, "TotalHoldingCost")
    return jsonify(top.to_dict(orient="records"))


@bp.get("/quadrants")
def quadrants():
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty:
        return jsonify({})
    df_q, xm, ym = quadrantify(df.rename(columns={"TotalUsage": "X", "OnHandWeightTotal": "Y"}), "X", "Y")
    counts = df_q.groupby("Quadrant").size().to_dict()
    return jsonify({"xm": xm, "ym": ym, "counts": counts})


@bp.get("/svsi")
def svsi():
    """Return sample points for Usage (X) vs On-Hand (Y) with labels.
    Limits to top-N by cost for visualization purposes.
    """
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty:
        return jsonify([])
    n = int(request.args.get("n", 300))
    df = df.copy()
    df["X"] = df.get("TotalUsage", 0)
    df["Y"] = df.get("OnHandWeightTotal", 0)
    df["Cost"] = df.get("OnHandCostTotal", 0)
    df["Label"] = df.get("SKU_Desc", df.get("SKU", "").astype(str))
    top = df.nlargest(n, "Cost")[["X", "Y", "Cost", "Label", "Supplier", "Protein"]]
    return jsonify(top.to_dict(orient="records"))


@bp.get("/insights")
def insights():
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty:
        return jsonify({"error": "No data processed yet"}), 404
    total_skus = int(df["SKU"].nunique())
    total_weight = float(df.get("OnHandWeightTotal", 0).sum())
    total_cost = float(df.get("OnHandCostTotal", 0).sum())
    avg_woh = float(df.get("WeeksOnHand", 0).mean())
    med_woh = float(df.get("WeeksOnHand", 0).median())
    avg_turns = float(df.get("AnnualTurns", 0).mean())
    med_turns = float(df.get("AnnualTurns", 0).median())
    at_risk = int((df.get("WeeksOnHand", 0) < 1).sum())
    healthy = total_skus - at_risk
    return jsonify(
        {
            "total_skus": total_skus,
            "total_weight_lb": total_weight,
            "total_cost": total_cost,
            "avg_woh": avg_woh,
            "med_woh": med_woh,
            "avg_turns": avg_turns,
            "med_turns": med_turns,
            "at_risk_skus": at_risk,
            "healthy_skus": healthy,
        }
    )


@bp.get("/insights/abc")
def insights_abc():
    st = get_state()
    hc = st.holding_cost
    if hc is None or hc.empty or "ABC" not in hc.columns or "InventoryValue" not in hc.columns:
        return jsonify([])
    abc_df = hc.groupby("ABC", as_index=False).agg(Value=("InventoryValue", "sum"))
    return jsonify(abc_df.to_dict(orient="records"))


@bp.get("/insights/cost_by_protein")
def insights_cost_by_protein():
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty or "Protein" not in df.columns or "OnHandCostTotal" not in df.columns:
        return jsonify([])
    agg = df.groupby("Protein", as_index=False).agg(TotalCost=("OnHandCostTotal", "sum"))
    return jsonify(agg.to_dict(orient="records"))


@bp.get("/insights/cost_vs_woh")
def insights_cost_vs_woh():
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty:
        return jsonify([])
    d = df.copy()
    d = d[["SKU_Desc", "WeeksOnHand", "OnHandCostTotal", "Protein"]].dropna()
    d = d.rename(columns={"SKU_Desc": "Label", "OnHandCostTotal": "Cost"})
    # limit to 400 points by cost
    d = d.nlargest(400, "Cost")
    return jsonify(d.to_dict(orient="records"))


@bp.get("/purchase_plan")
def purchase_plan():
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty:
        return jsonify({"error": "No data processed yet"}), 404
    prod_detail = st.raw_sheets.get("Product Detail") if st.raw_sheets else None
    desired = float(request.args.get("woh", 4.0))
    plan = parent_purchase_plan(df, prod_detail, desired_woh=desired)
    plan = plan[plan["PacksToOrder"] > 0]
    return jsonify(plan.to_dict(orient="records"))


@bp.get("/runs")
def runs_list():
    return jsonify(list_runs())


@bp.post("/runs/load")
def runs_load():
    run_id = int(request.args.get("run_id")) if request.args.get("run_id") else None
    if not run_id:
        return jsonify({"error": "run_id is required"}), 400
    sku_stats, holding_cost, params = load_run(run_id)
    set_state(sku_stats=sku_stats, holding_cost=holding_cost)
    set_state(holding_cost_params=params)
    return jsonify({"loaded_run": run_id})


@bp.get("/holding_cost/config")
def get_hc_config():
    st = get_state()
    return jsonify(st.holding_cost_params)


@bp.post("/holding_cost/config")
def set_hc_config():
    data = request.get_json(silent=True) or dict(request.form or {})
    st = get_state()
    params = st.holding_cost_params.copy()
    for k in ("rc", "sa", "spc", "rr"):
        if k in data:
            params[k] = float(data[k])
    set_state(holding_cost_params=params)
    return jsonify(params)


@bp.get("/download/purchase_plan.csv")
def download_purchase_plan_csv():
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty:
        return jsonify({"error": "No data processed yet"}), 404
    desired = float(request.args.get("woh", 4.0))
    prod_detail = st.raw_sheets.get("Product Detail") if st.raw_sheets else None
    plan = parent_purchase_plan(df, prod_detail, desired_woh=desired)
    plan = plan[plan["PacksToOrder"] > 0]
    csv = plan.to_csv(index=False)
    return (
        csv,
        200,
        {
            "Content-Type": "text/csv",
            "Content-Disposition": "attachment; filename=purchase_plan.csv",
        },
    )


@bp.get("/download/purchase_plan.xlsx")
def download_purchase_plan_xlsx():
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty:
        return jsonify({"error": "No data processed yet"}), 404
    desired = float(request.args.get("woh", 4.0))
    prod_detail = st.raw_sheets.get("Product Detail") if st.raw_sheets else None
    plan = parent_purchase_plan(df, prod_detail, desired_woh=desired)
    plan = plan[plan["PacksToOrder"] > 0]
    import io
    with io.BytesIO() as buf:
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            plan.to_excel(writer, sheet_name="PurchasePlan", index=False)
        data = buf.getvalue()
    return (
        data,
        200,
        {
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": "attachment; filename=purchase_plan.xlsx",
        },
    )


@bp.get("/download/report.xlsx")
def download_report_xlsx():
    st = get_state()
    if st.sku_stats is None or st.holding_cost is None:
        return jsonify({"error": "No data processed yet"}), 404
    import io
    with io.BytesIO() as buf:
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            st.sku_stats.to_excel(writer, sheet_name="Inventory", index=False)
            st.holding_cost.to_excel(writer, sheet_name="HoldingCost", index=False)
        data = buf.getvalue()
    return (
        data,
        200,
        {
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": "attachment; filename=inventory_report.xlsx",
        },
    )


@bp.get("/download/top_suppliers.csv")
def download_top_suppliers_csv():
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty:
        return jsonify({"error": "No data processed yet"}), 404
    n = int(request.args.get("n", 20))
    top = top_n_by_metric(df, "Supplier", "OnHandCostTotal", n=n)
    csv = top.to_csv(index=False)
    return (
        csv,
        200,
        {
            "Content-Type": "text/csv",
            "Content-Disposition": "attachment; filename=top_suppliers.csv",
        },
    )


@bp.get("/turnover")
def turnover():
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty or not st.raw_sheets:
        return jsonify({"error": "No data processed yet"}), 404
    sales = st.raw_sheets.get("Sales History")
    if sales is None or sales.empty:
        return jsonify([])

    start = request.args.get("start")
    end = request.args.get("end")
    group = request.args.get("group", "product")  # product|supplier

    s = sales.copy()
    s["DateExpected"] = pd.to_datetime(s.get("DateExpected"), errors="coerce")
    if start:
        s = s[s["DateExpected"] >= pd.to_datetime(start)]
    if end:
        s = s[s["DateExpected"] <= pd.to_datetime(end)]

    s["ShippedLb"] = pd.to_numeric(s.get("ShippedLb", 0), errors="coerce").fillna(0)

    period_days = 1 + int((s["DateExpected"].max() - s["DateExpected"].min()).days) if not s["DateExpected"].dropna().empty else 28
    period_weeks = max(period_days / 7.0, 1.0)

    if group == "supplier":
        usage = s.groupby("Supplier", as_index=False).agg(Usage=("ShippedLb", "sum"))
        onhand = df.groupby("Supplier", as_index=False).agg(OnHand=("OnHandWeightTotal", "sum"))
        merged = onhand.merge(usage, on="Supplier", how="left").fillna({"Usage": 0})
        merged["PeriodTurnover"] = merged["Usage"] / merged["OnHand"].replace({0: pd.NA})
        merged["AnnualizedTurnover"] = (merged["Usage"] * (52.0 / period_weeks)) / merged["OnHand"].replace({0: pd.NA})
        merged = merged.fillna(0)
        return jsonify({
            "period_weeks": period_weeks,
            "group": "supplier",
            "items": merged.rename(columns={"Supplier": "key"}).to_dict(orient="records"),
        })
    else:
        # product-level using SKU_Desc
        usage = s.groupby("SKU", as_index=False).agg(Usage=("ShippedLb", "sum"))
        onhand = df.groupby(["SKU", "SKU_Desc"], as_index=False).agg(OnHand=("OnHandWeightTotal", "sum"))
        merged = onhand.merge(usage, on="SKU", how="left").fillna({"Usage": 0})
        merged["PeriodTurnover"] = merged["Usage"] / merged["OnHand"].replace({0: pd.NA})
        merged["AnnualizedTurnover"] = (merged["Usage"] * (52.0 / period_weeks)) / merged["OnHand"].replace({0: pd.NA})
        merged = merged.fillna(0)
        merged = merged.rename(columns={"SKU_Desc": "key"})
        return jsonify({
            "period_weeks": period_weeks,
            "group": "product",
            "items": merged[[
                "SKU", "key", "OnHand", "Usage", "PeriodTurnover", "AnnualizedTurnover"
            ]].to_dict(orient="records"),
        })


@bp.get("/supplier/trend/usage")
def supplier_trend_usage():
    st = get_state()
    if not st.raw_sheets:
        return jsonify([]), 404
    sales = st.raw_sheets.get("Sales History")
    if sales is None or sales.empty:
        return jsonify([])
    s = sales.copy()
    s["DateExpected"] = pd.to_datetime(s.get("DateExpected"), errors="coerce")
    s["ShippedLb"] = pd.to_numeric(s.get("ShippedLb", 0), errors="coerce").fillna(0)
    start = request.args.get("start")
    end = request.args.get("end")
    supplier = request.args.get("supplier")
    if start:
        s = s[s["DateExpected"] >= pd.to_datetime(start)]
    if end:
        s = s[s["DateExpected"] <= pd.to_datetime(end)]
    if supplier:
        s = s[s.get("Supplier").astype(str) == supplier]
    # group weekly
    grp = (
        s.groupby(pd.Grouper(key="DateExpected", freq="W"))["ShippedLb"].sum().reset_index()
        .rename(columns={"DateExpected": "Week", "ShippedLb": "Usage"})
    )
    # format dates for JS
    grp["Week"] = grp["Week"].dt.strftime("%Y-%m-%d")
    return jsonify(grp.to_dict(orient="records"))


@bp.get("/supplier/woh_distribution")
def supplier_woh_distribution():
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty:
        return jsonify([]), 404
    supplier = request.args.get("supplier")
    d = df.copy()
    if supplier:
        d = d[d.get("Supplier").astype(str) == supplier]
    out = d[["WeeksOnHand"]].dropna()
    return jsonify(out.to_dict(orient="records"))


@bp.get("/movers/top_usage")
def movers_top_usage():
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty:
        return jsonify([])
    q = float(request.args.get("q", 0.5))
    dfm = classify_movement(df, quantile=q)
    top = dfm.nlargest(10, "AvgWeeklyUsage")[
        ["SKU_Desc", "AvgWeeklyUsage", "Supplier", "Protein"]
    ]
    return jsonify(top.to_dict(orient="records"))


@bp.get("/movers/top_woh")
def movers_top_woh():
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty:
        return jsonify([])
    q = float(request.args.get("q", 0.5))
    dfm = classify_movement(df, quantile=q)
    top = dfm.nlargest(10, "WeeksOnHand")[
        ["SKU_Desc", "WeeksOnHand", "Supplier", "Protein"]
    ]
    return jsonify(top.to_dict(orient="records"))


@bp.get("/movers/heatmap")
def movers_heatmap():
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty:
        return jsonify([])
    q = float(request.args.get("q", 0.5))
    dfm = classify_movement(df, quantile=q)
    heat = dfm.groupby(["Supplier", "MovementClass"]).size().reset_index(name="Count")
    return jsonify(heat.to_dict(orient="records"))


@bp.get("/bins/summary")
def bins_summary():
    st = get_state()
    if not st.raw_sheets:
        return jsonify({"error": "No data processed yet"}), 404
    df = prepare_bins_data(st.raw_sheets)
    out = {
        "total_packs": int(df["PackId1"].nunique()) if "PackId1" in df.columns else 0,
        "total_weight": float(df.get("TotalWeight", 0).sum()) if "TotalWeight" in df.columns else 0,
        "products": int(df.get("ProductDesc", pd.Series(dtype=object)).nunique()) if "ProductDesc" in df.columns else 0,
        "bins": int(df.get("LastKnownBin", pd.Series(dtype=object)).nunique()) if "LastKnownBin" in df.columns else 0,
        "locations": int(df.get("ProductLocation", pd.Series(dtype=object)).nunique()) if "ProductLocation" in df.columns else 0,
    }
    return jsonify(out)


@bp.get("/bins/weight_by_protein")
def bins_weight_by_protein():
    st = get_state()
    if not st.raw_sheets:
        return jsonify([]), 404
    df = prepare_bins_data(st.raw_sheets)
    if "Protein" not in df.columns or "TotalWeight" not in df.columns:
        return jsonify([])
    agg = df.groupby("Protein", as_index=False)["TotalWeight"].sum().rename(columns={"TotalWeight": "TotalWeight"})
    return jsonify(agg.to_dict(orient="records"))


@bp.get("/bins/weight_by_location")
def bins_weight_by_location():
    st = get_state()
    if not st.raw_sheets:
        return jsonify([]), 404
    df = prepare_bins_data(st.raw_sheets)
    if "ProductLocation" not in df.columns or "TotalWeight" not in df.columns:
        return jsonify([])
    agg = df.groupby("ProductLocation", as_index=False)["TotalWeight"].sum()
    return jsonify(agg.to_dict(orient="records"))


@bp.get("/bins/download.csv")
def bins_download_csv():
    st = get_state()
    if not st.raw_sheets:
        return jsonify({"error": "No data processed yet"}), 404
    df = prepare_bins_data(st.raw_sheets)
    loc = request.args.get("location")
    prot = request.args.get("protein")
    if loc:
        df = df[df.get("ProductLocation").astype(str) == loc]
    if prot:
        df = df[df.get("Protein").astype(str) == prot]
    csv = df.to_csv(index=False)
    return (
        csv,
        200,
        {
            "Content-Type": "text/csv",
            "Content-Disposition": "attachment; filename=bins_detail.csv",
        },
    )


@bp.get("/moves/fz_to_ext")
def moves_fz_to_ext():
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty:
        return jsonify([])
    thr = float(request.args.get("threshold", 1.0))
    out = compute_fz_to_ext_moves(df, desired_ext_woh=thr)
    return jsonify(out.to_dict(orient="records"))


@bp.get("/moves/ext_to_fz")
def moves_ext_to_fz():
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty:
        return jsonify([])
    thr = float(request.args.get("threshold", 1.0))
    out = compute_ext_to_fz_moves(df, desired_fz_woh=thr)
    return jsonify(out.to_dict(orient="records"))


@bp.get("/moves/download.xlsx")
def moves_download_xlsx():
    st = get_state()
    df = st.sku_stats
    if df is None or df.empty:
        return jsonify({"error": "No data processed yet"}), 404
    mtype = request.args.get("type", "fz_to_ext")
    thr = float(request.args.get("threshold", 1.0))
    data = compute_fz_to_ext_moves(df, thr) if mtype == "fz_to_ext" else compute_ext_to_fz_moves(df, thr)
    import io
    with io.BytesIO() as buf:
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            data.to_excel(writer, sheet_name=mtype, index=False)
        payload = buf.getvalue()
    return (
        payload,
        200,
        {
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": f"attachment; filename={mtype}.xlsx",
        },
    )
