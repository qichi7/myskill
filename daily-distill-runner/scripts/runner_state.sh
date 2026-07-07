#!/usr/bin/env bash
# daily-distill-runner: checkpoint state management.
#
# Manages three things:
#   1. runner_state.json  — last run date + current segment pointer
#   2. segments/.SEG_*    — marker files for completed task segments
#   3. logs/{date}.log    — per-day execution logs
#
# Usage:
#   runner_state.sh init                  Create state dir structure
#   runner_state.sh scan                  Print JSON summary of progress
#   runner_state.sh mark <name>           Mark a segment as done
#   runner_state.sh clear                 Remove all segment markers (fresh start)
#   runner_state.sh set_last_run <date>   Set last_run_date in state
#   runner_state.sh get_last_run          Print last_run_date (empty if none)
#   runner_state.sh log <message>         Append a timestamped line to today's log

set -euo pipefail

STATE_DIR="${HOME}/.cache/daily-distill-runner"
STATE_FILE="${STATE_DIR}/runner_state.json"
SEGMENTS_DIR="${STATE_DIR}/segments"
LOGS_DIR="${STATE_DIR}/logs"

mkdir -p "$SEGMENTS_DIR" "$LOGS_DIR"

today_date() { date +%Y-%m-%d; }
now_iso()    { date '+%Y-%m-%d %H:%M:%S'; }

write_state() {
  local last_run="$1" current_seg="$2"
  python3 -c "
import json
print(json.dumps({'last_run_date': '$last_run', 'current_seg': $current_seg}))
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
  local cur_seg
  cur_seg=$(read_field "current_seg")
  [[ -z "$cur_seg" ]] && cur_seg=0
  write_state "$date_val" "$cur_seg"
  echo "[state] last_run_date set to: ${date_val}"
}

get_last_run() {
  read_field "last_run_date"
}

scan_segments() {
  local done_count=0
  local done_list=""
  if [[ -d "$SEGMENTS_DIR" ]]; then
    done_list=$(ls -1 "$SEGMENTS_DIR"/.SEG_* 2>/dev/null | xargs -n1 basename 2>/dev/null | sed 's/^\.SEG_//' | sort | tr '\n' ' ')
    done_count=$(ls -1 "$SEGMENTS_DIR"/.SEG_* 2>/dev/null | wc -l | tr -d ' ')
  fi
  local last_run
  last_run=$(read_field "last_run_date")
  python3 -c "
import json
print(json.dumps({
  'last_run_date': '$last_run' if '$last_run' else None,
  'segments_done': $done_count,
  'segments_list': '${done_list}'.strip().split(' ') if '${done_list}'.strip() else [],
  'segments_dir': '$SEGMENTS_DIR'
}))
"
  echo "[state] Segments done: ${done_count} [${done_list}]"
}

mark_segment() {
  local name="$1"
  touch "${SEGMENTS_DIR}/.SEG_${name}"
  echo "[state] Marked segment: ${name}"
}

clear_segments() {
  rm -f "$SEGMENTS_DIR"/.SEG_* 2>/dev/null || true
  write_state "$(read_field last_run_date)" "0"
  echo "[state] All segment markers cleared."
}

log_line() {
  local msg="$1"
  local logfile="${LOGS_DIR}/$(today_date).log"
  echo "[$(now_iso)] ${msg}" >> "$logfile"
}

do_init() {
  mkdir -p "$SEGMENTS_DIR" "$LOGS_DIR"
  if [[ ! -f "$STATE_FILE" ]]; then
    write_state "" "0"
  fi
  echo "[state] Initialized at ${STATE_DIR}"
}

case "${1:-help}" in
  init)         do_init ;;
  scan)         scan_segments ;;
  mark)         shift; mark_segment "${1:?segment name required}" ;;
  clear)        clear_segments ;;
  set_last_run) shift; set_last_run "${1:?date required}" ;;
  get_last_run) get_last_run ;;
  log)          shift; log_line "$*" ;;
  *)
    echo "Usage: runner_state.sh {init|scan|mark <name>|clear|set_last_run <date>|get_last_run|log <msg>}"
    exit 1
    ;;
esac
