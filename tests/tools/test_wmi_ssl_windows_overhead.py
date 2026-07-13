"""WMI & SSL Windows-overhead optimizations (.plans/15, P-044).

Behavioural guards for the agent-init hotspots the plan targets:

* ``_wmi.exec_query`` (2.91%) — module-level ``platform.system()`` /
  ``platform.release()`` triggered a Windows WMI query on Python 3.12+ (via
  ``platform.uname()``), paid during the import cascade AND while building the
  system prompt.  The WMI-free ``platform_utils`` helpers answer the same
  questions from ``sys.platform`` / ``sys.getwindowsversion()``.
* ``_ssl.set_default_verify_paths`` (8.19%) — ``verify_ca_bundle()`` rebuilt a
  throwaway ``ssl.create_default_context()`` on every ``AIAgent`` construction;
  it is now memoised on a CA-config fingerprint (see ``test_ssl_ca_guard``).
* ``_io.open_code`` / ``builtins.compile`` — ``scripts/precompile.py`` warms the
  ``.pyc`` cache so first import doesn't compile on the hot path.

The pure-Python contracts run on every host; the live ``_wmi`` import-cascade
guard is Windows-only (the ``_wmi`` builtin only exists there).
"""

from __future__ import annotations

import importlib.util
import orjson
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_WIN = sys.platform == "win32"
try:
    import _wmi as _wmi_module  # type: ignore
except ImportError:
    _wmi_module = None
_WMI_AVAILABLE = _wmi_module is not None


# ==========================================================================
# platform_utils — WMI-free OS detection
# ==========================================================================


def test_is_windows_matches_sys_platform():
    """The OS-family checks are exactly the ``sys.platform`` predicates — no
    ``platform.uname()`` / WMI round trip."""
    import platform_utils as pu

    assert pu.is_windows() == (sys.platform == "win32")
    assert pu.is_macos() == (sys.platform == "darwin")
    assert pu.is_linux() == sys.platform.startswith("linux")


def test_os_checks_never_call_platform_uname(monkeypatch):
    """``is_windows`` / ``windows_release`` / ``host_os_label`` must resolve
    without ever calling the WMI-backed ``platform.uname()`` / ``system()``."""
    import platform as _p
    import platform_utils as pu

    def _boom(*a, **k):
        raise AssertionError("platform.uname()/system() called — hits WMI on Win 3.12+")

    monkeypatch.setattr(_p, "uname", _boom)
    monkeypatch.setattr(_p, "system", _boom)

    assert pu.is_windows() in (True, False)
    assert isinstance(pu.windows_release(), str)
    # host_os_label only reaches the WMI-free branch on Windows; off Windows it
    # legitimately uses the (non-WMI) stdlib, so only assert the Windows path.
    if pu.is_windows():
        assert pu.host_os_label().startswith("Windows")


def test_windows_release_matches_stdlib():
    """On Windows the WMI-free label equals ``platform.release()``; elsewhere
    it is the empty 'unknown' sentinel."""
    import platform

    import platform_utils as pu

    if _WIN:
        assert pu.windows_release() == platform.release()
    else:
        assert pu.windows_release() == ""


@pytest.mark.skipif(not _WIN or not _WMI_AVAILABLE, reason="_wmi builtin only exists on Windows")
def test_windows_release_is_wmi_free():
    """Calling the helpers issues zero ``_wmi.exec_query`` queries."""
    import _wmi

    import platform_utils as pu

    calls: list = []
    orig = _wmi.exec_query

    def traced(q):
        calls.append(q)
        return orig(q)

    _wmi.exec_query = traced
    try:
        pu.windows_release()
        pu.is_windows()
        pu.host_os_label()
    finally:
        _wmi.exec_query = orig
    assert calls == []


# ==========================================================================
# Import-cascade + prompt-build WMI regression guard (Windows-only, subprocess)
# ==========================================================================


@pytest.mark.skipif(not _WIN or not _WMI_AVAILABLE, reason="_wmi builtin only exists on Windows")
def test_agent_init_path_triggers_no_wmi():
    """Importing ``run_agent`` and building the environment hints must issue
    ZERO WMI queries.

    Pre-fix, module-level ``platform.system()`` in ``hermes_cli/config.py`` (+
    ``platform.release()`` in ``prompt_builder``) each drove ``platform.uname()``
    into ``_wmi.exec_query`` on Python 3.12+ (~45ms during import + ~40ms at
    init).  Runs in a clean subprocess so the in-process ``sys.modules`` cache
    doesn't hide a regression.
    """
    probe = textwrap.dedent(
        r"""
        import _wmi, json, orjson
        calls = []
        _orig = _wmi.exec_query
        def traced(q):
            calls.append(q)
            return _orig(q)
        _wmi.exec_query = traced

        from run_agent import AIAgent  # noqa: F401
        n_import = len(calls)

        import agent.prompt_builder as pb
        pb.build_environment_hints()
        print("RESULT " + orjson.dumps({"import": n_import, "total": len(calls), "queries": calls}).decode('utf-8'))
        """
    )
    r = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, f"probe failed:\n{r.stderr[-2000:]}"
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, f"probe produced no RESULT line:\n{r.stdout[-2000:]}"
    data = orjson.loads(lines[-1][len("RESULT "):])
    assert data["import"] == 0, (
        f"importing run_agent triggered {data['import']} WMI queries "
        f"{data['queries']} — a module-level platform.system()/release() call "
        f"regressed onto the WMI-backed platform.uname() path."
    )
    assert data["total"] == 0, (
        f"the agent-init path triggered {data['total']} WMI queries {data['queries']}"
    )


# ==========================================================================
# scripts/precompile.py — .pyc warm-up
# ==========================================================================


def _load_precompile():
    path = REPO_ROOT / "scripts" / "precompile.py"
    spec = importlib.util.spec_from_file_location("hermes_precompile_undertest", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_precompile_creates_pyc(tmp_path):
    """``precompile_all`` byte-compiles a package tree and a top-level module."""
    pc = _load_precompile()
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "m.py").write_text("VALUE = 42\n", encoding="utf-8")
    top = tmp_path / "top.py"
    top.write_text("X = 1\n", encoding="utf-8")

    ok = pc.precompile_all([str(pkg), str(top)], quiet=True)
    assert ok is True
    assert list(pkg.rglob("*.pyc")), "no .pyc produced for the package tree"
    assert list(tmp_path.rglob("top*.pyc")), "no .pyc produced for the top-level module"


def test_precompile_reports_syntax_error_without_raising(tmp_path):
    """A bad source file must not abort the warm-up — it returns False, not raise."""
    pc = _load_precompile()
    bad = tmp_path / "bad.py"
    bad.write_text("def (:\n", encoding="utf-8")
    ok = pc.precompile_all([str(bad)], quiet=True)
    assert ok is False


def test_precompile_missing_path_is_not_fatal(tmp_path):
    """Missing targets are skipped rather than treated as failures."""
    pc = _load_precompile()
    ok = pc.precompile_all([str(tmp_path / "nope_missing")], quiet=True)
    assert ok is True


def test_precompile_cli_smoke(tmp_path):
    """The CLI entry point compiles a file and exits 0."""
    pc = _load_precompile()
    src = tmp_path / "solo.py"
    src.write_text("Z = 3\n", encoding="utf-8")
    assert pc.main([str(src), "--quiet"]) == 0
    assert list(tmp_path.rglob("solo*.pyc"))


# ==========================================================================
# Windows Defender exclusion hint
# ==========================================================================


def test_suggest_defender_exclusion_platform_gated():
    """Returns an actionable hint on Windows (embedding the home path); ``None``
    off Windows."""
    from tools.environments.windows_env import suggest_defender_exclusion

    result = suggest_defender_exclusion(home=r"X:\hermes-home")
    if _WIN:
        assert result and r"X:\hermes-home" in result
        assert "Defender" in result
    else:
        assert result is None


def test_suggest_defender_exclusion_noop_when_not_windows(monkeypatch):
    """Forcing a non-Windows platform makes the hint an inert ``None``."""
    import tools.environments.windows_env as we

    monkeypatch.setattr(we.sys, "platform", "linux")
    assert we.suggest_defender_exclusion(home="/tmp/hermes") is None
