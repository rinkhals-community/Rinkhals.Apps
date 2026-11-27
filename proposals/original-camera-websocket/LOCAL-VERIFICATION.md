# Local verification — 27 November 2026

Input: `OriginalCamera_WebSocket_1.0.0-rc2_Quellcode.zip`, 47,503 bytes.
SHA-256: `ba981bb8ad0087131d970af3807b9af75cf7d9425a25638a4b73e7ca65bf40a8`.
Upstream reviewed: `rinkhals-community/Rinkhals.Apps`, develop commit `3620bfb589269d58f4deb235662b2c8fb9d309bd`.

## Passed in this workspace

- Python compilation of all supplied Python files; `sh -n` on all supplied shell files.
- All eleven original `test_*.py` scripts, run as separate Python processes.
- Synthetic H.264 FLV converted to HLS with FFmpeg; HTTP playlist/segment serving and path restriction.
- Twenty synthetic FLV AVC access units transmitted as binary WebSocket frames, decoded with FFmpeg; ping/pong; bounded queue behavior.
- Mocked print-idle checks, default/saved streaming modes, fallback, dependency checksum/traversal rejection, installation signals, startup checks, upgrades, failure restore and rollback wrapper checks.
- `sh build_packages.sh <output-directory>` produced both KS1 SWUs; archive contents inspected without running either installer.

## Scope limits

Mocked printer tests do not establish behavior on hardware. Desktop FFmpeg is not the stock printer FFmpeg. No real printer, firmware, camera, MQTT account or App Store installation was used. The pinned dependency release download, actual acoustic signals, full configuration rollback on hardware and long-duration performance were not independently verified. Syntax checks do not prove runtime safety. See README.md and RELEASE-NOTES.md for catalog blockers and remaining hardware tests.
