-- =====================================================================================
-- NorthPeak — Substitute Finder : batch gold view for Genie / dashboard / merge
-- =====================================================================================
-- Applies the SAME net-value ranking as the real-time finder (advisor.py), but computes
-- cosine similarity in Delta SQL over gold_product_embeddings — no Lakebase round-trip for
-- BI. Emits, for each open stockout (store × SKU), its top-3 markdown-prioritized
-- substitute products with the full score breakdown.
--
-- The real-time surface is Lakebase pgvector (see advisor.py); this table is the batch
-- companion that feeds Genie/dashboard and is ready to enrich the hollow `substitute`
-- branch of gold_recovery_recommendations (see MERGE_GUIDE.md).
--
-- `${catalog}` / `${schema}` substituted before execution. Runs on a Pro/Serverless
-- warehouse. Ranking params below mirror advisor.py defaults.
-- =====================================================================================

CREATE OR REPLACE TABLE ${catalog}.${schema}.gold_substitute_recommendations AS
WITH
-- ranking parameters (keep in sync with advisor.py)
params AS (
  SELECT 14        AS horizon_days,
         0.40      AS markdown_depth,
         1.0       AS w_capture,
         1.0       AS w_markdown
),
-- embeddings with a precomputed L2 norm (so cosine is one dot-product per pair)
emb AS (
  SELECT
    product_id, category, embedding,
    sqrt(aggregate(transform(embedding, x -> CAST(x AS DOUBLE) * CAST(x AS DOUBLE)),
                   CAST(0 AS DOUBLE), (acc, x) -> acc + x)) AS nrm
  FROM ${catalog}.${schema}.gold_product_embeddings
),
-- candidate substitutes: embedding + margins + markdown profile
cand AS (
  SELECT
    e.product_id, e.category, e.embedding, e.nrm,
    g.product_name, g.subcategory, g.unit_price, g.unit_margin,
    m.is_markdown_risk, m.position_status,
    m.overstock_on_hand_units, m.markdown_exposure_usd
  FROM emb e
  JOIN ${catalog}.${schema}.gold_product_embeddings   g USING (product_id)
  JOIN ${catalog}.${schema}.gold_product_markdown_profile m USING (product_id)
),
-- open shortfalls (the query side) with their demand signal
stockouts AS (
  SELECT store_id, store_name, region, climate_zone,
         product_id, product_name, avg_daily_velocity, lost_sales_exposure_usd
  FROM ${catalog}.${schema}.gold_store_sku_position
  WHERE position_status IN ('stockout', 'at_risk')
),
scored AS (
  SELECT
    s.store_id, s.store_name, s.region, s.climate_zone,
    s.product_id                        AS stockout_product_id,
    s.product_name                      AS stockout_product_name,
    ROUND(s.lost_sales_exposure_usd, 2) AS stockout_lost_sales_usd,
    c.product_id                        AS substitute_product_id,
    c.product_name                      AS substitute_product_name,
    c.subcategory                       AS substitute_subcategory,
    c.position_status                   AS substitute_status,
    c.is_markdown_risk,
    c.unit_price                        AS substitute_unit_price,
    c.unit_margin                       AS substitute_unit_margin,
    c.overstock_on_hand_units,
    ROUND(c.markdown_exposure_usd, 2)   AS substitute_markdown_exposure_usd,
    -- cosine similarity = dot(q, c) / (||q|| * ||c||)
    ROUND(
      aggregate(zip_with(q.embedding, c.embedding,
                         (x, y) -> CAST(x AS DOUBLE) * CAST(y AS DOUBLE)),
                CAST(0 AS DOUBLE), (acc, x) -> acc + x)
      / NULLIF(q.nrm * c.nrm, 0)
    , 4)                                AS similarity,
    p.horizon_days, p.markdown_depth, p.w_capture, p.w_markdown,
    -- economics
    (s.avg_daily_velocity * p.horizon_days)                                    AS demand_units
  FROM stockouts s
  JOIN emb  q ON q.product_id = s.product_id
  JOIN cand c ON c.category   = q.category           -- same category → relevant candidates
             AND c.product_id <> s.product_id
  CROSS JOIN params p
),
economics AS (
  SELECT *,
    LEAST(demand_units, CAST(overstock_on_hand_units AS DOUBLE))               AS expected_units,
    (LEAST(demand_units, CAST(overstock_on_hand_units AS DOUBLE))
        * substitute_unit_margin * similarity)                                 AS capture_margin,
    CASE WHEN is_markdown_risk
         THEN LEAST(demand_units, CAST(overstock_on_hand_units AS DOUBLE))
                * substitute_unit_price * markdown_depth
         ELSE 0 END                                                            AS markdown_saved
  FROM scored
),
ranked AS (
  SELECT *,
    ROUND(w_capture * capture_margin + w_markdown * markdown_saved, 2)         AS substitution_score,
    ROW_NUMBER() OVER (
      PARTITION BY store_id, stockout_product_id
      ORDER BY (w_capture * capture_margin + w_markdown * markdown_saved) DESC, similarity DESC
    )                                                                          AS substitute_rank
  FROM economics
  WHERE expected_units > 0        -- must be able to actually redirect units
)
SELECT
  store_id, store_name, region, climate_zone,
  stockout_product_id, stockout_product_name, stockout_lost_sales_usd,
  substitute_rank,
  substitute_product_id, substitute_product_name, substitute_subcategory,
  substitute_status, is_markdown_risk,
  similarity,
  ROUND(expected_units, 1)            AS expected_units,
  substitute_unit_margin,
  ROUND(capture_margin, 2)            AS capture_margin,
  ROUND(markdown_saved, 2)            AS markdown_saved,
  substitution_score
FROM ranked
WHERE substitute_rank <= 3            -- top-3 substitutes per shortfall
ORDER BY store_id, stockout_product_id, substitute_rank;

-- The recommended substitute per shortfall is substitute_rank = 1. Example:
-- SELECT * FROM ${catalog}.${schema}.gold_substitute_recommendations
--   WHERE store_id = 'STORE-0214' AND stockout_product_id = 'SKU-APP-04412' ORDER BY substitute_rank;
