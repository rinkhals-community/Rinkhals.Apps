"""
Mesh data types, geometry helpers, and the two files we write:
printer_mutable.cfg (the mesh itself) and app.json (the UI status line).

This module has no WebSocket / printer awareness: pure data and disk I/O.
"""

import json
import os
import tempfile
from itertools import groupby
from typing import Any, NamedTuple

from log import log

MUTABLE_CFG_PATH: str = "/userdata/app/gk/printer_mutable.cfg"
LOG_FILE_HINT: str = (
    "/tmp/unbiased-bed-mesh.log"  # informational; shell redirects stdout/stderr here
)


class Point(NamedTuple):
    """A bed-coordinate measurement point."""

    x: float
    y: float

    def __str__(self) -> str:
        return f"({self.x:.2f}, {self.y:.2f})"


class BedMeshConfig(NamedTuple):
    """Mesh geometry as reported by Moonraker."""

    mesh_min: Point
    mesh_max: Point
    probe_count: tuple[int, int]  # (x_count, y_count)
    mesh_pps: tuple[int, int]  # (mesh_x_pps, mesh_y_pps)
    algorithm: str
    tension: float


# ---------- grid construction ----------


def build_grid(cfg: BedMeshConfig) -> list[Point]:
    """
    Return a flat list of probe points, ordered low Y to high Y and low X
    to high X within each Y. Coordinates are linearly spaced from
    `mesh_min` to `mesh_max`, inclusive, with `probe_count` samples per
    axis.
    """
    nx, ny = cfg.probe_count
    if nx < 2 or ny < 2:
        raise ValueError(
            f"probe_count must be >= 2 on each axis, got {cfg.probe_count}"
        )

    def axis(lo: float, hi: float, n: int) -> list[float]:
        step: float = (hi - lo) / (n - 1)
        return [lo + i * step for i in range(n)]

    xs: list[float] = axis(cfg.mesh_min.x, cfg.mesh_max.x, nx)
    ys: list[float] = axis(cfg.mesh_min.y, cfg.mesh_max.y, ny)
    return [Point(x, y) for y in ys for x in xs]


def log_mesh_table(grid: list[Point], zs: list[float]) -> None:
    """Log the probed Z values as a Y-descending grid (top = +Y)."""
    rows: list[list[tuple[Point, float]]] = [
        list(r) for _, r in groupby(zip(grid, zs), key=lambda pz: pz[0].y)
    ]
    xs: list[float] = [pz[0].x for pz in rows[0]]
    log("probed mesh (mm):")
    log("           " + "  ".join(f"x={x:6.1f}" for x in xs))
    for row in reversed(rows):
        cells: str = "  ".join(f"{z:+8.4f}" for _, z in row)
        log(f"  y={row[0][0].y:6.1f}  {cells}")


# ---------- printer_mutable.cfg ----------


def _fmt_num(v: Any) -> str:
    """
    Format a number for printer_mutable.cfg in the same shape GoKlipper
    uses: whole floats render as ints (e.g. 10.0 -> "10"), the rest as
    their string representation.
    """
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _format_points(zs: list[float], nx: int, ny: int) -> str:
    """
    Render the probed Z values as the on-disk `points` string: rows joined
    with '\\n', values within a row joined with ', ', each value formatted
    as %.6f. `zs` is a flat list in row-major order (low Y first).
    """
    if len(zs) != nx * ny:
        raise ValueError(f"expected {nx * ny} z values, got {len(zs)}")
    rows: list[str] = []
    for r in range(ny):
        row_vals: list[float] = zs[r * nx : (r + 1) * nx]
        rows.append(", ".join(f"{z:.6f}" for z in row_vals))
    return "\n".join(rows)


def write_mesh_to_mutable(
    path: str,
    cfg: BedMeshConfig,
    zs: list[float],
) -> None:
    """
    Read printer_mutable.cfg (if present), replace the `bed_mesh default`
    block with our values, write back atomically. Other top-level keys
    (input_shaper, etc.) are preserved.
    """
    data: dict[str, Any] = {}
    if os.path.isfile(path):
        with open(path, "r") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                log(
                    f"warning: existing mutable cfg is not valid JSON ({e}), overwriting"
                )
                data = {}

    nx, ny = cfg.probe_count
    data["bed_mesh default"] = {
        "algo": cfg.algorithm,
        "max_x": _fmt_num(cfg.mesh_max.x),
        "max_y": _fmt_num(cfg.mesh_max.y),
        "mesh_x_pps": _fmt_num(cfg.mesh_pps[0]),
        "mesh_y_pps": _fmt_num(cfg.mesh_pps[1]),
        "min_x": _fmt_num(cfg.mesh_min.x),
        "min_y": _fmt_num(cfg.mesh_min.y),
        "points": _format_points(zs, nx, ny),
        "tension": _fmt_num(cfg.tension),
        "version": "1",
        "x_count": str(nx),
        "y_count": str(ny),
    }

    # Atomic write: tmp file in the same dir, rename over the target.
    dirname: str = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".mutable.", suffix=".tmp", dir=dirname)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, sort_keys=True, indent=4)
        os.rename(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------- app.json status report ----------


def read_app_json(app_json_path: str | None) -> dict[str, Any]:
    """
    Load app.json and return its parsed object, or {} when the path is None,
    missing, or not valid JSON. Never raises: callers use it for best-effort
    metadata and must tolerate an empty dict.
    """
    if not app_json_path or not os.path.isfile(app_json_path):
        return {}
    try:
        with open(app_json_path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log(f"warning: reading {app_json_path} failed: {e}")
        return {}
    return data if isinstance(data, dict) else {}


def write_status(app_json_path: str | None, text: str) -> None:
    """
    Update properties.last_result.default in app.json so the Rinkhals UI shows
    the last-run status. No-op when app_json_path is None or unreachable; any
    failure here is logged but never raised (status update must not mask the
    real run result).
    """
    if not app_json_path:
        return
    if not os.path.isfile(app_json_path):
        log(f"warning: cannot update status, {app_json_path} not found")
        return
    try:
        with open(app_json_path) as f:
            data = json.load(f)
        props = data.setdefault("properties", {})
        last = props.setdefault(
            "last_result", {"display": "Last run", "type": "report"}
        )
        last["default"] = text
        tmp = app_json_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=4)
        os.rename(tmp, app_json_path)
    except (OSError, json.JSONDecodeError) as e:
        log(f"warning: updating {app_json_path} failed: {e}")
