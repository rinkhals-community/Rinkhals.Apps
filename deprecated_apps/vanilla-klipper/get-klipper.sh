#!/bin/sh

# Run from Docker:
#   docker run --rm -it -v .\apps:/apps ghcr.io/rinkhals-community/rinkhals/build /apps/vanilla-klipper/get-klipper.sh

mkdir /work
cd /work


# Klipper - pinned to a specific master commit so each SWU build is
# reproducible. The Rinkhals.Apps auto-bump bot watches this against
# Klipper3d/klipper master and opens a PR when upstream moves; do not
# revert to master.zip without disabling the bot for this app.
KLIPPER_VERSION="2fb3d54e"
KLIPPER_DIRECTORY=/apps/vanilla-klipper


echo "Downloading Klipper $KLIPPER_VERSION..."

wget -O klipper.zip https://github.com/Klipper3d/klipper/archive/${KLIPPER_VERSION}.zip
unzip -d klipper klipper.zip

mkdir -p $KLIPPER_DIRECTORY/klippy
rm -rf $KLIPPER_DIRECTORY/klippy/*

cp -pr /work/klipper/*/klippy/* $KLIPPER_DIRECTORY/klippy/
cp -p /work/klipper/*/scripts/klippy-requirements.txt $KLIPPER_DIRECTORY/

cd $KLIPPER_DIRECTORY
patch -p0 < klippy.patch

# Keep app.json's version field in sync with the pinned commit so the
# Rinkhals Web catalog shows the right value and the bot's drift check
# sees the same source of truth.
sed -i "s/\"version\": *\"[^\"]*\"/\"version\": \"${KLIPPER_VERSION}\"/" $KLIPPER_DIRECTORY/app.json
