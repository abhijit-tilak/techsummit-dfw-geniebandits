#!/usr/bin/env python3
"""Apply migration 001 to the dev branch, load dimensions from the governed Delta
tables, seed writable recovery actions, and capture evidence.

Everything here targets the geniebandits-dev branch — production stays clean until
09_promote_to_production.py runs. Dimension data is pulled from the SAME governed
UC tables the lakehouse already owns, so the operational FKs resolve to real stores
and products.

Writes results/operational_schema_result.json (table row counts, FK list, samples)
and results/writable_proof.json (an INSERT + a status UPDATE that fires the audit
trigger — proof these tables are writable, unlike the read-only synced table).
"""
import json
import subprocess
import sys
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import lb  # noqa: E402

HERE = Path(__file__).parent
PROFILE = "rkm-sandbox-1"
CATALOG = "rkm_sandbox_1_catalog"
SCHEMA = "demo_workshop_northpeak_retail_stockout_markdown_rescue"
BRANCH = "geniebandits-dev"


def delta(query: str) -> list[dict]:
    out = subprocess.check_output(
        ["databricks", "experimental", "aitools", "tools", "query", query,
         "--profile", PROFILE, "-o", "json"],
        text=True,
    )
    return json.loads(out)


def main() -> None:
    conn = lb.connect(BRANCH)
    conn.autocommit = False
    cur = conn.cursor()

    # 1) schema + tables + keys
    cur.execute((HERE / "migrations" / "001_operational_schema.sql").read_text())
    conn.commit()

    # 2) load dimensions from governed Delta
    stores = delta(
        f"SELECT store_id, store_name, region, climate_zone, city, latitude, longitude "
        f"FROM {CATALOG}.{SCHEMA}.raw_stores"
    )
    cur.executemany(
        "INSERT INTO northpeak_ops.stores "
        "(store_id, store_name, region, climate_zone, city, latitude, longitude) "
        "VALUES (%(store_id)s,%(store_name)s,%(region)s,%(climate_zone)s,%(city)s,"
        "%(latitude)s,%(longitude)s) ON CONFLICT (store_id) DO NOTHING",
        stores,
    )
    products = delta(
        f"SELECT product_id, product_name, category, seasonality, unit_cost, unit_price "
        f"FROM {CATALOG}.{SCHEMA}.raw_products"
    )
    cur.executemany(
        "INSERT INTO northpeak_ops.products "
        "(product_id, product_name, category, seasonality, unit_cost, unit_price) "
        "VALUES (%(product_id)s,%(product_name)s,%(category)s,%(seasonality)s,"
        "%(unit_cost)s,%(unit_price)s) ON CONFLICT (product_id) DO NOTHING",
        products,
    )
    conn.commit()

    # 3) seed a representative approved transfer (hero: Summit Down Parka -> STORE-0001).
    #    Pick a real overstock southern source store for the same SKU from Delta.
    src = delta(
        f"SELECT store_id FROM {CATALOG}.{SCHEMA}.gold_store_sku_position "
        f"WHERE product_id='SKU-APP-04412' AND position_status='overstock' "
        f"ORDER BY on_hand_units DESC LIMIT 1"
    )
    source_store = src[0]["store_id"] if src else None
    cur.execute(
        "INSERT INTO northpeak_ops.recovery_actions "
        "(store_id, product_id, chosen_move, source_store_id, units, "
        " net_recaptured_value, status, approved_by) "
        "VALUES ('STORE-0001','SKU-APP-04412','transfer',%s,40,31070.18,'pending','priya.raghavan') "
        "RETURNING action_id",
        (source_store,),
    )
    action_id = cur.fetchone()[0]
    conn.commit()

    # 4) writable proof: UPDATE status -> fires the audit trigger into history
    cur.execute(
        "UPDATE northpeak_ops.recovery_actions SET status='approved', approved_by='priya.raghavan' "
        "WHERE action_id=%s",
        (action_id,),
    )
    conn.commit()

    # ---- evidence: operational schema ----
    cur.execute("SELECT count(*) FROM northpeak_ops.stores")
    n_stores = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM northpeak_ops.products")
    n_products = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM northpeak_ops.recovery_actions")
    n_actions = cur.fetchone()[0]
    cur.execute(
        "SELECT tc.constraint_name, tc.table_name, kcu.column_name, "
        "       ccu.table_name AS ref_table, ccu.column_name AS ref_column "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema "
        "JOIN information_schema.constraint_column_usage ccu "
        "  ON ccu.constraint_name=tc.constraint_name AND ccu.table_schema=tc.table_schema "
        "WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='northpeak_ops' "
        "ORDER BY tc.table_name, kcu.column_name"
    )
    fks = [
        {"constraint": r[0], "table": r[1], "column": r[2],
         "references": f"{r[3]}({r[4]})"}
        for r in cur.fetchall()
    ]
    schema_result = {
        "applied_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "branch": BRANCH,
        "schema": "northpeak_ops",
        "row_counts": {"stores": n_stores, "products": n_products,
                       "recovery_actions": n_actions},
        "foreign_keys": fks,
    }
    (HERE / "results" / "operational_schema_result.json").write_text(
        json.dumps(schema_result, indent=2)
    )

    # ---- evidence: writable proof + audit history ----
    cur.execute(
        "SELECT action_id, store_id, product_id, chosen_move, source_store_id, "
        "units, net_recaptured_value, status, approved_by "
        "FROM northpeak_ops.recovery_actions WHERE action_id=%s",
        (action_id,),
    )
    cols = [d[0] for d in cur.description]
    action_row = dict(zip(cols, cur.fetchone()))
    cur.execute(
        "SELECT old_status, new_status, changed_by FROM northpeak_ops.action_status_history "
        "WHERE action_id=%s ORDER BY history_id",
        (action_id,),
    )
    history = [{"old_status": r[0], "new_status": r[1], "changed_by": r[2]}
               for r in cur.fetchall()]
    writable = {
        "written_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "branch": BRANCH,
        "note": "recovery_actions + action_status_history are writable OLTP tables, "
                "distinct from the read-only synced table public.store_sku_position_synced.",
        "inserted_and_updated_action": {k: (float(v) if isinstance(v, (int,)) and k=='net_recaptured_value' else v)
                                        for k, v in action_row.items()},
        "audit_history_from_trigger": history,
    }
    (HERE / "results" / "writable_proof.json").write_text(json.dumps(writable, indent=2, default=str))

    cur.close()
    conn.close()
    print(json.dumps(schema_result, indent=2))
    print(json.dumps(writable, indent=2, default=str))


if __name__ == "__main__":
    main()
