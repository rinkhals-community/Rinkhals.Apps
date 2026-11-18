#!/bin/sh /etc/rc.common
# SPDX-License-Identifier: GPL-3.0-or-later
#
# K2 (Tina Linux / procd) boot shim — delegates to the SysV-style
# /etc/init.d/S99helixscreen. Required because procd's boot iterator only
# invokes scripts carrying the `#!/bin/sh /etc/rc.common` shebang (a
# DEPEND directive is optional for boot dispatch); a plain SysV script
# without the shebang is skipped at boot, leaving the device stuck on the
# Creality boot logo with no UI.
#
# Keep in sync with install_procd_shim_k2() in
# scripts/lib/installer/service.sh.

START=99
STOP=01
DEPEND=done

boot()    { /etc/init.d/S99helixscreen start; }
start()   { /etc/init.d/S99helixscreen start; }
stop()    { /etc/init.d/S99helixscreen stop; }
restart() { /etc/init.d/S99helixscreen restart; }
status()  { /etc/init.d/S99helixscreen status; }
