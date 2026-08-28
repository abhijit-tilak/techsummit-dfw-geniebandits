#!/usr/bin/env python3
"""Throwaway forecasting branch — a disposable what-if, isolated from dev/prod.

The geniebandits-forecast branch is a copy-on-write clone of dev with a 4h TTL.
Here we run a "cold snap intensifies +50%" scenario: write a scenario table and
recompute projected lost-sales exposure, WITHOUT touching dev or production. When
the TTL expires the branch (and this experiment) simply vanishes — an idle
throwaway branch costs close to nothing.

Writes results/forecast_throwaway_result.json, including a check that dev is
untouched by this experiment.
"""
import json
import sys
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import lb  # noqa: E402

HERE = Path(__file__).parent
UPLIFT = 1.5  # cold snap intensifies -> +50% demand


def main() -> None:
    fc = lb.connect("geniebandits-forecast")
    fc.autocommit = True
    cur = fc.cursor()

    # baseline (on the forecast branch's copy of the synced data)
    cur.execute(
        "SELECT round(sum(lost_sales_exposure_usd)::numeric,2) "
        "FROM public.store_sku_position_synced WHERE position_status='stockout'"
    )
    baseline = float(cur.fetchone()[0])

    # disposable what-if: a scenario table + projected exposure, only on this branch
    cur.execute("DROP TABLE IF EXISTS northpeak_ops.forecast_scenarios")
    cur.execute(
        "CREATE TABLE northpeak_ops.forecast_scenarios AS "
        "SELECT store_id, product_id, "
        "       lost_sales_exposure_usd AS baseline_exposure, "
        f"      lost_sales_exposure_usd * {UPLIFT} AS projected_exposure "
        "FROM public.store_sku_position_synced WHERE position_status='stockout'"
    )
    cur.execute(
        "SELECT round(sum(projected_exposure)::numeric,2) FROM northpeak_ops.forecast_scenarios"
    )
    projected = float(cur.fetchone()[0])
    cur.close()
    fc.close()

    # prove isolation: dev has NO forecast_scenarios table
    dev = lb.connect("geniebandits-dev")
    dcur = dev.cursor()
    dcur.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='northpeak_ops' AND table_name='forecast_scenarios'"
    )
    dev_has_scenario = dcur.fetchone()[0] > 0
    dcur.close()
    dev.close()

    result = {
        "ran_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "throwaway_branch": "geniebandits-forecast",
        "branch_ttl": "14400s (auto-expires; idle cost ~0)",
        "scenario": f"cold snap intensifies: demand x{UPLIFT}",
        "baseline_stockout_exposure_usd": baseline,
        "projected_stockout_exposure_usd": projected,
        "delta_usd": round(projected - baseline, 2),
        "isolation_check_dev_has_scenario_table": dev_has_scenario,
        "note": "The scenario table exists ONLY on the throwaway branch; dev is untouched "
                "and the branch self-destructs at TTL.",
    }
    (HERE / "results" / "forecast_throwaway_result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
