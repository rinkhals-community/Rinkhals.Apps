source /useremain/rinkhals/.current/tools.sh

export APP_ROOT=$(dirname $(realpath $0))

status() {
    PIDS=$(get_by_name klippy)

    if [ "$PIDS" == "" ]; then
        report_status $APP_STATUS_STOPPED
    else
        report_status $APP_STATUS_STARTED "$PIDS"
    fi
}

reset_mcus() {
    if [ "$KOBRA_MODEL_CODE" = "KS1" ]; then
        echo "KS1: Reseting MCU(s)"
        echo 116 > /sys/class/gpio/export
        echo out > /sys/class/gpio/gpio116/direction
        echo 0 > /sys/class/gpio/gpio116/value
        sleep 1
        echo 1 > /sys/class/gpio/gpio116/value
    else
        echo "MCU reset not implemented"
    fi
}

# Self-healing deployment of the ACE status dashboard (ace_status_integration).
# Runs on every start so it survives Moonraker/Mainsail updates that would
# otherwise overwrite the injected files. Only restarts Moonraker when the
# component or its config actually changed.
deploy_ace_dashboard() {
    local SELF_ROOT="$APP_ROOT"
    local SRC="$SELF_ROOT/ace_status_integration"
    [ -d "$SRC" ] || return 0

    local MR_CHANGED=0

    # 1. Moonraker component -> live components dir
    local MR_COMPONENTS="$(get_app_root 40-moonraker)/moonraker/moonraker/components"
    if [ -d "$MR_COMPONENTS" ] && [ -f "$SRC/moonraker/ace_status.py" ]; then
        if ! cmp -s "$SRC/moonraker/ace_status.py" "$MR_COMPONENTS/ace_status.py" 2>/dev/null; then
            cp -f "$SRC/moonraker/ace_status.py" "$MR_COMPONENTS/ace_status.py"
            MR_CHANGED=1
        fi
    fi

    # 2. Ensure [ace_status] is present in the merged Moonraker config
    local MR_CONF="/userdata/app/gk/printer_data/config/moonraker.custom.conf"
    if [ -f "$MR_CONF" ] && ! grep -q '^\[ace_status\]' "$MR_CONF"; then
        printf '\n# ACE status dashboard API (added by vanilla-klipper)\n[ace_status]\n' >> "$MR_CONF"
        MR_CHANGED=1
    fi

    # 3. Static dashboard -> Mainsail doc-root subfolder (non-destructive)
    local MAINSAIL_WEB="$(get_app_root 25-mainsail)/mainsail/ace"
    if [ -d "$(get_app_root 25-mainsail)/mainsail" ]; then
        mkdir -p "$MAINSAIL_WEB"
        # vue.global.prod.js is bundled for offline use and loaded by ace.html;
        # without it Vue is undefined and the page shows raw {{ }} templates.
        for f in ace.html ace-dashboard.js ace-dashboard.css ace-dashboard-config.js favicon.svg vue.global.prod.js; do
            [ -f "$SRC/web/$f" ] && cp -f "$SRC/web/$f" "$MAINSAIL_WEB/$f"
        done
    fi

    # 4. Restart Moonraker only if its component/config changed, so it loads
    #    the ACE API. get_app_root clobbers $APP_ROOT, so restore it afterwards.
    if [ "$MR_CHANGED" = "1" ]; then
        log "ACE dashboard: deploying Moonraker component, restarting Moonraker"
        # NOTE: 40-moonraker/app.sh stop only kills moonraker.py and leaves the
        # resident moonraker.sh restart-wrapper alive; start then launches
        # another wrapper, so a plain stop_app/start_app leaks a full Moonraker
        # instance every time (observed: 3 concurrent Moonrakers pegging the
        # CPU at 100%). Kill the wrapper(s) AND the python ourselves first so
        # the following start yields exactly one instance.
        kill_by_name moonraker.sh 2>/dev/null || true
        kill_by_name moonraker.py 2>/dev/null || true
        start_app 40-moonraker 5 2>/dev/null || true
    fi

    export APP_ROOT="$SELF_ROOT"
}

start() {
    # No-op on unsupported printers (mirrors klippy.sh and the app.sh
    # launchwrapper): without a printer config for this model there is nothing
    # to run, so don't kill gklib or start Klippy. Normally the launchwrapper
    # catches this first; this guards direct invocations of app_real.sh.
    if [ ! -f "$APP_ROOT/printer_${KOBRA_MODEL_CODE:-}.cfg" ]; then
        echo "vanilla-klipper: unsupported printer model '${KOBRA_MODEL_CODE:-unknown}'; not starting."
        return 0
    fi

    #Stop klippy in case it's running
    kill_by_name klippy
    # Stop gklib
    kill_by_name gklib

    reset_mcus

    # Deploy the ACE status dashboard (self-healing, before starting Klippy)
    deploy_ace_dashboard

    # Start Klippy
    cd $APP_ROOT
    chmod +x klippy.sh
    ./klippy.sh &
}

debug() {
    kill_by_name klippy
    kill_by_name gklib
    reset_mcus

    cd $APP_ROOT
    # Create Python venv
    python -m venv --without-pip $APP_ROOT
    . bin/activate

    # Start OctoApp
    cd klippy
    python -m klippy -a /tmp/unix_uds1  /userdata/app/gk/printer_data/config/printer.klipper.cfg >> /tmp/klippy.log 2>&1 &
}

stop() {
    kill_by_name klippy
    
    #MCU reset to allow gklib MCU configuration
    reset_mcus

    cd /userdata/app/gk

    LD_LIBRARY_PATH=/userdata/app/gk:$LD_LIBRARY_PATH \
        ./gklib -a /tmp/unix_uds1 /userdata/app/gk/printer_data/config/printer.generated.cfg &> $RINKHALS_ROOT/logs/gklib.log &
}

case "$1" in
    status)
        status
        ;;
    start)
        start
        ;;
    debug)
        shift
        debug $@
        ;;
    stop)
        stop
        ;;
    *)
        echo "Usage: $0 {status|start|debug|stop}" >&2
        exit 1
        ;;
esac
