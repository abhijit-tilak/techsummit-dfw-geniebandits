# MERGE GUIDE — Substitute Finder → NorthPeak demo

This module was built **additively and in isolation**. Nothing in the existing demo was edited.
This file is the change record (the repo isn't a git repo) and the recipe to fold it into the demo.

## 1. Files added (all new)

```
substitute_advisor/
  README.md                            # overview + run order
  01_product_embeddings.sql            # Delta: gold_product_embeddings + gold_product_markdown_profile
  02_ai_gateway_setup.sh               # governed endpoint np-substitute-llm (usage tracking + rate limits)
  03_lakebase_setup.sql                # pgvector schema: product_catalog + substitute_actions
  03_lakebase_setup.sh                 # isolated Lakebase branch + endpoint + db + apply schema
  04_load_lakebase.py                  # Delta → Lakebase loader (200 rows)
  advisor.py                           # real-time finder: recommend_substitutes() + approve()
  05_substitute_advisor_demo.py        # runnable notebook (the manager-facing demo surface)
  06_gold_substitute_recommendations.sql  # batch gold view (same score in Delta SQL) for Genie/dashboard
  MERGE_GUIDE.md                       # this file
```

**Existing files modified: none.** The merge points below are proposed edits you apply when ready.

## 2. What was pushed to the workspace

Only the **runnable code**, into your personal folder (nothing shared, nothing executed):

```
/Users/ramdas.murali@databricks.com/northpeaklocal/substitute_advisor/*
```

No AI Gateway endpoint was created, no Lakebase branch/table was created, no shared-schema Delta
writes happened. Run steps §4 yourself when you choose.

## 3. Isolation choices (so a merge never clobbers the shared demo)

| Resource | Isolated value | Shared demo value |
|---|---|---|
| Lakebase branch | `rkm-substitute-advisor` (new, off `production`) | `production` (untouched) |
| Governed endpoint | `np-substitute-llm` (workspace-global, yours) | — (demo had none) |
| Catalog/schema | parameters (default to the demo's) | can point at a scratch schema to test |

## 4. Run order

```bash
PROFILE=fevmrkmsb
CATALOG=rkm_sandbox_1_catalog
SCHEMA=demo_workshop_northpeak_retail_stockout_markdown_rescue
WAREHOUSE=<pro-or-serverless-warehouse-id>

# a. Delta tables (embeddings + metrics) — via the notebook step 1, or:
envsubst < substitute_advisor/01_product_embeddings.sql \
  | databricks experimental aitools tools query --warehouse $WAREHOUSE --profile $PROFILE --file /dev/stdin

# b. Governed AI Gateway endpoint
substitute_advisor/02_ai_gateway_setup.sh --profile $PROFILE

# c. Lakebase pgvector store (isolated branch)
substitute_advisor/03_lakebase_setup.sh --profile $PROFILE

# d. Load Delta → Lakebase
python substitute_advisor/04_load_lakebase.py --profile $PROFILE --warehouse $WAREHOUSE \
  --catalog $CATALOG --schema $SCHEMA

# e. Batch gold view (for Genie/dashboard)
envsubst < substitute_advisor/06_gold_substitute_recommendations.sql \
  | databricks experimental aitools tools query --warehouse $WAREHOUSE --profile $PROFILE --file /dev/stdin

# f. Real-time demo + approval
python substitute_advisor/advisor.py --profile $PROFILE --warehouse $WAREHOUSE \
  --store STORE-0214 --product SKU-APP-04412
```

## 5. Merge point A — `databricks.yml` (new setup-job task)

Add embeddings + gold view to the existing `northpeak_setup` job so a fresh deploy builds them.
Insert after `run_pipeline` (which creates `gold_store_sku_position`):

```yaml
        - task_key: build_substitute_advisor
          depends_on: [{ task_key: run_pipeline }]
          notebook_task:
            notebook_path: ./substitute_advisor/build_substitute_tables   # thin wrapper that runs 01 + 06
            base_parameters:
              catalog: ${resources.schemas.demo_schema.catalog_name}
              schema:  ${resources.schemas.demo_schema.name}
          environment_key: sdk_default
```

> `02` (AI Gateway) and `03/04` (Lakebase) stay **outside** the bundle — like the existing app's
> `lakebase_setup_db.sh` / `finalize_app.sh`, they're pre/post scripts (the CLI can't declare a
> pgvector table or a rate-limited endpoint). Add them to `dab_instructions.md`.

## 6. Merge point B — `genie_space.json` (expose the new tables)

Add to `data_sources.tables`:

```json
{"identifier": "rkm_sandbox_1_catalog.demo_workshop_northpeak_retail_stockout_markdown_rescue.gold_substitute_recommendations"},
{"identifier": "rkm_sandbox_1_catalog.demo_workshop_northpeak_retail_stockout_markdown_rescue.gold_product_markdown_profile"}
```

Suggested sample questions:
- "For the Store 214 parka stockout, what's the best substitute and why?"
- "Which substitute recommendations clear the most markdown exposure?"

## 7. Merge point C — enrich the hollow `substitute` branch of `gold_recovery_recommendations`

In `transformation/02_gold.sql`, the `substitute_moves` CTE currently emits a placeholder
(`NULL` source, `0` units, `lost_sales × 0.3`). Replace it so the substitute move carries the **real,
ranked** product from `gold_substitute_recommendations` (rank = 1):

```sql
-- replaces the old substitute_moves CTE
substitute_moves AS (
  SELECT
    s.store_id,
    s.product_id,
    'substitute'                          AS recommended_move,
    r.substitute_product_id               AS recommended_source_store_id,  -- now: the substitute SKU
    CAST(r.expected_units AS INT)         AS recommended_units,
    r.capture_margin + r.markdown_saved   AS predicted_recaptured_usd,     -- net-value incl. markdown saved
    0                                     AS move_cost_usd
  FROM shortfalls s
  JOIN LIVE.gold_substitute_recommendations r
    ON r.store_id = s.store_id AND r.stockout_product_id = s.product_id AND r.substitute_rank = 1
)
```

Result: all three moves (transfer / expedite / substitute) rank together by net value in the
prescription the manager approves — and `substitute` now names a real product that also clears
markdown. (If you prefer to keep the pipeline pure-heuristic, leave `02_gold.sql` as-is and keep the
substitute arm as the separate `gold_substitute_recommendations` view.)

## 8. Teardown of the isolated pieces

```bash
databricks postgres update-branch projects/dbdemos-asset-generator/branches/rkm-substitute-advisor \
  spec.is_protected --json '{"spec":{"is_protected":false}}' --profile fevmrkmsb
databricks postgres delete-branch projects/dbdemos-asset-generator/branches/rkm-substitute-advisor --profile fevmrkmsb
databricks serving-endpoints delete np-substitute-llm --profile fevmrkmsb
# Delta: DROP TABLE gold_product_embeddings, gold_product_markdown_profile,
#        gold_substitute_recommendations, gold_recovery_actions;
```
