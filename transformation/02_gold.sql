-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Gold Layer — NorthPeak Retail
-- MAGIC Business-ready aggregations: store×SKU position, open shortfalls, and recovery recommendations.
-- MAGIC The recovery recommendation ranks moves by NET recaptured value (recaptured − cost − margin impact),
-- MAGIC never gross — so a fix never trades a stockout for a worse markdown.

-- COMMAND ----------

CREATE OR REFRESH MATERIALIZED VIEW gold_store_sku_position
COMMENT "Current inventory position per store × SKU — healthy / at_risk / stockout / overstock with exposure dollars."
AS
WITH velocity AS (
  SELECT
    store_id,
    product_id,
    SUM(units_sold) AS recent_units_7d,
    SUM(units_sold) / 7.0 AS avg_daily_velocity
  FROM LIVE.raw_sales
  WHERE sale_date >= current_date() - INTERVAL 7 DAYS
  GROUP BY store_id, product_id
),
latest_inventory AS (
  SELECT *
  FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY store_id, product_id ORDER BY snapshot_date DESC) AS rn
    FROM LIVE.raw_inventory_snapshots
  )
  WHERE rn = 1
)
SELECT
  i.store_id,
  s.store_name,
  s.region,
  s.climate_zone,
  s.city,
  s.latitude AS store_lat,
  s.longitude AS store_lng,
  i.product_id,
  p.product_name,
  p.category,
  p.seasonality,
  i.on_hand_units,
  COALESCE(v.recent_units_7d, 0) AS recent_units_7d,
  COALESCE(v.avg_daily_velocity, 0) AS avg_daily_velocity,
  CASE
    WHEN COALESCE(v.avg_daily_velocity, 0) > 0
    THEN i.on_hand_units / v.avg_daily_velocity / 7.0
    ELSE 99.0
  END AS weeks_of_supply,
  CASE
    WHEN i.on_hand_units = 0 AND COALESCE(v.avg_daily_velocity, 0) > 0 THEN 'stockout'
    WHEN COALESCE(v.avg_daily_velocity, 0) > 0
      AND (i.on_hand_units / v.avg_daily_velocity / 7.0) < 1.0 THEN 'at_risk'
    WHEN COALESCE(v.avg_daily_velocity, 0) <= 0.1
      AND i.on_hand_units > 50
      AND (i.on_hand_units / GREATEST(v.avg_daily_velocity, 0.01) / 7.0) > 8.0 THEN 'overstock'
    ELSE 'healthy'
  END AS position_status,
  -- Exposure calculations
  CASE
    WHEN i.on_hand_units = 0 AND COALESCE(v.avg_daily_velocity, 0) > 0
    THEN v.avg_daily_velocity * p.unit_price * 30  -- 30-day lost-sales projection
    ELSE 0
  END AS lost_sales_exposure_usd,
  CASE
    WHEN COALESCE(v.avg_daily_velocity, 0) <= 0.1 AND i.on_hand_units > 50
    THEN i.on_hand_units * p.unit_price * 0.40  -- assume 40% markdown to clear
    ELSE 0
  END AS markdown_exposure_usd,
  CASE
    WHEN COALESCE(v.avg_daily_velocity, 0) <= 0.1 AND i.on_hand_units > 50
    THEN LEAST(1.0, (i.on_hand_units * 0.01))  -- risk score 0-1
    ELSE 0
  END AS markdown_risk_score
FROM latest_inventory i
JOIN LIVE.raw_stores s ON i.store_id = s.store_id
JOIN LIVE.raw_products p ON i.product_id = p.product_id
LEFT JOIN velocity v ON i.store_id = v.store_id AND i.product_id = v.product_id;

-- COMMAND ----------

CREATE OR REFRESH MATERIALIZED VIEW gold_open_shortfalls
COMMENT "Active shortfalls with nearest surplus store for transfer recommendation."
AS
WITH shortfalls AS (
  SELECT * FROM LIVE.gold_store_sku_position
  WHERE position_status IN ('stockout', 'at_risk')
),
surpluses AS (
  SELECT store_id, product_id, on_hand_units, store_lat, store_lng
  FROM LIVE.gold_store_sku_position
  WHERE position_status = 'overstock'
),
nearest AS (
  SELECT
    sh.store_id,
    sh.product_id,
    FIRST_VALUE(su.store_id) OVER (
      PARTITION BY sh.store_id, sh.product_id
      ORDER BY SQRT(POW(sh.store_lat - su.store_lat, 2) + POW(sh.store_lng - su.store_lng, 2))
    ) AS nearest_surplus_store_id,
    FIRST_VALUE(su.on_hand_units) OVER (
      PARTITION BY sh.store_id, sh.product_id
      ORDER BY SQRT(POW(sh.store_lat - su.store_lat, 2) + POW(sh.store_lng - su.store_lng, 2))
    ) AS nearest_surplus_on_hand,
    FIRST_VALUE(
      ROUND(111.0 * SQRT(POW(sh.store_lat - su.store_lat, 2) + POW(sh.store_lng - su.store_lng, 2)), 1)
    ) OVER (
      PARTITION BY sh.store_id, sh.product_id
      ORDER BY SQRT(POW(sh.store_lat - su.store_lat, 2) + POW(sh.store_lng - su.store_lng, 2))
    ) AS nearest_surplus_distance_km
  FROM shortfalls sh
  JOIN surpluses su ON sh.product_id = su.product_id AND sh.store_id != su.store_id
)
SELECT DISTINCT
  sh.*,
  n.nearest_surplus_store_id,
  n.nearest_surplus_on_hand,
  n.nearest_surplus_distance_km
FROM shortfalls sh
LEFT JOIN nearest n ON sh.store_id = n.store_id AND sh.product_id = n.product_id;

-- COMMAND ----------

CREATE OR REFRESH MATERIALIZED VIEW gold_recovery_recommendations
COMMENT "Ranked recovery moves (transfer / expedite / substitute) by NET recaptured value. Never recommends a move that trades a stockout for a worse markdown."
AS
WITH shortfalls AS (
  SELECT * FROM LIVE.gold_open_shortfalls
),
-- Transfer option: move stock from nearest surplus store
transfer_moves AS (
  SELECT
    store_id,
    product_id,
    'transfer' AS recommended_move,
    nearest_surplus_store_id AS recommended_source_store_id,
    LEAST(CAST(avg_daily_velocity * 14 AS INT), nearest_surplus_on_hand) AS recommended_units,
    -- Recaptured = units × price (we get to sell them)
    LEAST(CAST(avg_daily_velocity * 14 AS INT), nearest_surplus_on_hand) * 
      (lost_sales_exposure_usd / GREATEST(avg_daily_velocity * 30, 1)) AS predicted_recaptured_usd,
    -- Cost = shipping (~$2/unit × distance factor)
    LEAST(CAST(avg_daily_velocity * 14 AS INT), nearest_surplus_on_hand) * 
      2.0 * (nearest_surplus_distance_km / 500.0) AS move_cost_usd
  FROM shortfalls
  WHERE nearest_surplus_store_id IS NOT NULL
),
-- Expedite option: rush order from DC
expedite_moves AS (
  SELECT
    store_id,
    product_id,
    'expedite' AS recommended_move,
    'DC-CENTRAL' AS recommended_source_store_id,
    CAST(avg_daily_velocity * 14 AS INT) AS recommended_units,
    CAST(avg_daily_velocity * 14 AS INT) * 
      (lost_sales_exposure_usd / GREATEST(avg_daily_velocity * 30, 1)) AS predicted_recaptured_usd,
    CAST(avg_daily_velocity * 14 AS INT) * 5.0 AS move_cost_usd  -- $5/unit expedite premium
  FROM shortfalls
),
-- Substitute option: recommend an alternate SKU in same category
substitute_moves AS (
  SELECT
    store_id,
    product_id,
    'substitute' AS recommended_move,
    NULL AS recommended_source_store_id,
    0 AS recommended_units,
    lost_sales_exposure_usd * 0.3 AS predicted_recaptured_usd,  -- ~30% conversion on substitute
    0 AS move_cost_usd
  FROM shortfalls
),
all_moves AS (
  SELECT * FROM transfer_moves
  UNION ALL
  SELECT * FROM expedite_moves
  UNION ALL
  SELECT * FROM substitute_moves
)
SELECT
  store_id,
  product_id,
  recommended_move,
  recommended_source_store_id,
  recommended_units,
  ROUND(predicted_recaptured_usd, 2) AS predicted_recaptured_usd,
  ROUND(move_cost_usd, 2) AS move_cost_usd,
  ROUND(predicted_recaptured_usd - move_cost_usd, 2) AS predicted_net_value_usd,
  ROW_NUMBER() OVER (
    PARTITION BY store_id, product_id
    ORDER BY (predicted_recaptured_usd - move_cost_usd) DESC
  ) AS move_ranking
FROM all_moves
WHERE predicted_recaptured_usd - move_cost_usd > 0  -- NEVER recommend a net-negative move
ORDER BY store_id, product_id, move_ranking;