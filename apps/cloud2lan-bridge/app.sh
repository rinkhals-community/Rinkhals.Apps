. /useremain/rinkhals/.current/tools.sh

APP_ROOT=$(dirname $(realpath $0))

status() {
    PID=$(get_by_name mode_watchdog.py)

    if [ "$PID" == "" ]; then
        report_status $APP_STATUS_STOPPED
    else
        report_status $APP_STATUS_STARTED "$PID"
    fi
}
start() {
    stop

    cd $APP_ROOT

    chmod +x cloud2lan-supervisor.sh
    python3 ./mode_watchdog.py >> "${RINKHALS_LOGS:-/tmp/rinkhals}/app-cloud2lan-bridge.log" 2>&1 &
}
stop() {
    # Kill from the top down — watchdog kills its children via process group
    kill_by_name mode_watchdog.py
    # Belt-and-suspenders: kill anything that survived
    kill_by_name cloud2lan-supervisor.sh
    kill_by_name cloud2lan-bridge.py
    kill_by_name agora_pusher
    kill_by_name "ffmpeg -nostdin -loglevel quiet -i http://127.0.0.1:18088/flv"
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
