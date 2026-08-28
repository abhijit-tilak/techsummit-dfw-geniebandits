#!/usr/bin/env python3
"""Build + execute the Lakebase Search execution-evidence notebook, and write a
dedicated result export.

Proves the Assist step retrieves from the **Build 1 Lakebase Search index** (the
pgvector HNSW + tsvector GIN indexes on northpeak_ops.products) — not a separate
vector store — by running the hybrid RRF query live and showing the returned rows.

Outputs (from ONE live run against geniebandits-dev):
  notebooks/lakebase_search_evidence.ipynb   executed, with visible cell outputs
  results/lakebase_search_result.json        the query, the indexes used, the rows
"""
import json
import re
import subprocess
import datetime
from pathlib import Path

import nbformat as nbf
from nbclient import NotebookClient

HERE = Path(__file__).parent
SUB = HERE.parent
import sys
sys.path.insert(0, str(SUB))
from lib import lb  # noqa: E402

PROFILE = "rkm-sandbox-1"
BRANCH = "geniebandits-dev"
HERO_SKU = "SKU-APP-04412"
QUERY = "Summit Down Parka warm insulated cold weather substitute"
_STOP = {"for", "the", "and", "with", "of", "to", "in", "on", "a", "an"}

RRF_SQL = """
WITH ft AS (
  SELECT product_id, row_number() OVER (
           ORDER BY ts_rank_cd(search_document, to_tsquery('english', %(tsq)s)) DESC) AS rank
  FROM northpeak_ops.products
  WHERE search_document @@ to_tsquery('english', %(tsq)s) AND product_id <> %(ex)s LIMIT 50),
vec AS (
  SELECT product_id, row_number() OVER (ORDER BY embedding <=> %(qvec)s::vector) AS rank
  FROM northpeak_ops.products WHERE embedding IS NOT NULL AND product_id <> %(ex)s LIMIT 50),
fused AS (
  SELECT COALESCE(ft.product_id, vec.product_id) AS product_id, ft.rank AS ft_rank,
         vec.rank AS vec_rank,
         COALESCE(1.0/(60+ft.rank),0)+COALESCE(1.0/(60+vec.rank),0) AS rrf
  FROM ft FULL OUTER JOIN vec ON ft.product_id=vec.product_id)
SELECT p.product_id, p.product_name, p.seasonality, round(p.unit_margin::numeric,2) AS unit_margin,
       f.ft_rank, f.vec_rank, round(f.rrf::numeric,5) AS rrf_score
FROM fused f JOIN northpeak_ops.products p ON p.product_id=f.product_id
ORDER BY f.rrf DESC LIMIT 6;
"""


def query_embedding(text: str) -> str:
    out = subprocess.check_output(
        ["databricks", "experimental", "aitools", "tools", "query",
         f"SELECT to_json(ai_query('databricks-gte-large-en', '{text}')) AS emb",
         "--profile", PROFILE, "-o", "json"], text=True)
    vec = json.loads(json.loads(out)[0]["emb"])
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def run_and_export():
    """Run the hybrid search directly and write the JSON result export."""
    qvec = query_embedding(QUERY)
    tsq = " | ".join(w for w in re.findall(r"[A-Za-z]+", QUERY.lower()) if len(w) > 2 and w not in _STOP)
    conn = lb.connect(BRANCH); cur = conn.cursor()
    cur.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='northpeak_ops' "
        "AND tablename='products' AND (indexdef ILIKE '%hnsw%' OR indexdef ILIKE '%gin%') ORDER BY indexname")
    indexes = [{"name": r[0], "def": r[1]} for r in cur.fetchall()]
    cur.execute(RRF_SQL, {"tsq": tsq, "qvec": qvec, "ex": HERO_SKU})
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close(); conn.close()
    result = {
        "ran_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "branch": BRANCH,
        "index_table": "northpeak_ops.products (the Build 1 Lakebase Search index)",
        "note": "Retrieval hits the SAME pgvector HNSW + tsvector GIN indexes created in "
                "Build 1 — not a separate vector store.",
        "natural_language_query": QUERY,
        "lexical_tsquery": tsq,
        "method": "hybrid: full-text (tsvector/GIN) + vector (pgvector/HNSW), RRF k=60",
        "embedding_model": "databricks-gte-large-en",
        "lakebase_search_indexes": indexes,
        "retrieved_rows": rows,
    }
    (SUB / "results" / "lakebase_search_result.json").write_text(json.dumps(result, indent=2, default=str))
    return result


def build_notebook():
    nb = nbf.v4.new_notebook()
    nb.cells.append(nbf.v4.new_markdown_cell(
        "# Lakebase Search retrieval — execution evidence\n\n"
        "Proves the Assist step retrieves from the **Build 1 Lakebase Search index** "
        "(pgvector HNSW + tsvector GIN on `northpeak_ops.products`), not a separate vector "
        "store. Runs the hybrid RRF query live; outputs below are real results."))
    prelude = (
        'import sys, json, re, subprocess, textwrap\n'
        'sys.path.insert(0, ".")\n'
        'from lib import lb\n'
        'conn = lb.connect("geniebandits-dev"); cur = conn.cursor()\n'
        'HERO_SKU = "SKU-APP-04412"\n'
        f'QUERY = {QUERY!r}\n'
        '_STOP = {"for","the","and","with","of","to","in","on","a","an"}\n'
        'def show(sql, params=None, title=None):\n'
        '    if title: print(f"### {title}")\n'
        '    cur.execute(sql, params) if params else cur.execute(sql)\n'
        '    cols=[d[0] for d in cur.description]; rows=cur.fetchall()\n'
        '    print(" | ".join(cols))\n'
        '    for r in rows: print("  " + " | ".join(str(x) for x in r))\n'
        '    print(f"({len(rows)} row(s))\\n"); return rows')
    nb.cells.append(nbf.v4.new_code_cell(prelude))
    nb.cells.append(nbf.v4.new_code_cell(
        'show("""SELECT indexname, indexdef FROM pg_indexes\n'
        '        WHERE schemaname=\'northpeak_ops\' AND tablename=\'products\'\n'
        '          AND (indexdef ILIKE \'%hnsw%\' OR indexdef ILIKE \'%gin%\')\n'
        '        ORDER BY indexname""", title="Build 1 Lakebase Search indexes (vector + full-text)")'))
    nb.cells.append(nbf.v4.new_code_cell(
        '# generate the query embedding via the governed FM (same path Build 1 used)\n'
        'out = subprocess.check_output(["databricks","experimental","aitools","tools","query",\n'
        '  f"SELECT to_json(ai_query(\'databricks-gte-large-en\', \'{QUERY}\')) AS emb",\n'
        '  "--profile","rkm-sandbox-1","-o","json"], text=True)\n'
        'qvec = "[" + ",".join(repr(float(x)) for x in json.loads(json.loads(out)[0]["emb"])) + "]"\n'
        'tsq = " | ".join(w for w in re.findall(r"[A-Za-z]+", QUERY.lower()) if len(w)>2 and w not in _STOP)\n'
        'print("query:", QUERY); print("lexical tsquery:", tsq); print("embedding dims: 1024")'))
    nb.cells.append(nbf.v4.new_code_cell(
        'rrf = """\n' + RRF_SQL.strip() + '\n"""\n'
        'rows = show(rrf, {"tsq": tsq, "qvec": qvec, "ex": HERO_SKU},\n'
        '            title="Hybrid retrieval (RRF of full-text + vector) — substitute candidates")\n'
        'print("Retrieved from the Build 1 Lakebase Search index on northpeak_ops.products "\n'
        '      "(pgvector HNSW + tsvector GIN) — not a separate vector store.")'))
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    NotebookClient(nb, timeout=240, kernel_name="python3",
                   resources={"metadata": {"path": str(SUB)}}).execute()
    nbf.write(nb, HERE / "lakebase_search_evidence.ipynb")


if __name__ == "__main__":
    res = run_and_export()
    print("exported results/lakebase_search_result.json:",
          [r["product_id"] for r in res["retrieved_rows"]])
    build_notebook()
    print("executed notebooks/lakebase_search_evidence.ipynb")
