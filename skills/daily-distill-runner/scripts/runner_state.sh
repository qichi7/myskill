#!/usr/bin/env bash
# daily-distill-runner: run state management.
#
# Only manages per-day execution logs.
#
# Usage:
#   runner_state.sh init                  Create state dir structure
#   runner_state.sh log <message>         Append a timestamped line to today's log

set -euo pipefail

STATE_DIR="${HOME}/.cache/daily-distill-runner"
LOGS_DIR="${STATE_DIR}/logs"

mkdir -p "$LOGS_DIR"

today_date() { date +%Y-%m-%d; }
now_iso()    { date '+%Y-%m-%d %H:%M:%S'; }

log_line() {
  local msg="$1"
  local logfile="${LOGS_DIR}/$(today_date).log"
  echo "[$(now_iso)] ${msg}" >> "$logfile"
}

do_init() {
  mkdir -p "$LOGS_DIR"
  echo "[state] Initialized at ${STATE_DIR}"
}

case "${1:-help}" in
  init) do_init ;;
  log)  shift; log_line "$*" ;;
  *)
    echo "Usage: runner_state.sh {init|log <msg>}"
    exit 1
    ;;
esac
