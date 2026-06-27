#!/bin/sh
#
# cloud2lan-supervisor.sh — crash-loop restarter for cloud2lan-bridge.py
#
# This script ONLY restarts the Python bridge on crashes. It does NOT
# check remote_ctrl_mode — that is the mode_watchdog's job. When the
# watchdog detects a mode switch, it kills this script's entire process
# group, so we don't need any cleanup logic here.

APP_ROOT=$(dirname $(realpath $0))
cd "$APP_ROOT"

crash_count=0
crash_window_start=$(date +%s)
max_crashes=5
crash_window=300   # 5 minutes
cooldown=10

while true; do
    # Reset crash counter if we've been stable for a while
    current_time=$(date +%s)
    if [ $((current_time - crash_window_start)) -gt $crash_window ]; then
        crash_count=0
        crash_window_start=$current_time
    fi

    echo "$(date): Starting cloud2lan-bridge (crash count: $crash_count/$max_crashes)"
    python3 ./cloud2lan-bridge.py
    exit_code=$?
    echo "$(date): cloud2lan-bridge exited with code $exit_code"

    # Exit code 0 = clean shutdown (e.g. killed by watchdog) — don't restart
    if [ $exit_code -eq 0 ]; then
        echo "$(date): Clean exit, not restarting."
        break
    fi

    crash_count=$((crash_count + 1))
    if [ $crash_count -ge $max_crashes ]; then
        echo "$(date): Too many crashes ($crash_count) in ${crash_window}s, giving up"
        break
    fi

    echo "$(date): Waiting ${cooldown}s before restart..."
    sleep $cooldown
done
