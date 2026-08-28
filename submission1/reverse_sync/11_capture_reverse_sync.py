#!/usr/bin/env python3
"""Capture reverse Lakehouse Sync evidence — run AFTER enabling the UI sync.

Makes a couple of writes on the Lakebase writable table (an insert + a status
update), waits for CDC to stream them into the Unity Catalog SCD Type 2 history
table, then reads that Delta history back and writes:

  results/reverse_sync_sample.json   sample streamed rows (Postgres -> Delta)
  results/reverse_scd2_result.json   SCD2 change history + system metadata cols

Usage:
  python reverse_sync/11_capture_reverse_sync.py --catalog <DEST_CATALOG> --schema <DEST_SCHEMA>
"""
import argparse
import json
import subprocess
import sys
import time
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib import lb  # noqa: E402

HERE = Path(__file__).parent.parent
PROFILE = "rkm-sandbox-1"
HISTORY_TABLE = "lb_recovery_actions_history"


def delta(query: str) -> list[dict]:
    out = subprocess.check_output(
        ["databricks", "experimental", "aitools", "tools", "query", query,
         "--profile", PROFILE, "-o", "json"],
        text=True,
    )
    return json.loads(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True, help="destination UC catalog chosen in the UI")
    ap.add_argument("--schema", required=True, help="destination UC schema chosen in the UI")
    ap.add_argument("--branch", default="geniebandits-dev")
    args = ap.parse_args()
    hist = f"{args.catalog}.{args.schema}.{HISTORY_TABLE}"

    # 1) generate a change to capture: insert an action, then update its status
    conn = lb.connect(args.branch)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO northpeak_ops.recovery_actions "
        "(store_id, product_id, chosen_move, units, net_recaptured_value, status, approved_by) "
        "VALUES ('STORE-0002','SKU-APP-04413','expedite',25,18250.0,'pending','reverse.sync.demo') "
        "RETURNING action_id"
    )
    action_id = cur.fetchone()[0]
    time.sleep(2)
    cur.execute(
        "UPDATE northpeak_ops.recovery_actions SET status='approved', approved_by='reverse.sync.demo' "
        "WHERE action_id=%s",
        (action_id,),
    )
    cur.close()
    conn.close()

    # 2) wait for CDC to land the change in the Delta history table
    rows = []
    for _ in range(30):
        try:
            rows = delta(
                f"SELECT action_id, status, _pg_change_type, _pg_lsn, _pg_xid, _timestamp, _sort_by "
                f"FROM {hist} WHERE action_id = {action_id} ORDER BY _sort_by"
            )
        except subprocess.CalledProcessError:
            rows = []
        if rows:
            break
        time.sleep(10)

    sample = delta(
        f"SELECT action_id, store_id, product_id, chosen_move, status, "
        f"_pg_change_type, _pg_lsn, _timestamp FROM {hist} ORDER BY _sort_by DESC LIMIT 10"
    )

    (HERE / "results" / "reverse_sync_sample.json").write_text(json.dumps({
        "captured_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "history_table": hist,
        "direction": "Lakebase Postgres -> Unity Catalog Delta (CDC)",
        "sample_rows": sample,
    }, indent=2, default=str))

    (HERE / "results" / "reverse_scd2_result.json").write_text(json.dumps({
        "captured_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "history_table": hist,
        "tracked_action_id": action_id,
        "scd2_note": "insert then update yields insert / update_preimage / update_postimage "
                     "rows; system metadata columns _pg_change_type/_pg_lsn/_pg_xid/"
                     "_timestamp/_sort_by are present.",
        "change_history": rows,
    }, indent=2, default=str))
    print(json.dumps({"tracked_action_id": action_id, "history_rows": len(rows),
                      "sample_rows": len(sample)}, indent=2))


if __name__ == "__main__":
    main()
