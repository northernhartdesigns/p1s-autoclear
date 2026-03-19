"""
User settings and profiles: last-used settings plus filament-type profiles.
Stored in platform app data dir (e.g. %APPDATA%\\P1S-AutoClear on Windows).
"""

import json
import os
from pathlib import Path
from typing import Any


def _config_dir() -> Path:
    """Return platform-specific config directory (e.g. %APPDATA%\\P1S-AutoClear on Windows)."""
    if os.name == "nt":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif os.name == "posix":
        # macOS: ~/Library/Application Support; Linux: ~/.config
        base = os.environ.get(
            "XDG_CONFIG_HOME",
            os.path.expanduser("~/Library/Application Support") if os.uname().sysname == "Darwin" else os.path.expanduser("~/.config"),
        )
    else:
        base = os.path.expanduser("~")
    return Path(base) / "P1S-AutoClear"


def _profiles_dir() -> Path:
    """Return profiles subdir under config, creating it if needed."""
    d = _config_dir() / "profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _settings_path() -> Path:
    """Path to last-used settings JSON file (settings.json in config dir)."""
    d = _config_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "settings.json"


def _app_config_path() -> Path:
    """Path to app preferences (export/import paths, etc.)."""
    return _config_dir() / "app_config.json"


def load_app_config() -> dict[str, Any]:
    """Load app preferences. Returns dict with default_export_path, open_export_folder_after_export, default_import_path."""
    path = _app_config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_app_config(data: dict[str, Any]) -> None:
    """Save app preferences (merge with existing, never overwrite other keys)."""
    path = _app_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_app_config()
    merged = {**existing, **data}
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")


def settings_to_dict(
    *,
    cooldown_mode: str = "temp",
    cooldown_time: str = "180",
    cooldown_temp: str = "35",
    cooldown_hold_seconds: str = "60",
    loop_count: str = "1",
    remove_purge: bool = False,
    fans_during_cooldown: bool = False,
    skip_retraction_between_loops: bool = True,
    reheat_between_loops: bool = False,
    preheat_bed_temp: str = "70",
    preheat_nozzle_temp: str = "150",
    template: str = "",
    push_height_mode: str = "auto",
    push_height_mm: str = "5",
    push_height_offset_mm: str = "20",
    bending_mode: str = "nhdfarm",
    push_mode: str = "center_and_sweep",
    bed_level_interval: str = "0",
) -> dict[str, Any]:
    """Build a settings dict from GUI values for saving to disk or 3MF metadata."""
    return {
        "cooldown_mode": cooldown_mode,
        "cooldown_time": cooldown_time,
        "cooldown_temp": cooldown_temp,
        "cooldown_hold_seconds": cooldown_hold_seconds,
        "loop_count": loop_count,
        "remove_purge_line": remove_purge,
        "fans_during_cooldown": fans_during_cooldown,
        "skip_retraction_between_loops": skip_retraction_between_loops,
        "reheat_between_loops": reheat_between_loops,
        "preheat_bed_temp": preheat_bed_temp,
        "preheat_nozzle_temp": preheat_nozzle_temp,
        "template": template,
        "push_height_mode": push_height_mode,
        "push_height_mm": push_height_mm,
        "push_height_offset_mm": push_height_offset_mm,
        "bending_mode": bending_mode,
        "push_mode": push_mode,
        "bed_level_interval": str(bed_level_interval).strip() if str(bed_level_interval).strip().isdigit() else "0",
    }


def autoclear_to_gui_settings(autoclear: dict[str, Any]) -> dict[str, Any]:
    """Convert 3MF autoclear settings (from get_autoclear_settings) to GUI format.
    Maps cooldown_value to cooldown_temp or cooldown_time based on mode.
    Handles legacy push_heights/use_plate_flex and legacy->nhdfarm migration.
    """
    out: dict[str, Any] = {}
    if "loop_count" in autoclear:
        out["loop_count"] = str(int(autoclear["loop_count"]))
    if "cooldown_mode" in autoclear:
        out["cooldown_mode"] = str(autoclear["cooldown_mode"])
    if "cooldown_value" in autoclear:
        v = float(autoclear["cooldown_value"])
        mode = str(autoclear.get("cooldown_mode", "time"))
        if mode == "temp":
            out["cooldown_temp"] = str(int(v))
        else:
            out["cooldown_time"] = str(int(v))
    if "cooldown_hold_seconds" in autoclear:
        out["cooldown_hold_seconds"] = str(int(autoclear["cooldown_hold_seconds"]))
    # New NHDFARM params
    if "push_height_mode" in autoclear:
        out["push_height_mode"] = str(autoclear["push_height_mode"])
    if "push_height_mm" in autoclear:
        out["push_height_mm"] = str(autoclear["push_height_mm"])
    if "push_height_offset_mm" in autoclear:
        out["push_height_offset_mm"] = str(int(autoclear["push_height_offset_mm"]))
    if "bending_mode" in autoclear:
        bm = str(autoclear["bending_mode"]).lower()
        if bm == "z_pop":
            bm = "none"
        out["bending_mode"] = "on" if bm in ("nhdfarm", "farmloop") else "off"
    if "push_mode" in autoclear:
        pm = str(autoclear["push_mode"])
        if pm == "part_span":
            pm = "part_center"
        elif pm == "part_span_sweep":
            pm = "part_center_sweep"
        _pm_ok = (
            "center_only",
            "center_and_sweep",
            "part_center",
            "part_center_sweep",
            "bump",
        )
        out["push_mode"] = "center_and_sweep" if pm not in _pm_ok else ("part_center" if pm == "bump" else pm)
    if "bed_level_interval" in autoclear:
        out["bed_level_interval"] = str(int(autoclear["bed_level_interval"]))
    # Legacy migration: push_heights -> push_height_mode "auto" or manual with first value
    if "push_height_mode" not in out and "push_heights" in autoclear:
        ph = autoclear["push_heights"]
        if isinstance(ph, list) and len(ph) > 0 and ph[0] > 1.0:
            out["push_height_mode"] = "manual"
            out["push_height_mm"] = str(int(ph[0]))
        else:
            out["push_height_mode"] = "auto"
            out["push_height_mm"] = "5"
    # Legacy: use_plate_flex -> bending on (nhdfarm) or off
    if "bending_mode" not in out and "use_plate_flex" in autoclear:
        out["bending_mode"] = "on" if autoclear["use_plate_flex"] else "off"
    if "remove_purge_line" in autoclear:
        out["remove_purge_line"] = bool(autoclear["remove_purge_line"])
    if "fans_during_cooldown" in autoclear:
        out["fans_during_cooldown"] = bool(autoclear["fans_during_cooldown"])
    if "skip_retraction_between_loops" in autoclear:
        out["skip_retraction_between_loops"] = bool(autoclear["skip_retraction_between_loops"])
    if "reheat_between_loops" in autoclear:
        out["reheat_between_loops"] = bool(autoclear["reheat_between_loops"])
    if "preheat_bed_temp" in autoclear:
        out["preheat_bed_temp"] = str(int(autoclear["preheat_bed_temp"]))
    if "preheat_nozzle_temp" in autoclear:
        out["preheat_nozzle_temp"] = str(int(autoclear["preheat_nozzle_temp"]))
    if "template" in autoclear and autoclear["template"]:
        out["template"] = str(autoclear["template"])
    return out


def apply_settings_to_gui(
    data: dict[str, Any],
    *,
    cooldown_mode_var,
    cooldown_time_var,
    cooldown_temp_var,
    cooldown_hold_seconds_var=None,
    loop_count_var,
    remove_purge_var,
    fans_during_cooldown_var=None,
    skip_retraction_between_loops_var=None,
    reheat_between_loops_var=None,
    preheat_bed_temp_var=None,
    preheat_nozzle_temp_var=None,
    template_text=None,
    default_template: str = "",
    push_heights_var=None,
    use_plate_flex_var=None,
    push_height_mode_var=None,
    push_height_mm_var=None,
    push_height_offset_var=None,
    bending_mode_var=None,
    push_mode_var=None,
    bed_level_interval_var=None,
) -> None:
    """Apply a settings dict to GUI variables/widgets (StringVar, BooleanVar, Text).
    Updates cooldown, loop count, purge, push height, bending, template. Skips missing keys.
    """
    if "cooldown_mode" in data:
        cooldown_mode_var.set(str(data["cooldown_mode"]))
    if "cooldown_time" in data:
        cooldown_time_var.set(str(data["cooldown_time"]))
    if "cooldown_temp" in data:
        cooldown_temp_var.set(str(data["cooldown_temp"]))
    if cooldown_hold_seconds_var is not None and "cooldown_hold_seconds" in data:
        cooldown_hold_seconds_var.set(str(int(data["cooldown_hold_seconds"])))
    if push_heights_var is not None and "push_heights" in data:
        push_heights_var.set(str(data["push_heights"]))
    if "loop_count" in data:
        loop_count_var.set(str(int(data["loop_count"])))
    if "remove_purge_line" in data:
        remove_purge_var.set(bool(data["remove_purge_line"]))
    if fans_during_cooldown_var is not None and "fans_during_cooldown" in data:
        fans_during_cooldown_var.set(bool(data["fans_during_cooldown"]))
    if skip_retraction_between_loops_var is not None and "skip_retraction_between_loops" in data:
        skip_retraction_between_loops_var.set(bool(data["skip_retraction_between_loops"]))
    if reheat_between_loops_var is not None and "reheat_between_loops" in data:
        reheat_between_loops_var.set(bool(data["reheat_between_loops"]))
    if preheat_bed_temp_var is not None and "preheat_bed_temp" in data:
        preheat_bed_temp_var.set(str(int(data["preheat_bed_temp"])))
    if preheat_nozzle_temp_var is not None and "preheat_nozzle_temp" in data:
        preheat_nozzle_temp_var.set(str(int(data["preheat_nozzle_temp"])))
    if use_plate_flex_var is not None and "use_plate_flex" in data:
        use_plate_flex_var.set(bool(data["use_plate_flex"]))
    if push_height_mode_var is not None and "push_height_mode" in data:
        push_height_mode_var.set(str(data["push_height_mode"]))
    if push_height_mm_var is not None and "push_height_mm" in data:
        push_height_mm_var.set(str(data["push_height_mm"]))
    if push_height_offset_var is not None and "push_height_offset_mm" in data:
        try:
            val = data["push_height_offset_mm"]
            s = str(val).strip()
            parsed = int(s) if s else 20
            push_height_offset_var.set(str(max(1, parsed)))
        except (ValueError, TypeError):
            push_height_offset_var.set("20")
    if bending_mode_var is not None and "bending_mode" in data:
        bm = str(data["bending_mode"]).lower()
        bending_mode_var.set(
            "on" if bm in ("nhdfarm", "farmloop", "on") else "off"
        )
    if push_mode_var is not None and "push_mode" in data:
        pm = str(data["push_mode"])
        if pm == "part_span":
            pm = "part_center"
        elif pm == "part_span_sweep":
            pm = "part_center_sweep"
        _pm_ok = (
            "center_only",
            "center_and_sweep",
            "part_center",
            "part_center_sweep",
            "bump",
        )
        push_mode_var.set("center_and_sweep" if pm not in _pm_ok else ("part_center" if pm == "bump" else pm))
    if bed_level_interval_var is not None and "bed_level_interval" in data:
        bed_level_interval_var.set(str(int(data["bed_level_interval"])))
    if template_text is not None and default_template:
        template_text.delete("1.0", "end")
        tpl = (data.get("template") or "").strip() if isinstance(data.get("template"), str) else ""
        template_text.insert("1.0", tpl or default_template)


def load_last_settings() -> dict[str, Any]:
    """Load last-used settings from settings.json. Returns {} if missing or invalid JSON."""
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_last_settings(data: dict[str, Any]) -> None:
    """Save last-used settings to settings.json (overwrites existing)."""
    path = _settings_path()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def list_profiles() -> list[str]:
    """List available custom profile names from profiles dir. Built-in profiles are in BUILTIN_PROFILES."""
    d = _profiles_dir()
    names = []
    for p in d.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("name"):
                names.append(data["name"])
            else:
                names.append(p.stem)
        except (json.JSONDecodeError, OSError):
            pass
    return sorted(set(names))


def load_profile(name: str) -> dict[str, Any] | None:
    """Load a custom profile by name (from profiles/*.json). Returns None if not found."""
    d = _profiles_dir()
    for p in d.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("name") == name:
                return data
            if p.stem == name:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return None


def save_profile(name: str, data: dict[str, Any]) -> None:
    """Save a profile under the given name. Name is sanitized for the filename."""
    data = dict(data)
    data["name"] = name
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip() or "profile"
    path = _profiles_dir() / f"{safe}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def delete_profile(name: str) -> bool:
    """Delete a custom profile by name. Returns True if found and deleted."""
    d = _profiles_dir()
    for p in d.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("name") == name:
                p.unlink()
                return True
            if p.stem == name:
                p.unlink()
                return True
        except (json.JSONDecodeError, OSError):
            pass
    return False


# Built-in default profiles for common filament types (NHDFARM-style)
# All settings included so profiles fully override when loaded.
_BUILTIN_BASE = {
    "bed_level_interval": "0",
    "push_height_mode": "auto",
    "push_height_mm": "5",
    "push_height_offset_mm": "20",
    "bending_mode": "on",
    "push_mode": "center_and_sweep",
    "loop_count": "1",
    "remove_purge_line": False,
    "fans_during_cooldown": False,
    "skip_retraction_between_loops": True,
    "reheat_between_loops": False,
    "preheat_bed_temp": "70",
    "preheat_nozzle_temp": "150",
    "template": "",
}

BUILTIN_PROFILES: dict[str, dict[str, Any]] = {
    "PLA": {
        **_BUILTIN_BASE,
        "cooldown_mode": "time",
        "cooldown_time": "120",
        "cooldown_temp": "35",
    },
    "PETG": {
        **_BUILTIN_BASE,
        "cooldown_mode": "temp",
        "cooldown_time": "180",
        "cooldown_temp": "35",
    },
    "ABS / ASA": {
        **_BUILTIN_BASE,
        "cooldown_mode": "temp",
        "cooldown_time": "300",
        "cooldown_temp": "60",
    },
}
