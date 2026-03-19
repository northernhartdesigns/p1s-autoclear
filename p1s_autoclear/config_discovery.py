"""
Discover and parse config files inside 3MF archives that contain machine_end_gcode.
Bambu Studio: Metadata/machine_settings_*.config (JSON)
PrusaSlicer: Metadata/Slic3r_PE.config (INI-style)
"""

import json
import re
import zipfile
from pathlib import Path
from typing import Optional

# Config paths used by Bambu Studio and PrusaSlicer
BAMBU_CONFIG_PATTERNS = [
    "Metadata/machine_settings_*.config",
    "Metadata/machine_settings.config",
    "Metadata/model_settings.config",
    "Metadata/print_profile.config",
    "Metadata/project_settings.config",
]
PRUSA_CONFIG_PATTERNS = [
    "Metadata/Slic3r_PE.config",
    "Metadata/Slic3r_PE_model.config",
]
# Insert block right before M17 S (printer sleep)
ANCHOR_BEFORE_M17 = "M17 S"
FALLBACK_ANCHOR = "M104 S0"


def _glob_match(pattern: str, name: str) -> bool:
    """Simple glob match: * matches any chars."""
    regex = pattern.replace("*", r"[^/]*").replace(".", r"\.")
    return bool(re.match(regex + "$", name))


def find_config_files(zf: zipfile.ZipFile) -> list[str]:
    """Find config file paths in a 3MF ZIP that likely contain machine_end_gcode.
    Searches for Bambu Studio (Metadata/machine_settings*.config etc.) and
    PrusaSlicer (Metadata/Slic3r_PE.config) patterns. Returns unique paths.
    """
    candidates = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        for pattern in BAMBU_CONFIG_PATTERNS + PRUSA_CONFIG_PATTERNS:
            if _glob_match(pattern, name):
                candidates.append(name)
                break
        # Also accept any .config under Metadata that contains machine_end
        if "Metadata/" in name and name.endswith(".config"):
            if name not in candidates:
                candidates.append(name)
    return list(dict.fromkeys(candidates))


def _parse_json_config(data: bytes) -> Optional[dict]:
    """Parse JSON config. Bambu uses JSON."""
    try:
        return json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _parse_ini_config(data: bytes) -> Optional[dict]:
    """Parse Prusa-style INI config (key = value)."""
    try:
        text = data.decode("utf-8")
        result = {}
        current_section = ""
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current_section = line[1:-1].strip()
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                if current_section:
                    full_key = f"{current_section}_{key}"
                else:
                    full_key = key
                result[full_key] = val
                result[key] = val  # Also store without section for lookup
        return result if result else None
    except UnicodeDecodeError:
        return None


def load_config(zf: zipfile.ZipFile, path: str) -> Optional[dict]:
    """Load and parse a config file from the 3MF ZIP by path.
    Supports both JSON (Bambu) and INI-style (PrusaSlicer) formats.
    Returns None if file is missing or unparseable.
    """
    try:
        data = zf.read(path)
    except KeyError:
        return None
    return load_config_from_bytes(data)


def load_config_from_bytes(data: bytes) -> Optional[dict]:
    """Parse config from raw bytes. Tries JSON first (Bambu), then INI (PrusaSlicer).
    Returns None if neither format parses successfully.
    """
    cfg = _parse_json_config(data)
    if cfg is not None:
        return cfg
    return _parse_ini_config(data)


def get_machine_end_gcode(config: dict) -> Optional[str]:
    """Extract machine_end_gcode from a parsed config dict.
    Returns None if the key is missing or empty.
    """
    return config.get("machine_end_gcode")


def set_machine_end_gcode(config: dict, value: str) -> None:
    """Set machine_end_gcode in the config dict (in-place)."""
    config["machine_end_gcode"] = value


def serialize_config(config: dict, original_bytes: bytes) -> bytes:
    """Serialize the config dict back to bytes for writing into the 3MF.

    When the original file is JSON (Bambu Studio), output is JSON with the same
    structure. When the original is PrusaSlicer INI text, we still emit JSON here
    (INI round-trip is not implemented); avoid relying on this for Prusa projects
    or re-serialize only JSON machine_settings in the archive.
    """
    try:
        json.loads(original_bytes.decode("utf-8"))
        return json.dumps(config, indent=4, ensure_ascii=False).encode("utf-8")
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return json.dumps(config, indent=4, ensure_ascii=False).encode("utf-8")
