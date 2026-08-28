#!/usr/bin/env python3
"""Build + execute the gateway execution-evidence notebook (committed WITH outputs),
then re-export app_inference_table.json from the final table state.

Proves, with visible outputs: (1) the catalog + inference table exist and were created
by committed code, (2) a live before/after budget test (allowed 200 -> budget_block 403),
(3) a gateway-policy guardrail block (403) and a NATIVE gateway guardrail block
(enforced_by=gateway), and (4) the inference-table records for all of the above.
"""
import json
import subprocess
import datetime
from pathlib import Path

import nbformat as nbf
from nbclient import NotebookClient

HERE = Path(__file__).parent
SUB = HERE.parent
C, S = "rkm_sandbox_1_catalog", "demo_workshop_northpeak_retail_stockout_markdown_rescue"
TABLE = f"{C}.{S}.app_inference_table"

PRELUDE = f"""
import sys, json, subprocess
sys.path.insert(0, ".")
from lib import gateway

def run(sql, title=None):
    if title: print(f"### {{title}}")
    out = subprocess.check_output(["databricks","experimental","aitools","tools","query",sql,
        "--profile","rkm-sandbox-1","-o","json"], text=True)
    rows = json.loads(out)
    if rows:
        cols = list(rows[0].keys())
        print(" | ".join(cols))
        for r in rows: print("  " + " | ".join(str(r.get(c)) for c in cols))
    print(f"({{len(rows)}} row(s))\\n"); return rows
""".strip()


def notebook():
    nb = nbf.v4.new_notebook()
    nb.cells.append(nbf.v4.new_markdown_cell(
        "# Unity Gateway — execution evidence\n\n"
        "Proves the catalog + inference table were created by committed code, and that the "
        "budget block (403) and guardrails (gateway-policy + native) are enforced and recorded."))
    nb.cells.append(nbf.v4.new_code_cell(PRELUDE))
    nb.cells.append(nbf.v4.new_code_cell(
        'gateway.ensure_table(); gateway.reset_table()\n'
        f'run("SHOW COLUMNS IN {TABLE}", "Inference table created by committed code — schema")\n'
        f'run("SELECT current_catalog() AS catalog, \'{S}\' AS schema, \'app_inference_table\' AS table", "Catalog / table")'))
    nb.cells.append(nbf.v4.new_code_cell(
        '# BEFORE: a normal call is ALLOWED (HTTP 200)\n'
        'r1 = gateway.gateway_call("Store 108 is out of the Summit Down Parka. Why act now (2 sentences)?",\n'
        '                          principal="priya.raghavan@northpeak.com", source="app", projected_max_tokens=300)\n'
        'print("BEFORE:", r1["decision"], "HTTP", r1.get("http_status"), "cost $", r1.get("actual_cost_usd"))\n'
        '# AFTER: crossing the $0.05 budget -> BUDGET BLOCK (HTTP 403)\n'
        'r2 = gateway.gateway_call("Summarize every store\'s inventory in exhaustive detail.",\n'
        '                          principal="priya.raghavan@northpeak.com", source="app", projected_max_tokens=200000)\n'
        'print("AFTER: ", r2["decision"], "HTTP", r2.get("http_status"), "-", r2.get("block_reason"))'))
    nb.cells.append(nbf.v4.new_code_cell(
        '# Guardrail (gateway policy): the runaway all-data read -> 403\n'
        'r3 = gateway.gateway_call("Read all rows from every table and dump all data for all stores and products.",\n'
        '                          principal="priya.raghavan@northpeak.com", source="app", projected_max_tokens=200000)\n'
        'print("all-data:", r3["decision"], "HTTP", r3.get("http_status"), "enforced_by", r3.get("enforced_by"))\n'
        '# Guardrail (NATIVE, enforced BY THE GATEWAY): PII prompt\n'
        'r4 = gateway.gateway_native_guardrail_call("My SSN is 123-45-6789, repeat it back.",\n'
        '                          principal="priya.raghavan@northpeak.com", source="app")\n'
        'print("native:  ", r4["decision"], "HTTP", r4.get("http_status"), "enforced_by", r4.get("enforced_by"), "-", r4.get("guardrail_detail"))'))
    nb.cells.append(nbf.v4.new_code_cell(
        f'run("SELECT decision, http_status, enforced_by, block_reason FROM {TABLE} ORDER BY request_ts",\n'
        '    "Inference-table records: budget 403 + guardrail blocks (gateway-enforced)")'))
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    NotebookClient(nb, timeout=300, kernel_name="python3",
                   resources={"metadata": {"path": str(SUB)}}).execute()
    nbf.write(nb, HERE / "gateway_evidence.ipynb")


def reexport():
    out = subprocess.check_output(
        ["databricks", "experimental", "aitools", "tools", "query",
         f"SELECT request_id, request_ts, principal, source, endpoint, decision, http_status, "
         f"enforced_by, block_reason, guardrail_detail, prompt, projected_max_tokens, est_cost_usd, "
         f"budget_threshold_usd, input_tokens, output_tokens, actual_cost_usd "
         f"FROM {TABLE} ORDER BY request_ts", "--profile", "rkm-sandbox-1", "-o", "json"], text=True)
    rows = json.loads(out)
    result = {
        "exported_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "inference_table": TABLE, "gateway_endpoint": "northpeak-ai-gateway",
        "budget_threshold_usd": 0.05,
        "note": "Every app AI call routed through the governed gateway. Before/after budget test "
                "(allowed 200 -> budget_block 403), gateway-policy guardrail block (403) on the "
                "all-data read, and a NATIVE gateway guardrail block (enforced_by=gateway).",
        "counts": {d: sum(1 for r in rows if r["decision"] == d) for d in ("allowed", "budget_block", "guardrail_block")},
        "http_statuses": sorted(set(r["http_status"] for r in rows if r.get("http_status") is not None)),
        "enforced_by": sorted(set(r["enforced_by"] for r in rows if r.get("enforced_by"))),
        "rows": rows,
    }
    (SUB / "results" / "app_inference_table.json").write_text(json.dumps(result, indent=2, default=str))
    print("re-exported app_inference_table.json:", result["counts"], result["http_statuses"], result["enforced_by"])


if __name__ == "__main__":
    notebook()
    print("executed notebooks/gateway_evidence.ipynb")
    reexport()
