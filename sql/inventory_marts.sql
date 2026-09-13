-- Inventory marts in SQL.
--
-- The same calculations the Python engine performs, expressed against the
-- exported CSVs so they can be run in a warehouse instead of an app. This is
-- not a translation exercise: `tests/test_sql_matches_python.py` runs this file
-- in DuckDB and asserts the answers agree with `invapp.analytics` to a
-- tolerance, so the two cannot drift apart quietly.
--
-- Where the two genuinely cannot agree, the SQL says so rather than faking it:
-- forecasting (seven competing methods on a rolling-origin backtest) is not
-- expressible in a reasonable amount of SQL, so the planning demand here is the
-- trailing 13-week mean, which is what the Python engine falls back to when no
-- forecast exists. That difference is asserted in the test as a *known* gap
-- rather than left as a surprise.
--
-- Run:
--   duckdb -c ".read sql/inventory_marts.sql"          (from the repo root)
--   python -m seed.generate_workbook --csv-dir data    (to produce the inputs)

-- ---------------------------------------------------------------- sources
-- Where the CSVs live. Overridable so the parity test can point the same file
-- at a freshly generated copy in a temporary directory rather than at whatever
-- happens to be committed - a test that reads the committed data proves the
-- committed data is self-consistent, not that the SQL is right.
--
--   duckdb -c "SET VARIABLE data_dir='data'; .read sql/inventory_marts.sql"
SET VARIABLE data_dir = COALESCE(getvariable('data_dir'), 'data');

CREATE OR REPLACE VIEW dim_item AS SELECT * FROM read_csv_auto(getvariable('data_dir') || '/dim_item.csv', header = true);
CREATE OR REPLACE VIEW dim_supplier AS SELECT * FROM read_csv_auto(getvariable('data_dir') || '/dim_supplier.csv', header = true);
CREATE OR REPLACE VIEW dim_node AS SELECT * FROM read_csv_auto(getvariable('data_dir') || '/dim_node.csv', header = true);
CREATE OR REPLACE VIEW fact_demand AS SELECT * FROM read_csv_auto(getvariable('data_dir') || '/fact_demand.csv', header = true);
CREATE OR REPLACE VIEW fact_inventory AS SELECT * FROM read_csv_auto(getvariable('data_dir') || '/fact_inventory.csv', header = true);
CREATE OR REPLACE VIEW fact_purchase_order AS SELECT * FROM read_csv_auto(getvariable('data_dir') || '/fact_purchase_order.csv', header = true);
CREATE OR REPLACE VIEW fact_cycle_count AS SELECT * FROM read_csv_auto(getvariable('data_dir') || '/fact_cycle_count.csv', header = true);
CREATE OR REPLACE VIEW fact_adjustment AS SELECT * FROM read_csv_auto(getvariable('data_dir') || '/fact_adjustment.csv', header = true);
CREATE OR REPLACE VIEW ref_planning_parameter AS SELECT * FROM read_csv_auto(getvariable('data_dir') || '/ref_planning_parameter.csv', header = true);

-- Parameters as one wide row, so every downstream query can cross join it
-- rather than repeating a literal that then only gets changed in four of five
-- places.
CREATE OR REPLACE VIEW params AS
SELECT
    MAX(CASE WHEN Parameter = 'ReviewPeriodDays'        THEN Value END) AS review_period_days,
    MAX(CASE WHEN Parameter = 'CarryingCostRate'        THEN Value END) AS carrying_cost_rate,
    MAX(CASE WHEN Parameter = 'ServiceLevelA'           THEN Value END) AS service_level_a,
    MAX(CASE WHEN Parameter = 'ServiceLevelB'           THEN Value END) AS service_level_b,
    MAX(CASE WHEN Parameter = 'ServiceLevelC'           THEN Value END) AS service_level_c,
    MAX(CASE WHEN Parameter = 'SlowMoverDays'           THEN Value END) AS slow_mover_days,
    MAX(CASE WHEN Parameter = 'ObsoleteDays'            THEN Value END) AS obsolete_days,
    MAX(CASE WHEN Parameter = 'OnTimeGraceDays'         THEN Value END) AS on_time_grace_days,
    MAX(CASE WHEN Parameter = 'CountToleranceUnits'     THEN Value END) AS count_tolerance_units,
    MAX(CASE WHEN Parameter = 'StockoutPenaltyPerUnit'  THEN Value END) AS stockout_penalty
FROM ref_planning_parameter;

CREATE OR REPLACE VIEW as_of AS
SELECT MAX(CAST(SnapshotDate AS DATE)) AS snapshot_date FROM fact_inventory;

-- --------------------------------------------------------- demand history
-- The full week calendar, so a SKU-node pair that shipped nothing in a week is
-- averaged over that week rather than skipping it. A shipment export has no row
-- for a week with no movement, and averaging over rows-found instead of
-- weeks-elapsed reports an item that sold once in thirteen weeks as selling
-- every week - which then flows into safety stock and orders more of it.
CREATE OR REPLACE VIEW week_calendar AS
SELECT DISTINCT CAST(WeekEnding AS DATE) AS week_ending FROM fact_demand;

CREATE OR REPLACE VIEW demand_grid AS
SELECT p.SKU, p.NodeID, w.week_ending
FROM (SELECT DISTINCT SKU, NodeID FROM fact_demand) p
CROSS JOIN week_calendar w;

CREATE OR REPLACE VIEW demand_dense AS
SELECT
    g.SKU,
    g.NodeID,
    g.week_ending,
    COALESCE(d.UnitsShipped, 0)   AS units_shipped,
    COALESCE(d.UnitsRequested, 0) AS units_requested
FROM demand_grid g
LEFT JOIN fact_demand d
       ON d.SKU = g.SKU AND d.NodeID = g.NodeID
      AND CAST(d.WeekEnding AS DATE) = g.week_ending;

-- Trailing 13 weeks, the planning window.
CREATE OR REPLACE VIEW demand_window AS
SELECT d.*
FROM demand_dense d
WHERE d.week_ending > (SELECT MAX(week_ending) - INTERVAL 13 WEEK FROM week_calendar);

CREATE OR REPLACE TABLE mart_demand_stats AS
WITH stats AS (
    SELECT
        SKU,
        NodeID,
        AVG(units_shipped)                    AS weekly_demand,
        -- Sample standard deviation, ddof = 1: thirteen weeks is a sample of
        -- the process, not the population. `stddev_samp` is the default in
        -- DuckDB but naming it here keeps it from being changed by accident -
        -- with ddof = 0 every safety stock in the network is ~4% light.
        COALESCE(stddev_samp(units_shipped), 0) AS weekly_stddev,
        SUM(units_shipped)                    AS window_units
    FROM demand_window
    GROUP BY SKU, NodeID
),
recency AS (
    SELECT
        SKU,
        NodeID,
        DATE_DIFF('day', MAX(week_ending), (SELECT MAX(week_ending) FROM week_calendar))
            AS days_since_last_ship
    FROM demand_dense
    WHERE units_shipped > 0
    GROUP BY SKU, NodeID
)
SELECT
    s.SKU,
    s.NodeID,
    s.weekly_demand,
    s.weekly_stddev,
    -- Weekly to daily: the mean divides by seven and the standard deviation by
    -- its square root, assuming days within a week are independent. They are
    -- not (Saturdays outsell Tuesdays), so this slightly understates daily
    -- variance - stated rather than hidden, and the same assumption the Python
    -- engine makes so the two agree.
    s.weekly_demand / 7.0                        AS daily_demand,
    s.weekly_stddev / SQRT(7.0)                  AS daily_stddev,
    s.weekly_demand * 52.0                       AS annual_demand,
    CASE WHEN s.weekly_demand > 0
         THEN s.weekly_stddev / s.weekly_demand END AS coefficient_of_variation,
    COALESCE(r.days_since_last_ship, 9999)       AS days_since_last_ship
FROM stats s
LEFT JOIN recency r ON r.SKU = s.SKU AND r.NodeID = s.NodeID;

-- ------------------------------------------------------------ segmentation
-- ABC on annual *consumption* value, not on inventory value: an item with
-- $400k of stock and no demand is a C item sitting on an A item's worth of
-- cash, and classifying on what is on the shelf would hide exactly that.
CREATE OR REPLACE TABLE mart_sku_segment AS
WITH sku_demand AS (
    SELECT SKU, SUM(annual_demand) AS annual_demand,
           SUM(weekly_demand) AS weekly_demand,
           MIN(days_since_last_ship) AS days_since_last_ship
    FROM mart_demand_stats
    GROUP BY SKU
),
sku_variability AS (
    -- Variability is measured on the SKU's own national series, not averaged
    -- across its nodes: a SKU split three ways looks three times as erratic
    -- as it is, and every C item would grade Z.
    SELECT SKU,
           AVG(units) AS weekly_demand_national,
           COALESCE(stddev_samp(units), 0) AS weekly_stddev_national
    FROM (
        SELECT SKU, week_ending, SUM(units_shipped) AS units
        FROM demand_window GROUP BY SKU, week_ending
    )
    GROUP BY SKU
),
valued AS (
    SELECT
        d.SKU,
        i.ItemDescription,
        i.Department,
        i.Category,
        i.SupplierID,
        i.UnitCost,
        i.UnitPrice,
        d.annual_demand,
        d.weekly_demand,
        d.days_since_last_ship,
        d.annual_demand * i.UnitCost AS annual_consumption_value,
        CASE WHEN v.weekly_demand_national > 0
             THEN v.weekly_stddev_national / v.weekly_demand_national END AS cov
    FROM sku_demand d
    JOIN dim_item i USING (SKU)
    LEFT JOIN sku_variability v USING (SKU)
),
ranked AS (
    SELECT
        *,
        SUM(annual_consumption_value) OVER (
            -- SKU breaks the tie so two items with the same consumption value
            -- always fall on the same side of the 80% line. Without it the
            -- boundary rows swap between runs and a committed mart differs
            -- from CI for no reason anyone can see.
            ORDER BY annual_consumption_value DESC, SKU
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS cumulative_value,
        SUM(annual_consumption_value) OVER () AS total_value
    FROM valued
)
SELECT
    SKU, ItemDescription, Department, Category, SupplierID, UnitCost, UnitPrice,
    annual_demand, weekly_demand, annual_consumption_value, cov,
    days_since_last_ship,
    cumulative_value / NULLIF(total_value, 0) AS cumulative_share,
    CASE
        WHEN cumulative_value / NULLIF(total_value, 0) <= 0.80 THEN 'A'
        WHEN cumulative_value / NULLIF(total_value, 0) <= 0.95 THEN 'B'
        ELSE 'C'
    END AS abc_class,
    CASE
        WHEN cov IS NULL THEN 'Z'
        WHEN cov <= 0.5  THEN 'X'
        WHEN cov <= 1.0  THEN 'Y'
        ELSE 'Z'
    END AS xyz_class
FROM ranked;

-- ------------------------------------------------------- supplier lead time
-- Measured from receipts, not from the contract. Only closed lines count: an
-- order still in transit has no lead time yet, and counting it as zero days so
-- far reports the worst supplier as the best in the week after a big order.
CREATE OR REPLACE TABLE mart_supplier_leadtime AS
SELECT
    po.SupplierID,
    COUNT(*)                                                   AS receipt_count,
    AVG(DATE_DIFF('day', CAST(po.OrderDate AS DATE), CAST(po.ReceivedDate AS DATE)))
        AS lead_time_days_actual,
    COALESCE(stddev_samp(
        DATE_DIFF('day', CAST(po.OrderDate AS DATE), CAST(po.ReceivedDate AS DATE))
    ), 0)                                                      AS lead_time_days_stddev,
    MAX(s.LeadTimeDays)                                        AS lead_time_days_contract,
    MAX(s.OrderCostUSD)                                        AS order_cost_usd
FROM fact_purchase_order po
JOIN dim_supplier s USING (SupplierID)
WHERE po.ReceivedDate IS NOT NULL
GROUP BY po.SupplierID;

CREATE OR REPLACE TABLE mart_supplier_scorecard AS
WITH closed AS (
    SELECT
        po.SupplierID,
        po.QtyOrdered,
        po.QtyReceived,
        po.QtyRejected,
        po.QtyReceived * po.UnitCost AS spend_usd,
        DATE_DIFF('day', CAST(po.PromisedDate AS DATE), CAST(po.ReceivedDate AS DATE))
            AS late_days
    FROM fact_purchase_order po
    WHERE po.ReceivedDate IS NOT NULL
)
SELECT
    c.SupplierID,
    s.SupplierName,
    s.Country,
    COUNT(*)                                                        AS po_lines,
    SUM(c.spend_usd)                                                AS spend_usd,
    AVG(CASE WHEN c.late_days <= (SELECT on_time_grace_days FROM params)
             THEN 1.0 ELSE 0.0 END)                                 AS on_time_pct,
    SUM(c.QtyReceived) / NULLIF(SUM(c.QtyOrdered), 0)               AS fill_rate_pct,
    SUM(c.QtyRejected) / NULLIF(SUM(c.QtyReceived), 0)              AS defect_rate_pct,
    -- A perfect order is on time AND complete AND clean. Measured separately
    -- each of the three looks fine at 95%; multiplied, the same operation
    -- delivers a perfect order five times in six.
    AVG(CASE WHEN c.late_days <= (SELECT on_time_grace_days FROM params)
              AND c.QtyReceived >= c.QtyOrdered
              AND c.QtyRejected <= 0
             THEN 1.0 ELSE 0.0 END)                                 AS perfect_order_pct,
    lt.lead_time_days_actual,
    lt.lead_time_days_stddev,
    lt.lead_time_days_contract,
    lt.lead_time_days_actual - lt.lead_time_days_contract           AS lead_time_gap_days
FROM closed c
JOIN dim_supplier s ON s.SupplierID = c.SupplierID
LEFT JOIN mart_supplier_leadtime lt ON lt.SupplierID = c.SupplierID
GROUP BY c.SupplierID, s.SupplierName, s.Country,
         lt.lead_time_days_actual, lt.lead_time_days_stddev,
         lt.lead_time_days_contract;

-- ------------------------------------------------------------ replenishment
CREATE OR REPLACE VIEW in_transit AS
SELECT SKU, NodeID, SUM(QtyOrdered) AS in_transit_units
FROM fact_purchase_order
WHERE ReceivedDate IS NULL
GROUP BY SKU, NodeID;

CREATE OR REPLACE TABLE mart_replenishment AS
WITH base AS (
    SELECT
        st.SKU,
        st.NodeID,
        seg.ItemDescription,
        seg.Department,
        seg.abc_class,
        seg.xyz_class,
        seg.UnitCost,
        seg.UnitPrice,
        i.CasePack,
        st.daily_demand,
        st.daily_stddev,
        st.annual_demand,
        COALESCE(inv.OnHandUnits, 0)     AS on_hand_units,
        COALESCE(inv.ReservedUnits, 0)   AS reserved_units,
        COALESCE(tr.in_transit_units, 0) AS in_transit_units,
        COALESCE(lt.lead_time_days_actual, 14)  AS lead_time_days,
        COALESCE(lt.lead_time_days_stddev, 3)   AS lead_time_stddev,
        CASE seg.abc_class
            WHEN 'A' THEN (SELECT service_level_a FROM params)
            WHEN 'B' THEN (SELECT service_level_b FROM params)
            ELSE          (SELECT service_level_c FROM params)
        END AS service_level
    FROM mart_demand_stats st
    JOIN mart_sku_segment seg USING (SKU)
    JOIN dim_item i USING (SKU)
    LEFT JOIN fact_inventory inv ON inv.SKU = st.SKU AND inv.NodeID = st.NodeID
    LEFT JOIN in_transit tr      ON tr.SKU = st.SKU  AND tr.NodeID = st.NodeID
    LEFT JOIN mart_supplier_leadtime lt ON lt.SupplierID = seg.SupplierID
),
sized AS (
    SELECT
        *,
        -- The z for the target cycle service level. DuckDB has no inverse
        -- normal CDF, so the three values the policy actually uses are named
        -- rather than approximated: an interpolation would disagree with
        -- Python's `NormalDist.inv_cdf` in the fourth decimal and the parity
        -- test would fail on the approximation rather than on the logic.
        CASE
            WHEN service_level >= 0.98 THEN 2.053748910631823
            WHEN service_level >= 0.95 THEN 1.6448536269514729
            ELSE                            1.2815515655446004
        END AS safety_factor_z
    FROM base
),
computed AS (
    SELECT
        *,
        -- Two-term safety stock: demand variability over the lead time, plus
        -- average demand times lead-time variability. Dropping the second term
        -- assumes the supplier is never late, and on the long-lead suppliers
        -- here that term is the larger of the two.
        safety_factor_z * SQRT(
            lead_time_days * POWER(daily_stddev, 2)
            + POWER(daily_demand, 2) * POWER(lead_time_stddev, 2)
        ) AS safety_stock_units,
        on_hand_units + in_transit_units - reserved_units AS inventory_position
    FROM sized
)
SELECT
    *,
    daily_demand * lead_time_days + safety_stock_units AS reorder_point_units,
    CASE WHEN daily_demand > 0
         THEN (on_hand_units + in_transit_units - reserved_units) / daily_demand
    END AS days_of_cover,
    CASE
        WHEN inventory_position <= 0 AND daily_demand > 0 THEN 'Stocked out'
        WHEN inventory_position < safety_stock_units      THEN 'Below safety stock'
        WHEN inventory_position <= daily_demand * lead_time_days + safety_stock_units
             THEN 'At reorder point'
        ELSE 'Healthy'
    END AS urgency
FROM computed;

-- ---------------------------------------------------------------- accuracy
CREATE OR REPLACE TABLE mart_count_accuracy AS
SELECT
    c.NodeID,
    COUNT(*) AS records_counted,
    SUM(CASE WHEN ABS(c.CountedQty - c.SystemQty)
                  <= (SELECT count_tolerance_units FROM params)
             THEN 1 ELSE 0 END)                                 AS accurate_records,
    AVG(CASE WHEN ABS(c.CountedQty - c.SystemQty)
                  <= (SELECT count_tolerance_units FROM params)
             THEN 1.0 ELSE 0.0 END)                             AS record_accuracy,
    SUM(ABS(c.CountedQty - c.SystemQty) * c.UnitCost)           AS abs_variance_value_usd,
    SUM((c.CountedQty - c.SystemQty) * c.UnitCost)              AS net_variance_value_usd,
    SUM(c.SystemQty * c.UnitCost)                               AS system_value_usd,
    1 - SUM(ABS(c.CountedQty - c.SystemQty) * c.UnitCost)
        / NULLIF(SUM(c.SystemQty * c.UnitCost), 0)              AS value_accuracy
FROM fact_cycle_count c
GROUP BY c.NodeID;

-- ------------------------------------------------------------ working capital
CREATE OR REPLACE TABLE mart_working_capital AS
WITH inv AS (
    SELECT SUM(f.OnHandUnits * i.UnitCost) AS inventory_value_usd
    FROM fact_inventory f JOIN dim_item i USING (SKU)
),
cogs AS (
    SELECT
        SUM(d.UnitsShipped * i.UnitCost) AS cogs_usd,
        SUM(d.UnitsShipped * i.UnitPrice) AS revenue_usd,
        COUNT(DISTINCT d.WeekEnding) AS weeks
    FROM fact_demand d JOIN dim_item i USING (SKU)
)
SELECT
    inv.inventory_value_usd,
    cogs.cogs_usd * (52.0 / cogs.weeks)                 AS annual_cogs_usd,
    cogs.revenue_usd * (52.0 / cogs.weeks)              AS annual_revenue_usd,
    (cogs.cogs_usd * (52.0 / cogs.weeks)) / NULLIF(inv.inventory_value_usd, 0)
                                                        AS inventory_turns,
    -- Days inventory outstanding on cost of goods sold, not on revenue. Mixing
    -- the two bases understates it by the gross margin - about a third here,
    -- which turns 86 days into 60 and makes the number someone else's.
    inv.inventory_value_usd / NULLIF(cogs.cogs_usd * (52.0 / cogs.weeks), 0) * 365.0
                                                        AS days_inventory_outstanding,
    inv.inventory_value_usd * (SELECT carrying_cost_rate FROM params)
                                                        AS carrying_cost_usd
FROM inv CROSS JOIN cogs;

-- ------------------------------------------------------------------- output
SELECT 'mart_demand_stats' AS mart, COUNT(*) AS rows FROM mart_demand_stats
UNION ALL SELECT 'mart_sku_segment', COUNT(*) FROM mart_sku_segment
UNION ALL SELECT 'mart_supplier_leadtime', COUNT(*) FROM mart_supplier_leadtime
UNION ALL SELECT 'mart_supplier_scorecard', COUNT(*) FROM mart_supplier_scorecard
UNION ALL SELECT 'mart_replenishment', COUNT(*) FROM mart_replenishment
UNION ALL SELECT 'mart_count_accuracy', COUNT(*) FROM mart_count_accuracy
UNION ALL SELECT 'mart_working_capital', COUNT(*) FROM mart_working_capital
ORDER BY mart;
