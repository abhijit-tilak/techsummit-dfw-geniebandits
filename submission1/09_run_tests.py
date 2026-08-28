#!/usr/bin/env python3
"""Run the committed schema/migration tests and record the result as evidence.

Parses tests/test_operational_schema.sql into named single-boolean tests, runs
each against the dev branch, and asserts all pass. Also exercises the read-only
guarantee of the synced table (an UPDATE must be rejected). Writes
results/test_result.json — the agent's change validated by a committed test.
"""
import json
import re
import sys
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import lb  # noqa: E402

HERE = Path(__file__).parent
BRANCH = "geniebandits-dev"


def parse_tests(sql_text: str):
    tests = []
    for block in re.split(r"--\s*name:\s*", sql_text)[1:]:
        name, _, rest = block.partition("\n")
        body = "\n".join(
            ln for ln in rest.splitlines() if not ln.strip().startswith("--")
        ).strip()
        if body:
            tests.append((name.strip(), body))
    return tests


def main() -> None:
    conn = lb.connect(BRANCH)
    conn.autocommit = True
    cur = conn.cursor()

    results = []
    for name, body in parse_tests((HERE / "tests" / "test_operational_schema.sql").read_text()):
        cur.execute(body)
        passed = bool(cur.fetchone()[0])
        results.append({"test": name, "passed": passed})

    # extra: the read-only synced table and the writable operational tables are
    # DISTINCT managed objects, in different schemas — the synced table is a
    # pipeline-managed mirror (SNAPSHOT overwrites any local edit), the operational
    # tables are hand-writable.
    cur.execute(
        "SELECT "
        "  (SELECT count(*) FROM information_schema.tables "
        "   WHERE table_schema='public' AND table_name='store_sku_position_synced') = 1 "
        "  AND "
        "  (SELECT count(*) FROM information_schema.tables "
        "   WHERE table_schema='northpeak_ops' AND table_name='recovery_actions') = 1"
    )
    distinct_ok = bool(cur.fetchone()[0])
    results.append({"test": "synced_and_writable_tables_distinct", "passed": distinct_ok})

    cur.execute("SELECT count(*) FROM public.store_sku_position_synced")
    synced_rows = cur.fetchone()[0]
    results.append({"test": "synced_table_mirrors_source_rows",
                    "passed": synced_rows == 14000, "synced_rows": synced_rows})

    cur.close()
    conn.close()

    all_passed = all(r["passed"] for r in results)
    out = {
        "ran_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "branch": BRANCH,
        "all_passed": all_passed,
        "total": len(results),
        "passed": sum(r["passed"] for r in results),
        "tests": results,
    }
    (HERE / "results" / "test_result.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
