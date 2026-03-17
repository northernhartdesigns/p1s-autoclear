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
    Extract part center X and back Y from 3MF for targeted bump push.
    Requires mesh (trimesh); returns None for sliced .gcode.3mf with no mesh.
    Use (125, 250) when None.
    """
    verts = get_vertices_from_3mf(path)
    if verts:
        xs = [v[0] for v in verts]
        ys = [v[1] for v in verts]
        return ((min(xs) + max(xs)) / 2, max(ys))
    return None


# Bed dimensions for preview (Bambu P1S)
BED_X_MIN, BED_X_MAX = 0.0, 256.0
BED_Y_MIN, BED_Y_MAX = 0.0, 256.0
Y_FRONT, Y_BACK = 0.0, 250.0
X_CENTRAL = 125.0
X_RAKE_POSITIONS = (220, 190, 160, 130, 100, 70, 30)


def get_parts_from_3mf(path: str | Path) -> list[dict[str, float]]:
    """
    Extract per-part bounds from 3MF. Each part returns dict with
    min_x, max_x, min_y, max_y, min_z, max_z, center_x, back_y.
    Returns [] if mesh unavailable (trimesh required).
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


def get_pusher_path_segments(
    push_mode: str,
    part_bounds: tuple[float, float] | None = None,
) -> list[tuple[float, float, float, float]]:
    """
    Return pusher path as list of (x1, y1, x2, y2) line segments.
    push_mode: center_only, center_and_sweep, or bump.
    part_bounds: (center_x, back_y) for bump mode; None uses bed center.
    """
    cx = X_CENTRAL
    back_y = Y_BACK
    front_y = Y_FRONT
    if push_mode == "bump" and part_bounds:
        cx, back_y = part_bounds[0], part_bounds[1]

    segments: list[tuple[float, float, float, float]] = []

    # Central sweeps (all modes)
    segments.append((cx, back_y, cx, front_y))
    segments.append((cx, front_y, cx, back_y))
    segments.append((cx, back_y, cx, front_y))

    if push_mode == "center_only":
        return segments

    # Rake passes
    for x in X_RAKE_POSITIONS:
        segments.append((x, back_y, x, front_y))

    return segments


def _bbox_intersects_path(
    min_x: float, max_x: float, min_y: float, max_y: float,
    x1: float, y1: float, x2: float, y2: float,
) -> bool:
    """True if line segment (x1,y1)-(x2,y2) intersects axis-aligned box."""
    # Vertical segment
    if abs(x2 - x1) < 0.001:
        return min_x <= x1 <= max_x and min_y <= min(y1, y2) <= max_y and max_y >= max(y1, y2)
    # Horizontal segment
    if abs(y2 - y1) < 0.001:
        return min_y <= y1 <= max_y and min_x <= min(x1, x2) <= max_x and max_x >= max(x1, x2)
    # General: check if either endpoint is inside, or line crosses edges
    for x, y in [(x1, y1), (x2, y2)]:
        if min_x <= x <= max_x and min_y <= y <= max_y:
            return True
    # Line crosses bbox - use Liang-Barsky or simple span check
    x_lo, x_hi = min(x1, x2), max(x1, x2)
    y_lo, y_hi = min(y1, y2), max(y1, y2)
    if x_hi < min_x or x_lo > max_x or y_hi < min_y or y_lo > max_y:
        return False
    return True


# Pusher path constants (match injector.py)
X_POSITIONS_RAKE = (220, 190, 160, 130, 100, 70, 30)
Y_BACK = 250.0
Y_FRONT = 0.0
X_CENTRAL = 125.0
BED_X_MAX = 256.0
BED_Y_MAX = 256.0


def get_parts_from_3mf(path: str | Path) -> list[dict[str, float]]:
    """
    Extract per-part bounds from 3MF. Returns list of dicts with min_x, max_x, min_y, max_y,
    min_z, max_z, center_x, back_y for each mesh object. Enables multi-part contact visualization.
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
                if hasattr(geom, "vertices"):
                    vs = geom.vertices
                    if len(vs) == 0:
                        continue
                    xs = [float(v[0]) for v in vs]
                    ys = [float(v[1]) for v in vs]
                    zs = [float(v[2]) for v in vs]
                    mn_x, mx_x = min(xs), max(xs)
                    mn_y, mx_y = min(ys), max(ys)
                    mn_z, mx_z = min(zs), max(zs)
                    parts.append({
                        "min_x": mn_x, "max_x": mx_x,
                        "min_y": mn_y, "max_y": mx_y,
                        "min_z": mn_z, "max_z": mx_z,
                        "center_x": (mn_x + mx_x) / 2,
                        "back_y": mx_y,
                    })
        elif hasattr(loaded, "vertices"):
            vs = loaded.vertices
            xs = [float(v[0]) for v in vs]
            ys = [float(v[1]) for v in vs]
            zs = [float(v[2]) for v in vs]
            mn_x, mx_x = min(xs), max(xs)
            mn_y, mx_y = min(ys), max(ys)
            mn_z, mx_z = min(zs), max(zs)
            parts.append({
                "min_x": mn_x, "max_x": mx_x,
                "min_y": mn_y, "max_y": mx_y,
                "min_z": mn_z, "max_z": mx_z,
                "center_x": (mn_x + mx_x) / 2,
                "back_y": mx_y,
            })
        return parts
    except Exception:
        return []


def get_pusher_path_segments(
    push_mode: str,
    part_bounds: tuple[float, float] | None,
) -> list[tuple[float, float, float, float]]:
    """
    Return XY line segments (x1, y1, x2, y2) for the pusher path based on push mode.
    Used to overlay pusher path on top-down view.
    """
    segments: list[tuple[float, float, float, float]] = []
    cx, by = (part_bounds or (X_CENTRAL, Y_BACK))[0], (part_bounds or (X_CENTRAL, Y_BACK))[1]

    if push_mode == "center_only":
        segments.append((X_CENTRAL, Y_BACK, X_CENTRAL, Y_FRONT))
        segments.append((X_CENTRAL, Y_BACK, X_CENTRAL, Y_FRONT))
    elif push_mode == "bump":
        segments.append((cx, by, cx, Y_FRONT))
        for x in X_POSITIONS_RAKE:
            segments.append((x, Y_BACK, x, Y_FRONT))
    else:
        # center_and_sweep (default)
        segments.append((X_CENTRAL, Y_BACK, X_CENTRAL, Y_FRONT))
        segments.append((X_CENTRAL, Y_BACK, X_CENTRAL, Y_FRONT))
        for x in X_POSITIONS_RAKE:
            segments.append((x, Y_BACK, x, Y_FRONT))
    return segments


def build_xy_footprint_at_z(
    vertices: list[tuple[float, float, float]],
    sweep_z: float,
    z_tolerance: float = 2.0,
) -> list[tuple[float, float]]:
    """
    Build XY footprint of part at sweep Z (vertices within z_tolerance of sweep_z).
    Returns list of (x, y) points forming the contact cross-section outline.
    Uses bins for a simple silhouette; empty list if no vertices at height.
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


# Pusher path constants (match injector.py)
Y_FRONT = 0.0
Y_BACK = 250.0
X_CENTRAL = 125.0
X_POSITIONS = (220, 190, 160, 130, 100, 70, 30)
BED_X_MIN, BED_X_MAX = 0.0, 256.0
BED_Y_MIN, BED_Y_MAX = 0.0, 256.0


def get_parts_from_3mf(path: str | Path) -> list[dict[str, float]] | None:
    """
    Extract per-part bounds from 3MF mesh. Returns list of dicts, each with
    min_x, max_x, min_y, max_y, min_z, max_z, center_x, back_y.
    Returns None if mesh unavailable (trimesh required).
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
        parts: list[dict[str, float]] = []
        if hasattr(loaded, "geometry"):
            for _name, geom in loaded.geometry.items():
                if hasattr(geom, "vertices"):
                    verts = geom.vertices
                    if len(verts) == 0:
                        continue
                    xs = [float(v[0]) for v in verts]
                    ys = [float(v[1]) for v in verts]
                    zs = [float(v[2]) for v in verts]
                    mn_x, mx_x = min(xs), max(xs)
                    mn_y, mx_y = min(ys), max(ys)
                    mn_z, mx_z = min(zs), max(zs)
                    parts.append({
                        "min_x": mn_x, "max_x": mx_x,
                        "min_y": mn_y, "max_y": mx_y,
                        "min_z": mn_z, "max_z": mx_z,
                        "center_x": (mn_x + mx_x) / 2,
                        "back_y": mx_y,
                    })
        elif hasattr(loaded, "vertices"):
            verts = loaded.vertices
            if len(verts) > 0:
                xs = [float(v[0]) for v in verts]
                ys = [float(v[1]) for v in verts]
                zs = [float(v[2]) for v in verts]
                mn_x, mx_x = min(xs), max(xs)
                mn_y, mx_y = min(ys), max(ys)
                mn_z, mx_z = min(zs), max(zs)
                parts.append({
                    "min_x": mn_x, "max_x": mx_x,
                    "min_y": mn_y, "max_y": mx_y,
                    "min_z": mn_z, "max_z": mx_z,
                    "center_x": (mn_x + mx_x) / 2,
                    "back_y": mx_y,
                })
        return parts if parts else None
    except Exception:
        return None


def get_pusher_path_segments(
    push_mode: str,
    part_bounds: tuple[float, float] | None = None,
) -> list[tuple[float, float, float, float]]:
    """
    Return list of (x1, y1, x2, y2) line segments for the pusher path in XY.
    Used to draw the pusher path on the top-down view.
    """
    center_x = part_bounds[0] if part_bounds else X_CENTRAL
    back_y = part_bounds[1] if part_bounds else Y_BACK
    segments: list[tuple[float, float, float, float]] = []

    if push_mode == "center_only":
        segments.append((X_CENTRAL, Y_BACK, X_CENTRAL, Y_FRONT))
        segments.append((X_CENTRAL, Y_FRONT, X_CENTRAL, Y_BACK))
        segments.append((X_CENTRAL, Y_BACK, X_CENTRAL, Y_FRONT))
    elif push_mode == "bump":
        segments.append((center_x, back_y, center_x, Y_FRONT))
        for x in X_POSITIONS:
            segments.append((x, Y_BACK, x, Y_FRONT))
    else:
        # center_and_sweep (default)
        segments.append((X_CENTRAL, Y_BACK, X_CENTRAL, Y_FRONT))
        segments.append((X_CENTRAL, Y_FRONT, X_CENTRAL, Y_BACK))
        segments.append((X_CENTRAL, Y_BACK, X_CENTRAL, Y_FRONT))
        for x in X_POSITIONS:
            segments.append((x, Y_BACK, x, Y_FRONT))

    return segments


def compute_contact_points(
    parts: list[dict[str, float]],
    sweep_z: float,
    push_mode: str,
    part_bounds: tuple[float, float] | None,
) -> list[tuple[float, float]]:
    """
    Compute approximate contact points where the pusher (at sweep_z) hits each part.
    Returns list of (x, y) points. A part is contacted if sweep_z is within [min_z, max_z]
    and its XY footprint intersects the pusher path.
    """
    segments = get_pusher_path_segments(push_mode, part_bounds)
    contacts: list[tuple[float, float]] = []

    def segment_intersects_box(
        x1: float, y1: float, x2: float, y2: float,
        mn_x: float, mx_x: float, mn_y: float, mx_y: float,
    ) -> tuple[float, float] | None:
        """Return midpoint of intersection segment if vertical/horizontal line crosses box."""
        if abs(x1 - x2) < 0.01:  # vertical line
            if mn_x <= x1 <= mx_x:
                overlap_y_lo = max(min(y1, y2), mn_y)
                overlap_y_hi = min(max(y1, y2), mx_y)
                if overlap_y_lo <= overlap_y_hi:
                    return (x1, (overlap_y_lo + overlap_y_hi) / 2)
        elif abs(y1 - y2) < 0.01:  # horizontal (not used for our path, but safe)
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


# Bed dimensions for preview (Bambu P1S)
BED_X_MIN, BED_X_MAX = 0.0, 256.0
BED_Y_MIN, BED_Y_MAX = 0.0, 256.0
Y_FRONT, Y_BACK = 0.0, 250.0
X_CENTRAL = 125.0
X_RAKE_POSITIONS = (220, 190, 160, 130, 100, 70, 30)


def get_parts_from_3mf(path: str | Path) -> list[dict[str, float]]:
    """
    Extract per-part bounds from 3MF mesh. Each part gets min_x, max_x, min_y, max_y, min_z, max_z.
    Returns list of part bounds. Requires trimesh; returns [] for sliced .gcode.3mf with no mesh.
    """
    path = Path(path)
    if not path.exists():
        return []
    if not TRIMESH_AVAILABLE:
        return []
    try:
        loaded = trimesh.load(str(path))
        if loaded is None:
            return []
        parts: list[dict[str, float]] = []
        if hasattr(loaded, "geometry"):
            for _name, geom in loaded.geometry.items():
                if hasattr(geom, "vertices"):
                    vs = geom.vertices
                    if len(vs) == 0:
                        continue
                    xs = [float(v[0]) for v in vs]
                    ys = [float(v[1]) for v in vs]
                    zs = [float(v[2]) for v in vs]
                    parts.append({
                        "min_x": min(xs), "max_x": max(xs),
                        "min_y": min(ys), "max_y": max(ys),
                        "min_z": min(zs), "max_z": max(zs),
                    })
        elif hasattr(loaded, "vertices"):
            vs = loaded.vertices
            xs = [float(v[0]) for v in vs]
            ys = [float(v[1]) for v in vs]
            zs = [float(v[2]) for v in vs]
            parts.append({
                "min_x": min(xs), "max_x": max(xs),
                "min_y": min(ys), "max_y": max(ys),
                "min_z": min(zs), "max_z": max(zs),
            })
        return parts
    except Exception:
        return []


def get_pusher_path_segments(
    push_mode: str,
    part_bounds: tuple[float, float] | None = None,
) -> list[tuple[float, float, float, float]]:
    """
    Return pusher path as list of (x1, y1, x2, y2) line segments in mm.
    Used for top-down preview overlay.
    """
    y_front, y_back = Y_FRONT, Y_BACK
    x_central = X_CENTRAL
    x_positions = X_RAKE_POSITIONS
    segments: list[tuple[float, float, float, float]] = []

    if push_mode == "bump" and part_bounds:
        center_x, back_y = part_bounds
        segments.append((center_x, back_y, center_x, y_front))
        for x in x_positions:
            segments.append((x, y_back, x, y_front))
        return segments

    if push_mode == "center_only":
        segments.append((x_central, y_back, x_central, y_front))
        segments.append((x_central, y_back, x_central, y_front))
        return segments

    # center_and_sweep
    segments.append((x_central, y_back, x_central, y_front))
    segments.append((x_central, y_back, x_central, y_front))
    for x in x_positions:
        segments.append((x, y_back, x, y_front))
    for x in x_positions:
        segments.append((x, y_back, x, y_front))
    return segments


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
