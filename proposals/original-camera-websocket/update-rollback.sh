#!/bin/sh
set -eu
umask 077
ROOT=/useremain/update_swu
HOME_DIR=/useremain/home/rinkhals
DEST=$HOME_DIR/apps/39-original-camera-hls
SOUND_CONFIG=$HOME_DIR/apps/39-original-camera-hls.config
status_file=/mnt/udisk/original-camera-hls-result.txt
LOG=$HOME_DIR/original-camera-hls-install.log
PYTHON=
SOUND_TMP=
notify() {
    rc=$?
    trap - EXIT
    set +e
    # The snapshot preserves the user's sound preference even if rollback
    # restored or removed the live config. Skip sound if snapshot failed.
    if [ -n "$SOUND_TMP" ] && [ -r "$ROOT/install-signals.sh" ]; then
        ( . "$ROOT/install-signals.sh"; camera_signal "$rc" "$SOUND_TMP/config" "$PYTHON" ) || :
    fi
    [ -z "$SOUND_TMP" ] || rm -rf "$SOUND_TMP"
    if grep -q " /mnt/udisk " /proc/mounts 2>/dev/null; then
        cp "$LOG" /mnt/udisk/original-camera-hls-install.log 2>/dev/null || :
        case "$rc" in
            0) echo 'SUCCESS - reboot manually' > "$status_file";;
            3) echo 'BLOCKED - printer not confirmed idle; no camera changes; retry when idle' > "$status_file";;
            *) echo 'FAILED - see original-camera-hls-install.log on USB or in Rinkhals home' > "$status_file";;
        esac
        sync
    fi
    exit "$rc"
}
mkdir -p "$HOME_DIR"
exec >"$LOG" 2>&1
trap notify EXIT
printf '%s\n' 'Anycubic Original Camera - WebSocket Bridge 1.0.0-rc2 - rollback'
export PATH=/usr/bin:/usr/sbin:/bin:/sbin:${PATH:-}
# Best effort only: copying a preference must not prevent a safe rollback.
SOUND_TMP=$(mktemp -d /tmp/camera-rollback-sound.XXXXXX) || SOUND_TMP=
if [ -n "$SOUND_TMP" ] && [ -e "$SOUND_CONFIG" ]; then
    if ! cp "$SOUND_CONFIG" "$SOUND_TMP/config"; then
        rm -rf "$SOUND_TMP"; SOUND_TMP=
    fi
fi
for candidate in /usr/bin/python3 /useremain/rinkhals/.current/usr/bin/python3; do
    if [ -x "$candidate" ] && "$candidate" -c 'import sys, ssl, paho.mqtt.client; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then PYTHON=$candidate; break; fi
done
[ -n "$PYTHON" ] || { echo 'Rinkhals Python runtime unavailable'; exit 1; }
[ -f "$DEST/setup.py" ] || { echo 'App not installed'; exit 1; }
# Only use this rollback wrapper for versions with classified preflight.
"$PYTHON" -c 'import json,sys; a=json.load(open(sys.argv[1])); assert a.get("name") in ("Anycubic Original Camera - HLS Bridge","Anycubic Original Camera - WebSocket Bridge") and a.get("version") in ("0.2.7-beta","0.2.8-beta","0.2.9-beta","1.0.0-rc1","1.0.0-rc2"), "Use matching rollback package for older versions"' "$DEST/app.json"
# Exit 3 = not confirmed idle. The actual rollback retains its own idle check.
"$PYTHON" "$DEST/setup.py" preflight
"$PYTHON" "$DEST/setup.py" rollback
sh "$DEST/app.sh" stop
rm -rf "$DEST"
echo 'Uninstalled; reboot manually.'
