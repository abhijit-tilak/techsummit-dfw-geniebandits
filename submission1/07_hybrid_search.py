#!/usr/bin/env python3
"""Lakebase hybrid search: full-text (tsvector/GIN) + vector (pgvector/HNSW),
fused with Reciprocal Rank Fusion, over the operational products text.

Answers a natural-language query — the semantic arm surfaces relevant items even
when the exact words differ, the lexical arm nails exact term matches, and RRF
blends them. Writes search_query.txt and results/search_result.json.

Usage:  python 07_hybrid_search.py ["natural language query"]
"""
import json
import re
import subprocess
import sys
import datetime
from pathlib import Path

_STOP = {"for", "the", "and", "with", "of", "to", "in", "on", "a", "an"}

sys.path.insert(0, str(Path(__file__).parent))
from lib import lb  # noqa: E402

HERE = Path(__file__).parent
PROFILE = "rkm-sandbox-1"
BRANCH = "geniebandits-dev"
DEFAULT_QUERY = "warm waterproof insulated coat for freezing winter weather"

RRF_SQL = """
WITH ft AS (
  SELECT product_id,
         row_number() OVER (
           ORDER BY ts_rank_cd(search_document, to_tsquery('english', %(tsq)s)) DESC
         ) AS rank
  FROM northpeak_ops.products
  WHERE search_document @@ to_tsquery('english', %(tsq)s)
  LIMIT 50
),
vec AS (
  SELECT product_id,
         row_number() OVER (ORDER BY embedding <=> %(qvec)s::vector) AS rank
  FROM northpeak_ops.products
  WHERE embedding IS NOT NULL
  LIMIT 50
),
fused AS (
  SELECT COALESCE(ft.product_id, vec.product_id) AS product_id,
         ft.rank  AS ft_rank,
         vec.rank AS vec_rank,
         COALESCE(1.0/(60+ft.rank), 0) + COALESCE(1.0/(60+vec.rank), 0) AS rrf_score
  FROM ft FULL OUTER JOIN vec ON ft.product_id = vec.product_id
)
SELECT p.product_id, p.product_name, p.category, p.seasonality,
       f.ft_rank, f.vec_rank, round(f.rrf_score::numeric, 5) AS rrf_score
FROM fused f JOIN northpeak_ops.products p ON p.product_id = f.product_id
ORDER BY f.rrf_score DESC
LIMIT 10;
"""


def query_embedding(text: str) -> str:
    out = subprocess.check_output(
        ["databricks", "experimental", "aitools", "tools", "query",
         f"SELECT to_json(ai_query('databricks-gte-large-en', '{text}')) AS emb",
         "--profile", PROFILE, "-o", "json"],
        text=True,
    )
    vec = json.loads(json.loads(out)[0]["emb"])
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def main() -> None:
    q = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUERY
    qvec = query_embedding(q)
    # lexical arm: OR the meaningful terms so any lexical match contributes
    terms = [w for w in re.findall(r"[A-Za-z]+", q.lower()) if len(w) > 2 and w not in _STOP]
    tsq = " | ".join(terms)

    conn = lb.connect(BRANCH)
    cur = conn.cursor()
    cur.execute(RRF_SQL, {"tsq": tsq, "qvec": qvec})
    cols = [d[0] for d in cur.description]
    hits = [dict(zip(cols, row)) for row in cur.fetchall()]
    cur.close()
    conn.close()

    (HERE / "search_query.txt").write_text(q + "\n")
    result = {
        "ran_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "branch": BRANCH,
        "natural_language_query": q,
        "lexical_tsquery": tsq,
        "method": "hybrid: full-text (tsvector/GIN) + vector (pgvector/HNSW), RRF k=60",
        "embedding_model": "databricks-gte-large-en",
        "top_results": hits,
    }
    (HERE / "results" / "search_result.json").write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
