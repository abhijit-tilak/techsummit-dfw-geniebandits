#!/usr/bin/env python3
"""Apply migration 002 (hybrid search DDL) and load product embeddings.

Generates 1024-dim embeddings for every product in Delta via
ai_query('databricks-gte-large-en', ...) — the governed FM path — then loads them
into northpeak_ops.products.embedding on the dev branch. The full-text tsvector is
a generated column, so it populates automatically.

Writes results/hybrid_search_index_result.json (index + coverage evidence).
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
    cur = conn.cursor()

    # 1) hybrid-search DDL (tsvector + GIN, vector + HNSW).
    #    CREATE EXTENSION must commit before the vector type is usable in the
    #    same batch, so install it first in autocommit, then run the migration.
    conn.autocommit = True
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.autocommit = False
    cur.execute((HERE / "migrations" / "002_hybrid_search.sql").read_text())
    conn.commit()

    # 2) generate embeddings in Delta (governed FM), one JSON array per product
    rows = delta(
        "SELECT product_id, "
        "to_json(ai_query('databricks-gte-large-en', "
        "  concat_ws(' ', product_name, category, seasonality))) AS emb "
        f"FROM {CATALOG}.{SCHEMA}.raw_products"
    )

    # 3) load into Postgres as pgvector literals
    for r in rows:
        vec = json.loads(r["emb"])          # list[float] length 1024
        literal = "[" + ",".join(repr(float(x)) for x in vec) + "]"
        cur.execute(
            "UPDATE northpeak_ops.products SET embedding = %s::vector WHERE product_id = %s",
            (literal, r["product_id"]),
        )
    conn.commit()

    # ---- evidence ----
    cur.execute("SELECT count(*) FROM northpeak_ops.products WHERE embedding IS NOT NULL")
    n_emb = cur.fetchone()[0]
    cur.execute(
        "SELECT count(*) FROM northpeak_ops.products "
        "WHERE search_document @@ plainto_tsquery('english', 'jacket')"
    )
    n_ft = cur.fetchone()[0]
    cur.execute(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname='northpeak_ops' AND tablename='products' "
        "AND (indexdef ILIKE '%hnsw%' OR indexdef ILIKE '%gin%')"
    )
    indexes = [{"name": r[0], "def": r[1]} for r in cur.fetchall()]
    result = {
        "loaded_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "branch": BRANCH,
        "embedding_model": "databricks-gte-large-en",
        "embedding_dims": 1024,
        "products_with_embedding": n_emb,
        "products_matching_fulltext_jacket": n_ft,
        "indexes": indexes,
    }
    (HERE / "results" / "hybrid_search_index_result.json").write_text(json.dumps(result, indent=2))
    cur.close()
    conn.close()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
