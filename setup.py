"""
setup.py — wheel/sdist build that works from a read-only source tree.

Hermes-CN is installable via ``pip install "git+https://github.com/Eynzof/Hermes-CN-Core.git"``
(and editable installs), so unlike upstream this setup.py must produce working
wheels. Two adaptations make that reliable on Windows/CI:

* Read-only source trees (e.g. an installed package dir or a read-only
  checkout) never fail the build: when the source tree is not writable,
  the ``build`` and ``egg_info`` command bases are redirected to a
  temporary directory instead of writing into the source tree.
* Bundled content ships as ``data_files``: ``skills/`` and
  ``optional-skills/`` are enumerated at build time so a wheel carries the
  bundled skill packs that the source-checkout layout provides for free.

PEP 517 ``build_wheel`` / ``build_sdist`` hooks in ``setuptools.build_meta``
call these commands internally, so ``uv build``, ``pip wheel``, and
``python -m build`` all go through the same read-only-safe path.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import tempfile

from setuptools import setup
from setuptools.command.build import build as _build
from setuptools.command.egg_info import egg_info as _egg_info


REPO_ROOT = Path(__file__).parent.resolve()


def _source_tree_is_writable() -> bool:
    probe = REPO_ROOT / ".setuptools-write-probe"
    try:
        with probe.open("w", encoding="utf-8", errors="replace") as handle:
            handle.write("")
        probe.unlink()
    except OSError:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def _temporary_build_dir(kind: str) -> str:
    return tempfile.mkdtemp(prefix=f"hermes-agent-{kind}-")


def _would_write_under_source(path_value: str | None) -> bool:
    if path_value is None:
        return True
    path = Path(path_value)
    if not path.is_absolute():
        path = REPO_ROOT / path
    try:
        path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return False
    return True


class ReadOnlySourceBuild(_build):
    def finalize_options(self) -> None:
        if (
            not _source_tree_is_writable()
            and _would_write_under_source(self.build_base)
        ):
            self.build_base = _temporary_build_dir("build")
        super().finalize_options()


class ReadOnlySourceEggInfo(_egg_info):
    def finalize_options(self) -> None:
        if (
            not _source_tree_is_writable()
            and _would_write_under_source(self.egg_base)
        ):
            self.egg_base = _temporary_build_dir("egg-info")
        super().finalize_options()


def _data_file_tree(root_name: str) -> list[tuple[str, list[str]]]:
    root = REPO_ROOT / root_name
    grouped: defaultdict[str, list[str]] = defaultdict(list)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(REPO_ROOT)
        grouped[str(rel_path.parent)].append(str(rel_path))
    return sorted(grouped.items())


setup(
    cmdclass={
        "build": ReadOnlySourceBuild,
        "egg_info": ReadOnlySourceEggInfo,
    },
    data_files=[
        *_data_file_tree("skills"),
        *_data_file_tree("optional-skills"),
    ]
)
