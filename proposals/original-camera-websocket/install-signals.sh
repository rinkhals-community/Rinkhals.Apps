#!/bin/sh
# Use the Rinkhals 20261101_01 beep(milliseconds) helper, not a new PWM setup.
# Positional path parameters are only used by the local tests; the installer
# calls this with stock paths. Never alter PWM period or duty cycle.
camera_signal() (
    set +e
    result=$1; config=$2; py=$3
    tools=${4:-/useremain/rinkhals/.current/tools.sh}
    pwm=${5:-/sys/class/pwm/pwmchip0/pwm0}
    case "$result" in
        0) set -- 150 150;;
        2) set -- 150 600;;
        3) set -- 600 600;;
        *) set -- 150 150 150;;
    esac
    if [ -e "$config" ]; then
        [ -n "$py" ] && "$py" -c 'import json,sys; v=json.load(open(sys.argv[1])).get("install_sounds","On"); sys.exit(0 if v=="On" else 1)' "$config" || { echo 'Install sound skipped: disabled or config unavailable'; exit 0; }
    fi
    [ -r "$tools" ] && [ -r "$pwm/enable" ] && [ -r "$pwm/duty_cycle" ] && [ -r "$pwm/period" ] || { echo 'Install sound skipped: helper or PWM unavailable'; exit 0; }
    # The helper uses the native PWM. Skip if it is active, muted or not configured.
    pwm_ready() {
        [ "$(cat "$pwm/enable" 2>/dev/null)" = 0 ] || return 1
        duty=$(cat "$pwm/duty_cycle" 2>/dev/null); period=$(cat "$pwm/period" 2>/dev/null)
        case "$duty:$period" in *[!0-9:]*|:*|*:) return 1;; esac
        [ "$duty" -gt 0 ] && [ "$period" -gt "$duty" ]
    }
    pwm_ready || { echo 'Install sound skipped: PWM busy, muted or unconfigured'; exit 0; }
    # Source in this subshell; do not leak tool settings to the installer.
    . "$tools" || { echo 'Install sound skipped: Rinkhals tools unavailable'; exit 0; }
    [ "${KOBRA_MODEL_CODE:-}" = KS1 ] && command -v beep >/dev/null 2>&1 && command -v usleep >/dev/null 2>&1 || { echo 'Install sound skipped: unsupported helper/model'; exit 0; }
    # Only use the preconfigured native channel; leave other settings intact.
    for duration do
        pwm_ready || { echo 'Install sound interrupted: PWM busy or muted'; exit 0; }
        if ! beep "$duration"; then echo 'Install sound helper failed'; exit 0; fi
        usleep 180000 || exit 0
    done
    echo "Install sound: result=$result"
    exit 0
)
