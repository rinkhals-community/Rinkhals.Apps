#!/usr/bin/env python3
"""
mode_watchdog.py — watcher for /useremain/dev/remote_ctrl_mode

When the mode file changes to CLOUD, the entire child process group is killed
with SIGKILL. When it changes back to LAN, the supervisor is respawned.

It also owns the app's "lan_mode" setting. The touchscreen's LAN toggle unbinds
the printer from the Anycubic account before switching (firmware 2.7.2.7 on the
KS1 does), which leaves the bridge nothing to serve. Changing the setting
switches modes through gkapi directly, without the unbind, and the setting is
kept in step with the real mode whichever way it changes.
"""

import os
import sys
import json
import signal
import socket
import subprocess
import time
from datetime import datetime

MODE_FILE = "/useremain/dev/remote_ctrl_mode"
SUPERVISOR_SCRIPT = "cloud2lan-supervisor.sh"
POLL_INTERVAL = 1

# Rinkhals keeps a user app's settings next to its directory, see tools.sh
# set_app_property
CONFIG_FILE = os.path.dirname(os.path.realpath(__file__)) + ".config"
LAN_SETTING = "lan_mode"

# The JSON-RPC endpoint the touchscreen uses to talk to gkapi. Messages are
# JSON objects terminated by 0x03
GKAPI_ADDRESS = ("127.0.0.1", 18086)
GKAPI_RPC_ID = 31337

def log(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [watchdog] {msg}", flush=True)

def is_lan_mode():
    try:
        with open(MODE_FILE, "r") as f:
            val = f.read().strip().lower()
        return val in ("1", "lan", "true")
    except Exception:
        return False

# ---------------------------------------------------------------------------
# LAN mode setting
# ---------------------------------------------------------------------------
def read_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def read_lan_setting():
    value = read_config().get(LAN_SETTING)
    if value is None:
        return None
    return str(value).lower() == "true"

def write_lan_setting(lan):
    config = read_config()
    config[LAN_SETTING] = "True" if lan else "False"
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f)
    except Exception as e:
        log(f"Could not save the LAN mode setting: {e}")

def gkapi_call(method):
    request = json.dumps({"id": GKAPI_RPC_ID, "method": method, "params": {}})
    with socket.create_connection(GKAPI_ADDRESS, timeout=5) as s:
        s.sendall(request.encode() + b"\x03")
        buffer = b""
        while True:
            data = s.recv(4096)
            if not data:
                raise ConnectionError("gkapi closed the connection")
            buffer += data
            # gkapi also pushes status updates on the connection, skip them
            while b"\x03" in buffer:
                message, buffer = buffer.split(b"\x03", 1)
                response = json.loads(message)
                if response.get("id") == GKAPI_RPC_ID:
                    if "error" in response:
                        raise RuntimeError(response["error"])
                    return response.get("result")

def switch_mode(lan):
    method = "Printer/OpenLanPrint" if lan else "Printer/CloseLanPrint"
    log(f"LAN mode setting changed, calling {method}")
    try:
        gkapi_call(method)
        return True
    except Exception as e:
        log(f"{method} failed: {e}")
        return False

# ---------------------------------------------------------------------------
# Child process management
# ---------------------------------------------------------------------------
_child_pgid = None
_child_proc = None

# Supervisor first, so it can't restart the bridge while we kill it
KILL_PATTERNS = (
    "cloud2lan-supervisor.sh",
    "cloud2lan-bridge.py",
    "agora_pusher",
    "ffmpeg -nostdin -loglevel quiet",
)

def kill_by_cmdline(pattern):
    # busybox ps cuts command lines short, which hides the supervisor's script
    # name, so tools.sh kill_by_name misses it. Match on the full cmdline instead
    me = os.getpid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if pattern in cmdline:
            try:
                os.kill(int(pid), signal.SIGKILL)
            except OSError:
                pass

def kill_children():
    global _child_pgid, _child_proc
    log("Killing all bridge processes...")
    if _child_pgid is not None:
        try:
            os.killpg(_child_pgid, signal.SIGKILL)
        except OSError:
            pass
    # The agora pipeline runs in its own session, and a supervisor from an
    # earlier run may still be around, so sweep by name as well
    for pattern in KILL_PATTERNS:
        kill_by_cmdline(pattern)
    if _child_proc is not None:
        try:
            _child_proc.wait(timeout=2)
        except Exception:
            pass

    _child_pgid = None
    _child_proc = None

def spawn_supervisor():
    global _child_pgid, _child_proc
    script_dir = os.path.dirname(os.path.realpath(__file__))
    supervisor_path = os.path.join(script_dir, SUPERVISOR_SCRIPT)
    log_file_path = os.path.join(
        os.environ.get("RINKHALS_LOGS", "/tmp/rinkhals"),
        "app-cloud2lan-bridge.log",
    )

    log(f"Spawning supervisor: {supervisor_path}")
    try:
        log_file = open(log_file_path, "a", buffering=1)
    except Exception:
        log_file = subprocess.DEVNULL

    proc = subprocess.Popen(
        ["/bin/sh", supervisor_path],
        cwd=script_dir,
        stdout=log_file,
        stderr=log_file,
        preexec_fn=os.setsid,  # new process group — killable as a unit
    )
    _child_proc = proc
    _child_pgid = os.getpgid(proc.pid)
    log(f"Supervisor started (PID {proc.pid}, PGID {_child_pgid})")

# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------
def on_sigterm(signum, frame):
    log("Received SIGTERM, shutting down...")
    kill_children()
    sys.exit(0)

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    signal.signal(signal.SIGTERM, on_sigterm)
    signal.signal(signal.SIGINT, on_sigterm)

    log("Started. Watching " + MODE_FILE)

    last_lan = None
    last_setting = None

    while True:
        lan = is_lan_mode()
        setting = read_lan_setting()

        if lan != last_lan:
            # The mode changed at startup, from the touchscreen or after our own
            # switch. Show the real mode in the setting, never act on a stale one
            if setting != lan:
                write_lan_setting(lan)
            last_setting = lan
        elif setting is not None and setting != last_setting:
            last_setting = setting
            if setting != lan and not switch_mode(setting):
                write_lan_setting(lan)
                last_setting = lan
        last_lan = lan

        if lan:
            if _child_pgid is None:
                log("LAN mode detected. Starting bridge...")
                spawn_supervisor()
            # Check if child died unexpectedly
            elif _child_proc is not None and _child_proc.poll() is not None:
                log("Supervisor exited unexpectedly. Restarting...")
                kill_children()
                spawn_supervisor()
        else:
            if _child_pgid is not None:
                log("CLOUD mode detected. Killing bridge...")
                kill_children()

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
