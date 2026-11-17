# Turn an ACEPRO reference config into the one this app ships (see get-klipper.sh).
#
# ACEPRO's printer configs carry two copies of the [virtual_sdcard], [mcu] and
# [mcu nozzle_mcu] sections: one under "For vanilla-klipper on SoC", commented
# out, and one under "For tunneled-klipper", active. Swap them, so Klipper on
# the printer uses /dev/ttyS* and the printer's gcode folder. Its ACE configs
# turn the ACE on by default; this app has always shipped it off.

function is_serial_line(s) {
    return s ~ /^\[(virtual_sdcard|mcu|mcu nozzle_mcu)\][ \t]*$/ ||
           s ~ /^(path|serial|restart_method|baud)[ \t]*:/
}

/^#+For vanilla-klipper on SoC/ { region = "soc"; print; next }
/^#+For tunneled-klipper/       { region = "tunnel"; print; next }

region == "soc" {
    line = $0
    sub(/^#[ ]?/, "", line)
    if ($0 ~ /^#/ && is_serial_line(line)) { print line; next }
}

region == "tunnel" {
    # The tunneled block ends at the first section that isn't one of its own
    if ($0 ~ /^\[/ && !is_serial_line($0)) { region = "" }
    else if (is_serial_line($0)) { print "#" $0; next }
}

/^\[output_pin ACE_Pro\]/ { ace_pro = 1; print; next }
ace_pro && /^\[/          { ace_pro = 0 }
ace_pro && /^value:/      { print "value: 0"; ace_pro = 0; next }

{ print }
