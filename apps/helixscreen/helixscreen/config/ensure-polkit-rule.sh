#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
#
# ensure-polkit-rule.sh - Create NetworkManager polkit rule if missing
#
# Called by ExecStartPre=+ in helixscreen.service (runs as root).
# Self-heals installs that predate polkit support in the installer.
#
# Usage: ensure-polkit-rule.sh <username>
#
# Guards: exits cleanly (0) if rule already exists, user is root,
# nmcli is not installed, or no polkit directory is found.

set -e

HELIX_USER="${1:-root}"

# Root doesn't need polkit rules
[ "$HELIX_USER" = "root" ] && exit 0

# No NetworkManager, no rule needed
command -v nmcli >/dev/null 2>&1 || exit 0

# Check if any HelixScreen polkit rule already exists
RULES_FILE="/etc/polkit-1/rules.d/50-helixscreen-network.rules"
PKLA_FILE="/etc/polkit-1/localauthority/50-local.d/helixscreen-network.pkla"
[ -f "$RULES_FILE" ] && exit 0
[ -f "$PKLA_FILE" ] && exit 0

# Prefer modern .rules format (Debian 12+, polkit >= 0.106)
if [ -d "/etc/polkit-1/rules.d" ]; then
    cat > "$RULES_FILE" << EOF
// Installed by HelixScreen — allow service user to manage NetworkManager
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.freedesktop.NetworkManager.") === 0 &&
        subject.user === "${HELIX_USER}") {
        return polkit.Result.YES;
    }
});
EOF
    exit 0
fi

# Fallback to legacy .pkla format (Debian 11 and similar)
if [ -d "/etc/polkit-1/localauthority/50-local.d" ]; then
    cat > "$PKLA_FILE" << EOF
# Installed by HelixScreen — allow service user to manage NetworkManager
# connections (Wi-Fi connect/disconnect/scan)
[HelixScreen NetworkManager access]
Identity=unix-user:${HELIX_USER}
Action=org.freedesktop.NetworkManager.*
ResultAny=yes
ResultInactive=yes
ResultActive=yes
EOF
    exit 0
fi

# No polkit directory found — nothing we can do
exit 0
