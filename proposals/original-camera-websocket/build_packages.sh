#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUT=${1:-$ROOT/dist}; mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
for TYPE in install rollback; do
  mkdir -p "$TMP/$TYPE/src" "$TMP/$TYPE/outer/update_swu"
  cp "$ROOT/update-$TYPE.sh" "$TMP/$TYPE/src/update.sh"
  cp "$ROOT/install-signals.sh" "$TMP/$TYPE/src/"
  if [ "$TYPE" = install ]; then
    mkdir "$TMP/$TYPE/src/39-original-camera-hls"
    cp "$ROOT/39-original-camera-hls/"*.py "$ROOT/39-original-camera-hls/app.json" "$ROOT/39-original-camera-hls/app.sh" "$TMP/$TYPE/src/39-original-camera-hls/"
    chmod +x "$TMP/$TYPE/src/39-original-camera-hls/app.sh"
  fi
  chmod +x "$TMP/$TYPE/src/update.sh"
  tar -czf "$TMP/$TYPE/outer/update_swu/setup.tar.gz" -C "$TMP/$TYPE/src" .
  # The public stock KS1 packaging password, as specified by Rinkhals build/tools.sh.
  (cd "$TMP/$TYPE/outer" && zip -q -0 -P 'U2FsdGVkX1+lG6cHmshPLI/LaQr9cZCjA8HZt6Y8qmbB7riY' -r "$TMP/$TYPE/update.swu" update_swu)
  mkdir -p "$OUT/$TYPE/aGVscF9zb3Nf"
  cp "$TMP/$TYPE/update.swu" "$OUT/$TYPE/aGVscF9zb3Nf/update.swu"
done
cp "$ROOT/README-DE.txt" "$OUT/ANLEITUNG.txt"
