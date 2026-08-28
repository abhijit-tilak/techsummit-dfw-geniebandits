#!/usr/bin/env bash
# =====================================================================================
# Lakehouse -> Lakebase forward sync, defined as code (NorthPeak / Genie Bandits)
# =====================================================================================
# Syncs the GOVERNED Unity Catalog materialized view gold_store_sku_position into
# Lakebase Postgres as a read-only synced table. This is the "sync defined as code"
# path — a committed, idempotent, parameterized script, not a UI click-through.
#
#   source (governed UC MV) : <CATALOG>.<SCHEMA>.gold_store_sku_position
#   target (Lakebase PG)     : lakebase_geniebandits.public.store_sku_position_synced
#   mode                     : SNAPSHOT  (full copy; no CDF required)
#   PK                       : (store_id, product_id)
#
# The Postgres table is read-only (managed by the sync pipeline) — the WRITABLE
# operational tables live separately in schema northpeak_ops (migration 001).
#
# The same definition is also expressed declaratively in sync.tf (Terraform). The
# CLI flow below is the one that executes on current Autoscaling Lakebase; see
# sync.tf's header for the IaC-parity note.
# =====================================================================================
set -euo pipefail

PROFILE="${PROFILE:-rkm-sandbox-1}"
PROJECT="${PROJECT:-dbdemos-asset-generator}"
CATALOG="${CATALOG:-rkm_sandbox_1_catalog}"
SCHEMA="${SCHEMA:-demo_workshop_northpeak_retail_stockout_markdown_rescue}"
BRANCH="${BRANCH:-geniebandits-dev}"
LB_CATALOG="${LB_CATALOG:-lakebase_geniebandits}"
TARGET="$LB_CATALOG.public.store_sku_position_synced"
PROJ="projects/$PROJECT"

# 1) Register the Lakebase Postgres database as a UC catalog (one-time, idempotent).
if ! databricks catalogs get "$LB_CATALOG" --profile "$PROFILE" >/dev/null 2>&1; then
  databricks postgres create-catalog "$LB_CATALOG" \
    --json "{\"spec\":{\"postgres_database\":\"databricks_postgres\",\"branch\":\"$PROJ/branches/$BRANCH\"}}" \
    --profile "$PROFILE" >/dev/null
  echo "✓ registered UC catalog $LB_CATALOG"
fi

# 2) Create (or reuse) the synced table.
if databricks postgres get-synced-table "synced_tables/$TARGET" --profile "$PROFILE" >/dev/null 2>&1; then
  echo "• synced table $TARGET exists"
else
  databricks postgres create-synced-table "$TARGET" \
    --json "{
      \"spec\": {
        \"source_table_full_name\": \"$CATALOG.$SCHEMA.gold_store_sku_position\",
        \"primary_key_columns\": [\"store_id\", \"product_id\"],
        \"scheduling_policy\": \"SNAPSHOT\",
        \"branch\": \"$PROJ/branches/$BRANCH\",
        \"postgres_database\": \"databricks_postgres\",
        \"create_database_objects_if_missing\": true,
        \"new_pipeline_spec\": {\"storage_catalog\": \"$CATALOG\", \"storage_schema\": \"$SCHEMA\"}
      }
    }" --profile "$PROFILE"
  echo "✓ created synced table $TARGET"
fi

echo "✓ Forward sync defined and applied. Rows land in public.store_sku_position_synced."
