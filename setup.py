"""
setup.py — wheel/sdist build that works from a read-only source tree.

Hermes-CN is installable via ``pip install "git+https://github.com/Eynzof/Hermes-CN-Core.git"``
(and editable installs), so unlike upstream this setup.py must produce working
wheels. Two adaptations make that reliable on Windows/CI:

* Read-only source trees (e.g. an installed package dir or a read-only
  checkout) never fail the build: when the source tree is not writable,
  the ``build`` and ``egg_info`` command bases are redirected to a
  temporary directory instead of writing into the source tree.
* Bundled content ships as ``data_files``: ``skills/``, ``optional-skills/``,
  ``locales/`` and ``optional-mcps/`` are enumerated at build time so a wheel
  carries the payloads the source-checkout layout provides for free. The
  enumeration must live here, not in ``[tool.setuptools.data-files]``: a
  pyproject table *replaces* the ``data_files`` kwarg instead of extending it,
  which silently dropped every enumerated path from the wheel.

PEP 517 ``build_wheel`` / ``build_sdist`` hooks in ``setuptools.build_meta``
call these commands internally, so ``uv build``, ``pip wheel``, and
``python -m build`` all go through the same read-only-safe path.
"""

from __future__ import annotations

from collections import defaultdict
import os
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


def _root_py_modules() -> list[str]:
    """Root single-file modules (``run_agent``, ``hermes_state``, ``toolsets``...).

    ``packages.find`` only sees directories with an ``__init__.py``, so the wheel
    build needs them on ``py_modules``. Derive the list from the source tree at
    build time: a static list in ``pyproject.toml`` drifts every time the root
    layout changes and shipped broken wheels (``ModuleNotFoundError: hermes_state``).
    ``tests/test_packaging_py_modules.py`` pins both halves of the contract.
    """
    try:
        names = os.listdir(REPO_ROOT)
    except OSError:
        return []
    return sorted(name[:-3] for name in names if name.endswith(".py") and name != "setup.py")


def _data_file_tree(root_name: str) -> list[tuple[str, list[str]]]:
    """Enumerate ``root_name`` as setuptools ``data_files`` entries.

    Each distinct directory under the root becomes its own target, because
    setuptools flattens a data-files target: one shared ``optional-mcps`` target
    would collapse all 65 catalog manifests into a single colliding
    ``optional-mcps/manifest.yaml`` (``hermes_cli/mcp_catalog.py`` iterates the
    per-entry directories). Wheel member names are POSIX, so render them with
    ``as_posix()`` rather than the host separator.
    """
    root = REPO_ROOT / root_name
    grouped: defaultdict[str, list[str]] = defaultdict(list)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(REPO_ROOT)
        grouped[rel_path.parent.as_posix()].append(rel_path.as_posix())
    return sorted(grouped.items())


setup(
    py_modules=_root_py_modules(),
    cmdclass={
        "build": ReadOnlySourceBuild,
        "egg_info": ReadOnlySourceEggInfo,
    },
    data_files=[
        # Bundled, non-package payloads. skills/ + optional-skills/ are the
        # prompt/skill packs; locales/ is the i18n catalogs (a sealed install
        # otherwise surfaces raw keys like gateway.reset.header_default);
        # optional-mcps/ is the shipped MCP catalog (`hermes mcp catalog` and the
        # dashboard catalog screen read one manifest per entry). All four were
        # enumerated here once and then shadowed by a pyproject data-files table —
        # the wheel shipped 2 of 65 catalog manifests and none of the 441 skill
        # files. tests/test_packaging_build_guard.py builds the real wheel.
        *_data_file_tree("skills"),
        *_data_file_tree("optional-skills"),
        *_data_file_tree("locales"),
        *_data_file_tree("optional-mcps"),
    ]
)
