#!/bin/sh
set -eu
umask 077
ROOT=/useremain/update_swu
APP=39-original-camera-hls
HOME_DIR=/useremain/home/rinkhals
DEST=$HOME_DIR/apps/$APP
STATE=$HOME_DIR/original-camera-hls-state/state.json
SOUND_CONFIG=$HOME_DIR/apps/$APP.config
PYTHON=
LOG=$HOME_DIR/original-camera-hls-install.log
status_file=/mnt/udisk/original-camera-hls-result.txt
notify() {
    rc=$?
    trap - EXIT
    set +e
    if grep -q " /mnt/udisk " /proc/mounts 2>/dev/null; then
        cp "$LOG" /mnt/udisk/original-camera-hls-install.log 2>/dev/null || :
        if [ "$rc" = 0 ]; then echo 'SUCCESS - setup/update completed; remove USB; check live stream; reboot if bridge requires it' > "$status_file"; elif [ "$rc" = 2 ]; then echo 'INSTALLED - Moonraker reload or camera startup not confirmed; remove USB and reboot manually when idle; see log' > "$status_file"; elif [ "$rc" = 3 ]; then echo 'BLOCKED - printer not confirmed idle; no camera changes; retry when idle' > "$status_file"; else echo 'FAILED - see original-camera-hls-install.log on USB or in Rinkhals home' > "$status_file"; fi
        sync
    fi
    # Notifications are best effort and must never change installer exit status.
    if [ -r "$ROOT/install-signals.sh" ]; then
        ( . "$ROOT/install-signals.sh"; camera_signal "$rc" "$SOUND_CONFIG" "$PYTHON" ) || :
    fi
    exit "$rc"
}
mkdir -p "$HOME_DIR"
exec >"$LOG" 2>&1
trap notify EXIT
printf '%s\n' 'Anycubic Original Camera - WebSocket Bridge 1.0.0-rc2 - setup'
export PATH=/usr/bin:/usr/sbin:/bin:/sbin:${PATH:-}
[ -f /useremain/rinkhals/.current/tools.sh ] || { echo 'Rinkhals not found'; exit 1; }
PYTHON=
for candidate in /usr/bin/python3 /useremain/rinkhals/.current/usr/bin/python3; do
    if [ -x "$candidate" ] && "$candidate" -c 'import sys, ssl, paho.mqtt.client; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then PYTHON=$candidate; break; fi
done
[ -n "$PYTHON" ] || { echo 'Rinkhals Python runtime unavailable. Boot Rinkhals fully, then retry; no configuration changed.'; exit 1; }
echo "Python: $PYTHON"
# Classify initial print interlock before any update or setup changes.
"$PYTHON" "$ROOT/$APP/setup.py" preflight
if [ -e "$STATE" ]; then
    "$PYTHON" "$ROOT/$APP/upgrade.py"
    exit $?
fi
if [ -e "$DEST" ]; then
    # Recover only a recognizable, failed installation; never overwrite active config.
    [ -d "$DEST" ] && [ ! -L "$DEST" ] && [ -f "$DEST/app.json" ] && [ -f "$DEST/app.sh" ] || { echo 'Unexpected existing app; stopped'; exit 1; }
    "$PYTHON" -c 'import json,sys; a=json.load(open(sys.argv[1])); assert a.get("name") in ("Anycubic Original Camera - HLS Bridge","Anycubic Original Camera - WebSocket Bridge") and a.get("version") in ("0.2.0-beta","0.2.1-beta","0.2.2-beta","0.2.3-beta","0.2.4-beta","0.2.6-beta","0.2.7-beta","0.2.8-beta","0.2.9-beta","1.0.0-rc1","1.0.0-rc2")' "$DEST/app.json"
    status=$(sh "$DEST/app.sh" status)
    case "$status" in *'Status: stopped'*) ;; *) echo 'Previous app not confirmed stopped'; exit 1;; esac
    [ ! -e "$HOME_DIR/apps/$APP.enabled" ] && [ ! -e "$DEST/.enabled" ] || { echo 'Previous app enabled; manual review required'; exit 1; }
    backup=$(mktemp -d "$HOME_DIR/original-camera-hls-failed.XXXXXX")
    mv "$DEST" "$backup/app"
    echo "Incomplete previous app saved: $backup"
fi
mkdir -p "$DEST"
cp -r "$ROOT/$APP/." "$DEST/"
chmod +x "$DEST/app.sh"
rc=0
"$PYTHON" "$DEST/setup.py" install || rc=$?
if [ "$rc" != 0 ]; then
    if [ ! -f "$STATE" ]; then rm -rf "$DEST"; fi
    # Reserve exit 2 for a committed installation with restart warning.
    if [ "$rc" = 2 ] && [ -f "$STATE" ]; then exit 2; fi
    exit 1
fi
echo 'Installation complete; camera service started. Check stream.'
