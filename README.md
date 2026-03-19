# P1S Auto-Clear

NHDFARM-style G-code injection for Bambu Lab P1S (and P1P/X1C) automation. Injects configurable cooldown and pusher sweeps into your 3MF project so parts are automatically cleared after each print.

**Repository root:** Clone and open the **`p1s-autoclear`** folder as the Git/project root (`pyproject.toml` and `.git` live there). An optional parent folder with launch scripts or scratch files is fine for local use only.

## Features

- **3MF workflow**: Load a 3MF file → configure → export updated 3MF
- **Time-based cooldown**: Wait N seconds (G4) before pushing
- **Temperature-based cooldown**: Wait until bed cools to X°C (M190)
- **Push height**: Auto (max part height minus offset, 5–50 mm) or Manual (fixed mm). Minimum 5 mm clearance: `max(5, max_layer_z - offset)` prevents bed damage on short parts
- **Bending modes**: NHDFARM (Z235↔Z200 ×6) or none – helps break adhesion before sweeps
- **Part center / Part center + sweep**: Same movement as **center_only** (one line at part center, double push back→front→back→front). X = part center (**clamped 32–206 mm**); Y uses **full bed** (back 250 mm → front 0) so the part is pushed out. Part center + sweep runs the same sequence twice (second pass at higher speed).
- **End section (match Auto-Clear)**: XY park (X65 Y265), fans off, M400—no G28 Z (Auto-Clear skips Z homing after sweeps to avoid Z axis homing failure)
- **Z-height caution**: Bambu default printable height is 250 mm (not 256 mm; 6 mm reserved for z-hop/debris). NHDFARM bending uses Z235, which is close to that limit—debris or dust caps can cause roof collision. Use bending **none** if you see grinding or collision. See [Bambu Lab: Print volume limitations](https://wiki.bambulab.com/en/knowledge-sharing/print-volume-limitations)
- **Editable G-code template**: Customize the injection block (placeholders: `{cooldown}`, `{bending}`, `{sweeps}`)
- **Loop count**: Set how many times to run the project (1–999), stored in 3MF metadata
- **Multi-file merge**: Add multiple sliced 3MFs — **one continuous print** (default): job 1 → auto-clear → job 2 → … in a single `plate_1.gcode`. Or uncheck “One continuous print” for separate Bambu plates. Per-file **Settings** still applies (cooldown, loops per job).
- **Remove purge line**: Option to remove the filament purge line (orange line at front of bed) from start G-code
- **Run Loop script**: Automatically send the job to your P1S, wait for completion, and repeat (supports multi-plate)

## Requirements

- Python 3.10+
- Bambu Studio or PrusaSlicer 3MF project
- NHDFARM Stage 1 hardware (tilted feet, pusher) recommended

## Installation

**Auto-install (recommended):**
- **Windows**: Double-click `install.bat`
- **Linux/Mac**: `./install.sh` or `bash install.sh`

**Manual install:**
```bash
cd p1s-autoclear
pip install -r requirements.txt
```

**Minimal (no preview/run-loop):**
```bash
cd p1s-autoclear
pip install -e .
```

Or run directly without installing (Part center mode requires `pip install trimesh`):

```bash
cd p1s-autoclear
python -m p1s_autoclear
```

## Usage

1. Run the GUI: `python -m p1s_autoclear`
2. Click **Add 3MF** to add one or more sliced 3MF files (each must have `Metadata/plate_1.gcode`)
3. For multiple files, use **Settings** on each to set per-file cooldown, loop count, etc.
4. Configure cooldown, push height, loop count, etc. (main form applies to the first/single file)
5. Click **Export 3MF** and save. Single-file: one 3MF with auto-clear. Multi-file: merged 3MF with per-plate settings.
6. Open in Bambu Studio or use Run Loop to print

The injected G-code runs at the end of each print: after the hotend and timelapse finish, the bed cools (time or temp), then the toolhead sweeps at multiple Z heights to push the part off into your collection bin.

## How It Works

The app modifies `machine_end_gcode` inside the 3MF config. It inserts an auto-clear block right before `M17 S` (printer sleep). The block uses Bambu placeholders like `{max_layer_z}` so sweep heights resolve correctly at slice time.

## Releasing

See [RELEASING.md](RELEASING.md) for the version bump and release process.

## References

- [Bambu Lab – Print volume limitations](https://wiki.bambulab.com/en/knowledge-sharing/print-volume-limitations) – Z-height limits, roof collision
- [NHDFARM Stage 1 MakerWorld](https://makerworld.com/en/models/1776315-farmloop-stage-1-x1c-p1s-p1p-automatic-printing)
- [Infinity Flow 3D - Auto Bed Clearing](https://infinityflow3d.com/blogs/3d-printer-automation/3d-printer-auto-bed-clearing-bambu-p1s-example)
- [Bambu Lab – Print volume limitations](https://wiki.bambulab.com/en/knowledge-sharing/print-volume-limitations) (Z height, roof collision)
- [Bambu Lab – Print volume limitations](https://wiki.bambulab.com/en/knowledge-sharing/print-volume-limitations) – Z-height, roof collision, 250 vs 256 mm
- [Bambu Lab – Print volume limitations](https://wiki.bambulab.com/en/knowledge-sharing/print-volume-limitations) (Z height, roof collision)
- [Bambu Lab – Print volume limitations (Z-height, roof collision)](https://wiki.bambulab.com/en/knowledge-sharing/print-volume-limitations)
- [Bambu Lab – Print volume limitations](https://wiki.bambulab.com/en/knowledge-sharing/print-volume-limitations) – Z-height, z-hop, roof collision safety
- [Bambu Lab – Print volume limitations (Z-height, roof collision)](https://wiki.bambulab.com/en/knowledge-sharing/print-volume-limitations)
- [Bambu Lab – Print volume limitations](https://wiki.bambulab.com/en/knowledge-sharing/print-volume-limitations) (Z-height, roof collision safety)
