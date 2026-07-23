# Cloud 2 Lan bridge

This application adds support for Cloud only features for your printer with the Rinkhals firmware while your printer is in LAN mode.

## Architecture & Process Hierarchy
The application runs as a strict parent→child process tree managed by an inotify watchdog to ensure instant, complete shutdown when switching from LAN mode to Cloud mode:

```text
app.sh start
  └── mode_watchdog.py                  ← Inotify watcher for remote_ctrl_mode, starts cloud2lan-supervisor or kills child processes
        └── cloud2lan-supervisor.sh         ← Crash-loop restarter
              └── cloud2lan-bridge.py           ← MQTT bridge & payload interceptor
                    └── agora_pusher              ← Video streaming
                        └── ffmpeg                    ← Video capture and encoding
```

## Developing

The agora header file can be sourced from the [agora linux SDK docs](https://api-ref.agora.io/en/iot-sdk/linux/1.x/agora__rtc__api_8h_source.html).