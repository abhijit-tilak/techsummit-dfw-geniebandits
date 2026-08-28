# NorthPeak — Governed, AI-Assisted Substitute Finder

A **live inventory view for store operations** that surfaces a shortfall and **prescribes the
recovery move — transfer, expedite, or substitute — for a manager to approve**, turning governed
lakehouse data into action today.

The existing NorthPeak demo (`../`) already surfaces shortfalls and prescribes **transfer** and
**expedite** ranked by net recaptured value. Its **`substitute` move was a hollow placeholder** — it
never named a substitute product (`recommended_source_store_id = NULL`, `recommended_units = 0`,
recaptured = `lost_sales × 0.3`). This module makes `substitute` **real and governed**.

## The idea in one sentence

> When a store is **stocked out** of a SKU, find *semantically similar* products that are sitting in
> **markdown-risk overstock** elsewhere and rank them by an economic **net-value score** — so one move
> fills the demand **and** clears markdown-bound inventory.

Confirmed in the sandbox: **771 stockouts** (~$15.9M lost-sales exposure) vs **6,091 overstock /
markdown positions** (~$22.7M markdown exposure) across 200 products / 17 subcategories.

## Tech stack (exactly as required)

| Requirement | How this module satisfies it |
|---|---|
| **LakeBase search** for similar products | pgvector `vector(1024)` KNN over product embeddings (`<=>` cosine), HNSW index — `product_catalog` in Lakebase Postgres |
| **Rank by markdown status** (markdown items higher priority) | Net-value `substitution_score` in which markdown items add `markdown_saved` dollars → floated to the top |
| **+ margin metrics in the ranking** | uses the **replacement item's margin** (`unit_price − unit_cost`) and the **markdown item's margin/exposure** (`price × markdown_depth`) |
| **Real-time inference** | at request time: embed the query SKU → Lakebase KNN → score → FM rationale (`advisor.recommend_substitutes`) |
| **Unity AI Gateway to track cost / bound spend** | governed endpoint `np-substitute-llm` with `usage_tracking_config.enabled` (per-principal cost) + `rate_limits` (bounded) + optional budget policy |
| **`ai_query` against a FM in AI Gateway** | embeddings via `ai_query('databricks-gte-large-en', …)`; rationale via `ai_query('np-substitute-llm', …)` |

## Ranking algorithm

For a stockout of product `p` at store `s`, each candidate substitute `c`:

```
similarity      = 1 − cosine(embed(p), embed(c))                 # conversion-likelihood proxy  [LakeBase]
demand_units    = avg_daily_velocity(p, s) × HORIZON_DAYS        # gap to fill (default 14)
expected_units  = LEAST(demand_units, c.overstock_on_hand_units) # units we can actually redirect
capture_margin  = expected_units × c.unit_margin × similarity    # replacement-item MARGIN captured
markdown_saved  = c.is_markdown_risk                             # markdown-item MARGIN protected
                    ? expected_units × c.unit_price × MARKDOWN_DEPTH : 0   # default depth 0.40
substitution_score = W_CAPTURE·capture_margin + W_MARKDOWN·markdown_saved
ORDER BY substitution_score DESC, similarity DESC
```

Markdown-risk items rank higher because `markdown_saved` adds avoided-loss dollars; both margins drive
the score. Tunables (defaults): `HORIZON_DAYS=14`, `MARKDOWN_DEPTH=0.40`, `W_CAPTURE=1.0`,
`W_MARKDOWN=1.0` — all live in `advisor.py` and are echoed in the batch SQL.

## Files

| File | Role |
|---|---|
| `01_product_embeddings.sql` | Delta: `gold_product_embeddings` (via `ai_query` gte-large-en) + `gold_product_markdown_profile` (margin + markdown metrics) |
| `02_ai_gateway_setup.sh` | Create governed endpoint `np-substitute-llm` + `put-ai-gateway` (usage tracking + rate limits) |
| `03_lakebase_setup.sh` / `.sql` | Isolated Lakebase branch `rkm-substitute-advisor`, `northpeak` DB, pgvector `product_catalog` + `substitute_actions`, HNSW index |
| `04_load_lakebase.py` | Load 200 catalog rows (embeddings + metrics) Delta → Lakebase |
| `advisor.py` | Real-time finder: `recommend_substitutes()` + `approve()` |
| `05_substitute_advisor_demo.py` | Runnable notebook — unified prescription + approval + spend panel |
| `06_gold_substitute_recommendations.sql` | Batch gold view (same score in Delta SQL) for Genie/dashboard |
| `MERGE_GUIDE.md` | Every file added + exact merge points into the demo + run order |

## Run order (you run these — see `MERGE_GUIDE.md` for the merge into the demo)

```bash
PROFILE=fevmrkmsb
CATALOG=rkm_sandbox_1_catalog
SCHEMA=demo_workshop_northpeak_retail_stockout_markdown_rescue
WAREHOUSE=<pro-or-serverless-warehouse-id>     # ai_query needs Pro/Serverless

# 1. Embeddings + metrics (Delta)   — see 01, or run via the demo notebook
# 2. Governed AI Gateway endpoint
./02_ai_gateway_setup.sh --profile $PROFILE
# 3. Lakebase pgvector store (isolated branch)
./03_lakebase_setup.sh --profile $PROFILE
# 4. Load Delta → Lakebase
python 04_load_lakebase.py --profile $PROFILE --catalog $CATALOG --schema $SCHEMA --warehouse $WAREHOUSE
# 5. Real-time demo + approval
#    open 05_substitute_advisor_demo.py (staged in your workspace folder) and run
```

> **Status:** authored locally and staged to your workspace folder
> `/Users/ramdas.murali@databricks.com/northpeaklocal`. **Nothing here has been executed against the
> workspace** — no endpoint created, no Lakebase table created, no shared-schema writes. Every step is
> a parameterized script you run when you choose. Catalog/schema are parameters, so you can point the
> mutating steps at an isolated scratch schema before merging into the shared demo.
