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
| App inference table | `results/app_inference_table.json` | app calls routed through the gateway, the **budget block** ($0.12 > $0.05 rejected), and the **guardrail block** (runaway all-Lakebase-data read) |
| Gateway usage dashboard | `gateway_usage.lvdash.json` | AI/BI dashboard tracking usage + budgets across **app / coding agent / MCP** (deployed + published, dashboard ACTIVE) |
| Coding-agent thread | `agent_thread.txt` | the `ucode` call (model routed to the governed endpoint), the MCP config, and a **real Slack MCP call** (DM `D0716QWMD7G`, ts `1787898327.799779`) |
| [Optional] Coding-agent inference table | `results/agent_inference_table.json` | the coding agent's **own** inference table, distinct from the app's (endpoint `northpeak-agent-gateway`) |

## How the controls work

- **Budgets** — the governed client (`lib/gateway.py`) blocks any call whose projected
  cost exceeds **$0.05** (demonstrated: a runaway generation projected at $0.12 was
  rejected). Rate limits (60/min endpoint, 20/min user) bound throughput natively.
- **Guardrails** — a guardrail blocks reads of all Lakebase data (patterns like "all
  rows", "all data", "entire table", "select \*", "read everything"); native AI Gateway
  adds safety + PII BLOCK. The weekend "read all data" call is now rejected pre-execution.
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
python 02_route_agent.py                # route the coding agent -> agent_inference_table.json (own endpoint)
python scripts/build_dashboard.py       # gateway_usage.lvdash.json  (then databricks lakeview create/publish)
# agent_thread.txt: the ucode call + MCP config + the real Slack MCP call
```

## Governed endpoints created

- `northpeak-ai-gateway` — the app's gateway (inference table `gw_app`).
- `northpeak-agent-gateway` — the coding agent's gateway (inference table `gw_agent`).
- Both: external-model endpoints over `databricks-gpt-oss-120b`, usage tracking +
  inference table + rate limits + safety/PII guardrails; secret scope `northpeak_gw`.
