"""
Klipper/GoKlipper-specific helpers: protocol calls, motion primitives,
heating, and the spiral probing loop. Everything that issues G-code or
queries `printer.objects.*` lives here.
"""

import os
import re
from re import Pattern
from typing import Any

from log import log
from mesh import BedMeshConfig, Point, read_app_json
from ws_client import JsonRpcWebSocket, WebSocketError

# ---------- spiral geometry ----------

# Spiral waypoints, expressed as (dx, dy) offsets from the probe point.
# Klipper draws straight lines between consecutive waypoints, so the path
# is a polygonal in-spiral that hits four quadrants with decreasing radius
# and lands exactly on the probe point. The first waypoint is reached from
# wherever the head currently sits (Y-normalised row start).
SPIRAL_WAYPOINTS: tuple[tuple[int, int], ...] = (
    (8, 0),
    (0, 7),
    (-6, 0),
    (0, -5),
    (4, 0),
    (0, 3),
    (-2, 0),
    (1, 0),
    (0, 0),
)
SPIRAL_FEEDRATE: int = 1500  # mm/min, slow on purpose so belts relax


# ---------- probing parameters ----------

# Strain-gauge probes on Kobra stay triggered briefly after touchdown. The
# default sample_retract_dist of 2 mm puts the second internal sample back
# into the still-triggered zone and probing aborts on samples_tolerance.
# 5 mm gives the gauge time to relax between samples.
#
# Confirmed by disassembling gklib 2.4.6.7 (Cmd_PROBE calls Probe, which
# calls Run_probe). PROBE accepts these overrides:
#   LIFT_SPEED                    mm/s, retract/lift speed
#   PROBE_SPEED                   mm/s, descent speed
#   SAMPLES                       touchdowns per point
#   SAMPLES_RESULT                average or median
#   SAMPLES_TOLERANCE             mm, max spread across samples
#   SAMPLES_TOLERANCE_RETRIES     retries before giving up
#   SAMPLE_RETRACT_DIST           mm, retract before the next sample
PROBE_GCODE: str = "PROBE SAMPLE_RETRACT_DIST=5.0"
PROBE_MAX_ATTEMPTS: int = 2


# ---------- heating profile ----------

# Mirrors the wrapped BED_MESH_CALIBRATE in kobra.py. Bed and hotend are
# brought to printing conditions, then the hotend is cooled to a lower
# temperature for the actual probing so that nozzle thermal expansion
# doesn't drift the trigger height between touchdowns.
BED_TEMP_C: int = 60
HOTEND_PREHEAT_C: int = 170
HOTEND_PROBE_C: int = 140
HEAT_TIMEOUT_S: float = 600.0  # bed warm-up from cold can take ~5 minutes

# WIPE_ENTER / WIPE_EXIT exist only on KS1 / KS1M; other models ship the
# bare WIPE_NOZZLE and would error on enter/exit. Model code is exported by
# tools.sh and inherited through app.sh.
KOBRA_MODEL_CODE: str = os.environ.get("KOBRA_MODEL_CODE", "")
WIPE_POSITIONING_MODELS: tuple[str, ...] = ("KS1", "KS1M")


# ---------- motion parameters ----------

SAFE_Z_MM: float = 5.0  # height to lift to before any horizontal move
Y_NORMALIZE_OFFSET_MM: float = 5.0  # distance below a row used to seed +Y
Z_HOP_FEEDRATE: int = 3000  # mm/min for Z moves
XY_FEEDRATE: int = 6000  # mm/min for general X/Y travel (non-spiral)
PROBE_TIMEOUT_S: float = 60.0
MOVE_TIMEOUT_S: float = 30.0


# ---------- gcode state name (shared with main + signal handler) ----------

GCODE_STATE_NAME: str = "unbiased_mesh"


# ---------- probe result parsing ----------

# Parses GoKlipper's averaged probe result, e.g. "Result is z=-0.186400".
RESULT_RE: Pattern[str] = re.compile(r"Result is z=(-?\d+(?:\.\d+)?)")


class ProbeCollector:
    """
    Subscribed handler for `notify_gcode_response`. Accumulates incoming
    response lines into a list that the probing loop drains around each
    PROBE call.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, params: Any) -> None:
        if isinstance(params, list):
            for item in params:
                self.lines.append(str(item))

    def clear(self) -> None:
        self.lines.clear()

    def find_result(self) -> float | None:
        """Return the parsed Z from the latest `Result is z=...` line, or None."""
        for line in reversed(self.lines):
            m = RESULT_RE.search(line)
            if m:
                return float(m.group(1))
        return None


# ---------- moonraker queries ----------


# Moonraker client_name is an identifier, so keep the app slug (matches the
# app directory and app.sh); version / url are read from app.json at runtime.
CLIENT_NAME: str = "unbiased-bed-mesh"
IDENTIFY_URL_FALLBACK: str = "https://github.com/rinkhals-community/Rinkhals.Apps"
IDENTIFY_VERSION_FALLBACK: str = "unknown"


def identify(ws: JsonRpcWebSocket, app_json_path: str | None = None) -> None:
    """
    Identify to Moonraker. Best-effort; a failure never aborts the run.
    version and url come from app.json so they cannot drift from the
    canonical values; a missing / unreadable app.json falls back to defaults.
    """
    meta = read_app_json(app_json_path)
    try:
        ws.call(
            "server.connection.identify",
            params={
                "client_name": CLIENT_NAME,
                "version": str(meta.get("version", IDENTIFY_VERSION_FALLBACK)),
                "type": "agent",
                "url": str(meta.get("url", IDENTIFY_URL_FALLBACK)),
            },
        )
    except (WebSocketError, TimeoutError, OSError) as e:
        log(f"identify failed (continuing anyway): {e}")


def query_probe_z_offset(ws: JsonRpcWebSocket) -> float:
    """
    Read the live probe.z_offset from Moonraker's parsed config tree.

    GoKlipper applies `[probe] z_offset` to the mesh value during printing:
    final_z_adjust = stored_mesh_z + probe_z_offset. To make our raw PROBE
    results (which are trigger-Z in toolhead coords) round-trip correctly,
    we must pre-subtract z_offset before writing the mesh.

    Source: `configfile.settings.probe.z_offset`. Note this tree is populated
    from printer_mutable.cfg at Klipper startup; if z_offset was changed via
    Z_OFFSET_APPLY_PROBE in the same session and no restart has happened
    since, the live value may differ. Document, do not work around.

    Raises ValueError if the value is missing or non-numeric.
    """
    q = ws.call(
        "printer.objects.query",
        params={"objects": {"configfile": ["settings"]}},
    )
    settings: dict[str, Any] = (
        (q or {}).get("status", {}).get("configfile", {}).get("settings", {})
    )
    probe = settings.get("probe")
    if not isinstance(probe, dict):
        raise ValueError("configfile.settings.probe is missing or not a dict")
    if "z_offset" not in probe:
        raise ValueError("configfile.settings.probe.z_offset is missing")
    try:
        return float(probe["z_offset"])
    except (TypeError, ValueError) as e:
        raise ValueError(f"probe.z_offset is not numeric: {probe['z_offset']!r}") from e


def preflight_ok(ws: JsonRpcWebSocket) -> bool:
    """
    Returns True if it is safe to drive the head: printer is not printing
    or paused. We do NOT check homed_axes here. heat_up runs G28 first, and
    an unhomed printer at start is the normal case for a fresh boot or after
    sitting idle.
    """
    try:
        q = ws.call(
            "printer.objects.query",
            params={
                "objects": {
                    "print_stats": ["state"],
                }
            },
        )
    except WebSocketError as e:
        log(f"preflight query failed: {e}")
        return False

    status: dict[str, Any] = (q or {}).get("status", {})
    state: str = str(status.get("print_stats", {}).get("state", ""))

    if state in ("printing", "paused"):
        log(f"refusing: printer state is {state!r}")
        return False
    log(f"preflight ok: state={state!r}")
    return True


def query_bed_mesh(ws: JsonRpcWebSocket) -> BedMeshConfig | None:
    """
    Read the current mesh geometry from Moonraker. Returns None if no
    mesh has ever been calibrated (caller should bail with a clear error).

    Note: on GoKlipper, the `bed_mesh` and `bed_mesh default` objects are
    synthesized from printer_mutable.cfg by the Rinkhals Moonraker patch.
    They are populated iff a previous BED_MESH_CALIBRATE wrote the file.
    """
    try:
        q = ws.call(
            "printer.objects.query",
            params={"objects": {"bed_mesh": None, "bed_mesh default": None}},
        )
    except WebSocketError as e:
        log(f"bed_mesh query failed: {e}")
        return None

    status: dict[str, Any] = (q or {}).get("status", {})
    bm: dict[str, Any] = status.get("bed_mesh", {})
    bmd: dict[str, Any] = status.get("bed_mesh default", {})
    params: dict[str, Any] = bmd.get("mesh_params", {}) if isinstance(bmd, dict) else {}

    if not bm or not params:
        return None

    return BedMeshConfig(
        mesh_min=Point(float(params["min_x"]), float(params["min_y"])),
        mesh_max=Point(float(params["max_x"]), float(params["max_y"])),
        probe_count=(int(params["x_count"]), int(params["y_count"])),
        mesh_pps=(int(params["mesh_x_pps"]), int(params["mesh_y_pps"])),
        algorithm=str(params["algo"]),
        tension=float(params["tension"]),
    )


# ---------- probing motion ----------


def prepare_row(ws: JsonRpcWebSocket, first: Point) -> None:
    """
    Normalise the Y approach for a row of probes: lift Z, jump below the
    row's first point, then move +Y onto the row. After this every probe
    in the row shares the same Y history; inter-point spiraling is X/Y
    around each probe point without large Y direction changes.
    """
    moves: list[str] = [
        f"G0 Z{SAFE_Z_MM:.2f} F{Z_HOP_FEEDRATE}",
        f"G1 X{first.x:.3f} Y{first.y - Y_NORMALIZE_OFFSET_MM:.3f} F{XY_FEEDRATE}",
        f"G1 X{first.x:.3f} Y{first.y:.3f} F{XY_FEEDRATE}",
    ]
    for gcode in moves:
        ws.call(
            "printer.gcode.script",
            params={"script": gcode},
            timeout=MOVE_TIMEOUT_S,
        )


def spiral_then_probe(
    ws: JsonRpcWebSocket,
    collector: ProbeCollector,
    p: Point,
) -> float | None:
    """
    Run the in-spiral around `p` at SPIRAL_FEEDRATE, ending exactly on `p`,
    then PROBE. Returns the parsed Z, or None if no Result line was seen.
    A failed PROBE (e.g. samples_tolerance) raises WebSocketError; the
    caller handles retries via probe_with_retry.
    """
    for dx, dy in SPIRAL_WAYPOINTS:
        gcode: str = f"G1 X{p.x + dx:.3f} Y{p.y + dy:.3f} F{SPIRAL_FEEDRATE}"
        ws.call(
            "printer.gcode.script",
            params={"script": gcode},
            timeout=MOVE_TIMEOUT_S,
        )

    collector.clear()
    ws.call(
        "printer.gcode.script",
        params={"script": PROBE_GCODE},
        timeout=PROBE_TIMEOUT_S,
    )
    z: float | None = collector.find_result()

    ws.call(
        "printer.gcode.script",
        params={"script": f"G0 Z{SAFE_Z_MM:.2f} F{Z_HOP_FEEDRATE}"},
        timeout=MOVE_TIMEOUT_S,
    )
    return z


def probe_with_retry(
    ws: JsonRpcWebSocket,
    collector: ProbeCollector,
    p: Point,
) -> float | None:
    """
    Try spiral_then_probe up to PROBE_MAX_ATTEMPTS times. Returns the Z
    on the first attempt that yields a Result line, or None if every
    attempt failed. Between attempts we lift Z to SAFE_Z so the next
    spiral starts from a known height (a probe aborted mid-touchdown can
    leave Z in an unpredictable state).
    """
    for attempt in range(1, PROBE_MAX_ATTEMPTS + 1):
        last_err: WebSocketError | None = None
        try:
            z: float | None = spiral_then_probe(ws, collector, p)
            if z is not None:
                if attempt > 1:
                    log(f"point {p}: succeeded on attempt {attempt}")
                return z
            log(f"point {p}: attempt {attempt}: no Result line")
        except WebSocketError as e:
            log(f"point {p}: attempt {attempt}: {e}")
            last_err = e

        # Recovery before the next attempt. What we need depends on what
        # actually went wrong, so inspect the error message:
        #   - "Must home axis first": kinematic state was lost mid-run
        #     (cause unknown; happens occasionally). The only way out is
        #     a full G28.
        #   - "samples_tolerance" / other probe failures: head is still
        #     where it was, axes are still homed. A Z-lift is enough so
        #     the next spiral starts from a known height.
        err_msg: str = str(last_err or "")
        if "home axis" in err_msg.lower():
            log(f"point {p}: homing lost, running G28 before retry")
            try:
                ws.call(
                    "printer.gcode.script",
                    params={"script": "G28"},
                    timeout=PROBE_TIMEOUT_S,
                )
                ws.call(
                    "printer.gcode.script",
                    params={"script": "G90"},
                    timeout=MOVE_TIMEOUT_S,
                )
            except (WebSocketError, TimeoutError) as e:
                log(f"point {p}: G28 recovery failed (continuing): {e}")
        else:
            try:
                ws.call(
                    "printer.gcode.script",
                    params={"script": f"G0 Z{SAFE_Z_MM:.2f} F{Z_HOP_FEEDRATE}"},
                    timeout=MOVE_TIMEOUT_S,
                )
            except (WebSocketError, TimeoutError) as e:
                log(f"point {p}: Z-lift recovery failed (continuing): {e}")

    return None


# ---------- heating and wiping ----------


def heat_up(ws: JsonRpcWebSocket) -> None:
    """
    Mirror the heating / wiping sequence from the wrapped BED_MESH_CALIBRATE
    in kobra.py: home, heat bed and hotend, wipe, cool the hotend to probing
    temp. Re-home Z last, on the settled hot bed: the bed warps while heating
    and the wipe nudges the head, so an earlier Z zero would drift.

    WIPE_ENTER / WIPE_EXIT exist only on KS1 / KS1M; other models have the
    bare WIPE_NOZZLE.
    """
    wipe: list[str] = ["WIPE_NOZZLE"]
    if KOBRA_MODEL_CODE in WIPE_POSITIONING_MODELS:
        wipe = ["WIPE_ENTER", "WIPE_NOZZLE", "WIPE_EXIT"]

    sequence: list[str] = [
        f"M140 S{BED_TEMP_C}",  # set bed temperature, non-blocking
        f"M104 S{HOTEND_PREHEAT_C}",  # set hotend temperature, non-blocking
        "G28",
        "MOVE_HEAT_POS",
        f"M109 S{HOTEND_PREHEAT_C}",  # set hotend and wait
        *wipe,
        f"M109 S{HOTEND_PROBE_C}",  # cool hotend to probing temp, wait
        f"M190 S{BED_TEMP_C}",  # wait for bed
        "G28 Z",
    ]
    for gcode in sequence:
        log(f"heat: {gcode}")
        ws.call(
            "printer.gcode.script",
            params={"script": gcode},
            timeout=HEAT_TIMEOUT_S,
        )


def cool_down(ws: JsonRpcWebSocket) -> None:
    """Turn off heaters and the part fan. Best-effort, never raises."""
    for gcode in ("TURN_OFF_HEATERS", "M106 S0"):
        try:
            ws.call(
                "printer.gcode.script",
                params={"script": gcode},
                timeout=MOVE_TIMEOUT_S,
            )
        except (WebSocketError, TimeoutError, OSError) as e:
            log(f"cool: {gcode} failed (continuing): {e}")
