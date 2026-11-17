#!/bin/sh
# Started by app_real.sh next to Klippy. If Klippy isn't ready in time (a
# config error, a missing MCU), bring GoKlipper back so the printer
# isn't left without a controller until someone disables the app
# (rinkhals-community/Rinkhals#158). Klippy's own log says why it didn't start.

. /useremain/rinkhals/.current/tools.sh
APP_ROOT=$(dirname $(realpath $0))
echo $$ > /tmp/vanilla-klipper-fallback.pid

# The first start compiles Klipper's C helper on the printer, which can take
# minutes on the slower boards, so allow longer until it exists
TIMEOUT=180
[ -f "$APP_ROOT/klippy/chelper/c_helper.so" ] || TIMEOUT=600

RESULT=$(python3 "$APP_ROOT/ready_check.py" /tmp/unix_uds1 $TIMEOUT)
STATUS=$?
log "vanilla-klipper: $RESULT"
rm -f /tmp/vanilla-klipper-fallback.pid
[ $STATUS -eq 0 ] && exit 0

# Stopping the app meanwhile already brought GoKlipper back
if [ -n "$(get_by_name gklib)" ]; then
    exit 0
fi

log "vanilla-klipper: Klippy didn't come up, starting GoKlipper instead"
"$APP_ROOT/app_real.sh" stop
