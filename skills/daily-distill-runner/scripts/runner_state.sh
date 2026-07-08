#!/usr/bin/env bash
# daily-distill-runner: run state management.
#
# Manages two things:
#   1. runner_state.json  — last run date (used by wait_until_22.sh)
#   2. logs/{date}.log    — per-day execution logs
#
# Usage:
#   runner_state.sh init                  Create state dir structure
#   runner_state.sh set_last_run <date>   Set last_run_date in state
#   runner_state.sh get_last_run          Print last_run_date (empty if none)
#   runner_state.sh log <message>         Append a timestamped line to today's log

set -euo pipefail

STATE_DIR="${HOME}/.cache/daily-distill-runner"
STATE_FILE="${STATE_DIR}/runner_state.json"
LOGS_DIR="${STATE_DIR}/logs"

mkdir -p "$LOGS_DIR"

today_date() { date +%Y-%m-%d; }
now_iso()    { date '+%Y-%m-%d %H:%M:%S'; }

write_state() {
  local last_run="$1"
  python3 -c "
import json
print(json.dumps({'last_run_date': '$last_run'}))
" > "$STATE_FILE"
}

read_field() {
  local field="$1"
  if [[ ! -f "$STATE_FILE" ]]; then echo ""; return; fi
  python3 -c "
import json
try:
    with open('$STATE_FILE') as f:
        d = json.load(f)
    v = d.get('$field', '')
    print(v if v is not None else '')
except Exception:
    print('')
"
}

set_last_run() {
  local date_val="$1"
  write_state "$date_val"
  echo "[state] last_run_date set to: ${date_val}"
}

get_last_run() {
  read_field "last_run_date"
}

log_line() {
  local msg="$1"
  local logfile="${LOGS_DIR}/$(today_date).log"
  echo "[$(now_iso)] ${msg}" >> "$logfile"
}

do_init() {
  mkdir -p "$LOGS_DIR"
  if [[ ! -f "$STATE_FILE" ]]; then
    write_state ""
  fi
  echo "[state] Initialized at ${STATE_DIR}"
}

case "${1:-help}" in
  init)         do_init ;;
  set_last_run) shift; set_last_run "${1:?date required}" ;;
  get_last_run) get_last_run ;;
  log)          shift; log_line "$*" ;;
  *)
    echo "Usage: runner_state.sh {init|set_last_run <date>|get_last_run|log <msg>}"
    exit 1
    ;;
esac
