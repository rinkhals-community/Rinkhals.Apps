# Unbiased Bed Mesh

A Rinkhals app for the Anycubic Kobra 3. It probes the bed mesh point by point, spiralling onto each point so the direction the head arrives from stops skewing the reading. That cancels the probe bias baked into Klipper's snake-pattern calibration.

## The problem

`BED_MESH_CALIBRATE` walks the bed in a snake: one row is probed moving in +X, the next moving in -X, and so on. The strain gauge probe on a Kobra reads a slightly different Z depending on which way along X the head was travelling when it touched down. On one K3 that came to roughly ±0.02 to ±0.08 mm, and the error grew about threefold from one part of the bed to another. Since alternate rows are probed in opposite X directions, the error alternates with them, and the saved mesh ends up with a zigzag that prints as an uneven first layer.

## What it does

For every point it spirals in through all four quadrants and touches down only at the end, so both X belt directions are exercised before the reading and no single direction can dominate. Step by step:

1. Reads the current mesh geometry from Moonraker (the `bed_mesh` and `bed_mesh default` objects).
2. Homes, heats the bed to 60°C and the hotend to 170°C, wipes the nozzle, then cools the hotend to 140°C for probing, mirroring GoKlipper's own `BED_MESH_CALIBRATE` heating sequence. From a cold printer this alone can take several minutes.
3. Walks the mesh row by row, from low Y to high Y, approaching each row from the same side.
4. Spirals onto each point (eight segments, four quadrants, about 47 mm of travel at 1500 mm/min) and probes once it arrives.
5. Probes with `SAMPLE_RETRACT_DIST=5` to work around the strain gauge triggering again on the next sample.
6. Writes the mesh into `printer_mutable.cfg` using the same JSON layout GoKlipper writes, leaving any other keys (such as `input_shaper`) untouched.
7. Reminds you to reboot the printer. GoKlipper only loads the mesh at startup, and Rinkhals refuses `RESTART` / `FIRMWARE_RESTART` because they lock GoKlipper up, so a reboot is the way to apply it.

It runs once and exits. No daemon, nothing watching files, nothing that starts it on its own.

## Supported printers

Only the Kobra 3 so far, since that's where it has been run. Other models heat, wipe and park differently (the S1 family has no `MOVE_HEAT_POS`, for example), so each needs its own sequence and a test run before it's added. On any other model the app logs why and stops without moving anything.

## Before you run it

Run a normal `BED_MESH_CALIBRATE` at least once on the current geometry first. That fills in `printer_mutable.cfg`, and this app reads `mesh_min`, `mesh_max`, `probe_count`, `mesh_x_pps`, `mesh_y_pps`, `algo`, and `tension` from it. If the file is empty, for example after you change the `[bed_mesh]` section, the app stops with a clear message.

## Running it

Leave the app **disabled**. On the touchscreen, go to Settings > Rinkhals > Apps, tap the app's name and use Start (not the checkbox, which enables the app). Over SSH:

```sh
/useremain/home/rinkhals/apps/unbiased-bed-mesh/app.sh start
```

Either way, `start` returns immediately: it hands the run off to `compensator.py` in the background and gets out of the way. The run itself takes longer: heating first (a few minutes from a cold printer), then probing at roughly 10 s per point (about 17 minutes for a 10x10 mesh). Watch it with:

```sh
tail -f /tmp/rinkhals/app-unbiased-bed-mesh.log
```

or check the app's "Last run" setting in the UI once it finishes. To abort a run in progress, use `app.sh stop` (or Stop in the UI); it cools the bed and hotend down and restores the gcode state before exiting.

**When it finishes, reboot the printer** to load the new mesh, and don't change any settings that get saved (a Z offset, `SAVE_CONFIG`) before you do, since GoKlipper may write its old mesh back over the new one.

## Why it has to stay disabled

This app moves the head and heats the printer, so it must never start by itself. Rinkhals starts every enabled app at boot, so if the app is enabled, `app.sh` refuses to run and says so in "Last run". Disable it again and use Start.
