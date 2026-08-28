#!/usr/bin/env python3
"""Generate gateway_usage.lvdash.json — the AI/BI dashboard tracking usage +
budgets across the app, the coding agent, and the MCP, from the governed
inference tables."""
import json
from pathlib import Path

C = "rkm_sandbox_1_catalog"
S = "demo_workshop_northpeak_retail_stockout_markdown_rescue"
APP = f"{C}.{S}.app_inference_table"
AGENT = f"{C}.{S}.agent_inference_table"
OUT = Path(__file__).parent.parent / "gateway_usage.lvdash.json"

DS_SQL = f"""SELECT source, endpoint, decision, request_ts,
       COALESCE(actual_cost_usd, est_cost_usd, 0) AS cost_usd,
       CASE WHEN decision LIKE '%block%' THEN 1 ELSE 0 END AS blocked
FROM (
  SELECT source, endpoint, decision, request_ts, actual_cost_usd, est_cost_usd FROM {APP}
  UNION ALL
  SELECT source, endpoint, decision, request_ts, actual_cost_usd, est_cost_usd FROM {AGENT}
)"""


def q(fields):
    return [{"name": "main_query", "query": {"datasetName": "ds_gateway_usage",
             "fields": fields, "disaggregated": False}}]


def counter(name, expr, alias, title, fmt_money=False):
    fmt = {"type": "number", "abbreviation": "compact", "decimalPlaces": {"type": "max", "places": 4 if fmt_money else 0}}
    if fmt_money:
        fmt["type"] = "number-currency"; fmt["currencyCode"] = "USD"
    return {"name": name, "queries": q([{"name": alias, "expression": expr}]),
            "spec": {"version": 2, "widgetType": "counter",
                     "encodings": {"value": {"fieldName": alias, "displayName": title, "format": fmt}},
                     "frame": {"showTitle": True, "title": title}}}


def bar(name, dim_field, dim_expr, dim_disp, val_expr, val_alias, title, color_map=None):
    enc = {"x": {"fieldName": dim_field, "displayName": dim_disp, "scale": {"type": "categorical"}},
           "y": {"fieldName": val_alias, "displayName": "Calls", "scale": {"type": "quantitative"}}}
    fields = [{"name": dim_field, "expression": dim_expr}, {"name": val_alias, "expression": val_expr}]
    if color_map:
        enc["color"] = {"fieldName": dim_field, "displayName": dim_disp,
                        "scale": {"type": "categorical", "mappings": color_map}}
    return {"name": name, "queries": q(fields),
            "spec": {"version": 3, "widgetType": "bar", "frame": {"showTitle": True, "title": title},
                     "encodings": enc}}


def table(name, title):
    fields = [{"name": c, "expression": f"`{c}`"} for c in
              ("source", "endpoint", "decision", "cost_usd", "request_ts")]
    return {"name": name, "queries": q(fields),
            "spec": {"version": 1, "widgetType": "table", "frame": {"showTitle": True, "title": title},
                     "encodings": {"columns": [{"fieldName": f["name"], "displayName": f["name"]} for f in fields]}}}


dash = {
    "datasets": [{"name": "ds_gateway_usage", "displayName": "Gateway usage (app + agent + MCP)",
                  "queryLines": [l + "\n" for l in DS_SQL.splitlines()]}],
    "pages": [{
        "name": "usage", "displayName": "AI Gateway Usage & Budgets", "pageType": "PAGE_TYPE_CANVAS",
        "layoutVersion": 2,
        "layout": [
            {"widget": {"name": "title", "multilineTextboxSpec": {"lines": [
                "# NorthPeak — Unity Gateway: Usage & Budgets\nBounded, visible, attributable AI spend across the app, the coding agent, and the MCP."]}},
             "position": {"x": 0, "y": 0, "width": 12, "height": 2}},
            {"widget": counter("c_calls", "COUNT(`request_ts`)", "total_calls", "Total governed calls"),
             "position": {"x": 0, "y": 2, "width": 3, "height": 3}},
            {"widget": counter("c_cost", "SUM(`cost_usd`)", "total_cost", "Total AI spend ($)", fmt_money=True),
             "position": {"x": 3, "y": 2, "width": 3, "height": 3}},
            {"widget": counter("c_blocked", "SUM(`blocked`)", "blocked_calls", "Calls blocked (budget+guardrail)"),
             "position": {"x": 6, "y": 2, "width": 3, "height": 3}},
            {"widget": counter("c_sources", "COUNT(DISTINCT `source`)", "sources", "Governed sources (app/agent/mcp)"),
             "position": {"x": 9, "y": 2, "width": 3, "height": 3}},
            {"widget": bar("b_source", "source", "`source`", "Source", "COUNT(`request_ts`)", "calls",
                           "Calls by source (app / coding_agent / mcp)"),
             "position": {"x": 0, "y": 5, "width": 4, "height": 5}},
            {"widget": bar("b_endpoint", "endpoint", "`endpoint`", "Gateway endpoint", "SUM(`cost_usd`)", "cost",
                           "Spend by governed endpoint ($)"),
             "position": {"x": 4, "y": 5, "width": 4, "height": 5}},
            {"widget": bar("b_decision", "decision", "`decision`", "Decision", "COUNT(`request_ts`)", "calls",
                           "Decisions (allowed vs blocked)",
                           color_map=[{"value": "allowed", "color": "#3C6997"},
                                      {"value": "budget_block", "color": "#E5484D"},
                                      {"value": "guardrail_block", "color": "#FFB020"}]),
             "position": {"x": 8, "y": 5, "width": 4, "height": 5}},
            {"widget": table("t_calls", "Recent governed calls (investigate via inference tables)"),
             "position": {"x": 0, "y": 10, "width": 12, "height": 6}},
        ],
    }],
    "uiSettings": {"theme": {"widgetHeaderAlignment": "ALIGNMENT_UNSPECIFIED"}},
}

OUT.write_text(json.dumps(dash, indent=2))
print("wrote", OUT)
