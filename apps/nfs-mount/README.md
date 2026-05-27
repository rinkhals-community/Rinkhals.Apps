# NFS Mount

Replace local gcode storage with NFS to offload I/O operations from the printer's storage.

## Features

- Mounts NFS shares to replace local gcode storage
- Supports both `/userdata/app/gk/printer_data/gcodes` and `/useremain/app/gk/gcodes` directories
- Automatically unmounts and remounts local filesystems when starting/stopping
- Configurable NFS server, port, and share path
- Skips mounting if using placeholder defaults (requires configuration)

## Configuration

- `server`: NFS server IP address (default: 192.168.1.100)
- `port`: NFS port (default: 2049)
- `share`: NFS share path (default: /mnt/share)

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
