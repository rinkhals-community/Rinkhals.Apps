#!/bin/sh
# shellcheck disable=SC1091
. /useremain/rinkhals/.current/tools.sh

APP_ROOT=$(dirname "$(realpath "$0")")
APP_NAME=unbiased-bed-mesh
LOG_FILE=/tmp/${APP_NAME}.log

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
    # Safety gate: refuse to run unless .disabled marker is present. This
    # makes the app safe by construction: if some external actor (UI "enable"
    # button, boot autostart) removes the marker, the app will not probe.
    # Touch the marker on every start to re-assert the safe state.
    if [ ! -e "$APP_ROOT/.disabled" ]; then
        log "${APP_NAME}: refusing to run, .disabled marker missing"
        return 0
    fi
    touch "$APP_ROOT/.disabled"

    PIDS=$(running_pids)
    if [ "$PIDS" != "" ]; then
        log "${APP_NAME} already running (pids: $PIDS), nothing to do"
        return 0
    fi

    cd $APP_ROOT
    nohup python ./compensator.py "$APP_ROOT/app.json" >> $LOG_FILE 2>&1 &
    log "Started ${APP_NAME} from $APP_ROOT (pid: $!)"
}

stop() {
    PIDS=$(running_pids)
    if [ "$PIDS" = "" ]; then
        log "${APP_NAME} not running, nothing to stop"
        return 0
    fi

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
