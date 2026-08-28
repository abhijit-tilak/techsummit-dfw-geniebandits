"""Governed gateway client — bounded, visible, attributable AI calls.

Every AI-backed call goes through gateway_call(): it enforces a per-call BUDGET
block ($0.05) and a GUARDRAIL that prevents reading all Lakebase data, routes
allowed calls through the governed AI Gateway endpoint (northpeak-ai-gateway,
which adds usage tracking + inference table + rate limits + safety/PII guardrails),
and logs EVERY call (allowed / budget_block / guardrail_block) with cost and
principal to the inference table so platform teams can investigate historically.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request
import uuid

PROFILE = "rkm-sandbox-1"
HOST = "https://fe-sandbox-rkm-sandbox-1.cloud.databricks.com"
ENDPOINT = "northpeak-ai-gateway"
CATALOG = "rkm_sandbox_1_catalog"
SCHEMA = "demo_workshop_northpeak_retail_stockout_markdown_rescue"
INFERENCE_TABLE = f"{CATALOG}.{SCHEMA}.app_inference_table"

BUDGET_USD = 0.05                       # per-call hard block
PRICE_IN_PER_1M = 0.15                  # gpt-oss-120b (approx) input $/1M tokens
PRICE_OUT_PER_1M = 0.60                 # output $/1M tokens

# runaway "read all Lakebase data" guardrail
ALL_DATA_PATTERNS = [
    r"\ball rows\b", r"\ball data\b", r"\bevery record\b", r"\bevery row\b",
    r"\bentire (table|database|dataset)\b", r"\bselect\s+\*\b", r"\bdump .*(table|database|data)\b",
    r"\bread everything\b", r"\ball (tables|stores|products|customers)\b",
]


def _token() -> str:
    out = subprocess.check_output(["databricks", "auth", "token", "--profile", PROFILE], text=True)
    return json.loads(out)["access_token"]


def _sql(stmt: str) -> None:
    subprocess.check_output(
        ["databricks", "experimental", "aitools", "tools", "query", stmt,
         "--profile", PROFILE, "-o", "json"], text=True)


def ensure_table() -> None:
    _sql(f"""CREATE TABLE IF NOT EXISTS {INFERENCE_TABLE} (
      request_id STRING, request_ts TIMESTAMP, principal STRING, source STRING,
      endpoint STRING, decision STRING, block_reason STRING, prompt STRING,
      projected_max_tokens INT, est_cost_usd DOUBLE, budget_threshold_usd DOUBLE,
      input_tokens INT, output_tokens INT, actual_cost_usd DOUBLE, response STRING
    ) USING DELTA""")


def _esc(s) -> str:
    if s is None:
        return "NULL"
    return "'" + str(s).replace("'", "''")[:2000] + "'"


def _num(v):
    return str(v) if v is not None else "NULL"


def _log(row: dict) -> None:
    cols = ("request_id, request_ts, principal, source, endpoint, decision, block_reason, "
            "prompt, projected_max_tokens, est_cost_usd, budget_threshold_usd, "
            "input_tokens, output_tokens, actual_cost_usd, response")
    vals = ", ".join([
        _esc(row["request_id"]), "current_timestamp()", _esc(row["principal"]),
        _esc(row["source"]), _esc(ENDPOINT), _esc(row["decision"]), _esc(row.get("block_reason")),
        _esc(row.get("prompt")), _num(row.get("projected_max_tokens")), _num(row.get("est_cost_usd") or 0),
        str(BUDGET_USD), _num(row.get("input_tokens")), _num(row.get("output_tokens")),
        _num(row.get("actual_cost_usd")), _esc(row.get("response")),
    ])
    _sql(f"INSERT INTO {INFERENCE_TABLE} ({cols}) VALUES ({vals})")


def _invoke(prompt: str) -> dict:
    body = json.dumps({"messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        f"{HOST}/serving-endpoints/{ENDPOINT}/invocations", data=body,
        headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def _text(content) -> str:
    if isinstance(content, str):
        return content
    parts = [b.get("text", "") for b in content if b.get("type") in ("text", "output_text")]
    return " ".join(p for p in parts if p).strip()


def gateway_call(prompt: str, principal: str, source: str = "app",
                 projected_max_tokens: int = 300) -> dict:
    """Route one AI call through the governed gateway. Returns a result dict."""
    rid = "req-" + uuid.uuid4().hex[:12]
    est_in = max(1, len(prompt) // 4)

    # 1) GUARDRAIL — prevent reading all Lakebase data
    low = prompt.lower()
    if any(re.search(p, low) for p in ALL_DATA_PATTERNS):
        # projected cost had this runaway all-data read been allowed
        projected = round((est_in * PRICE_IN_PER_1M + projected_max_tokens * PRICE_OUT_PER_1M) / 1e6, 4)
        row = {"request_id": rid, "principal": principal, "source": source,
               "decision": "guardrail_block",
               "block_reason": "runaway all-Lakebase-data read blocked by guardrail",
               "prompt": prompt, "projected_max_tokens": projected_max_tokens,
               "est_cost_usd": projected}
        _log(row)
        return {"request_id": rid, "decision": "guardrail_block", **row}

    # 2) BUDGET — block calls whose projected cost exceeds the threshold
    est_cost = round((est_in * PRICE_IN_PER_1M + projected_max_tokens * PRICE_OUT_PER_1M) / 1e6, 4)
    if est_cost > BUDGET_USD:
        row = {"request_id": rid, "principal": principal, "source": source,
               "decision": "budget_block",
               "block_reason": f"projected cost ${est_cost} exceeds per-call budget ${BUDGET_USD}",
               "prompt": prompt, "projected_max_tokens": projected_max_tokens,
               "est_cost_usd": est_cost}
        _log(row)
        return {"request_id": rid, "decision": "budget_block", **row}

    # 3) ALLOWED — route through the governed endpoint, log actual cost
    resp = _invoke(prompt)
    usage = resp.get("usage", {})
    itok, otok = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    actual = round((itok * PRICE_IN_PER_1M + otok * PRICE_OUT_PER_1M) / 1e6, 6)
    text = _text(resp["choices"][0]["message"]["content"])
    row = {"request_id": rid, "principal": principal, "source": source,
           "decision": "allowed", "block_reason": None, "prompt": prompt,
           "projected_max_tokens": projected_max_tokens, "est_cost_usd": est_cost,
           "input_tokens": itok, "output_tokens": otok, "actual_cost_usd": actual,
           "response": text}
    _log(row)
    return {"request_id": rid, "decision": "allowed", "response": text,
            "actual_cost_usd": actual, "input_tokens": itok, "output_tokens": otok}
