"""
Run a print job in a loop: send to printer, wait for completion, repeat.

Requires: pip install bambulabs_api

Usage:
  python -m p1s_autoclear.run_loop path/to/autoclear.gcode.3mf
  python -m p1s_autoclear.run_loop path/to/autoclear.gcode --loop 5

Environment:
  BAMBU_IP          - Printer IP (e.g. 192.168.1.200)
  BAMBU_ACCESS_CODE - LAN access code from printer Settings
  BAMBU_SERIAL      - Printer serial number
  BAMBU_LOOP_DELAY  - Seconds between loops (default 60)
"""

import argparse
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
        print("Uploaded. Starting print...")
        printer.start_print(upload_name, 1)
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
    parser.add_argument("--ip", help="Printer IP address")
    parser.add_argument("--access-code", help="LAN access code")
    parser.add_argument("--serial", help="Printer serial number")
    args = parser.parse_args()
    run_loop(
        args.file,
        loop_count=args.loop,
        delay=args.delay,
        ip=args.ip,
        access_code=args.access_code,
        serial=args.serial,
    )


if __name__ == "__main__":
    main()
