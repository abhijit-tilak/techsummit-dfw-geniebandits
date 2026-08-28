# Databricks notebook source
# MAGIC %md
# MAGIC # NorthPeak — Governed, AI-Assisted Substitute Finder
# MAGIC
# MAGIC A **live inventory view** that surfaces a shortfall and **prescribes the recovery move
# MAGIC (transfer / expedite / substitute) for a manager to approve**. This notebook demos the new
# MAGIC **AI-assisted `substitute` arm**:
# MAGIC
# MAGIC 1. **LakeBase search** — pgvector KNN finds similar products
# MAGIC 2. **Rank by markdown status + margin** — a net-value `substitution_score` (markdown items float up)
# MAGIC 3. **Real-time inference** — `ai_query` embedding + `ai_query` rationale, per request
# MAGIC 4. **Unity AI Gateway** — the rationale FM (`np-substitute-llm`) is rate-limited + usage-tracked
# MAGIC 5. **Manager approval** — write the decision to an action log (action today)

# COMMAND ----------
# MAGIC %pip install -q psycopg2-binary databricks-sdk
# MAGIC %restart_python

# COMMAND ----------
dbutils.widgets.text("catalog", "rkm_sandbox_1_catalog")
dbutils.widgets.text("schema", "demo_workshop_northpeak_retail_stockout_markdown_rescue")
dbutils.widgets.text("warehouse_id", "")           # Pro/Serverless — for the local advisor path
dbutils.widgets.text("store_id", "STORE-0214")
dbutils.widgets.text("product_id", "SKU-APP-04412")
dbutils.widgets.text("chat_endpoint", "np-substitute-llm")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
STORE = dbutils.widgets.get("store_id")
PRODUCT = dbutils.widgets.get("product_id")
CHAT = dbutils.widgets.get("chat_endpoint")
print(f"{CATALOG}.{SCHEMA} — shortfall {STORE}/{PRODUCT}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 1 · Build embeddings + margin/markdown metrics (Delta, via `ai_query`)
# MAGIC Runs `01_product_embeddings.sql`. One-time batch — 200 rows, cheap, tracked in system tables.

# COMMAND ----------
import os
sql_dir = os.path.dirname(os.path.abspath("__file__")) if "__file__" in dir() else os.getcwd()

def run_sql_file(fname):
    with open(os.path.join(sql_dir, fname)) as f:
        body = f.read().replace("${catalog}", CATALOG).replace("${schema}", SCHEMA)
    # execute statement-by-statement (split on bare semicolons at line ends)
    for stmt in [s for s in body.split(";\n") if s.strip() and not s.strip().startswith("--")]:
        spark.sql(stmt)

run_sql_file("01_product_embeddings.sql")
display(spark.sql(f"""
  SELECT COUNT(*) AS products, size(ANY_VALUE(embedding)) AS embed_dim,
         COUNT_IF(unit_margin IS NOT NULL) AS with_margin
  FROM {CATALOG}.{SCHEMA}.gold_product_embeddings"""))

# COMMAND ----------
# MAGIC %md
# MAGIC ## 2 · Surface the shortfall

# COMMAND ----------
display(spark.sql(f"""
  SELECT store_id, store_name, product_id, product_name, on_hand_units,
         ROUND(avg_daily_velocity,1) AS daily_velocity,
         ROUND(lost_sales_exposure_usd) AS lost_sales_usd, position_status
  FROM {CATALOG}.{SCHEMA}.gold_store_sku_position
  WHERE store_id = '{STORE}' AND product_id = '{PRODUCT}'"""))

# COMMAND ----------
# MAGIC %md
# MAGIC ## 3 · The ranking (batch view) — markdown items float to the top
# MAGIC `06_gold_substitute_recommendations.sql` applies the **same net-value score** as the
# MAGIC real-time finder, in Delta SQL. Note `markdown_saved` > 0 for markdown-risk substitutes —
# MAGIC that's what lifts them above equally-similar healthy products.

# COMMAND ----------
run_sql_file("06_gold_substitute_recommendations.sql")
display(spark.sql(f"""
  SELECT substitute_rank, substitute_product_name, substitute_subcategory,
         is_markdown_risk, similarity, expected_units, substitute_unit_margin,
         capture_margin, markdown_saved, substitution_score
  FROM {CATALOG}.{SCHEMA}.gold_substitute_recommendations
  WHERE store_id = '{STORE}' AND stockout_product_id = '{PRODUCT}'
  ORDER BY substitute_rank"""))

# COMMAND ----------
# MAGIC %md
# MAGIC ## 4 · Real-time path — LakeBase search + governed FM rationale
# MAGIC `advisor.recommend_substitutes()` does the live embed → **LakeBase pgvector KNN** → score →
# MAGIC **governed FM** rationale. Requires the Databricks CLI + Lakebase set up (02/03/04). If this
# MAGIC environment lacks the CLI, run `advisor.py` from your laptop; the batch view above shows the
# MAGIC identical ranking.

# COMMAND ----------
try:
    from advisor import SubstituteAdvisor, AdvisorConfig
    wid = dbutils.widgets.get("warehouse_id")
    assert wid, "set the warehouse_id widget (Pro/Serverless) to run the real-time path"
    advisor = SubstituteAdvisor(AdvisorConfig(catalog=CATALOG, schema=SCHEMA,
                                              warehouse_id=wid, chat_endpoint=CHAT))
    result = advisor.recommend_substitutes(STORE, PRODUCT, k=5)
    print("RATIONALE:", result.get("rationale"))
    print("SPEND    :", result.get("spend"))
    display(spark.createDataFrame(result["substitutes"]))
    _rt_ok = True
except Exception as e:
    print(f"[real-time path skipped in this environment] {type(e).__name__}: {e}")
    print("→ run advisor.py locally, or use the batch view in step 3 (same ranking).")
    _rt_ok = False

# COMMAND ----------
# MAGIC %md
# MAGIC ### The governed FM call, shown natively
# MAGIC The rationale goes through `np-substitute-llm` — the AI-Gateway endpoint with usage tracking +
# MAGIC rate limits. Here it is as a bare `ai_query` (same endpoint the advisor uses):

# COMMAND ----------
display(spark.sql(f"""
  SELECT ai_query('{CHAT}',
    'In one sentence, why substitute the Summit Down Parka stockout with a similar cold-weather '
    || 'layer that is in markdown-risk overstock? Mention filling demand AND clearing markdown.'
  ) AS rationale"""))

# COMMAND ----------
# MAGIC %md
# MAGIC ## 5 · Manager approves → action today

# COMMAND ----------
if _rt_ok:
    top = result["substitutes"][0]
    action = advisor.approve(STORE, PRODUCT, chosen_move="substitute",
                             approved_by=spark.sql("SELECT current_user()").collect()[0][0],
                             substitute=top, rationale=result.get("rationale"))
    print("APPROVED:", action)
    display(spark.sql(f"SELECT * FROM {CATALOG}.{SCHEMA}.gold_recovery_actions ORDER BY approved_at DESC"))
else:
    print("Approval writes to Lakebase substitute_actions + Delta gold_recovery_actions "
          "once the real-time path is connected (see advisor.approve).")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 6 · Governance — bounded & attributable spend
# MAGIC The assistant can't run open-ended: `np-substitute-llm` has AI Gateway **rate limits**, and
# MAGIC **usage tracking** attributes cost per principal.

# COMMAND ----------
# MAGIC %md
# MAGIC ```bash
# MAGIC # AI Gateway config on the endpoint:
# MAGIC databricks serving-endpoints get np-substitute-llm --profile fevmrkmsb \
# MAGIC   | jq '{rate_limits: .ai_gateway.rate_limits, usage: .ai_gateway.usage_tracking_config}'
# MAGIC ```

# COMMAND ----------
# per-principal / per-endpoint spend (tracked automatically once usage tracking is on)
try:
    display(spark.sql("""
      SELECT served_entity_name, count(*) AS calls, sum(total_tokens) AS tokens
      FROM system.serving.endpoint_usage
      WHERE served_entity_name LIKE 'np-substitute%'
      GROUP BY served_entity_name"""))
except Exception as e:
    print(f"[system.serving.endpoint_usage not readable here] {e}")
