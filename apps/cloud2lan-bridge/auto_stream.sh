#!/bin/sh

log_sh() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S.%N')] [DEBUG-SH] $1"
}

log_sh "Initializing auto_stream pipeline script..."

GKCAM_BIN="/userdata/app/gk/gkcam"
REMOTE_CTRL_MODE_FILE="/useremain/dev/remote_ctrl_mode"
APP_ROOT=$(cd "$(dirname "$0")" && pwd)

is_lan_mode() {
  if [ -f "$REMOTE_CTRL_MODE_FILE" ]; then
    MODE_VALUE=$(tr -d '[:space:]' < "$REMOTE_CTRL_MODE_FILE")
    case "$MODE_VALUE" in
      1|lan|LAN|Lan|true|TRUE|True)
        return 0
        ;;
    esac
  fi
  return 1
}

BRIDGE_PID=""

# Start background watchdog to exit if the parent process dies
PARENT_PID=$PPID
(
  while kill -0 $PARENT_PID 2>/dev/null && kill -0 $$ 2>/dev/null; do
    sleep 5
  done
  pkill -P $$ 2>/dev/null
  kill -9 $$ 2>/dev/null
) &

cleanup_bridge() {
  if [ -n "$BRIDGE_PID" ]; then
    log_sh "Terminating existing cloud bridge processes (PID: $BRIDGE_PID)..."
    kill "$BRIDGE_PID" 2>/dev/null
    pkill -f "ffmpeg -nostdin -loglevel quiet -i http://127.0.0.1:18088/flv" 2>/dev/null
    wait "$BRIDGE_PID" 2>/dev/null
    BRIDGE_PID=""
  fi
}

launch_custom_bridge() {
  cleanup_bridge
  log_sh "Starting custom LAN-to-Agora cloud bridge (Direct VENC with HTTP/FLV STDIN fallback)..."
  
  # Run agora_pusher in VENC mode (channel 0) fed by ffmpeg over HTTP/FLV STDIN fallback
  ffmpeg -nostdin -loglevel quiet -i http://127.0.0.1:18088/flv -vcodec copy -f h264 - | "$APP_ROOT/agora_pusher" "$APPID" "$CHANNEL" "$TOKEN" "$LICENSE" "$AGORA_UID" 0 "$ENC_MODE" "$ENC_KEY" "$ENC_SALT" &
  BRIDGE_PID=$!
  log_sh "Launched custom cloud bridge process (PID: $BRIDGE_PID)."
}

FIFO="/tmp/gkapi_pipe_$$"
rm -f "$FIFO"
mkfifo "$FIFO"
exec 3<>"$FIFO"

exit_cleanup() {
  cleanup_bridge
  pkill -f agora_pusher 2>/dev/null
  exec 3>&-
  rm -f "$FIFO"
  exit 0
}

trap exit_cleanup INT TERM EXIT

# Daemon loop to handle automatic reconnection if connection drops
while true; do
  log_sh "Connecting to gkapi event stream on port 18086..."
  nc 127.0.0.1 18086 < "$FIFO" | tr '}' '\n' | while read -r line; do
    log_sh "RAW EVENT: $line"
    
    # Check if this is a video stream request that needs a reply
    if echo "$line" | grep -q "video_stream_request"; then
      REQ_ID=$(echo "$line" | sed -n 's/.*"video_stream_request":{"id":\([0-9]*\).*/\1/p')
      REQ_METHOD=$(echo "$line" | sed -n 's/.*"video_stream_request":{"id":[0-9]*,"method":"\([^"]*\)".*/\1/p')
      
      if [ -n "$REQ_ID" ] && [ -n "$REQ_METHOD" ]; then
        log_sh "Acknowledging request: ID $REQ_ID, Method $REQ_METHOD"
        REPLY="{\"id\":0,\"method\":\"process_status_update\",\"params\":{\"eventtime\":0,\"response\":\"\",\"status\":{\"video_stream_reply\":{\"id\":$REQ_ID,\"method\":\"$REQ_METHOD\",\"result\":{}}}}}"
        echo "$REPLY" > "$FIFO"
      fi
    fi

    if echo "$line" | grep -q "startLanCapture"; then
      APPID=$(echo "$line" | sed -n 's/.*"appid":"\([^"]*\)".*/\1/p')
      CHANNEL=$(echo "$line" | sed -n 's/.*"channel":"\([^"]*\)".*/\1/p')
      TOKEN=$(echo "$line" | sed -n 's/.*"rtc_token":"\([^"]*\)".*/\1/p')
      LICENSE=$(echo "$line" | sed -n 's/.*"license":"\([^"]*\)".*/\1/p')
      AGORA_UID=$(echo "$line" | sed -n 's/.*"uid":\([0-9]*\).*/\1/p')
      ENC_KEY=$(echo "$line" | sed -n 's/.*"key":"\([^"]*\)".*/\1/p')
      ENC_MODE=$(echo "$line" | sed -n 's/.*"mode":"\([^"]*\)".*/\1/p')
      ENC_SALT=$(echo "$line" | sed -n 's/.*"salt":"\([^"]*\)".*/\1/p')

      if [ -z "$APPID" ] || [ -z "$TOKEN" ]; then
         continue
      fi

      if is_lan_mode; then
        log_sh "remote_ctrl_mode indicates LAN; bypassing gkcam and launching custom cloud bridge."
        launch_custom_bridge
      else
        log_sh "remote_ctrl_mode is not LAN; leaving gkcam path untouched."
      fi
    elif echo "$line" | grep -qE "stopCapture|stopLanCapture"; then
      if is_lan_mode; then
        log_sh "Received stop capture event. Stopping cloud bridge."
        cleanup_bridge
      fi
    fi
  done
  log_sh "Connection to event stream lost. Reconnecting in 2 seconds..."
  sleep 2
done