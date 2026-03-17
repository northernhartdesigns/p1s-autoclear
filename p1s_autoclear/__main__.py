"""Run P1S Auto-Clear GUI: python -m p1s_autoclear"""

import sys
import subprocess


def _ensure_deps() -> None:
    """Install optional deps (trimesh, bambulabs_api) if missing."""
    missing = []
    try:
        import trimesh  # noqa: F401
    except ImportError:
        missing.append("trimesh>=4.0")
    try:
        import bambulabs_api  # noqa: F401
    except ImportError:
        missing.append("bambulabs_api>=2.6.0")
    if missing:
        print("Installing missing dependencies:", ", ".join(missing))
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *missing])


if __name__ == "__main__":
    _ensure_deps()
    from .gui import main

    main()
