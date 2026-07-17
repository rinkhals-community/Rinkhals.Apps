# Cloud 2 Lan bridge

This application adds support for Cloud only features for your printer with the Rinkhals firmware while your printer is in LAN mode.

## Architecture & Process Hierarchy
The application runs as a strict parent→child process tree managed by a inotify watchdog to ensure instant, complete shutdown when switching from LAN mode to Cloud mode:

```text
app.sh start
  └── mode_watchdog.py                  ← Inotify watcher for remote_ctrl_mode, starts cloud2lan-supervisor or kills child processes
        └── cloud2lan-supervisor.sh         ← Crash-loop restarter
              └── cloud2lan-bridge.py           ← MQTT bridge & payload interceptor
                    └── agora_pusher              ← Video streaming
                        └── ffmpeg                    ← Video capture and encoding
```