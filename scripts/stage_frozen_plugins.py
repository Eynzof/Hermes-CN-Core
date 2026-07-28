#!/usr/bin/env python3
"""Stage runtime plugin files for PyInstaller's physical plugin loader."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


_EXCLUDED_DIRECTORIES = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "docs",
    "node_modules",
    "tests",
}
_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def _ignore_runtime_irrelevant_files(directory: str, names: list[str]) -> set[str]:
    root = Path(directory)
    ignored: set[str] = set()
    for name in names:
        candidate = root / name
        if candidate.is_dir() and name in _EXCLUDED_DIRECTORIES:
            ignored.add(name)
        elif candidate.is_file() and candidate.suffix.lower() in _EXCLUDED_SUFFIXES:
            ignored.add(name)
    return ignored


def stage_frozen_plugins(source: Path, output: Path) -> None:
    source = source.resolve()
    output = output.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"plugin source directory not found: {source}")
    if output.exists():
        raise FileExistsError(f"plugin staging output already exists: {output}")
    if output.is_relative_to(source):
        raise ValueError("plugin staging output must be outside the source tree")

    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        output,
        ignore=_ignore_runtime_irrelevant_files,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("plugins"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    stage_frozen_plugins(args.source, args.output)


if __name__ == "__main__":
    main()
