-- =====================================================================================
-- Migration 002 — Lakebase hybrid search over operational product text
-- =====================================================================================
-- Applied to geniebandits-dev by 04_load_embeddings.py (which then fills embeddings).
--
-- Enables BOTH retrieval modes over the SAME operational text column so a query can
-- fuse them (Reciprocal Rank Fusion in 07_hybrid_search.py):
--   * FULL-TEXT  : a generated tsvector + GIN index (lexical match)
--   * VECTOR     : a pgvector(1024) embedding + HNSW cosine index (semantic match)
--
-- The embedding model is databricks-gte-large-en (1024 dims), generated in Delta via
-- ai_query and loaded into Postgres — the same governed FM path the app uses.
-- =====================================================================================

CREATE EXTENSION IF NOT EXISTS vector;
-- include public so the pgvector `vector` type (created in public) resolves
SET search_path TO northpeak_ops, public;

-- full-text: generated tsvector over name + category + seasonality
ALTER TABLE products
  ADD COLUMN IF NOT EXISTS search_document tsvector
  GENERATED ALWAYS AS (
    to_tsvector('english',
      coalesce(product_name, '') || ' ' ||
      coalesce(category, '')     || ' ' ||
      coalesce(seasonality, ''))
  ) STORED;
CREATE INDEX IF NOT EXISTS products_search_gin ON products USING gin (search_document);

-- vector: semantic embedding + HNSW cosine index
ALTER TABLE products ADD COLUMN IF NOT EXISTS embedding vector(1024);
CREATE INDEX IF NOT EXISTS products_embedding_hnsw
  ON products USING hnsw (embedding vector_cosine_ops);
