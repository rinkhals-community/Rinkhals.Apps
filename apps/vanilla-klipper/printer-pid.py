"""Use this printer's own heater PID calibration in printer.klipper.cfg.

The configs this app ships carry PID values tuned on someone else's printer.
GoKlipper keeps each printer's own calibration in printer_mutable.cfg (JSON),
and its PID controller is the same code as Klipper's (same PID_PARAM_BASE,
integral limit and anti-windup), so those values can be used as they are.

For [extruder] and [heater_bed], the values in effect in printer.klipper.cfg
(the SAVE_CONFIG block if it sets them, otherwise the section) are replaced
only while they're still exactly what the app shipped. Anything the user has
changed, including a PID_CALIBRATE saved with SAVE_CONFIG, is left alone.

Usage: printer-pid.py SHIPPED_CFG TARGET_CFG MUTABLE_JSON [--dry-run]
Prints one line per heater saying what it did. Never fails the caller.
"""

import json
import os
import re
import sys

SECTIONS = ("extruder", "heater_bed")
KEYS = ("pid_kp", "pid_ki", "pid_kd")
AUTOSAVE = "#*#"

BODY_RE = re.compile(r"^(\s*)(pid_k[pid])(\s*[:=]\s*)([-+0-9.eE]+)(.*)$", re.IGNORECASE)
AUTOSAVE_RE = re.compile(r"^(#\*#\s*)(pid_k[pid])(\s*=\s*)([-+0-9.eE]+)(.*)$", re.IGNORECASE)


def find_pid(lines, section):
    """Return {key: (value, line_index, regex)} for the values in effect."""
    found = {}
    # Section body: from "[section]" up to the next section or the SAVE_CONFIG block
    inside = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if line.startswith(AUTOSAVE):
            break
        if stripped.startswith("["):
            inside = stripped.split("#")[0].strip().lower() == "[%s]" % section
            continue
        if inside:
            m = BODY_RE.match(line)
            if m:
                found[m.group(2).lower()] = (float(m.group(4)), i, BODY_RE)
    # SAVE_CONFIG block overrides the section
    inside = False
    for i, line in enumerate(lines):
        if not line.startswith(AUTOSAVE):
            continue
        body = line[len(AUTOSAVE):].strip()
        if body.startswith("["):
            inside = body.split("#")[0].strip().lower() == "[%s]" % section
            continue
        if inside:
            m = AUTOSAVE_RE.match(line)
            if m:
                found[m.group(2).lower()] = (float(m.group(4)), i, AUTOSAVE_RE)
    return found


def same(a, b):
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def printer_values(mutable, section):
    entry = mutable.get(section)
    if not isinstance(entry, dict) or str(entry.get("control", "pid")).lower() != "pid":
        return None
    values = {}
    for key, value in entry.items():
        if key.lower() in KEYS:
            try:
                values[key.lower()] = float(value)
            except (TypeError, ValueError):
                return None
    if len(values) != 3 or any(v <= 0 for v in values.values()):
        return None
    return values


def fmt(value):
    return ("%.3f" % value).rstrip("0").rstrip(".") if value != int(value) else "%d" % value


def main(argv):
    dry_run = "--dry-run" in argv
    args = [a for a in argv if a != "--dry-run"]
    if len(args) != 3:
        print("usage: printer-pid.py SHIPPED_CFG TARGET_CFG MUTABLE_JSON [--dry-run]")
        return
    shipped_path, target_path, mutable_path = args

    try:
        with open(mutable_path) as f:
            mutable = json.load(f)
    except (OSError, ValueError) as e:
        print("PID: no printer calibration to use (%s), keeping the config's values" % e)
        return
    # newline="" keeps the files' own line endings (some ship with CRLF)
    with open(shipped_path, newline="") as f:
        shipped = f.read().split("\n")
    with open(target_path, newline="") as f:
        target = f.read().split("\n")

    changed = False
    for section in SECTIONS:
        mine = printer_values(mutable, section)
        if mine is None:
            print("PID [%s]: no calibration in %s, left as is" % (section, os.path.basename(mutable_path)))
            continue
        current = find_pid(target, section)
        if set(current) != set(KEYS):
            print("PID [%s]: not set in the config, left as is" % section)
            continue
        if all(same(current[k][0], mine[k]) for k in KEYS):
            print("PID [%s]: already this printer's calibration" % section)
            continue
        defaults = find_pid(shipped, section)
        if set(defaults) != set(KEYS) or not all(same(current[k][0], defaults[k][0]) for k in KEYS):
            print("PID [%s]: changed from what the app shipped, left as is" % section)
            continue
        for key in KEYS:
            _, index, regex = current[key]
            m = regex.match(target[index])
            target[index] = m.group(1) + m.group(2) + m.group(3) + fmt(mine[key]) + m.group(5)
        changed = True
        print("PID [%s]: using this printer's calibration %s / %s / %s (was %s / %s / %s)" % (
            section, fmt(mine["pid_kp"]), fmt(mine["pid_ki"]), fmt(mine["pid_kd"]),
            fmt(current["pid_kp"][0]), fmt(current["pid_ki"][0]), fmt(current["pid_kd"][0])))

    if not changed or dry_run:
        return
    backup = target_path + ".before-printer-pid"
    if not os.path.exists(backup):
        with open(target_path, newline="") as src, open(backup, "w", newline="") as dst:
            dst.write(src.read())
    tmp = target_path + ".tmp"
    with open(tmp, "w", newline="") as f:
        f.write("\n".join(target))
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, target_path)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as e:  # never stop Klipper from starting over this
        print("PID: couldn't apply the printer's calibration: %s" % e)
