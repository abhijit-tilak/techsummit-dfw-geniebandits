#!/usr/bin/env bash
# =====================================================================================
# Unity Gateway — governed model-service + inference-table creation script
# =====================================================================================
# Stands up the governed AI Gateway endpoint that ALL AI-backed app calls route
# through, so spend is bounded, visible, and attributable. Creates:
#   1. a secret scope + PAT the gateway uses to reach the backing FM
#   2. the external-model serving endpoint `northpeak-ai-gateway` (routes to the
#      Databricks FM databricks-gpt-oss-120b, OpenAI-compatible)
#   3. AI Gateway config: usage tracking + INFERENCE TABLE (tracing) + rate limits
#      + guardrails (safety + PII BLOCK). The runaway "read all Lakebase data"
#      guardrail and the $0.05 per-call budget block are enforced by the governed
#      client (lib/gateway.py) which logs every allow/block to the inference table.
#
# Inference table (platform teams query it to investigate historical calls):
#   rkm_sandbox_1_catalog.demo_workshop_northpeak_retail_stockout_markdown_rescue.gw_app_*
# =====================================================================================
set -euo pipefail
PROFILE="${PROFILE:-rkm-sandbox-1}"
HOST="${HOST:-https://fe-sandbox-rkm-sandbox-1.cloud.databricks.com}"
CATALOG="${CATALOG:-rkm_sandbox_1_catalog}"
SCHEMA="${SCHEMA:-demo_workshop_northpeak_retail_stockout_markdown_rescue}"
ENDPOINT="${ENDPOINT:-northpeak-ai-gateway}"
BACKING_FM="${BACKING_FM:-databricks-gpt-oss-120b}"

# 1) secret scope + PAT for the gateway to call the backing FM
databricks secrets create-scope northpeak_gw --profile "$PROFILE" 2>/dev/null || true
PAT=$(databricks tokens create --lifetime-seconds 604800 --comment northpeak-gw-ext \
        --profile "$PROFILE" -o json | python3 -c "import sys,json;print(json.load(sys.stdin)['token_value'])")
databricks secrets put-secret northpeak_gw pat --string-value "$PAT" --profile "$PROFILE"

# 2) governed external-model endpoint (no GPU; full AI Gateway support)
if ! databricks serving-endpoints get "$ENDPOINT" --profile "$PROFILE" >/dev/null 2>&1; then
  databricks serving-endpoints create --profile "$PROFILE" --no-wait --json "{
    \"name\": \"$ENDPOINT\",
    \"config\": {\"served_entities\": [{
      \"name\": \"gw-ext\",
      \"external_model\": {
        \"name\": \"$BACKING_FM\", \"provider\": \"openai\", \"task\": \"llm/v1/chat\",
        \"openai_config\": {
          \"openai_api_base\": \"$HOST/serving-endpoints\",
          \"openai_api_key\": \"{{secrets/northpeak_gw/pat}}\"
        }}}]}}"
fi

# 3) AI Gateway: usage tracking + inference table (tracing) + rate limits + guardrails
databricks serving-endpoints put-ai-gateway "$ENDPOINT" --profile "$PROFILE" --json "{
  \"usage_tracking_config\": {\"enabled\": true},
  \"inference_table_config\": {\"enabled\": true, \"catalog_name\": \"$CATALOG\",
    \"schema_name\": \"$SCHEMA\", \"table_name_prefix\": \"gw_app\"},
  \"rate_limits\": [
    {\"key\": \"endpoint\", \"renewal_period\": \"minute\", \"calls\": 60},
    {\"key\": \"user\",     \"renewal_period\": \"minute\", \"calls\": 20}
  ],
  \"guardrails\": {\"input\": {\"safety\": true, \"pii\": {\"behavior\": \"BLOCK\"}}}
}"

echo "✓ Governed endpoint '$ENDPOINT' ready with usage tracking + inference table + rate limits + guardrails."
echo "  Attributable spend:  SELECT * FROM system.serving.endpoint_usage WHERE endpoint_name='$ENDPOINT';"
