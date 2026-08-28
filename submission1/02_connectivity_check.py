#!/usr/bin/env python3
"""Connectivity check against the Lakebase dev branch (committed evidence).

Proves the instance-defined-in-code is actually reachable and running:
opens a real connection, reads server version + key extensions + current db,
and writes the result to results/lakebase_connectivity_result.json.

Usage:  python 02_connectivity_check.py [branch]   (default: geniebandits-dev)
"""
import json
import sys
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import lb  # noqa: E402

BRANCH = sys.argv[1] if len(sys.argv) > 1 else "geniebandits-dev"
RESULT = Path(__file__).parent / "results" / "lakebase_connectivity_result.json"


def main() -> None:
    conn = lb.connect(BRANCH)
    cur = conn.cursor()
    cur.execute("SELECT version();")
    version = cur.fetchone()[0]
    cur.execute("SELECT current_database(), current_user, now();")
    db, user, now = cur.fetchone()
    cur.execute(
        "SELECT extname, extversion FROM pg_extension ORDER BY extname;"
    )
    exts = {name: ver for name, ver in cur.fetchall()}
    cur.close()
    conn.close()

    result = {
        "checked_at": datetime.datetime.utcnow().isoformat() + "Z",
        "branch": BRANCH,
        "endpoint_host": lb.host_for(BRANCH),
        "connected": True,
        "server_version": version,
        "current_database": db,
        "current_user": user,
        "server_now": str(now),
        "installed_extensions": exts,
    }
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
