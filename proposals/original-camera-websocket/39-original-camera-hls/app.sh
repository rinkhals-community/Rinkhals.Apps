#!/bin/sh
ROOT=$(dirname "$(realpath "$0")")
PIDFILE=/tmp/rinkhals/original-camera-hls.pid
LOG=/tmp/rinkhals/app-original-camera-hls.log
running() {
    [ -s "$PIDFILE" ] || return 1
    PID=$(cat "$PIDFILE")
    case "$PID" in ''|*[!0-9]*) return 1;; esac
    kill -0 "$PID" 2>/dev/null && grep -aqF "$ROOT/camera_hls.py" "/proc/$PID/cmdline" 2>/dev/null
}
case "$1" in
 version) echo 1.0.0-rc2;;
 status)
    if running; then printf 'Status: started\nPIDs: %s\nLog: %s\n' "$PID" "$LOG"; else printf 'Status: stopped\nPIDs: \nLog: %s\n' "$LOG"; fi;;
 start)
    mkdir -p /tmp/rinkhals
    if running; then exit 0; fi
    [ -f /useremain/home/rinkhals/original-camera-hls-state/state.json ] || { echo 'Install using the setup SWU first.'; exit 1; }
    [ -x /usr/bin/python3 ] || { echo "Rinkhals Python unavailable"; exit 1; }
    nohup /usr/bin/python3 "$ROOT/camera_hls.py" </dev/null >>"$LOG" 2>&1 &
    ;;
 stop) if running; then kill -TERM "$PID"; fi;;
 *) echo 'Usage: app.sh {start|stop|status|version}' >&2; exit 1;;
esac
