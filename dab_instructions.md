# Deploy — NorthPeak Retail (Stockout & Markdown Rescue)

Re-creates the full demo on any workspace: schema + raw_data volume + SDP
pipeline + AI/BI dashboard + Genie space + Databricks App (Lakebase-backed).

```bash
# 1. Lakebase DB (pre-deploy — the CLI can't declare a postgres database)
./app/scripts/lakebase_setup_db.sh --db-name dbgen_northpeak

# 2. Create resource shells (schema, volume, pipeline, dashboard, app) + setup job
databricks bundle deploy \
  --var catalog=solution_builder \
  --var schema=demo_workshop_northpeak_retail_stockout_markdown_rescue \
  --var warehouse_id=a94beda3aab06fa4

# 3. Run the setup job: data → pipeline → metric view → Genie → app grants → export IDs
databricks bundle run northpeak_setup \
  --var catalog=solution_builder \
  --var schema=demo_workshop_northpeak_retail_stockout_markdown_rescue \
  --var warehouse_id=a94beda3aab06fa4

# 4. Grant the app SP on the Lakebase (Postgres) schemas
./app/scripts/lakebase_grant_app_credential.sh \
  --app-name dbgen-northpeak \
  --project-id dbdemos-asset-generator \
  --db-name dbgen_northpeak

# 5. Harvest resolved IDs → write app.yaml env → deploy the app
./app/scripts/finalize_app.sh
```

After a content change to the app, re-run steps 2 + 5. After a data/resource
change, re-run 2 + 3 + 5. Re-runs are idempotent (the Genie task updates in
place; the pipeline refreshes).

> This demo ships **no ML model / Knowledge Assistant / Multi-Agent Supervisor** —
> the recovery recommendation (`gold_recovery_recommendations`) is built by the
> SDP pipeline heuristic, ranked by net recaptured value. There is nothing to
> train or serve.

## Teardown
```bash
databricks bundle destroy --auto-approve
```
(Does not drop the Lakebase project/DB, the UC tables/volume, or the Genie space.)
