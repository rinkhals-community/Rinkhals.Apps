#!/bin/sh

# From a Windows machine:
#   <cd to this git repo root>
#   docker run --rm -it -v .\apps:/apps ghcr.io/rinkhals-community/rinkhals/build /apps/cloudflare-tunnel/get-cloudflared.sh

mkdir /work
cd /work


CLOUDFLARED_VERSION="2026.10.7"
CLOUDFLARED_DIRECTORY=/apps/cloudflare-tunnel


echo "Downloading cloudflared..."

wget -O cloudflared https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-arm

mkdir -p $CLOUDFLARED_DIRECTORY/bin
rm -rf $CLOUDFLARED_DIRECTORY/bin/*
cp -p /work/cloudflared $CLOUDFLARED_DIRECTORY/bin/cloudflared
chmod +x $CLOUDFLARED_DIRECTORY/bin/cloudflared

sed -i "s/\"version\": *\"[^\"]*\"/\"version\": \"${CLOUDFLARED_VERSION}\"/" $CLOUDFLARED_DIRECTORY/app.json
