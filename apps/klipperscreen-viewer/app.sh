#!/bin/sh
# Non-blocking launch wrapper for klipperscreen-viewer.
# Keeps app.sh fast for Rinkhals start timeout constraints.

. /useremain/rinkhals/.current/tools.sh

APP_ROOT=$(dirname "$(realpath "$0")")
REAL_SCRIPT="${ORIG_SCRIPT:-"$APP_ROOT/app_real.sh"}"
CONF_FILE="/useremain/rinkhals/klipperscreen-viewer.conf"

VIEWER_BOOT_DELAY_SEC="15"
[ -f "$CONF_FILE" ] && . "$CONF_FILE"

STATE_DIR="${RINKHALS_LOGS:-/tmp/rinkhals}"
WAIT_PID_FILE="$STATE_DIR/klipperscreen-viewer.wait.pid"
WAIT_UNTIL_FILE="$STATE_DIR/klipperscreen-viewer.wait.until"
mkdir -p "$STATE_DIR" 2>/dev/null || true

sanitize_delay() {
    case "${1:-}" in
        ''|*[!0-9]*)
            echo "15"
            ;;
        *)
            echo "$1"
            ;;
    esac
}

parent_cmdline() {
    tr '\000' ' ' < "/proc/$PPID/cmdline" 2>/dev/null
}

launcher_alive() {
    [ -f "$WAIT_PID_FILE" ] || return 1
    PID=$(cat "$WAIT_PID_FILE" 2>/dev/null || true)
    [ -n "$PID" ] || return 1
    kill -0 "$PID" 2>/dev/null
}

start_async() {
    DELAY="$1"

    if launcher_alive; then
        echo "klipperscreen-viewer: start already scheduled (pid $(cat "$WAIT_PID_FILE" 2>/dev/null))."
        return 0
    fi

    rm -f "$WAIT_PID_FILE" "$WAIT_UNTIL_FILE" 2>/dev/null || true
    UNTIL_TS=$(( $(date +%s) + DELAY ))
    echo "$UNTIL_TS" > "$WAIT_UNTIL_FILE"

    setsid nohup sh -c "
        sleep $DELAY
        \"$REAL_SCRIPT\" start || true
        rm -f \"$WAIT_PID_FILE\" \"$WAIT_UNTIL_FILE\" 2>/dev/null || true
    " >> "$STATE_DIR/app-fb-vnc-viewer-launchwrapper.log" 2>&1 &

    echo "$!" > "$WAIT_PID_FILE"
    return 0
}

start() {
    PARENT="$(parent_cmdline)"
    BOOT_DELAY="$(sanitize_delay "$VIEWER_BOOT_DELAY_SEC")"

    DELAY=0
    case "$PARENT" in
        *start.sh*)
            DELAY="$BOOT_DELAY"
            ;;
    esac

    start_async "$DELAY"
}

status() {
    if launcher_alive; then
        REM=""
        if [ -f "$WAIT_UNTIL_FILE" ]; then
            UNTIL_TS=$(cat "$WAIT_UNTIL_FILE" 2>/dev/null || echo 0)
            NOW_TS=$(date +%s)
            DELTA=$(( UNTIL_TS - NOW_TS ))
            [ "$DELTA" -lt 0 ] && DELTA=0
            REM=" (~${DELTA}s remaining)"
        fi
        echo "launchwrapper: delayed start pending; launcher PID $(cat "$WAIT_PID_FILE" 2>/dev/null)${REM}"
    else
        rm -f "$WAIT_PID_FILE" "$WAIT_UNTIL_FILE" 2>/dev/null || true
    fi

    "$REAL_SCRIPT" status
}

stop() {
    if launcher_alive; then
        kill "$(cat "$WAIT_PID_FILE" 2>/dev/null)" 2>/dev/null || true
    fi
    rm -f "$WAIT_PID_FILE" "$WAIT_UNTIL_FILE" 2>/dev/null || true

    "$REAL_SCRIPT" stop
}

version() {
    "$REAL_SCRIPT" version
}

case "${1:-}" in
    start)  start ;;
    stop)   stop ;;
    status) status ;;
    version) version ;;
    *)
        echo "Usage: $0 {start|stop|status|version}" >&2
        exit 1
        ;;
esac
