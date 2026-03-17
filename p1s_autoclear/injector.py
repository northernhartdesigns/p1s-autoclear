"""
G-code injection logic for NHDFARM-style auto-clear sequence.
Injects cooldown (time or temp) and pusher sweeps into machine_end_gcode.
"""

import re

from .config_discovery import ANCHOR_BEFORE_M17, FALLBACK_ANCHOR

# Minimum sweep Z above bed (mm). Prevents toolhead/nozzle from digging into bed.
MIN_SWEEP_Z_MM = 5.0


def build_cooldown_line(mode: str, value: float, temp_hold_seconds: float = 60) -> str:
    """Build cooldown G-code lines.
    mode 'time': outputs G4 P{ms} (value in seconds).
    mode 'temp': outputs M190 S{temp} (wait for bed to cool to value °C; S used per FarmLoop/Bambu compatibility), then G4 for temp_hold_seconds.
    Returns empty string for unknown mode.
    """
    if mode == "time":
        # value in seconds
        ms = int(value * 1000)
        return f"G4 P{ms} ; cooldown {value}s"
    if mode == "temp":
        # M190 S: Bambu-compatible; R may not be supported. FarmLoop uses M190 S for cooldown.
        lines = [f"M190 S{int(value)} ; wait for bed to cool to {int(value)}C"]
        if temp_hold_seconds > 0:
            lines.append(f"G4 P{int(temp_hold_seconds * 1000)} ; wait {int(temp_hold_seconds)}s before sweep")
        return "\n".join(lines)
    return ""


def build_bending_block(mode: str) -> str:
    """Build bending motion block that runs before pusher sweeps.
    Flexes the plate to help break adhesion before sweeping.
    mode 'nhdfarm': Z235↔Z200 repeated 6 times.
    CAUTION: Z235 is near Bambu's 250 mm printable limit; debris or dust caps
    can cause roof collision. See Bambu Lab wiki on print volume limitations.
    mode 'z_pop': reduce Z current, lift near part top, drop (Bambu-style).
    mode 'none' or other: returns empty string.
    """
    if mode == "nhdfarm":
        # Z235 is near Bambu's 250mm default limit. See roof collision warning in Help.
        # Ref: https://wiki.bambulab.com/en/knowledge-sharing/print-volume-limitations
        lines = ["; Bending motion (NHDFARM: Z flex to break adhesion)"]
        for i in range(6):
            lines.append("G1 Z235 F12000")
            lines.append("G1 Z200 F12000")
        return "\n".join(lines)
    if mode == "z_pop":
        return "\n".join([
            "; Plate flex (Z pop: helps break adhesion before sweep)",
            "M17 Z0.4 ; reduce Z holding current",
            "G1 Z{max_layer_z + 5} F600",
            "G1 Z{max_layer_z + 3}",
            "M400",
            "M17 R ; restore Z current",
        ])
    return ""


# Bed center fallback when mesh unavailable for bump mode
BED_CENTER_X = 125.0
BED_BACK_Y = 250.0


def build_bump_block(
    center_x: float,
    back_y: float,
    z_expr: str,
    y_front: float = 0,
    y_back: float = 250,
    speed_push: int = 3000,
    speed_sweep: int = 3000,
    speed_rake_fast: int = 12000,
    x_positions: tuple[float, ...] = (220, 190, 160, 130, 100, 70, 30),
    fans_off_after_first_push: str = "",
) -> str:
    """
    Targeted bump at part center/back, then rake passes (same as center_and_sweep).
    Positions at (center_x, back_y), one bump forward, then right-to-left rake (2 passes).
    When fans_off_after_first_push is set, inserts it after the first bump (maximize cooldown).
    """

    def rake_pass(speed: int) -> list[str]:
        out = []
        for x in x_positions:
            out.append(f"G1 Y{y_back} F{speed}")
            out.append(f"G1 X{x} F{speed}")
            out.append(f"G1 Y{y_front} F{speed}")
        return out

    lines = [
        "; -------- choose sweep height ----------",
        f"G1 Z{z_expr} F10000",
        "M400",
        "",
        "; -------- targeted bump at part center/back --------",
        f"G1 X{center_x:.1f} Y{back_y:.1f} F{speed_push}",
        f"G1 Y{y_front} F{speed_push}",
        "",
    ]
    if fans_off_after_first_push:
        lines.append(fans_off_after_first_push)
        lines.append("")
    lines.extend([
        "; -------- extended right-to-left rake (pass 1) --------",
        *rake_pass(speed_sweep),
        "",
        "; ---------- choose sweep height ----------",
        f"G1 Y{y_back} F{speed_sweep}",
        "M400",
        f"G1 X{x_positions[0]} F{speed_sweep}",
        "M400",
        "",
        f"G1 Z{z_expr} F12000",
        "M400",
        "",
        "; -------- extended right-to-left rake (pass 2) --------",
        *rake_pass(speed_rake_fast),
    ])
    return "\n".join(lines)


def build_nhdfarm_sweep_block(  # noqa: D213
    push_height_mode: str = "auto",
    push_height_mm: float = 5.0,
    push_height_offset_mm: float = 20,
    push_mode: str = "center_and_sweep",
    y_back: float = 250,
    y_front: float = 0,
    speed_sweep: int = 3000,
    speed_rake_fast: int = 12000,
    x_positions: tuple[float, ...] = (220, 190, 160, 130, 100, 70, 30),
    x_central: float = 125,
    fans_off_after_first_push: str = "",
) -> str:
    """
    NHDFARM-style sweep (mirror Auto-Clear): central sweeps + optional double rake pass.
    push_mode 'center_only': two pushes at center X only (no rake).
    push_mode 'center_and_sweep': central sweeps + rake pass 1 at F3000, rake pass 2 at F12000.
    """
    if push_height_mode == "auto":
        offset = max(1, min(249, int(push_height_offset_mm)))  # Placeholder: max_layer_z unknown
        z_expr = f"{{max(1, max_layer_z - {offset})}}"  # Min 1 mm to avoid bed contact
    else:
        z_expr = str(max(1.0, push_height_mm))
    z_line_first = f"G1 Z{z_expr} F10000"
    z_line_second = f"G1 Z{z_expr} F12000"

    def rake_pass(speed: int) -> list[str]:
        out = []
        for x in x_positions:
            out.append(f"G1 Y{y_back} F{speed}")
            out.append(f"G1 X{x} F{speed}")
            out.append(f"G1 Y{y_front} F{speed}")
        return out

    lines = [
        "; -------- choose sweep height ----------",
        z_line_first,
        "M400",
        "",
        "; -------- central sweeps (2) --------",
        f"G1 X{x_central} F{speed_sweep}",
        f"G1 Y{y_back} F{speed_sweep}",
        f"G1 Y{y_front} F{speed_sweep}",
        f"G1 Y{y_back} F{speed_sweep}",
        f"G1 Y{y_front} F{speed_sweep}",
        "",
    ]
    if fans_off_after_first_push:
        lines.append(fans_off_after_first_push)
        lines.append("")

    if push_mode == "center_and_sweep":
        lines.extend([
            "; -------- extended right-to-left rake (pass 1) --------",
            *rake_pass(speed_sweep),
            "",
            "; ---------- choose sweep height ----------",
            f"G1 Y{y_back} F{speed_sweep}",
            "M400",
            f"G1 X{x_positions[0]} F{speed_sweep}",
            "M400",
            "",
            z_line_second,
            "M400",
            "",
            "; -------- extended right-to-left rake (pass 2) --------",
            *rake_pass(speed_rake_fast),
        ])

    return "\n".join(lines)


def build_nhdfarm_sweep_block_expanded(
    max_layer_z: float,
    push_height_mode: str = "auto",
    push_height_mm: float = 5.0,
    push_height_offset_mm: float = 20,
    **kwargs,
) -> str:
    """
    NHDFARM sweep block with concrete Z values (for plate gcode injection).
    max_layer_z from plate header; no Bambu placeholders.
    """
    if push_height_mode == "auto":
        offset = max(5, min(50, int(push_height_offset_mm)))
        z_val = max(0.0, max_layer_z - offset)  # Allow <5mm (user warned); 0 prevents negative Z
    else:
        z_val = push_height_mm
    z_str = f"{z_val:.2f}"
    return build_nhdfarm_sweep_block(
        push_height_mode="manual",
        push_height_mm=float(z_val),
        push_height_offset_mm=int(push_height_offset_mm),
        **kwargs,
    )


def build_injection_block_expanded(
    max_layer_z: float,
    cooldown_mode: str,
    cooldown_value: float,
    push_height_mode: str = "auto",
    push_height_mm: float = 5.0,
    push_height_offset_mm: int = 20,
    bending_mode: str = "nhdfarm",
    push_mode: str = "center_and_sweep",
    template: str | None = None,
    fans_during_cooldown: bool = False,
    reheat_between_loops: bool = False,
    preheat_bed_temp: int = 70,
    preheat_nozzle_temp: int = 150,
    loop_count: int = 1,
    *,
    cooldown_hold_seconds: float = 60,
    part_bounds: tuple[float, float] | None = None,
) -> str:
    """
    Build auto-clear block with concrete values for injection into plate gcode.
    Same as build_injection_block but sweep Z is expanded (no placeholders).
    When push_mode is 'bump', uses part_bounds (center_x, back_y) from mesh; falls back to bed center if None.
    """
    cooldown_line = build_cooldown_line(cooldown_mode, cooldown_value, temp_hold_seconds=cooldown_hold_seconds)
    if fans_during_cooldown:
        cooldown_line = (
            "M106 S255 ; part cooling 100%\n"
            "M106 P2 S255 ; auxiliary part cooling 100%\n"
            "M106 P3 S255 ; chamber fan 100%\n"
            + cooldown_line
        )
    bending = build_bending_block(bending_mode)
    if push_mode == "bump":
        cx, cy = part_bounds or (BED_CENTER_X, BED_BACK_Y)
        if push_height_mode == "auto":
            offset = max(5, min(50, int(push_height_offset_mm)))
            z_val = max(0.0, max_layer_z - offset)
        else:
            z_val = max(1.0, push_height_mm)
        fans_off_blk = (
            "M107 ; part cooling off\nM107 P2 ; aux off\nM107 P3 ; chamber off"
            if fans_during_cooldown
            else ""
        )
        sweeps = build_bump_block(
            center_x=cx, back_y=cy, z_expr=f"{z_val:.2f}",
            fans_off_after_first_push=fans_off_blk,
        )
    else:
        fans_off_blk = (
            "M107 ; part cooling off\nM107 P2 ; aux off\nM107 P3 ; chamber off"
            if fans_during_cooldown
            else ""
        )
        sweeps = build_nhdfarm_sweep_block_expanded(
            max_layer_z=max_layer_z,
            push_height_mode=push_height_mode,
            push_height_mm=push_height_mm,
            push_height_offset_mm=push_height_offset_mm,
            push_mode=push_mode,
            fans_off_after_first_push=fans_off_blk,
        )
    template = template or DEFAULT_TEMPLATE
    if "{bending}" not in template and "{sweeps}" in template:
        template = template.replace("\n\n{sweeps}", "\n\n{bending}\n\n{sweeps}")
    if "{fans_off}" not in template:
        template = template.replace("; --- End Auto-Clear ---", "{fans_off}\n; --- End Auto-Clear ---")
    if "{heaters_off}" not in template and "{fans_off}" in template:
        template = template.replace("{fans_off}", "{heaters_off}\n{fans_off}")
    # Backward compat: inject fans_off_before_sweep and preheat if missing
    if "{fans_off_before_sweep}" not in template or "{preheat}" not in template:
        template = template.replace(
            "\n\n{sweeps}",
            "\n\n{fans_off_before_sweep}\n{preheat}\n\n{sweeps}",
        )
    required = ["{cooldown}", "{bending}", "{sweeps}"]
    missing = [p for p in required if p not in template]
    if missing:
        raise ValueError(f"Template missing: {', '.join(missing)}")
    # Fans off is now embedded in sweeps (after first push) when fans_during_cooldown; keep template placeholder empty
    fans_off_before_sweep = ""
    # Preheat at sweep start: when reheat_between_loops and loop_count > 1, start heating during push
    preheat = ""
    if reheat_between_loops and loop_count > 1:
        preheat = (
            f"M140 S{preheat_bed_temp} ; preheat bed for next print\n"
            f"M104 S{preheat_nozzle_temp} ; preheat nozzle for next print"
        )
    heaters_off = "M140 S0 ; bed off\nM104 S0 ; nozzle off"
    fans_off = (
        "M107 ; part cooling off\nM107 P2 ; aux off\nM107 P3 ; chamber off"
        if fans_during_cooldown
        else ""
    )
    return template.strip().format(
        cooldown=cooldown_line,
        bending=bending,
        sweeps=sweeps,
        fans_off_before_sweep=fans_off_before_sweep,
        preheat=preheat,
        heaters_off=heaters_off,
        fans_off=fans_off,
    )


def parse_max_z_from_plate_gcode(gcode: str) -> float | None:
    """
    Parse max_z_height from Bambu plate gcode header.
    Format: ; max_z_height: 5.20
    Returns None if not found.
    """
    import re
    m = re.search(r";\s*max_z_height\s*:\s*([\d.]+)", gcode, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def parse_temps_from_plate_gcode(gcode: str) -> tuple[int | None, int | None]:
    """
    Parse bed and nozzle target temps from plate gcode (M140/M190, M104/M109).
    Also checks slicer comment metadata (Bambu/PrusaSlicer: first_layer_bed_temperature, etc.).
    Returns (bed_temp, nozzle_temp). Excludes M104 S0 (hotend off).
    Uses the largest value found (ensures reheat covers highest temp needed).
    """

    def _parse_int(s: str) -> int:
        return int(float(s))

    bed_candidates: list[int] = []
    nozzle_candidates: list[int] = []

    # Bed: M140 S{value} or M190 S{value} (allow optional P param, decimals)
    for m in re.finditer(r"M140(?:\s+P\d+)?\s*S([\d.]+)", gcode, re.IGNORECASE):
        t = _parse_int(m.group(1))
        if t > 0:  # skip M140 S0 (cool plate)
            bed_candidates.append(t)
    for m in re.finditer(r"M190(?:\s+P\d+)?\s*S([\d.]+)", gcode, re.IGNORECASE):
        t = _parse_int(m.group(1))
        if t > 0:
            bed_candidates.append(t)

    # Fallback: Bambu/PrusaSlicer comment metadata (e.g. ; first_layer_bed_temperature = 60)
    for pattern in (
        r";\s*first_layer_bed_temperature\s*[=:]\s*([\d.]+)",
        r";\s*bed_temperature_initial_layer_single\s*[=:]\s*([\d.]+)",
        r";\s*bed_temperature_initial_layer\s*[=:]\s*([\d.]+)",
        r";\s*bed_temperature\s*[=:]\s*([\d.]+)",
    ):
        for m in re.finditer(pattern, gcode, re.IGNORECASE):
            t = _parse_int(m.group(1))
            if t > 0:
                bed_candidates.append(t)

    # Nozzle: M104 S{value} or M109 S{value}, exclude M104 S0
    for m in re.finditer(r"M104(?:\s+P\d+)?\s*S([\d.]+)", gcode, re.IGNORECASE):
        t = _parse_int(m.group(1))
        if t > 0:
            nozzle_candidates.append(t)
    for m in re.finditer(r"M109(?:\s+P\d+)?\s*S([\d.]+)", gcode, re.IGNORECASE):
        t = _parse_int(m.group(1))
        if t > 0:
            nozzle_candidates.append(t)

    bed_temp = max(bed_candidates) if bed_candidates else None
    nozzle_temp = max(nozzle_candidates) if nozzle_candidates else None
    return (bed_temp, nozzle_temp)


_EXECUTABLE_END_MARKER = "; MACHINE_END_GCODE_START"


def inject_autoclear_into_plate_gcode(
    plate_gcode: str,
    block: str,
) -> str:
    """
    Inject auto-clear block into plate gcode (same as Auto-Clear).
    Inserts before each M17 S in the executable section only (not CONFIG_BLOCK).
    Handles looped plates. Skips if already present.
    """
    if "; --- Auto-Clear Bed Sequence" in plate_gcode or "; --- End Auto-Clear ---" in plate_gcode:
        return plate_gcode
    if ";Cooldown Start" in plate_gcode and ";Cooldown End" in plate_gcode:
        return plate_gcode  # Auto-Clear-style already present
    anchor = ANCHOR_BEFORE_M17
    insertion = "\n" + block + "\n"
    result = plate_gcode
    # Only inject before M17 S that appears after ; MACHINE_END_GCODE_START (executable section)
    search_start = 0
    while True:
        block_start = result.find(_EXECUTABLE_END_MARKER, search_start)
        if block_start < 0:
            break
        idx = result.find(anchor, block_start + len(_EXECUTABLE_END_MARKER))
        if idx < 0:
            search_start = block_start + 1
            continue
        result = result[:idx] + insertion + result[idx:]
        search_start = idx + len(insertion)
    return result


def build_sweep_block(
    push_heights: list[float],
    x_pos: float = 128,
    y_back: float = 220,
    y_front: float = 0,
    speed_travel: int = 6000,
    speed_z: int = 3000,
    speed_push: int = 1500,
) -> str:
    """
    Legacy: Build push sweep G-code block (multiple heights at single X).
    Kept for backward compatibility with old templates.
    """
    lines = []
    for i, h in enumerate(push_heights):
        if h <= 1.0:
            z_expr = f"{{max_layer_z * {h}}}"
            label = f" ({int(h*100)}%)"
        else:
            z_expr = str(h)
            label = f" ({h}mm)"
        lines.append(
            f"; Sweep {i + 1}{label}\n"
            f"G1 X{x_pos} Y{y_back} F{speed_travel}\n"
            f"G1 Z{z_expr} F{speed_z}\n"
            f"G1 Y{y_front} F{speed_push}"
        )
    return "\n\n".join(lines)


DEFAULT_TEMPLATE = """
; --- Auto-Clear Bed Sequence (NHDFARM-style) ---
M400
G90
{cooldown}

{bending}

{fans_off_before_sweep}
{preheat}

{sweeps}

; End section (match Auto-Clear): safe corner XY park, heaters off, then M17 S
G1 X65 Y245 F12000     ; move to safe corner before parking
G1 Y265 F3000          ; final park at rear edge (idle position)

M400
{heaters_off}
{fans_off}
; --- End Auto-Clear ---
"""


def build_injection_block(
    cooldown_mode: str,
    cooldown_value: float,
    push_height_mode: str = "auto",
    push_height_mm: float = 5.0,
    push_height_offset_mm: int = 20,
    bending_mode: str = "nhdfarm",
    push_mode: str = "center_and_sweep",
    template: str | None = None,
    fans_during_cooldown: bool = False,
    reheat_between_loops: bool = False,
    preheat_bed_temp: int = 70,
    preheat_nozzle_temp: int = 150,
    loop_count: int = 1,
    *,
    cooldown_hold_seconds: float = 60,
    push_heights: list[float] | None = None,
    use_plate_flex: bool | None = None,
    part_bounds: tuple[float, float] | None = None,
) -> str:
    """
    Build the full injection block from config.
    New params: push_height_mode, push_height_mm, bending_mode.
    Legacy params (push_heights, use_plate_flex) used for backward compat when new absent.
    cooldown_hold_seconds: extra seconds to wait after bed reaches target temp (temp mode only).
    """
    cooldown_line = build_cooldown_line(
        cooldown_mode, cooldown_value, temp_hold_seconds=cooldown_hold_seconds
    )
    if fans_during_cooldown:
        cooldown_line = (
            "M106 S255 ; part cooling 100%\n"
            "M106 P2 S255 ; auxiliary part cooling 100%\n"
            "M106 P3 S255 ; chamber fan 100%\n"
            + cooldown_line
        )
    # Legacy: use_plate_flex or push_heights list
    if push_heights is not None and len(push_heights) > 0:
        bending = build_bending_block("z_pop") if use_plate_flex else ""
        sweeps = build_sweep_block(
            push_heights,
            x_pos=128,
            y_back=220,
            y_front=0,
            speed_travel=6000,
            speed_z=3000,
            speed_push=1500,
        )
    else:
        bending = build_bending_block(bending_mode)
        if push_mode == "bump":
            cx, cy = part_bounds or (BED_CENTER_X, BED_BACK_Y)
            if push_height_mode == "auto":
                offset = max(1, min(249, int(push_height_offset_mm)))
                z_expr = f"{{max(1, max_layer_z - {offset})}}"
            else:
                z_expr = str(max(1.0, push_height_mm))
            fans_off_blk = (
                "M107 ; part cooling off\nM107 P2 ; aux off\nM107 P3 ; chamber off"
                if fans_during_cooldown else ""
            )
            sweeps = build_bump_block(
                center_x=cx, back_y=cy, z_expr=z_expr,
                fans_off_after_first_push=fans_off_blk,
            )
        else:
            fans_off_blk = (
                "M107 ; part cooling off\nM107 P2 ; aux off\nM107 P3 ; chamber off"
                if fans_during_cooldown else ""
            )
            sweeps = build_nhdfarm_sweep_block(
                push_height_mode=push_height_mode,
                push_height_mm=push_height_mm,
                push_height_offset_mm=push_height_offset_mm,
                push_mode=push_mode,
                fans_off_after_first_push=fans_off_blk,
            )
    template = template or DEFAULT_TEMPLATE
    # Backward compat: old templates use {plate_flex}, new use {bending}
    if "{bending}" not in template and "{plate_flex}" in template:
        template = template.replace("{plate_flex}", "{bending}")
    if "{bending}" not in template and "{sweeps}" in template:
        template = template.replace("\n\n{sweeps}", "\n\n{bending}\n\n{sweeps}")
    if "{fans_off}" not in template:
        template = template.replace("; --- End Auto-Clear ---", "{fans_off}\n; --- End Auto-Clear ---")
    if "{heaters_off}" not in template and "{fans_off}" in template:
        template = template.replace("{fans_off}", "{heaters_off}\n{fans_off}")
    # Backward compat: inject fans_off_before_sweep and preheat if missing
    if "{fans_off_before_sweep}" not in template or "{preheat}" not in template:
        template = template.replace(
            "\n\n{sweeps}",
            "\n\n{fans_off_before_sweep}\n{preheat}\n\n{sweeps}",
        )
    required = ["{cooldown}", "{bending}", "{sweeps}"]
    missing = [p for p in required if p not in template]
    if missing:
        raise ValueError(f"G-code template missing required placeholder(s): {', '.join(missing)}")
    # Fans off now embedded in sweeps (after first push) when fans_during_cooldown.
    fans_off_before_sweep = ""
    # Preheat at sweep start: when reheat_between_loops and loop_count > 1, start heating during push
    preheat = ""
    if reheat_between_loops and loop_count > 1:
        preheat = (
            f"M140 S{preheat_bed_temp} ; preheat bed for next print\n"
            f"M104 S{preheat_nozzle_temp} ; preheat nozzle for next print"
        )
    heaters_off = "M140 S0 ; bed off\nM104 S0 ; nozzle off"
    fans_off = (
        "M107 ; part cooling off\nM107 P2 ; aux off\nM107 P3 ; chamber off"
        if fans_during_cooldown
        else ""
    )
    fmt = {
        "cooldown": cooldown_line,
        "bending": bending,
        "sweeps": sweeps,
        "fans_off_before_sweep": fans_off_before_sweep,
        "preheat": preheat,
        "heaters_off": heaters_off,
        "fans_off": fans_off,
    }
    return template.strip().format(**fmt)


def find_insertion_index(end_gcode: str, anchor: str | None = None) -> int:
    """
    Find the index to insert our block (right before anchor).
    Default: insert before M17 S (after the M400 before it).
    Fallback: after M104 S0.
    """
    anchor = anchor or ANCHOR_BEFORE_M17
    idx = end_gcode.find(anchor)
    if idx >= 0:
        # Insert right before the anchor (at start of that line)
        return idx
    idx = end_gcode.find(FALLBACK_ANCHOR)
    if idx >= 0:
        # Insert after the line containing M104 S0
        end_of_line = end_gcode.find("\n", idx)
        if end_of_line >= 0:
            return end_of_line + 1
        return len(end_gcode)
    # Last resort: append at end
    return len(end_gcode)


def inject_block(end_gcode: str, block: str, anchor: str | None = None) -> str:
    """Inject the auto-clear block into machine_end_gcode."""
    # Avoid double-injection
    if "; --- Auto-Clear Bed Sequence" in end_gcode or "; --- End Auto-Clear ---" in end_gcode:
        return end_gcode
    idx = find_insertion_index(end_gcode, anchor)
    insertion = "\n" + block + "\n"
    return end_gcode[:idx] + insertion + end_gcode[idx:]


def remove_retraction_from_end_section(gcode: str) -> str:
    """
    Remove filament retraction commands from the executable end section only.
    Used to keep filament loaded between loops (retract only after last loop).
    Targets:
    - G10 (firmware retract) and G1 E-[value] (direct retract)
    - Bambu AMS unload block: ; pull back filament to AMS ... M620 ... M621
    Only modifies the section after ; MACHINE_END_GCODE_START to avoid touching layer retractions.
    """
    if not gcode or _EXECUTABLE_END_MARKER not in gcode:
        return gcode
    has_cr = "\r\n" in gcode or gcode.endswith("\r")
    lines = gcode.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sep = "\r\n" if has_cr else "\n"
    out: list[str] = []
    in_end_section = False
    skip_ams_block = False
    max_ams_block_lines = 25  # Safeguard: typical block is ~10 lines
    ams_block_line_count = 0
    for line in lines:
        if _EXECUTABLE_END_MARKER in line:
            in_end_section = True
            skip_ams_block = False
            out.append(line)
            continue
        if not in_end_section:
            out.append(line)
            continue
        stripped = line.strip()
        # Skip Bambu AMS unload block (M620 ... M621) - keeps filament loaded
        if skip_ams_block:
            ams_block_line_count += 1
            if ams_block_line_count > max_ams_block_lines:
                skip_ams_block = False
                out.append(line)
            elif re.search(r"M621\b", stripped, re.IGNORECASE):
                skip_ams_block = False
                continue  # Skip M621 line too
            else:
                continue
        if not stripped:
            out.append(line)
            continue
        # Start of AMS unload block: comment or M620
        if (
            "pull back filament" in stripped.lower()
            or re.match(r"^\s*M620\b", stripped, re.IGNORECASE)
        ):
            skip_ams_block = True
            ams_block_line_count = 0
            continue
        # Remove G10 (firmware retract)
        if re.match(r"^\s*G10\b", stripped, re.IGNORECASE):
            continue
        # Remove G1 E-negative (direct retract); matches G1 E-0.8 F1800, G1E-18F200, etc.
        if re.search(r"G1\s*E-\d*\.?\d+", stripped, re.IGNORECASE):
            continue
        out.append(line)
    return sep.join(out)


def replace_m104_s0_in_end_section(gcode: str, temp: int) -> str:
    """
    Replace M104 S0 (turn off hotend) with M104 S{temp} in the executable end section only.
    Used when reheat_between_loops: keeps nozzle at preheat temp between loops instead of
    cooling to 0, so next print starts faster. Only modifies section after MACHINE_END_GCODE_START.
    """
    if not gcode or _EXECUTABLE_END_MARKER not in gcode:
        return gcode
    has_cr = "\r\n" in gcode or gcode.endswith("\r")
    lines = gcode.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sep = "\r\n" if has_cr else "\n"
    out: list[str] = []
    in_end_section = False
    # Match M104 S0 (with optional P param, optional space before S)
    m104_s0_pattern = re.compile(r"M104(?:\s+P\d+)?\s+S\s*0\b", re.IGNORECASE)
    replacement = f"M104 S{temp} ; hold nozzle at preheat temp (no cool between loops)"
    for line in lines:
        if _EXECUTABLE_END_MARKER in line:
            in_end_section = True
            out.append(line)
            continue
        if not in_end_section:
            out.append(line)
            continue
        # Only replace M104 S0 in end section (exact line match for hotend-off command)
        if m104_s0_pattern.search(line) and re.search(r"\bS\s*0\b", line, re.IGNORECASE):
            out.append(replacement)
            continue
        out.append(line)
    return sep.join(out)


def wrap_plate_gcode_in_loops(
    gcode: str,
    loop_count: int,
    pause_seconds: float = 2.0,
    skip_retraction_between_loops: bool = True,
    reheat_between_loops: bool = False,
    preheat_bed_temp: int = 70,
    preheat_nozzle_temp: int = 150,
) -> str:
    """
    Wrap plate G-code in Auto-Clear-style loop structure.
    Duplicates the full plate content loop_count times with headers.
    If gcode already contains "; === LOOP " markers, returns unchanged.
    pause_seconds: G4 delay between loops (default 2s, matches Auto-Clear).
    skip_retraction_between_loops: When True and loop_count > 1, removes retraction from loops 1..N-1
        so filament stays loaded between loops; loop N keeps retraction for final idle.
    reheat_between_loops: When True, add M140/M104 in "Preparing for next loop" to preheat for the
        next print. Not added after the last loop (no next print).
    Bed leveling is left to Bambu (runs every loop per slicer config).
    """
    if loop_count <= 1:
        return gcode
    if "; === LOOP " in gcode:
        return gcode
    has_cr = "\r\n" in gcode or gcode.endswith("\r")
    sep = "\r\n" if has_cr else "\n"
    body = gcode.strip()
    if not body:
        return gcode
    body_no_retract = remove_retraction_from_end_section(body) if skip_retraction_between_loops else body
    # When reheat enabled: replace M104 S0 with preheat temp in loops 1..N-1 so nozzle
    # stays warm instead of cooling to 0 between prints (only last loop turns off).
    body_between_loops = body_no_retract
    if reheat_between_loops:
        body_between_loops = replace_m104_s0_in_end_section(body_no_retract, preheat_nozzle_temp)
    # Purge only on loop 1 when same filament; skip purge on loops 2..N (saves time/filament).
    body_no_purge_between = remove_purge_line_from_gcode(body_between_loops)
    body_no_purge_last = remove_purge_line_from_gcode(body)
    # When skip retraction: filament stays loaded; loops 2..N must not run AMS load (flush/poop).
    if skip_retraction_between_loops:
        body_no_purge_between = remove_ams_load_from_gcode(body_no_purge_between)
        body_no_purge_last = remove_ams_load_from_gcode(body_no_purge_last)
    parts: list[str] = []
    for i in range(1, loop_count + 1):
        parts.append(f"; === LOOP {i} OF {loop_count} ===")
        # Loop 2+: restore Z holding current before G28 (M17 Z0.4 from previous loop
        # reduces it and can cause "z axis homing failed" on subsequent loops)
        if i > 1:
            parts.append("M17 R ; restore Z current before homing")
        # Loop 1: purge (prime), no retract, M104 replaced if reheat. Loops 2..N-1: no purge, no retract, M104 replaced if reheat.
        # Last loop: no purge, keep retraction and M104 S0.
        if i == 1:
            parts.append(body_between_loops)
        elif i < loop_count:
            parts.append(body_no_purge_between)
        else:
            parts.append(body_no_purge_last)
        parts.append(f"; === END OF LOOP {i} ===")
        if i < loop_count:
            parts.append("")
            parts.append("; Preparing for next loop...")
            if reheat_between_loops:
                parts.append(f"M140 S{preheat_bed_temp} ; preheat bed for next loop")
                parts.append(f"M104 S{preheat_nozzle_temp} ; preheat nozzle for next loop")
            parts.append(f"G4 S{int(pause_seconds)} ; brief pause between loops")
            parts.append("")
    return sep.join(parts)


def remove_purge_line_from_start_gcode(start_gcode: str) -> str:
    """
    Remove the nozzle load/purge line block from machine_start_gcode.
    Bambu uses: ;===== nozzle load line ===== ... until next ;===== section.
    """
    return _remove_purge_block(start_gcode, replace_instead_of_remove=False)


def remove_purge_line_from_gcode(gcode: str) -> str:
    """
    Remove the nozzle load/purge line block from raw G-code (e.g. Metadata/plate_*.gcode).
    Bambu uses: ;===== nozzle load line ===== ... until next ;===== section.
    Used for sliced .gcode.3mf files where the purge is baked into the plate G-code.
    Replaces the block with a minimal M400 (sync) to preserve structure for Bambu's parser.
    """
    return _remove_purge_block(gcode, replace_instead_of_remove=True)


def _remove_ams_load_block(text: str) -> str:
    """Remove Bambu AMS load block (prepare print temperature and material).
    Replaces with section header + M400 to preserve structure.
    """
    if not text or "prepare print temperature" not in text.lower() or "material" not in text.lower():
        return text
    has_cr = "\r\n" in text or text.endswith("\r")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sep = "\r\n" if has_cr else "\n"
    out: list[str] = []
    in_block = False
    block_line_count = 0
    max_block_lines = 80  # AMS block is ~50-60 lines
    for line in lines:
        lower = line.strip().lower()
        # Start: prepare print temperature and material (but not "end")
        if "prepare print temperature" in lower and "material" in lower and "=====" in lower and "end" not in lower:
            in_block = True
            block_line_count = 0
            out.append(line)
            out.append("; (AMS load removed by P1S Auto-Clear - filament already loaded)")
            out.append("M400")
            continue
        if in_block:
            block_line_count += 1
            if block_line_count > max_block_lines:
                in_block = False
                out.append(line)
                continue
            # End: prepare print temperature and material end
            if "prepare print temperature" in lower and "material" in lower and "end" in lower:
                in_block = False
                out.append(line)
            continue
        out.append(line)
    result = sep.join(out)
    if len(result) < len(text) * 0.5:
        return text
    return result


def remove_ams_load_from_gcode(gcode: str) -> str:
    """
    Remove the Bambu AMS load block from plate G-code start section.
    Targets: ;===== prepare print temperature and material ========== ...
             ;===== prepare print temperature and material end =====
    That block contains M620/M621 (switch material), M620.1 E (push filament), and
    G1 E50/E5 flush extrusion. When filament stays loaded between loops (skip retraction),
    running this block causes unwanted poop. Replaces with M400 to preserve structure.
    """
    return _remove_ams_load_block(gcode)


# Markers that end the purge block (sliced G-code may use different formats than config)
_PURGE_END_MARKERS = (
    ";=====",      # Bambu section header (primary)
    "; layer",     # Layer boundary
    ";layer",      # No space variant
    "; printing",  # Object print start
)


def _remove_purge_block(text: str, replace_instead_of_remove: bool = False) -> str:
    """Shared logic: remove purge block from G-code/config text.
    When replace_instead_of_remove=True (for plate G-code), keep section header
    and replace content with M400 to preserve structure for Bambu Studio parser.
    """
    if not text or "nozzle load line" not in text.lower():
        return text
    # Preserve line endings (Windows uses \r\n; Bambu may expect them)
    has_cr = "\r\n" in text or text.endswith("\r")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sep = "\r\n" if has_cr else "\n"
    out: list[str] = []
    in_purge = False
    purge_line_count = 0
    max_purge_lines = 80  # Safeguard: purge block is ~15-30 lines; avoid eating whole file
    for line in lines:
        lower = line.strip().lower()
        if "nozzle load line" in lower and "=====" in lower:
            in_purge = True
            purge_line_count = 0
            if replace_instead_of_remove:
                out.append(line)  # Keep section header for plate G-code
                out.append("; (purge removed by P1S Auto-Clear)")
                out.append("M400")
            continue
        if in_purge:
            purge_line_count += 1
            if purge_line_count > max_purge_lines:
                in_purge = False
                out.append(line)
                continue
            if any(lower.startswith(m) for m in _PURGE_END_MARKERS):
                in_purge = False
                out.append(line)
            continue
        out.append(line)
    result = sep.join(out)
    # Safeguard: if removal would shrink file by >50%, likely a bad parse - skip
    if len(result) < len(text) * 0.5:
        return text
    return result
