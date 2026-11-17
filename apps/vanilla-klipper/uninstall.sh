#!/bin/sh
#
# uninstall.sh - manual test-cleanup for the vanilla-klipper app.
#
# Reverts the printer to stock GoKlipper and removes everything the app
# deploys *outside* its own folder (configs copied into the printer config
# dir, the Moonraker ACE-status component, the [ace_status] Moonraker config
# block, and the ACE dashboard files injected into Mainsail).
#
# The app folder itself is left untouched: run ./app.sh start to redeploy.
# Safe to run repeatedly (every step is guarded).
#
# Usage:
#   ./uninstall.sh

set -u

TOOLS=/useremain/rinkhals/.current/tools.sh
if [ ! -f "$TOOLS" ]; then
    echo "ERROR: $TOOLS not found - run this on the printer." >&2
    exit 1
fi
. "$TOOLS"

# Our own app folder. Captured once: get_app_root clobbers $APP_ROOT.
SELF_ROOT=$(dirname "$(realpath "$0")")

CONFIG_DIR=/userdata/app/gk/printer_data/config

echo "vanilla-klipper uninstall: reverting to GoKlipper and removing deployed files"

# 1. Stop Klippy and hand control back to gklib (app.sh stop starts gklib).
echo "-> Stopping Klippy / starting gklib"
if [ -x "$SELF_ROOT/app.sh" ]; then
    "$SELF_ROOT/app.sh" stop >/dev/null 2>&1 || kill_by_name klippy
else
    kill_by_name klippy
fi

# 2. Configs this app copies into the printer config dir.
echo "-> Removing deployed config files"
for f in \
    printer.klipper.cfg \
    printer_generic_macros.cfg \
    ace_macros_generic.cfg \
    mainsail.cfg \
    ace_KS1.cfg ace_K3.cfg ace_K3M.cfg
do
    if [ -f "$CONFIG_DIR/$f" ]; then
        echo "   removing $CONFIG_DIR/$f"
        rm -f "$CONFIG_DIR/$f"
    fi
done

# 3. Moonraker ACE-status component.
MR_COMPONENTS="$(get_app_root 40-moonraker)/moonraker/moonraker/components"
if [ -f "$MR_COMPONENTS/ace_status.py" ]; then
    echo "   removing $MR_COMPONENTS/ace_status.py"
    rm -f "$MR_COMPONENTS/ace_status.py"
fi

# 4. The [ace_status] block this app appends to moonraker.custom.conf.
MR_CONF="$CONFIG_DIR/moonraker.custom.conf"
if [ -f "$MR_CONF" ] && grep -q '^\[ace_status\]' "$MR_CONF"; then
    echo "   removing [ace_status] section from $MR_CONF"
    awk '
        /^# ACE status dashboard API \(added by vanilla-klipper\)/ { next }
        /^\[ace_status\][ \t]*$/ { skip=1; next }
        skip && /^\[/ { skip=0 }
        skip { next }
        { print }
    ' "$MR_CONF" > "$MR_CONF.tmp" && mv "$MR_CONF.tmp" "$MR_CONF"
fi

# 5. ACE dashboard files injected into Mainsail.
MAINSAIL_ACE="$(get_app_root 25-mainsail)/mainsail/ace"
if [ -d "$MAINSAIL_ACE" ]; then
    echo "   removing $MAINSAIL_ACE"
    rm -rf "$MAINSAIL_ACE"
fi

# 6. ACE/Klipper persistent state (ace_global_enabled, inventory ...).
#    Always safe to remove: GoKlipper keeps its own separate variables file.
if [ -f "$CONFIG_DIR/saved_variables.cfg" ]; then
    echo "   removing $CONFIG_DIR/saved_variables.cfg (Klipper/ACE persistent state)"
    rm -f "$CONFIG_DIR/saved_variables.cfg"
fi

# 7. Restart Moonraker so it drops the now-removed ACE-status component/config.
#    40-moonraker/app.sh stop only kills moonraker.py, not its resident
#    moonraker.sh restart-wrapper, so stop_app/start_app would leak a duplicate
#    Moonraker. Kill the wrapper(s) + python ourselves, then start exactly one.
echo "-> Restarting Moonraker"
kill_by_name moonraker.sh >/dev/null 2>&1 || true
kill_by_name moonraker.py >/dev/null 2>&1 || true
start_app 40-moonraker 5 >/dev/null 2>&1 || true

echo "Done. Printer reverted to GoKlipper; vanilla-klipper deployed files removed."
echo "The app folder is untouched - run ./app.sh start to redeploy."
