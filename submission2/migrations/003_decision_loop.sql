-- =====================================================================================
-- Migration 003 — Build 2 decision loop (workflow state + write-back timestamps)
-- =====================================================================================
-- Applied to the Build 1 development branch geniebandits-dev by 01_migrate_and_view.py.
-- Adds the observability/workflow-state table and the write-back timestamp/proposer
-- columns needed to close the loop (surface -> prescribe -> approve -> act).
--
-- Everything writable lives in northpeak_ops; the Build 1 synced table
-- public.store_sku_position_synced stays READ-ONLY (never written by the app).
-- =====================================================================================

SET search_path TO northpeak_ops, public;

-- write-back timestamps + who proposed the action (assistant vs person)
ALTER TABLE recovery_actions ADD COLUMN IF NOT EXISTS proposed_by   TEXT;
ALTER TABLE recovery_actions ADD COLUMN IF NOT EXISTS proposed_at   TIMESTAMPTZ DEFAULT now();
ALTER TABLE recovery_actions ADD COLUMN IF NOT EXISTS committed_at  TIMESTAMPTZ;   -- set when a person commits the decision
ALTER TABLE recovery_actions ADD COLUMN IF NOT EXISTS rationale     TEXT;          -- assistant's why / memo pointer

-- ── workflow state + observability ─────────────────────────────────────────────────────
-- One row per workflow event: trigger firings (scheduled re-score / system update) AND
-- recorded human decisions, each with a timestamp. This is the closed-loop audit trail.
CREATE TABLE IF NOT EXISTS workflow_state (
  event_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  event_type    TEXT NOT NULL
                  CHECK (event_type IN ('trigger','decision')),
  trigger_source TEXT,                 -- 'scheduled_job' | 'system_update' | 'manual'  (for trigger events)
  job_run_ref   TEXT,                  -- Databricks job/run id when fired by the schedule
  action_id     BIGINT REFERENCES recovery_actions(action_id),  -- for decision events
  store_id      TEXT,
  product_id    TEXT,
  decision      TEXT,                  -- 'approved' | 'corrected' | 'rejected'
  flagged_count INTEGER,               -- how many rows the trigger flagged
  detail        JSONB,                 -- scores / what-if / free-form observability
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS workflow_state_type_idx  ON workflow_state (event_type, created_at);
CREATE INDEX IF NOT EXISTS workflow_state_action_idx ON workflow_state (action_id);
