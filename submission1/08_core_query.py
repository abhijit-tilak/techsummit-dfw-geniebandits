#!/usr/bin/env python3
"""Run the core domain query and record its result + latency as evidence.

Answers a representative store-ops question by joining the read-only synced
analytics table with the writable operational tables. Records wall-clock latency
(server-side, warm) to demonstrate it is low-latency OLTP, not batch reporting.
"""
import json
import sys
import time
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import lb  # noqa: E402

HERE = Path(__file__).parent
BRANCH = "geniebandits-dev"


def main() -> None:
    sql = (HERE / "core_query.sql").read_text()
    conn = lb.connect(BRANCH)
    cur = conn.cursor()

    # warm the plan/cache, then time the answering query
    cur.execute(sql)
    cur.fetchall()
    best = None
    for _ in range(5):
        t0 = time.perf_counter()
        cur.execute(sql)
        rows = cur.fetchall()
        dt = (time.perf_counter() - t0) * 1000.0
        best = dt if best is None else min(best, dt)
    cols = [d[0] for d in cur.description]
    result_rows = [dict(zip(cols, r)) for r in rows]

    # server-side execution time (independent of client<->region network RTT)
    cur.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + sql)
    plan = cur.fetchone()[0][0]
    server_ms = round(plan.get("Execution Time", 0.0), 2)
    cur.close()
    conn.close()

    result = {
        "ran_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "branch": BRANCH,
        "question": "Which northern stores are stocked out of a cold-weather SKU with the "
                    "highest lost-sales exposure, and is a recovery move approved?",
        "server_execution_ms": server_ms,
        "client_round_trip_ms_best_of_5": round(best, 2),
        "row_count": len(result_rows),
        "rows": result_rows,
    }
    (HERE / "results" / "core_query_result.json").write_text(
        json.dumps(result, indent=2, default=str)
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
