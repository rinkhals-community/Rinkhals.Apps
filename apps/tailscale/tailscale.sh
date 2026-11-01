#!/bin/sh

. /useremain/rinkhals/.current/tools.sh

# ---------------------------------------------------------------------------
# Read writable settings the user has *explicitly* set via the Rinkhals Web
# Configure drawer. We deliberately bypass get_app_property here because that
# helper falls through to the manifest default - and if we then "applied" the
# default to tailscaled, we'd clobber whatever the daemon had persisted from
# a previous session. The rule is:
#
#   user explicitly set -> apply
#   user not set         -> leave daemon alone
#
# A missing/empty value here means "user has no opinion".
# ---------------------------------------------------------------------------

USER_CONFIG=/useremain/home/rinkhals/apps/tailscale.config

bool_for_cli() {
    case "$1" in
        True|true|1|yes) echo "true" ;;
        False|false|0|no) echo "false" ;;
        *) echo "" ;;
    esac
}

read_override() {
    [ -f "$USER_CONFIG" ] || { echo ""; return; }
    jq -r --arg k "$1" '.[$k] // empty' "$USER_CONFIG" 2>/dev/null
}

read_user_settings() {
    USER_SSH=$(bool_for_cli "$(read_override ssh_enabled)")
    USER_HOSTNAME=$(read_override hostname)
    USER_EXIT_NODE=$(bool_for_cli "$(read_override advertise_exit_node)")
    USER_DNS=$(bool_for_cli "$(read_override accept_dns)")
    USER_ROUTES=$(bool_for_cli "$(read_override accept_routes)")
}

# ---------------------------------------------------------------------------
# Daemon lifecycle.
# ---------------------------------------------------------------------------

tailscale_cli() {
    "$TAILSCALE_BIN_DIR/tailscale" --socket="$TAILSCALE_SOCKET" "$@"
}

start_daemon() {
    nohup "$TAILSCALE_BIN_DIR/tailscaled" \
        --tun=userspace-networking \
        --statedir="$TAILSCALE_DATA_DIR" \
        --socket="$TAILSCALE_SOCKET" \
        --port=41641 \
        > "$TAILSCALED_LOG_FILE" 2>&1 &

    echo $! > "$TAILSCALE_PID_FILE"
    sleep 3
}

# Manifest defaults for accept_dns / accept_routes. We always apply these on
# bring_up because the original tailscale.sh hardcoded them, and front-panel
# users who never touch the Rinkhals Web portal would otherwise silently
# regress (Tailscale's built-in default for accept_routes is false, which
# breaks subnet-routing scenarios that worked before). The other new
# settings (ssh, hostname, advertise_exit_node) only apply on explicit user
# override, because tailscaled's persisted preference is the right fallback
# when the user has no opinion.
DEFAULT_ACCEPT_DNS=false
DEFAULT_ACCEPT_ROUTES=true

bring_up() {
    UP_ARGS=""
    [ -n "$USER_SSH" ]       && UP_ARGS="$UP_ARGS --ssh=$USER_SSH"
    [ -n "$USER_HOSTNAME" ]  && UP_ARGS="$UP_ARGS --hostname=$USER_HOSTNAME"
    UP_ARGS="$UP_ARGS --accept-dns=${USER_DNS:-$DEFAULT_ACCEPT_DNS}"
    UP_ARGS="$UP_ARGS --accept-routes=${USER_ROUTES:-$DEFAULT_ACCEPT_ROUTES}"
    [ "$USER_EXIT_NODE" = "true" ] && UP_ARGS="$UP_ARGS --advertise-exit-node"

    log "tailscale up $UP_ARGS"
    tailscale_cli up $UP_ARGS >> "$TAILSCALE_LOG_FILE" 2>&1 &
}

# Apply runtime changes that arrive while the daemon is running. We compare
# each setting against the last value we applied so we don't spam the CLI
# every poll when nothing has changed.
APPLIED_SSH=""
APPLIED_HOSTNAME=""
APPLIED_EXIT_NODE=""
APPLIED_DNS=""
APPLIED_ROUTES=""

apply_runtime_changes() {
    if [ -n "$USER_SSH" ] && [ "$USER_SSH" != "$APPLIED_SSH" ]; then
        log "applying ssh=$USER_SSH"
        tailscale_cli set --ssh="$USER_SSH" >> "$TAILSCALE_LOG_FILE" 2>&1 \
            && APPLIED_SSH="$USER_SSH"
    fi
    if [ -n "$USER_HOSTNAME" ] && [ "$USER_HOSTNAME" != "$APPLIED_HOSTNAME" ]; then
        log "applying hostname=$USER_HOSTNAME"
        tailscale_cli set --hostname="$USER_HOSTNAME" >> "$TAILSCALE_LOG_FILE" 2>&1 \
            && APPLIED_HOSTNAME="$USER_HOSTNAME"
    fi
    if [ -n "$USER_EXIT_NODE" ] && [ "$USER_EXIT_NODE" != "$APPLIED_EXIT_NODE" ]; then
        log "applying advertise-exit-node=$USER_EXIT_NODE"
        tailscale_cli set --advertise-exit-node="$USER_EXIT_NODE" >> "$TAILSCALE_LOG_FILE" 2>&1 \
            && APPLIED_EXIT_NODE="$USER_EXIT_NODE"
    fi
    if [ -n "$USER_DNS" ] && [ "$USER_DNS" != "$APPLIED_DNS" ]; then
        log "applying accept-dns=$USER_DNS"
        tailscale_cli set --accept-dns="$USER_DNS" >> "$TAILSCALE_LOG_FILE" 2>&1 \
            && APPLIED_DNS="$USER_DNS"
    fi
    if [ -n "$USER_ROUTES" ] && [ "$USER_ROUTES" != "$APPLIED_ROUTES" ]; then
        log "applying accept-routes=$USER_ROUTES"
        tailscale_cli set --accept-routes="$USER_ROUTES" >> "$TAILSCALE_LOG_FILE" 2>&1 \
            && APPLIED_ROUTES="$USER_ROUTES"
    fi
}

# ---------------------------------------------------------------------------
# Status publishing. We poll both `tailscale status --json` (for connection
# state, peer info, IPs) and `tailscale debug prefs` (for the SSH/DNS/routes
# preferences actually in effect on this node). Capabilities listed in the
# status output reflect what the tailnet ACL grants this node, NOT whether
# the node is offering the feature; prefs are the ground truth.
# ---------------------------------------------------------------------------

CONFIG_DIR=/tmp/rinkhals/apps
CONFIG_FILE="$CONFIG_DIR/tailscale.config"

write_temp_property() {
    local key=$1 value=$2
    mkdir -p "$CONFIG_DIR"
    local current
    current=$(cat "$CONFIG_FILE" 2>/dev/null)
    : "${current:='{}'}"
    echo "$current" | jq --arg k "$key" --arg v "$value" '.[$k] = $v' > "$CONFIG_FILE.tmp" \
        && mv "$CONFIG_FILE.tmp" "$CONFIG_FILE"
}

publish_status() {
    local s p status_json
    s=$(tailscale_cli status --json 2>/dev/null)
    p=$(tailscale_cli debug prefs 2>/dev/null)

    if [ -n "$s" ] && [ -n "$p" ]; then
        status_json=$(jq -nc \
            --argjson s "$s" --argjson p "$p" \
            '{
                state: ($s.BackendState // "Unknown"),
                tailnet_ip: (($s.TailscaleIPs // [])[0] // ""),
                magic_dns: (if ($s.MagicDNSSuffix // "") == "" then ""
                            else (($s.Self.HostName // "") + "." + $s.MagicDNSSuffix) end),
                tailnet_name: ($s.CurrentTailnet.Name // ""),
                hostname: ($s.Self.HostName // ""),
                ssh: ($p.RunSSH // false),
                exit_node: (($p.AdvertiseRoutes // []) != [] or ($s.Self.ExitNodeOption // false)),
                accept_dns: ($p.CorpDNS // false),
                accept_routes: ($p.RouteAll // false),
                online: ($s.Self.Online // false),
                peer_count: (($s.Peer // {}) | length)
            }' 2>/dev/null)
    fi
    [ -z "$status_json" ] && status_json='{"state":"Unknown"}'
    write_temp_property status "$status_json"
}

publish_login_url() {
    local line url
    line=$(tail -n 30 "$TAILSCALE_LOG_FILE" 2>/dev/null | grep https://login.tailscale.com | tail -n 1)
    if [ -n "$line" ]; then
        url=$(echo "$line" | sed -r -e 's/.*(http[s]?:\/\/[^[:space:]]+).*/\1/')
        if [ -n "$url" ]; then
            write_temp_property account_login "$url"
        fi
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

read_user_settings
start_daemon
bring_up

APPLIED_SSH="$USER_SSH"
APPLIED_HOSTNAME="$USER_HOSTNAME"
APPLIED_EXIT_NODE="$USER_EXIT_NODE"
APPLIED_DNS="$USER_DNS"
APPLIED_ROUTES="$USER_ROUTES"

while true; do
    read_user_settings
    apply_runtime_changes
    publish_login_url
    publish_status
    sleep 5
done
