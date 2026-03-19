"""
Merge multiple 3MF files into a single multi-plate 3MF with per-plate settings.

Each input 3MF must be sliced and contain at least one Metadata/plate_*.gcode.

Two modes:
- **Multi-plate**: plate_1, plate_2, … (separate print jobs in Bambu).
- **Chain** (merge_3mf_chain_files): one plate_1.gcode — all jobs back-to-back with
  auto-clear between (one continuous print).
"""

import json
import re
import zipfile
from pathlib import Path

from .injector import chain_plate_segments
from .merge_model_settings import (
    copy_plate_visual_assets,
    update_model_settings_for_plate_count,
)
from .preview import get_part_bounds_from_3mf
from .processor import (
    AUTOCLEAR_SETTINGS_PATH,
    apply_autoclear_to_plate_gcode_str,
    get_autoclear_settings,
)

# Keys to extract from each source's autoclear settings for plate_settings
_PLATE_SETTING_KEYS = (
    "loop_count",
    "cooldown_mode",
    "cooldown_value",
    "cooldown_hold_seconds",
    "remove_purge_line",
    "fans_during_cooldown",
    "skip_retraction_between_loops",
    "reheat_between_loops",
    "preheat_bed_temp",
    "preheat_nozzle_temp",
    "push_height_mode",
    "push_height_mm",
    "push_height_offset_mm",
    "bending_mode",
    "push_mode",
    "template",
    "push_heights",
    "use_plate_flex",
)


def _extract_plate_settings(autoclear: dict) -> dict:
    """Extract processor-relevant keys from autoclear dict for plate_settings.
    Accepts both autoclear format (cooldown_value) and GUI format (cooldown_temp/time).
    """
    out = {}
    for k in _PLATE_SETTING_KEYS:
        if k in autoclear and autoclear[k] is not None:
            out[k] = autoclear[k]
    # Normalize cooldown_value from GUI format (cooldown_temp, cooldown_time)
    if "cooldown_value" not in out:
        mode = str(out.get("cooldown_mode", autoclear.get("cooldown_mode", "temp")))
        if mode == "temp" and "cooldown_temp" in autoclear:
            try:
                out["cooldown_value"] = float(autoclear["cooldown_temp"])
            except (TypeError, ValueError):
                pass
        elif mode == "time" and "cooldown_time" in autoclear:
            try:
                out["cooldown_value"] = float(autoclear["cooldown_time"])
            except (TypeError, ValueError):
                pass
    # Normalize numeric strings to int/float for processor
    for k in ("loop_count", "preheat_bed_temp", "preheat_nozzle_temp", "cooldown_hold_seconds"):
        if k in out and isinstance(out[k], str):
            try:
                out[k] = int(out[k]) if k != "cooldown_hold_seconds" else float(out[k])
            except (TypeError, ValueError):
                pass
    for k in ("push_height_mm", "push_height_offset_mm"):
        if k in out and isinstance(out[k], str):
            try:
                out[k] = float(out[k]) if k == "push_height_mm" else int(out[k])
            except (TypeError, ValueError):
                pass
    # Normalize bending_mode: GUI uses "on"/"off", processor expects "nhdfarm"/"none"
    if "bending_mode" in out:
        bm = str(out["bending_mode"]).lower()
        if bm in ("on", "nhdfarm", "farmloop"):
            out["bending_mode"] = "nhdfarm"
        elif bm in ("off", "none"):
            out["bending_mode"] = "none"
        elif bm == "z_pop":
            out["bending_mode"] = "none"
    return out


def _get_plate_gcode_path(zf: zipfile.ZipFile) -> str | None:
    """Find first Metadata/plate_*.gcode entry in zip. Returns path or None."""
    for name in zf.namelist():
        n = name.replace("\\", "/")
        if "Metadata/plate_" in n and n.endswith(".gcode"):
            return name
    return None


def merge_3mf_files(
    paths: list[str | Path],
    output_path: str | Path,
    settings_per_file: list[dict] | None = None,
) -> Path:
    """
    Merge multiple sliced 3MF files into one multi-plate 3MF.

    Each input must have at least one Metadata/plate_*.gcode. Output plate 1 = file 0's
    first plate, plate 2 = file 1's first plate, etc. Base config (machine, models) from first file.
    Writes p1s_autoclear_settings.json with plate_settings keyed by plate index.

    If settings_per_file is provided (same length as paths), use those for each plate
    instead of extracting from each file's p1s_autoclear_settings.

    Returns the output path.
    """
    paths = [Path(p) for p in paths]
    if not paths:
        raise ValueError("At least one input path required")
    output_path = Path(output_path)
    if settings_per_file is not None and len(settings_per_file) != len(paths):
        raise ValueError("settings_per_file must have same length as paths")

    all_files: dict[str, bytes] = {}
    plate_settings: dict[str, dict] = {}

    with zipfile.ZipFile(paths[0], "r") as zf:
        gcode_path_0 = _get_plate_gcode_path(zf)
        if gcode_path_0 is None:
            raise ValueError(
                f"{paths[0].name} has no Metadata/plate_*.gcode. "
                "Input must be a sliced 3MF or .gcode.3mf."
            )
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            all_files[name] = zf.read(info.filename)
        # Normalize first file's plate to Metadata/plate_1.gcode (it may be plate_2 etc.)
        norm_name = gcode_path_0.replace("\\", "/")
        if norm_name != "Metadata/plate_1.gcode":
            all_files["Metadata/plate_1.gcode"] = all_files.pop(norm_name)
        copy_plate_visual_assets(zf, gcode_path_0, 1, all_files)

    if settings_per_file:
        plate_settings["1"] = _extract_plate_settings(settings_per_file[0])
    else:
        autoclear_0 = get_autoclear_settings(paths[0])
        plate_settings["1"] = _extract_plate_settings(autoclear_0) if autoclear_0 else {}

    for i, path in enumerate(paths[1:], start=2):
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        plate_name = f"Metadata/plate_{i}.gcode"
        with zipfile.ZipFile(path, "r") as zf:
            gcode_path = _get_plate_gcode_path(zf)
            if gcode_path is None:
                raise ValueError(
                    f"{path.name} has no Metadata/plate_*.gcode. "
                    "Input must be a sliced 3MF or .gcode.3mf."
                )
            data = zf.read(gcode_path)
            all_files[plate_name] = data
            copy_plate_visual_assets(zf, gcode_path, i, all_files)
        if settings_per_file:
            plate_settings[str(i)] = _extract_plate_settings(settings_per_file[i - 1])
        else:
            autoclear = get_autoclear_settings(path)
            plate_settings[str(i)] = _extract_plate_settings(autoclear) if autoclear else {}

    base = {
        "loop_count": 1,
        "bed_level_interval": 0,
        "plate_settings": plate_settings,
    }
    if plate_settings.get("1"):
        base.update({k: v for k, v in plate_settings["1"].items() if k != "plate_settings"})
    all_files[AUTOCLEAR_SETTINGS_PATH] = json.dumps(
        base, indent=2
    ).encode("utf-8")

    update_model_settings_for_plate_count(all_files, len(paths))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in all_files.items():
            zf.writestr(name, data)

    return output_path


_PLATE_ASSET_NUM_PATTERNS = (
    re.compile(r"^Metadata/plate_(\d+)\.gcode$", re.I),
    re.compile(r"^Metadata/plate_(\d+)\.gcode\.md5$", re.I),
    re.compile(r"^Metadata/plate_(\d+)\.png$", re.I),
    re.compile(r"^Metadata/plate_(\d+)_small\.png$", re.I),
    re.compile(r"^Metadata/plate_(\d+)\.json$", re.I),
    re.compile(r"^Metadata/plate_no_light_(\d+)\.png$", re.I),
    re.compile(r"^Metadata/top_(\d+)\.png$", re.I),
    re.compile(r"^Metadata/pick_(\d+)\.png$", re.I),
)


def _strip_plate_assets_after_first(all_files: dict[str, bytes]) -> None:
    """Remove plate_2+ assets so the project is single-plate."""
    to_del: list[str] = []
    for k in all_files:
        kn = k.replace("\\", "/")
        for pat in _PLATE_ASSET_NUM_PATTERNS:
            m = pat.match(kn)
            if m and int(m.group(1)) != 1:
                to_del.append(k)
                break
    for k in to_del:
        del all_files[k]


def _resolve_chain_job(path: Path, gui_settings: dict, defaults: dict) -> dict:
    """Merge defaults, file autoclear JSON, and per-row GUI settings."""
    ac = get_autoclear_settings(path)
    r = dict(defaults)
    for k, v in ac.items():
        if k == "plate_settings":
            continue
        if v is not None:
            r[k] = v
    r.update(_extract_plate_settings(gui_settings))
    return r


def merge_3mf_chain_files(
    paths: list[str | Path],
    output_path: str | Path,
    settings_per_file: list[dict] | None,
    default_params: dict,
    *,
    skip_retraction_between_jobs: bool = True,
    reheat_between_jobs: bool = False,
    preheat_bed_temp: int = 70,
    preheat_nozzle_temp: int = 150,
    pause_between_jobs: float = 2.0,
) -> Path:
    """
    Build one 3MF with a single plate whose gcode runs job 1 → auto-clear → job 2 → …
    Same AMS / filament assumptions as loop mode (optional retract between jobs).
    """
    paths = [Path(p) for p in paths]
    if len(paths) < 2:
        raise ValueError("Chain mode requires at least two input files")
    output_path = Path(output_path)
    if settings_per_file is not None and len(settings_per_file) != len(paths):
        raise ValueError("settings_per_file must match paths length")

    all_files: dict[str, bytes] = {}
    with zipfile.ZipFile(paths[0], "r") as zf:
        gcode_path_0 = _get_plate_gcode_path(zf)
        if gcode_path_0 is None:
            raise ValueError(
                f"{paths[0].name} has no Metadata/plate_*.gcode. "
                "Input must be a sliced 3MF or .gcode.3mf."
            )
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            all_files[name] = zf.read(info.filename)
        norm_name = gcode_path_0.replace("\\", "/")
        if norm_name != "Metadata/plate_1.gcode":
            all_files["Metadata/plate_1.gcode"] = all_files.pop(norm_name)
        copy_plate_visual_assets(zf, gcode_path_0, 1, all_files)

    _strip_plate_assets_after_first(all_files)

    segments: list[str] = []
    plate_settings_out: dict[str, dict] = {}
    for i, path in enumerate(paths):
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        gui = settings_per_file[i] if settings_per_file else {}
        plate_settings_out[str(i + 1)] = _extract_plate_settings(gui)
        resolved = _resolve_chain_job(path, gui, default_params)
        with zipfile.ZipFile(path, "r") as zf:
            gp = _get_plate_gcode_path(zf)
            if gp is None:
                raise ValueError(f"{path.name} has no Metadata/plate_*.gcode.")
            raw = zf.read(gp).decode("utf-8")
        fb = None
        if resolved.get("push_mode") in ("part_center", "part_center_sweep"):
            fb = get_part_bounds_from_3mf(path)
        segments.append(
            apply_autoclear_to_plate_gcode_str(raw, resolved, part_bounds_fallback=fb)
        )

    chained = chain_plate_segments(
        segments,
        skip_retraction_between_jobs=skip_retraction_between_jobs,
        reheat_between_jobs=reheat_between_jobs,
        preheat_bed_temp=preheat_bed_temp,
        preheat_nozzle_temp=preheat_nozzle_temp,
        pause_seconds=pause_between_jobs,
    )
    all_files["Metadata/plate_1.gcode"] = chained.encode("utf-8")

    update_model_settings_for_plate_count(all_files, 1)

    base_settings = {
        "chain_single_print": True,
        "chain_jobs": len(paths),
        "loop_count": 1,
        "cooldown_mode": default_params.get("cooldown_mode", "temp"),
        "cooldown_value": float(default_params.get("cooldown_value", 35)),
        "cooldown_hold_seconds": float(default_params.get("cooldown_hold_seconds", 60)),
        "remove_purge_line": bool(default_params.get("remove_purge_line", False)),
        "fans_during_cooldown": bool(default_params.get("fans_during_cooldown", False)),
        "skip_retraction_between_loops": skip_retraction_between_jobs,
        "reheat_between_loops": reheat_between_jobs,
        "preheat_bed_temp": preheat_bed_temp,
        "preheat_nozzle_temp": preheat_nozzle_temp,
        "push_height_mode": default_params.get("push_height_mode", "auto"),
        "push_height_mm": float(default_params.get("push_height_mm", 5.0)),
        "push_height_offset_mm": int(default_params.get("push_height_offset_mm", 20)),
        "bending_mode": default_params.get("bending_mode", "nhdfarm"),
        "push_mode": default_params.get("push_mode", "center_and_sweep"),
        "plate_settings": plate_settings_out,
    }
    if default_params.get("template"):
        base_settings["template"] = default_params["template"]
    all_files[AUTOCLEAR_SETTINGS_PATH] = json.dumps(base_settings, indent=2).encode("utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(all_files.items()):
            zf.writestr(name, data)

    return output_path
