source /useremain/rinkhals/.current/tools.sh

APP_ROOT=$(dirname $(realpath $0))
APP_NAME=$(basename "$APP_ROOT")
STATE_DIR="/tmp/lan_tunnel"
WATCHDOG_PID_FILE="$STATE_DIR/lanModeWatchdog.pid"
FAKE_GKAPI_PID_FILE="$STATE_DIR/fake_gkapi_server.pid"
SOCAT_MAIN_PORT=7003
SOCAT_NOZZLE_PORT=7005
SOCAT_MAIN_PID_FILE="$STATE_DIR/socat-$SOCAT_MAIN_PORT.pid"
SOCAT_NOZZLE_PID_FILE="$STATE_DIR/socat-$SOCAT_NOZZLE_PORT.pid"
NETWORK_GUARD_PID_FILE="$STATE_DIR/network-guard.pid"

property_is_enabled() {
    [ "$1" = "True" ] || [ "$1" = "true" ] || [ "$1" = "1" ]
}

# Echo the PIDs of the udhcpc instances k3sysui started for the given interface.
# Each candidate from the Rinkhals helper is verified before being reported so
# DHCP clients for other printer interfaces are never matched.
get_udhcpc_pids_for_interface() {
    local target_interface="$1"
    local pid cmdline_path command

    for pid in $(get_by_name udhcpc); do
        cmdline_path="/proc/$pid/cmdline"
        [ -r "$cmdline_path" ] || continue

        # The first NUL-separated cmdline field is the executable path. Require
        # basename "udhcpc" so another command mentioning udhcpc is ignored.
        command=$(tr '\000' '\n' < "$cmdline_path" | sed -n '1p')
        case "${command##*/}" in
            udhcpc) ;;
            *) continue ;;
        esac

        # Match the exact argument pair "-i <interface>". This keeps DHCP clients
        # for other printer interfaces alive and avoids substring matches.
        if ! tr '\000' '\n' < "$cmdline_path" | awk -v interface="$target_interface" '
            previous == "-i" && $0 == interface { found = 1 }
            { previous = $0 }
            END { exit !found }
        '; then
            continue
        fi

        echo "$pid"
    done
}

get_interface_ip() {
    local ip=$(ifconfig "$1" 2>/dev/null | grep 'inet addr:' | cut -d: -f2 | awk '{print $1}')
    [ -n "$ip" ] || ip=$(ifconfig "$1" 2>/dev/null | grep 'inet ' | awk '{print $2}')
    echo "$ip"
}

get_interface_mac() {
    local mac=$(ifconfig "$1" 2>/dev/null | grep -o 'HWaddr [^ ]*' | awk '{print $2}')
    [ -n "$mac" ] || mac=$(ifconfig "$1" 2>/dev/null | grep -o 'ether [^ ]*' | awk '{print $2}')
    echo "$mac"
}

# Hold the static configuration instead of winning it once: k3sysui launches a
# fresh udhcpc after interface/address events (observed generations 1751, 2042,
# 2217, 2431, 2603 within 30s at boot), so a single kill+set always loses. Kill
# every udhcpc that appears on the interface and re-assert the IP if DHCP got
# to it first. This only needs to cover the boot-time fight: once nothing has
# had to be corrected for 30s in a row the system has settled and the guard
# EXITS - start() waits for that before launching the latency-critical socat
# bridge, so guard and tunnel never run together. Lowest priority as backstop.
network_guard() {
    local interface="$1"
    local ip_address="$2"
    local netmask="$3"
    local pid current_ip clean=0 acted

    renice 19 $$ >/dev/null 2>&1 || renice -n 19 -p $$ >/dev/null 2>&1 || true

    while [ "$clean" -lt 15 ]; do
        acted=0

        for pid in $(get_udhcpc_pids_for_interface "$interface"); do
            log "Network guard: killing udhcpc PID $pid on $interface"
            kill -KILL "$pid" 2>/dev/null || true
            acted=1
        done

        current_ip=$(get_interface_ip "$interface")
        if [ "$current_ip" != "$ip_address" ]; then
            log "Network guard: IP drifted to '$current_ip', re-asserting $ip_address"
            ifconfig "$interface" "$ip_address" netmask "$netmask"
            acted=1
        fi

        if [ "$acted" = "1" ]; then
            clean=0
        else
            clean=$((clean + 1))
        fi

        sleep 2
    done

    log "Network guard: network settled for 30s, exiting"
    rm -f "$NETWORK_GUARD_PID_FILE"
}

# Bounded wait for the guard to settle and exit, so the socat serial bridge
# never shares the core with it. On timeout we start anyway (guard is nice 19).
wait_for_network_guard_settle() {
    local timeout="${1:-120}"
    local waited=0

    while [ -r "$NETWORK_GUARD_PID_FILE" ] && kill -0 "$(cat "$NETWORK_GUARD_PID_FILE" 2>/dev/null)" 2>/dev/null; do
        if [ "$waited" -ge "$timeout" ]; then
            log "Network guard: still fighting after ${timeout}s, starting tunnel anyway"
            return 0
        fi
        sleep 2
        waited=$((waited + 2))
    done
}

start_network_guard() {
    stop_network_guard
    network_guard "$1" "$2" "$3" &
    echo $! > "$NETWORK_GUARD_PID_FILE"
}

stop_network_guard() {
    local pid

    [ -r "$NETWORK_GUARD_PID_FILE" ] || return 0
    pid=$(cat "$NETWORK_GUARD_PID_FILE")
    rm -f "$NETWORK_GUARD_PID_FILE"
    if [ -n "$pid" ]; then
        kill "$pid" 2>/dev/null || true
    fi
}

# Stop only the udhcpc instance that k3sysui starts for the configured interface.
# It must be stopped before assigning our static IP, otherwise DHCP can overwrite it.
stop_udhcpc_for_interface() {
    local target_interface="$1"
    local pid

    for pid in $(get_udhcpc_pids_for_interface "$target_interface"); do
        log "Stopping udhcpc PID $pid for interface $target_interface"
        kill -TERM "$pid" 2>/dev/null || true
        sleep 1
        if kill -0 "$pid" 2>/dev/null; then
            log "Force-stopping udhcpc PID $pid for interface $target_interface"
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done
}

# Apply MAC and static IP in a single down-window, then bring the interface up
# once. Setting the MAC right after down matters: k3sysui re-ups the interface
# within about a second, and SIOCSIFHWADDR fails with EBUSY on an UP interface
# (why every boot-time MAC change silently failed before). Assigning the IP
# while still down means the single up event exposes the finished config, and
# any udhcpc k3sysui launches in response is handled by the network guard.
apply_network_config() {
    local interface="$1"
    local mac_address="$2"
    local ip_address="$3"
    local netmask="$4"
    local current_mac mac_try mac_output

    if [ -n "$mac_address" ] && [ "$(get_interface_mac "$interface")" != "$mac_address" ]; then
        for mac_try in 1 2 3; do
            log "Bringing $interface down to set MAC $mac_address (attempt $mac_try)"
            ifconfig "$interface" down
            mac_output=$(ifconfig "$interface" hw ether "$mac_address" 2>&1)
            [ -n "$mac_output" ] && log "ifconfig hw ether: $mac_output"

            current_mac=$(get_interface_mac "$interface")
            [ "$current_mac" = "$mac_address" ] && break
            log "MAC is still '$current_mac' after set attempt $mac_try"
            sleep 1
        done
    fi

    stop_udhcpc_for_interface "$interface"

    log "Setting IP $ip_address with netmask $netmask and bringing $interface up"
    ifconfig "$interface" "$ip_address" netmask "$netmask" up
}

verify_network_config() {
    local interface="$1"
    local expected_mac="$2"
    local expected_ip="$3"

    # Dump interface status for debugging
    log "Interface $interface status:"
    ifconfig "$interface" 2>&1 | while IFS= read -r line; do log "  $line"; done

    local current_ip=$(get_interface_ip "$interface")
    log "Verifying interface $interface: current IP='$current_ip', expected IP='$expected_ip'"

    # Verify IP address
    if [ "$current_ip" != "$expected_ip" ]; then
        log "IP address verification failed: expected '$expected_ip', got '$current_ip'"
        return 1
    fi

    # Verify MAC address if specified
    if [ -n "$expected_mac" ]; then
        local current_mac=$(get_interface_mac "$interface")
        log "Verifying interface $interface: current MAC='$current_mac', expected MAC='$expected_mac'"
        if [ "$current_mac" != "$expected_mac" ]; then
            log "MAC address verification failed: expected '$expected_mac', got '$current_mac'"
            return 1
        fi
    fi

    return 0
}

configure_network() {
    NETWORK_ENABLED=$(get_app_property "$APP_NAME" network_enabled)
    property_is_enabled "$NETWORK_ENABLED" || return 0

    INTERFACE=$(get_app_property "$APP_NAME" interface)
    MAC_ADDRESS=$(get_app_property "$APP_NAME" mac_address)
    IP_ADDRESS=$(get_app_property "$APP_NAME" ip_address)
    NETMASK=$(get_app_property "$APP_NAME" netmask)

    if [ -z "$INTERFACE" ] || [ -z "$IP_ADDRESS" ] || [ -z "$NETMASK" ]; then
        log "Ethernet configuration skipped: interface, IP address, and netmask are required"
        return 1
    fi

    for attempt in 1 2 3; do
        log "Configuring Ethernet interface $INTERFACE (attempt $attempt)"

        # The guard re-asserts the IP, which would re-up the interface mid
        # MAC change - keep it out of the down-window, start it right after.
        stop_network_guard
        apply_network_config "$INTERFACE" "$MAC_ADDRESS" "$IP_ADDRESS" "$NETMASK"
        start_network_guard "$INTERFACE" "$IP_ADDRESS" "$NETMASK"
        sleep 1  # Allow interface to stabilize before verification

        if verify_network_config "$INTERFACE" "$MAC_ADDRESS" "$IP_ADDRESS"; then
            log "Ethernet interface $INTERFACE configured; checking again in 2 seconds for DHCP overwrite"
            sleep 2

            if verify_network_config "$INTERFACE" "$MAC_ADDRESS" "$IP_ADDRESS"; then
                log "Ethernet interface $INTERFACE configured successfully"
                return 0
            fi

            log "Ethernet configuration changed after initial verification"
        fi

        if [ "$attempt" -lt 3 ]; then
            log "Configuration verification failed, retrying in 2 seconds..."
            sleep 2
        fi
    done

    log "Failed to configure Ethernet interface $INTERFACE after 3 attempts"
    # Leave DHCP in charge rather than half-holding a config that never verified.
    stop_network_guard
    return 1
}

start_watchdog() {
    WATCHDOG_ENABLED=$(get_app_property "$APP_NAME" watchdog_enabled)
    property_is_enabled "$WATCHDOG_ENABLED" || return 0

    if [ -r "$WATCHDOG_PID_FILE" ] && kill -0 "$(cat "$WATCHDOG_PID_FILE")" 2>/dev/null; then
        log "LAN mode watchdog is already running"
        return 0
    fi

    log "Starting LAN mode watchdog"
    chmod +x "$APP_ROOT/lanModeWatchdog.sh"
    "$APP_ROOT/lanModeWatchdog.sh" &
    echo $! > "$WATCHDOG_PID_FILE"
}

stop_watchdog() {
    if [ -r "$WATCHDOG_PID_FILE" ]; then
        kill "$(cat "$WATCHDOG_PID_FILE")" 2>/dev/null || true
        rm -f "$WATCHDOG_PID_FILE"
    fi
}

record_socat_pid() {
    local pid="$1"
    local pid_file="$2"
    local start_time

    start_time=$(awk '{print $22}' "/proc/$pid/stat" 2>/dev/null)
    [ -n "$start_time" ] && echo "$pid $start_time" > "$pid_file"
}

get_recorded_socat_pid() {
    local pid_file="$1"
    local port="$2"
    local pid start_time command

    [ -r "$pid_file" ] || return 1
    read pid start_time < "$pid_file"

    case "$pid:$start_time" in
        *[!0-9:]*|:) return 1 ;;
    esac
    [ -r "/proc/$pid/cmdline" ] || return 1

    [ "$(awk '{print $22}' "/proc/$pid/stat" 2>/dev/null)" = "$start_time" ] || return 1

    command=$(tr '\000' '\n' < "/proc/$pid/cmdline" | sed -n '1p')
    case "${command##*/}" in
        socat) ;;
        *) return 1 ;;
    esac

    if ! tr '\000' '\n' < "/proc/$pid/cmdline" | awk -v listener="TCP-LISTEN:$port" '
        index($0, listener) == 1 { found = 1 }
        END { exit !found }
    '; then
        return 1
    fi

    echo "$pid"
}

stop_recorded_socat() {
    local pid_file="$1"
    local port="$2"
    local pid

    pid=$(get_recorded_socat_pid "$pid_file" "$port") || {
        rm -f "$pid_file"
        return 0
    }

    rm -f "$pid_file"
    log "Stopping socat PID $pid listening on TCP port $port"
    kill -TERM "$pid" 2>/dev/null || true
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
        log "Force-stopping socat PID $pid listening on TCP port $port"
        kill -KILL "$pid" 2>/dev/null || true
    fi
}

get_tunnel_listener_pids() {
    local pids=""
    local pid

    pid=$(get_recorded_socat_pid "$SOCAT_MAIN_PID_FILE" "$SOCAT_MAIN_PORT") && pids="$pid"
    pid=$(get_recorded_socat_pid "$SOCAT_NOZZLE_PID_FILE" "$SOCAT_NOZZLE_PORT") && pids="${pids}${pids:+ }$pid"

    echo "$pids"
}

stop_tunnel_listeners() {
    stop_recorded_socat "$SOCAT_MAIN_PID_FILE" "$SOCAT_MAIN_PORT"
    stop_recorded_socat "$SOCAT_NOZZLE_PID_FILE" "$SOCAT_NOZZLE_PORT"
}

start_fake_gkapi_server() {
    local pid start_time

    nohup python3 "$APP_ROOT/fake_gkapi_server.py" > /tmp/fake_gkapi.log 2>&1 &
    pid=$!
    start_time=$(awk '{print $22}' "/proc/$pid/stat" 2>/dev/null)
    [ -n "$start_time" ] && echo "$pid $start_time" > "$FAKE_GKAPI_PID_FILE"
}

stop_fake_gkapi_server() {
    local pid

    # Remove PID file if it exists
    rm -f "$FAKE_GKAPI_PID_FILE"

    # Find and stop all fake_gkapi_server.py processes
    for pid in $(ps | grep 'fake_gkapi_server.py' | grep -v grep | awk '{print $1}'); do
        log "Stopping fake gkapi server PID $pid"
        kill -TERM "$pid" 2>/dev/null || true
        sleep 1
        if kill -0 "$pid" 2>/dev/null; then
            log "Force-stopping fake gkapi server PID $pid"
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done
}

reset_mcus() {
    case "${KOBRA_MODEL_CODE:-}" in
        KS1|KS1M)
            log "MCU Reset"
            echo 116 > /sys/class/gpio/export 2>/dev/null || true
            echo out > /sys/class/gpio/gpio116/direction
            echo 0 > /sys/class/gpio/gpio116/value
            sleep 1
            echo 1 > /sys/class/gpio/gpio116/value
            ;;
        *)
            log "MCU reset is not applicable for model ${KOBRA_MODEL_CODE:-unknown}; skipping"
            ;;
    esac
}

status() {
    mkdir -p "$STATE_DIR"
    PIDS=$(get_tunnel_listener_pids)

    if [ -n "$PIDS" ]; then
        report_status $APP_STATUS_STARTED "$PIDS"
    else
        report_status $APP_STATUS_STOPPED
    fi
}
start() {
        (
        mkdir -p "$STATE_DIR"
        echo 1 > "$STATE_DIR/.status"

        if ! configure_network; then
            log "Network configuration failed, tunnel will not start"
            exit 1
        fi
        sleep 5
        start_watchdog
        cd "$APP_ROOT"
        chmod +x ./pwm_jingle.sh
        ./pwm_jingle.sh start
        sleep 1
        log "Started lan_tunnel $EXAMPLE_VERSION from $APP_ROOT"

        sleep 17
        # Do not bring up the serial tunnel while the network guard is active.
        wait_for_network_guard_settle 120

        kill_by_name gklib

        reset_mcus

        log "Socat start"
        sleep 2
        stop_tunnel_listeners
        nice -n -20 socat -ly -d -d -T 10 TCP-LISTEN:$SOCAT_MAIN_PORT,reuseaddr,fork,max-children=1,nodelay,keepalive,keepidle=5,keepintvl=1,keepcnt=3 FILE:/dev/ttyS3,rawer,b576000,echo=0,clocal,crtscts=0 &
        record_socat_pid "$!" "$SOCAT_MAIN_PID_FILE"
        nice -n -20 socat -ly -d -d -T 10 TCP-LISTEN:$SOCAT_NOZZLE_PORT,reuseaddr,fork,max-children=1,nodelay,keepalive,keepidle=5,keepintvl=1,keepcnt=3 FILE:/dev/ttyS5,rawer,b576000,echo=0,clocal,crtscts=0 &
        record_socat_pid "$!" "$SOCAT_NOZZLE_PID_FILE"

        cd "$APP_ROOT"
        chmod +x ./pwm_jingle.sh
        ./pwm_jingle.sh imperial

        # --- GKAPI PATCH/Fake server logic (only for KS1) ---
        if [ "${KOBRA_MODEL_CODE:-}" = "KS1" ] || [ "${KOBRA_MODEL_CODE:-}" = "KS1M" ]; then
            "$APP_ROOT/gkapi_patched_run.sh" ensure-original || true
            killall -q gkapi 2>/dev/null || true
            start_fake_gkapi_server
        fi
        ) &
}

stop() {
    (
        mkdir -p "$STATE_DIR"
        echo 0 > "$STATE_DIR/.status"
        stop_network_guard
        stop_watchdog
        log "Stopped lan_tunnel"
        stop_tunnel_listeners
        sleep 5
        reset_mcus
        sleep 2

        # --- GKAPI PATCH/Fake server logic (only for KS1) ---
        if [ "${KOBRA_MODEL_CODE:-}" = "KS1" ] || [ "${KOBRA_MODEL_CODE:-}" = "KS1M" ]; then
            stop_fake_gkapi_server
            "$APP_ROOT/gkapi_patched_run.sh" run-patched || true
        fi

        cd /userdata/app/gk
        LD_LIBRARY_PATH=/userdata/app/gk:$LD_LIBRARY_PATH \
                ./gklib -a /tmp/unix_uds1 /userdata/app/gk/printer_data/config/printer.generated.cfg &> $RINKHALS_ROOT/logs/gklib.log &
        log "gklib started"
    ) &
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
