#!/usr/bin/env bash
# cannbot-token-guard: monitor daily Cannbot token usage and stop when threshold or date change is reached.
#
# Two-phase workflow:
#   Phase 1 (启动检测):  check_guard.sh init      Verify Cannbot provider, record start date. Monitoring NOT active yet.
#   Phase 2 (任务开始):  check_guard.sh activate  Activate monitoring. Only after this does `check` enforce limits.
#                        check_guard.sh check     Check if task should continue (returns OK / STOP).
#   Always:              check_guard.sh status    Print current guard status.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${HOME}/.cache/cannbot-token-guard"
STATE_FILE="${STATE_DIR}/guard_state.json"
CHECK_INTERVAL_SEC=600  # 10 minutes

DAILY_TOKEN_BUDGET=100000000       # 1 亿
STOP_THRESHOLD=$((DAILY_TOKEN_BUDGET * 95 / 100))  # 9500 万 (95%)

mkdir -p "$STATE_DIR"

# ── helpers ──────────────────────────────────────────────

now_epoch()   { date +%s; }
today_date()  { date +%Y-%m-%d; }
now_iso()     { date '+%Y-%m-%d %H:%M:%S'; }

# Write JSON state using python3 (always available on macOS).
write_state() {
  local start_date="$1" last_check="$2" last_total="$3" last_cost="$4" monitoring_active="$5"
  python3 -c "
import json
data = {
  'start_date': '$start_date',
  'last_check_epoch': $last_check,
  'last_total_tokens': $last_total,
  'last_cost_usd': $last_cost,
  'monitoring_active': $monitoring_active
}
print(json.dumps(data))
" > "$STATE_FILE"
}

read_state_field() {
  local field="$1"
  if [[ ! -f "$STATE_FILE" ]]; then echo ""; return; fi
  python3 -c "
import json
try:
    with open('$STATE_FILE') as f:
        d = json.load(f)
    print(d.get('$field', ''))
except Exception:
    print('')
"
}

# Fetch today's Cannbot token usage via tokscale JSON.
# Returns: total_tokens  cannbot_found(yes/no)  cost
fetch_usage() {
  npx tokscale@latest --today --client opencode --no-spinner --json 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print("0 no 0")
    sys.exit(0)

cannbot_total = 0
cannbot_found = False
cost = 0.0
for e in data.get("entries", []):
    if e.get("provider", "").lower() == "cannbot":
        cannbot_found = True
        cannbot_total += (e.get("input", 0) + e.get("output", 0)
                          + e.get("cacheRead", 0) + e.get("reasoning", 0))
        cost += e.get("cost", 0.0)

found_str = "yes" if cannbot_found else "no"
print("%d %s %.4f" % (cannbot_total, found_str, cost))
'
}

# ── init ─────────────────────────────────────────────────

do_init() {
  echo "[guard] Initializing Cannbot token guard..."
  echo "[guard] Daily budget: ${DAILY_TOKEN_BUDGET} tokens"
  echo "[guard] Stop threshold (95%): ${STOP_THRESHOLD} tokens"

  # Fetch usage to verify cannbot is present
  local usage_result
  usage_result=$(fetch_usage)
  local total found cost
  total=$(echo "$usage_result" | awk '{print $1}')
  found=$(echo "$usage_result" | awk '{print $2}')
  cost=$(echo "$usage_result" | awk '{print $3}')

  if [[ "$found" != "yes" ]]; then
    echo "[guard] ERROR: No Cannbot provider usage detected in tokscale output."
    echo "[guard] The current key may NOT be from Cannbot. Aborting task setup."
    echo "STOP"
    return 1
  fi

  echo "[guard] OK: Cannbot provider confirmed."
  echo "[guard] Today's Cannbot usage so far: ${total} tokens (\$${cost})"

  local remaining=$((DAILY_TOKEN_BUDGET - total))
  if (( remaining < 0 )); then remaining=0; fi
  echo "[guard] Remaining budget: ${remaining} tokens"

  # Phase 1 complete: guard is armed but monitoring NOT active yet.
  # monitoring_active=0 means `check` will skip enforcement until `activate` is called.
  write_state "$(today_date)" "0" "$total" "$cost" "0"
  echo "[guard] Start date recorded: $(today_date)"
  echo "[guard] Guard ARMED. Monitoring is NOT active yet."
  echo "[guard] Run 'activate' when the actual task begins to start enforcement."
  echo "[guard] State saved to: ${STATE_FILE}"
  echo "ARMED"
}

# ── activate ─────────────────────────────────────────────

do_activate() {
  if [[ ! -f "$STATE_FILE" ]]; then
    echo "[guard] ERROR: No state file. Run 'init' first."
    echo "STOP"
    return 1
  fi

  local start_date last_total last_cost monitoring_active
  start_date=$(read_state_field "start_date")
  last_total=$(read_state_field "last_total_tokens")
  last_cost=$(read_state_field "last_cost_usd")
  monitoring_active=$(read_state_field "monitoring_active")

  if [[ "$monitoring_active" == "1" ]]; then
    echo "[guard] Monitoring already active since start_date=${start_date}."
    echo "OK"
    return 0
  fi

  # Re-verify Cannbot provider is still present at task start
  local usage_result
  usage_result=$(fetch_usage)
  local total found cost
  total=$(echo "$usage_result" | awk '{print $1}')
  found=$(echo "$usage_result" | awk '{print $2}')
  cost=$(echo "$usage_result" | awk '{print $3}')

  if [[ "$found" != "yes" ]]; then
    echo "[guard] ERROR: Cannbot provider not detected at activation time."
    echo "[guard] The current key may NOT be from Cannbot. Refusing to activate."
    echo "STOP"
    return 1
  fi

  # Check date hasn't changed since init
  local current_date
  current_date=$(today_date)
  if [[ "$current_date" != "$start_date" ]]; then
    echo "[guard] ERROR: Date changed since init (init: $start_date, now: $current_date)."
    echo "[guard] Run 'init' again to re-arm."
    echo "STOP"
    return 1
  fi

  # Activate monitoring: set monitoring_active=1 and record current usage as baseline
  write_state "$start_date" "$(now_epoch)" "$total" "$cost" "1"
  echo "[guard] Monitoring ACTIVATED at $(now_iso)."
  echo "[guard] Cannbot usage at activation: ${total} tokens (\$${cost})"

  # Immediately check if already over threshold
  if (( total >= STOP_THRESHOLD )); then
    echo "[guard] STOP: Token usage ${total} already at/over 95% threshold (${STOP_THRESHOLD})."
    echo "STOP"
    return 0
  fi

  local pct=$(( total * 100 / DAILY_TOKEN_BUDGET ))
  local remaining=$(( DAILY_TOKEN_BUDGET - total ))
  echo "[guard] ${pct}% used, ${remaining} tokens remaining."
  echo "OK"
}

# ── check ────────────────────────────────────────────────

do_check() {
  if [[ ! -f "$STATE_FILE" ]]; then
    echo "[guard] ERROR: No state file. Run 'init' first."
    echo "STOP"
    return 1
  fi

  local monitoring_active
  monitoring_active=$(read_state_field "monitoring_active")

  if [[ "$monitoring_active" != "1" ]]; then
    echo "[guard] WARNING: Monitoring not active. Run 'activate' first."
    echo "[guard] Skipping enforcement. Returning OK (no-op)."
    echo "OK"
    return 0
  fi

  local start_date last_check
  start_date=$(read_state_field "start_date")
  last_check=$(read_state_field "last_check_epoch")

  # ── Check 1: date changed? ──
  local current_date
  current_date=$(today_date)
  if [[ "$current_date" != "$start_date" ]]; then
    echo "[guard] STOP: Date changed (started $start_date, now $current_date)."
    echo "STOP"
    return 0
  fi

  # ── Enforce check interval ──
  local now last_diff
  now=$(now_epoch)
  if [[ -n "$last_check" && "$last_check" != "" ]]; then
    last_diff=$(( now - last_check ))
    if (( last_diff < CHECK_INTERVAL_SEC )); then
      local cached_total
      cached_total=$(read_state_field "last_total_tokens")
      echo "[guard] Skipping fetch (checked ${last_diff}s ago, interval ${CHECK_INTERVAL_SEC}s)."
      echo "[guard] Cached usage: ${cached_total} / ${STOP_THRESHOLD} tokens"
      if (( cached_total >= STOP_THRESHOLD )); then
        echo "[guard] STOP: Token threshold reached (cached: ${cached_total} >= ${STOP_THRESHOLD})."
        echo "STOP"
        return 0
      fi
      echo "OK"
      return 0
    fi
  fi

  # ── Check 2: fetch fresh usage ──
  local usage_result
  usage_result=$(fetch_usage)
  local total found cost
  total=$(echo "$usage_result" | awk '{print $1}')
  found=$(echo "$usage_result" | awk '{print $2}')
  cost=$(echo "$usage_result" | awk '{print $3}')

  if [[ "$found" != "yes" ]]; then
    echo "[guard] WARNING: Cannbot provider not found in this fetch. Using cached data."
    total=$(read_state_field "last_total_tokens")
    if [[ -z "$total" ]]; then total=0; fi
  fi

  echo "[guard] $(now_iso) | Cannbot today: ${total} tokens (\$${cost}) | Threshold: ${STOP_THRESHOLD}"

  write_state "$start_date" "$now" "$total" "$cost" "1"

  if (( total >= STOP_THRESHOLD )); then
    echo "[guard] STOP: Token usage ${total} reached 95% of daily budget (${STOP_THRESHOLD})."
    echo "STOP"
    return 0
  fi

  local pct=$(( total * 100 / DAILY_TOKEN_BUDGET ))
  local remaining=$(( DAILY_TOKEN_BUDGET - total ))
  echo "[guard] OK: ${pct}% used, ${remaining} tokens remaining."
  echo "OK"
}

# ── status ───────────────────────────────────────────────

do_status() {
  echo "[guard] === Cannbot Token Guard Status ==="
  echo "[guard] Daily budget: ${DAILY_TOKEN_BUDGET} tokens"
  echo "[guard] Stop threshold: ${STOP_THRESHOLD} tokens (95%)"

  if [[ -f "$STATE_FILE" ]]; then
    local start_date cached_total cached_cost monitoring_active
    start_date=$(read_state_field "start_date")
    cached_total=$(read_state_field "last_total_tokens")
    cached_cost=$(read_state_field "last_cost_usd")
    monitoring_active=$(read_state_field "monitoring_active")
    echo "[guard] Task start date: ${start_date}"
    echo "[guard] Last known usage: ${cached_total} tokens (\$${cached_cost})"
    if [[ "$monitoring_active" == "1" ]]; then
      echo "[guard] Monitoring: ACTIVE (enforcing limits)"
    else
      echo "[guard] Monitoring: ARMED (not yet active — run 'activate' to start)"
    fi
  else
    echo "[guard] No state file (not initialized)."
  fi

  echo "[guard] Current date: $(today_date)"
  echo "[guard] Current time: $(now_iso)"
}

# ── main ─────────────────────────────────────────────────

case "${1:-help}" in
  init)     do_init ;;
  activate) do_activate ;;
  check)    do_check ;;
  status)   do_status ;;
  *)
    echo "Usage: check_guard.sh {init|activate|check|status}"
    echo ""
    echo "  init      Phase 1: Verify Cannbot provider and record start date. Guard is ARMED but not enforcing."
    echo "  activate  Phase 2: Activate monitoring. Only after this does 'check' enforce limits."
    echo "  check     Check if task should continue. Returns 'OK' or 'STOP'. No-op if not activated."
    echo "  status    Print current guard status."
    exit 1
    ;;
esac
