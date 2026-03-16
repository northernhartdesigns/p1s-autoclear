"""P1S Auto-Clear: NHDFARM-style G-code injection for Bambu P1S automation."""

from pathlib import Path


def get_sacrificial_3mf_path() -> Path:
    """Path to the sacrificial 3MF, used to avoid Bambu Studio File Open crash.
    Open this first, then add your exported file (Ctrl+I) and choose 'Open as Project'."""
    return Path(__file__).parent / "data" / "sacrificial.3mf"


def _get_version() -> str:
    try:
        from importlib.metadata import version
        return version("p1s-autoclear")
    except Exception:
        return "0.1.0"  # fallback when not installed


__version__ = _get_version()
