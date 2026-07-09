#!/usr/bin/env bash
# cannbot-token-guard: monitor daily Cannbot token usage and stop when threshold or time window is exceeded.
#
# Two-phase workflow:
#   Phase 1 (启动检测):  check_guard.sh init      Verify Cannbot provider, record start date. Monitoring NOT active yet.
#   Phase 2 (任务开始):  check_guard.sh activate  Activate monitoring. Only after this does `check` enforce limits.
#                        check_guard.sh check     Check if task should continue (returns OK / STOP).
#   Always:              check_guard.sh status    Print current guard status.
#
# Stop conditions:
#   - Cannbot daily token usage >= 95% of budget (9500 万 / 1 亿)
#   - Current time outside 22:00-24:00 task window

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

# Fetch today's Cannbot token usage via opencode-usage data.
# Runs 'opencode-usage sync' to ensure fresh data, then reads the
# underlying JSON storage file directly for reliable machine-readable parsing.
# Returns: total_tokens  cannbot_found(yes/no)  cost
fetch_usage() {
  opencode-usage sync >/dev/null 2>&1 || true

  local data_file="${HOME}/.local/share/opencode-usage/usage-data.json"
  if [[ ! -f "$data_file" ]]; then
    echo "0 no 0"
    return
  fi

  python3 - "$data_file" <<'PYEOF'
import json, sys
from datetime import datetime, date

data_file = sys.argv[1]
try:
    with open(data_file) as f:
        data = json.load(f)
except Exception:
    print("0 no 0")
    sys.exit(0)

today = date.today()
cannbot_total = 0
cannbot_found = False
cost = 0.0

sessions = data.get("sessions", {})
if isinstance(sessions, dict):
    session_list = list(sessions.values())
elif isinstance(sessions, list):
    session_list = sessions
else:
    session_list = []

for s in session_list:
    if str(s.get("provider", "")).lower() != "cannbot":
        continue

    created = s.get("createdAt", "")
    try:
        session_date = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone().date()
    except Exception:
        continue

    if session_date != today:
        continue

    cannbot_found = True

    tokens = s.get("tokens", {})
    cannbot_total += (tokens.get("input", 0) + tokens.get("output", 0)
                      + tokens.get("cacheRead", 0) + tokens.get("cacheWrite", 0)
                      + tokens.get("reasoning", 0))

    for sub in s.get("subagents", []):
        st = sub.get("tokens", {})
        cannbot_total += (st.get("input", 0) + st.get("output", 0)
                          + st.get("cacheRead", 0) + st.get("cacheWrite", 0)
                          + st.get("reasoning", 0))

    cost += s.get("totalCost", s.get("cost", 0.0))

found_str = "yes" if cannbot_found else "no"
print("%d %s %.4f" % (cannbot_total, found_str, cost))
PYEOF
}

# ── init ─────────────────────────────────────────────────

do_init() {
  echo "[guard] Initializing Cannbot token guard..."
  echo "[guard] Daily budget: ${DAILY_TOKEN_BUDGET} tokens"
  echo "[guard] Stop threshold (95%): ${STOP_THRESHOLD} tokens"

  # Ensure opencode-usage is available
  if ! command -v opencode-usage >/dev/null 2>&1; then
    echo "[guard] opencode-usage not found. Installing via npm..."
    npm install -g @azatakmyradov/opencode-usage 2>&1 || {
      echo "[guard] ERROR: Failed to install opencode-usage."
      echo "[guard] Please install manually: npm install -g @azatakmyradov/opencode-usage"
      echo "STOP"
      return 1
    }
    if ! command -v opencode-usage >/dev/null 2>&1; then
      echo "[guard] ERROR: opencode-usage still not available after install."
      echo "STOP"
      return 1
    fi
    echo "[guard] opencode-usage installed successfully."
  fi

  # Fetch usage to verify cannbot is present
  local usage_result
  usage_result=$(fetch_usage)
  local total found cost
  total=$(echo "$usage_result" | awk '{print $1}')
  found=$(echo "$usage_result" | awk '{print $2}')
  cost=$(echo "$usage_result" | awk '{print $3}')

  if [[ "$found" != "yes" ]]; then
    echo "[guard] ERROR: No Cannbot provider usage detected in opencode-usage output."
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

  # Check time window: must be within 22:00-24:00
  local hour
  hour=$(date +%H)
  if (( 10#$hour < 22 )); then
    echo "[guard] STOP: Current time $(date +%H:%M) outside task window (22:00-24:00)."
    echo "STOP"
    return 0
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

  # ── Check 1: time window (22:00-24:00) ──
  local hour
  hour=$(date +%H)
  if (( 10#$hour < 22 )); then
    echo "[guard] STOP: Current time $(date +%H:%M) outside task window (22:00-24:00)."
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
  echo "[guard] Task window: 22:00-24:00"

  if [[ -f "$STATE_FILE" ]]; then
    local start_date cached_total cached_cost monitoring_active
    start_date=$(read_state_field "start_date")
    cached_total=$(read_state_field "last_total_tokens")
    cached_cost=$(read_state_field "last_cost_usd")
    monitoring_active=$(read_state_field "monitoring_active")
    echo "[guard] Init date: ${start_date}"
    echo "[guard] Last known usage: ${cached_total} tokens (\$${cached_cost})"
    if [[ "$monitoring_active" == "1" ]]; then
      echo "[guard] Monitoring: ACTIVE (enforcing limits)"
    else
      echo "[guard] Monitoring: ARMED (not yet active — run 'activate' to start)"
    fi
  else
    echo "[guard] No state file (not initialized)."
  fi

  local hour
  hour=$(date +%H)
  if (( 10#$hour >= 22 )); then
    echo "[guard] Current time: $(now_iso) [IN WINDOW]"
  else
    echo "[guard] Current time: $(now_iso) [OUTSIDE WINDOW]"
  fi
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
