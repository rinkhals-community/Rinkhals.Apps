#!/bin/sh
set -eu
# Builds shaper-calibrate for the printers (32-bit ARM, static, no libc), from
# this folder. Needs Go; the toolchain version is pinned so that the same
# source always gives the same file, which CI checks against the committed one.
#
#   sh apps/vanilla-klipper/shaper-calibrate/build.sh

cd "$(dirname "$0")"

GOTOOLCHAIN=go1.26.1 CGO_ENABLED=0 GOOS=linux GOARCH=arm GOARM=7 GOFLAGS= \
    go build -trimpath -buildvcs=false -ldflags="-s -w -buildid=" -o shaper-calibrate .

ls -l shaper-calibrate
