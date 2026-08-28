-- =====================================================================================
-- NorthPeak — Substitute Finder : Lakebase (Postgres + pgvector) schema
-- =====================================================================================
-- Run against the `northpeak` database on the isolated branch (03_lakebase_setup.sh
-- creates the branch + DB and pipes this file in). Defines the LakeBase search surface:
--   * product_catalog    — one row per product: margins, markdown metrics, embedding
--   * substitute_actions — manager-approval write-back (the "action today" log)
-- 04_load_lakebase.py fills product_catalog from Delta.
-- =====================================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ── product catalog: the vector search surface ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS product_catalog (
  product_id               TEXT PRIMARY KEY,
  product_name             TEXT NOT NULL,
  category                 TEXT,
  subcategory              TEXT,
  seasonality              TEXT,
  unit_cost                DOUBLE PRECISION,
  unit_price               DOUBLE PRECISION,
  unit_margin              DOUBLE PRECISION,   -- unit_price - unit_cost  (replacement margin)
  margin_pct               DOUBLE PRECISION,
  is_markdown_risk         BOOLEAN DEFAULT FALSE,
  position_status          TEXT,
  overstock_on_hand_units  BIGINT DEFAULT 0,   -- units we could redirect from overstock
  overstock_store_count    INT DEFAULT 0,
  markdown_exposure_usd    DOUBLE PRECISION DEFAULT 0,
  avg_daily_velocity       DOUBLE PRECISION DEFAULT 0,
  embedding                vector(1024) NOT NULL,   -- databricks-gte-large-en
  updated_at               TIMESTAMPTZ DEFAULT now()
);

-- HNSW cosine index → fast KNN (`embedding <=> :q` is cosine distance; similarity = 1 - dist)
CREATE INDEX IF NOT EXISTS product_catalog_embedding_hnsw
  ON product_catalog USING hnsw (embedding vector_cosine_ops);

-- Helps the "markdown items higher priority" pass filter/sort cheaply
CREATE INDEX IF NOT EXISTS product_catalog_markdown_idx
  ON product_catalog (is_markdown_risk, category);

-- ── manager approval write-back: "for a manager to approve → action today" ─────────────
CREATE TABLE IF NOT EXISTS substitute_actions (
  action_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  store_id              TEXT NOT NULL,
  stockout_product_id   TEXT NOT NULL,
  chosen_move           TEXT NOT NULL,          -- 'transfer' | 'expedite' | 'substitute'
  substitute_product_id TEXT,                   -- set when chosen_move = 'substitute'
  substitution_score    DOUBLE PRECISION,
  similarity            DOUBLE PRECISION,
  capture_margin        DOUBLE PRECISION,
  markdown_saved        DOUBLE PRECISION,
  rationale             TEXT,                   -- the governed-FM one-liner
  approved_by           TEXT NOT NULL,
  approved_at           TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS substitute_actions_store_idx
  ON substitute_actions (store_id, stockout_product_id);
