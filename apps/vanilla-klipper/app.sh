# app.sh launchwrapper
# A thin wrapper that defers start() by model depended time when invoked by a parent "./start.sh"
# and returns immediately, while still exposing start/stop/status/debug entrypoints.

source /useremain/rinkhals/.current/tools.sh
export APP_ROOT=$(dirname $(realpath $0))

set -euo pipefail

ORIG_SCRIPT="${ORIG_SCRIPT:-"$APP_ROOT/app_real.sh"}"

STATE_DIR="/run/rinkhals"
WAIT_PID_FILE="$STATE_DIR/launchwrapper.wait.pid"
WAIT_UNTIL_FILE="$STATE_DIR/launchwrapper.wait.until"
mkdir -p "$STATE_DIR" 2>/dev/null || true

parent_cmdline() {
  tr '\0' ' ' < "/proc/$PPID/cmdline" | sed 's/[[:space:]]*$//'
}
START_DELAY=0
model_setup() {
    case "${KOBRA_MODEL_CODE:-}" in
        KS1)
            START_DELAY=15
            ;;
        K3|K3M)
            START_DELAY=15
            ;;
        ""|*)
            START_DELAY=0
            ;;
    esac
}

start() {
  # No-op on unsupported printers: if there is no printer config for the
  # current Kobra model, do nothing — don't schedule a delayed start, kill
  # gklib or start Klippy (mirrors klippy.sh's model check). Play the
  # distinctive "failure" jingle so the user still gets audible feedback that
  # a start was attempted on an unsupported printer.
  if [ ! -f "$APP_ROOT/printer_${KOBRA_MODEL_CODE:-}.cfg" ]; then
    echo "vanilla-klipper: unsupported printer model '${KOBRA_MODEL_CODE:-unknown}' (no printer_${KOBRA_MODEL_CODE:-unknown}.cfg); nothing to start."
    "$APP_ROOT/pwm_jingle.sh" failure 2>/dev/null || true
    return 0
  fi

  local parent
  parent="$(parent_cmdline)"
  model_setup

  if [[ "$parent" == *"./start.sh"* ]] && [ "$START_DELAY" -gt 0 ]; then
    # If there is already a waiting launcher, just report and return
    if [[ -f "$WAIT_PID_FILE" ]]; then
      local wpid
      wpid="$(cat "$WAIT_PID_FILE" 2>/dev/null || true)"
      if [[ -n "${wpid:-}" ]] && kill -0 "$wpid" 2>/dev/null; then
        echo "start(): already scheduled; launcher PID $wpid"
        return 0
      else
        rm -f "$WAIT_PID_FILE" "$WAIT_UNTIL_FILE"
      fi
    fi

    local delay=$START_DELAY
    local until_ts=$(( $(date +%s) + delay ))
    echo "$until_ts" > "$WAIT_UNTIL_FILE"

    setsid nohup bash -c "
      sleep $delay
      \"$APP_ROOT/pwm_jingle.sh\" ok 2>/dev/null || true
      \"$ORIG_SCRIPT\" start || true
      rm -f \"$WAIT_PID_FILE\" \"$WAIT_UNTIL_FILE\" 2>/dev/null || true
    " >/dev/null 2>&1 &

    echo $! > "$WAIT_PID_FILE"
    echo "Delayed start scheduled in ${delay}s (launcher PID $(cat "$WAIT_PID_FILE"))."
    "$APP_ROOT/pwm_jingle.sh" ta-da 2>/dev/null || true
    # Return immediately while the detached launcher sleeps.
    return 0
  fi

  # Otherwise, call through immediately
  "$APP_ROOT/pwm_jingle.sh" ok 2>/dev/null || true
  "$ORIG_SCRIPT" start
}

status() {
  if [[ -f "$WAIT_PID_FILE" ]]; then
    local wpid
    wpid="$(cat "$WAIT_PID_FILE" 2>/dev/null || true)"
    if [[ -n "${wpid:-}" ]] && kill -0 "$wpid" 2>/dev/null; then
      local rem=""
      if [[ -f "$WAIT_UNTIL_FILE" ]]; then
        local until_ts now_ts
        until_ts="$(cat "$WAIT_UNTIL_FILE" 2>/dev/null || echo 0)"
        now_ts="$(date +%s)"
        local delta=$(( until_ts - now_ts ))
        (( delta < 0 )) && delta=0
        rem=" (~${delta}s remaining)"
      fi
      echo "launchwrapper: delayed start pending; launcher PID ${wpid}${rem}"
    else
      rm -f "$WAIT_PID_FILE" "$WAIT_UNTIL_FILE" 2>/dev/null || true
    fi
  fi

  "$ORIG_SCRIPT" status
}

stop() {
  if [[ -f "$WAIT_PID_FILE" ]]; then
    local wpid
    wpid="$(cat "$WAIT_PID_FILE" 2>/dev/null || true)"
    if [[ -n "${wpid:-}" ]] && kill -0 "$wpid" 2>/dev/null; then
      kill -- "-$wpid" 2>/dev/null || true
    fi
    rm -f "$WAIT_PID_FILE" "$WAIT_UNTIL_FILE" 2>/dev/null || true
  fi

  "$ORIG_SCRIPT" stop
  "$APP_ROOT/pwm_jingle.sh" sad 2>/dev/null || true
}

debug() {
  "$ORIG_SCRIPT" debug
}

case "${1:-}" in
  start)  start ;;
  stop)   stop ;;
  status) status ;;
  debug)  shift; debug "$@" ;;
  *)
    echo "Usage: $0 {start|stop|status|debug}" >&2
    exit 1
    ;;
esac
