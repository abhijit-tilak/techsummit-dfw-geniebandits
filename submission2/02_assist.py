#!/usr/bin/env python3
"""Build 2, Step 2 — Assist.

For the top NEEDS_DECISION row on the live view, the assistant:
  1. EXPLAINS why it is flagged and why the prescribed move (transfer) wins,
  2. answers a WHAT-IF (substitute instead of transfer?), retrieving substitute
     candidates from the Build 1 Lakebase Search index (hybrid vector+full-text),
  3. DRAFTS the transfer memo.

Retrieval uses the Build 1 hybrid index on northpeak_ops.products — not a separate
vector store. FM calls go through the governed AI Gateway endpoint (lib/fm.py).

Outputs:
  results/assist_log.jsonl   one line per interaction (request + response)
  drafted_sample.md          the auto-drafted memo
  results/hero.json          the resolved hero row + linked ids (used by Act step)
"""
import json
import re
import subprocess
import sys
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import lb, fm  # noqa: E402

HERE = Path(__file__).parent
BRANCH = "geniebandits-dev"
PROFILE = "rkm-sandbox-1"
HERO_SKU = "SKU-APP-04412"  # Summit Down Parka
_STOP = {"for", "the", "and", "with", "of", "to", "in", "on", "a", "an"}
LOG = HERE / "results" / "assist_log.jsonl"


def query_embedding(text: str) -> str:
    out = subprocess.check_output(
        ["databricks", "experimental", "aitools", "tools", "query",
         f"SELECT to_json(ai_query('databricks-gte-large-en', '{text}')) AS emb",
         "--profile", PROFILE, "-o", "json"], text=True)
    vec = json.loads(json.loads(out)[0]["emb"])
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def hybrid_substitutes(cur, query_text: str, exclude_id: str, k: int = 5):
    """Retrieve substitute candidates from the Build 1 hybrid search index (RRF)."""
    qvec = query_embedding(query_text)
    terms = [w for w in re.findall(r"[A-Za-z]+", query_text.lower()) if len(w) > 2 and w not in _STOP]
    tsq = " | ".join(terms)
    cur.execute(
        """
        WITH ft AS (
          SELECT product_id, row_number() OVER (
                   ORDER BY ts_rank_cd(search_document, to_tsquery('english', %(tsq)s)) DESC) AS rank
          FROM northpeak_ops.products
          WHERE search_document @@ to_tsquery('english', %(tsq)s) AND product_id <> %(ex)s LIMIT 50),
        vec AS (
          SELECT product_id, row_number() OVER (ORDER BY embedding <=> %(qvec)s::vector) AS rank
          FROM northpeak_ops.products WHERE embedding IS NOT NULL AND product_id <> %(ex)s LIMIT 50),
        fused AS (
          SELECT COALESCE(ft.product_id, vec.product_id) AS product_id,
                 COALESCE(1.0/(60+ft.rank),0)+COALESCE(1.0/(60+vec.rank),0) AS rrf
          FROM ft FULL OUTER JOIN vec ON ft.product_id=vec.product_id)
        SELECT p.product_id, p.product_name, p.seasonality, p.unit_price, p.unit_margin
        FROM fused f JOIN northpeak_ops.products p ON p.product_id=f.product_id
        ORDER BY f.rrf DESC LIMIT %(k)s
        """,
        {"tsq": tsq, "qvec": qvec, "ex": exclude_id, "k": k},
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def log(kind, request, response, retrieved=None):
    rec = {"ts": datetime.datetime.now(datetime.UTC).isoformat(), "kind": kind,
           "endpoint": fm.ENDPOINT, "request": request, "response": response}
    if retrieved is not None:
        rec["retrieved_from_lakebase_search"] = retrieved
    with LOG.open("a") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def main() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if LOG.exists():
        LOG.unlink()

    conn = lb.connect(BRANCH)
    cur = conn.cursor()

    # hero = top NEEDS_DECISION northern parka stockout
    cur.execute(
        """
        SELECT s.store_id, st.city, s.product_id, p.product_name,
               s.on_hand_units, round(s.lost_sales_exposure_usd::numeric,2) AS exposure,
               round(s.avg_daily_velocity::numeric,2) AS velocity
        FROM public.store_sku_position_synced s
        JOIN northpeak_ops.stores st ON st.store_id=s.store_id
        JOIN northpeak_ops.products p ON p.product_id=s.product_id
        LEFT JOIN northpeak_ops.recovery_actions ra
               ON ra.store_id=s.store_id AND ra.product_id=s.product_id AND ra.committed_at IS NOT NULL
        WHERE s.position_status='stockout' AND st.climate_zone='North'
          AND p.seasonality='cold_weather' AND s.product_id=%s AND ra.action_id IS NULL
        ORDER BY s.lost_sales_exposure_usd DESC LIMIT 1
        """, (HERO_SKU,))
    cols = [d[0] for d in cur.description]
    hero = dict(zip(cols, cur.fetchone()))

    # best transfer source: southern overstock store for same SKU
    cur.execute(
        """SELECT s.store_id, st.city, s.on_hand_units
           FROM public.store_sku_position_synced s JOIN northpeak_ops.stores st ON st.store_id=s.store_id
           WHERE s.product_id=%s AND s.position_status='overstock' AND st.climate_zone='South'
           ORDER BY s.on_hand_units DESC LIMIT 1""", (HERO_SKU,))
    src = cur.fetchone()
    source = {"store_id": src[0], "city": src[1], "on_hand_units": src[2]} if src else None

    # substitutes from Build 1 Lakebase Search (hybrid)
    subs = hybrid_substitutes(cur, f"{hero['product_name']} warm insulated cold weather substitute", HERO_SKU)

    ctx = (f"Store {hero['store_id']} ({hero['city']}) is STOCKED OUT of {hero['product_name']} "
           f"({hero['product_id']}); daily velocity {hero['velocity']} units, annualized lost-sales "
           f"exposure ${hero['exposure']}. Candidate transfer source: store {source['store_id']} "
           f"({source['city']}) holding {source['on_hand_units']} overstock units. Substitute candidates "
           f"(from Lakebase hybrid search): " + ", ".join(f"{s['product_name']} (margin ${s['unit_margin']:.2f})" for s in subs) + ".")

    # 1) EXPLAIN
    m1 = [{"role": "system", "content": "You are NorthPeak's store-ops recovery advisor. Be concise and quantitative."},
          {"role": "user", "content": ctx + "\n\nExplain in 3-4 sentences WHY this row is flagged for action and WHY a transfer from the overstock store is the recommended recovery move over doing nothing."}]
    explain = fm.chat(m1)
    log("explanation", m1, explain, retrieved=[s["product_id"] for s in subs])
    print("EXPLAIN:\n", explain, "\n")

    # 2) WHAT-IF (uses retrieved substitutes)
    m2 = [{"role": "system", "content": "You are NorthPeak's store-ops recovery advisor. Be concise and quantitative."},
          {"role": "user", "content": ctx + "\n\nWHAT-IF: instead of a transfer, we offer the top substitute product to shoppers. Using the substitute candidates above, name the best substitute and explain the trade-off vs the transfer in 3-4 sentences."}]
    whatif = fm.chat(m2)
    log("what_if", m2, whatif, retrieved=[s["product_id"] for s in subs])
    print("WHAT-IF:\n", whatif, "\n")

    # 3) DRAFT MEMO
    units = max(1, round(hero["velocity"] * 14))  # ~2 weeks of demand
    m3 = [{"role": "system", "content": "You draft short, professional internal store-ops transfer memos."},
          {"role": "user", "content": ctx + f"\n\nDraft a concise transfer memo (markdown) proposing to transfer {units} units of {hero['product_name']} from store {source['store_id']} to store {hero['store_id']}, for a manager to approve. Include: subject line, situation, recommended action, expected recaptured value, and an approval line."}]
    memo = fm.chat(m3, max_tokens=900)
    log("memo_draft", m3, memo)
    (HERE / "drafted_sample.md").write_text(memo + "\n")
    print("MEMO drafted -> drafted_sample.md")

    hero_out = {"hero": hero, "transfer_source": source, "proposed_units": units,
                "substitute_candidates": subs}
    (HERE / "results" / "hero.json").write_text(json.dumps(hero_out, indent=2, default=str))
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
