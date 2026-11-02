. /useremain/rinkhals/.current/tools.sh

APP_ROOT="$(dirname "$(realpath "$0")")"

# busybox ps cuts command lines short, which hides the supervisor's script name
# from tools.sh kill_by_name, so match on the full /proc/<pid>/cmdline instead.
# One grep over all of them: a fork per process made stop slow enough for start
# to hit Rinkhals' 5s timeout at boot. Arguments are NUL separated in cmdline,
# so a pattern can't span two of them
kill_by_cmdline() {
    for CMDLINE in $(grep -lF "$@" /proc/[0-9]*/cmdline 2>/dev/null); do
        PID=${CMDLINE#/proc/}
        PID=${PID%/cmdline}
        [ "$PID" != "$$" ] && kill -9 $PID 2>/dev/null
    done
}

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

    cd "$APP_ROOT"

    chmod +x cloud2lan-supervisor.sh
    # The watchdog is long-lived, so it must not inherit the caller's fds. When
    # start is invoked from the on-screen menu (rinkhals-ui.py), an inherited
    # pipe stays open and freezes the touchscreen
    python3 ./mode_watchdog.py </dev/null >> "${RINKHALS_LOGS:-/tmp/rinkhals}/app-cloud2lan-bridge.log" 2>&1 &
}
stop() {
    # Kill from the top down — watchdog kills its children via process group
    kill_by_name mode_watchdog.py
    # Belt-and-suspenders: kill anything that survived, supervisor first so it
    # can't restart the bridge
    kill_by_cmdline -e cloud2lan-supervisor.sh
    kill_by_cmdline -e cloud2lan-bridge.py -e agora_pusher -e 127.0.0.1:18088/flv
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
