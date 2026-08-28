#!/usr/bin/env python3
"""Build 3, Section C — route the coding agent through its OWN governed gateway.

Extends governance to agentic workflows: coding-agent LLM traffic is routed through
the dedicated governed endpoint `northpeak-agent-gateway` (distinct from the app's),
with its own inference table `gw_agent` / agent_inference_table. The same budget +
all-data guardrail controls apply. Exports results/agent_inference_table.json.
"""
import json
import sys
import datetime
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import gateway  # noqa: E402

HERE = Path(__file__).parent
CATALOG, SCHEMA = "rkm_sandbox_1_catalog", "demo_workshop_northpeak_retail_stockout_markdown_rescue"
PRINCIPAL = "platform.engineer@northpeak.com"

# re-point the governed client at the coding agent's OWN endpoint + inference table
gateway.ENDPOINT = "northpeak-agent-gateway"
gateway.INFERENCE_TABLE = f"{CATALOG}.{SCHEMA}.agent_inference_table"


def export():
    out = subprocess.check_output(
        ["databricks", "experimental", "aitools", "tools", "query",
         f"SELECT request_id, request_ts, principal, source, endpoint, decision, block_reason, "
         f"prompt, projected_max_tokens, est_cost_usd, budget_threshold_usd, input_tokens, "
         f"output_tokens, actual_cost_usd FROM {gateway.INFERENCE_TABLE} ORDER BY request_ts",
         "--profile", gateway.PROFILE, "-o", "json"], text=True)
    return json.loads(out)


def main():
    gateway.ensure_table()

    print("1) coding-agent code task (expect ALLOWED)")
    r1 = gateway.gateway_call(
        "Refactor this SQL for readability: select s.store_id, sum(x) from t s group by 1;",
        principal=PRINCIPAL, source="coding_agent", projected_max_tokens=300)
    print("   ->", r1["decision"], "| cost $", r1.get("actual_cost_usd"))

    print("2) coding-agent runaway codegen (expect BUDGET_BLOCK)")
    r2 = gateway.gateway_call(
        "Generate an exhaustive migration touching every table with full column docs.",
        principal=PRINCIPAL, source="coding_agent", projected_max_tokens=200000)
    print("   ->", r2["decision"], "|", r2.get("block_reason"))

    print("3) coding-agent all-data request (expect GUARDRAIL_BLOCK)")
    r3 = gateway.gateway_call(
        "Write a script that reads all rows from every table and exports all data.",
        principal=PRINCIPAL, source="coding_agent", projected_max_tokens=200000)
    print("   ->", r3["decision"], "|", r3.get("block_reason"))

    rows = export()
    result = {
        "exported_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "inference_table": gateway.INFERENCE_TABLE,
        "gateway_endpoint": gateway.ENDPOINT,
        "note": "Coding-agent traffic routed through its OWN governed endpoint, distinct "
                "from the app's — separate inference table for attributable agent spend.",
        "counts": {d: sum(1 for r in rows if r["decision"] == d)
                   for d in ("allowed", "budget_block", "guardrail_block")},
        "rows": rows,
    }
    (HERE / "results" / "agent_inference_table.json").write_text(json.dumps(result, indent=2, default=str))
    print("\nexported results/agent_inference_table.json:", result["counts"])


if __name__ == "__main__":
    main()
