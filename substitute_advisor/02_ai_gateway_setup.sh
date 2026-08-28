#!/usr/bin/env bash
# =====================================================================================
# NorthPeak — Substitute Finder : governed Unity AI Gateway endpoint
# =====================================================================================
# Creates a serving endpoint `np-substitute-llm` that fronts a cheap foundation model,
# then attaches AI Gateway config so the assistant's spend is BOUNDED and ATTRIBUTABLE:
#   * usage_tracking_config.enabled = true   → per-principal cost in system.serving usage
#   * rate_limits (endpoint + per-user)       → the assistant can't run open-ended
#   * (optional) --budget-policy-id           → hard budget cap
#
# The interactive rationale call in advisor.py goes through THIS endpoint via
# ai_query('np-substitute-llm', ...). Embeddings use the pre-provisioned
# databricks-gte-large-en endpoint (batch, cheap, already tracked in system tables).
#
# Idempotent: creates the endpoint if missing, always (re)applies the gateway config.
# Nothing here mutates the shared demo — it's a workspace-global endpoint you own.
# =====================================================================================
set -euo pipefail

PROFILE=""
ENDPOINT="np-substitute-llm"
# Open, cheap FM that supports a custom pay-per-token endpoint. Swap for any chat FM
# (e.g. system.ai.gpt-oss-120b, system.ai.llama-4-maverick) — advisor.py just needs a
# chat endpoint name. Proprietary FMs (Claude/GPT) are only the managed endpoints.
FM_NAME="system.ai.gpt-oss-20b"
ENDPOINT_RPM=60        # endpoint-wide calls/minute (bounded spend)
USER_RPM=20            # per-user calls/minute (fair-use + runaway protection)
BUDGET_POLICY_ID=""    # optional: a UC budget policy id for a hard $ cap

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)          PROFILE="$2"; shift 2 ;;
    --endpoint)         ENDPOINT="$2"; shift 2 ;;
    --model)            FM_NAME="$2"; shift 2 ;;
    --endpoint-rpm)     ENDPOINT_RPM="$2"; shift 2 ;;
    --user-rpm)         USER_RPM="$2"; shift 2 ;;
    --budget-policy-id) BUDGET_POLICY_ID="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done
[[ -z "$PROFILE" ]] && { echo "usage: $0 --profile <profile> [--endpoint np-substitute-llm] [--model system.ai.gpt-oss-20b] [--endpoint-rpm 60] [--user-rpm 20] [--budget-policy-id <id>]" >&2; exit 1; }

echo "▶ Ensuring governed endpoint '$ENDPOINT' (model: $FM_NAME) on profile '$PROFILE'"

if databricks serving-endpoints get "$ENDPOINT" --profile "$PROFILE" >/dev/null 2>&1; then
  echo "  • endpoint already exists — skipping create"
else
  echo "  • creating pay-per-token endpoint over $FM_NAME"
  CREATE_JSON=$(cat <<JSON
{
  "served_entities": [
    { "name": "np-substitute-fm", "foundation_model": { "name": "$FM_NAME" } }
  ]
}
JSON
)
  BUDGET_FLAG=()
  [[ -n "$BUDGET_POLICY_ID" ]] && BUDGET_FLAG=(--budget-policy-id "$BUDGET_POLICY_ID")
  databricks serving-endpoints create "$ENDPOINT" \
    --description "NorthPeak substitute advisor — governed FM (bounded, tracked)" \
    "${BUDGET_FLAG[@]}" \
    --json "$CREATE_JSON" --profile "$PROFILE"
fi

echo "▶ Applying AI Gateway config (usage tracking + rate limits)"
GATEWAY_JSON=$(cat <<JSON
{
  "usage_tracking_config": { "enabled": true },
  "rate_limits": [
    { "key": "endpoint", "renewal_period": "minute", "calls": $ENDPOINT_RPM },
    { "key": "user",     "renewal_period": "minute", "calls": $USER_RPM }
  ]
}
JSON
)
databricks serving-endpoints put-ai-gateway "$ENDPOINT" --json "$GATEWAY_JSON" --profile "$PROFILE"

echo "▶ Verifying"
databricks serving-endpoints get "$ENDPOINT" --profile "$PROFILE" -o json \
  | python3 -c "import sys,json;d=json.load(sys.stdin);g=d.get('ai_gateway',{});print('  state      :',d.get('state'));print('  usage_track:',g.get('usage_tracking_config'));print('  rate_limits:',g.get('rate_limits'))"

echo "✓ '$ENDPOINT' is governed. advisor.py will call it via ai_query('$ENDPOINT', ...)."
echo "  Cost per principal:  SELECT * FROM system.serving.endpoint_usage WHERE served_entity_name LIKE 'np-substitute%';"
