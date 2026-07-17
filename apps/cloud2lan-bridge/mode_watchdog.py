#!/usr/bin/env python3
"""
mode_watchdog.py — inotify-based watcher for /useremain/dev/remote_ctrl_mode

Process tree:
    mode_watchdog.py
      └── [LAN only] cloud2lan-supervisor.sh (in its own process group)
            └── cloud2lan-bridge.py
                  └── ffmpeg | agora_pusher

When the mode file changes to CLOUD, the entire child process group is killed
with SIGKILL. When it changes back to LAN, the supervisor is respawned.
"""

import os
import sys
import signal
import subprocess
import ctypes
import ctypes.util
import struct
import time
from datetime import datetime

MODE_FILE = "/useremain/dev/remote_ctrl_mode"
SUPERVISOR_SCRIPT = "cloud2lan-supervisor.sh"

# inotify constants
IN_MODIFY = 0x00000002
IN_CLOSE_WRITE = 0x00000008
IN_MASK = IN_MODIFY | IN_CLOSE_WRITE

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
# inotify via ctypes (no external dependencies)
# ---------------------------------------------------------------------------
_libc = None

def _load_libc():
    global _libc
    if _libc is not None:
        return _libc
    # Try common libc names — works on glibc, musl, uclibc
    for name in (ctypes.util.find_library("c"), "libc.so.6", "libc.so", None):
        try:
            _libc = ctypes.CDLL(name, use_errno=True)
            return _libc
        except (OSError, TypeError):
            continue
    return None

def inotify_wait(path):
    """Block until `path` is modified. Returns True on success, False if
    inotify is unavailable (caller should fall back to polling)."""
    libc = _load_libc()
    if libc is None:
        return False
    try:
        fd = libc.inotify_init()
        if fd < 0:
            return False
        wd = libc.inotify_add_watch(fd, path.encode("utf-8"), IN_MASK)
        if wd < 0:
            os.close(fd)
            return False
        # Block until the kernel delivers an event
        os.read(fd, 4096)
        os.close(fd)
        return True
    except Exception:
        return False

def wait_for_mode_change():
    """Wait for the mode file to change. Uses inotify; falls back to 2s poll."""
    if not inotify_wait(MODE_FILE):
        time.sleep(2)

# ---------------------------------------------------------------------------
# Child process management
# ---------------------------------------------------------------------------
_child_pgid = None
_child_proc = None

def kill_children():
    global _child_pgid, _child_proc
    log("Killing all bridge processes forcefully by name...")
    # Emulate app.sh stop (but don't kill mode_watchdog.py itself)
    cmd = """
    . /useremain/rinkhals/.current/tools.sh
    kill_by_name cloud2lan-supervisor.sh
    kill_by_name cloud2lan-bridge.py
    kill_by_name agora_pusher
    kill_by_name "ffmpeg -nostdin -loglevel quiet -i http://127.0.0.1:18088/flv"
    """
    subprocess.call(["/bin/sh", "-c", cmd])
    
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

    while True:
        if is_lan_mode():
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

        wait_for_mode_change()

if __name__ == "__main__":
    main()
