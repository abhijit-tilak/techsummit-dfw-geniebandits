"""
NorthPeak — Governed, AI-Assisted Substitute Finder (real-time module)
======================================================================

Given a stockout (store_id, product_id), find semantically-similar products sitting in
markdown-risk overstock and rank them by a net-value score — so one substitution fills the
demand AND clears markdown inventory. Every FM call is governed by Unity AI Gateway.

Tech stack (as required):
  • LakeBase search     — pgvector KNN over product_catalog (cosine `<=>`, HNSW index)
  • real-time inference — ai_query embedding + ai_query rationale, synchronous per request
  • ai_query FM         — embeddings: databricks-gte-large-en ; rationale: np-substitute-llm
  • AI Gateway          — np-substitute-llm has usage tracking + rate limits (bounded, tracked)

Ranking (uses replacement margin + markdown margin/exposure):
  similarity      = 1 - cosine(embed(query), embed(candidate))            # from LakeBase
  demand_units    = avg_daily_velocity(store, product) * HORIZON_DAYS
  expected_units  = min(demand_units, candidate.overstock_on_hand_units)
  capture_margin  = expected_units * candidate.unit_margin * similarity   # replacement margin
  markdown_saved  = expected_units * candidate.unit_price * MARKDOWN_DEPTH # if markdown-risk
  substitution_score = W_CAPTURE*capture_margin + W_MARKDOWN*markdown_saved

Dependencies: databricks-sdk, psycopg2-binary. Auth: a --profile in ~/.databrickscfg.
"""
from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass, field, asdict
from typing import Any

import psycopg2
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem, StatementState


# ─────────────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────────────
@dataclass
class AdvisorConfig:
    profile: str = "fevmrkmsb"
    warehouse_id: str = ""                     # Pro/Serverless — required for ai_query
    catalog: str = "rkm_sandbox_1_catalog"
    schema: str = "demo_workshop_northpeak_retail_stockout_markdown_rescue"

    # FM endpoints (through Unity AI Gateway)
    embed_endpoint: str = "databricks-gte-large-en"   # 1024-dim, pay-per-token, tracked
    chat_endpoint: str = "np-substitute-llm"          # GOVERNED: usage tracking + rate limits

    # Lakebase (isolated branch — shared production is untouched)
    lakebase_project: str = "dbdemos-asset-generator"
    lakebase_branch: str = "rkm-substitute-advisor"
    lakebase_endpoint: str = "primary-rw"
    lakebase_db: str = "northpeak"

    # Ranking params (keep in sync with 06_gold_substitute_recommendations.sql)
    horizon_days: int = 14
    markdown_depth: float = 0.40
    w_capture: float = 1.0
    w_markdown: float = 1.0

    # spend transparency (approx pay-per-token price used only for the demo panel)
    approx_chat_usd_per_1k_tokens: float = 0.0005


# ─────────────────────────────────────────────────────────────────────────────────────
class SubstituteAdvisor:
    def __init__(self, cfg: AdvisorConfig):
        self.cfg = cfg
        self._w = WorkspaceClient(profile=cfg.profile)
        self._pg = None  # lazy

    # ── ai_query (real-time FM inference through AI Gateway) ────────────────────────────
    def _sql(self, statement: str, params: list[StatementParameterListItem] | None = None) -> list[list[Any]]:
        if not self.cfg.warehouse_id:
            raise ValueError("warehouse_id is required (Pro/Serverless) for ai_query calls")
        resp = self._w.statement_execution.execute_statement(
            statement=statement,
            warehouse_id=self.cfg.warehouse_id,
            parameters=params or [],
            wait_timeout="50s",
        )
        if resp.status and resp.status.state != StatementState.SUCCEEDED:
            raise RuntimeError(f"SQL failed: {resp.status.state} {getattr(resp.status,'error',None)}")
        return (resp.result.data_array if resp.result and resp.result.data_array else [])

    def embed(self, text: str) -> list[float]:
        """Real-time embedding via ai_query on the FM endpoint (tracked in system.serving)."""
        rows = self._sql(
            f"SELECT ai_query('{self.cfg.embed_endpoint}', :t) AS e",
            [StatementParameterListItem(name="t", value=text)],
        )
        raw = rows[0][0]
        return [float(x) for x in (json.loads(raw) if isinstance(raw, str) else raw)]

    def _rationale(self, ctx: dict, top: dict) -> tuple[str, dict]:
        """One-line manager-facing rationale via the GOVERNED endpoint (bounded + tracked)."""
        prompt = (
            "You are a retail store-ops assistant. In ONE sentence, tell the manager why this "
            "substitute is the right recovery move. Mention it fills the stockout AND clears "
            "markdown-risk inventory. Be specific and concise.\n"
            f"Stockout: {ctx['product_name']} at {ctx['store_id']} "
            f"(losing ~${ctx['lost_sales_exposure_usd']:,.0f}).\n"
            f"Substitute: {top['product_name']} ({top['subcategory']}), "
            f"similarity {top['similarity']:.2f}, markdown-risk={top['is_markdown_risk']}, "
            f"captures ~${top['capture_margin']:,.0f} margin and protects "
            f"~${top['markdown_saved']:,.0f} of markdown exposure."
        )
        rows = self._sql(
            f"SELECT ai_query('{self.cfg.chat_endpoint}', :p) AS r",
            [StatementParameterListItem(name="p", value=prompt)],
        )
        text = str(rows[0][0]).strip()
        approx_tokens = (len(prompt) + len(text)) / 4.0
        spend = {
            "endpoint": self.cfg.chat_endpoint,
            "governed": True,
            "approx_tokens": round(approx_tokens),
            "approx_cost_usd": round(approx_tokens / 1000 * self.cfg.approx_chat_usd_per_1k_tokens, 6),
            "note": "bounded by AI Gateway rate limits; per-principal cost in system.serving.endpoint_usage",
        }
        return text, spend

    # ── Lakebase (pgvector) search ──────────────────────────────────────────────────────
    def _conn(self):
        if self._pg is not None:
            return self._pg
        ep = (f"projects/{self.cfg.lakebase_project}/branches/{self.cfg.lakebase_branch}"
              f"/endpoints/{self.cfg.lakebase_endpoint}")
        br = f"projects/{self.cfg.lakebase_project}/branches/{self.cfg.lakebase_branch}"

        def cli(*args) -> str:
            out = subprocess.run(["databricks", *args, "--profile", self.cfg.profile, "-o", "json"],
                                 capture_output=True, text=True, check=True).stdout
            return out

        host = json.loads(cli("postgres", "list-endpoints", br))[0]["status"]["hosts"]["host"]
        token = json.loads(cli("postgres", "generate-database-credential", ep))["token"]
        email = json.loads(cli("current-user", "me"))["userName"]
        self._pg = psycopg2.connect(host=host, port=5432, dbname=self.cfg.lakebase_db,
                                    user=email, password=token, sslmode="require")
        return self._pg

    def _knn(self, qvec: list[float], exclude_id: str, retrieve_n: int) -> list[dict]:
        vec = "[" + ",".join(repr(float(x)) for x in qvec) + "]"
        sql = """
            SELECT product_id, product_name, category, subcategory,
                   unit_price, unit_margin, is_markdown_risk, position_status,
                   overstock_on_hand_units, markdown_exposure_usd,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM product_catalog
            WHERE product_id <> %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        cur = self._conn().cursor()
        cur.execute(sql, (vec, exclude_id, vec, retrieve_n))
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        return rows

    # ── shortfall context (surface the shortfall) ──────────────────────────────────────
    def _shortfall(self, store_id: str, product_id: str) -> dict:
        rows = self._sql(
            f"""SELECT product_name, avg_daily_velocity, lost_sales_exposure_usd, position_status,
                       concat_ws(' | ', product_name, category, seasonality) AS product_text
                FROM {self.cfg.catalog}.{self.cfg.schema}.gold_store_sku_position
                WHERE store_id = :s AND product_id = :p LIMIT 1""",
            [StatementParameterListItem(name="s", value=store_id),
             StatementParameterListItem(name="p", value=product_id)],
        )
        if not rows:
            raise ValueError(f"no position row for {store_id}/{product_id}")
        pn, vel, lost, status, text = rows[0]
        return {"store_id": store_id, "product_id": product_id, "product_name": pn,
                "avg_daily_velocity": float(vel or 0), "lost_sales_exposure_usd": float(lost or 0),
                "position_status": status, "product_text": text}

    # ── the net-value ranking ───────────────────────────────────────────────────────────
    def _score(self, ctx: dict, cand: dict) -> dict:
        c = self.cfg
        demand_units = ctx["avg_daily_velocity"] * c.horizon_days
        expected = min(demand_units, float(cand["overstock_on_hand_units"] or 0))
        sim = float(cand["similarity"] or 0)
        capture_margin = expected * float(cand["unit_margin"] or 0) * sim
        markdown_saved = (expected * float(cand["unit_price"] or 0) * c.markdown_depth
                          if cand["is_markdown_risk"] else 0.0)
        score = c.w_capture * capture_margin + c.w_markdown * markdown_saved
        return {
            "product_id": cand["product_id"], "product_name": cand["product_name"],
            "subcategory": cand["subcategory"], "position_status": cand["position_status"],
            "is_markdown_risk": bool(cand["is_markdown_risk"]),
            "similarity": round(sim, 4),
            "expected_units": round(expected, 1),
            "unit_margin": round(float(cand["unit_margin"] or 0), 2),
            "capture_margin": round(capture_margin, 2),
            "markdown_saved": round(markdown_saved, 2),
            "substitution_score": round(score, 2),
        }

    # ── public API ──────────────────────────────────────────────────────────────────────
    def recommend_substitutes(self, store_id: str, product_id: str,
                              k: int = 5, retrieve_n: int = 20,
                              with_rationale: bool = True) -> dict:
        ctx = self._shortfall(store_id, product_id)                 # surface the shortfall
        qvec = self.embed(ctx["product_text"])                       # real-time embedding (ai_query)
        candidates = self._knn(qvec, product_id, retrieve_n)         # LakeBase search
        ranked = sorted((self._score(ctx, c) for c in candidates),
                        key=lambda r: (r["substitution_score"], r["similarity"]), reverse=True)[:k]
        out = {"shortfall": ctx, "substitutes": ranked, "params": {
            "horizon_days": self.cfg.horizon_days, "markdown_depth": self.cfg.markdown_depth,
            "w_capture": self.cfg.w_capture, "w_markdown": self.cfg.w_markdown}}
        if with_rationale and ranked:
            out["rationale"], out["spend"] = self._rationale(ctx, ranked[0])
        return out

    def approve(self, store_id: str, product_id: str, chosen_move: str, approved_by: str,
                substitute: dict | None = None, rationale: str | None = None) -> dict:
        """Manager approval → write-back to Lakebase + mirror to Delta (action today)."""
        sub = substitute or {}
        conn = self._conn()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO substitute_actions
                 (store_id, stockout_product_id, chosen_move, substitute_product_id,
                  substitution_score, similarity, capture_margin, markdown_saved, rationale, approved_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING action_id, approved_at""",
            (store_id, product_id, chosen_move, sub.get("product_id"),
             sub.get("substitution_score"), sub.get("similarity"), sub.get("capture_margin"),
             sub.get("markdown_saved"), rationale, approved_by),
        )
        action_id, approved_at = cur.fetchone()
        conn.commit(); cur.close()

        # mirror to Delta so the demo's gold layer / dashboard sees approved actions
        t = f"{self.cfg.catalog}.{self.cfg.schema}.gold_recovery_actions"
        self._sql(f"""CREATE TABLE IF NOT EXISTS {t} (
                        action_id BIGINT, store_id STRING, stockout_product_id STRING,
                        chosen_move STRING, substitute_product_id STRING,
                        substitution_score DOUBLE, rationale STRING,
                        approved_by STRING, approved_at TIMESTAMP)""")
        self._sql(
            f"INSERT INTO {t} VALUES (:id,:s,:p,:m,:sub,:sc,:r,:by, current_timestamp())",
            [StatementParameterListItem(name="id", value=str(action_id)),
             StatementParameterListItem(name="s", value=store_id),
             StatementParameterListItem(name="p", value=product_id),
             StatementParameterListItem(name="m", value=chosen_move),
             StatementParameterListItem(name="sub", value=sub.get("product_id")),
             StatementParameterListItem(name="sc", value=str(sub.get("substitution_score") or 0)),
             StatementParameterListItem(name="r", value=rationale or ""),
             StatementParameterListItem(name="by", value=approved_by)],
        )
        return {"action_id": action_id, "approved_at": str(approved_at),
                "store_id": store_id, "chosen_move": chosen_move,
                "substitute_product_id": sub.get("product_id")}


# ─────────────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse, pprint
    ap = argparse.ArgumentParser(description="NorthPeak substitute finder (real-time)")
    ap.add_argument("--profile", default="fevmrkmsb")
    ap.add_argument("--warehouse", required=True, help="Pro/Serverless warehouse id (ai_query)")
    ap.add_argument("--store", default="STORE-0214")
    ap.add_argument("--product", default="SKU-APP-04412")
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()

    advisor = SubstituteAdvisor(AdvisorConfig(profile=args.profile, warehouse_id=args.warehouse))
    result = advisor.recommend_substitutes(args.store, args.product, k=args.k)
    pprint.pp(result)
