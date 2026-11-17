#!/usr/bin/env python3
# Wait for Klippy to report "ready" on its API socket.
#
#   ready_check.py [socket] [timeout_seconds]
#
# Exits 0 once Klippy is ready, 1 if it isn't by the deadline. Prints the last
# state and message Klippy gave, so the caller can log why it gave up.

import json
import socket
import sys
import time

SOCKET_PATH = sys.argv[1] if len(sys.argv) > 1 else "/tmp/unix_uds1"
TIMEOUT = float(sys.argv[2]) if len(sys.argv) > 2 else 180.0


def query_info():
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect(SOCKET_PATH)
        sock.sendall(json.dumps({"id": 1, "method": "info", "params": {}}).encode() + b"\x03")
        data = b""
        while b"\x03" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    finally:
        sock.close()
    return json.loads(data.split(b"\x03", 1)[0]).get("result", {})


def main():
    deadline = time.monotonic() + TIMEOUT
    last = "no answer on " + SOCKET_PATH
    while time.monotonic() < deadline:
        try:
            info = query_info()
            state = info.get("state", "")
            if state == "ready":
                print("Klippy is ready")
                return 0
            last = "%s: %s" % (state, info.get("state_message", "").strip())
        except (OSError, ValueError) as e:
            last = "no answer on %s (%s)" % (SOCKET_PATH, e)
        time.sleep(2)
    print("Klippy not ready after %ds, last state %s" % (TIMEOUT, last))
    return 1


if __name__ == "__main__":
    sys.exit(main())
