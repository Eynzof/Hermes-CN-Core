"""Short-lived Windows spelling for the native wheel cache."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess

from pm.store import current_target


@contextmanager
def native_wheel_cache(cache: Path):
    """Keep MSVC object paths below MAX_PATH without moving the persistent cache."""
    if not current_target().startswith("win32"):
        yield cache
        return

    cache.mkdir(parents=True, exist_ok=True)
    occupied = {drive[0].upper() for drive in os.listdrives()}
    letter = next((letter for letter in "ZYXWVUTSRQPONMLKJIHGFED" if letter not in occupied), None)
    if letter is None:
        raise RuntimeError("native wheel build needs an unused Windows drive letter")
    drive = f"{letter}:"
    subprocess.run(["subst", drive, str(cache)], check=True, timeout=30)
    try:
        # Do not resolve this path: that would expand the alias before uv sees it.
        yield Path(drive + "\\")
    finally:
        subprocess.run(["subst", drive, "/d"], check=True, timeout=30)
