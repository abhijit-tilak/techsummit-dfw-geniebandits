#!/usr/bin/env bash
# =====================================================================================
# Lakebase branching — captured as code (NorthPeak / Genie Bandits)
# =====================================================================================
# Creates two branches off `production` on the existing Lakebase project, both
# copy-on-write and both scale-to-zero, and records the branch state as evidence:
#
#   geniebandits-dev       iterative development branch (no expiry) — where the
#                          operational schema + hybrid search are built and tested
#                          BEFORE anything touches production ("main stays clean").
#   geniebandits-forecast  THROWAWAY branch (4h TTL) off dev — used for a one-off
#                          what-if forecast, then auto-expires so an idle branch
#                          costs close to nothing.
#
# Scale-to-zero: endpoints autoscale down to 0.5 CU and suspend when idle, so an
# idle branch's compute cost trends to zero. The forecast branch additionally
# self-destructs via TTL.
#
# Idempotent: re-running skips branches/endpoints that already exist.
# =====================================================================================
set -euo pipefail

PROFILE="${PROFILE:-rkm-sandbox-1}"
PROJECT="${PROJECT:-dbdemos-asset-generator}"
PROJ="projects/$PROJECT"
HERE="$(cd "$(dirname "$0")" && pwd)"

create_branch () {   # $1 branch_id  $2 source_branch_id  $3 expiry_json
  local br="$1" src="$2" expiry="$3"
  if databricks postgres get-branch "$PROJ/branches/$br" --profile "$PROFILE" >/dev/null 2>&1; then
    echo "  • branch $br exists"
  else
    databricks postgres create-branch "$PROJ" "$br" \
      --json "{\"spec\":{\"source_branch\":\"$PROJ/branches/$src\",$expiry}}" \
      --profile "$PROFILE" >/dev/null
    echo "  ✓ created branch $br (off $src)"
  fi
}

scale_to_zero () {   # $1 branch_id  $2 min_cu  $3 max_cu
  local br="$1" mn="$2" mx="$3"
  databricks postgres update-endpoint \
    "$PROJ/branches/$br/endpoints/primary" \
    spec.autoscaling_limit_min_cu,spec.autoscaling_limit_max_cu \
    --json "{\"spec\":{\"autoscaling_limit_min_cu\":$mn,\"autoscaling_limit_max_cu\":$mx}}" \
    --profile "$PROFILE" >/dev/null
  echo "  ✓ $br endpoint autoscale $mn–$mx CU, suspends when idle (scale-to-zero)"
}

echo "▶ Dev iteration branch (permanent)"
create_branch geniebandits-dev production '"no_expiry":true'
scale_to_zero geniebandits-dev 0.5 2.0

echo "▶ Throwaway forecast branch (4h TTL)"
create_branch geniebandits-forecast geniebandits-dev '"ttl":"14400s"'
scale_to_zero geniebandits-forecast 0.5 1.0

echo "▶ Recording branch state → results/branches_result.json"
mkdir -p "$HERE/results"
databricks postgres list-branches "$PROJ" --profile "$PROFILE" -o json \
  | python3 -c "
import sys, json
out=[]
for b in json.load(sys.stdin):
    s=b.get('status',{})
    out.append({'name':b['name'],'state':s.get('current_state'),
                'expiry':'no_expiry' if s.get('no_expiry') else s.get('expire_time',''),
                'created':b.get('create_time')})
json.dump(out, open('$HERE/results/branches_result.json','w'), indent=2)
print(json.dumps(out, indent=2))
"
echo "✓ Branches provisioned as code."
