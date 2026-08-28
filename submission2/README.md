# Build 2 submission — NorthPeak decision loop (Genie Bandits)

Build 2 turns the Build 1 governed lakehouse + Lakebase into a **decision** for store
ops: **surface → prescribe → approve → act → reflect**. It is built on the Build 1
**development branch** `geniebandits-dev` (production/`main` stays the clean demo env),
reads the Build 1 **read-only synced table** `public.store_sku_position_synced`, and
persists all state/actions to **writable** `northpeak_ops` tables — never to the synced
table. All artifacts were executed live; `results/*` are the committed outputs.

## The three steps

1. **Visualize** — `view_query.sql` ranks/flags northern cold-weather stockouts by
   lost-sales exposure over the read-only synced table (`NEEDS_DECISION` vs
   `COMMITTED_*`). A **scheduled Databricks Job** (`trigger/`) re-scores and records a
   trigger event — a schedule fires it, not a person opening the view.
2. **Assist** — `02_assist.py` explains *why* the row is flagged, runs a **what-if**
   (substitute vs transfer) that retrieves candidates from the **Build 1 Lakebase Search
   index** (pgvector + full-text, RRF — not a separate vector store), and **drafts the
   memo**. FM calls go through the governed AI Gateway endpoint `databricks-gpt-oss-120b`.
3. **Act** — `03_act.py`: the assistant proposes; a **person corrects the quantity and
   commits**; the write lands in writable `northpeak_ops.recovery_actions`; the decision
   is logged to `workflow_state`; the next view read shows `COMMITTED_APPROVED` (closed loop).

## Evidence → file map (all 7 requested exports)

| Required export | File | What it contains |
|---|---|---|
| Writable action table | `results/writeback_table.json` | action_id 10: transfer, proposed_by=assistant → approved_by=priya.raghavan, `created_at` + `committed_at` |
| Workflow-state / observability | `results/state_table.json` | 2 `trigger` events (scheduled_job, real `job_run_ref`) + 1 `decision` event, all timestamped |
| Live-view query + rows | `view_query.sql`, `results/view_result.json` | ranked/flagged decision surface; hero row #1 = NEEDS_DECISION |
| Assistant interaction log | `results/assist_log.jsonl` | ≥1 explanation + ≥1 what-if (+ memo draft), each with request + response and the Lakebase-search ids retrieved |
| Auto-drafted memo | `drafted_sample.md` | the transfer memo the assistant drafted |
| Hero question + decision chain | `hero_question.txt` | the hero question and the linked record IDs across all exports |
| Git history | `results/git_history.txt` | `git log --graph --oneline --decorate --all` — layer-by-layer build on the dev branch off main |

Supporting: `results/closed_loop_view.json` (hero re-read → COMMITTED), `results/hero.json`
(resolved hero + linked ids), `migrations/003_decision_loop.sql`, `trigger/` (scheduled job as code).

**Retrieves from the Build 1 Lakebase Search index (execution evidence):**
`notebooks/lakebase_search_evidence.ipynb` (executed, with outputs) and
`results/lakebase_search_result.json` show the hybrid RRF query hitting the **same Build 1
indexes** — `products_embedding_hnsw` (pgvector) + `products_search_gin` (full-text) on
`northpeak_ops.products` — and returning ranked substitute candidates (both arms
contributing). This is retrieval from the Build 1 Lakebase Search index, **not a separate
vector store**. Regenerate with `python notebooks/_build_search_evidence.py`.

## Decision chain (linked record IDs)

`STORE-0108` / `SKU-APP-04412` (Summit Down Parka) → view flags NEEDS_DECISION →
scheduled trigger (run 512688725663836) → assist explains + what-if (retrieves
SKU-APP-04415/04413/04414/... from Lakebase Search) + memo → **action_id 10** written
(transfer 60 units from STORE-0213, corrected+approved by priya.raghavan) → decision
event references action_id 10 → next read = COMMITTED_APPROVED. See `hero_question.txt`.

## Run order

```bash
export PROFILE=rkm-sandbox-1
python 01_migrate_and_view.py     # migration 003 + ranked/flagged view -> view_result.json
./trigger/create_trigger_job.sh   # scheduled Databricks Job (re-score -> workflow_state)
databricks jobs run-now --json '{"job_id": <id>}' --profile $PROFILE   # fire once (or wait for schedule)
python 02_assist.py               # explain + what-if (Lakebase Search) + memo
python 03_act.py                  # propose -> correct -> commit -> closed-loop re-read
```

## Deployed Databricks App (live demo)

The Build 2 decision loop is demoed **live in the deployed Databricks App** `dbgen-northpeak`
(RUNNING at https://dbgen-northpeak-7474644154611302.aws.databricksapps.com). Its source is
version-controlled here under `app/` (Node/Express + vanilla JS; `app.yaml`, `server.js`,
`public/`). The app's data matches Build 1/2 exactly — same hero (**STORE-0108 Missoula,
Summit Down Parka, $53,569**), same KPIs (771 stockouts / $15.9M lost-sales).

Live loop exercised end-to-end (evidence: `results/app_demo_result.json`):
1. **Visualize** — `/api/kpis`, `/api/shortfalls` → STORE-0108 ranked #1.
2. **Assist** — `/api/recommendations/STORE-0108/SKU-APP-04412` → transfer ranked #1
   (net $24,632), plus `/api/recovery` (vector substitutes + FM rationale).
3. **Act** — `/api/approve` writes to `recovery_approvals` (+ a markdown-hold on the source
   store); **the person approves/corrects before commit**.
4. **Closed loop** — `/api/approvals` reflects the committed decision on the next read; the
   UI now shows a **"Committed decision"** banner (see fix below).

**Fix applied this session:** the Approve button hung on "Approving…" because the frontend's
`handleApprove` never reset the button and had no error handling or loop-close. Patched
`app/public/app.js` to (a) set the button to **"Approved ✓"** on success with a `.catch`,
and (b) read the committed record back from `/api/approvals` and render a **closed-loop
confirmation banner**. Redeployed via `databricks apps deploy` (SUCCEEDED).

## Notes

- The deployed app reads the `production` branch / `dbgen_northpeak` (its own `northpeak_ops`
  with `gold_*` + `recovery_approvals`); the 7 validator exports above were generated on the
  Build 1 **dev branch** (`geniebandits-dev`) per the "build against the dev branch"
  requirement. Same decision-loop behavior, same hero — the live app is the running surface.
- The synced table is **never written**; all writes target writable `northpeak_ops` tables.
