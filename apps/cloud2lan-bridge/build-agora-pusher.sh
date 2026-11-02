#!/bin/sh

# Builds agora_pusher with the same uClibc toolchain as the printer. From the
# repo root (on a non-ARM host, install ARM emulation first):
#   docker run --rm --privileged tonistiigi/binfmt --install arm
#   docker run --platform=linux/arm/v7 --rm -v ./apps:/apps ghcr.io/jbatonnet/armv7-uclibc:rinkhals /apps/cloud2lan-bridge/build-agora-pusher.sh
#
# agora_pusher loads the Agora SDK and the Rockchip MPP libraries from the
# printer's firmware at runtime with dlopen, so nothing from them is needed
# to build it.

set -e

cd /apps/cloud2lan-bridge
# uClibc-ng has dlopen in libc, there is no separate libdl
gcc -O2 -Wall -o agora_pusher agora_pusher.c
