"""
NorthPeak — Substitute Finder : load embeddings + metrics from Delta into Lakebase.

Reads gold_product_embeddings ⋈ gold_product_markdown_profile from Delta (via the SQL
warehouse) and upserts the 200 product rows into Lakebase `product_catalog` (pgvector).
Idempotent (ON CONFLICT DO UPDATE). Run AFTER 01_* (Delta tables) and 03_* (Lakebase schema).

    python 04_load_lakebase.py --profile fevmrkmsb --warehouse <id> \
        --catalog rkm_sandbox_1_catalog \
        --schema demo_workshop_northpeak_retail_stockout_markdown_rescue

Dependencies: databricks-sdk, psycopg2-binary.
"""
from __future__ import annotations

import argparse
import json
import subprocess

import psycopg2
from psycopg2.extras import execute_values
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState


def fetch_delta(w: WorkspaceClient, warehouse: str, catalog: str, schema: str) -> list[list]:
    sql = f"""
        SELECT e.product_id, e.product_name, e.category, e.subcategory, e.seasonality,
               e.unit_cost, e.unit_price, e.unit_margin, e.margin_pct,
               COALESCE(m.is_markdown_risk, false)        AS is_markdown_risk,
               COALESCE(m.position_status, 'healthy')     AS position_status,
               COALESCE(m.overstock_on_hand_units, 0)     AS overstock_on_hand_units,
               COALESCE(m.overstock_store_count, 0)       AS overstock_store_count,
               COALESCE(m.markdown_exposure_usd, 0)       AS markdown_exposure_usd,
               COALESCE(m.avg_daily_velocity, 0)          AS avg_daily_velocity,
               e.embedding
        FROM {catalog}.{schema}.gold_product_embeddings e
        LEFT JOIN {catalog}.{schema}.gold_product_markdown_profile m USING (product_id)
    """
    resp = w.statement_execution.execute_statement(statement=sql, warehouse_id=warehouse,
                                                    wait_timeout="50s")
    if resp.status and resp.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"read failed: {resp.status.state} {getattr(resp.status,'error',None)}")
    return resp.result.data_array or []


def pg_connect(profile: str, project: str, branch: str, endpoint: str, db: str):
    def cli(*a):
        return subprocess.run(["databricks", *a, "--profile", profile, "-o", "json"],
                              capture_output=True, text=True, check=True).stdout
    br = f"projects/{project}/branches/{branch}"
    ep = f"{br}/endpoints/{endpoint}"
    host = json.loads(cli("postgres", "list-endpoints", br))[0]["status"]["hosts"]["host"]
    token = json.loads(cli("postgres", "generate-database-credential", ep))["token"]
    email = json.loads(cli("current-user", "me"))["userName"]
    return psycopg2.connect(host=host, port=5432, dbname=db, user=email,
                            password=token, sslmode="require")


def to_vec(cell) -> str:
    arr = json.loads(cell) if isinstance(cell, str) else cell
    return "[" + ",".join(repr(float(x)) for x in arr) + "]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="fevmrkmsb")
    ap.add_argument("--warehouse", required=True)
    ap.add_argument("--catalog", default="rkm_sandbox_1_catalog")
    ap.add_argument("--schema", default="demo_workshop_northpeak_retail_stockout_markdown_rescue")
    ap.add_argument("--project", default="dbdemos-asset-generator")
    ap.add_argument("--branch", default="rkm-substitute-advisor")
    ap.add_argument("--endpoint", default="primary-rw")
    ap.add_argument("--db", default="northpeak")
    args = ap.parse_args()

    w = WorkspaceClient(profile=args.profile)
    print("▶ reading products from Delta …")
    rows = fetch_delta(w, args.warehouse, args.catalog, args.schema)
    print(f"  • {len(rows)} products")

    values = []
    for r in rows:
        (pid, name, cat, sub, seas, cost, price, margin, mpct, mrisk, status,
         oh, ostores, mexp, vel, emb) = r
        values.append((pid, name, cat, sub, seas,
                       float(cost or 0), float(price or 0), float(margin or 0), float(mpct or 0),
                       str(mrisk).lower() in ("true", "1", "t"), status,
                       int(float(oh or 0)), int(float(ostores or 0)),
                       float(mexp or 0), float(vel or 0), to_vec(emb)))

    conn = pg_connect(args.profile, args.project, args.branch, args.endpoint, args.db)
    cur = conn.cursor()
    print("▶ upserting into Lakebase product_catalog …")
    execute_values(cur, """
        INSERT INTO product_catalog
          (product_id, product_name, category, subcategory, seasonality,
           unit_cost, unit_price, unit_margin, margin_pct,
           is_markdown_risk, position_status, overstock_on_hand_units,
           overstock_store_count, markdown_exposure_usd, avg_daily_velocity, embedding)
        VALUES %s
        ON CONFLICT (product_id) DO UPDATE SET
           product_name = EXCLUDED.product_name, category = EXCLUDED.category,
           subcategory = EXCLUDED.subcategory, seasonality = EXCLUDED.seasonality,
           unit_cost = EXCLUDED.unit_cost, unit_price = EXCLUDED.unit_price,
           unit_margin = EXCLUDED.unit_margin, margin_pct = EXCLUDED.margin_pct,
           is_markdown_risk = EXCLUDED.is_markdown_risk, position_status = EXCLUDED.position_status,
           overstock_on_hand_units = EXCLUDED.overstock_on_hand_units,
           overstock_store_count = EXCLUDED.overstock_store_count,
           markdown_exposure_usd = EXCLUDED.markdown_exposure_usd,
           avg_daily_velocity = EXCLUDED.avg_daily_velocity,
           embedding = EXCLUDED.embedding, updated_at = now()
    """, values, template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)", page_size=100)
    conn.commit()
    cur.execute("SELECT count(*), count(*) FILTER (WHERE is_markdown_risk) FROM product_catalog")
    total, mrisk = cur.fetchone()
    cur.close(); conn.close()
    print(f"✓ loaded {total} products ({mrisk} markdown-risk) into Lakebase product_catalog")


if __name__ == "__main__":
    main()
