#!/usr/bin/env python3
"""Build 2, Step 1 — Visualize.

Applies migration 003 to the Build 1 dev branch (geniebandits-dev), then runs the
ranked/flagged decision view and captures its rows. Never writes the synced table.

Outputs:
  results/view_result.json   the rows backing the live decision view
"""
import json
import sys
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import lb  # noqa: E402

HERE = Path(__file__).parent
BRANCH = "geniebandits-dev"


def main() -> None:
    conn = lb.connect(BRANCH)
    cur = conn.cursor()
    cur.execute((HERE / "migrations" / "003_decision_loop.sql").read_text())
    conn.commit()

    sql = (HERE / "view_query.sql").read_text()
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    needs = [r for r in rows if r["flag"] == "NEEDS_DECISION"]
    cur.close()
    conn.close()

    result = {
        "ran_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "branch": BRANCH,
        "source_table_read_only": "public.store_sku_position_synced",
        "view": "northern cold-weather stockouts, ranked by lost-sales exposure, flagged",
        "row_count": len(rows),
        "needs_decision_count": len(needs),
        "rows": rows,
    }
    (HERE / "results" / "view_result.json").write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps({"row_count": len(rows), "needs_decision": len(needs),
                      "top": rows[0] if rows else None}, indent=2, default=str))


if __name__ == "__main__":
    main()
