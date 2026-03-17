# P1S Auto-Clear

NHDFARM-style G-code injection for Bambu Lab P1S (and P1P/X1C) automation. Injects configurable cooldown and pusher sweeps into your 3MF project so parts are automatically cleared after each print.

## Features

- **3MF workflow**: Load a 3MF file → configure → export updated 3MF
- **Time-based cooldown**: Wait N seconds (G4) before pushing
- **Temperature-based cooldown**: Wait until bed cools to X°C (M190)
- **Push height**: Auto (max part height minus offset, 5–50 mm) or Manual (fixed mm). Minimum 5 mm clearance: `max(5, max_layer_z - offset)` prevents bed damage on short parts
- **Bending modes**: NHDFARM (Z235↔Z200 ×6), z_pop, or none – helps break adhesion before sweeps
- **End section (match Auto-Clear)**: XY park (X65 Y265), fans off, M400—no G28 Z (Auto-Clear skips Z homing after sweeps to avoid Z axis homing failure)
- **Z-height caution**: Bambu default printable height is 250 mm (not 256 mm; 6 mm reserved for z-hop/debris). NHDFARM bending uses Z235, which is close to that limit—debris or dust caps can cause roof collision. Use bending "none" or "z_pop" if you see grinding or collision. See [Bambu Lab: Print volume limitations](https://wiki.bambulab.com/en/knowledge-sharing/print-volume-limitations)
- **Editable G-code template**: Customize the injection block (placeholders: `{cooldown}`, `{bending}`, `{sweeps}`)
- **Loop count**: Set how many times to run the project (1–999), stored in 3MF metadata
- **Remove purge line**: Option to remove the filament purge line (orange line at front of bed) from start G-code
- **Run Loop script**: Automatically send the job to your P1S, wait for completion, and repeat

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

Or run directly without installing (bump mode requires `pip install trimesh`):

```bash
cd p1s-autoclear
python -m p1s_autoclear
```

## Usage

1. Run the GUI: `python -m p1s_autoclear`
2. Click **Load 3MF** and select your Bambu Studio project file
3. Configure:
   - **Cooldown**: Time (seconds) or Temperature (°C)
   - **Push heights**: Comma-separated fractions (0.9, 0.6, 0.4, 0.2) or fixed mm
   - **G-code template**: Edit if you need custom behavior
4. Click **Export 3MF** and save the modified file
5. Open the exported 3MF in Bambu Studio, slice, and print as usual

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
