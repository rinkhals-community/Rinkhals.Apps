Anycubic Original Camera - WebSocket Bridge 1.0.0 RC2
Release candidate community app for Rinkhals Kobra S1/S1 Combo,
model 20025, firmware 2.7.2.7. Uses the stock camera and MQTT over local TLS,
not an exclusive V4L2 camera capture. Defaults to raw H264 Annex B WebSocket mode, via local LAN port 8086, for Mainsail jMuxer.
HLS is the fallback. An explicitly saved mode (including HLS after fallback)
is preserved on upgrade. No external computer and no re-encoding. HLS and WebSocket are alternative
modes, not concurrent streams. The stock Anycubic services remain running.
Install or upgrade from USB only while idle. Switch modes using the Rinkhals
app Configure menu, save, restart camera app if needed and refresh Mainsail.
Use the printer's IP over HTTP for the WebSocket mode, trusted LAN only.
Fallback to HLS on repeated server-side errors is deferred during printing;
client-only playback issues require manual selection of HLS. No reboot is
initiated automatically. Enabled camera service starts immediately after setup and its diagnostics are checked. A manual reboot may still be necessary for the LAN bridge.
Original settings are backed up; a rollback USB package is provided.
Prior hardware tests reported success for 0.2.7 installation tones, print
interlock and rollback. A screenshot shows 0.2.9 enabled and running; no independent confirmation that no manual start was used. RC2 hardware testing and rollback tones remain open. Not submitted to the Rinkhals app store.
Build: sh build_packages.sh <output-directory>
Tests: python test_hotfix.py; python test_idle.py; python test_dependency.py;
python test_restart.py; python test_local.py; python test_low_latency.py;
python test_upgrade.py; python test_default_mode.py; python test_install_signals.py;
python test_rollback_signals.py; python test_camera_start.py. Requires ffmpeg and websocket-client for local tests.

USB INSTALL/UPDATE/ROLLBACK RESULT SOUNDS (0.2.8): original Rinkhals detection tone unchanged.
2 short = success; 3 short = failure; 2 long = preflight blocked (busy/unknown);
short + long = committed but Moonraker reload or camera startup unconfirmed. Best effort only;
never changes installer status. Uses Rinkhals beep helper with existing PWM.
Manage Apps -> Configure -> USB install/update/rollback result sounds: On/Off (default On).
Native Key Sound toggle interaction and actual acoustics unverified on hardware.
Update from 0.2.9 supported; streaming logic unchanged. Explicitly disabled apps stay disabled on upgrade. Repeated installation checks enabled service. Rollback package supports 0.2.7 through 0.2.9 and 1.0.0-rc2;
use matching rollback for older versions. The existing sound preference is
snapshotted before rollback. No streaming changes.

RC2: consistent display name; upgrade from RC1 supported. Internal app ID,
log paths, URLs and [webcam Webcam] are retained for compatibility.
