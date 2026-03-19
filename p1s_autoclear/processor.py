"""
Main 3MF processing logic: load, modify machine_end_gcode, save.
"""

import json
import re
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
    get_part_bounds_from_gcode,
    get_part_xy_extents_from_gcode,
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
    Supports plate_settings for per-plate overrides in merged multi-file 3MFs.
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


def get_plate_settings(
    settings: dict,
    plate_num: int,
    defaults: dict | None = None,
) -> dict:
    """Resolve per-plate settings from autoclear_settings.
    Returns plate_settings[str(plate_num)] merged over defaults/top-level.
    Plate overrides take precedence. Missing keys fall back to defaults or top-level.
    """
    base = dict(defaults) if defaults else {}
    # Apply top-level keys as defaults (excluding plate_settings itself)
    for k, v in settings.items():
        if k != "plate_settings" and v is not None:
            base.setdefault(k, v)
    plate_overrides = settings.get("plate_settings") or {}
    override = plate_overrides.get(str(plate_num))
    if override:
        base = {**base, **{k: v for k, v in override.items() if v is not None}}
    return base


def apply_autoclear_to_plate_gcode_str(
    gcode: str,
    resolved: dict,
    part_bounds_fallback: list | None = None,
) -> str:
    """
    Inject auto-clear into one plate gcode and optionally wrap loops.
    resolved: same shape as per-plate resolution in process_3mf (cooldown_mode, loop_count, ...).
    """
    r_loop = max(1, min(999, int(resolved.get("loop_count", 1))))
    r_cooldown_mode = str(resolved.get("cooldown_mode", "temp"))
    r_cooldown_value = float(resolved.get("cooldown_value", 35))
    r_cooldown_hold = float(resolved.get("cooldown_hold_seconds", 60))
    r_remove_purge = bool(resolved.get("remove_purge_line", False))
    r_fans = bool(resolved.get("fans_during_cooldown", False))
    r_skip_retract = bool(resolved.get("skip_retraction_between_loops", True))
    r_reheat = bool(resolved.get("reheat_between_loops", False))
    r_preheat_bed = max(0, min(150, int(resolved.get("preheat_bed_temp", 70))))
    r_preheat_nozzle = max(0, min(300, int(resolved.get("preheat_nozzle_temp", 150))))
    r_push_mode = str(resolved.get("push_mode", "center_and_sweep"))
    r_push_height_mode = str(resolved.get("push_height_mode", "auto"))
    r_push_height_mm = float(resolved.get("push_height_mm", 5.0))
    r_push_height_offset = int(resolved.get("push_height_offset_mm", 20))
    r_bending = str(resolved.get("bending_mode", "nhdfarm"))
    r_template = (resolved.get("template") or "").strip() or None
    r_push_heights = resolved.get("push_heights")
    r_use_plate_flex = resolved.get("use_plate_flex")

    if r_push_heights is not None and len(r_push_heights) > 0:
        block = build_injection_block(
            cooldown_mode=r_cooldown_mode,
            cooldown_value=r_cooldown_value,
            template=r_template,
            fans_during_cooldown=r_fans,
            reheat_between_loops=r_reheat,
            preheat_bed_temp=r_preheat_bed,
            preheat_nozzle_temp=r_preheat_nozzle,
            loop_count=r_loop,
            push_heights=r_push_heights,
            use_plate_flex=bool(r_use_plate_flex) if r_use_plate_flex is not None else False,
            cooldown_hold_seconds=r_cooldown_hold,
        )
        gcode = inject_autoclear_into_plate_gcode(gcode, block)
    else:
        max_z = parse_max_z_from_plate_gcode(gcode)
        if max_z is None:
            max_z = 10.0
        plate_part_bounds = None
        if r_push_mode in ("part_center", "part_center_sweep"):
            plate_part_bounds = get_part_bounds_from_gcode(gcode)
        if plate_part_bounds is None:
            plate_part_bounds = part_bounds_fallback
        plate_xy_extents = None
        if r_push_mode in ("part_center", "part_center_sweep"):
            plate_xy_extents = get_part_xy_extents_from_gcode(gcode)
            if plate_xy_extents is None and plate_part_bounds:
                cx, by = plate_part_bounds[0]
                plate_xy_extents = (cx - 24.0, cx + 24.0, max(4.0, by - 48.0), by)
            elif plate_xy_extents is None and part_bounds_fallback:
                cx, by = part_bounds_fallback[0]
                plate_xy_extents = (cx - 24.0, cx + 24.0, max(4.0, by - 48.0), by)
        plate_block = build_injection_block_expanded(
            max_layer_z=max_z,
            cooldown_mode=r_cooldown_mode,
            cooldown_value=r_cooldown_value,
            push_height_mode=r_push_height_mode,
            push_height_mm=r_push_height_mm,
            push_height_offset_mm=r_push_height_offset,
            bending_mode=r_bending,
            push_mode=r_push_mode,
            template=r_template,
            fans_during_cooldown=r_fans,
            reheat_between_loops=r_reheat,
            preheat_bed_temp=r_preheat_bed,
            preheat_nozzle_temp=r_preheat_nozzle,
            loop_count=r_loop,
            part_bounds_list=plate_part_bounds,
            cooldown_hold_seconds=r_cooldown_hold,
            part_xy_extents=plate_xy_extents
            or (
                (107.0, 143.0, 8.0, 175.0)
                if r_push_mode in ("part_center", "part_center_sweep")
                else None
            ),
        )
        gcode = inject_autoclear_into_plate_gcode(gcode, plate_block)
    if r_remove_purge:
        gcode = remove_purge_line_from_gcode(gcode)
    if r_loop > 1:
        gcode = wrap_plate_gcode_in_loops(
            gcode,
            r_loop,
            skip_retraction_between_loops=r_skip_retract,
            reheat_between_loops=r_reheat,
            preheat_bed_temp=r_preheat_bed,
            preheat_nozzle_temp=r_preheat_nozzle,
        )
    return gcode


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
    push_height_offset_mm, bending_mode ('nhdfarm'|'none'), loop_count, remove_purge_line.
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

    part_bounds_list = get_part_bounds_from_3mf(input_path) if push_mode in (
        "part_center",
        "part_center_sweep",
    ) else None

    def _first_plate_gcode_bytes(zf: zipfile.ZipFile) -> bytes | None:
        for info in zf.infolist():
            n = info.filename.replace("\\", "/")
            if "Metadata/plate_" in n and n.endswith(".gcode"):
                return zf.read(info.filename)
        return None

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
    elif push_mode in ("part_center", "part_center_sweep"):
        with zipfile.ZipFile(input_path, "r") as _zf:
            raw0 = _first_plate_gcode_bytes(_zf)
        g0 = raw0.decode("utf-8", errors="replace") if raw0 else ""
        ext0 = get_part_xy_extents_from_gcode(g0) if g0 else None
        if ext0 is None and part_bounds_list:
            cx, by = part_bounds_list[0]
            ext0 = (cx - 24.0, cx + 24.0, max(4.0, by - 48.0), by)
        mz0 = parse_max_z_from_plate_gcode(g0) if g0 else None
        if mz0 is None:
            mz0 = 10.0
        block = build_injection_block_expanded(
            max_layer_z=mz0,
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
            part_bounds_list=part_bounds_list,
            cooldown_hold_seconds=cooldown_hold_seconds,
            part_xy_extents=ext0 or (107.0, 143.0, 8.0, 175.0),
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
            part_bounds_list=part_bounds_list,
            cooldown_hold_seconds=cooldown_hold_seconds,
        )

    with zipfile.ZipFile(input_path, "r") as zf_in:
        all_files = {info.filename.replace("\\", "/"): zf_in.read(info.filename) for info in zf_in.infolist()}
        config_paths = find_config_files(zf_in)

    # Existing autoclear_settings from input (may have plate_settings for merged files)
    existing_autoclear: dict = {}
    if AUTOCLEAR_SETTINGS_PATH in all_files:
        try:
            existing_autoclear = json.loads(all_files[AUTOCLEAR_SETTINGS_PATH].decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    # Defaults from function params for get_plate_settings resolution
    param_defaults = {
        "cooldown_mode": cooldown_mode,
        "cooldown_value": cooldown_value,
        "cooldown_hold_seconds": cooldown_hold_seconds,
        "loop_count": loop_count,
        "remove_purge_line": remove_purge_line,
        "fans_during_cooldown": fans_during_cooldown,
        "skip_retraction_between_loops": skip_retraction_between_loops,
        "reheat_between_loops": reheat_between_loops,
        "preheat_bed_temp": preheat_bed_temp,
        "preheat_nozzle_temp": preheat_nozzle_temp,
        "push_height_mode": push_height_mode,
        "push_height_mm": push_height_mm,
        "push_height_offset_mm": push_height_offset_mm,
        "bending_mode": bending_mode,
        "push_mode": push_mode,
        "template": template or "",
    }
    if push_heights is not None and len(push_heights) > 0:
        param_defaults["push_heights"] = push_heights
        param_defaults["use_plate_flex"] = use_plate_flex if use_plate_flex is not None else False

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
    plate_match = re.compile(r"Metadata/plate_(\d+)\.gcode$").match
    skip_plate_processing = bool(existing_autoclear.get("chain_single_print"))

    for name in list(all_files.keys()):
        if "Metadata/plate_" in name and name.endswith(".gcode"):
            if skip_plate_processing:
                continue
            m = plate_match(name.replace("\\", "/"))
            plate_num = int(m.group(1)) if m else 1
            resolved = get_plate_settings(
                {**param_defaults, **existing_autoclear},
                plate_num,
                defaults=param_defaults,
            )
            # Resolved values (with type coercion for injector)
            r_loop = max(1, min(999, int(resolved.get("loop_count", 1))))
            r_cooldown_mode = str(resolved.get("cooldown_mode", "temp"))
            r_cooldown_value = float(resolved.get("cooldown_value", 35))
            r_cooldown_hold = float(resolved.get("cooldown_hold_seconds", 60))
            r_remove_purge = bool(resolved.get("remove_purge_line", False))
            r_fans = bool(resolved.get("fans_during_cooldown", False))
            r_skip_retract = bool(resolved.get("skip_retraction_between_loops", True))
            r_reheat = bool(resolved.get("reheat_between_loops", False))
            r_preheat_bed = max(0, min(150, int(resolved.get("preheat_bed_temp", 70))))
            r_preheat_nozzle = max(0, min(300, int(resolved.get("preheat_nozzle_temp", 150))))
            r_push_mode = str(resolved.get("push_mode", "center_and_sweep"))
            r_push_height_mode = str(resolved.get("push_height_mode", "auto"))
            r_push_height_mm = float(resolved.get("push_height_mm", 5.0))
            r_push_height_offset = int(resolved.get("push_height_offset_mm", 20))
            r_bending = str(resolved.get("bending_mode", "nhdfarm"))
            r_template = resolved.get("template") or None
            r_push_heights = resolved.get("push_heights")
            r_use_plate_flex = resolved.get("use_plate_flex")

            raw = all_files[name]
            try:
                gcode = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            # Inject auto-clear into plate gcode (same as Auto-Clear) - before purge/loops
            if r_push_heights is None or len(r_push_heights) == 0:
                max_z = parse_max_z_from_plate_gcode(gcode)
                if max_z is not None:
                    # Part center: prefer gcode-based bounds per plate (correct for merged
                    # multi-plate where each plate has different part positions)
                    plate_part_bounds = None
                    if r_push_mode in ("part_center", "part_center_sweep"):
                        plate_part_bounds = get_part_bounds_from_gcode(gcode)
                    if plate_part_bounds is None:
                        plate_part_bounds = part_bounds_list
                    plate_xy_extents = None
                    if r_push_mode in ("part_center", "part_center_sweep"):
                        plate_xy_extents = get_part_xy_extents_from_gcode(gcode)
                        if plate_xy_extents is None and plate_part_bounds:
                            cx, by = plate_part_bounds[0]
                            plate_xy_extents = (
                                cx - 24.0,
                                cx + 24.0,
                                max(4.0, by - 48.0),
                                by,
                            )
                        elif plate_xy_extents is None and part_bounds_list:
                            cx, by = part_bounds_list[0]
                            plate_xy_extents = (
                                cx - 24.0,
                                cx + 24.0,
                                max(4.0, by - 48.0),
                                by,
                            )
                    plate_block = build_injection_block_expanded(
                        max_layer_z=max_z,
                        cooldown_mode=r_cooldown_mode,
                        cooldown_value=r_cooldown_value,
                        push_height_mode=r_push_height_mode,
                        push_height_mm=r_push_height_mm,
                        push_height_offset_mm=r_push_height_offset,
                        bending_mode=r_bending,
                        push_mode=r_push_mode,
                        template=r_template,
                        fans_during_cooldown=r_fans,
                        reheat_between_loops=r_reheat,
                        preheat_bed_temp=r_preheat_bed,
                        preheat_nozzle_temp=r_preheat_nozzle,
                        loop_count=r_loop,
                        part_bounds_list=plate_part_bounds,
                        cooldown_hold_seconds=r_cooldown_hold,
                        part_xy_extents=plate_xy_extents
                        or (
                            (107.0, 143.0, 8.0, 175.0)
                            if r_push_mode in ("part_center", "part_center_sweep")
                            else None
                        ),
                    )
                    gcode = inject_autoclear_into_plate_gcode(gcode, plate_block)
            if r_remove_purge:
                gcode = remove_purge_line_from_gcode(gcode)
            if r_loop > 1:
                gcode = wrap_plate_gcode_in_loops(
                    gcode,
                    r_loop,
                    skip_retraction_between_loops=r_skip_retract,
                    reheat_between_loops=r_reheat,
                    preheat_bed_temp=r_preheat_bed,
                    preheat_nozzle_temp=r_preheat_nozzle,
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
    if existing_autoclear.get("plate_settings"):
        autoclear_settings["plate_settings"] = existing_autoclear["plate_settings"]
    if existing_autoclear.get("chain_single_print"):
        autoclear_settings["chain_single_print"] = True
        autoclear_settings["chain_jobs"] = existing_autoclear.get("chain_jobs", 0)
    all_files[AUTOCLEAR_SETTINGS_PATH] = json.dumps(
        autoclear_settings, indent=2
    ).encode("utf-8")

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf_out:
        for name, data in all_files.items():
            zf_out.writestr(name, data)

    return str(output_path)
