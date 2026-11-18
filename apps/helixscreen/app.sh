#!/bin/sh
# HelixScreen (https://github.com/prestonbrown/helixscreen) on the printer's
# own touch screen, in place of Anycubic's K3SysUi.
#
# Only one program can own the screen. Starting this app stops K3SysUi, and
# stopping it brings K3SysUi back. See README.md for what that means.

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

status() {
    PIDS=$(helix_pids)

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
            --log-dest file --log-file $LOG --log-level info > /dev/null 2>&1
}

# Keeps HelixScreen running, and gives the screen back to K3SysUi if it won't stay up
supervise() {
    FAILURES=0

    while [ $FAILURES -lt 3 ]; do
        STARTED=$(date +%s)
        run_helix
        # A run of a minute or more was a working one
        if [ $(( $(date +%s) - STARTED )) -ge 60 ]; then
            FAILURES=0
        else
            FAILURES=$((FAILURES + 1))
        fi
        sleep 2
    done

    log "helixscreen: HelixScreen keeps exiting, giving the screen back to K3SysUi (see $LOG)"
    start_sysui
}

start() {
    if [ "$ROTATION" = "" ]; then
        echo "helixscreen: not available for this printer model (${KOBRA_MODEL_CODE:-unknown}) yet; not starting."
        return 0
    fi

    stop_helix
    prepare_config
    chmod +x $PAYLOAD/bin/helix-screen
    stop_sysui

    # Detached, with no inherited output, so that a caller waiting on ours isn't held up
    setsid sh "$APP_ROOT/app.sh" supervise < /dev/null > /dev/null 2>&1 &
}

stop_helix() {
    # The supervisor first, so it doesn't start HelixScreen again
    for PROC in /proc/[0-9]*; do
        case "$(cat $PROC/cmdline 2> /dev/null | tr '\000' ' ')" in
            *"$APP_ROOT/app.sh supervise"*) kill ${PROC#/proc/} 2> /dev/null ;;
        esac
    done

    PIDS=$(helix_pids)
    [ "$PIDS" = "" ] && return
    kill $PIDS 2> /dev/null
    msleep 1000
    PIDS=$(helix_pids)
    [ "$PIDS" != "" ] && kill -9 $PIDS 2> /dev/null
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
