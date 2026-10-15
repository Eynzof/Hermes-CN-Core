"""Full-repo self-scan wrapper for scripts/check-windows-footguns.py.

scripts/check_subprocess_stdin.py has had a pytest wrapper (see
tests/tools/test_subprocess_stdin_guard.py's test_all_tui_subprocess_calls_
have_stdin) that runs the checker with its default full-scan behavior and
asserts a clean exit — so a normal pytest run of that file catches
regressions even when no one remembers to run the standalone script by hand.
check-windows-footguns.py had no equivalent: only a narrow rule-level test
(tests/scripts/test_footgun_subprocess_encoding.py, scoped to the
text=True/encoding= rule) existed, so a bare ``os.killpg``/``signal.SIGKILL``
regression (caught by CI running the real script with --all, not by any
local pytest run) shipped in the T1-T3 npx-agent-browser hardening commit
before anyone ran the script directly. This closes that gap the same way
the stdin guard already closes its equivalent one.

The CN fork's two console-window rules (``subprocess`` with ``shell=True`` or
``capture_output=True`` and no ``creationflags=windows_hide_flags()``) are
stricter than upstream's tree, so ``--all`` subtracts the inherited findings
recorded in scripts/ci/windows_footguns_upstream_baseline.txt (upstream call
sites at the 2026-09-20 sync). These tests pin both halves of that mechanism:
the baseline keeps the blocking gate green for what it lists, and a finding the
baseline does not cover — or one in the branch's own work, where the baseline is
never consulted — still fails the scan.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check-windows-footguns.py"
BASELINE = REPO_ROOT / "scripts" / "ci" / "windows_footguns_upstream_baseline.txt"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=180,
        stdin=subprocess.DEVNULL,
    )


def _baseline_entries() -> list[str]:
    return [
        line
        for line in BASELINE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def test_full_repo_scan_has_no_unsuppressed_windows_footguns():
    """Mirrors check_subprocess_stdin.py's wrapper: run the real checker
    against the whole repo (--all) and require a clean exit, so this test
    file — not just institutional memory — is what catches the next
    bare os.killpg/signal.SIGKILL-style regression."""
    result = _run("--all")
    assert result.returncode == 0, (
        f"Windows footgun check failed:\n{result.stdout}\n{result.stderr}"
    )


def test_full_repo_scan_still_fails_for_a_finding_outside_the_baseline(tmp_path):
    """The gate is green *because of* the baseline, not despite the rules.

    Drop one inherited entry and its finding must be reported again — the
    subtraction is per (file, rule) with an exact count, so a new instance in a
    listed file, a new rule in a listed file, and any unlisted file all fail.
    """
    entries = _baseline_entries()
    assert entries, "the inherited-findings baseline records nothing"
    dropped = entries[0]
    dropped_path = dropped.split("|")[0].strip()
    reduced = tmp_path / "reduced_baseline.txt"
    reduced.write_text(
        BASELINE.read_text(encoding="utf-8").replace(f"{dropped}\n", "", 1),
        encoding="utf-8",
    )

    result = _run("--all", "--baseline", str(reduced))

    assert result.returncode == 1, (
        f"a finding missing from the baseline must fail the scan:\n{result.stdout}"
    )
    assert dropped_path in result.stdout


def test_stale_baseline_entries_are_advisory_then_fatal_under_strict(tmp_path):
    """An entry whose line is gone is reported, not silently kept."""
    stale = tmp_path / "stale_baseline.txt"
    stale.write_text(
        BASELINE.read_text(encoding="utf-8")
        + "hermes_cli/does_not_exist.py | subprocess capture_output=True without creationflags | 1\n",
        encoding="utf-8",
    )

    advisory = _run("--all", "--baseline", str(stale))
    assert advisory.returncode == 0
    assert "advisory" in advisory.stderr
    assert "does_not_exist.py" in advisory.stderr

    strict = _run("--all", "--baseline", str(stale), "--strict-baseline")
    assert strict.returncode == 1


def test_branch_scan_never_subtracts_the_baseline():
    """--diff / explicit paths check the branch's own work, which is expected to
    be clean: the baseline must not hide anything there, and the baseline flags
    are --all-only (exit 2 otherwise)."""
    probe_dir = REPO_ROOT / ".footgun_probe"
    probe = probe_dir / "probe.py"
    probe_dir.mkdir(exist_ok=True)
    try:
        # Outside the --all roots, so a crash cannot leak a finding into the
        # full-repo scan, but inside the repo (the checker resolves paths
        # relative to REPO_ROOT).
        probe.write_text(
            'import subprocess\n\n\ndef probe():\n'
            '    return subprocess.run(["x"], capture_output=True, text=True)\n',
            encoding="utf-8",
        )
        result = _run(str(probe))
        assert result.returncode == 1, result.stdout + result.stderr
        assert "capture_output=True without creationflags" in result.stdout

        no_all = _run("--print-baseline")
        assert no_all.returncode == 2
        assert "--all" in no_all.stderr
    finally:
        probe.unlink(missing_ok=True)
        try:
            probe_dir.rmdir()
        except OSError:
            pass
