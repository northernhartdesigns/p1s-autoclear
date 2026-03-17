"""Unit tests for p1s_autoclear.injector."""

import re
import pytest

from p1s_autoclear.injector import (
    build_nhdfarm_sweep_block,
    build_injection_block,
    parse_temps_from_plate_gcode,
    remove_ams_load_from_gcode,
    remove_retraction_from_end_section,
    replace_m104_s0_in_end_section,
    wrap_plate_gcode_in_loops,
)


def test_build_nhdfarm_sweep_block_auto():
    """Auto mode outputs max(1, max_layer_z - offset) for min 1 mm (avoids bed contact)."""
    result = build_nhdfarm_sweep_block(
        push_height_mode="auto",
        push_height_offset_mm=20,
    )
    assert "max(1" in result and "max_layer_z - 20)" in result
    assert "G1 Z" in result


def test_build_nhdfarm_sweep_block_auto_custom_offset():
    """Auto mode uses configured offset."""
    result = build_nhdfarm_sweep_block(
        push_height_mode="auto",
        push_height_offset_mm=25,
    )
    assert "max(1" in result and "max_layer_z - 25)" in result


def test_build_nhdfarm_sweep_block_manual():
    """Manual mode outputs fixed mm height."""
    result = build_nhdfarm_sweep_block(
        push_height_mode="manual",
        push_height_mm=5,
    )
    assert "G1 Z5 F10000" in result
    assert "G1 Z5 F12000" in result


def test_build_nhdfarm_sweep_block_center_only():
    """push_mode center_only: two central pushes only, no rake passes."""
    result = build_nhdfarm_sweep_block(
        push_height_mode="manual", push_height_mm=5, push_mode="center_only"
    )
    assert "central sweeps (2)" in result
    assert "rake (pass 1)" not in result
    assert "rake (pass 2)" not in result


def test_build_nhdfarm_sweep_block_double_rake():
    """Sweep block mirrors Auto-Clear: double rake pass (F3000 then F12000)."""
    result = build_nhdfarm_sweep_block(push_height_mode="manual", push_height_mm=5)
    assert "rake (pass 1)" in result
    assert "rake (pass 2)" in result
    assert "G1 Y250 F3000" in result
    assert "G1 Y250 F12000" in result
    assert "G1 X220 F3000" in result
    assert "G1 X220 F12000" in result


def test_build_nhdfarm_sweep_block_center_only():
    """push_mode center_only: two central pushes only, no rake passes."""
    result = build_nhdfarm_sweep_block(
        push_height_mode="manual",
        push_height_mm=5,
        push_mode="center_only",
    )
    assert "central sweeps (2)" in result
    assert "G1 X125" in result
    assert "rake (pass 1)" not in result
    assert "rake (pass 2)" not in result


def test_build_nhdfarm_sweep_block_center_and_sweep():
    """push_mode center_and_sweep: central pushes + double rake (default)."""
    result = build_nhdfarm_sweep_block(
        push_height_mode="manual",
        push_height_mm=5,
        push_mode="center_and_sweep",
    )
    assert "central sweeps (2)" in result
    assert "rake (pass 1)" in result
    assert "rake (pass 2)" in result


def test_build_injection_block_no_g28_z_matches_autoclear():
    """End section matches Auto-Clear: safe corner only, no G28 Z command (avoids Z homing failure)."""
    result = build_injection_block(
        cooldown_mode="temp",
        cooldown_value=40,
        push_height_mode="auto",
        bending_mode="nhdfarm",
    )
    # Auto-Clear has no G28 Z after sweeps—check we don't emit the command (not the word in comments)
    import re
    assert not re.search(r"^\s*G28\s+Z\b", result, re.MULTILINE), "Should not emit G28 Z command"


def test_build_injection_block_bump_mode():
    """push_mode bump: single targeted push at part center/back."""
    result = build_injection_block(
        cooldown_mode="temp",
        cooldown_value=40,
        push_height_mode="manual",
        push_height_mm=5,
        push_mode="bump",
        part_bounds=(60.0, 120.0),
    )
    assert "targeted bump" in result
    assert "G1 X60.0 Y120.0" in result
    assert "G1 Y0" in result
    assert "rake (pass 1)" in result
    assert "rake (pass 2)" in result


def test_build_injection_block_has_xy_park():
    """Injection block includes XY park (match Auto-Clear end section)."""
    result = build_injection_block(
        cooldown_mode="temp",
        cooldown_value=40,
        push_height_mode="auto",
        bending_mode="nhdfarm",
    )
    assert "G1 X65 Y245" in result
    assert "G1 Y265" in result


def test_build_injection_block_short_part_safety():
    """Auto mode sweep uses max(1, ...) minimum to avoid bed contact."""
    result = build_injection_block(
        cooldown_mode="temp",
        cooldown_value=40,
        push_height_mode="auto",
        push_height_offset_mm=20,
        bending_mode="nhdfarm",
    )
    assert "max(1" in result


def test_build_injection_block_fans_during_cooldown_true():
    """When fans_during_cooldown=True, output contains all fan commands before cooldown and M107 at end."""
    result = build_injection_block(
        cooldown_mode="temp",
        cooldown_value=40,
        push_height_mode="auto",
        bending_mode="nhdfarm",
        fans_during_cooldown=True,
    )
    assert "M106 S255" in result
    assert "M106 P2 S255" in result
    assert "M106 P3 S255" in result
    assert "M190 S40" in result
    assert result.index("M106 S255") < result.index("M190 S40")
    # Fans must be turned off at end of sequence (especially important on last loop)
    assert "M107 ; part cooling off" in result
    assert "M107 P2 ; aux off" in result
    assert "M107 P3 ; chamber off" in result
    assert result.index("M107") < result.index("; --- End Auto-Clear ---")


def test_build_injection_block_fans_off_at_end():
    """When fans_during_cooldown=True, fans are turned off after first push and at end of sequence."""
    result = build_injection_block(
        cooldown_mode="temp",
        cooldown_value=40,
        push_height_mode="auto",
        bending_mode="nhdfarm",
        fans_during_cooldown=True,
    )
    assert "M107" in result
    assert "M107 P2" in result
    assert "M107 P3" in result
    # Fans off before sweep (after bending); last fans off after park
    assert result.rindex("M107") > result.index("G1 X65")


def test_build_injection_block_fans_during_cooldown_false():
    """When fans_during_cooldown=False, no M106 fan line is added."""
    result = build_injection_block(
        cooldown_mode="temp",
        cooldown_value=40,
        push_height_mode="auto",
        bending_mode="nhdfarm",
        fans_during_cooldown=False,
    )
    assert "M106 S255" not in result
    assert "M190 S40" in result


def test_build_injection_block_fans_off_after_first_push():
    """When fans_during_cooldown=True, M107 appears after central sweeps, before rake (maximize cooldown)."""
    result = build_injection_block(
        cooldown_mode="temp",
        cooldown_value=40,
        push_height_mode="auto",
        bending_mode="nhdfarm",
        fans_during_cooldown=True,
    )
    # M107 (fans off) must appear after central sweeps, before rake pass 1
    central_sweeps_end = "G1 Y0 F3000\n\nM107"
    rake_marker = "; -------- extended right-to-left rake (pass 1) --------"
    assert central_sweeps_end in result
    first_m107 = result.index("M107")
    assert first_m107 > result.index("; -------- central sweeps (2) --------")
    assert first_m107 < result.index(rake_marker)


def test_build_injection_block_reheat_between_loops_true_loop_count_2():
    """Preheat is never in the injection block; it is added by wrap_plate_gcode_in_loops between loops only."""
    result = build_injection_block(
        cooldown_mode="temp",
        cooldown_value=40,
        push_height_mode="auto",
        bending_mode="nhdfarm",
        fans_during_cooldown=False,
        reheat_between_loops=True,
        preheat_bed_temp=60,
        preheat_nozzle_temp=220,
        loop_count=2,
    )
    # Preheat at sweep start when reheat + loop_count > 1; heaters off at end always
    assert "M140 S60" in result  # preheat bed
    assert "M104 S220" in result  # preheat nozzle
    assert "M140 S0" in result  # heaters off at end
    assert "M104 S0" in result


def test_build_injection_block_reheat_between_loops_true_loop_count_1():
    """When reheat_between_loops=True and loop_count=1, no preheat (single print) but heaters off."""
    result = build_injection_block(
        cooldown_mode="temp",
        cooldown_value=40,
        push_height_mode="auto",
        bending_mode="nhdfarm",
        fans_during_cooldown=False,
        reheat_between_loops=True,
        preheat_bed_temp=60,
        preheat_nozzle_temp=220,
        loop_count=1,
    )
    assert "M140 S60" not in result  # no preheat for single print
    assert "M104 S220" not in result
    assert "M140 S0" in result  # heaters off at end
    assert "M104 S0" in result


def test_build_injection_block_reheat_between_loops_false():
    """When reheat_between_loops=False, no preheat but heaters off at end."""
    result = build_injection_block(
        cooldown_mode="temp",
        cooldown_value=40,
        push_height_mode="auto",
        bending_mode="nhdfarm",
        fans_during_cooldown=False,
        reheat_between_loops=False,
        loop_count=2,
    )
    assert "M140 S70" not in result  # no preheat
    assert "M104 S150" not in result
    assert "M140 S0" in result  # heaters off
    assert "M104 S0" in result


def test_build_injection_block_missing_placeholder():
    """ValueError when template lacks required placeholder."""
    bad_template = "Only {cooldown} and {bending} - no sweeps"
    with pytest.raises(ValueError) as excinfo:
        build_injection_block(
            cooldown_mode="temp",
            cooldown_value=40,
            template=bad_template,
        )
    assert "sweeps" in str(excinfo.value).lower()


def test_parse_temps_from_plate_gcode_m140_m104():
    """Parse bed and nozzle from M140/M104 commands."""
    gcode = "M140 S60\nM104 S220\nG28"
    bed, nozzle = parse_temps_from_plate_gcode(gcode)
    assert bed == 60
    assert nozzle == 220


def test_parse_temps_from_plate_gcode_bed_from_comment():
    """Parse bed temp from slicer comment when M140 not present (Bambu-style)."""
    gcode = (
        "; CONFIG_BLOCK_START\n"
        "; first_layer_bed_temperature = 55\n"
        "; nozzle_temperature = 220\n"
        "M104 S220\n"
        "G28\n"
    )
    bed, nozzle = parse_temps_from_plate_gcode(gcode)
    assert bed == 55
    assert nozzle == 220


def test_parse_temps_from_plate_gcode_excludes_m104_s0():
    """M104 S0 (hotend off) is excluded from nozzle temp."""
    gcode = "M140 S60\nM104 S0\nM104 S220\nG28"
    bed, nozzle = parse_temps_from_plate_gcode(gcode)
    assert bed == 60
    assert nozzle == 220


def test_parse_temps_from_plate_gcode_uses_largest():
    """Uses largest bed/nozzle temps found (reheat needs max for next print)."""
    gcode = (
        "M140 S55 ; first layer\n"
        "M104 S200 ; first layer\n"
        "G28\n"
        "M140 S60 ; other layers\n"
        "M104 S220 ; printing temp\n"
        "G1 Z0.2\n"
    )
    bed, nozzle = parse_temps_from_plate_gcode(gcode)
    assert bed == 60
    assert nozzle == 220


def test_wrap_plate_gcode_in_loops():
    """Loop count > 1 wraps plate gcode in Auto-Clear-style structure."""
    body = "G1 X10\nG1 Y20"
    result = wrap_plate_gcode_in_loops(body, loop_count=2)
    assert "; === LOOP 1 OF 2 ===" in result
    assert "; === END OF LOOP 1 ===" in result
    assert "; Preparing for next loop..." in result
    assert "G4 S2" in result
    assert "; === LOOP 2 OF 2 ===" in result
    assert "; === END OF LOOP 2 ===" in result
    assert body in result
    assert result.count(body) == 2


def test_wrap_plate_gcode_reheat_between_loops_not_after_last():
    """Reheat appears in 'Preparing for next loop' (between loops) but not after the last loop."""
    body = "G1 X10\nG1 Y20"
    result = wrap_plate_gcode_in_loops(
        body,
        loop_count=3,
        reheat_between_loops=True,
        preheat_bed_temp=60,
        preheat_nozzle_temp=220,
    )
    # Reheat between loop 1 and 2
    between_1_2 = result.split("; === END OF LOOP 1 ===")[1].split("; === LOOP 2 OF 3 ===")[0]
    assert "M140 S60" in between_1_2
    assert "M104 S220" in between_1_2
    # Reheat between loop 2 and 3
    between_2_3 = result.split("; === END OF LOOP 2 ===")[1].split("; === LOOP 3 OF 3 ===")[0]
    assert "M140 S60" in between_2_3
    assert "M104 S220" in between_2_3
    # No 'Preparing for next loop' after loop 3 (last) - so no reheat after last
    after_loop_3 = result.split("; === END OF LOOP 3 ===")[1]
    assert "M140" not in after_loop_3
    assert "M104" not in after_loop_3


def test_replace_m104_s0_in_end_section():
    """M104 S0 is replaced with M104 S{temp} in end section when reheat enabled (nozzle stays warm between loops)."""
    gcode = "G28\n; MACHINE_END_GCODE_START\nM104 S0 ; turn off hotend\nM400\nM17 S"
    result = replace_m104_s0_in_end_section(gcode, 220)
    assert "M104 S0" not in result
    assert "M104 S220" in result
    assert "hold nozzle at preheat temp" in result
    assert "M17 S" in result


def test_wrap_plate_gcode_reheat_replaces_m104_s0_between_loops():
    """When reheat_between_loops=True, loops 1..N-1 use M104 S{preheat} instead of M104 S0; last loop keeps M104 S0."""
    body = (
        "G28\n"
        "; MACHINE_END_GCODE_START\n"
        "M104 S0 ; turn off hotend\n"
        "M400\n"
        "M17 S"
    )
    result = wrap_plate_gcode_in_loops(
        body, loop_count=3, reheat_between_loops=True, preheat_nozzle_temp=200
    )
    # Loop 1 and 2: M104 S0 replaced with M104 S200
    loop1_body = result.split("; === LOOP 1 OF 3 ===")[1].split("; === END OF LOOP 1 ===")[0]
    loop2_body = result.split("; === LOOP 2 OF 3 ===")[1].split("; === END OF LOOP 2 ===")[0]
    assert "M104 S0" not in loop1_body
    assert "M104 S200" in loop1_body
    assert "M104 S0" not in loop2_body
    assert "M104 S200" in loop2_body
    # Loop 3 (last): keeps M104 S0 (turn off at end)
    loop3_body = result.split("; === LOOP 3 OF 3 ===")[1].split("; === END OF LOOP 3 ===")[0]
    assert "M104 S0" in loop3_body


def test_wrap_plate_gcode_purge_only_on_first_loop():
    """Purge (nozzle load line) runs only on loop 1; loops 2+ skip it when same filament."""
    body = (
        "G28\n"
        ";===== nozzle load line =====\n"
        "G1 X100 Y200 F12000\n"
        "; layer height 0.2\n"
        "G1 Z0.2\n"
        "M17 S"
    )
    result = wrap_plate_gcode_in_loops(body, loop_count=3)
    loop1_body = result.split("; === LOOP 1 OF 3 ===")[1].split("; === END OF LOOP 1 ===")[0]
    loop2_body = result.split("; === LOOP 2 OF 3 ===")[1].split("; === END OF LOOP 2 ===")[0]
    loop3_body = result.split("; === LOOP 3 OF 3 ===")[1].split("; === END OF LOOP 3 ===")[0]
    assert "nozzle load line" in loop1_body
    assert "G1 X100 Y200" in loop1_body
    assert "nozzle load line" in loop2_body  # section header kept, content replaced
    assert "purge removed by P1S Auto-Clear" in loop2_body or "G1 X100 Y200" not in loop2_body
    # Loop 2 and 3: purge block removed (replaced with M400)
    assert "G1 X100 Y200" not in loop2_body
    assert "G1 X100 Y200" not in loop3_body


def test_wrap_plate_gcode_loop_count_1():
    """Loop count 1 returns gcode unchanged."""
    body = "G1 X10"
    assert wrap_plate_gcode_in_loops(body, loop_count=1) == body


def test_wrap_plate_gcode_restores_z_current_before_loop_2():
    """Loop 2+ includes M17 R to restore Z current before homing (avoids z axis homing failed)."""
    body = "G28\nG1 X10"
    result = wrap_plate_gcode_in_loops(body, loop_count=2)
    # Loop 1: no M17 R (first loop doesn't need it)
    loop1_start = result.find("; === LOOP 1 OF 2 ===")
    loop1_body = result[loop1_start : result.find("; === END OF LOOP 1 ===")]
    assert "M17 R" not in loop1_body
    # Loop 2: has M17 R before body (restore Z current after M17 Z0.4 from previous loop)
    loop2_start = result.find("; === LOOP 2 OF 2 ===")
    loop2_section = result[loop2_start:]
    assert "M17 R ; restore Z current before homing" in loop2_section
    # M17 R must come before G28
    m17_pos = loop2_section.find("M17 R")
    g28_pos = loop2_section.find("G28")
    assert m17_pos < g28_pos


def test_wrap_plate_gcode_already_wrapped():
    """Already wrapped gcode is not double-wrapped."""
    wrapped = "; === LOOP 1 OF 2 ===\nG1 X10\n; === END OF LOOP 1 ==="
    result = wrap_plate_gcode_in_loops(wrapped, loop_count=3)
    assert result == wrapped


def test_remove_retraction_from_end_section_removes_g10():
    """G10 (firmware retract) is removed from executable end section."""
    gcode = "G28\n; MACHINE_END_GCODE_START\nG10\nM17 S"
    result = remove_retraction_from_end_section(gcode)
    assert "G10" not in result
    assert "M17 S" in result


def test_remove_retraction_from_end_section_removes_g1_e_negative():
    """G1 E-negative (direct retract) is removed from end section."""
    gcode = "G28\n; MACHINE_END_GCODE_START\nG1 E-0.8 F1800\nM17 S"
    result = remove_retraction_from_end_section(gcode)
    assert "G1 E-0.8" not in result
    assert "M17 S" in result


def test_remove_retraction_from_end_section_ignores_layer_gcode():
    """Retraction before MACHINE_END_GCODE_START is not removed."""
    gcode = "G1 E-0.5 F1800\nG28\n; MACHINE_END_GCODE_START\nM17 S"
    result = remove_retraction_from_end_section(gcode)
    assert "G1 E-0.5 F1800" in result
    assert "M17 S" in result


def test_remove_retraction_from_end_section_removes_ams_unload():
    """Bambu AMS unload block (M620..M621) is removed from end section."""
    gcode = (
        "G28\n; MACHINE_END_GCODE_START\n"
        "; pull back filament to AMS\n"
        "M620 S255\n"
        "G1 X20 Y50 F12000\n"
        "T255\n"
        "M621 S255\n"
        "M104 S0\n"
        "M17 S"
    )
    result = remove_retraction_from_end_section(gcode)
    assert "M620" not in result
    assert "M621" not in result
    assert "pull back filament to AMS" not in result
    assert "M104 S0" in result
    assert "M17 S" in result


def test_wrap_plate_gcode_skip_retraction_between_loops():
    """When skip_retraction_between_loops=True, middle loops get no-retract variant; last keeps retraction."""
    body = "G28\n; MACHINE_END_GCODE_START\nG1 E-0.8 F1800\nM17 S"
    result = wrap_plate_gcode_in_loops(body, loop_count=3, skip_retraction_between_loops=True)
    # Loop 1 and 2: G1 E-0.8 removed. Loop 3: kept.
    assert result.count("G1 E-0.8") == 1  # Only in last loop
    assert "; === LOOP 1 OF 3 ===" in result
    assert "; === LOOP 3 OF 3 ===" in result


def test_wrap_plate_gcode_removes_ams_load_when_skip_retraction():
    """AMS load block (prepare print temperature and material) is removed from loops 2+ when skip_retraction."""
    body = (
        "G28\n"
        ";===== prepare print temperature and material ==========\n"
        "M620 S2A\n"
        "M621 S2A\n"
        "M620.1 E F299 T270\n"
        "G1 E50 F200\n"
        ";===== prepare print temperature and material end =====\n"
        ";===== nozzle load line =====\n"
        "G1 X18 Y1\n"
        "; layer\n"
        "M17 S"
    )
    result = wrap_plate_gcode_in_loops(body, loop_count=2, skip_retraction_between_loops=True)
    # Loop 1: AMS load block kept (full start sequence)
    loop1_body = result.split("; === LOOP 1 OF 2 ===")[1].split("; === END OF LOOP 1 ===")[0]
    assert "M620 S2A" in loop1_body
    assert "G1 E50" in loop1_body
    # Loop 2: AMS load block removed (filament already loaded, avoid flush/poop)
    loop2_body = result.split("; === LOOP 2 OF 2 ===")[1].split("; === END OF LOOP 2 ===")[0]
    assert "prepare print temperature" in loop2_body  # section header kept
    assert "AMS load removed by P1S Auto-Clear" in loop2_body
    assert "M620 S2A" not in loop2_body
    assert "G1 E50" not in loop2_body


def test_remove_ams_load_from_gcode_removes_ams_block():
    """AMS load block (prepare print temperature and material) is replaced with M400 when present."""
    gcode = (
        "G28\n"
        ";===== prepare print temperature and material ==========\n"
        "M620 S2A\n"
        "M621 S2A\n"
        "M620.1 E F299 T270\n"
        "G1 E50 F200\n"
        ";===== prepare print temperature and material end =====\n"
        ";===== wipe nozzle =====\n"
        "G1 X100\n"
    )
    result = remove_ams_load_from_gcode(gcode)
    assert "M620" not in result
    assert "M621" not in result
    assert "G1 E50" not in result
    assert "prepare print temperature" in result  # Section header kept
    assert "AMS load removed by P1S Auto-Clear" in result
    assert "wipe nozzle" in result  # Following section unchanged


def test_wrap_plate_gcode_ams_load_removed_on_loops_2_plus():
    """When skip_retraction=True, AMS load block is removed from loops 2+ (no flush when filament stays loaded)."""
    body = (
        "G28\n"
        ";===== prepare print temperature and material ==========\n"
        "M620 S2A\n"
        "G1 E50 F200\n"
        ";===== prepare print temperature and material end =====\n"
        "G1 Z0.2\n"
        "; MACHINE_END_GCODE_START\n"
        "G10\n"
        "M17 S"
    )
    result = wrap_plate_gcode_in_loops(body, loop_count=2, skip_retraction_between_loops=True)
    loop1 = result.split("; === LOOP 1 OF 2 ===")[1].split("; === END OF LOOP 1 ===")[0]
    loop2 = result.split("; === LOOP 2 OF 2 ===")[1].split("; === END OF LOOP 2 ===")[0]
    assert "M620" in loop1  # Loop 1 keeps AMS load
    assert "G1 E50" in loop1
    assert "M620" not in loop2  # Loop 2: AMS load removed
    assert "G1 E50" not in loop2
    assert "AMS load removed by P1S Auto-Clear" in loop2


def test_remove_ams_load_from_gcode():
    """AMS load block (M620/M621/M620.1 + flush) is removed when filament stays loaded."""
    gcode = (
        "G28\n"
        ";===== prepare print temperature and material ==========\n"
        "M620 S2A\n"
        "M621 S2A\n"
        "M620.1 E F299 T270\n"
        "G1 E50 F200\n"
        "G1 E5 F300\n"
        ";===== prepare print temperature and material end =====\n"
        ";===== wipe nozzle =====\n"
        "G1 X100\n"
    )
    result = remove_ams_load_from_gcode(gcode)
    assert "M620" not in result
    assert "M621" not in result
    assert "M620.1" not in result
    assert "G1 E50" not in result
    assert "G1 E5" not in result
    assert "prepare print temperature and material" in result  # header kept
    assert "AMS load removed by P1S Auto-Clear" in result
    assert "wipe nozzle" in result


def test_wrap_plate_gcode_removes_ams_load_on_loops_2_plus_when_skip_retraction():
    """When skip_retraction: loops 2+ have AMS load block removed (no flush when filament loaded)."""
    body = (
        "G28\n"
        ";===== prepare print temperature and material ==========\n"
        "M620 S2A\n"
        "M621 S2A\n"
        "G1 E50 F200\n"
        ";===== prepare print temperature and material end =====\n"
        ";===== nozzle load line =====\n"
        "G1 X100 Y200\n"
        "; layer\n"
        "G1 Z0.2\n"
        "M17 S"
    )
    result = wrap_plate_gcode_in_loops(body, loop_count=2, skip_retraction_between_loops=True)
    loop1 = result.split("; === LOOP 1 OF 2 ===")[1].split("; === END OF LOOP 1 ===")[0]
    loop2 = result.split("; === LOOP 2 OF 2 ===")[1].split("; === END OF LOOP 2 ===")[0]
    assert "M620" in loop1
    assert "M621" in loop1
    assert "G1 E50" in loop1
    assert "M620" not in loop2
    assert "M621" not in loop2
    assert "G1 E50" not in loop2
    assert "AMS load removed by P1S Auto-Clear" in loop2


def test_remove_ams_load_from_gcode_removes_block():
    """Bambu AMS load block (prepare print temperature and material) is replaced with M400."""
    gcode = (
        "G28\n"
        ";===== prepare print temperature and material ==========\n"
        "M620 M\nM620 S2A\nM621 S2A\nM620.1 E F299 T270\n"
        "G1 E50 F200\nG1 E5 F300\n"
        ";===== prepare print temperature and material end =====\n"
        ";===== wipe nozzle =====\n"
    )
    result = remove_ams_load_from_gcode(gcode)
    assert "M620" not in result
    assert "M621" not in result
    assert "M620.1" not in result
    assert "G1 E50" not in result
    assert "prepare print temperature" in result
    assert "AMS load removed by P1S Auto-Clear" in result
    assert "wipe nozzle" in result


def test_wrap_plate_gcode_ams_load_removed_on_loops_2_plus():
    """When skip_retraction_between_loops=True, AMS load block is removed from loops 2+."""
    body = (
        "G28\n"
        ";===== prepare print temperature and material ==========\n"
        "M620 S2A\nM621 S2A\nG1 E50 F200\n"
        ";===== prepare print temperature and material end =====\n"
        "G1 X10\n"
    )
    result = wrap_plate_gcode_in_loops(body, loop_count=3, skip_retraction_between_loops=True)
    loop1 = result.split("; === LOOP 1 OF 3 ===")[1].split("; === END OF LOOP 1 ===")[0]
    loop2 = result.split("; === LOOP 2 OF 3 ===")[1].split("; === END OF LOOP 2 ===")[0]
    loop3 = result.split("; === LOOP 3 OF 3 ===")[1].split("; === END OF LOOP 3 ===")[0]
    # Loop 1: keeps AMS load (full load on first run)
    assert "M620" in loop1
    assert "M621" in loop1
    # Loops 2 and 3: AMS load removed (filament stays loaded between loops)
    assert "M620" not in loop2
    assert "M621" not in loop2
    assert "AMS load removed" in loop2
    assert "M620" not in loop3
    assert "M621" not in loop3
    assert "AMS load removed" in loop3


def test_remove_ams_load_from_gcode_removes_block():
    """AMS load block (M620/M621/M620.1 + flush) is removed when filament stays loaded."""
    gcode = (
        "G28\n"
        ";===== prepare print temperature and material ==========\n"
        "M620 M\n"
        "M620 S2A\n"
        "M621 S2A\n"
        "M620.1 E F299.339 T270\n"
        "G1 E50 F200\n"
        ";===== prepare print temperature and material end =====\n"
        ";===== wipe nozzle =====\n"
        "G1 X100\n"
    )
    result = remove_ams_load_from_gcode(gcode)
    assert "M620" not in result
    assert "M621" not in result
    assert "M620.1" not in result
    assert "G1 E50" not in result
    assert "prepare print temperature" in result
    assert "AMS load removed by P1S Auto-Clear" in result
    assert "M400" in result
    assert "wipe nozzle" in result


def test_wrap_plate_gcode_ams_load_only_on_first_loop():
    """When skip_retraction_between_loops=True, AMS load block runs only on loop 1; loops 2+ skip it."""
    body = (
        "G28\n"
        ";===== prepare print temperature and material ==========\n"
        "M620 S2A\n"
        "M621 S2A\n"
        "G1 E50 F200\n"
        ";===== prepare print temperature and material end =====\n"
        ";===== nozzle load line =====\n"
        "G1 X18 Y1\n"
        "; layer 1\n"
        "M17 S"
    )
    result = wrap_plate_gcode_in_loops(body, loop_count=2, skip_retraction_between_loops=True)
    loop1_body = result.split("; === LOOP 1 OF 2 ===")[1].split("; === END OF LOOP 1 ===")[0]
    loop2_body = result.split("; === LOOP 2 OF 2 ===")[1].split("; === END OF LOOP 2 ===")[0]
    # Loop 1: AMS load block present (M620, G1 E50)
    assert "M620 S2A" in loop1_body
    assert "G1 E50" in loop1_body
    # Loop 2: AMS load removed (filament already loaded)
    assert "M620" not in loop2_body
    assert "G1 E50" not in loop2_body
    assert "AMS load removed" in loop2_body


def test_wrap_plate_gcode_skip_retraction_removes_ams_load_from_loops_2_plus():
    """When skip_retraction_between_loops=True, loops 2+ skip AMS load block to avoid flush/poop."""
    from p1s_autoclear.injector import remove_ams_load_from_gcode

    body = (
        "G28\n"
        ";===== prepare print temperature and material ==========\n"
        "M620 S2A\n"
        "M621 S2A\n"
        "M620.1 E F299 T270\n"
        "G1 E50 F200\n"
        ";===== prepare print temperature and material end =====\n"
        ";===== nozzle load line =====\n"
        "G1 X18 Y1\n"
        "; layer 1\n"
        "M17 S"
    )
    result = wrap_plate_gcode_in_loops(body, loop_count=2, skip_retraction_between_loops=True)
    loop1_body = result.split("; === LOOP 1 OF 2 ===")[1].split("; === END OF LOOP 1 ===")[0]
    loop2_body = result.split("; === LOOP 2 OF 2 ===")[1].split("; === END OF LOOP 2 ===")[0]
    # Loop 1: keeps AMS load (M620, M621, M620.1, G1 E50)
    assert "M620 S2A" in loop1_body
    assert "G1 E50" in loop1_body
    # Loop 2: AMS load removed (replaced with M400); no M620/M621/G1 E50 flush
    assert "M620" not in loop2_body
    assert "M621" not in loop2_body
    assert "G1 E50" not in loop2_body
    assert "AMS load removed by P1S Auto-Clear" in loop2_body
    assert "prepare print temperature and material" in loop2_body  # section header kept


def test_remove_ams_load_from_gcode_standalone():
    """remove_ams_load_from_gcode replaces AMS load block with M400."""
    from p1s_autoclear.injector import remove_ams_load_from_gcode

    gcode = (
        "G28\n"
        ";===== prepare print temperature and material ==========\n"
        "M620 S2A\n"
        "M620.1 E F299 T270\n"
        "G1 E50 F200\n"
        ";===== prepare print temperature and material end =====\n"
        ";===== wipe nozzle =====\n"
        "G1 X100\n"
    )
    result = remove_ams_load_from_gcode(gcode)
    assert "M620" not in result
    assert "G1 E50" not in result
    assert "AMS load removed by P1S Auto-Clear" in result
    assert "prepare print temperature and material" in result  # header kept
    assert "M400" in result
    assert ";===== wipe nozzle =====" in result  # subsequent section unchanged


