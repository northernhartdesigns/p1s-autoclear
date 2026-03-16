"""
Push height preview: side view of model showing where the pusher will interact.
Requires optional dependency: pip install p1s-autoclear[preview]
"""

import zipfile
from pathlib import Path
from typing import Any

from .injector import (
    MIN_SWEEP_Z_MM,
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
            # Sliced: try plate gcode
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
    # Unsliced: use mesh bounds (requires trimesh)
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
            # 1. Try plate gcode (sliced)
            for name in zf.namelist():
                n = name.replace("\\", "/")
                if "Metadata/plate_" in n and n.endswith(".gcode"):
                    try:
                        gcode = zf.read(name).decode("utf-8")
                        bed_temp, nozzle_temp = parse_temps_from_plate_gcode(gcode)
                        if bed_temp is not None and nozzle_temp is not None:
                            return (bed_temp, nozzle_temp)
                        # Keep whatever we found, try config fallback for missing
                        break
                    except (UnicodeDecodeError, KeyError):
                        continue

            # 2. Fallback: bed/nozzle temp from config JSON (Bambu stores here when not in gcode)
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
        # Scene has .geometry; single mesh has .vertices
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


def get_part_bounds_from_3mf(path: str | Path) -> tuple[float, float] | None:
    """
    Extract part center X and back Y from 3MF mesh for targeted bump push.
    center_x = (min_x + max_x) / 2, back_y = max_y (rear edge of part).
    Returns (center_x, back_y) or None if mesh unavailable (trimesh required).
    Falls back gracefully: use (125, 250) when None.
    """
    verts = get_vertices_from_3mf(path)
    if not verts:
        return None
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    center_x = (min(xs) + max(xs)) / 2
    back_y = max(ys)  # Rear edge toward Y=250
    return (center_x, back_y)


def compute_sweep_z(
    max_layer_z: float,
    push_height_mode: str,
    push_height_mm: float,
    push_height_offset_mm: int,
) -> float:
    """Compute sweep Z (mm) from settings. Matches injector logic. Allows <5mm (user warned)."""
    if push_height_mode == "manual":
        return max(1.0, float(push_height_mm))
    offset = max(1, min(int(max_layer_z) - 1, int(push_height_offset_mm)))
    return max(1.0, max_layer_z - offset)


def build_xz_profile(
    vertices: list[tuple[float, float, float]],
    num_bins: int = 120,
    x_min: float = 0,
    x_max: float = 256,
) -> list[tuple[float, float, float]]:
    """
    Build XZ silhouette for side view. For each X bin, returns (x_center, z_bed, z_top).
    z_bed is 0, z_top is max Z of vertices in that bin.
    Returns list of (x, z_lo, z_hi) forming the top profile.
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
    Draw side view (XZ) preview on tkinter Canvas. Part = gray fill, sweep line = red.
    Z axis zooms to part so sweep position and distance from bed are clearly visible.
    Returns info string for display below canvas (avoids truncation); None when no data.
    """
    canvas.delete("all")
    pad_side, pad_top, pad_bottom = 10, 10, 28  # Extra bottom so Bed label is fully visible
    draw_w = max(1, width - 2 * pad_side)
    draw_h = max(1, height - pad_top - pad_bottom)
    # Model bounds (Bambu P1S bed, X mm)
    model_x_min, model_x_max = 0.0, 256.0

    # Dark theme colors (Bambu branded)
    _text_muted = "#A6A9AA"
    _bed_line = "#888888"
    _bed_label = "#A6A9AA"
    _part_fill = "#5A5A5A"
    _part_outline = "#404040"
    _sweep_line = "#FF6A13"  # Bambu brand orange
    _z_label_outline = "#1a1a1a"
    _z_label_fill = "#ffffff"

    # Fallback: no file
    if path is None or not Path(path).exists():
        canvas.create_text(
            width // 2, height // 2,
            text="Load a 3MF\nto see preview",
            fill=_text_muted,
            font=("", 9),
            justify="center",
        )
        return None

    # Get data: max_z from plate gcode (sliced) or mesh (unsliced)
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

    # Zoom Z to part, centered in frame: add equal margin above and below for balanced view
    margin = max(2.0, max_z_val * 0.2)
    z_min = -margin
    z_max = max_z_val + margin

    def to_canvas_x(x: float) -> float:
        return pad_side + (x - model_x_min) / (model_x_max - model_x_min) * draw_w

    def to_canvas_y(z: float) -> float:
        return pad_top + draw_h - (z - z_min) / (z_max - z_min) * draw_h

    # Draw bed line
    bed_y = to_canvas_y(0)
    canvas.create_line(
        to_canvas_x(model_x_min), bed_y,
        to_canvas_x(model_x_max), bed_y,
        fill=_bed_line,
        width=2,
    )
    # Bed label in the padded bottom area so it's always fully visible
    label_y = pad_top + draw_h + 8  # Fixed position in the reserved bottom padding
    canvas.create_text(pad_side, label_y, text="Bed", fill=_bed_label, font=("", 10), anchor="nw")

    # Draw part profile
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

    # Draw sweep line (Bambu orange) - where the pusher hits
    canvas.create_line(
        to_canvas_x(model_x_min), to_canvas_y(sweep_z),
        to_canvas_x(model_x_max), to_canvas_y(sweep_z),
        fill=_sweep_line,
        width=2,
    )

    # Build info string for label below canvas (newlines avoid truncation)
    hit_info = f"{sweep_z:.1f} mm from bed"
    if sweep_z < 5 and sweep_z >= 0:
        hit_info += "\n⚠ May hit build plate!"
    if sweep_z < max_z_val:
        below_top = max_z_val - sweep_z
        hit_info += f"\n{below_top:.0f} mm below part height"
    hit_info += f"\nPart height: {max_z_val:.1f} mm"

    # Z label on sweep line: dark outline + white fill for maximum contrast
    sweep_y = to_canvas_y(sweep_z)
    z_text = f"Z={sweep_z:.0f}"
    cx = width // 2
    for dx, dy in [(1, 1), (1, -1), (-1, 1), (-1, -1)]:
        canvas.create_text(cx + dx, sweep_y - 1 + dy, text=z_text, fill=_z_label_outline, font=("", 11, "bold"), anchor="s")
    canvas.create_text(cx, sweep_y - 1, text=z_text, fill=_z_label_fill, font=("", 11, "bold"), anchor="s")
    return hit_info
