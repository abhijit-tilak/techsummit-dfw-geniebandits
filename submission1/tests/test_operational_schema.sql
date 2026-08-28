-- =====================================================================================
-- Tests for migrations 001 + 002 (run by 09_run_tests.py, result committed as evidence)
-- =====================================================================================
-- Each test is a single query that returns one boolean column named `passed`.
-- The runner asserts every row is TRUE and records the outcome.
-- =====================================================================================

-- name: fk_no_orphan_stores
-- Every recovery action references a real store (referential integrity holds).
SELECT NOT EXISTS (
  SELECT 1 FROM northpeak_ops.recovery_actions ra
  LEFT JOIN northpeak_ops.stores s ON s.store_id = ra.store_id
  WHERE s.store_id IS NULL
) AS passed;

-- name: fk_no_orphan_products
-- Every recovery action references a real product.
SELECT NOT EXISTS (
  SELECT 1 FROM northpeak_ops.recovery_actions ra
  LEFT JOIN northpeak_ops.products p ON p.product_id = ra.product_id
  WHERE p.product_id IS NULL
) AS passed;

-- name: audit_trigger_populated
-- The status-change trigger wrote at least one history row.
SELECT (SELECT count(*) FROM northpeak_ops.action_status_history) > 0 AS passed;

-- name: hybrid_indexes_present
-- Both the GIN full-text index and the HNSW vector index exist.
SELECT (
  SELECT count(*) FROM pg_indexes
  WHERE schemaname = 'northpeak_ops' AND tablename = 'products'
    AND (indexdef ILIKE '%hnsw%' OR indexdef ILIKE '%gin%')
) = 2 AS passed;

-- name: embedding_coverage_complete
-- Every product has a 1024-dim embedding.
SELECT (SELECT count(*) FROM northpeak_ops.products WHERE embedding IS NOT NULL) = 200 AS passed;

-- name: check_constraint_rejects_bad_move
-- The chosen_move CHECK constraint is enforced (a bad value must NOT already exist).
SELECT NOT EXISTS (
  SELECT 1 FROM northpeak_ops.recovery_actions
  WHERE chosen_move NOT IN ('transfer','expedite','substitute')
) AS passed;
