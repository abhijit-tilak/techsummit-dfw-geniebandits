# Databricks notebook source
# MAGIC %md
# MAGIC # NorthPeak decision trigger (scheduled)
# MAGIC Re-scores the live decision view on a schedule and records a **trigger event**
# MAGIC into `northpeak_ops.workflow_state` on the Build 1 dev branch. A scheduled/system
# MAGIC trigger — not a person opening the view — is what fires this.

# COMMAND ----------
# MAGIC %pip install psycopg2-binary --quiet
dbutils.library.restartPython()

# COMMAND ----------
import json, psycopg2
from databricks.sdk import WorkspaceClient

# Build 1 dev-branch Lakebase endpoint (non-secret); token minted at runtime.
ENDPOINT = "projects/dbdemos-asset-generator/branches/geniebandits-dev/endpoints/primary"
HOST = "ep-wandering-dew-d15vttwi.database.us-west-2.cloud.databricks.com"

w = WorkspaceClient()
user = w.current_user.me().user_name
token = w.api_client.do("POST", "/api/2.0/postgres/credentials", body={"endpoint": ENDPOINT})["token"]

# job run id (passed in by the job via {{job.run_id}}) so the trigger event
# references the scheduled run that fired it
dbutils.widgets.text("run_id", "manual")
run_id = dbutils.widgets.get("run_id")

conn = psycopg2.connect(host=HOST, port=5432, dbname="databricks_postgres",
                        user=user, password=token, sslmode="require")
conn.autocommit = True
cur = conn.cursor()

# COMMAND ----------
# Re-score the decision view: how many northern cold-weather stockouts still need a decision?
cur.execute("""
  SELECT count(*)
  FROM public.store_sku_position_synced s
  JOIN northpeak_ops.stores   st ON st.store_id  = s.store_id
  JOIN northpeak_ops.products p  ON p.product_id = s.product_id
  LEFT JOIN northpeak_ops.recovery_actions ra
         ON ra.store_id = s.store_id AND ra.product_id = s.product_id
        AND ra.committed_at IS NOT NULL
  WHERE s.position_status='stockout' AND st.climate_zone='North'
    AND p.seasonality='cold_weather' AND ra.action_id IS NULL
""")
flagged = cur.fetchone()[0]

cur.execute("""
  INSERT INTO northpeak_ops.workflow_state (event_type, trigger_source, job_run_ref, flagged_count, detail)
  VALUES ('trigger','scheduled_job', %s, %s, %s::jsonb)
  RETURNING event_id, created_at
""", (str(run_id), flagged, json.dumps({"view": "northern_coldweather_stockouts_needing_decision"})))
event_id, created_at = cur.fetchone()
print(f"trigger event {event_id} recorded at {created_at}: {flagged} rows flagged (run {run_id})")
conn.close()
