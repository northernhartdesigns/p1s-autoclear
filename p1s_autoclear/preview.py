"""
Push height preview: side view of model showing where the pusher will interact.
Requires optional dependency: pip install p1s-autoclear[preview]
"""

import zipfile
from pathlib import Path
from typing import Any

from .injector import (
    PART_CENTER_Y_PUSH_FRONT,
    _clamp_part_center_x,
    _compute_auto_push_z,
    parse_max_z_from_plate_gcode,
    parse_temps_from_plate_gcode,
)

# Try trimesh for mesh parsing
try:
    import trimesh
    TRIMESH_AVAILABLE = True
except ImportError:
    TRIMESH_AVAILABLE = False


def get_max_z_from_3mf(path: str | Path) -> float | None:
    """
    Get max part height from 3MF. Tries plate gcode first (sliced), then mesh bounds (unsliced).
    Returns None if not found.
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        with zipfile.ZipFile(path, "r") as zf:
            for name in zf.namelist():
                if "Metadata/plate_" in name and name.endswith(".gcode"):
                    try:
                        gcode = zf.read(name).decode("utf-8")
                        max_z = parse_max_z_from_plate_gcode(gcode)
                        if max_z is not None:
                            return max_z
                    except (UnicodeDecodeError, KeyError):
                        continue
    except (zipfile.BadZipFile, KeyError):
        pass
    if TRIMESH_AVAILABLE:
        verts = get_vertices_from_3mf(path)
        if verts is not None and len(verts) > 0:
            return float(max(v[2] for v in verts))
    return None


def get_temps_from_3mf(path: str | Path) -> tuple[int | None, int | None]:
    """
    Get bed and nozzle target temps from 3MF.
    Uses plate gcode (M140/M190, M104/M109, slicer comments) first.
    Falls back to 3MF config JSON (Bambu/PrusaSlicer: first_layer_bed_temperature).
    Returns (bed_temp, nozzle_temp); (None, None) if not found.
    """
    import json

    path = Path(path)
    if not path.exists():
        return (None, None)

    bed_temp: int | None = None
    nozzle_temp: int | None = None

    try:
        with zipfile.ZipFile(path, "r") as zf:
            for name in zf.namelist():
                n = name.replace("\\", "/")
                if "Metadata/plate_" in n and n.endswith(".gcode"):
                    try:
                        gcode = zf.read(name).decode("utf-8")
                        bed_temp, nozzle_temp = parse_temps_from_plate_gcode(gcode)
                        if bed_temp is not None and nozzle_temp is not None:
                            return (bed_temp, nozzle_temp)
                        break
                    except (UnicodeDecodeError, KeyError):
                        continue

            if bed_temp is None or nozzle_temp is None:

                def _temp_from_val(v: object) -> int | None:
                    if isinstance(v, (int, float)) and v > 0:
                        return int(v)
                    if isinstance(v, list) and len(v) > 0:
                        first = v[0]
                        if isinstance(first, (int, float)) and first > 0:
                            return int(first)
                    return None

                def _search_cfg(obj: object, keys: tuple[str, ...]) -> int | None:
                    if isinstance(obj, dict):
                        for key in keys:
                            v = obj.get(key)
                            t = _temp_from_val(v)
                            if t is not None:
                                return t
                        for v in obj.values():
                            t = _search_cfg(v, keys)
                            if t is not None:
                                return t
                    return None

                for name in zf.namelist():
                    n = name.replace("\\", "/")
                    if not (n.endswith(".config") or n.endswith(".json")):
                        continue
                    if "Metadata" not in n and "metadata" not in n.lower():
                        continue
                    try:
                        data = zf.read(name).decode("utf-8")
                        cfg = json.loads(data)
                        if bed_temp is None:
                            bed_temp = _search_cfg(
                                cfg,
                                (
                                    "first_layer_bed_temperature",
                                    "bed_temperature",
                                    "bed_temperature_initial_layer",
                                ),
                            )
                        if nozzle_temp is None:
                            nozzle_temp = _search_cfg(
                                cfg,
                                (
                                    "nozzle_temperature_initial_layer",
                                    "first_layer_temperature",
                                    "temperature",
                                    "nozzle_temperature",
                                ),
                            )
                        if bed_temp is not None and nozzle_temp is not None:
                            break
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
    except (zipfile.BadZipFile, KeyError):
        pass

    return (bed_temp, nozzle_temp)


def get_vertices_from_3mf(path: str | Path) -> list[tuple[float, float, float]] | None:
    """
    Extract all mesh vertices from 3MF. Returns list of (x,y,z) or None.
    Requires trimesh (pip install p1s-autoclear[preview]).
    """
    if not TRIMESH_AVAILABLE:
        return None
    path = Path(path)
    if not path.exists():
        return None
    try:
        loaded = trimesh.load(str(path))
        if loaded is None:
            return None
        verts: list[tuple[float, float, float]] = []
        if hasattr(loaded, "geometry"):
            for _name, geom in loaded.geometry.items():
                if hasattr(geom, "vertices"):
                    for v in geom.vertices:
                        verts.append((float(v[0]), float(v[1]), float(v[2])))
        elif hasattr(loaded, "vertices"):
            for v in loaded.vertices:
                verts.append((float(v[0]), float(v[1]), float(v[2])))
        return verts if verts else None
    except Exception:
        return None


# Heuristics to exclude build plate from part bounds
_PLATE_HEIGHT_THRESHOLD = 2.0  # mm; flat geometry assumed plate
_PLATE_FOOTPRINT_MIN = 200.0  # mm; full-bed footprint assumed plate

# Bed / pusher preview (match injector.py)
BED_X_MIN, BED_X_MAX = 0.0, 256.0
BED_Y_MIN, BED_Y_MAX = 0.0, 256.0
Y_FRONT = float(PART_CENTER_Y_PUSH_FRONT)
Y_BACK = 250.0
X_CENTRAL = 125.0
X_RAKE_POSITIONS = (220, 190, 160, 130, 100, 70, 30)


def get_parts_from_3mf(path: str | Path) -> list[dict[str, float]]:
    """
    Extract per-part bounds from 3MF mesh. Each dict has
    min_x, max_x, min_y, max_y, min_z, max_z, center_x, back_y.
    Returns [] if trimesh unavailable or no mesh.
    """
    if not TRIMESH_AVAILABLE:
        return []
    path = Path(path)
    if not path.exists():
        return []
    try:
        loaded = trimesh.load(str(path))
        if loaded is None:
            return []
        parts: list[dict[str, float]] = []
        if hasattr(loaded, "geometry"):
            for _name, geom in loaded.geometry.items():
                if not hasattr(geom, "vertices"):
                    continue
                verts = geom.vertices
                if len(verts) == 0:
                    continue
                xs = [float(v[0]) for v in verts]
                ys = [float(v[1]) for v in verts]
                zs = [float(v[2]) for v in verts]
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                min_z, max_z = min(zs), max(zs)
                parts.append({
                    "min_x": min_x, "max_x": max_x,
                    "min_y": min_y, "max_y": max_y,
                    "min_z": min_z, "max_z": max_z,
                    "center_x": (min_x + max_x) / 2,
                    "back_y": max_y,
                })
        elif hasattr(loaded, "vertices"):
            verts = loaded.vertices
            if len(verts) == 0:
                return []
            xs = [float(v[0]) for v in verts]
            ys = [float(v[1]) for v in verts]
            zs = [float(v[2]) for v in verts]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            min_z, max_z = min(zs), max(zs)
            parts.append({
                "min_x": min_x, "max_x": max_x,
                "min_y": min_y, "max_y": max_y,
                "min_z": min_z, "max_z": max_z,
                "center_x": (min_x + max_x) / 2,
                "back_y": max_y,
            })
        return parts
    except Exception:
        return []


def get_part_bounds_from_3mf(path: str | Path) -> list[tuple[float, float]] | None:
    """
    Extract (center_x, back_y) per part from 3MF for Part center push mode.
    Excludes plate geometry (flat or full-bed footprint). One entry per part.
    Returns None for sliced .gcode.3mf with no mesh. Fallback to [(125, 250)] when empty.
    """
    parts = get_parts_from_3mf(path)
    if not parts:
        return None
    result: list[tuple[float, float]] = []
    for p in parts:
        h = p["max_z"] - p["min_z"]
        wx = p["max_x"] - p["min_x"]
        wy = p["max_y"] - p["min_y"]
        if h < _PLATE_HEIGHT_THRESHOLD:
            continue
        if wx >= _PLATE_FOOTPRINT_MIN and wy >= _PLATE_FOOTPRINT_MIN:
            continue
        cx = p.get("center_x", (p["min_x"] + p["max_x"]) / 2)
        by = p.get("back_y", p["max_y"])
        result.append((cx, by))
    return result if result else None


def _part_center_push_x(part_bounds: tuple[float, float] | None) -> float:
    """Safe X for part_center modes (match injector footprint margin)."""
    if part_bounds:
        cx = float(part_bounds[0])
        xmin, xmax = cx - 22.0, cx + 22.0
    else:
        xmin, xmax = 107.0, 143.0
    return _clamp_part_center_x((xmin + xmax) * 0.5)


def _segments_central_double_push(x: float) -> list[tuple[float, float, float, float]]:
    """Back→front→back→front at fixed X (injector central sweeps)."""
    return [
        (x, Y_BACK, x, Y_FRONT),
        (x, Y_FRONT, x, Y_BACK),
        (x, Y_BACK, x, Y_FRONT),
    ]


def get_pusher_path_segments(
    push_mode: str,
    part_bounds: tuple[float, float] | None = None,
) -> list[tuple[float, float, float, float]]:
    """
    Return (x1, y1, x2, y2) segments for the pusher path in XY (top-down preview).
    part_bounds: (center_x, back_y); only center_x affects X for part_center modes.
    """
    if push_mode == "center_only":
        return _segments_central_double_push(X_CENTRAL)
    if push_mode == "part_center":
        return _segments_central_double_push(_part_center_push_x(part_bounds))
    if push_mode == "part_center_sweep":
        px = _part_center_push_x(part_bounds)
        # Injector: same column, second pass at higher F (two double-push blocks)
        return _segments_central_double_push(px) + _segments_central_double_push(px)
    # center_and_sweep: central double push + two rake passes (same columns)
    out = _segments_central_double_push(X_CENTRAL)
    for x in X_RAKE_POSITIONS:
        out.append((x, Y_BACK, x, Y_FRONT))
    for x in X_RAKE_POSITIONS:
        out.append((x, Y_BACK, x, Y_FRONT))
    return out


def build_xy_footprint_at_z(
    vertices: list[tuple[float, float, float]],
    sweep_z: float,
    z_tolerance: float = 2.0,
) -> list[tuple[float, float]]:
    """
    Build XY footprint of part at sweep Z (vertices within z_tolerance of sweep_z).
    Returns list of (x, y) points forming the contact cross-section outline.
    """
    if not vertices:
        return []
    near = [(v[0], v[1]) for v in vertices if abs(v[2] - sweep_z) <= z_tolerance]
    if not near:
        near = [(v[0], v[1]) for v in vertices if v[2] <= sweep_z]
    if not near:
        return []
    xs, ys = [p[0] for p in near], [p[1] for p in near]
    return [(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))]


def compute_contact_points(
    parts: list[dict[str, float]],
    sweep_z: float,
    push_mode: str,
    part_bounds: tuple[float, float] | None,
) -> list[tuple[float, float]]:
    """
    Approximate (x, y) where the pusher at sweep_z contacts each part.
    """
    segments = get_pusher_path_segments(push_mode, part_bounds)
    contacts: list[tuple[float, float]] = []

    def segment_intersects_box(
        x1: float, y1: float, x2: float, y2: float,
        mn_x: float, mx_x: float, mn_y: float, mx_y: float,
    ) -> tuple[float, float] | None:
        if abs(x1 - x2) < 0.01:
            if mn_x <= x1 <= mx_x:
                overlap_y_lo = max(min(y1, y2), mn_y)
                overlap_y_hi = min(max(y1, y2), mx_y)
                if overlap_y_lo <= overlap_y_hi:
                    return (x1, (overlap_y_lo + overlap_y_hi) / 2)
        elif abs(y1 - y2) < 0.01:
            if mn_y <= y1 <= mx_y:
                overlap_x_lo = max(min(x1, x2), mn_x)
                overlap_x_hi = min(max(x1, x2), mx_x)
                if overlap_x_lo <= overlap_x_hi:
                    return ((overlap_x_lo + overlap_x_hi) / 2, y1)
        return None

    for part in parts:
        if part["min_z"] > sweep_z or part["max_z"] < sweep_z:
            continue
        mn_x, mx_x = part["min_x"], part["max_x"]
        mn_y, mx_y = part["min_y"], part["max_y"]
        for x1, y1, x2, y2 in segments:
            pt = segment_intersects_box(x1, y1, x2, y2, mn_x, mx_x, mn_y, mx_y)
            if pt:
                contacts.append(pt)
                break

    return contacts


def compute_sweep_z(
    max_layer_z: float,
    push_height_mode: str,
    push_height_mm: float,
    push_height_offset_mm: int,
) -> float:
    """Compute sweep Z (mm) from settings. Matches injector logic (short-part rules)."""
    if push_height_mode == "manual":
        return max(1.0, float(push_height_mm))
    return _compute_auto_push_z(max_layer_z, int(push_height_offset_mm))


def build_xz_profile(
    vertices: list[tuple[float, float, float]],
    num_bins: int = 120,
    x_min: float = 0,
    x_max: float = 256,
) -> list[tuple[float, float, float]]:
    """
    XZ silhouette: for each X bin, (x_center, z_bed, z_top) with z_top = max Z in bin.
    """
    if not vertices:
        return []
    x_span = max(0.1, x_max - x_min)
    bin_width = x_span / num_bins
    bins: list[list[float]] = [[] for _ in range(num_bins)]
    for x, _y, z in vertices:
        idx = int((x - x_min) / bin_width)
        idx = max(0, min(num_bins - 1, idx))
        bins[idx].append(z)
    out: list[tuple[float, float, float]] = []
    for i in range(num_bins):
        if bins[i]:
            x_center = x_min + (i + 0.5) * bin_width
            z_lo = 0.0
            z_hi = max(bins[i])
            out.append((x_center, z_lo, z_hi))
    return out


def draw_preview_on_canvas(
    canvas: Any,
    path: str | Path | None,
    push_height_mode: str,
    push_height_mm: str,
    push_height_offset_mm: str,
    width: int = 180,
    height: int = 130,
) -> str | None:
    """
    Side view (XZ) on tkinter Canvas. Part = gray fill, sweep line = orange.
    Returns info string for below canvas; None when no data.
    """
    canvas.delete("all")
    pad_side, pad_top, pad_bottom = 10, 10, 28
    draw_w = max(1, width - 2 * pad_side)
    draw_h = max(1, height - pad_top - pad_bottom)
    model_x_min, model_x_max = 0.0, 256.0

    _text_muted = "#A6A9AA"
    _bed_line = "#888888"
    _bed_label = "#A6A9AA"
    _part_fill = "#5A5A5A"
    _part_outline = "#404040"
    _sweep_line = "#FF6A13"
    _z_label_outline = "#1a1a1a"
    _z_label_fill = "#ffffff"

    if path is None or not Path(path).exists():
        canvas.create_text(
            width // 2, height // 2,
            text="Load a 3MF\nto see preview",
            fill=_text_muted,
            font=("", 9),
            justify="center",
        )
        return None

    max_z = get_max_z_from_3mf(path)
    verts = get_vertices_from_3mf(path) if TRIMESH_AVAILABLE else None
    if max_z is None and verts:
        max_z = max(v[2] for v in verts)
    if max_z is None and verts is None:
        canvas.create_text(
            width // 2, height // 2,
            text="No model data in 3MF\n(load unsliced 3MF or slice first)",
            fill=_text_muted,
            font=("", 9),
            justify="center",
        )
        return None

    try:
        push_mm = float(push_height_mm.strip() or 5)
        push_offset = int(push_height_offset_mm.strip() or 20)
    except (ValueError, AttributeError):
        push_mm = 5.0
        push_offset = 20
    max_z_val = max_z or 10.0
    sweep_z = compute_sweep_z(max_z_val, push_height_mode, push_mm, push_offset)

    margin = max(2.0, max_z_val * 0.2)
    z_min = -margin
    z_max = max_z_val + margin

    def to_canvas_x(x: float) -> float:
        return pad_side + (x - model_x_min) / (model_x_max - model_x_min) * draw_w

    def to_canvas_y(z: float) -> float:
        return pad_top + draw_h - (z - z_min) / (z_max - z_min) * draw_h

    bed_y = to_canvas_y(0)
    canvas.create_line(
        to_canvas_x(model_x_min), bed_y,
        to_canvas_x(model_x_max), bed_y,
        fill=_bed_line,
        width=2,
    )
    label_y = pad_top + draw_h + 8
    canvas.create_text(pad_side, label_y, text="Bed", fill=_bed_label, font=("", 10), anchor="nw")

    if verts:
        profile = build_xz_profile(verts, num_bins=100, x_min=model_x_min, x_max=model_x_max)
        if profile:
            points = []
            for x, z_lo, z_hi in profile:
                points.extend([to_canvas_x(x), to_canvas_y(z_hi)])
            for x, z_lo, z_hi in reversed(profile):
                points.extend([to_canvas_x(x), to_canvas_y(z_lo)])
            if len(points) >= 6:
                canvas.create_polygon(points, fill=_part_fill, outline=_part_outline, width=1)
    elif max_z_val > 0:
        canvas.create_polygon(
            to_canvas_x(model_x_min), to_canvas_y(0),
            to_canvas_x(model_x_min), to_canvas_y(max_z_val),
            to_canvas_x(model_x_max), to_canvas_y(max_z_val),
            to_canvas_x(model_x_max), to_canvas_y(0),
            fill=_part_fill, outline=_part_outline, width=1,
        )

    canvas.create_line(
        to_canvas_x(model_x_min), to_canvas_y(sweep_z),
        to_canvas_x(model_x_max), to_canvas_y(sweep_z),
        fill=_sweep_line,
        width=2,
    )

    hit_info = f"{sweep_z:.1f} mm from bed"
    if sweep_z < 5 and sweep_z >= 0:
        hit_info += "\n⚠ May hit build plate!"
    if sweep_z < max_z_val:
        below_top = max_z_val - sweep_z
        hit_info += f"\n{below_top:.0f} mm below part height"
    hit_info += f"\nPart height: {max_z_val:.1f} mm"

    sweep_y = to_canvas_y(sweep_z)
    z_text = f"Z={sweep_z:.0f}"
    cx = width // 2
    for dx, dy in [(1, 1), (1, -1), (-1, 1), (-1, -1)]:
        canvas.create_text(cx + dx, sweep_y - 1 + dy, text=z_text, fill=_z_label_outline, font=("", 11, "bold"), anchor="s")
    canvas.create_text(cx, sweep_y - 1, text=z_text, fill=_z_label_fill, font=("", 11, "bold"), anchor="s")
    return hit_info
