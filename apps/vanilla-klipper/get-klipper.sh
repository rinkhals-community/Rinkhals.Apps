#!/bin/sh
set -eu
# Run from Docker, at the repo root:
#   docker run --rm -v ./apps:/apps ghcr.io/rinkhals-community/rinkhals/build sh /apps/vanilla-klipper/get-klipper.sh
#
# Klipper comes from Kobra-S1's fork (mainline Klipper plus the Kobra modules),
# the ACE Pro driver, its dashboard and the reference configs from
# Kobra-S1/ACEPRO. Both are pinned below, and scripts/auto-bump-versions.sh
# moves the pins through the "track" block in app.json.

KLIPPER_VERSION=880188ba35600c57e60865192a494dd5af88f809
ACEPRO_VERSION=200583b525c3340dc9d0e3d39268590af2348334

APP=/apps/vanilla-klipper

rm -rf /work
mkdir -p /work
cd /work

echo "Downloading Klipper (Kobra-S1/klipper-kobra-s1 $KLIPPER_VERSION)..."
wget -q -O klipper.zip "https://github.com/Kobra-S1/klipper-kobra-s1/archive/$KLIPPER_VERSION.zip"
unzip -q -d klipper klipper.zip
KLIPPER=$(echo /work/klipper/*)

echo "Downloading the ACE Pro driver (Kobra-S1/ACEPRO $ACEPRO_VERSION)..."
wget -q -O acepro.zip "https://github.com/Kobra-S1/ACEPRO/archive/$ACEPRO_VERSION.zip"
unzip -q -d acepro acepro.zip
ACEPRO=$(echo /work/acepro/*)

# Klippy
rm -rf "$APP/klippy"
cp -pr "$KLIPPER/klippy" "$APP/klippy"
cp -p "$KLIPPER/scripts/klippy-requirements.txt" "$APP/"
cd "$APP"
patch -p0 < klippy.patch

# SHAPER_CALIBRATE without numpy: klippy.patch makes shaper_calibrate.py fall
# back to this module, which runs the compiled shaper-calibrate/shaper-calibrate
# (built by shaper-calibrate/build.sh)
cp -p "$APP/shaper-calibrate/shaper_calibrate_native.py" "$APP/klippy/extras/"

# ACE Pro driver
rm -rf "$APP/klippy/extras/ace"
cp -pr "$ACEPRO/extras/ace" "$APP/klippy/extras/ace"
cp -p "$ACEPRO/extras/temperature_ace.py" "$ACEPRO/extras/virtual_pins.py" "$APP/klippy/extras/"

# ACE status dashboard, deployed to Moonraker and Mainsail at runtime by app_real.sh
rm -rf "$APP/ace_status_integration"
cp -pr "$ACEPRO/ace_status_integration" "$APP/ace_status_integration"

# Reference configs. ACEPRO ships them set up for tunneled-klipper; this app
# runs Klipper on the printer, so vanilla-cfg.awk enables the on-printer serial
# ports and SD path, comments out the tunneled ones, and leaves the ACE off
# until the user turns it on, as in the configs this app has always shipped.
for f in ace_macros_generic.cfg printer_generic_macros.cfg spoolman_logic.cfg; do
    cp -p "$ACEPRO/config/$f" "$APP/$f"
done
for model in K3 K3M KS1; do
    awk -f "$APP/vanilla-cfg.awk" "$ACEPRO/config/printer_$model.cfg" > "$APP/printer_$model.cfg"
    awk -f "$APP/vanilla-cfg.awk" "$ACEPRO/config/ace_$model.cfg" > "$APP/ace_$model.cfg"
done

rm -rf /work
echo "Done."
