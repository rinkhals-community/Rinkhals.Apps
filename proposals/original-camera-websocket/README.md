# Anycubic Original Camera – WebSocket Bridge (proposal)

**Review candidate: 1.0.0-rc2. Not a released App Store entry.**

This proposal reuses the stock Anycubic FLV/H.264 camera feed on the Kobra S1 / S1 Combo (model 20025, stock firmware 2.7.2.7, with Rinkhals). It provides a raw Annex B H.264 WebSocket for Mainsail jMuxer, with HLS remuxing as an alternative mode. No external computer or video re-encoding is required. The existing internal ID `39-original-camera-hls` is retained for upgrades.

The files are intentionally under `proposals/`, outside `apps/`: the catalog's generic packaging must not publish an incomplete installer or builds for unsupported printers. This is a draft submission for integration review, not a request to merge an untested production entry.

## Contents

- `39-original-camera-hls/`: manifest, worker, configuration, dependency check and transactional upgrade code.
- `update-install.sh`, `update-rollback.sh`, `install-signals.sh`: existing custom SWU install and rollback entry points.
- `build_packages.sh`: build KS1 USB installation and rollback packages.
- `test_*.py`: eleven local test scripts with mocked printer configuration and synthetic video.
- `README-DE.txt`, `README-EN.txt`, `RELEASE-NOTES.md`: usage, previous reported hardware evidence and remaining hardware checks.
- `LOCAL-VERIFICATION.md`: checks rerun for this proposal.

## Local validation and package build

Requires Python 3.10+, FFmpeg with libx264, `websocket-client`, and standard `sh`, `tar`, `zip` tools. `websocket-client` is a test dependency only.

```sh
python3 -m pip install websocket-client
cd proposals/original-camera-websocket
for test in test_*.py; do python3 "$test" || exit; done
sh build_packages.sh /tmp/original-camera-packages
```

The resulting directories `install/aGVscF9zb3Nf/update.swu` and `rollback/aGVscF9zb3Nf/update.swu` are mutually exclusive USB packages. Follow the full instructions in `README-DE.txt` or `README-EN.txt` and install only on the specified printer/firmware while idle. Do not run the installer or rollback shell scripts on a development computer.

## Integration decisions before promotion to apps/

1. **Install/update lifecycle:** use the custom setup transaction rather than the generic copy-only updater. Keep model/firmware preflight, idle checks, original configuration backup, upgrade handling and exit status reporting. The current TouchUI installs release SWUs through `install_swu`; this must be validated with the custom updater. A committed install with an unconfirmed service restart returns 2; catalog UI currently treats that as failure and needs an agreed presentation.
2. **Model-specific publishing:** publish an `app-39-original-camera-hls-ks1.swu` asset only. Do not generate K2P/K3/K3M packages. The UI also maps KS1M to ks1; the installer must continue rejecting unsupported model IDs. Agree whether catalog model filtering is needed.
3. **Removal:** the current TouchUI removal handler stops/disables/deletes the app directory. It does not call this proposal's rollback. It would leave the managed Moonraker/bridge/MJPEG settings behind. Agree on a rollback-capable removal path before enabling catalog installation. Use the matching custom rollback package in the meantime.
4. **Dependencies:** `cloud2lan-bridge` 1.2+ is required. The custom USB installer can obtain the pinned upstream 20261123_01 SWU, with a SHA-256 check, or use an already installed compatible bridge. Review this policy against catalog dependency installation. The release asset's current bytes were not independently fetched during this review.
5. **Security and permissions:** HTTP diagnostics/HLS bind loopback behind the printer webcam proxy. WebSocket binds the selected LAN IP on port 8086 and is unauthenticated, with a same-IP browser Origin check. Use a trusted LAN; no external exposure. Local MQTT uses existing stock credentials and TLS with certificate verification disabled on loopback. Review before accepting.
6. **Hardware acceptance:** direct RC2 install/update, immediate playback, reboot, 24-hour representative printing, reconnect, failure fallback, removal/rollback and power-loss recovery remain open. Prior hardware observations in the release notes are tester reports about earlier versions, not a new RC2 validation.
7. **Licensing:** agree the contribution's licensing under the repository policy and review dependencies before merging; no third-party camera/FFmpeg binary is bundled here.

No streaming or printer runtime code was changed for this proposal. Do not promote to the live catalog until lifecycle decisions and hardware checks are resolved.
