#!/usr/bin/env bash

HOST="${LAN_MODE_HOST:-localhost}"
PORT="${LAN_MODE_PORT:-18086}"
INTERVAL="${LAN_MODE_INTERVAL:-120}"
TIMEOUT="${LAN_MODE_TIMEOUT:-5}"

QUERY_CMD='{"id":2017,"method":"Printer/QueryLanPrintStatus","params":{}}'
OPEN_CMD='{"id":2015,"method":"Printer/OpenLanPrint","params":{}}'

send_command() {
    local command="$1"

    if command -v timeout >/dev/null 2>&1; then
        printf '%s\003' "$command" | timeout "$TIMEOUT" nc "$HOST" "$PORT" 2>/dev/null
    else
        printf '%s\003' "$command" | nc -w "$TIMEOUT" "$HOST" "$PORT" 2>/dev/null
    fi
}

while true; do
    RESPONSE=$(send_command "$QUERY_CMD")

    if echo "$RESPONSE" | grep -q '"open":1'; then
        echo "$(date) - LAN mode already open"
    elif echo "$RESPONSE" | grep -q '"open":0'; then
        echo "$(date) - LAN mode closed, opening..."
        send_command "$OPEN_CMD" >/dev/null
    else
        echo "$(date) - LAN mode status query failed or returned unexpected response"
    fi

    sleep "$INTERVAL"
done
