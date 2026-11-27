# Anycubic Original Camera – WebSocket Bridge 1.0.0 RC2

Release candidate, 27 November 2026. Target: Kobra S1 / S1 Combo (model 20025), firmware 2.7.2.7, Rinkhals. Based on 0.2.9-beta; no changes to streaming algorithms, service lifecycle or print interlock. Version identifiers and installer/rollback compatibility updated. No official endorsement.

## Hardware evidence reported by the tester
- 0.2.2: USB setup, LAN switch, HLS, Mainsail (after manual Moonraker reload), reboot, Anycubic app and Slicer Next; reconnection tested.
- 0.2.4: update, WebSocket mode, loaded Moonraker configuration and picture in Mainsail; picture latency subjectively acceptable.
- 0.2.6: USB update accepted.
- 0.2.7: install tones, active-print installation interlock, rollback.
- 0.2.9: screenshot shows enabled and running camera app.

## RC2 verification still required
- Direct RC2 installation/update and immediate video in Mainsail without manual start.
- Reboot, simultaneous Anycubic app/Slicer Next/Mainsail in WebSocket mode, reconnection and at least 24 h of representative printing.
- Server-side WebSocket failure and HLS fallback when idle; no mode switch/Moonraker restart while printing.
- RC2 rollback, tones and restoration of original settings; manual reboot after rollback.
- Missing bridge download/install on real printer, DHCP changes, power-loss recovery.

Local tests are separate from hardware tests; see LOCAL-TESTS.txt. The available RAM and CPU figures are system-wide, not measured app overhead. Video endpoints are not authenticated; trusted LAN only. Do not expose ports externally. No support for other models or firmware is claimed.

RC2 changes relative to RC1: consistent public name, backward-compatible
installer/rollback name checks, RC1 upgrade support. No streaming changes.
The Moonraker camera section and internal app ID retain their old names.
