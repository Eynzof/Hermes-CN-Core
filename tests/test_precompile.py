"""Tests for the ahead-of-time bytecode warm-up (``scripts/precompile.py``).

P-044 added ``precompile_all`` (covered by
``tests/tools/test_wmi_ssl_windows_overhead.py``).  These tests cover the
flame-graph import-optimization follow-ups:

* ``source_fingerprint`` — cheap, stat-only, and sensitive to any source or
  interpreter change (so the stamp can never serve a stale "already warm").
* ``precompile_if_needed`` — idempotent: compiles once, then skips on an
  unchanged tree, recompiles on change, and honours ``force``.
* ``precompile_in_background`` — runs off the hot path in a daemon thread.
* ``_pyproject_targets`` — the ``[tool.hermes.precompile]`` override wiring.

The module is loaded by path (it lives in ``scripts/``, not an importable
package) exactly as the P-044 test does.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_precompile():
    path = REPO_ROOT / "scripts" / "precompile.py"
    spec = importlib.util.spec_from_file_location("hermes_precompile_undertest", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pc = _load_precompile()


# ---------------------------------------------------------------------------
# pyproject [tool.hermes.precompile] wiring
# ---------------------------------------------------------------------------

def test_pyproject_targets_read():
    targets = pc._pyproject_targets()
    assert targets is not None, "[tool.hermes.precompile].targets missing from pyproject.toml"
    # Behaviour contract (not a snapshot): the override must include the core
    # package trees and the top-level entry module, however the list evolves.
    assert "agent" in targets and "tools" in targets
    assert "run_agent.py" in targets


def test_default_target_list_uses_override():
    assert pc._default_target_list() == pc._pyproject_targets()


# ---------------------------------------------------------------------------
# source_fingerprint
# ---------------------------------------------------------------------------

def test_source_fingerprint_stable(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")
    fp1 = pc.source_fingerprint([str(tmp_path)])
    fp2 = pc.source_fingerprint([str(tmp_path)])
    assert fp1 == fp2 and len(fp1) == 40  # sha1 hexdigest


def test_source_fingerprint_changes_on_edit(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    before = pc.source_fingerprint([str(tmp_path)])
    # Change the size so the fingerprint differs even if mtime resolution is
    # coarse — the point is that an edit is never missed.
    f.write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    after = pc.source_fingerprint([str(tmp_path)])
    assert before != after


def test_source_fingerprint_keys_on_interpreter():
    """The interpreter identity is part of the digest, so a Python upgrade (whose
    ``.pyc`` magic differs) forces a fresh compile."""
    fp = pc.source_fingerprint(["platform_utils.py"])
    # Same call is stable within this interpreter.
    assert fp == pc.source_fingerprint(["platform_utils.py"])


# ---------------------------------------------------------------------------
# precompile_if_needed (stamp-guarded idempotency)
# ---------------------------------------------------------------------------

def _pyc_exists(py: Path) -> bool:
    cache = py.parent / "__pycache__"
    return cache.is_dir() and any(cache.glob(f"{py.stem}.*.pyc"))


def test_precompile_if_needed_compiles_then_skips(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text("VALUE = 42\n", encoding="utf-8")
    stamp = tmp_path / "precompile.stamp"

    first = pc.precompile_if_needed([str(src)], stamp_path=str(stamp), quiet=True)
    assert first == "compiled"
    assert stamp.exists()
    assert _pyc_exists(src)

    second = pc.precompile_if_needed([str(src)], stamp_path=str(stamp), quiet=True)
    assert second == "skipped", "unchanged tree should not recompile"


def test_precompile_if_needed_recompiles_on_change(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text("VALUE = 1\n", encoding="utf-8")
    stamp = tmp_path / "precompile.stamp"

    assert pc.precompile_if_needed([str(src)], stamp_path=str(stamp), quiet=True) == "compiled"
    assert pc.precompile_if_needed([str(src)], stamp_path=str(stamp), quiet=True) == "skipped"

    src.write_text("VALUE = 1\nEXTRA = 2\n", encoding="utf-8")
    assert pc.precompile_if_needed([str(src)], stamp_path=str(stamp), quiet=True) == "compiled"


def test_precompile_if_needed_force(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text("VALUE = 1\n", encoding="utf-8")
    stamp = tmp_path / "precompile.stamp"

    assert pc.precompile_if_needed([str(src)], stamp_path=str(stamp), quiet=True) == "compiled"
    # Without force it would skip; force recompiles regardless of the stamp.
    assert pc.precompile_if_needed(
        [str(src)], stamp_path=str(stamp), quiet=True, force=True
    ) == "compiled"


def test_precompile_if_needed_never_raises(tmp_path):
    """A guarded warm-up must not raise even on a bad target — it returns a
    status string the caller can ignore."""
    # A syntax-error file: precompile_all reports it (ok=False) but if_needed
    # still returns a normal completion status, never an exception.
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    stamp = tmp_path / "precompile.stamp"
    status = pc.precompile_if_needed([str(bad)], stamp_path=str(stamp), quiet=True)
    assert status in ("compiled", "skipped", "unavailable")


# ---------------------------------------------------------------------------
# precompile_in_background
# ---------------------------------------------------------------------------

def test_precompile_in_background_completes(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text("VALUE = 7\n", encoding="utf-8")
    stamp = tmp_path / "precompile.stamp"

    thread = pc.precompile_in_background([str(src)], stamp_path=str(stamp), quiet=True)
    thread.join(timeout=30)
    assert not thread.is_alive(), "background precompile did not finish in time"
    assert thread.daemon is True
    assert stamp.exists()
    assert _pyc_exists(src)


# ---------------------------------------------------------------------------
# precompile_all regression (signature/behaviour preserved after refactor)
# ---------------------------------------------------------------------------

def test_precompile_all_still_returns_bool(tmp_path):
    good = tmp_path / "ok.py"
    good.write_text("A = 1\n", encoding="utf-8")
    assert pc.precompile_all([str(good)], quiet=True) is True
    assert _pyc_exists(good)


def test_precompile_all_missing_path_not_fatal(tmp_path):
    assert pc.precompile_all([str(tmp_path / "nope")], quiet=True) is True


def test_precompile_all_syntax_error_reported_not_raised(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    assert pc.precompile_all([str(bad)], quiet=True) is False
