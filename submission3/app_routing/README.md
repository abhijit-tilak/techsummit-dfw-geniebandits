# Route the app through the governed gateway (Section B)

The deployed Databricks App `dbgen-northpeak` calls its foundation model via
`ai_query(RATIONALE_ENDPOINT, ...)`. It was re-routed through the governed Unity
Gateway endpoint and redeployed:

1. `app.yaml` (here): `RATIONALE_ENDPOINT=northpeak-ai-gateway` (was `databricks-gpt-oss-20b`).
2. Granted the app service principal `CAN_QUERY` on `northpeak-ai-gateway`.
3. `databricks apps deploy dbgen-northpeak` — SUCCEEDED.

Verified live: `GET /api/recovery/STORE-0108/SKU-APP-04412` returned an FM rationale
generated **through the governed gateway** ("Transferring the 100-unit … Summit Down
Parkas from El Paso #26 … recapturing ~$25,009…"). All the app's AI calls now carry the
gateway's usage tracking, inference-table tracing, rate limits, and guardrails; the
demonstrable $0.05 budget block and all-data guardrail block are shown in
`results/app_inference_table.json` via the governed client.
