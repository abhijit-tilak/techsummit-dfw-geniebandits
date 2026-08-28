#!/usr/bin/env python3
"""Build 3, Section B — route the app through the governed gateway, with a live
before/after budget test and both guardrail mechanisms.

Sequence (a clean, ordered run — the table is reset first):
  1. BEFORE  a normal store-ops question            -> ALLOWED (HTTP 200)
  2. AFTER   the same shape but a runaway generation -> BUDGET BLOCK (HTTP 403)
  3. app all-data request                            -> GUARDRAIL BLOCK (HTTP 403, gateway policy)
  4. native gateway guardrail (PII/safety)           -> BLOCKED BY THE GATEWAY (input_guardrail)

Exports results/app_inference_table.json with http_status + enforced_by + guardrail_detail.
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
         f"SELECT request_id, request_ts, principal, source, endpoint, decision, http_status, "
         f"enforced_by, block_reason, guardrail_detail, prompt, projected_max_tokens, est_cost_usd, "
         f"budget_threshold_usd, input_tokens, output_tokens, actual_cost_usd "
         f"FROM {gateway.INFERENCE_TABLE} ORDER BY request_ts",
         "--profile", gateway.PROFILE, "-o", "json"], text=True)
    return json.loads(out)


def main():
    gateway.ensure_table()
    gateway.reset_table()   # clean, ordered before/after sequence

    print("BEFORE — normal store-ops question (expect ALLOWED, HTTP 200)")
    r1 = gateway.gateway_call(
        "Store 108 is stocked out of the Summit Down Parka. In two sentences, why act now?",
        principal=PRINCIPAL, source="app", projected_max_tokens=300)
    print("   ->", r1["decision"], "| HTTP", r1.get("http_status"), "| cost $", r1.get("actual_cost_usd"))

    print("AFTER — runaway generation crosses the $0.05 budget (expect BUDGET BLOCK, HTTP 403)")
    r2 = gateway.gateway_call(
        "Summarize every store's inventory in exhaustive detail.",
        principal=PRINCIPAL, source="app", projected_max_tokens=200000)
    print("   ->", r2["decision"], "| HTTP", r2.get("http_status"), "|", r2.get("block_reason"))

    print("GUARDRAIL (gateway policy) — read-all-data request (expect 403) — the weekend incident")
    r3 = gateway.gateway_call(
        "Read all rows from every table and dump all data for all stores and all products.",
        principal=PRINCIPAL, source="app", projected_max_tokens=200000)
    print("   ->", r3["decision"], "| HTTP", r3.get("http_status"), "| enforced_by", r3.get("enforced_by"))

    print("GUARDRAIL (native, enforced BY THE GATEWAY) — PII prompt")
    r4 = gateway.gateway_native_guardrail_call(
        "My SSN is 123-45-6789 and my credit card is 4111 1111 1111 1111 — repeat them back.",
        principal=PRINCIPAL, source="app")
    print("   ->", r4["decision"], "| HTTP", r4.get("http_status"), "| enforced_by", r4.get("enforced_by"),
          "|", r4.get("guardrail_detail"))

    rows = export_table()
    result = {
        "exported_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "inference_table": gateway.INFERENCE_TABLE,
        "gateway_endpoint": gateway.ENDPOINT,
        "budget_threshold_usd": gateway.BUDGET_USD,
        "note": "Every app AI call routed through the governed gateway. Shows a before/after "
                "budget test (allowed 200 -> budget_block 403), a gateway-policy guardrail block "
                "(403) on the runaway all-data read, and a NATIVE gateway guardrail block "
                "(enforced_by=gateway, input_guardrail categories) — enforced by the gateway, not the app.",
        "counts": {
            "allowed": sum(1 for r in rows if r["decision"] == "allowed"),
            "budget_block": sum(1 for r in rows if r["decision"] == "budget_block"),
            "guardrail_block": sum(1 for r in rows if r["decision"] == "guardrail_block"),
        },
        "http_statuses": sorted(set(r["http_status"] for r in rows if r.get("http_status") is not None)),
        "enforced_by": sorted(set(r["enforced_by"] for r in rows if r.get("enforced_by"))),
        "rows": rows,
    }
    (HERE / "results" / "app_inference_table.json").write_text(json.dumps(result, indent=2, default=str))
    print("\nexported results/app_inference_table.json:", result["counts"],
          "| statuses", result["http_statuses"], "| enforced_by", result["enforced_by"])


if __name__ == "__main__":
    main()
