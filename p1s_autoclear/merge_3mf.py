"""
Merge multiple 3MF files into a single multi-plate 3MF with per-plate settings.

Each input 3MF must be sliced and contain Metadata/plate_1.gcode.
Output: one 3MF with plate_1 from file 0, plate_2 from file 1, etc.
Writes plate_settings into p1s_autoclear_settings.json for per-plate auto-clear config.
"""

import json
import zipfile
from pathlib import Path

from .processor import AUTOCLEAR_SETTINGS_PATH, get_autoclear_settings

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
            out["bending_mode"] = "z_pop"
    return out


def merge_3mf_files(
    paths: list[str | Path],
    output_path: str | Path,
    settings_per_file: list[dict] | None = None,
) -> Path:
    """
    Merge multiple sliced 3MF files into one multi-plate 3MF.

    Each input must have Metadata/plate_1.gcode. Output plate 1 = file 0's plate_1,
    plate 2 = file 1's plate_1, etc. Base config (machine, models) from first file.
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
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            all_files[name] = zf.read(info.filename)

    if settings_per_file:
        plate_settings["1"] = _extract_plate_settings(settings_per_file[0])
    else:
        autoclear_0 = get_autoclear_settings(paths[0])
        plate_settings["1"] = _extract_plate_settings(autoclear_0) if autoclear_0 else {}

    for i, path in enumerate(paths[1:], start=2):
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        plate_name = f"Metadata/plate_{i}.gcode"
        try:
            with zipfile.ZipFile(path, "r") as zf:
                data = zf.read("Metadata/plate_1.gcode")
        except KeyError:
            raise ValueError(
                f"{path.name} has no Metadata/plate_1.gcode. "
                "Input must be a sliced 3MF or .gcode.3mf."
            )
        all_files[plate_name] = data
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

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in all_files.items():
            zf.writestr(name, data)

    return output_path
