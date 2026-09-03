#!/usr/bin/env python
"""
unbiased-bed-mesh: one-shot custom bed-mesh probe.

Replaces GoKlipper's bias-prone snake probing with a per-point spiral
approach that washes out directional bias by traversing the area around
each probe point from every direction before touching down. The spiral is
intentionally slow to let belts and cables relax.

On invocation:
  1. Reads the active mesh geometry from Moonraker
     (printer.objects.query → bed_mesh / bed_mesh default). If no mesh
     exists yet, exits with an actionable error.
  2. Walks the full N x M grid row by row (low Y to high Y), normalising
     Y approach direction once per row, then spiraling into each point.
  3. Subtracts probe.z_offset from each trigger Z so the round-trip
     cancels (GoKlipper applies stored + z_offset during prints).
  4. Writes the probed values into printer_mutable.cfg in the same
     schema GoKlipper itself uses, preserving any other top-level keys
     in the file (e.g. input_shaper).
  5. Logs a reminder that Klipper must be restarted manually for the
     new mesh to take effect.

Module layout:
  log.py       shared timestamped logger
  ws_client.py JSON-RPC WebSocket transport to Moonraker
  mesh.py      Point / BedMeshConfig + file I/O (mutable cfg, app.json)
  printer.py   Klipper protocol calls, motion, heating, spiral probing
  compensator.py (this file) orchestration only
"""

import signal
import sys
from itertools import groupby
from typing import Any

from log import log
from mesh import (
    LOG_FILE_HINT,
    MUTABLE_CFG_PATH,
    BedMeshConfig,
    Point,
    build_grid,
    log_mesh_table,
    write_mesh_to_mutable,
    write_status,
)
from printer import (
    GCODE_STATE_NAME,
    MOVE_TIMEOUT_S,
    PROBE_MAX_ATTEMPTS,
    ProbeCollector,
    cool_down,
    heat_up,
    identify,
    preflight_ok,
    prepare_row,
    probe_with_retry,
    query_bed_mesh,
    query_probe_z_offset,
)
from ws_client import JsonRpcWebSocket, WebSocketError


def install_sigterm_handler(ws: JsonRpcWebSocket) -> None:
    """
    On SIGTERM (e.g. `app.sh stop`), turn heaters off, best-effort restore
    the gcode state, and exit non-zero. Cooling comes first: a stop during
    heating must not leave the bed and hotend energized. Never blocks forever.
    """

    def handler(signum: int, frame: Any) -> None:
        log(f"caught signal {signum}, cooling down and restoring state before exit")
        cool_down(ws)
        try:
            ws.call(
                "printer.gcode.script",
                params={"script": f"RESTORE_GCODE_STATE NAME={GCODE_STATE_NAME}"},
                timeout=MOVE_TIMEOUT_S,
            )
        except (WebSocketError, TimeoutError, OSError) as e:
            log(f"RESTORE on signal failed: {type(e).__name__}: {e}")
        sys.exit(2)

    signal.signal(signal.SIGTERM, handler)


def main() -> int:
    # Optional positional argument: path to this app's app.json, used to
    # update the UI "Last run" report property. The script intentionally
    # does not infer its own location, see app.sh.
    app_json_path: str | None = sys.argv[1] if len(sys.argv) > 1 else None
    log(f"unbiased-bed-mesh: one-shot start (app.json={app_json_path})")

    step: str = "startup"
    collector: ProbeCollector = ProbeCollector()

    try:
        with JsonRpcWebSocket() as ws:
            identify(ws, app_json_path)
            ws.subscribe("notify_gcode_response", collector)

            step = "query bed mesh geometry"
            cfg: BedMeshConfig | None = query_bed_mesh(ws)
            if cfg is None:
                msg = (
                    "no bed mesh found in Moonraker state. "
                    "Run a standard BED_MESH_CALIBRATE first to establish "
                    "mesh geometry, then re-run this app."
                )
                log(msg)
                write_status(
                    app_json_path,
                    f"FAILED at {step} - no mesh geometry. See {LOG_FILE_HINT}",
                )
                return 1

            nx, ny = cfg.probe_count
            log(
                f"mesh: {nx}x{ny} points, "
                f"min=({cfg.mesh_min.x},{cfg.mesh_min.y}), "
                f"max=({cfg.mesh_max.x},{cfg.mesh_max.y}), "
                f"algo={cfg.algorithm}"
            )

            step = "preflight"
            if not preflight_ok(ws):
                write_status(
                    app_json_path,
                    f"FAILED at {step} - printer busy. See {LOG_FILE_HINT}",
                )
                return 1

            step = "query probe.z_offset"
            z_offset: float = query_probe_z_offset(ws)
            log(f"probe.z_offset = {z_offset:+.4f}")

            grid: list[Point] = build_grid(cfg)
            install_sigterm_handler(ws)

            # Everything from here commands heaters and motion. Wrap it all
            # in one try/finally so any failure (heat timeout, rejected macro,
            # dropped socket, probe abort) still restores the gcode state and
            # cools the bed and hotend. M109 / M190 block until temps reach
            # setpoint, so heating can take several minutes on a cold printer.
            step = "heating"
            probe_failed: bool = False
            zs: list[float] = []
            try:
                heat_up(ws)

                ws.call(
                    "printer.gcode.script",
                    params={"script": f"SAVE_GCODE_STATE NAME={GCODE_STATE_NAME}"},
                    timeout=MOVE_TIMEOUT_S,
                )
                ws.call(
                    "printer.gcode.script",
                    params={"script": "G90"},
                    timeout=MOVE_TIMEOUT_S,
                )

                step = "probing"
                for _, row_iter in groupby(grid, key=lambda p: p.y):
                    row: list[Point] = list(row_iter)
                    prepare_row(ws, row[0])
                    for p in row:
                        z: float | None = probe_with_retry(ws, collector, p)
                        if z is None:
                            log(
                                f"point {p}: failed after {PROBE_MAX_ATTEMPTS} "
                                f"attempts, aborting"
                            )
                            probe_failed = True
                            break
                        zs.append(z)
                        log(f"point {p}: z={z:+.4f}")
                    if probe_failed:
                        break
            finally:
                try:
                    ws.call(
                        "printer.gcode.script",
                        params={
                            "script": f"RESTORE_GCODE_STATE NAME={GCODE_STATE_NAME}"
                        },
                        timeout=MOVE_TIMEOUT_S,
                    )
                except (WebSocketError, TimeoutError, OSError) as e:
                    log(f"RESTORE_GCODE_STATE failed: {e}")
                cool_down(ws)

            if probe_failed:
                write_status(
                    app_json_path,
                    f"FAILED at {step} - probe gave up after "
                    f"{PROBE_MAX_ATTEMPTS} attempts. See {LOG_FILE_HINT}",
                )
                return 1

            step = "writing mesh"
            # Subtract probe.z_offset from each raw trigger-Z so the round
            # trip cancels: GoKlipper applies stored + z_offset during print,
            # so storing (raw - z_offset) yields the raw trigger height as
            # the effective bed-relative adjustment. Matches native
            # BED_MESH_CALIBRATE storage semantics.
            raw_mean: float = sum(zs) / len(zs)
            zs_corrected: list[float] = [z - z_offset for z in zs]
            corrected_mean: float = sum(zs_corrected) / len(zs_corrected)
            log(
                f"means: raw={raw_mean:+.4f} z_offset={z_offset:+.4f} "
                f"corrected={corrected_mean:+.4f}"
            )
            log_mesh_table(grid, zs_corrected)
            write_mesh_to_mutable(MUTABLE_CFG_PATH, cfg, zs_corrected)
            log(f"wrote mesh to {MUTABLE_CFG_PATH}")
            log(
                "NOTE: restart Klipper manually (UI or restart_klipper.sh) "
                "for the new mesh to take effect."
            )

            write_status(
                app_json_path,
                f"ok, {len(zs)} points (mean {corrected_mean:+.4f}, "
                f"z_offset {z_offset:+.4f}). Restart Klipper to apply.",
            )

    except (WebSocketError, OSError, TimeoutError, ValueError) as e:
        msg = f"{type(e).__name__}: {e}"
        log(f"run failed at {step}: {msg}")
        write_status(
            app_json_path,
            f"FAILED at {step} - {msg}. See {LOG_FILE_HINT}",
        )
        return 1

    log("unbiased-bed-mesh: done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
