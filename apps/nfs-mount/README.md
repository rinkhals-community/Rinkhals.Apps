# NFS Mount

Replace local gcode storage with NFS to offload I/O operations from the printer's storage.

## Features

- Mounts NFS shares to replace local gcode storage
- Supports both `/userdata/app/gk/printer_data/gcodes` and `/useremain/app/gk/gcodes` directories
- Automatically unmounts and remounts local filesystems when starting/stopping
- Configurable NFS server, port, and share path
- Mounts in the background and retries until the NFS server is reachable, so it survives the boot startup timeout and a network that is not yet up
- Skips mounting until configured (the NFS server and share must be set)

## Configuration

The app must be configured before it will mount. Set the NFS server and share
in the Rinkhals UI (or via the app's config), otherwise it starts but skips
mounting and local storage is left in place.

- `server`: NFS server IP address (no default; required)
- `port`: NFS port (default: 2049)
- `share`: NFS share path (no default; required)

## Installation

1. Clone this repository
2. Configure your NFS server details in `apps/nfs-mount/app.json`
3. Create build directory: `mkdir -p build/dist`
4. Build the SWU package for your printer model:
   ```bash
   export KOBRA_MODEL_CODE="YOUR_MODEL"  # K2P, K3, KS1, or K3M
   docker run --rm -e KOBRA_MODEL_CODE="$KOBRA_MODEL_CODE" -v $(pwd)/build:/build -v $(pwd)/apps:/apps ghcr.io/rinkhals-community/rinkhals/build /bin/bash -c "chmod +x /build/build-swu.sh && /build/build-swu.sh apps/nfs-mount"
   ```
5. Install the SWU file following the [official Rinkhals installation guide](https://rinkhals-community.github.io/Rinkhals/Rinkhals/installation-and-firmware-updates/)
