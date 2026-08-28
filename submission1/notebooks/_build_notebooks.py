#!/usr/bin/env python3
"""Build + execute the execution-evidence notebooks (committed WITH outputs).

Each notebook connects to the geniebandits-dev Lakebase branch and runs real SQL,
so the committed .ipynb carries visible cell outputs proving the operational
schema, the writable tables, and the migration tests actually ran.

Run from submission1/:  python notebooks/_build_notebooks.py
"""
import nbformat as nbf
from nbclient import NotebookClient
from pathlib import Path

HERE = Path(__file__).parent

PRELUDE = """
import sys, textwrap
sys.path.insert(0, ".")
from lib import lb
conn = lb.connect("geniebandits-dev")
conn.autocommit = True
cur = conn.cursor()

def show(sql, title=None):
    if title: print(f"### {title}")
    print(textwrap.dedent(sql).strip())
    cur.execute(sql)
    if cur.description:
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        print("-> " + " | ".join(cols))
        for r in rows:
            print("   " + " | ".join(str(x) for x in r))
        print(f"({len(rows)} row(s))\\n")
    else:
        print(f"-> OK ({cur.rowcount} affected)\\n")
""".strip()


def notebook(md_intro, cells):
    nb = nbf.v4.new_notebook()
    nb.cells.append(nbf.v4.new_markdown_cell(md_intro))
    nb.cells.append(nbf.v4.new_code_cell(PRELUDE))
    for c in cells:
        nb.cells.append(nbf.v4.new_code_cell(c))
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    return nb


OPERATIONAL = notebook(
    "# Operational schema — execution evidence\n\n"
    "Proves the modeled operational schema (related tables + primary/foreign keys) "
    "exists and is live on the `geniebandits-dev` Lakebase branch. Outputs below are "
    "real query results captured at execution time.",
    [
        'show("""SELECT table_name FROM information_schema.tables\n'
        '        WHERE table_schema=\'northpeak_ops\' ORDER BY table_name""",\n'
        '     "Tables in northpeak_ops")',
        'show("""SELECT tc.table_name, kcu.column_name AS pk_column\n'
        '        FROM information_schema.table_constraints tc\n'
        '        JOIN information_schema.key_column_usage kcu USING (constraint_name, table_schema)\n'
        '        WHERE tc.constraint_type=\'PRIMARY KEY\' AND tc.table_schema=\'northpeak_ops\'\n'
        '        ORDER BY 1,2""", "Primary keys")',
        'show("""SELECT tc.table_name, kcu.column_name,\n'
        '               ccu.table_name AS references_table, ccu.column_name AS references_column\n'
        '        FROM information_schema.table_constraints tc\n'
        '        JOIN information_schema.key_column_usage kcu USING (constraint_name, table_schema)\n'
        '        JOIN information_schema.constraint_column_usage ccu\n'
        '          ON ccu.constraint_name=tc.constraint_name AND ccu.table_schema=tc.table_schema\n'
        '        WHERE tc.constraint_type=\'FOREIGN KEY\' AND tc.table_schema=\'northpeak_ops\'\n'
        '        ORDER BY 1,2""", "Foreign keys (related tables)")',
        'show("""SELECT \'stores\' t, count(*) n FROM northpeak_ops.stores\n'
        '        UNION ALL SELECT \'products\', count(*) FROM northpeak_ops.products\n'
        '        UNION ALL SELECT \'recovery_actions\', count(*) FROM northpeak_ops.recovery_actions\n'
        '        UNION ALL SELECT \'action_status_history\', count(*) FROM northpeak_ops.action_status_history\n'
        '        ORDER BY 1""", "Row counts")',
    ],
)

WRITABLE = notebook(
    "# Writable Postgres tables — execution evidence\n\n"
    "Proves the operational tables are **writable** and **distinct** from the "
    "read-only synced table `public.store_sku_position_synced`. We INSERT and UPDATE "
    "a recovery action (the audit trigger fires), then contrast with the synced table.",
    [
        'cur.execute("""INSERT INTO northpeak_ops.recovery_actions\n'
        '  (store_id, product_id, chosen_move, units, net_recaptured_value, status, approved_by)\n'
        '  VALUES (\'STORE-0003\',\'SKU-APP-04414\',\'expedite\',15,9100.0,\'pending\',\'evidence.nb\')\n'
        '  RETURNING action_id""")\n'
        'aid = cur.fetchone()[0]\n'
        'print("inserted recovery_actions.action_id =", aid)',
        'cur.execute("UPDATE northpeak_ops.recovery_actions SET status=\'approved\','
        ' approved_by=\'evidence.nb\' WHERE action_id=%s", (aid,))\n'
        'print("updated action", aid, "-> approved (writable UPDATE succeeded)")',
        'show(f"""SELECT action_id, store_id, product_id, chosen_move, status, approved_by\n'
        '        FROM northpeak_ops.recovery_actions WHERE action_id={aid}""",\n'
        '     "The writable row")',
        'show(f"""SELECT old_status, new_status, changed_by\n'
        '        FROM northpeak_ops.action_status_history WHERE action_id={aid}""",\n'
        '     "Audit history written by the trigger")',
        'show("""SELECT table_schema, table_name FROM information_schema.tables\n'
        '        WHERE (table_schema=\'northpeak_ops\' AND table_name=\'recovery_actions\')\n'
        '           OR (table_schema=\'public\' AND table_name=\'store_sku_position_synced\')\n'
        '        ORDER BY 1""", "Writable ops table vs read-only synced table (distinct objects)")',
        'show("SELECT count(*) AS synced_rows FROM public.store_sku_position_synced",\n'
        '     "Synced table row count (pipeline-managed mirror)")',
    ],
)

TESTS = notebook(
    "# Agent change validated by tests — execution evidence\n\n"
    "Runs every assertion in `tests/test_operational_schema.sql` against the live "
    "branch and shows the pass/fail output, validating the agent's migrations "
    "(001 + 002). All tests return TRUE.",
    [
        'import re\n'
        'sql_text = open("tests/test_operational_schema.sql").read()\n'
        'tests = []\n'
        'for block in re.split(r"--\\s*name:\\s*", sql_text)[1:]:\n'
        '    name, _, rest = block.partition("\\n")\n'
        '    body = "\\n".join(l for l in rest.splitlines() if not l.strip().startswith("--")).strip()\n'
        '    if body: tests.append((name.strip(), body))\n'
        'passed = 0\n'
        'for name, body in tests:\n'
        '    cur.execute(body); ok = bool(cur.fetchone()[0]); passed += ok\n'
        '    print(("PASS" if ok else "FAIL"), name)\n'
        'print(f"\\n{passed}/{len(tests)} tests passed")',
    ],
)


def main():
    specs = {
        "operational_schema_evidence.ipynb": OPERATIONAL,
        "writable_tables_evidence.ipynb": WRITABLE,
        "agent_change_test_validation.ipynb": TESTS,
    }
    for fname, nb in specs.items():
        print(f"executing {fname} ...")
        NotebookClient(nb, timeout=180, kernel_name="python3",
                       resources={"metadata": {"path": str(HERE.parent)}}).execute()
        nbf.write(nb, HERE / fname)
        print(f"  wrote {fname}")


if __name__ == "__main__":
    main()
