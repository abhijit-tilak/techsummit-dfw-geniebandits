# Build 3 submission — Unity Gateway: bounded, visible, attributable AI spend

After the weekend incident (a runaway 2-hour chat call read "all data" and cost
$1,200, with no tracing to investigate), AI spend at NorthPeak is now **bounded,
visible, and attributable**. Every AI-backed call — from the app, the coding agent,
and the MCP — routes through a governed AI Gateway endpoint with budgets, guardrails,
and inference-table tracing. All artifacts here were executed live on `rkm-sandbox-1`.

## Evidence → file map (5 exports)

| Required export | File | What it shows |
|---|---|---|
| Gateway model-service + inference-table creation | `gateway_service.txt` (`scripts/gateway_service.sh`) | creates the governed external-model endpoint `northpeak-ai-gateway` + AI Gateway: usage tracking + **inference table** + rate limits + guardrails |
| App inference table | `results/app_inference_table.json` + `notebooks/gateway_evidence.ipynb` (executed, with outputs) | before/after budget test (**allowed HTTP 200 → budget_block HTTP 403**), the **gateway-policy guardrail block (403)** on the all-data read, and a **native gateway guardrail block (400, `enforced_by=gateway`, `input_guardrail flagged: privacy`)** — enforced by the gateway, not the app. Also proves the catalog + inference table were created (SHOW COLUMNS) |
| Gateway usage dashboard | `gateway_usage.lvdash.json` | AI/BI dashboard tracking usage + budgets across **app / coding agent / MCP** (deployed + published, dashboard ACTIVE) |
| Coding-agent thread | `agent_thread.txt` | the `ucode` call (model routed to the governed endpoint), the MCP config, and a **real Slack MCP call** (DM `D0716QWMD7G`, ts `1787898327.799779`) |
| [Optional] Coding-agent inference table | `results/agent_inference_table.json` | the coding agent's **own** inference table, distinct from the app's (endpoint `northpeak-agent-gateway`) |

## How the controls work

- **Budgets** — the governed client (`lib/gateway.py`) blocks any call whose projected
  cost exceeds **$0.05**, returning **HTTP 403** (not just an alert). A live **before/after**
  sequence (`notebooks/gateway_evidence.ipynb`, `results/app_inference_table.json`) shows a
  normal call ALLOWED (HTTP 200) then a runaway generation BLOCKED (HTTP 403, projected $0.12).
  Rate limits (60/min endpoint, 20/min user) bound throughput natively.
- **Guardrails — two layers, both recorded in the inference table:**
  1. **Gateway-policy** all-data guardrail (403) blocks the runaway "read all Lakebase data"
     call (the weekend incident), `enforced_by=gateway`.
  2. **Native AI Gateway guardrail** (safety + PII BLOCK) — a PII prompt is rejected **by the
     gateway** (HTTP 400, `input_guardrail: {privacy: true}`), proving the guardrail is
     **enforced by the gateway, not the app** (`enforced_by=gateway`, `guardrail_detail` carries
     the flagged categories).
- **Inference-table tracing** — the gateway logs every call (allowed/budget_block/
  guardrail_block) with principal, cost, and prompt to UC inference tables
  (`app_inference_table`, `gw_app_*`) so platform teams can investigate historical queries.
- **Agent + MCP governance** — coding-agent traffic routes through its **own** governed
  endpoint `northpeak-agent-gateway` (inference table `gw_agent` / `agent_inference_table`);
  MCP calls (Slack MCP) are declared to route through the same gateway. Bonus satisfied:
  the agent has a distinct governed endpoint + inference table from the app.

## Exec report — the numbers (D. Prove it and report)

Across the app, the coding agent, and the MCP (from the inference tables):

| Source | Allowed | Budget blocks | Guardrail blocks |
|---|---|---|---|
| app | 1 | 1 | 1 |
| coding_agent | 1 | 1 | 1 |
| mcp | 1 | 0 | 0 |
| **Total** | **3** | **2** | **2** |

- **7** governed calls across **3** attributable sources; **4** blocked (2 budget + 2 guardrail).
- Allowed spend: **~$0.0005** total. Each blocked runaway had a **projected $0.12** (> $0.05 cap) prevented.
- The weekend **$1,200** runaway (all-data read) would now be **blocked before execution**, and
  every call is traced for investigation — the gap that made the incident un-investigable is closed.

## Run order

```bash
export PROFILE=rkm-sandbox-1
./scripts/gateway_service.sh            # governed endpoint + inference table + guardrails + rate limits
python 01_route_app.py                  # route the app -> app_inference_table.json (allowed/budget/guardrail)
python notebooks/_build_gateway_evidence.py  # executed evidence notebook (catalog+table, 403 budget, gateway guardrails)
python 02_route_agent.py                # route the coding agent -> agent_inference_table.json (own endpoint)
python scripts/build_dashboard.py       # gateway_usage.lvdash.json  (then databricks lakeview create/publish)
# agent_thread.txt: the ucode call + MCP config + the real Slack MCP call
```

## Governed endpoints created

- `northpeak-ai-gateway` — the app's gateway (inference table `gw_app`).
- `northpeak-agent-gateway` — the coding agent's gateway (inference table `gw_agent`).
- Both: external-model endpoints over `databricks-gpt-oss-120b`, usage tracking +
  inference table + rate limits + safety/PII guardrails; secret scope `northpeak_gw`.
