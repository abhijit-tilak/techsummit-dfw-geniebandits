-- =====================================================================================
-- Migration 001 — NorthPeak operational schema (writable OLTP model)
-- =====================================================================================
-- Applied to the geniebandits-dev Lakebase branch by 03_apply_migrations.py.
--
-- This is the OPERATIONAL side of the store-ops app: a fully modeled domain schema
-- with related tables and foreign keys, and WRITABLE tables that are DISTINCT from
-- the read-only Delta->Lakebase synced table (public.store_sku_position_synced,
-- created by 05_synced_table.sh). Managers act here; the synced table is analytics.
--
--   stores               dimension (referenced by actions on both sides of a move)
--   products             dimension (referenced by the shorted SKU and the substitute)
--   recovery_actions     WRITABLE fact — one manager-approved recovery move
--   action_status_history WRITABLE audit — status transitions for each action
--
-- Keys: every fact FK resolves to a dimension; the history FK cascades from its action.
-- =====================================================================================

CREATE SCHEMA IF NOT EXISTS northpeak_ops;
SET search_path TO northpeak_ops;

-- ── dimensions ───────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stores (
  store_id      TEXT PRIMARY KEY,
  store_name    TEXT NOT NULL,
  region        TEXT,
  climate_zone  TEXT,                    -- North / South / Mixed
  city          TEXT,
  latitude      DOUBLE PRECISION,
  longitude     DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS products (
  product_id    TEXT PRIMARY KEY,
  product_name  TEXT NOT NULL,
  category      TEXT,
  seasonality   TEXT,
  unit_cost     DOUBLE PRECISION,
  unit_price    DOUBLE PRECISION,
  unit_margin   DOUBLE PRECISION GENERATED ALWAYS AS (unit_price - unit_cost) STORED
);

-- ── writable fact: manager-approved recovery moves ("action today") ────────────────────
CREATE TABLE IF NOT EXISTS recovery_actions (
  action_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  store_id              TEXT NOT NULL REFERENCES stores(store_id),
  product_id            TEXT NOT NULL REFERENCES products(product_id),
  chosen_move           TEXT NOT NULL
                          CHECK (chosen_move IN ('transfer','expedite','substitute')),
  source_store_id       TEXT REFERENCES stores(store_id),        -- transfer origin
  substitute_product_id TEXT REFERENCES products(product_id),    -- when move = substitute
  units                 INTEGER NOT NULL DEFAULT 0 CHECK (units >= 0),
  net_recaptured_value  DOUBLE PRECISION NOT NULL DEFAULT 0,
  status                TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending','approved','in_transit','fulfilled','cancelled')),
  approved_by           TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS recovery_actions_store_idx   ON recovery_actions (store_id, product_id);
CREATE INDEX IF NOT EXISTS recovery_actions_status_idx  ON recovery_actions (status);

-- ── writable audit: status transitions (operational history) ───────────────────────────
CREATE TABLE IF NOT EXISTS action_status_history (
  history_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  action_id    BIGINT NOT NULL REFERENCES recovery_actions(action_id) ON DELETE CASCADE,
  old_status   TEXT,
  new_status   TEXT NOT NULL,
  changed_by   TEXT,
  changed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS action_status_history_action_idx ON action_status_history (action_id);

-- ── keep updated_at fresh + log every status change to the audit table ─────────────────
CREATE OR REPLACE FUNCTION northpeak_ops.log_action_status_change() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at := now();
  IF (TG_OP = 'UPDATE' AND NEW.status IS DISTINCT FROM OLD.status) THEN
    INSERT INTO northpeak_ops.action_status_history (action_id, old_status, new_status, changed_by)
    VALUES (NEW.action_id, OLD.status, NEW.status, NEW.approved_by);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_action_status ON northpeak_ops.recovery_actions;
CREATE TRIGGER trg_action_status
  BEFORE UPDATE ON northpeak_ops.recovery_actions
  FOR EACH ROW EXECUTE FUNCTION northpeak_ops.log_action_status_change();
