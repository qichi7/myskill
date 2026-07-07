#!/usr/bin/env bash
# daily-distill-runner: block until 22:00 on a date after LAST_RUN_DATE.
#
# Used by the main orchestrator loop to wait for the nightly task window
# WITHOUT consuming AI tokens (pure bash sleep). The AI agent calls this
# with a long bash timeout (e.g. 86400000 ms = 24h); the script polls
# every 10 minutes and exits as soon as the trigger window is reached.
#
# Usage:
#   wait_until_22.sh [last_run_date]
#     last_run_date  YYYY-MM-DD of the last successful trigger.
#                    Prevents same-date re-triggering. Omit to wait for
#                    the next 22:00 regardless of date.
#
# Output:
#   TRIGGER   — window reached, caller should start the task
#   TIMEOUT   — safety max wait exceeded (25h), caller should abort
#
# Exit codes:
#   0  TRIGGER
#   1  TIMEOUT or error

set -euo pipefail

LAST_RUN_DATE="${1:-}"

MAX_WAIT_SEC=90000    # 25 hours safety cap
POLL_INTERVAL=600     # 10 minutes

start_epoch=$(date +%s)

while true; do
  now_epoch=$(date +%s)
  elapsed=$(( now_epoch - start_epoch ))
  if (( elapsed > MAX_WAIT_SEC )); then
    echo "[wait] ERROR: exceeded max wait ${MAX_WAIT_SEC}s. Aborting."
    echo "TIMEOUT"
    exit 1
  fi

  today=$(date +%Y-%m-%d)
  hour=$(date +%H | sed 's/^0//')   # strip leading zero for arithmetic

  if (( hour >= 22 )); then
    if [[ -z "$LAST_RUN_DATE" || "$today" != "$LAST_RUN_DATE" ]]; then
      echo "[wait] Trigger window reached: ${today} $(date +%H:%M)."
      echo "[wait] last_run_date=${LAST_RUN_DATE:-<none>}, today=${today}"
      echo "TRIGGER"
      exit 0
    fi
    # Same date as last run — keep waiting for tomorrow's 22:00
  fi

  sleep "$POLL_INTERVAL"
done
