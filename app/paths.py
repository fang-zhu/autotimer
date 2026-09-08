"""Runtime paths that work both from source and from a PyInstaller bundle."""
import sys
from pathlib import Path


def project_root() -> Path:
    """Return the writable directory containing the source tree or EXE."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]
