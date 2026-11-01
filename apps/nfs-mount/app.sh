#!/bin/sh

source /useremain/rinkhals/.current/tools.sh

APP_ROOT=$(dirname $(realpath $0))
APP_NAME=$(basename $APP_ROOT)

GCODES_PRIMARY="/userdata/app/gk/printer_data/gcodes"
GCODES_BIND="/useremain/app/gk/gcodes"
WORKER_PID_FILE="/tmp/app-$APP_NAME.worker.pid"

stop_worker() {
    if [ -f "$WORKER_PID_FILE" ]; then
        WPID=$(cat "$WORKER_PID_FILE" 2>/dev/null)
        [ -n "$WPID" ] && kill "$WPID" 2>/dev/null
        rm -f "$WORKER_PID_FILE"
    fi
}

# Runs in the background so start() stays well within the boot start timeout.
# Waits for the NFS server to become reachable (retrying a bounded number of
# times) so a network that isn't up yet at boot doesn't permanently fail the
# mount. Reverts to local ext4 storage only after genuinely giving up.
mount_worker() {
    SERVER=$1
    PORT=$2
    SHARE=$3

    ATTEMPTS=30
    i=0
    while [ $i -lt $ATTEMPTS ]; do
        i=$((i + 1))

        # Already mounted? Nothing left to do.
        if mountpoint -q "$GCODES_PRIMARY" 2>/dev/null && mountpoint -q "$GCODES_BIND" 2>/dev/null; then
            log "NFS mount already active"
            return 0
        fi

        # Clean up any stale/partial mounts before (re)trying
        umount -f "$GCODES_BIND" 2>/dev/null
        umount -f "$GCODES_PRIMARY" 2>/dev/null

        # Wrap the mount in a timeout so a slow/unreachable server can't block
        # the worker indefinitely on a single attempt
        if timeout -t 10 mount -o port=$PORT,nolock,proto=tcp -t nfs "$SERVER:$SHARE" "$GCODES_PRIMARY" 2>/dev/null; then
            if mount --bind "$GCODES_PRIMARY" "$GCODES_BIND" 2>/dev/null; then
                log "NFS mount successful: $SERVER:$SHARE -> both locations"
                return 0
            fi

            log "Bind mount failed, unmounting NFS (attempt $i/$ATTEMPTS)"
            umount -f "$GCODES_PRIMARY" 2>/dev/null
        else
            log "NFS mount attempt $i/$ATTEMPTS failed: $SERVER:$SHARE (server not ready?)"
        fi

        sleep 5
    done

    log "NFS mount giving up after $ATTEMPTS attempts, reverting to local storage"
    # Remount original ext4 mount on failure (only userdata location)
    mount -t ext4 -o rw,relatime /dev/block/by-name/useremain "$GCODES_PRIMARY" 2>/dev/null
    return 1
}

status() {
    # Check if both mount points are mounted
    if mountpoint -q "$GCODES_PRIMARY" 2>/dev/null && mountpoint -q "$GCODES_BIND" 2>/dev/null; then
        report_status $APP_STATUS_STARTED "NFS mounted"
    else
        report_status $APP_STATUS_STOPPED
    fi
}

start() {
    # Stop any previous worker so we don't race two mount loops
    stop_worker

    SERVER=$(get_app_property $APP_NAME server)
    PORT=$(get_app_property $APP_NAME port)
    SHARE=$(get_app_property $APP_NAME share)
    PORT=${PORT:-2049}

    # Skip if not configured yet (NFS Server / NFS Share Path are empty)
    if [ -z "$SERVER" ] || [ -z "$SHARE" ]; then
        log "NFS mount skipped: not configured (set NFS Server and NFS Share Path)"
        exit 0
    fi

    log "Starting NFS mount: $SERVER:$SHARE -> $GCODES_PRIMARY and $GCODES_BIND"

    # Create mount points if they don't exist
    mkdir -p "$GCODES_PRIMARY"
    mkdir -p "$GCODES_BIND"

    # Mount in the background so the (non-blocking) start call returns quickly,
    # honoring the startup timeout while the actual mount + retries happen async
    mount_worker "$SERVER" "$PORT" "$SHARE" &
    echo $! > "$WORKER_PID_FILE"
}

stop() {
    # Stop the background mount worker first so it can't re-mount after we clean up
    stop_worker

    log "Stopping NFS mount: both locations"

    # Force unmount both locations (in reverse order due to bind mount)
    umount -f "$GCODES_BIND" 2>/dev/null
    umount -f "$GCODES_PRIMARY" 2>/dev/null

    # Remount original ext4 mount (only userdata location)
    mount -t ext4 -o rw,relatime /dev/block/by-name/useremain "$GCODES_PRIMARY" 2>/dev/null
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
