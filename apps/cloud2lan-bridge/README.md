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

`agora_pusher` is built from `agora_pusher.c` against `agora_rtc_api_interop_rinkhals.h`, a minimal header written from the public [Agora IoT SDK docs](https://api-ref.agora.io/en/iot-sdk/linux/1.x/agora__rtc__api_8h_source.html) so the SDK's own header isn't redistributed. The SDK itself is loaded at runtime from the printer's firmware (`/ac_lib/lib/third_lib/libagora-rtc-sdk.so`).

After changing `agora_pusher.c`, rebuild the committed binary with the printer's uClibc toolchain:

```sh
docker run --platform=linux/arm/v7 --rm -v ./apps:/apps ghcr.io/jbatonnet/armv7-uclibc:rinkhals /apps/cloud2lan-bridge/build-agora-pusher.sh
```
