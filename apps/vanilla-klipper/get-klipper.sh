#!/bin/sh
set -eu
# Run from Docker:
#   docker run --rm -it -v .\apps:/apps ghcr.io/jbatonnet/rinkhals/build /apps/vanilla-klipper/get-klipper.sh

mkdir /work
cd /work

# Klipper
echo "Downloading Klipper..."

# Klipper base commit is auto-detected from the Kobra-S1 fork by
# generate_klipper_patch.sh and stored in klipper-base.txt.
KLIPPER_REF=$(cat /apps/vanilla-klipper/klipper-base.txt)
echo "Using klipper base: $KLIPPER_REF"

wget -O klipper.zip https://github.com/Klipper3d/klipper/archive/$KLIPPER_REF.zip
unzip -d klipper klipper.zip

mkdir -p /apps/vanilla-klipper/klippy
rm -rf /apps/vanilla-klipper/klippy/*

cp -pr /work/klipper/*/klippy/* /apps/vanilla-klipper/klippy/
cp -p /work/klipper/*/scripts/klippy-requirements.txt /apps/vanilla-klipper/

cd /apps/vanilla-klipper
patch -p0 < klippy.patch

#Add driver for ACE Pro and virtual_pins module
# ACE Pro klippy extras + config, auto-generated from the Kobra-S1/ACEPRO fork
grep '^diff --git' acepro.patch | sed 's|.* b/||' | while read -r f; do rm -f "$f"; done
patch -p1 < acepro.patch

# ACE status dashboard (ace_status_integration: moonraker component + web UI),
# auto-generated from the Kobra-S1/ACEPRO fork by generate_ace_dashboard_patch.sh.
# Deployed to Moonraker/Mainsail at runtime by app.sh (self-healing).
grep '^diff --git' ace_dashboard.patch | sed 's|.* b/||' | while read -r f; do rm -f "$f"; done
patch -p1 < ace_dashboard.patch

# Apply our Kobra-S1 klippy changes (extra modules + config)
patch -p1 < kobra_klippy.patch


