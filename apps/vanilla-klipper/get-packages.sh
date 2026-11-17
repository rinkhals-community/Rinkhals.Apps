#!/bin/sh

# Run from Docker, at the repo root (needs ARM emulation, see the Rinkhals.Apps README):
#   docker run --platform=linux/arm/v7 --rm -v ./build/cache/pip:/root/.cache/pip -v ./apps:/apps ghcr.io/jbatonnet/armv7-uclibc:rinkhals sh /apps/vanilla-klipper/get-packages.sh

set -e

cd /apps/vanilla-klipper

# uClibc-ng keeps the math functions in libc and this image has no libm, so
# greenlet's "-lm" fails to link. An empty archive satisfies the linker.
[ -e /usr/lib/libm.so ] || [ -e /usr/lib/libm.a ] || ar rc /usr/lib/libm.a

echo "Removing old packages..."
rm -rf lib

echo "Creating temporary venv..."
python -m venv .
. bin/activate

echo "Installing Klippy requirements..."
python -m pip install -r klippy-requirements.txt -c packages-constraints.txt

echo "Cleaning up..."
rm -rf bin
rm -rf include
rm -rf lib/python3.*/site-packages/_distutils_hack
rm -rf lib/python3.*/site-packages/pip
rm -rf lib/python3.*/site-packages/pip*
rm -rf lib/python3.*/site-packages/pkg_resources
rm -rf lib/python3.*/site-packages/setuptools
rm -rf lib/python3.*/site-packages/setuptools*
rm -f lib/python3.*/site-packages/distutils-precedence.pth
rm -f pyvenv.cfg
find lib/python3.* -name '*.pyc' -type f | xargs rm -f
