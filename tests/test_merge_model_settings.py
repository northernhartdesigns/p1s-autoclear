"""Tests for merge_model_settings (multi-plate Bambu config)."""

import json
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from pathlib import Path

from p1s_autoclear.merge_model_settings import (
    copy_plate_visual_assets,
    plate_index_from_gcode_path,
    update_model_settings_for_plate_count,
)


def _local_tag(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def test_plate_index_from_gcode_path():
    assert plate_index_from_gcode_path("Metadata/plate_1.gcode") == 1
    assert plate_index_from_gcode_path(r"Metadata\plate_12.gcode") == 12


def test_update_model_settings_sets_gcode_per_plate():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="plater_id" value="1"/>
    <metadata key="gcode_file" value="Metadata/plate_1.gcode"/>
  </plate>
  <plate>
    <metadata key="plater_id" value="2"/>
    <metadata key="gcode_file" value=""/>
  </plate>
</config>"""
    files = {"Metadata/model_settings.config": xml}
    update_model_settings_for_plate_count(files, 2)
    root = ET.fromstring(files["Metadata/model_settings.config"])
    plates = [c for c in root if _local_tag(c.tag) == "plate"]
    assert len(plates) == 2
    meta = {
        m.get("key"): m.get("value")
        for m in plates[1]
        if _local_tag(m.tag) == "metadata"
    }
    assert meta.get("gcode_file") == "Metadata/plate_2.gcode"
    assert meta.get("plater_id") == "2"


def test_update_model_settings_clones_plate_when_only_one():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="gcode_file" value="Metadata/plate_1.gcode"/>
  </plate>
</config>"""
    files = {"Metadata/model_settings.config": xml}
    update_model_settings_for_plate_count(files, 3)
    root = ET.fromstring(files["Metadata/model_settings.config"])
    plates = [c for c in root if _local_tag(c.tag) == "plate"]
    assert len(plates) == 3
    for i, p in enumerate(plates, start=1):
        vals = [
            m.get("value")
            for m in p
            if _local_tag(m.tag) == "metadata" and m.get("key") == "gcode_file"
        ]
        assert vals == [f"Metadata/plate_{i}.gcode"]


def test_filament_sequence_extended():
    files = {
        "Metadata/filament_sequence.json": b'{"plate_1":{"sequence":[1]}}',
    }
    update_model_settings_for_plate_count(files, 2)
    fs = json.loads(files["Metadata/filament_sequence.json"].decode())
    assert "plate_2" in fs
    assert fs["plate_2"] == {"sequence": [1]}


def test_copy_plate_visual_assets(tmp_path: Path):
    out_zip = tmp_path / "a.3mf"
    png = b"\x89PNG\r\n\x1a\n"
    with zipfile.ZipFile(out_zip, "w") as z:
        z.writestr("Metadata/plate_2.gcode", b";g")
        z.writestr("Metadata/plate_2.png", png)
        z.writestr("Metadata/plate_2.json", b"{}")
    all_files: dict[str, bytes] = {}
    with zipfile.ZipFile(out_zip, "r") as zf:
        copy_plate_visual_assets(zf, "Metadata/plate_2.gcode", 5, all_files)
    assert all_files["Metadata/plate_5.png"] == png
    assert all_files["Metadata/plate_5.json"] == b"{}"


def test_copy_skips_existing_dst():
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Metadata/plate_1.gcode", b";")
        z.writestr("Metadata/plate_1.png", b"A")
    buf.seek(0)
    all_files = {"Metadata/plate_1.png": b"B"}
    with zipfile.ZipFile(buf, "r") as zf:
        copy_plate_visual_assets(zf, "Metadata/plate_1.gcode", 1, all_files)
    assert all_files["Metadata/plate_1.png"] == b"B"
