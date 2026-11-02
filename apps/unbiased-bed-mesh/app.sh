#!/bin/sh
# shellcheck disable=SC1091
. /useremain/rinkhals/.current/tools.sh

APP_ROOT=$(dirname "$(realpath "$0")")
APP_NAME=unbiased-bed-mesh
LOG_FILE=${RINKHALS_LOGS:-/tmp/rinkhals}/app-${APP_NAME}.log

# Single source of truth for "is the app currently running?".
# report_status / status output do NOT set an exit code, so we can't rely on
# `status >/dev/null` like a sysvinit script. We check PIDs directly instead.
running_pids() {
    get_by_name compensator.py
}

status() {
    PIDS=$(running_pids)

    if [ "$PIDS" = "" ]; then
        report_status $APP_STATUS_STOPPED
    else
        report_status $APP_STATUS_STARTED "$PIDS" "$LOG_FILE"
    fi
}

start() {
    # Safety gate: this app moves the head and heats the printer, so it must
    # never start by itself. Rinkhals starts enabled apps at boot, so refuse to
    # run while the app is enabled. A freshly installed app is disabled, and
    # Start on its page runs it without enabling it
    if [ "$(is_app_enabled $APP_NAME)" = "1" ]; then
        log "${APP_NAME}: refusing to run while the app is enabled"
        set_app_property $APP_NAME last_result "Not run: the app is enabled, which would start it at every boot. Disable it, then use Start"
        return 0
    fi

    PIDS=$(running_pids)
    if [ "$PIDS" != "" ]; then
        log "${APP_NAME} already running (pids: $PIDS), nothing to do"
        return 0
    fi

    cd $APP_ROOT
    nohup python ./compensator.py "$APP_ROOT/app.json" </dev/null >> $LOG_FILE 2>&1 &
    log "Started ${APP_NAME} from $APP_ROOT (pid: $!)"
}

stop() {
    PIDS=$(running_pids)
    if [ "$PIDS" = "" ]; then
        log "${APP_NAME} not running, nothing to stop"
        return 0
    fi

    # SIGTERM first, so compensator.py turns the heaters off and restores the
    # gcode state. kill_by_name sends SIGKILL by default, which would leave the
    # bed and hotend on. Give it a few seconds before forcing it
    kill_by_name compensator.py 15
    for i in 1 2 3 4 5 6 7 8 9 10; do
        [ "$(running_pids)" = "" ] && break
        sleep 1
    done
    kill_by_name compensator.py
    log "Stopped ${APP_NAME}"
}

case "$1" in
    status)
        status
        ;;
    start)
        start
        ;;
    stop)
        stop
        ;;
    *)
        echo "Usage: $0 {status|start|stop}" >&2
        exit 1
        ;;
esac
