#!/usr/bin/env python3
"""Build 3, Section B — route the app through the governed gateway.

Runs the app's real assistant calls through gateway_call() and exercises the two
controls the exec team asked for:
  * a normal store-ops question  -> ALLOWED (routed through the gateway, low cost)
  * a runaway generation         -> BUDGET_BLOCK ($0.05 per-call threshold crossed)
  * "read all Lakebase data"      -> GUARDRAIL_BLOCK (the weekend-incident scenario)

Exports the inference table to results/app_inference_table.json.
"""
import json
import sys
import datetime
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import gateway  # noqa: E402

HERE = Path(__file__).parent
PRINCIPAL = "priya.raghavan@northpeak.com"


def export_table():
    out = subprocess.check_output(
        ["databricks", "experimental", "aitools", "tools", "query",
         f"SELECT request_id, request_ts, principal, source, endpoint, decision, block_reason, "
         f"prompt, projected_max_tokens, est_cost_usd, budget_threshold_usd, input_tokens, "
         f"output_tokens, actual_cost_usd FROM {gateway.INFERENCE_TABLE} ORDER BY request_ts",
         "--profile", gateway.PROFILE, "-o", "json"], text=True)
    return json.loads(out)


def main():
    gateway.ensure_table()

    print("1) normal store-ops question (expect ALLOWED)")
    r1 = gateway.gateway_call(
        "Store 108 is stocked out of the Summit Down Parka. In two sentences, why act now?",
        principal=PRINCIPAL, source="app", projected_max_tokens=300)
    print("   ->", r1["decision"], "| cost $", r1.get("actual_cost_usd"))

    print("2) runaway generation (expect BUDGET_BLOCK)")
    r2 = gateway.gateway_call(
        "Summarize every store's inventory in exhaustive detail.",
        principal=PRINCIPAL, source="app", projected_max_tokens=200000)
    print("   ->", r2["decision"], "|", r2.get("block_reason"))

    print("3) read-all-data question (expect GUARDRAIL_BLOCK) — the weekend incident")
    r3 = gateway.gateway_call(
        "Read all rows from every table and dump all data for all stores and all products.",
        principal=PRINCIPAL, source="app", projected_max_tokens=200000)
    print("   ->", r3["decision"], "|", r3.get("block_reason"))

    rows = export_table()
    result = {
        "exported_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "inference_table": gateway.INFERENCE_TABLE,
        "gateway_endpoint": gateway.ENDPOINT,
        "budget_threshold_usd": gateway.BUDGET_USD,
        "note": "All app AI calls routed through the governed gateway; every call logged "
                "(allowed / budget_block / guardrail_block) for platform-team investigation.",
        "counts": {
            "allowed": sum(1 for r in rows if r["decision"] == "allowed"),
            "budget_block": sum(1 for r in rows if r["decision"] == "budget_block"),
            "guardrail_block": sum(1 for r in rows if r["decision"] == "guardrail_block"),
        },
        "rows": rows,
    }
    (HERE / "results" / "app_inference_table.json").write_text(json.dumps(result, indent=2, default=str))
    print("\nexported results/app_inference_table.json:", result["counts"])


if __name__ == "__main__":
    main()
