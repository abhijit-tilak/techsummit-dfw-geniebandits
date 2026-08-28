#!/usr/bin/env bash
# =====================================================================================
# NorthPeak — Substitute Finder : Lakebase (pgvector) provisioning
# =====================================================================================
# On the EXISTING Lakebase project, creates an ISOLATED branch so the shared `production`
# branch is never touched, then a read-write endpoint, the `northpeak` database, and the
# pgvector schema (03_lakebase_setup.sql). Uses the OAuth connect pattern from the
# databricks-lakebase skill (postgres generate-database-credential).
#
# Requires: databricks CLI v0.285.0+ (postgres commands) and psql on PATH.
# =====================================================================================
set -euo pipefail

PROFILE=""
PROJECT="dbdemos-asset-generator"     # existing Lakebase project ("NorthPeak Retail Demo")
BRANCH="rkm-substitute-advisor"       # ISOLATED branch (not production)
ENDPOINT="primary-rw"                 # read-write endpoint on the isolated branch
DBNAME="northpeak"
SQL_FILE="$(dirname "$0")/03_lakebase_setup.sql"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)  PROFILE="$2"; shift 2 ;;
    --project)  PROJECT="$2"; shift 2 ;;
    --branch)   BRANCH="$2"; shift 2 ;;
    --endpoint) ENDPOINT="$2"; shift 2 ;;
    --db)       DBNAME="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done
[[ -z "$PROFILE" ]] && { echo "usage: $0 --profile <profile> [--project ...] [--branch rkm-substitute-advisor] [--db northpeak]" >&2; exit 1; }
command -v psql >/dev/null || { echo "psql not found — brew install postgresql@16" >&2; exit 1; }

PROJ="projects/$PROJECT"
BR="$PROJ/branches/$BRANCH"
EP="$BR/endpoints/$ENDPOINT"

echo "▶ Isolated branch $BR (off production)"
if databricks postgres get-branch "$BR" --profile "$PROFILE" >/dev/null 2>&1; then
  echo "  • branch exists"
else
  databricks postgres create-branch "$PROJ" "$BRANCH" \
    --json "{\"spec\": {\"source_branch\": \"$PROJ/branches/production\", \"no_expiry\": true}}" \
    --profile "$PROFILE"
fi

echo "▶ Read-write endpoint $EP"
if databricks postgres get-endpoint "$EP" --profile "$PROFILE" >/dev/null 2>&1; then
  echo "  • endpoint exists"
else
  databricks postgres create-endpoint "$BR" "$ENDPOINT" \
    --json '{"spec": {"endpoint_type": "ENDPOINT_TYPE_READ_WRITE", "autoscaling_limit_min_cu": 0.5, "autoscaling_limit_max_cu": 2.0}}' \
    --profile "$PROFILE"
fi

echo "▶ Waiting for endpoint to become ACTIVE"
for i in $(seq 1 60); do
  STATE=$(databricks postgres list-endpoints "$BR" --profile "$PROFILE" -o json 2>/dev/null \
            | python3 -c "import sys,json;e=json.load(sys.stdin);print(e[0]['status'].get('current_state','')) if e else print('')" 2>/dev/null || echo "")
  [[ "$STATE" == "ACTIVE" ]] && break
  sleep 5
done

HOST=$(databricks postgres list-endpoints "$BR" --profile "$PROFILE" -o json | python3 -c "import sys,json;print(json.load(sys.stdin)[0]['status']['hosts']['host'])")
TOKEN=$(databricks postgres generate-database-credential "$EP" --profile "$PROFILE" -o json | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
EMAIL=$(databricks current-user me --profile "$PROFILE" -o json | python3 -c "import sys,json;print(json.load(sys.stdin)['userName'])")

echo "▶ Creating database '$DBNAME' (if absent)"
PGPASSWORD="$TOKEN" psql "host=$HOST port=5432 dbname=postgres user=$EMAIL sslmode=require" \
  -tc "SELECT 1 FROM pg_database WHERE datname='$DBNAME'" | grep -q 1 \
  || PGPASSWORD="$TOKEN" psql "host=$HOST port=5432 dbname=postgres user=$EMAIL sslmode=require" -c "CREATE DATABASE $DBNAME;"

echo "▶ Applying pgvector schema → $DBNAME"
PGPASSWORD="$TOKEN" psql "host=$HOST port=5432 dbname=$DBNAME user=$EMAIL sslmode=require" -v ON_ERROR_STOP=1 -f "$SQL_FILE"

echo "✓ Lakebase ready."
echo "  host   : $HOST"
echo "  branch : $BR"
echo "  db     : $DBNAME  (tables: product_catalog, substitute_actions)"
echo "  Next   : python 04_load_lakebase.py --profile $PROFILE --project $PROJECT --branch $BRANCH --db $DBNAME ..."
