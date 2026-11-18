#!/bin/sh
# Rebuilds the helixscreen/ payload of this app from HelixScreen's sources.
# The result is committed, so this only runs when moving to a new version.
#
#   ./get-helixscreen.sh
#
# Needs docker and about 3 GB of disk. The compile takes 15 to 20 minutes.
#
# What comes from where:
#   - assets, ui_xml, config, certs: HelixScreen's own release package for the
#     FlashForge AD5M. That is a fully static ARMv7 build for a framebuffer
#     screen, which is what the Kobra printers need too.
#   - bin/helix-screen: compiled here from the same tag, because every small
#     printer package upstream is built without Anycubic ACE support. The only
#     difference from upstream's build is HELIX_HAS_ACE=1.
# Everything outside helixscreen/ is ours and is left alone.

set -e

HELIXSCREEN_VERSION=1.0.10
HELIXSCREEN_PACKAGE_SHA256=cc71cf3e886b1bacfa360f568745099349e32f659f4cd034bc2b4d9aa7310b18

APP=$(cd "$(dirname "$0")" && pwd)
WORK=${WORK:-$APP/../../build/tmp/helixscreen-build}
mkdir -p "$WORK"
cd "$WORK"

echo "Downloading the HelixScreen $HELIXSCREEN_VERSION release package..."
PACKAGE=helixscreen-ad5m-v$HELIXSCREEN_VERSION.tar.gz
[ -f $PACKAGE ] || wget -q -O $PACKAGE \
    "https://github.com/prestonbrown/helixscreen/releases/download/v$HELIXSCREEN_VERSION/$PACKAGE"
echo "$HELIXSCREEN_PACKAGE_SHA256  $PACKAGE" | sha256sum -c -
rm -rf package && mkdir package && tar xzf $PACKAGE -C package

echo "Fetching the sources..."
if [ ! -d src ]; then
    git clone -q --depth 1 -b v$HELIXSCREEN_VERSION https://github.com/prestonbrown/helixscreen src
    git -C src submodule update --init --recursive --depth 1
fi

echo "Building the toolchain image..."
docker image inspect helixscreen/toolchain-ad5m > /dev/null 2>&1 ||
    docker build -q -t helixscreen/toolchain-ad5m -f src/docker/Dockerfile.ad5m src/docker

echo "Compiling helix-screen with ACE support..."
# The sources are copied into a volume and not bind-mounted, so this also works
# where docker runs in a VM that can't see this folder.
docker volume rm -f helixscreen-build > /dev/null 2>&1 || true
docker volume create helixscreen-build > /dev/null
tar cf - -C src . | docker run --rm -i -v helixscreen-build:/src -w /src helixscreen/toolchain-ad5m tar xf -
docker run --rm -v helixscreen-build:/src -w /src -e HOME=/tmp \
    -e HELIX_GIT_HASH=$(git -C src rev-parse --short HEAD) helixscreen/toolchain-ad5m \
    sh -c 'git config --global --add safe.directory "*"; make PLATFORM_TARGET=ad5m SKIP_OPTIONAL_DEPS=1 HELIX_HAS_ACE=1 -j$(nproc)'

echo "Assembling the payload..."
rm -rf "$APP/helixscreen"
mkdir -p "$APP/helixscreen/bin"
for ITEM in assets ui_xml config certs release_info.json; do
    cp -R package/helixscreen/$ITEM "$APP/helixscreen/"
done
# Shipped by upstream with the AD5M's own first-run settings; app.sh writes ours
rm -f "$APP/helixscreen/config/settings.json"
docker run --rm -v helixscreen-build:/src helixscreen/toolchain-ad5m cat /src/build/ad5m/bin/helix-screen \
    > "$APP/helixscreen/bin/helix-screen"
chmod +x "$APP/helixscreen/bin/helix-screen"
docker volume rm -f helixscreen-build > /dev/null

sed -i "s/\"version\": *\"[^\"]*\"/\"version\": \"$HELIXSCREEN_VERSION\"/" "$APP/app.json"
echo "Done. Check the result on a printer before committing it."
