"""Tests for the first-party import accelerator (``import_accelerator``).

The accelerator installs a meta-path finder that resolves Hermes's own
top-level modules/packages with a single dict lookup, skipping the per-entry
``sys.path`` directory scan (``nt.stat`` / ``nt._path_exists``) the stock
machinery pays per import (``.plans/16-Flame-Graph-Import-Optimizations.md``).

These tests pin the two things that matter:

1. **Correctness** — package-over-module precedence, ``.pyc``-preserving specs,
   O(1) fall-through for non-first-party names, curated allow-list (no
   accidental shadowing of the real PyPI ``packaging`` dependency), idempotent
   install/uninstall, and the env opt-out.
2. **The bypass itself** — ``test_import_hook_bypass`` proves, deterministically
   (not via a flaky wall-clock number), that an accelerated module resolves
   WITHOUT consulting ``sys.path`` at all: with the repo root stripped from
   ``sys.path`` the import still succeeds through the accelerator, and
   ``PathFinder`` is never consulted for it.

Pure-function tests construct a finder directly (no ``sys.meta_path``
mutation); stateful tests run in a clean subprocess so the process import state
is never polluted.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import import_accelerator as ia  # noqa: E402


# ---------------------------------------------------------------------------
# Pure-function unit tests (no global import-state mutation)
# ---------------------------------------------------------------------------

def test_build_map_curated_names():
    modules, packages = ia.build_first_party_map()
    # Canonical single-file modules resolve as modules...
    assert "hermes_constants" in modules
    assert "run_agent" in modules
    assert "platform_utils" in modules
    # ...and top-level packages as packages.
    assert "agent" in packages
    assert "tools" in packages
    # A package always wins over a same-named ``.py`` at the root: the repo-root
    # ``agent.py`` harness must NEVER be registered as the ``agent`` module.
    assert "agent" not in modules


def test_build_map_never_shadows_third_party_packaging():
    """The repo has a local ``packaging/`` dir, but ``packaging`` is also a real
    PyPI dependency imported by pip/uv/hermes. A curated allow-list must not
    register it (a blind os.listdir scan would)."""
    modules, packages = ia.build_first_party_map()
    assert "packaging" not in modules
    assert "packaging" not in packages
    assert "tests" not in packages  # never intercept the test tree either


def test_build_map_only_existing_files():
    """Only entries whose files exist are registered (partial checkout safe)."""
    modules, packages = ia.build_first_party_map()
    for name, path in modules.items():
        assert os.path.isfile(path), (name, path)
    for name, (init_py, search) in packages.items():
        assert os.path.isfile(init_py), (name, init_py)
        assert search and os.path.isdir(search[0])


def _fresh_finder():
    modules, packages = ia.build_first_party_map()
    return ia._FirstPartyFinder(ia._repo_root(), modules, packages)


def test_finder_resolves_module_spec():
    finder = _fresh_finder()
    spec = finder.find_spec("hermes_constants")
    assert spec is not None
    assert spec.name == "hermes_constants"
    assert spec.origin.endswith("hermes_constants.py")


def test_finder_resolves_package_with_search_locations():
    finder = _fresh_finder()
    spec = finder.find_spec("agent")
    assert spec is not None
    # A package spec carries submodule_search_locations so ``agent.<sub>``
    # resolves normally through the standard one-directory FileFinder.
    assert spec.submodule_search_locations
    assert os.path.basename(spec.origin) == "__init__.py"


def test_finder_spec_uses_source_file_loader():
    """The returned spec uses the stdlib SourceFileLoader, so ``.pyc`` caching
    (marshal.loads of ready bytecode) is preserved — we do not recompile source
    on every import."""
    finder = _fresh_finder()
    spec = finder.find_spec("platform_utils")
    loader = spec.loader
    # SourceFileLoader exposes get_code / path and reads/writes __pycache__.
    assert hasattr(loader, "get_code")
    assert getattr(loader, "path", "").endswith("platform_utils.py")


def test_finder_returns_none_for_unknown_and_stdlib():
    finder = _fresh_finder()
    assert finder.find_spec("json") is None
    assert finder.find_spec("os") is None
    # Not-shadowed third-party name.
    assert finder.find_spec("packaging") is None
    # A name that does not exist at all.
    assert finder.find_spec("definitely_not_a_hermes_module_xyz") is None


def test_finder_ignores_submodule_imports():
    """Submodule imports arrive with a non-None ``path`` (the parent's __path__)
    and must fall through to the standard FileFinder."""
    finder = _fresh_finder()
    assert finder.find_spec("tools.registry", ["/some/path"]) is None
    assert finder.find_spec("agent.message_utils", ["/x"]) is None


def test_finder_owns_and_handled_names():
    finder = _fresh_finder()
    assert finder.owns("agent") and finder.owns("hermes_constants")
    assert not finder.owns("json")
    names = finder.handled_names
    assert "agent" in names and "hermes_constants" in names
    assert names == tuple(sorted(names))  # stable, sorted


def test_build_map_excludes_missing_files(tmp_path):
    """Existence is validated once at build time (not re-stat'd on every
    resolve), so a curated name absent from *root* is simply not registered."""
    modules, packages = ia.build_first_party_map(str(tmp_path))
    assert modules == {} and packages == {}


def test_build_map_picks_up_present_first_party_files(tmp_path):
    """When a curated name's file exists under *root*, it is registered — a
    module as a module, a package (with __init__.py) as a package."""
    (tmp_path / "hermes_constants.py").write_text("X = 1\n", encoding="utf-8")
    pkg = tmp_path / "agent"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("\n", encoding="utf-8")
    # A repo-root agent.py alongside the package must NOT win (package precedence).
    (tmp_path / "agent.py").write_text("raise RuntimeError('harness')\n", encoding="utf-8")

    modules, packages = ia.build_first_party_map(str(tmp_path))
    assert "hermes_constants" in modules
    assert "agent" in packages
    assert "agent" not in modules


# ---------------------------------------------------------------------------
# Install / uninstall lifecycle (restore global state in finally)
# ---------------------------------------------------------------------------

@pytest.fixture
def restore_meta_path():
    before = list(sys.meta_path)
    was_installed = ia.is_installed()
    try:
        yield
    finally:
        sys.meta_path[:] = before
        # Re-sync the module-global bookkeeping to the restored state.
        if not was_installed:
            ia._installed_finder = None
        else:
            ia._installed_finder = ia.active_finder()


def test_install_is_idempotent(restore_meta_path):
    ia.uninstall()
    assert not ia.is_installed()
    assert ia.install() is True
    assert ia.is_installed()
    # A second install is a no-op (does not stack a second finder).
    assert ia.install() is False
    n_finders = sum(1 for f in sys.meta_path if isinstance(f, ia._FirstPartyFinder))
    assert n_finders == 1
    # Installed at the FRONT so it wins before PathFinder builds a FileFinder.
    assert isinstance(sys.meta_path[0], ia._FirstPartyFinder)


def test_uninstall_removes_finder(restore_meta_path):
    ia.install()
    assert ia.is_installed()
    assert ia.uninstall() is True
    assert not ia.is_installed()
    # Uninstall again reports nothing to remove.
    assert ia.uninstall() is False


def test_disable_env_var(monkeypatch, restore_meta_path):
    ia.uninstall()
    monkeypatch.setenv("HERMES_DISABLE_IMPORT_ACCELERATOR", "1")
    assert ia.install() is False
    assert not ia.is_installed()


# ---------------------------------------------------------------------------
# THE bypass proof — deterministic, runs in a clean subprocess.
# ---------------------------------------------------------------------------

_BYPASS_PROBE = r'''
import os, sys, orjson
sys.path.insert(0, ROOT)
import import_accelerator as ia
import importlib.machinery as machinery

# Instrument PathFinder so we can prove it is never consulted for an
# accelerated module.
_orig = machinery.PathFinder.find_spec
_seen = {"names": []}
def _counting(fullname, path=None, target=None):
    _seen["names"].append(fullname)
    return _orig(fullname, path, target)
machinery.PathFinder.find_spec = staticmethod(_counting)

assert ia.install() is True

root = ia._repo_root()

# 1) Strip the repo root (and relative cwd entries) from sys.path entirely, so
#    the ONLY way a first-party module can still resolve is the accelerator.
sys.path[:] = [
    p for p in sys.path
    if p not in ("", ".") and os.path.abspath(p) != os.path.abspath(root)
]
sys.modules.pop("platform_utils", None)
_seen["names"].clear()

import platform_utils  # must succeed purely via the accelerator
bypass_ok = platform_utils.is_windows() in (True, False)
pathfinder_touched = "platform_utils" in _seen["names"]

# 2) Control: with the accelerator REMOVED and the root still absent, the same
#    import must FAIL — proving step 1 only worked because of the accelerator.
ia.uninstall()
sys.modules.pop("platform_utils", None)
control_failed = False
try:
    import platform_utils  # noqa: F811
except ModuleNotFoundError:
    control_failed = True

print("RESULT " + orjson.dumps({
    "bypass_ok": bypass_ok,
    "pathfinder_touched": pathfinder_touched,
    "control_failed": control_failed,
}).decode('utf-8'))
'''


def _run_probe(probe: str, timeout: int = 60):
    src = f"ROOT = {str(PROJECT_ROOT)!r}\n" + probe
    result = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True, text=True, timeout=timeout, cwd=str(PROJECT_ROOT),
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr[-2000:]}"
    lines = [ln for ln in result.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, f"probe produced no RESULT line:\n{result.stdout[-2000:]}"
    import orjson
    return orjson.loads(lines[-1][len("RESULT "):])


def test_import_hook_bypass():
    """A known first-party module resolves through the accelerator without any
    ``sys.path`` scan, and would NOT resolve without it.

    This is the deterministic form of the plan's "known modules load faster":
    the accelerated import touches neither ``sys.path`` nor ``PathFinder``,
    which is strictly less work than the stock scan.
    """
    data = _run_probe(_BYPASS_PROBE)
    assert data["bypass_ok"], "accelerated import did not resolve"
    assert not data["pathfinder_touched"], (
        "PathFinder was consulted for an accelerated module — the meta_path "
        "finder is not short-circuiting the sys.path scan"
    )
    assert data["control_failed"], (
        "control import (accelerator removed, root off sys.path) unexpectedly "
        "succeeded — the bypass proof is not actually exercising the accelerator"
    )


_TIMING_PROBE = r'''
import os, sys, orjson, time
sys.path.insert(0, ROOT)
import import_accelerator as ia

NAMES = [n for n in ia.build_first_party_map()[1]]  # top-level packages
MODS = list(ia.build_first_party_map()[0])

def _reimport_cost(names):
    # Drop from sys.modules and re-resolve just the spec via the whole
    # meta_path chain, isolating the *finding* cost (no module body exec).
    import importlib.util
    t0 = time.perf_counter()
    for n in names:
        importlib.util.find_spec(n)
    return (time.perf_counter() - t0) * 1000.0

# Warm the FileFinder directory caches first so we compare finder overhead,
# not first-touch disk stat noise.
ia.uninstall()
_reimport_cost(NAMES + MODS)
baseline = min(_reimport_cost(NAMES + MODS) for _ in range(5))

ia.install()
accel = min(_reimport_cost(NAMES + MODS) for _ in range(5))

print("RESULT " + orjson.dumps({"baseline_ms": baseline, "accel_ms": accel}).decode('utf-8'))
'''


def test_import_hook_timing_not_slower():
    """Soft, non-flaky timing guard: resolving the first-party name set through
    the accelerator is not materially slower than the stock path (it should be
    faster, but we only assert 'not a regression' to stay CI-stable)."""
    data = _run_probe(_TIMING_PROBE)
    print(f"\n  spec-find baseline={data['baseline_ms']:.3f}ms "
          f"accel={data['accel_ms']:.3f}ms")
    # Generous bound: accel must not be more than 2x the stock path. In practice
    # it is faster; this only catches a gross regression (e.g. accidental O(n)
    # scan added to find_spec) without flaking on scheduler jitter.
    assert data["accel_ms"] <= max(data["baseline_ms"] * 2.0, 5.0)


def test_accelerator_preserves_lazy_tool_import_invariant():
    """Installing the accelerator must not eagerly import any self-registering
    tool module — the lazy tool-import optimization (P-043) must still hold with
    the accelerator active."""
    probe = r'''
import sys, orjson
sys.path.insert(0, ROOT)
from run_agent import AIAgent  # noqa: F401
import import_accelerator as ia
import tools.registry as R
self_reg = set(R.build_tool_index()["modules"])
loaded = sorted(m for m in sys.modules if m in self_reg)
print("RESULT " + orjson.dumps({
    "installed": ia.is_installed(),
    "self_reg_loaded": loaded,
}).decode('utf-8'))
'''
    data = _run_probe(probe, timeout=120)
    assert data["installed"], "run_agent did not install the accelerator"
    assert data["self_reg_loaded"] == [], (
        f"importing run_agent eagerly imported tool modules {data['self_reg_loaded']} "
        "— the lazy tool-import invariant regressed under the accelerator"
    )
