# NorthPeak Retail — Stockout & Markdown Rescue

> A live, governed inventory-recovery app for store operations. It doesn't just
> surface a shortfall — it **prescribes the recovery move** (transfer, expedite,
> or substitute), ranks it by **net recaptured value**, and lets a manager
> approve it in one click. Every AI call runs through **Unity AI Gateway** so the
> spend is capped, guard-railed, and attributable per store.

Built for **TechSummit DFW** by team **Genie Bandits**, on the Databricks
Lakehouse + Databricks Apps + Lakebase.

---

## The business challenge

NorthPeak Retail (~$2B revenue, 400 US stores, ~40K SKUs) loses money at **both
ends of the shelf**, from a single root cause:

- **Stockouts** — a store has none of a product a shopper came in to buy. The
  demand was real, but the sale walks out the door (and often the rest of the
  basket goes to a competitor). **~4% of sales, ~$80M/yr in lost revenue.**
- **Markdowns** — product sitting in stores that won't sell at full price, then
  discounted to clear shelf space, giving the margin away. **~$120M/yr.**

Both are the **same misallocation** — inventory in the wrong store at the wrong
time — and nobody sees it until it's too late to move the stock. Today NorthPeak's
lakehouse reports everything *after the fact*: dashboards refresh on a schedule,
so store-ops teams act on **yesterday's picture** rather than what's on the shelf
now.

And the fix has to be **AI-assisted without open-ended AI spend** — an ungoverned
assistant that recommends transfers all day just trades one margin leak for
another.

## What this app solves

A **real-time inventory view for store operations** that surfaces a shortfall,
**prescribes the recovery move for a manager to approve**, and writes the action
back — turning governed lakehouse data into action *today*, not tomorrow.

### Business outcomes to defend

| Outcome | Target |
|---|---|
| Revenue recaptured from fewer stockouts | **~$10M/yr** |
| Margin protected from earlier markdowns | **~$12M/yr** |
| AI spend | **Capped, auditable, per-store attributable** (~$200K/yr bounded) |
| Latency | **Real-time** store action, replacing batch-reporting latency |

---

## The live slice: the cold snap

The company-wide `$80M / $120M` is the *why this exists*. The demo shows one
concrete, catchable slice of it:

An early **cold snap ~3 weeks ago** flipped demand for cold-weather apparel:

- **North** — cold-weather styles **sold out** (real demand, lost sales).
- **South** — the **same styles pile toward markdown** (dead stock, margin clock
  running).

Inventory had been allocated to a normal-weather plan; the snap pulled demand
North faster than the batch replenishment cycle could react.

| Metric (the cold-snap slice) | Value |
|---|---|
| Stocked-out northern stores (affected SKUs) | ~30, at 0 on-hand with rising velocity |
| Over-stocked southern stores (same SKUs) | ~40, high on-hand near-zero velocity |
| Lost-sales exposure | ~$4.8M annualized |
| Markdown exposure | ~$5.6M |
| Hero SKU | Summit Down Parka (`SKU-APP-04412`) |
| Hero store | Store 214 — Denver, CO (North) |

---

## The demo arc

1. **See it** — open the Store Ops app: a US map with red stockouts in the North
   next to amber overstock in the South, on the *same five SKUs*, plus lost-sales
   and markdown KPIs.
2. **Ask why** — in the chat dock, ask *"Store 214 is short on the Summit Down
   Parka — what's the best recovery move?"* The assistant investigates via
   **Genie** over the governed lakehouse.
3. **Get the move** — it ranks **transfer / expedite / substitute** by **net
   recaptured value** (recaptured revenue − cost − margin impact) and recommends
   the best one, with a what-if. It will **not** recommend a move that trades a
   stockout for a worse markdown.
4. **Act** — approve → the move + a markdown-hold write back to **Lakebase** →
   the queue and KPIs update live.
5. **Governed & bounded AI** — every assistant call runs through **Unity AI
   Gateway**: spend cap, guardrails, and per-store attributable logging.

---

## Who this is built for

| Persona | Role | What they ask |
|---|---|---|
| **Priya Raghavan** | SVP Retail Operations | *"Which stores act differently tomorrow because of this?"* — owns the ~$200M/yr stockout + markdown loss. |
| **Amara Nwachukwu** | VP Finance, Technology | *"What does a recommendation cost, and what share of company AI spend is this?"* — the ~$200K/yr here must be capped and per-store attributable. |
| **Tobias Lindqvist** | Director, Data Engineering | *"Can this serve 400 stores at low latency without a nightly batch behind it?"* |
| **Hector Villalobos** | Platform Engineering Lead | *"Can I explain why the app said that, and show my work?"* — needs to trace a recommendation back through inventory/order records. |

---

## Architecture

```
Governed data  →  Governed recommendation  →  Governed, bounded AI assistant
```

| Layer | Technology | Role |
|---|---|---|
| **Ingest / storage** | Unity Catalog + `raw_data` volume | Sales, inventory, product data land in the lakehouse with lineage + governance. |
| **Transform** | Lakeflow Spark Declarative Pipeline (SDP) | `silver` → `gold` materialized views: store×SKU position (`stockout / at_risk / overstock / healthy`), open shortfalls, and `gold_recovery_recommendations` ranked by **net recaptured value**. |
| **Governed metrics** | Metric View (`mv_store_position`) | Consistent metrics for dashboard + Genie. |
| **Analytics** | AI/BI (Lakeview) dashboard | The stockout-vs-overstock map + KPIs (`dashboard.lvdash.json`). |
| **Natural-language Q&A** | Genie space | Answers *"why is Store 214 short?"* over the governed lakehouse (`genie_space.json`). |
| **Serving layer** | Databricks App (Node/React) + **Lakebase** (Postgres) | Per-store, low-latency screen serving 400 stores with write-back — no second nightly batch database. |
| **AI governance** | Unity AI Gateway | Spend cap + rate limits + per-principal usage tracking on every FM call. |

> **No ML model / Knowledge Assistant / Multi-Agent Supervisor to train or
> serve** — the recovery recommendation is a governed SDP-pipeline heuristic
> ranked by net recaptured value. There's nothing to train.

### The `substitute` move (`substitute_advisor/`)

The base pipeline ranks **transfer** and **expedite**; its `substitute` move was
originally a placeholder. The `substitute_advisor/` module makes it **real and
governed**:

> When a store is stocked out of a SKU, find *semantically similar* products
> sitting in **markdown-risk overstock** elsewhere and rank them by an economic
> **net-value score** — so one move fills the demand **and** clears
> markdown-bound inventory.

- **Lakebase pgvector** (`vector(1024)`, HNSW, cosine KNN) over product embeddings.
- Embeddings + rationale via **`ai_query`** against FMs behind **Unity AI Gateway**
  (`np-substitute-llm`), with usage tracking + rate limits.
- Ranking floats markdown-risk items to the top by adding avoided-loss dollars,
  using both the replacement item's and the markdown item's margins.

See `substitute_advisor/README.md` and `substitute_advisor/MERGE_GUIDE.md` for
detail and merge points.

---

## Repository layout

| Path | What it is |
|---|---|
| `databricks.yml` | Asset Bundle — schema, volume, SDP pipeline, dashboard, Genie, app shells + setup job. |
| `dab_instructions.md` | The 5-command deploy runbook. |
| `resources.json` | Capability manifest + resolved resource IDs. |
| `dashboard.lvdash.json` | AI/BI dashboard definition. |
| `genie_space.json` | Genie space configuration. |
| `transformation/01_silver.sql`, `02_gold.sql` | Silver → gold pipeline logic (positions + net-value recovery recs). |
| `substitute_advisor/` | Governed, AI-assisted substitute finder (Lakebase pgvector + AI Gateway). |
| `architecture.md` | Architecture graph stub. |
| `*.svg` | NorthPeak / summit-mascot branding assets. |

---

## Deploy

Full runbook in [`dab_instructions.md`](./dab_instructions.md). Re-creates the
whole demo on any workspace:

```bash
# 1. Lakebase DB (pre-deploy — the CLI can't declare a Postgres database)
./app/scripts/lakebase_setup_db.sh --db-name dbgen_northpeak

# 2. Create resource shells (schema, volume, pipeline, dashboard, app) + setup job
databricks bundle deploy \
  --var catalog=solution_builder \
  --var schema=demo_workshop_northpeak_retail_stockout_markdown_rescue \
  --var warehouse_id=<warehouse_id>

# 3. Run the setup job: data → pipeline → metric view → Genie → app grants → export IDs
databricks bundle run northpeak_setup --var catalog=… --var schema=… --var warehouse_id=…

# 4. Grant the app service principal on the Lakebase (Postgres) schemas
./app/scripts/lakebase_grant_app_credential.sh \
  --app-name dbgen-northpeak --project-id dbdemos-asset-generator --db-name dbgen_northpeak

# 5. Harvest resolved IDs → write app.yaml env → deploy the app
./app/scripts/finalize_app.sh
```

- After an **app content** change: re-run steps **2 + 5**.
- After a **data/resource** change: re-run steps **2 + 3 + 5**.
- Re-runs are **idempotent** (Genie updates in place; the pipeline refreshes).

> ⚠️ **Never** `databricks apps update --json` a deployed app out of band — it
> replaces the whole app spec and silently drops the `resources` bindings, which
> deprovisions the Lakebase SP role and breaks Postgres auth. Change app config
> only via the bundle (+ redeploy) or `app.yaml` (+ `finalize_app.sh`).

### Teardown

```bash
databricks bundle destroy --auto-approve
```

Does **not** drop the Lakebase project/DB, the UC tables/volume, or the Genie
space.

---

## Why it lands

Two problems that look opposite are the **same misallocation seen from two
sides** — and the app shows them **on one map, in one glance**. Then it does the
hard part: it doesn't just flag the problem, it **ranks the fix by net value**
and lets the manager act in one click, with the assisting AI **governed and
spend-bounded end to end**. Governed data → a governed recommendation → a
governed, bounded AI assistant.
