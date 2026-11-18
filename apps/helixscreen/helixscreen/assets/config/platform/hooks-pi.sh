#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Platform hooks: Raspberry Pi
#
# Stub implementation. The Pi platform uses systemd for service management,
# so these SysV-style hooks are not currently needed. All functions are
# no-ops to satisfy the hook contract.

platform_stop_competing_uis() {
    :
}

platform_enable_backlight() {
    :
}

platform_wait_for_services() {
    :
}

platform_pre_start() {
    :
}

platform_post_stop() {
    :
}
