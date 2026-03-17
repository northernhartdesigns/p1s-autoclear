"""
Run a print job in a loop: send to printer, wait for completion, repeat.

Requires: pip install bambulabs_api

Usage:
  python -m p1s_autoclear.run_loop path/to/autoclear.gcode.3mf
  python -m p1s_autoclear.run_loop path/to/autoclear.gcode --loop 5
  python -m p1s_autoclear.run_loop path/to/autoclear.gcode.3mf --bed-level-interval 5

Environment:
  BAMBU_IP          - Printer IP (e.g. 192.168.1.200)
  BAMBU_ACCESS_CODE - LAN access code from printer Settings
  BAMBU_SERIAL      - Printer serial number
  BAMBU_LOOP_DELAY  - Seconds between loops (default 60)
"""

import argparse
import json
import os
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path

from .processor import AUTOCLEAR_SETTINGS_PATH, get_autoclear_settings


def get_loop_count(path: Path, override: int | None) -> int:
    """Get loop count from 3MF metadata (p1s_autoclear_settings.json) or --loop override.
    Returns value clamped to 1–999. For non-3MF files, returns 1.
    """
    if override is not None:
        return max(1, min(999, override))
    if path.suffix.lower() in (".3mf", ".gcode.3mf"):
        settings = get_autoclear_settings(path)
        return max(1, min(999, settings.get("loop_count", 1)))
    return 1


def get_bed_level_interval(path: Path, override: int | None) -> int:
    """Get bed level interval from 3MF metadata or override.
    0 = level only on first loop; N = level every N loops (1, N+1, 2N+1, ...).
    """
    if override is not None:
        return max(0, min(999, override))
    if path.suffix.lower() in (".3mf", ".gcode.3mf"):
        settings = get_autoclear_settings(path)
        return max(0, min(999, int(settings.get("bed_level_interval", 0))))
    return 0


def should_run_bed_leveling(run: int, loop_count: int, bed_level_interval: int) -> bool:
    """True if bed leveling should run before this loop.
    Always on loop 1. If interval > 0, also on loops N+1, 2N+1, ...
    """
    if run == 1:
        return True
    if bed_level_interval <= 0:
        return False
    return (run - 1) % bed_level_interval == 0


def _start_print_with_bed_level_control(
    printer,
    upload_name: str,
    plate_number: int,
    bed_leveling: bool,
    flow_calibration: bool,
    use_ams: bool = True,
    ams_mapping: list[int] | None = None,
) -> bool:
    """Publish print command via MQTT with configurable bed_leveling.
    Bypasses bambulabs_api's hardcoded bed_leveling=True.
    """
    plate_location = f"Metadata/plate_{int(plate_number)}.gcode"
    ams_mapping = ams_mapping if ams_mapping is not None else [0]
    payload = {
        "print": {
            "command": "project_file",
            "param": plate_location,
            "file": upload_name,
            "bed_leveling": bool(bed_leveling),
            "bed_type": "textured_plate",
            "flow_cali": bool(flow_calibration),
            "vibration_cali": False,
            "url": f"ftp:///{upload_name}",
            "layer_inspect": False,
            "sequence_id": "10000000",
            "use_ams": bool(use_ams),
            "ams_mapping": list(ams_mapping),
            "skip_objects": None,
        }
    }
    if not printer.mqtt_client._client.is_connected():
        return False
    msg = printer.mqtt_client._client.publish(
        printer.mqtt_client.command_topic,
        json.dumps(payload),
    )
    msg.wait_for_publish()
    return msg.is_published()


def load_print_file(path: Path) -> tuple[BytesIO, str, str]:
    """
    Load file for printing. Returns (zip_buffer, upload_name, gcode_location).
    - .gcode.3mf: use as zip, upload as .3mf, gcode at Metadata/plate_1.gcode
    - .3mf: same
    - .gcode: wrap in zip as Metadata/plate_1.gcode, upload as .3mf
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    name = path.name
    upload_name = name if name.lower().endswith(".3mf") else f"{path.stem}.3mf"

    if path.suffix.lower() in (".3mf", ".gcode.3mf"):
        with open(path, "rb") as f:
            data = f.read()
        buf = BytesIO(data)
        buf.seek(0)
        return buf, upload_name, "Metadata/plate_1.gcode"

    # Plain .gcode - wrap in zip
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        gcode = f.read()
    zip_buf = BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Metadata/plate_1.gcode", gcode)
    zip_buf.seek(0)
    return zip_buf, upload_name, "Metadata/plate_1.gcode"


def run_loop(
    file_path: str | Path,
    loop_count: int | None = None,
    delay: int | None = None,
    bed_level_interval: int | None = None,
    ip: str | None = None,
    access_code: str | None = None,
    serial: str | None = None,
) -> None:
    """Execute the print loop: upload file to printer, start print, poll until finish, repeat.
    Uses bambulabs_api. Connects via IP, access code, and serial. Waits delay seconds
    between loops. Exits on upload/print failure.
    """
    try:
        import bambulabs_api as bl
    except ImportError:
        print(
            "bambulabs_api is required. Install with: pip install bambulabs_api",
            file=sys.stderr,
        )
        sys.exit(1)

    path = Path(file_path)
    loop_count = get_loop_count(path, loop_count)
    bed_level_interval_val = get_bed_level_interval(path, bed_level_interval)
    delay = delay if delay is not None else int(os.environ.get("BAMBU_LOOP_DELAY", "60"))
    ip = ip or os.environ.get("BAMBU_IP")
    access_code = access_code or os.environ.get("BAMBU_ACCESS_CODE")
    serial = serial or os.environ.get("BAMBU_SERIAL")

    if not all((ip, access_code, serial)):
        print(
            "Set BAMBU_IP, BAMBU_ACCESS_CODE, BAMBU_SERIAL environment variables "
            "or pass --ip, --access-code, --serial",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Connecting to {ip} (serial: {serial})")
    print(f"File: {path}")
    print(f"Loops: {loop_count}")
    print(f"Bed level: first loop only" if bed_level_interval_val == 0 else f"Bed level: loop 1 + every {bed_level_interval_val} loops")
    print("-" * 40)

    printer = bl.Printer(ip, access_code, serial)
    printer.connect()
    time.sleep(2)

    zip_buf, upload_name, gcode_location = load_print_file(path)

    for run in range(1, loop_count + 1):
        print(f"\n--- Loop {run}/{loop_count} ---")

        # Reload file each time (reuse same buffer for same file)
        zip_buf, upload_name, gcode_location = load_print_file(path)
        zip_buf.seek(0)

        result = printer.upload_file(zip_buf, upload_name)
        if "226" not in str(result):
            print(f"Error uploading file: {result}", file=sys.stderr)
            printer.disconnect()
            sys.exit(1)
        do_bed_level = should_run_bed_leveling(run, loop_count, bed_level_interval_val)
        do_flow_calib = do_bed_level  # flow calib when we level
        print("Uploaded. Starting print..." + (" (with bed leveling)" if do_bed_level else " (skip bed leveling)"))
        ok = _start_print_with_bed_level_control(
            printer, upload_name, 1,
            bed_leveling=do_bed_level,
            flow_calibration=do_flow_calib,
        )
        if not ok:
            print("Error starting print via MQTT", file=sys.stderr)
            printer.disconnect()
            sys.exit(1)
        print("Print started. Waiting for completion...")

        while True:
            time.sleep(15)
            status = printer.get_state()
            pct = printer.get_percentage()
            remaining = printer.get_time()
            print(f"  Status: {status} | {pct}% | ~{remaining}m left")
            if status in ("FINISH", "IDLE", "FAILED"):
                if status == "FAILED":
                    print("Print failed!", file=sys.stderr)
                    printer.disconnect()
                    sys.exit(1)
                print(f"Loop {run}/{loop_count} complete.")
                break

        if run < loop_count:
            print(f"Cooling and auto-clear will run. Next loop in {delay}s...")
            time.sleep(delay)

    printer.disconnect()
    print("\nAll loops finished.")


def main() -> None:
    """CLI entry point: parse args, run print loop. Requires BAMBU_IP, BAMBU_ACCESS_CODE, BAMBU_SERIAL."""
    parser = argparse.ArgumentParser(
        description="Run a P1S Auto-Clear print job in a loop"
    )
    parser.add_argument(
        "file",
        type=Path,
        help="Path to .gcode.3mf, .3mf, or .gcode file",
    )
    parser.add_argument(
        "-n", "--loop",
        type=int,
        default=None,
        help="Override loop count (from 3MF metadata if not set)",
    )
    parser.add_argument(
        "-d", "--delay",
        type=int,
        default=None,
        help="Seconds between loops (default 60, or BAMBU_LOOP_DELAY env)",
    )
    parser.add_argument(
        "--bed-level-interval",
        type=int,
        default=None,
        metavar="N",
        help="Re-level bed every N loops (0=first only). From 3MF if not set.",
    )
    parser.add_argument("--ip", help="Printer IP address")
    parser.add_argument("--access-code", help="LAN access code")
    parser.add_argument("--serial", help="Printer serial number")
    args = parser.parse_args()
    run_loop(
        args.file,
        loop_count=args.loop,
        delay=args.delay,
        bed_level_interval=args.bed_level_interval,
        ip=args.ip,
        access_code=args.access_code,
        serial=args.serial,
    )


if __name__ == "__main__":
    main()
