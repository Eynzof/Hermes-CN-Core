"""Private Python-script entry point for the frozen cron runtime."""

from __future__ import annotations

import os
import runpy
import sys
from collections.abc import Sequence
from pathlib import Path


INTERNAL_CRON_SCRIPT_ARG = "--hermes-internal-cron-script"


def _error(message: str) -> int:
    print(f"Cron script runner: {message}", file=sys.stderr)
    return 2


def run_cron_script(script_path: str) -> int:
    """Run one script contained by the active profile's scripts directory."""
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    if not hermes_home:
        return _error("HERMES_HOME is required")

    try:
        scripts_dir = (Path(hermes_home).expanduser() / "scripts").resolve()
        raw_path = Path(script_path).expanduser()
        path = raw_path.resolve() if raw_path.is_absolute() else (scripts_dir / raw_path).resolve()
    except (OSError, RuntimeError) as exc:
        return _error(f"cannot resolve script path: {exc}")

    try:
        path.relative_to(scripts_dir)
    except ValueError:
        return _error(f"script path resolves outside the scripts directory: {script_path!r}")

    if not path.exists():
        return _error(f"script not found: {path}")
    if not path.is_file():
        return _error(f"script path is not a file: {path}")

    original_argv = sys.argv
    original_path = sys.path
    sys.argv = [str(path)]
    sys.path = [str(path.parent), *sys.path]
    try:
        runpy.run_path(str(path), run_name="__main__")
    finally:
        sys.argv = original_argv
        sys.path = original_path
    return 0


def dispatch_frozen_cron_script(argv: Sequence[str] | None = None) -> int | None:
    """Dispatch the private cron runner only from a PyInstaller process."""
    if not getattr(sys, "frozen", False):
        return None

    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != INTERNAL_CRON_SCRIPT_ARG:
        return None
    if len(args) != 2:
        return _error(f"expected exactly one script path, received {len(args) - 1}")
    return run_cron_script(args[1])
