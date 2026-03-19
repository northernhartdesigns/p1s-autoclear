# Changelog

All notable changes to P1S Auto-Clear are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Multi-file merge**: Add multiple 3MF files to the GUI, configure per-file settings (cooldown, loops, etc.), and export a single merged 3MF with per-plate auto-clear settings
- **Per-plate settings**: `plate_settings` in 3MF metadata for different cooldown/loop counts per plate in merged files
- **Run Loop multi-plate**: `run_loop` detects multiple plates in a 3MF and prints each in sequence, honoring per-plate loop counts
- **Part center push mode**: targeted push at each part center/back (from mesh). Multi-part: one bump per part. Uses `get_part_bounds_from_3mf` (requires trimesh `[preview]`). Excludes plate geometry. Falls back to bed center when mesh unavailable.
- Last-used settings: app remembers cooldown, push heights, purge line, template, loop count
- Filament profiles: save/load presets (PLA, PETG, ABS/ASA + custom)
- Full settings stored in 3MF: reopening exported files restores all settings
- Purge-line removal from sliced .gcode.3mf (Metadata/plate_*.gcode)
- Replace purge block with minimal M400 in plate G-code to preserve Bambu Studio compatibility

### Fixed

- Bambu Studio "Failed to process G-code" when purge was removed from sliced files

## [0.1.0] - 2025-03-13

### Added

- 3MF load/edit/export workflow
- Time-based cooldown (G4) and temperature-based cooldown (M190)
- Configurable push heights (fraction or fixed mm)
- Editable G-code template with {cooldown} and {sweeps} placeholders
- Loop count (1–999) stored in 3MF metadata
- Remove purge line from project machine_start_gcode
- Run Loop script for automated print looping via bambulabs_api
