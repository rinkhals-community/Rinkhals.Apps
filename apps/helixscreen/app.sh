#!/bin/sh
# HelixScreen (https://github.com/prestonbrown/helixscreen) on the printer's
# own touch screen, in place of Anycubic's K3SysUi.
#
# Only one program can own the screen. While this app runs and Klipper is in
# charge of the printer, K3SysUi is stopped and HelixScreen has the screen.
# Whenever Anycubic's GoKlipper is running instead, or the app is stopped,
# K3SysUi gets the screen back. See README.md for what that means.

. /useremain/rinkhals/.current/tools.sh

APP_ROOT=$(dirname "$(realpath "$0")")
PAYLOAD=$APP_ROOT/helixscreen

# Settings live outside the app folder, which is replaced on every update
CONFIG_DIR=$RINKHALS_HOME/helixscreen
STATE_DIR=/tmp/rinkhals/helixscreen
LOG=$RINKHALS_LOGS/app-helixscreen.log
SYSUI_ENV=$STATE_DIR/k3sysui.env

# How the panel is mounted, per model. Only models this was run on are listed.
case "$KOBRA_MODEL_CODE" in
    KS1)
        ROTATION=180
        TOUCH_DEVICE=/dev/input/event0
        ;;
    *)
        ROTATION=
        ;;
esac

pids_of() {
    # By executable path: busybox ps cuts command lines short
    for PROC in /proc/[0-9]*; do
        case "$(readlink $PROC/exe 2> /dev/null)" in
            $1) echo ${PROC#/proc/} ;;
        esac
    done
}
helix_pids() {
    echo $(pids_of "$PAYLOAD/bin/helix-screen*")
}
sysui_pids() {
    echo $(pids_of "/userdata/app/gk/K3SysUi*")
}
supervisor_pids() {
    for PROC in /proc/[0-9]*; do
        case "$(cat $PROC/cmdline 2> /dev/null | tr '\000' ' ')" in
            *"$APP_ROOT/app.sh supervise"*) echo ${PROC#/proc/} ;;
        esac
    done
}
# Anycubic's GoKlipper is running the printer. HelixScreen needs real Klipper:
# the Klipper apps all stop gklib, and it comes back when they are stopped,
# removed, or fall back to it because Klipper wouldn't start.
goklipper_running() {
    [ "$(pids_of "/userdata/app/gk/gklib*")" != "" ]
}

status() {
    # The supervisor counts: the app is also running while it waits for Klipper
    PIDS=$(echo $(helix_pids) $(supervisor_pids))

    if [ "$PIDS" = "" ]; then
        report_status $APP_STATUS_STOPPED
    else
        report_status $APP_STATUS_STARTED "$PIDS" $LOG
    fi
}

# Writes the environment of process $1 as "export" lines
save_environment() {
    tr '\000' '\n' < /proc/$1/environ | sed "s/'/'\\\\''/g; s/^\([^=]*\)=\(.*\)$/export \1='\2'/"
}

stop_sysui() {
    PIDS=$(sysui_pids)
    [ "$PIDS" = "" ] && return

    # K3SysUi needs its Qt settings to start again, and whoever restarts it
    # (the web portal, an SSH session) may not have them
    save_environment ${PIDS%% *} > $SYSUI_ENV

    kill $PIDS 2> /dev/null
    kill_by_name rinkhals-ui > /dev/null 2>&1
    msleep 500
    PIDS=$(sysui_pids)
    [ "$PIDS" != "" ] && kill -9 $PIDS 2> /dev/null
}
start_sysui() {
    [ "$(sysui_pids)" != "" ] && return

    # Rinkhals keeps the patched K3SysUi (the one with the Rinkhals menu) next to the stock one
    SYSUI=K3SysUi
    [ -f /userdata/app/gk/K3SysUi.patch ] && SYSUI=K3SysUi.patch

    if [ ! -f $SYSUI_ENV ]; then
        # Not stopped by us this boot: gkapi was started with the same settings
        GKAPI=$(echo $(pids_of "/userdata/app/gk/gkapi*"))
        [ "$GKAPI" != "" ] && save_environment ${GKAPI%% *} > $SYSUI_ENV
    fi

    (
        [ -f $SYSUI_ENV ] && . $SYSUI_ENV
        cd /userdata/app/gk
        ./$SYSUI >> $RINKHALS_LOGS/K3SysUi.log 2>&1 &
    )
}

prepare_config() {
    mkdir -p $CONFIG_DIR/printer_database.d $STATE_DIR

    # Teaches HelixScreen's printer detection the host names Rinkhals gives
    # the printers. Ours to keep current, so it is replaced on every start.
    cp -f $APP_ROOT/rinkhals-kobra.json $CONFIG_DIR/printer_database.d/rinkhals-kobra.json

    if [ ! -f $CONFIG_DIR/settings.json ]; then
        # The build this is based on assumes another printer's touch panel.
        # Ours reports plain screen coordinates, so start with a neutral
        # calibration. HelixScreen fills in the rest of the file.
        cat > $CONFIG_DIR/settings.json << SETTINGS
{
  "language": "en",
  "input": {
    "calibration": { "a": 1.0, "b": 0.0, "c": 0.0, "d": 0.0, "e": 1.0, "f": 0.0, "valid": true, "rotation": 0 }
  }
}
SETTINGS
    fi
}

run_helix() {
    HOST=$(get_app_property helixscreen moonraker_host)
    PORT=$(get_app_property helixscreen moonraker_port)

    cd $PAYLOAD
    # No LD_LIBRARY_PATH: the binary is fully static
    HOME=$CONFIG_DIR \
    HELIX_DATA_DIR=$PAYLOAD \
    HELIX_CONFIG_DIR=$CONFIG_DIR \
    HELIX_DISPLAY_BACKEND=fbdev \
    HELIX_TOUCH_DEVICE=$TOUCH_DEVICE \
    HELIX_DISABLE_AUTO_UPDATES=1 \
        ./bin/helix-screen --rotate $ROTATION --skip-splash --no-sound \
            --moonraker ws://${HOST:-127.0.0.1}:${PORT:-7125} \
            --log-dest file --log-file $LOG --log-level info > /dev/null 2>&1 &
}

kill_helix() {
    PIDS=$(helix_pids)
    [ "$PIDS" = "" ] && return
    kill $PIDS 2> /dev/null
    msleep 1000
    PIDS=$(helix_pids)
    [ "$PIDS" != "" ] && kill -9 $PIDS 2> /dev/null
}

# Decides who has the screen, every few seconds:
#   - Klipper in charge: HelixScreen, restarted if it exits
#   - GoKlipper in charge: K3SysUi. That is the case before a Klipper app has
#     started at boot, and after one is stopped, removed or has fallen back
#   - HelixScreen won't stay up: K3SysUi, for good
# A change has to be seen twice in a row before it is acted on, so a Klipper
# app restarting doesn't flip the screen back and forth.
supervise() {
    FAILURES=0
    STARTED=0
    SEEN=0
    OWNER=

    while true; do
        if goklipper_running; then WANT=sysui; else WANT=helix; fi

        if [ "$WANT" = "$OWNER" ]; then
            SEEN=0
        else
            SEEN=$((SEEN + 1))
        fi

        if [ "$WANT" != "$OWNER" ] && [ $SEEN -ge 2 ]; then
            if [ "$WANT" = "helix" ]; then
                log "helixscreen: Klipper is in charge, taking the screen"
                stop_sysui
            else
                log "helixscreen: Anycubic's GoKlipper is running, leaving the screen to K3SysUi until a Klipper app takes over"
                kill_helix
                start_sysui
            fi
            OWNER=$WANT
            SEEN=0
            FAILURES=0
        fi

        if [ "$OWNER" = "helix" ] && [ "$(helix_pids)" = "" ]; then
            # Not running: first start, or it exited. A run of a minute or more was a working one
            NOW=$(date +%s)
            if [ $STARTED -ne 0 ] && [ $((NOW - STARTED)) -lt 60 ]; then
                FAILURES=$((FAILURES + 1))
            else
                FAILURES=0
            fi

            if [ $FAILURES -ge 3 ]; then
                log "helixscreen: HelixScreen keeps exiting, giving the screen back to K3SysUi (see $LOG)"
                start_sysui
                return
            fi

            STARTED=$NOW
            run_helix
        fi

        sleep 3
    done
}

start() {
    if [ "$ROTATION" = "" ]; then
        echo "helixscreen: not available for this printer model (${KOBRA_MODEL_CODE:-unknown}) yet; not starting."
        return 0
    fi

    stop_helix
    prepare_config
    chmod +x $PAYLOAD/bin/helix-screen

    # The supervisor takes the screen once Klipper is in charge. Detached, with
    # no inherited output, so that a caller waiting on ours isn't held up
    setsid sh "$APP_ROOT/app.sh" supervise < /dev/null > /dev/null 2>&1 &
}

stop_helix() {
    # The supervisor first, so it doesn't start HelixScreen again
    PIDS=$(echo $(supervisor_pids))
    [ "$PIDS" != "" ] && kill $PIDS 2> /dev/null

    kill_helix
}
stop() {
    stop_helix
    [ "$ROTATION" != "" ] && start_sysui
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
    supervise)
        supervise
        ;;
    version)
        jq -r .version "$APP_ROOT/app.json"
        ;;
    *)
        echo "Usage: $0 {status|start|stop}" >&2
        exit 1
        ;;
esac
