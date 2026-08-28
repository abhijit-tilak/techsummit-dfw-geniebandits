#!/usr/bin/env python3
"""Build 2, Step 3 — Act (closed loop).

The assistant PROPOSES the transfer; a PERSON reviews, CORRECTS the quantity, and
COMMITS it. The write lands in the writable table northpeak_ops.recovery_actions
(the synced table is never written). The decision is logged to workflow_state, and
the next read of the live view reflects the committed decision (closed loop).

Outputs:
  results/writeback_table.json   the action: proposed action, approval status,
                                 approver, created + committed timestamps
  results/state_table.json       workflow_state: trigger events + recorded decisions
  results/closed_loop_view.json  the hero row re-read, now COMMITTED
"""
import json
import sys
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import lb  # noqa: E402

HERE = Path(__file__).parent
BRANCH = "geniebandits-dev"
APPROVER = "priya.raghavan"


def dump(cur, sql, params=None):
    cur.execute(sql, params or ())
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> None:
    hero_data = json.loads((HERE / "results" / "hero.json").read_text())
    hero = hero_data["hero"]
    source = hero_data["transfer_source"]
    proposed_units = hero_data["proposed_units"]
    store_id, product_id = hero["store_id"], hero["product_id"]

    conn = lb.connect(BRANCH)
    conn.autocommit = False
    cur = conn.cursor()

    # idempotency: clear any prior action for this store+product (Build 1 seed / prior runs)
    cur.execute("DELETE FROM northpeak_ops.recovery_actions WHERE store_id=%s AND product_id=%s",
                (store_id, product_id))

    # 1) assistant PROPOSES (write-back #1: proposed, not yet committed)
    recaptured = round(float(hero["exposure"]), 2)
    cur.execute(
        """INSERT INTO northpeak_ops.recovery_actions
           (store_id, product_id, chosen_move, source_store_id, units, net_recaptured_value,
            status, proposed_by, proposed_at, rationale)
           VALUES (%s,%s,'transfer',%s,%s,%s,'pending','assistant',now(),
                   'Assistant-proposed transfer; see drafted_sample.md')
           RETURNING action_id, created_at""",
        (store_id, product_id, source["store_id"], proposed_units, recaptured),
    )
    action_id, created_at = cur.fetchone()
    conn.commit()

    # 2) PERSON reviews, CORRECTS the quantity (cap to a policy max), and COMMITS
    corrected_units = min(proposed_units, 60)   # manager correction before commit
    cur.execute(
        """UPDATE northpeak_ops.recovery_actions
           SET units=%s, status='approved', approved_by=%s, committed_at=now()
           WHERE action_id=%s
           RETURNING committed_at""",
        (corrected_units, APPROVER, action_id),
    )
    committed_at = cur.fetchone()[0]
    conn.commit()

    # 3) log the DECISION to workflow_state (observability / closed-loop audit)
    cur.execute(
        """INSERT INTO northpeak_ops.workflow_state
           (event_type, action_id, store_id, product_id, decision, detail)
           VALUES ('decision',%s,%s,%s,'corrected_and_approved',%s::jsonb)""",
        (action_id, store_id, product_id,
         json.dumps({"proposed_units": proposed_units, "committed_units": corrected_units,
                     "approver": APPROVER, "net_recaptured_value": recaptured})),
    )
    conn.commit()

    # ---- exports ----
    writeback = dump(cur,
        """SELECT action_id, store_id, product_id, chosen_move, source_store_id, units,
                  net_recaptured_value, status, proposed_by, approved_by,
                  created_at, proposed_at, committed_at, rationale
           FROM northpeak_ops.recovery_actions WHERE action_id=%s""", (action_id,))
    (HERE / "results" / "writeback_table.json").write_text(json.dumps({
        "exported_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "branch": BRANCH, "table": "northpeak_ops.recovery_actions (writable)",
        "note": "proposed by assistant, corrected + committed by a person; created_at and "
                "committed_at both recorded.",
        "rows": writeback}, indent=2, default=str))

    state = dump(cur,
        """SELECT event_id, event_type, trigger_source, job_run_ref, action_id, store_id,
                  product_id, decision, flagged_count, detail, created_at
           FROM northpeak_ops.workflow_state ORDER BY event_id""")
    (HERE / "results" / "state_table.json").write_text(json.dumps({
        "exported_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "branch": BRANCH, "table": "northpeak_ops.workflow_state (Lakebase observability)",
        "trigger_events": sum(1 for r in state if r["event_type"] == "trigger"),
        "decision_events": sum(1 for r in state if r["event_type"] == "decision"),
        "rows": state}, indent=2, default=str))

    # 4) closed loop: re-read the live view for the hero row -> now COMMITTED
    loop = dump(cur, (HERE / "view_query.sql").read_text().replace(
        "LIMIT 15", "") .rstrip().rstrip(";") +
        f" -- hero re-read", )
    hero_now = [r for r in loop if r["store_id"] == store_id and r["product_id"] == product_id]
    (HERE / "results" / "closed_loop_view.json").write_text(json.dumps({
        "exported_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "hero_store": store_id, "hero_product": product_id,
        "flag_after_commit": hero_now[0]["flag"] if hero_now else None,
        "row": hero_now[0] if hero_now else None}, indent=2, default=str))

    cur.close(); conn.close()
    print(json.dumps({"action_id": action_id, "proposed_units": proposed_units,
                      "committed_units": corrected_units,
                      "flag_after_commit": hero_now[0]["flag"] if hero_now else None},
                     indent=2, default=str))


if __name__ == "__main__":
    main()
