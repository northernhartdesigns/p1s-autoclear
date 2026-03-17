"""
Main 3MF processing logic: load, modify machine_end_gcode, save.
"""

import json
import zipfile
from pathlib import Path

from .config_discovery import (
    find_config_files,
    get_machine_end_gcode,
    load_config_from_bytes,
    serialize_config,
    set_machine_end_gcode,
)
from .injector import (
    build_injection_block,
    build_injection_block_expanded,
    inject_autoclear_into_plate_gcode,
    inject_block,
    parse_max_z_from_plate_gcode,
    remove_purge_line_from_gcode,
    remove_purge_line_from_start_gcode,
    wrap_plate_gcode_in_loops,
)
from .preview import get_part_bounds_from_3mf


AUTOCLEAR_SETTINGS_PATH = "Metadata/p1s_autoclear_settings.json"


def get_autoclear_settings(input_path: str | Path) -> dict:
    """Read p1s_autoclear_settings from a 3MF (Metadata/p1s_autoclear_settings.json).
    Returns a dict with loop_count, cooldown_mode/value, push_height*, bending_mode,
    remove_purge_line, template. Returns {} if file not found or unparseable.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        return {}
    try:
        with zipfile.ZipFile(input_path, "r") as zf:
            data = zf.read(AUTOCLEAR_SETTINGS_PATH)
    except (KeyError, zipfile.BadZipFile):
        return {}
    try:
        return json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def process_3mf(
    input_path: str | Path,
    output_path: str | Path | None,
    cooldown_mode: str = "temp",
    cooldown_value: float = 35,
    template: str | None = None,
    loop_count: int = 1,
    remove_purge_line: bool = False,
    fans_during_cooldown: bool = False,
    skip_retraction_between_loops: bool = True,
    reheat_between_loops: bool = False,
    preheat_bed_temp: int = 70,
    preheat_nozzle_temp: int = 150,
    cooldown_hold_seconds: float = 60,
    *,
    push_height_mode: str = "auto",
    push_height_mm: float = 5.0,
    push_height_offset_mm: int = 20,
    bending_mode: str = "nhdfarm",
    push_mode: str = "center_and_sweep",
    push_heights: list[float] | None = None,
    use_plate_flex: bool | None = None,
    bed_level_interval: int = 0,
) -> str:
    """
    Load 3MF, inject NHDFARM-style auto-clear block into machine_end_gcode, save to output.
    Optionally removes purge line from start gcode and from sliced Metadata/plate_*.gcode.
    Writes p1s_autoclear_settings.json into the output 3MF for later reload.
    Returns the path of the saved file.
    Params: cooldown_mode ('time'|'temp'), cooldown_value, push_height_mode, push_height_mm,
    push_height_offset_mm, bending_mode ('nhdfarm'|'z_pop'|'none'), loop_count, remove_purge_line.
    Legacy: push_heights, use_plate_flex (for backward compat with old templates).
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    loop_count = max(1, min(999, loop_count))
    preheat_bed_temp = max(0, min(150, preheat_bed_temp))
    preheat_nozzle_temp = max(0, min(300, preheat_nozzle_temp))

    if output_path is None:
        stem = input_path.stem
        if stem.endswith(".gcode"):
            stem = Path(stem).stem
        output_path = input_path.parent / f"{stem}_autoclear.3mf"
    output_path = Path(output_path)

    part_bounds = get_part_bounds_from_3mf(input_path) if push_mode == "bump" else None

    loop_count = max(1, min(999, loop_count))
    preheat_bed_temp = max(0, min(150, preheat_bed_temp))
    preheat_nozzle_temp = max(0, min(300, preheat_nozzle_temp))

    # Use new NHDFARM params unless legacy push_heights provided
    if push_heights is not None and len(push_heights) > 0:
        block = build_injection_block(
            cooldown_mode=cooldown_mode,
            cooldown_value=cooldown_value,
            template=template,
            fans_during_cooldown=fans_during_cooldown,
            reheat_between_loops=reheat_between_loops,
            preheat_bed_temp=preheat_bed_temp,
            preheat_nozzle_temp=preheat_nozzle_temp,
            loop_count=loop_count,
            push_heights=push_heights,
            use_plate_flex=use_plate_flex if use_plate_flex is not None else False,
            cooldown_hold_seconds=cooldown_hold_seconds,
        )
    else:
        block = build_injection_block(
            cooldown_mode=cooldown_mode,
            cooldown_value=cooldown_value,
            push_height_mode=push_height_mode,
            push_height_mm=push_height_mm,
            push_height_offset_mm=push_height_offset_mm,
            bending_mode=bending_mode,
            push_mode=push_mode,
            template=template,
            fans_during_cooldown=fans_during_cooldown,
            reheat_between_loops=reheat_between_loops,
            preheat_bed_temp=preheat_bed_temp,
            preheat_nozzle_temp=preheat_nozzle_temp,
            loop_count=loop_count,
            part_bounds=part_bounds,
            cooldown_hold_seconds=cooldown_hold_seconds,
        )

    with zipfile.ZipFile(input_path, "r") as zf_in:
        all_files = {info.filename.replace("\\", "/"): zf_in.read(info.filename) for info in zf_in.infolist()}
        config_paths = find_config_files(zf_in)

    found_end_gcode = False
    for config_path in config_paths:
        cfg_data = all_files.get(config_path)
        if not cfg_data:
            continue
        config = load_config_from_bytes(cfg_data)
        if config is None:
            continue
        end_gcode = get_machine_end_gcode(config)
        if end_gcode is None:
            continue

        found_end_gcode = True
        config_modified = False
        new_end_gcode = inject_block(end_gcode, block)
        if new_end_gcode != end_gcode:
            set_machine_end_gcode(config, new_end_gcode)
            config_modified = True

        if remove_purge_line:
            start_gcode = config.get("machine_start_gcode")
            if start_gcode:
                new_start = remove_purge_line_from_start_gcode(start_gcode)
                if new_start != start_gcode:
                    config["machine_start_gcode"] = new_start
                    config_modified = True

        if config_modified:
            all_files[config_path] = serialize_config(config, cfg_data)

    # Process sliced G-code in Metadata/plate_*.gcode (inject auto-clear, purge, loops)
    for name in list(all_files.keys()):
        if "Metadata/plate_" in name and name.endswith(".gcode"):
            raw = all_files[name]
            try:
                gcode = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            # Inject auto-clear into plate gcode (same as Auto-Clear) - before purge/loops
            if push_heights is None or len(push_heights) == 0:
                max_z = parse_max_z_from_plate_gcode(gcode)
                if max_z is not None:
                    plate_block = build_injection_block_expanded(
                        max_layer_z=max_z,
                        cooldown_mode=cooldown_mode,
                        cooldown_value=cooldown_value,
                        push_height_mode=push_height_mode,
                        push_height_mm=push_height_mm,
                        push_height_offset_mm=push_height_offset_mm,
                        bending_mode=bending_mode,
                        push_mode=push_mode,
                        template=template,
                        fans_during_cooldown=fans_during_cooldown,
                        reheat_between_loops=reheat_between_loops,
                        preheat_bed_temp=preheat_bed_temp,
                        preheat_nozzle_temp=preheat_nozzle_temp,
                        loop_count=loop_count,
                        part_bounds=part_bounds,
                        cooldown_hold_seconds=cooldown_hold_seconds,
                    )
                    gcode = inject_autoclear_into_plate_gcode(gcode, plate_block)
            if remove_purge_line:
                gcode = remove_purge_line_from_gcode(gcode)
            if loop_count > 1:
                gcode = wrap_plate_gcode_in_loops(
                    gcode,
                    loop_count,
                    skip_retraction_between_loops=skip_retraction_between_loops,
                    reheat_between_loops=reheat_between_loops,
                    preheat_bed_temp=preheat_bed_temp,
                    preheat_nozzle_temp=preheat_nozzle_temp,
                )
            all_files[name] = gcode.encode("utf-8")

    if not found_end_gcode:
        raise ValueError(
            "No machine_end_gcode found in 3MF. "
            "Ensure the file is a Bambu Studio or PrusaSlicer project."
        )

    # Write full autoclear settings to 3MF (stored in file for reload)
    autoclear_settings = {
        "loop_count": loop_count,
        "bed_level_interval": max(0, bed_level_interval),
        "cooldown_mode": cooldown_mode,
        "cooldown_value": cooldown_value,
        "cooldown_hold_seconds": cooldown_hold_seconds,
        "remove_purge_line": remove_purge_line,
        "fans_during_cooldown": fans_during_cooldown,
        "skip_retraction_between_loops": skip_retraction_between_loops,
        "reheat_between_loops": reheat_between_loops,
        "preheat_bed_temp": preheat_bed_temp,
        "preheat_nozzle_temp": preheat_nozzle_temp,
    }
    if push_heights is not None and len(push_heights) > 0:
        autoclear_settings["push_heights"] = push_heights
        autoclear_settings["use_plate_flex"] = use_plate_flex if use_plate_flex is not None else False
    else:
        autoclear_settings["push_height_mode"] = push_height_mode
        autoclear_settings["push_height_mm"] = push_height_mm
        autoclear_settings["push_height_offset_mm"] = push_height_offset_mm
        autoclear_settings["bending_mode"] = bending_mode
        autoclear_settings["push_mode"] = push_mode
    if template:
        autoclear_settings["template"] = template
    all_files[AUTOCLEAR_SETTINGS_PATH] = json.dumps(
        autoclear_settings, indent=2
    ).encode("utf-8")

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf_out:
        for name, data in all_files.items():
            zf_out.writestr(name, data)

    return str(output_path)
