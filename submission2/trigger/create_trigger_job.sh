#!/usr/bin/env bash
# =====================================================================================
# Scheduled decision trigger — created as code (Build 2, Step 1 differentiator)
# =====================================================================================
# Uploads the trigger notebook and creates a scheduled Databricks Job that re-scores
# the live decision view and writes a trigger event into northpeak_ops.workflow_state.
# A schedule/system update — not a person opening the view — fires this.
#
#   schedule : daily 06:00 UTC (quartz cron), UNPAUSED
#   compute  : serverless (notebook task, no cluster)
#   writes   : workflow_state (event_type='trigger', trigger_source='scheduled_job',
#              job_run_ref={{job.run_id}}, flagged_count)
# Idempotent: resets the job settings if the job already exists.
# =====================================================================================
set -euo pipefail
PROFILE="${PROFILE:-rkm-sandbox-1}"
WSDIR="${WSDIR:-/Workspace/Users/abhijit.tilak@databricks.com/northpeak-build2}"
HERE="$(cd "$(dirname "$0")" && pwd)"

databricks workspace mkdirs "$WSDIR" --profile "$PROFILE"
databricks workspace import "$WSDIR/decision_trigger" \
  --file "$HERE/decision_trigger_notebook.py" --format SOURCE --language PYTHON \
  --overwrite --profile "$PROFILE"

JOB_JSON=$(cat <<JSON
{
  "name": "northpeak-decision-trigger",
  "tasks": [{
    "task_key": "rescore_and_flag",
    "notebook_task": {
      "notebook_path": "$WSDIR/decision_trigger", "source": "WORKSPACE",
      "base_parameters": {"run_id": "{{job.run_id}}"}
    }
  }],
  "schedule": {"quartz_cron_expression": "0 0 6 * * ?", "timezone_id": "UTC", "pause_status": "UNPAUSED"},
  "max_concurrent_runs": 1
}
JSON
)

EXISTING=$(databricks jobs list --profile "$PROFILE" -o json | python3 -c "
import sys,json
for j in json.load(sys.stdin):
    if j.get('settings',{}).get('name')=='northpeak-decision-trigger': print(j['job_id']); break
")
if [[ -n "$EXISTING" ]]; then
  databricks jobs reset --profile "$PROFILE" --json "{\"job_id\": $EXISTING, \"new_settings\": $JOB_JSON}"
  echo "reset job $EXISTING"
else
  databricks jobs create --profile "$PROFILE" --json "$JOB_JSON"
fi
