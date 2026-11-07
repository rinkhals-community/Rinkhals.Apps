#!/bin/sh

source /useremain/rinkhals/.current/tools.sh

APP_ROOT=$(dirname "$(realpath "$0")")
APP_NAME=$(basename "$APP_ROOT")
PID_FILE="/tmp/rinkhals/app-$APP_NAME.pid"
LOG_FILE="$RINKHALS_LOGS/app-$APP_NAME.log"

validate_mac() {
    case "$1" in
        [0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F])
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

is_unicast_mac() {
    FIRST_OCTET=$(echo "$1" | cut -d: -f1)
    VALUE=$((16#$FIRST_OCTET))
    [ $((VALUE & 1)) -eq 0 ]
}

is_usb_iface() {
    case "$(readlink -f "/sys/class/net/$1/device" 2>/dev/null)" in
        *usb*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

find_usb_eth() {
    OVERRIDE=$(get_app_property "$APP_NAME" interface)

    if [ -n "$OVERRIDE" ]; then
        if [ -e "/sys/class/net/$OVERRIDE" ] && is_usb_iface "$OVERRIDE"; then
            echo "$OVERRIDE"
            return 0
        fi
    fi

    for IFACE_PATH in /sys/class/net/*; do
        [ -e "$IFACE_PATH" ] || continue

        IFACE=$(basename "$IFACE_PATH")
        case "$IFACE" in
            eth*|en*)
                ;;
            *)
                continue
                ;;
        esac

        if is_usb_iface "$IFACE"; then
            echo "$IFACE"
            return 0
        fi
    done

    return 1
}

wait_for_usb_eth() {
    TRIES=${1:-40}
    COUNT=0

    while [ "$COUNT" -lt "$TRIES" ]; do
        IFACE=$(find_usb_eth)
        if [ -n "$IFACE" ]; then
            echo "$IFACE"
            return 0
        fi

        COUNT=$((COUNT + 1))
        msleep 500
    done

    return 1
}

derive_mac() {
    SEED=""

    if [ -f /userdata/ethaddr.txt ]; then
        SEED=$(tr -d ' \t\n\r' < /userdata/ethaddr.txt)
    fi

    if [ -z "$SEED" ] && [ -n "$KOBRA_DEVICE_ID" ]; then
        SEED="$KOBRA_DEVICE_ID"
    fi

    if [ -z "$SEED" ] && [ -f /useremain/dev/device_id ]; then
        SEED=$(tr -d ' \t\n\r' < /useremain/dev/device_id)
    fi

    if [ -z "$SEED" ]; then
        return 1
    fi

    HASH=$(printf '%s' "$SEED" | md5sum | cut -c 1-12)
    FIRST_HEX=$(echo "$HASH" | cut -c 1-2)
    FIRST_OCTET=$((16#$FIRST_HEX))
    FIRST_OCTET=$(( (FIRST_OCTET & 0xFC) | 0x02 ))

    echo "$(printf '%02x' "$FIRST_OCTET"):$(echo "$HASH" | cut -c 3-4):$(echo "$HASH" | cut -c 5-6):$(echo "$HASH" | cut -c 7-8):$(echo "$HASH" | cut -c 9-10):$(echo "$HASH" | cut -c 11-12)"
}

resolve_mac() {
    CUSTOM=$(get_app_property "$APP_NAME" mac_address | tr -d ' \t\n\r' | tr 'A-F' 'a-f')

    if [ -n "$CUSTOM" ]; then
        echo "$CUSTOM"
        return 0
    fi

    derive_mac
}

restart_dhcp() {
    IFACE=$1

    if [ "$(get_app_property "$APP_NAME" restart_dhcp)" = "False" ]; then
        log "DHCP restart disabled by configuration"
        return 0
    fi

    for PID in $(get_by_name udhcpc); do
        CMDLINE=$(tr '\0' '\n' < "/proc/$PID/cmdline" 2>/dev/null)
        IFACE_ARG=$(printf '%s\n' "$CMDLINE" | awk 'found { print; exit } $0 == "-i" { found = 1 }')

        if [ "$IFACE_ARG" != "$IFACE" ]; then
            continue
        fi

        log "Stopping udhcpc (PID $PID) on $IFACE"
        kill "$PID" 2>/dev/null
    done

    msleep 500

    HOSTNAME_VALUE=$(hostname 2>/dev/null)
    log "Starting udhcpc on $IFACE"
    udhcpc -b -i "$IFACE" -x hostname:"$HOSTNAME_VALUE" >> "$LOG_FILE" 2>&1 &
}

apply_mac() {
    IFACE=$(wait_for_usb_eth 60) || {
        log "No USB Ethernet interface found, skipping"
        return 0
    }

    TARGET=$(resolve_mac)

    if [ -z "$TARGET" ]; then
        log "No MAC address configured and unable to derive one, skipping"
        return 0
    fi

    if ! validate_mac "$TARGET"; then
        log "/!\\ Invalid MAC address '$TARGET', skipping"
        return 0
    fi

    if ! is_unicast_mac "$TARGET"; then
        log "/!\\ MAC address '$TARGET' is not unicast, skipping"
        return 0
    fi

    CURRENT=$(cat "/sys/class/net/$IFACE/address" 2>/dev/null)

    if [ "$CURRENT" = "$TARGET" ]; then
        log "MAC address of $IFACE is already $TARGET"
        return 0
    fi

    log "Changing MAC address of $IFACE: $CURRENT -> $TARGET"

    # Bring the interface back up if the worker is interrupted mid-change.
    trap 'ifconfig "$IFACE" up 2>/dev/null' EXIT INT TERM

    ifconfig "$IFACE" down

    if ! ifconfig "$IFACE" hw ether "$TARGET"; then
        log "/!\\ Failed to set MAC address of $IFACE"
        ifconfig "$IFACE" up
        return 1
    fi

    ifconfig "$IFACE" up
    msleep 500

    NEW_MAC=$(cat "/sys/class/net/$IFACE/address" 2>/dev/null)

    if [ "$NEW_MAC" != "$TARGET" ]; then
        log "/!\\ MAC address of $IFACE is $NEW_MAC after applying $TARGET"
        return 1
    fi

    log "MAC address of $IFACE set to $NEW_MAC"

    restart_dhcp "$IFACE"
}

is_our_worker() {
    PID=$1

    if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
        return 1
    fi

    CMDLINE=$(tr '\0' ' ' < "/proc/$PID/cmdline" 2>/dev/null)
    case "$CMDLINE" in
        *"$APP_NAME"*)
            return 0
            ;;
    esac

    return 1
}

status() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE" 2>/dev/null)
        if is_our_worker "$PID"; then
            report_status "$APP_STATUS_STARTED" "$PID" "$LOG_FILE"
            return
        fi

        rm -f "$PID_FILE"
    fi

    IFACE=$(find_usb_eth)
    if [ -n "$IFACE" ]; then
        TARGET=$(resolve_mac)
        CURRENT=$(cat "/sys/class/net/$IFACE/address" 2>/dev/null)

        if [ -n "$TARGET" ] && [ "$CURRENT" = "$TARGET" ]; then
            report_status "$APP_STATUS_STARTED" "" "$LOG_FILE"
            return
        fi
    fi

    report_status "$APP_STATUS_STOPPED" "" "$LOG_FILE"
}

start() {
    stop

    mkdir -p "$RINKHALS_LOGS"
    log "Starting $APP_NAME"

    # Run in the background so the Rinkhals start timeout is never exceeded
    # while the USB adapter is still enumerating.
    ( apply_mac ) >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
}

stop() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE" 2>/dev/null)
        if is_our_worker "$PID"; then
            kill "$PID" 2>/dev/null
        fi
        rm -f "$PID_FILE"
    fi

    return 0
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
