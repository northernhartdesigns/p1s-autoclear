"""
Update Bambu Metadata/model_settings.config for multi-plate merged 3MFs.

Bambu Studio requires each <plate> to have gcode_file pointing at Metadata/plate_N.gcode;
otherwise only plate 1 prints.
"""

from __future__ import annotations

import copy
import json
import re
import xml.etree.ElementTree as ET
from io import BytesIO


MODEL_SETTINGS_PATH = "Metadata/model_settings.config"
FILAMENT_SEQUENCE_PATH = "Metadata/filament_sequence.json"


def _local_tag(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _plate_children(root: ET.Element) -> list[ET.Element]:
    return [c for c in root if _local_tag(c.tag) == "plate"]


def _set_plate_metadata(plate_el: ET.Element, plate_num: int) -> None:
    """Ensure plater_id, gcode_file, and asset paths match plate_num."""
    keys_to_set = {
        "plater_id": str(plate_num),
        "gcode_file": f"Metadata/plate_{plate_num}.gcode",
        "thumbnail_file": f"Metadata/plate_{plate_num}.png",
        "thumbnail_no_light_file": f"Metadata/plate_no_light_{plate_num}.png",
        "top_file": f"Metadata/top_{plate_num}.png",
        "pick_file": f"Metadata/pick_{plate_num}.png",
        "pattern_bbox_file": f"Metadata/plate_{plate_num}.json",
    }
    seen: set[str] = set()
    for child in list(plate_el):
        if _local_tag(child.tag) != "metadata":
            continue
        k = child.get("key")
        if not k:
            continue
        seen.add(k)
        if k in keys_to_set:
            child.set("value", keys_to_set[k])
    for k, v in keys_to_set.items():
        if k not in seen:
            el = ET.SubElement(plate_el, "metadata")
            el.set("key", k)
            el.set("value", v)


def _minimal_plate_template() -> ET.Element:
    p = ET.Element("plate")
    for k, v in [
        ("plater_id", "1"),
        ("plater_name", ""),
        ("locked", "false"),
        ("filament_map_mode", "Auto For Flush"),
        ("filament_maps", "1 1 1 1 1"),
        ("filament_volume_maps", "0 0 0 0 0"),
        ("gcode_file", "Metadata/plate_1.gcode"),
        ("thumbnail_file", "Metadata/plate_1.png"),
        ("thumbnail_no_light_file", "Metadata/plate_no_light_1.png"),
        ("top_file", "Metadata/top_1.png"),
        ("pick_file", "Metadata/pick_1.png"),
    ]:
        m = ET.SubElement(p, "metadata")
        m.set("key", k)
        m.set("value", v)
    return p


def update_model_settings_for_plate_count(
    all_files: dict[str, bytes],
    num_plates: int,
) -> None:
    """
    Mutate all_files: fix model_settings.config and filament_sequence.json
    so plates 1..num_plates each reference the correct gcode and assets.
    """
    if num_plates < 1:
        return

    if MODEL_SETTINGS_PATH in all_files:
        try:
            text = all_files[MODEL_SETTINGS_PATH].decode("utf-8")
            root = ET.fromstring(text)
        except (ET.ParseError, UnicodeDecodeError):
            root = None
        if root is not None and _local_tag(root.tag) == "config":
            plates = _plate_children(root)
            if not plates:
                plates = [_minimal_plate_template()]
                root.append(plates[0])
            while len(plates) < num_plates:
                clone = copy.deepcopy(plates[0])
                root.append(clone)
                plates.append(clone)
            while len(plates) > num_plates:
                root.remove(plates[-1])
                plates.pop()
            for i, p_el in enumerate(plates, start=1):
                _set_plate_metadata(p_el, i)
            buf = BytesIO()
            buf.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
            ET.ElementTree(root).write(buf, encoding="utf-8", xml_declaration=False)
            all_files[MODEL_SETTINGS_PATH] = buf.getvalue()

    if FILAMENT_SEQUENCE_PATH in all_files:
        try:
            fs = json.loads(all_files[FILAMENT_SEQUENCE_PATH].decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            fs = {}
        if not isinstance(fs, dict):
            fs = {}
        template = fs.get("plate_1") if isinstance(fs.get("plate_1"), dict) else {"sequence": []}
        for n in range(1, num_plates + 1):
            key = f"plate_{n}"
            if key not in fs:
                fs[key] = copy.deepcopy(template) if isinstance(template, dict) else {"sequence": []}
        all_files[FILAMENT_SEQUENCE_PATH] = (
            json.dumps(fs, separators=(",", ":")).encode("utf-8")
        )


def plate_index_from_gcode_path(gcode_path: str) -> int:
    """e.g. Metadata/plate_2.gcode -> 2."""
    m = re.search(r"plate_(\d+)\.gcode", gcode_path.replace("\\", "/"), re.I)
    return int(m.group(1)) if m else 1


def copy_plate_visual_assets(
    src_zf,
    src_gcode_path: str,
    dest_plate_num: int,
    all_files: dict[str, bytes],
) -> None:
    """Copy thumbnails/json from source zip into all_files as plate_{dest_plate_num}.*"""
    src_plate = plate_index_from_gcode_path(src_gcode_path)
    pairs = [
        (f"Metadata/plate_{src_plate}.png", f"Metadata/plate_{dest_plate_num}.png"),
        (f"Metadata/plate_{src_plate}_small.png", f"Metadata/plate_{dest_plate_num}_small.png"),
        (f"Metadata/plate_no_light_{src_plate}.png", f"Metadata/plate_no_light_{dest_plate_num}.png"),
        (f"Metadata/top_{src_plate}.png", f"Metadata/top_{dest_plate_num}.png"),
        (f"Metadata/pick_{src_plate}.png", f"Metadata/pick_{dest_plate_num}.png"),
        (f"Metadata/plate_{src_plate}.json", f"Metadata/plate_{dest_plate_num}.json"),
    ]
    for src, dst in pairs:
        if dst in all_files:
            continue
        try:
            all_files[dst] = src_zf.read(src)
        except KeyError:
            pass
