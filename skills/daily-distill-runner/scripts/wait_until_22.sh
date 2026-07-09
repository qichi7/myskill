#!/usr/bin/env bash
# daily-distill-runner: block until 22:00.
#
# Pure bash sleep, no AI token consumed. Polls every 10 minutes.
# If called from within the 22:00-24:00 window (e.g. task just finished at 23:30),
# waits until midnight passes, then waits for the next 22:00.
#
# Usage:
#   wait_until_22.sh
#
# Output:
#   TRIGGER   — 22:00 reached, caller should start the task
#   TIMEOUT   — safety max wait exceeded (25h), caller should abort
#
# Exit codes:
#   0  TRIGGER
#   1  TIMEOUT or error

set -euo pipefail

MAX_WAIT_SEC=90000    # 25 hours safety cap
POLL_INTERVAL=600     # 10 minutes

start_epoch=$(date +%s)

# Track whether we started inside the 22:00-24:00 window.
# If so, we must wait until the window ends (midnight) before
# waiting for the next 22:00, to avoid re-triggering same night.
started_in_window=0
start_hour=$(date +%H | sed 's/^0//')
if (( start_hour >= 22 )); then
  started_in_window=1
fi

while true; do
  now_epoch=$(date +%s)
  elapsed=$(( now_epoch - start_epoch ))
  if (( elapsed > MAX_WAIT_SEC )); then
    echo "[wait] ERROR: exceeded max wait ${MAX_WAIT_SEC}s. Aborting."
    echo "TIMEOUT"
    exit 1
  fi

  hour=$(date +%H | sed 's/^0//')

  if (( hour >= 22 )); then
    if (( started_in_window == 0 )); then
      echo "[wait] Trigger window reached: $(date '+%Y-%m-%d %H:%M')."
      echo "TRIGGER"
      exit 0
    fi
    # Still in the same window we started in — keep waiting for midnight.
  else
    # Out of window (00:00–21:59). Reset flag so next 22:00 triggers.
    started_in_window=0
  fi

  sleep "$POLL_INTERVAL"
done
