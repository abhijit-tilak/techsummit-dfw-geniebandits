-- =====================================================================================
-- NorthPeak — Substitute Finder : product embeddings + margin/markdown metrics (batch)
-- =====================================================================================
-- Builds the two Delta tables the finder ranks over:
--   * gold_product_embeddings        — 1024-dim FM embedding per product + margin fields
--   * gold_product_markdown_profile  — per-product markdown/overstock rollup + demand signal
--
-- `${catalog}` / `${schema}` are substituted before execution (same convention as the
-- existing transformation/*.sql). The demo notebook (05_*.py) reads this file and
-- substitutes them; for a manual run, `envsubst < 01_product_embeddings.sql | ...`,
-- or wire it as a job task with base_parameters (see MERGE_GUIDE.md).
--
-- REQUIRES a Pro or Serverless SQL warehouse (ai_query is not available on Classic).
-- ai_query('databricks-gte-large-en', <text>) → ARRAY<FLOAT> of length 1024, and every
-- call is logged to system.serving usage tables (cost tracking). Only 200 rows, so this
-- is a one-time, cheap batch — never call ai_query per query at serving time.
-- =====================================================================================

-- ── 1. Product embeddings + margins ──────────────────────────────────────────────────
CREATE OR REPLACE TABLE ${catalog}.${schema}.gold_product_embeddings AS
SELECT
  p.product_id,
  p.product_name,
  p.category,
  p.subcategory,
  p.seasonality,
  p.unit_cost,
  p.unit_price,
  ROUND(p.unit_price - p.unit_cost, 2)                                        AS unit_margin,
  CASE WHEN p.unit_price > 0
       THEN ROUND((p.unit_price - p.unit_cost) / p.unit_price, 4)
       ELSE 0 END                                                             AS margin_pct,
  -- the text we embed: name + taxonomy + seasonality → captures "cold-weather outerwear"
  concat_ws(' | ', p.product_name, p.category, p.subcategory, p.seasonality)  AS product_text,
  -- FM embedding through the pay-per-token endpoint (tracked in system.serving usage)
  ai_query('databricks-gte-large-en',
           concat_ws(' | ', p.product_name, p.category, p.subcategory, p.seasonality)
  )                                                                           AS embedding
FROM ${catalog}.${schema}.raw_products p;

-- ── 2. Per-product markdown / overstock profile + demand signal ────────────────────────
-- Rolls the store×SKU position up to the product grain: is this product a markdown risk,
-- how many units of overstock sit out there, and how fast does it sell (for demand_units).
CREATE OR REPLACE TABLE ${catalog}.${schema}.gold_product_markdown_profile AS
SELECT
  product_id,
  MAX(CASE WHEN position_status = 'overstock' THEN 1 ELSE 0 END) = 1          AS is_markdown_risk,
  -- representative status: overstock dominates (it's the substitution opportunity)
  CASE
    WHEN SUM(CASE WHEN position_status = 'overstock' THEN 1 ELSE 0 END) > 0 THEN 'overstock'
    WHEN SUM(CASE WHEN position_status = 'stockout'  THEN 1 ELSE 0 END) > 0 THEN 'stockout'
    WHEN SUM(CASE WHEN position_status = 'at_risk'   THEN 1 ELSE 0 END) > 0 THEN 'at_risk'
    ELSE 'healthy'
  END                                                                        AS position_status,
  CAST(SUM(CASE WHEN position_status = 'overstock' THEN on_hand_units ELSE 0 END) AS BIGINT)
                                                                             AS overstock_on_hand_units,
  SUM(CASE WHEN position_status = 'overstock' THEN 1 ELSE 0 END)             AS overstock_store_count,
  ROUND(SUM(markdown_exposure_usd), 2)                                       AS markdown_exposure_usd,
  ROUND(AVG(avg_daily_velocity), 3)                                          AS avg_daily_velocity
FROM ${catalog}.${schema}.gold_store_sku_position
GROUP BY product_id;

-- ── quick sanity checks (optional) ─────────────────────────────────────────────────────
-- SELECT COUNT(*) AS n, size(ANY_VALUE(embedding)) AS dim FROM ${catalog}.${schema}.gold_product_embeddings;
-- SELECT COUNT_IF(is_markdown_risk) AS markdown_products, COUNT(*) AS total
--   FROM ${catalog}.${schema}.gold_product_markdown_profile;
